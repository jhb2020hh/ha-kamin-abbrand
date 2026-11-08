"""Einstellungen, Migration, Ableitungen, Wechsel zur Laufzeit, Start ohne Helfer."""
import asyncio
import json
from functools import partial
from pathlib import Path

import pytest

from anbindung.ableitung import Ableitung
from anbindung.bereitstellung import kopieren
from anbindung.eingaenge import Eingaenge, Entitaeten
from anbindung.einstellungen import EinstellungsFehler, Speicher
from anbindung.ha_client import _ERLAUBTE_WS_BEFEHLE, HaClient
from anbindung.main import App, steuerung_aus_kern
from anbindung.schatten import Verlauf
from fake_ha import TOKEN, FakeHa, warte_auf
from kern.typen import NEU, PARITAET, STANDBY

ALT = {"entitaeten": {"abgas": "sensor.alt_t", "tuer": "binary_sensor.alt", "fremd": "x.y", "can_alter": ""}}
FIXTUR = Path(__file__).parents[1] / "fixtures" / "ableitung_2026-11-08.json"


def test_migration_uebernimmt_nur_bekannte_rollen(tmp_path):
    s = Speicher(tmp_path / "e.json", ALT)
    assert s.rollen == {"abgas": "sensor.alt_t", "tuer": "binary_sensor.alt"}
    assert (tmp_path / "e.json").exists() and len(s.token) > 20


def test_vorhandene_datei_hat_vorrang_vor_optionen(tmp_path):
    Speicher(tmp_path / "e.json", ALT).rollen_setzen({"abgas": "sensor.neu"}, False)
    s = Speicher(tmp_path / "e.json", ALT)
    assert s.rollen == {"abgas": "sensor.neu"} and s.tuerkontakt_vorhanden is False


def test_token_bleibt_gleich(tmp_path):
    assert Speicher(tmp_path / "e.json").token == Speicher(tmp_path / "e.json").token


def test_kaputte_datei_wird_nicht_ueberschrieben(tmp_path):
    (tmp_path / "e.json").write_text("{kaputt")
    Speicher(tmp_path / "e.json", ALT)
    assert (tmp_path / "e.json").read_text() == "{kaputt"


def test_ohne_zuweisung_sind_eingaenge_leer_nicht_die_eigenen_vorgaben(tmp_path):
    ent = Speicher(tmp_path / "e.json").entitaeten()
    assert ent.abgas is None and ent.tuer is None and ent.tuerkontakt_vorhanden is None


@pytest.mark.parametrize("rollen, text", [
    ({"unbekannt": "sensor.a"}, "Unbekannte"), ({"abgas": "kein punkt"}, "gültige"),
    ({"abgas": "switch.a"}, "Erlaubt"), ({"ausgang_klappe": "sensor.a"}, "Erlaubt"),
])
def test_rollen_pruefung(tmp_path, rollen, text):
    with pytest.raises(EinstellungsFehler, match=text):
        Speicher(tmp_path / "e.json").rollen_setzen(rollen, True)


@pytest.mark.parametrize("ausgang, eid", [("ausgang_klappe", "cover.k"), ("ausgang_klappe", "input_number.k"),
                                          ("ausgang_heartbeat", "number.h"), ("ausgang_neustart", "script.n"),
                                          ("ausgang_meldungen", "notify.handy")])
def test_ausgaenge_sind_zuweisbar(tmp_path, ausgang, eid):
    s = Speicher(tmp_path / "e.json")
    s.rollen_setzen({ausgang: eid}, True)
    assert s.rollen[ausgang] == eid


def test_parametergrenzen_und_zuruecknehmen(tmp_path):
    s = Speicher(tmp_path / "e.json")
    assert s.parameter_setzen("regler_verstaerkung", 0.8) == 0.8
    for falsch in (99, "abc", True):
        with pytest.raises(EinstellungsFehler):
            s.parameter_setzen("regler_verstaerkung", falsch)
    with pytest.raises(EinstellungsFehler):
        s.parameter_setzen("gibt_es_nicht", 1)
    s.parameter_setzen("regler_verstaerkung", None)
    assert s.parameter_neu == {}


def test_ableitung_gegen_echte_werte():
    """Abbrand vom 08.11., verglichen mit den Helfern der Live-Anlage."""
    proben = json.loads(FIXTUR.read_text())
    a, letzter = Ableitung(), None
    beginn = proben[0]["jetzt"] + 300
    abw_g, abw_m = [], []
    for z in proben:
        if z["t"] != letzter:
            a.abgas(z["jetzt"], z["t"])
            letzter = z["t"]
        if z["jetzt"] >= beginn:
            abw_g.append(a.gradient(z["jetzt"]) - z["g"])
            abw_m.append(a.t_max_3min(z["jetzt"]) - z["tmax3"])
    assert (sum(x * x for x in abw_g) / len(abw_g)) ** 0.5 < 0.3
    assert max(map(abs, abw_m)) <= 3.0


def test_twt_anstieg():
    a = Ableitung()
    for s, w in ((0, 40.0), (300, 42.0), (601, 43.0)):
        a.twt(s, w)
    assert a.twt_anstieg(601) == 1.0


def test_integration_wird_nur_in_ihren_ordner_kopiert(tmp_path):
    quelle = tmp_path / "q" / "kamin_abbrand"
    quelle.mkdir(parents=True)
    (quelle / "manifest.json").write_text("{}")
    ha = tmp_path / "ha"
    (ha / "custom_components" / "anderes").mkdir(parents=True)
    (ha / "configuration.yaml").write_text("x")
    assert kopieren(quelle, ha)["neustart_noetig"] is True
    assert (ha / "custom_components" / "kamin_abbrand" / "manifest.json").exists()
    assert (ha / "custom_components" / "anderes").exists() and (ha / "configuration.yaml").read_text() == "x"
    assert kopieren(quelle, ha)["neustart_noetig"] is False


def test_schreibsperre_ohne_zusatzweg():
    assert _ERLAUBTE_WS_BEFEHLE == frozenset({"get_states", "subscribe_events"})
    ordner = Path(__file__).parents[2] / "kamin_abbrand" / "app" / "anbindung"
    for pfad in ordner.glob("*.py"):
        text = pfad.read_text(encoding="utf8")
        assert "call_service" not in text and "/services/" not in text, pfad


def _app(ha, tmp_path, speicher, praefix="kamin_abbrand", **weiteres):
    return App(partial(HaClient, TOKEN, ws_url=ha.ws_url, rest_url=ha.rest_url, backoff_start_s=0.01),
               Eingaenge(speicher.entitaeten(), praefix), Verlauf(tmp_path), steuerung_fabrik=steuerung_aus_kern,
               speicher=speicher, takt_paritaet_s=0.05, takt_neu_s=0.05, wache_intervall_s=0.01, **weiteres)


def test_start_ohne_helfer_und_mit_eigenen_ableitungen(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            ha.setzen("sensor.fremd_t", "25", {"unit_of_measurement": "°C"})
            s = Speicher(tmp_path / "e.json")
            s.rollen_setzen({"abgas": "sensor.fremd_t"}, False)
            app = _app(ha, tmp_path, s, start_ohne_helfer_nach_s=0.0)
            stopp = asyncio.Event()
            aufgabe = asyncio.create_task(app.laufen(stopp))
            try:
                await warte_auf(lambda: NEU in app.letzte and PARITAET in app.letzte)
                assert app.ohne_helfer and app.steuerungen[NEU].zustand.phase == STANDBY
                assert app.letzte_eingaben.t == 25
            finally:
                stopp.set()
                await aufgabe
    asyncio.run(ablauf())


def test_eingangswechsel_zur_laufzeit_ohne_neustart(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            ha.setzen("sensor.a", "100")
            ha.setzen("sensor.b", "200")
            s = Speicher(tmp_path / "e.json")
            s.rollen_setzen({"abgas": "sensor.a"}, True)
            app = _app(ha, tmp_path, s, start_ohne_helfer_nach_s=0.0)
            stopp = asyncio.Event()
            aufgabe = asyncio.create_task(app.laufen(stopp))
            try:
                await warte_auf(lambda: app.letzte_eingaben is not None and app.letzte_eingaben.t == 100)
                instanz = app.steuerungen[NEU]
                s.rollen_setzen({"abgas": "sensor.b"}, True)
                app.zuweisung_uebernehmen()
                await warte_auf(lambda: app.letzte_eingaben.t == 200)
                assert app.steuerungen[NEU] is instanz
            finally:
                stopp.set()
                await aufgabe
    asyncio.run(ablauf())


def test_eigene_reglerwerte_nur_fuer_neu(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            ha.setzen("sensor.a", "100")
            ha.setzen("input_number.kamin_abbrand_regler_verstaerkung", "0.5")
            s = Speicher(tmp_path / "e.json")
            s.rollen_setzen({"abgas": "sensor.a"}, True)
            s.parameter_setzen("regler_verstaerkung", 1.1)
            app = _app(ha, tmp_path, s, start_ohne_helfer_nach_s=0.0)
            stopp = asyncio.Event()
            aufgabe = asyncio.create_task(app.laufen(stopp))
            try:
                await warte_auf(lambda: NEU in app.letzte)
                assert app.steuerungen[NEU].parameter.regler_verstaerkung == 1.1
                assert app.steuerungen[PARITAET].parameter.regler_verstaerkung == 0.5
                # Nichts außer den Schattensensoren wurde geschrieben.
                assert all(p.startswith("/core/api/states/sensor.kamin_app_") for _, p, _ in ha.rest_anfragen)
            finally:
                stopp.set()
                await aufgabe
    asyncio.run(ablauf())


def test_tuerkontakt_aus_gilt_nur_fuer_neu(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            ha.setzen("sensor.a", "100")
            ha.setzen("binary_sensor.t", "on")
            s = Speicher(tmp_path / "e.json")
            s.rollen_setzen({"abgas": "sensor.a", "tuer": "binary_sensor.t"}, False)
            verlauf = []
            app = _app(ha, tmp_path, s, start_ohne_helfer_nach_s=0.0)
            original = app.verlauf.schreiben
            app.verlauf.schreiben = lambda r, e, *a, **k: (verlauf.append((r, e)), original(r, e, *a, **k))
            stopp = asyncio.Event()
            aufgabe = asyncio.create_task(app.laufen(stopp))
            try:
                await warte_auf(lambda: {r for r, _ in verlauf} == {PARITAET, NEU})
            finally:
                stopp.set()
                await aufgabe
            je = dict(verlauf)
            assert je[NEU].tuer_offen is False and je[NEU].tuerkontakt_vorhanden is False
            assert je[PARITAET].tuer_offen is True
    asyncio.run(ablauf())
