"""Brennstoff in der App: gespeichert, über Neustarts gehalten, nach dem Nachlauf zurück auf Holz (Issue #3)."""
import asyncio
import tempfile
import time
from functools import partial
from pathlib import Path

from anbindung.eingaenge import Eingaenge
from anbindung.einstellungen import Speicher
from anbindung.ha_client import HaClient
from anbindung.main import App, steuerung_aus_kern
from anbindung.schatten import Verlauf
from fake_ha import TOKEN, FakeHa, warte_auf
from kern.typen import NEU
from test_app import helfer_setzen
from testdaten import ENTITAETEN as E


def test_speicher_haelt_brennstoff_und_nachlauf(tmp_path):
    s = Speicher(tmp_path / "e.json")
    assert s.brennstoff == "Holz" and s.brikett_nachlauf_bis is None
    s.brennstoff_setzen("Briketts")
    s.brikett_nachlauf_setzen(1_800_000_000.0)
    s = Speicher(tmp_path / "e.json")
    assert s.brennstoff == "Briketts" and s.brikett_nachlauf_bis == 1_800_000_000.0


def test_verdorbener_eintrag_gilt_als_holz(tmp_path):
    s = Speicher(tmp_path / "e.json")
    s.daten["brennstoff"] = {"wahl": "Koks", "nachlauf_bis": "morgen"}
    s.speichern()
    s = Speicher(tmp_path / "e.json")
    assert s.brennstoff == "Holz" and s.brikett_nachlauf_bis is None


def _mit_app(pruefung, phase, abgas, vorbereiten):
    async def ablauf(tmp):
        async with FakeHa() as ha:
            helfer_setzen(ha, phase=phase)
            ha.setzen(E.abgas, abgas, {"unit_of_measurement": "°C"})
            s = Speicher(tmp / "e.json")
            s.rollen_setzen({"abgas": E.abgas}, True)
            vorbereiten(s)
            app = App(partial(HaClient, TOKEN, ws_url=ha.ws_url, rest_url=ha.rest_url, backoff_start_s=0.01),
                      Eingaenge(s.entitaeten(), "kamin_abbrand"), Verlauf(tmp), speicher=s, steuerung_fabrik=steuerung_aus_kern,
                      takt_paritaet_s=3600.0, takt_neu_s=0.05, wache_intervall_s=0.01)
            stopp = asyncio.Event()
            aufgabe = asyncio.create_task(app.laufen(stopp))
            try:
                await warte_auf(lambda: app.client.verbunden and NEU in app.letzte)
                await pruefung(app, s)
            finally:
                stopp.set()
                await aufgabe

    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(ablauf(Path(tmp)))


def test_nachlauf_aus_den_einstellungen_wirkt_nach_neustart():
    bis = time.time() + 5 * 3600

    def vorbereiten(s):
        s.brennstoff_setzen("Holz")
        s.brikett_nachlauf_setzen(bis)

    async def pruefung(app, s):
        await warte_auf(lambda: app.steuerungen[NEU].zustand.brikett_nachlauf_bis == bis)
        ergebnis = app.letzte[NEU][0]
        assert ergebnis.zustand.phase in ("Glut", "Abkühlen") and ergebnis.klappe >= 25
        assert s.brikett_nachlauf_bis == bis
    _mit_app(pruefung, "Glut", "110", vorbereiten)


def test_nach_dem_nachlauf_stellt_die_app_auf_holz_zurueck():
    def vorbereiten(s):
        s.brennstoff_setzen("Briketts")
        s.brikett_nachlauf_setzen(time.time() - 60)

    async def pruefung(app, s):
        await warte_auf(lambda: s.brennstoff == "Holz")
        await warte_auf(lambda: s.brikett_nachlauf_bis is None)
        assert any("wieder auf Holz" in m["text"] for m in app.meldungen)
    _mit_app(pruefung, "Standby", "30", vorbereiten)


def test_umstellen_im_lauschen_meldet_dass_es_nicht_wirkt():
    async def pruefung(app, s):
        assert app.brennstoff_setzen("Briketts") == "Briketts"
        assert "Automatikregler auf 2 Uhr" in app.meldungen[0]["text"]
        assert "Lauschen" in app.meldungen[0]["text"]
    _mit_app(pruefung, "Standby", "30", lambda s: None)
