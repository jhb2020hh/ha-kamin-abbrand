"""Ablauf der ganzen App gegen den Fake-Server, mit Stub statt Kern."""
import asyncio
import json
import logging
import os
import signal
from contextlib import asynccontextmanager
from functools import partial

import pytest

from anbindung import main as main_modul
from anbindung.eingaenge import Eingaenge, Entitaeten
from anbindung.ha_client import HaClient
from anbindung.main import App, entitaeten_aus, naechstes_raster
from anbindung.schatten import Verlauf
from fake_ha import TOKEN, FakeHa, warte_auf
from kern.typen import BEDIENUNG, NEU, PARITAET, SENSOR_WEG, TICK, TUER
from stub_kern import StubFabrik
from testdaten import ENTITAETEN

E = ENTITAETEN
STATUS = "sensor.kamin_app_status"


def helfer_setzen(ha, phase="Abbrand", klappe="72"):
    ha.setzen("input_select.kamin_abbrand_phase", phase)
    ha.setzen("input_datetime.kamin_abbrand_phase_seit", "2026-11-06 14:52:30", {"timestamp": 1793973150})
    ha.setzen("input_number.kamin_abbrand_t_maximum", "220")
    ha.setzen("input_number.kamin_abbrand_t_phasenstart", "191")
    ha.setzen("input_number.kamin_abbrand_regler_i_anteil", "57.9")
    ha.setzen("input_number.kamin_abbrand_klappe_soll", klappe)
    ha.setzen(E.abgas, "165")
    ha.setzen(E.tuer, "off")


@asynccontextmanager
async def laufende_app(ha, tmp_path, fabrik, **weiteres):
    weiteres.setdefault("takt_paritaet_s", 3600.0)
    weiteres.setdefault("takt_neu_s", 3600.0)
    app = App(
        partial(HaClient, TOKEN, ws_url=ha.ws_url, rest_url=ha.rest_url, backoff_start_s=0.01),
        Eingaenge(E, "kamin_abbrand"),
        Verlauf(tmp_path),
        steuerung_fabrik=fabrik,
        wache_intervall_s=0.01,
        **weiteres,
    )
    stopp = asyncio.Event()
    aufgabe = asyncio.create_task(app.laufen(stopp))
    try:
        await warte_auf(lambda: app.client.verbunden)
        yield app
    finally:
        stopp.set()
        await aufgabe


def test_uebernimmt_zustand_fuer_beide_instanzen_und_getrennt():
    async def ablauf(tmp_path):
        async with FakeHa() as ha:
            helfer_setzen(ha)
            fabrik = StubFabrik()
            async with laufende_app(ha, tmp_path, fabrik, takt_paritaet_s=30.0, takt_neu_s=5.0) as app:
                p, n = fabrik.instanz(PARITAET), fabrik.instanz(NEU)
                assert (p.takt_s, n.takt_s) == (30.0, 5.0)
                assert p.zustand.phase == "Abbrand" and p.zustand.klappe_soll == 72
                assert p.zustand is not n.zustand
                assert p.parameter is app.eingaenge.parameter

    with_tmp(ablauf)


def with_tmp(ablauf):
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as ordner:
        asyncio.run(ablauf(Path(ordner)))


def test_reconnect_baut_spiegel_neu_uebernimmt_zustand_aber_nicht_erneut():
    async def ablauf(tmp_path):
        async with FakeHa() as ha:
            helfer_setzen(ha)
            fabrik = StubFabrik()
            async with laufende_app(ha, tmp_path, fabrik) as app:
                await ha.aendern(E.tuer, "on")
                await warte_auf(lambda: fabrik.instanz(PARITAET).laeufe)

                await ha.abbrechen()
                await warte_auf(lambda: not app.client.verbunden)
                # Die Live-Automation ist in der Lücke weitergelaufen.
                helfer_setzen(ha, phase="Glut", klappe="10")
                ha.setzen(E.abgas, "130")
                await warte_auf(lambda: app.client.verbunden)

                assert len(fabrik.gebaut) == 2
                assert fabrik.instanz(PARITAET).zustand.phase == "Abbrand"
                await ha.aendern(E.tuer, "off")
                await warte_auf(lambda: len(fabrik.instanz(PARITAET).laeufe) == 2)
                assert fabrik.instanz(PARITAET).laeufe[-1].t == 130

    with_tmp(ablauf)


def test_wartet_bis_phase_lesbar_ist():
    async def ablauf(tmp_path):
        async with FakeHa() as ha:
            ha.setzen("input_select.kamin_abbrand_phase", "unavailable")
            fabrik = StubFabrik()
            async with laufende_app(ha, tmp_path, fabrik) as app:
                assert fabrik.gebaut == []
                await warte_auf(lambda: ha.geschrieben(STATUS) == ["wartet"])
                await ha.abbrechen()
                await warte_auf(lambda: not app.client.verbunden)
                helfer_setzen(ha)
                await warte_auf(lambda: len(fabrik.gebaut) == 2)

    with_tmp(ablauf)


def test_ereignis_laeuft_sofort_in_beiden_instanzen():
    async def ablauf(tmp_path):
        async with FakeHa() as ha:
            helfer_setzen(ha)
            fabrik = StubFabrik()
            async with laufende_app(ha, tmp_path, fabrik):
                await ha.aendern(E.tuer, "on")
                await ha.aendern(E.handbetrieb, "on")
                n = fabrik.instanz(NEU)
                await warte_auf(lambda: len(n.laeufe) == 2)
                p = fabrik.instanz(PARITAET)
                assert [e.ausloeser for e in p.laeufe] == [TUER, BEDIENUNG]
                assert [e.ausloeser for e in n.laeufe] == [TUER, BEDIENUNG]
                assert p.laeufe[0].tuer_offen

    with_tmp(ablauf)


def test_raster():
    assert naechstes_raster(1000.0, 30) == 1020
    assert naechstes_raster(1019.999, 30) == 1020
    assert naechstes_raster(1020.0, 30) == 1050
    assert naechstes_raster(1021.0, 5) == 1025


def test_takt_getrennt_und_paritaet_gleicht_vor_dem_lauf_ab():
    async def ablauf(tmp_path):
        async with FakeHa() as ha:
            helfer_setzen(ha)
            fabrik = StubFabrik()
            async with laufende_app(ha, tmp_path, fabrik, takt_paritaet_s=0.2, takt_neu_s=0.05):
                p, n = fabrik.instanz(PARITAET), fabrik.instanz(NEU)
                await warte_auf(lambda: len(p.laeufe) >= 2)
                # Gleicher Wert, neue Meldung: Dafür gibt es kein state_changed.
                ha.zustaende[E.abgas]["last_reported"] = "2099-01-01T00:00:00+00:00"
                alt = len(p.laeufe)
                await warte_auf(lambda: len(p.laeufe) > alt)
                assert p.laeufe[-1].t_alter_s == 0
                assert all(e.ausloeser == TICK for e in p.laeufe + n.laeufe)
                assert len(n.laeufe) > len(p.laeufe)
                abrufe = sum(m.get("type") == "get_states" for m in ha.ws_nachrichten)
                assert abrufe >= len(p.laeufe)

    with_tmp(ablauf)


def test_sensor_weg_nach_60_s_durchgehend():
    async def ablauf(tmp_path):
        uhr = [1_800_000_000.0]
        async with FakeHa() as ha:
            helfer_setzen(ha)
            fabrik = StubFabrik()
            async with laufende_app(ha, tmp_path, fabrik, uhr=lambda: uhr[0]):
                await ha.aendern(E.abgas, "unavailable", zeit=uhr[0])
                await asyncio.sleep(0.05)
                uhr[0] += 59
                await asyncio.sleep(0.05)
                assert fabrik.instanz(PARITAET).laeufe == []
                uhr[0] += 1
                await warte_auf(lambda: fabrik.instanz(NEU).laeufe)
                eingabe = fabrik.instanz(PARITAET).laeufe[0]
                assert eingabe.ausloeser == SENSOR_WEG and eingabe.t is None
                uhr[0] += 100
                await asyncio.sleep(0.05)
                assert len(fabrik.instanz(PARITAET).laeufe) == 1

    with_tmp(ablauf)


@pytest.mark.parametrize("ausnahme", [ValueError("kaputt"), ZeroDivisionError(), KeyError("x")])
def test_ausnahme_im_kern_beendet_nichts(ausnahme):
    async def ablauf(tmp_path):
        versuche = []

        def einmal_kaputt(eingaben):
            versuche.append(eingaben)
            if len(versuche) == 1:
                raise ausnahme

        async with FakeHa() as ha:
            helfer_setzen(ha)
            fabrik = StubFabrik(neu_angeboten=False, verhalten=einmal_kaputt)
            async with laufende_app(ha, tmp_path, fabrik):
                await ha.aendern(E.tuer, "on")
                await warte_auf(lambda: "fehler" in ha.geschrieben(STATUS))
                await ha.aendern(E.tuer, "off")
                await warte_auf(lambda: ha.geschrieben(STATUS)[-1] == "ok")
                assert len(versuche) == 2
            datei = next(tmp_path.glob("verlauf-*.jsonl"))
            zeilen = [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines()]
            assert repr(ausnahme) == zeilen[0]["fehler"]
            assert zeilen[1]["ergebnis"] is not None

    with_tmp(ablauf)


def test_neu_nicht_im_konstruktor_angeboten(caplog):
    async def ablauf(tmp_path):
        async with FakeHa() as ha:
            helfer_setzen(ha)
            fabrik = StubFabrik(neu_angeboten=False)
            async with laufende_app(ha, tmp_path, fabrik):
                await ha.aendern(E.tuer, "on")
                await warte_auf(lambda: fabrik.instanz(PARITAET).laeufe)
                await warte_auf(lambda: ha.geschrieben("sensor.kamin_app_p_phase"))
                assert [s.regelstand for s in fabrik.gebaut] == [PARITAET]
                assert ha.geschrieben("sensor.kamin_app_n_phase") == []

    with caplog.at_level(logging.INFO):
        with_tmp(ablauf)
    assert sum("noch nicht angeboten" in r.getMessage() for r in caplog.records) == 1


def test_neu_erst_in_schritt_nicht_angeboten(caplog):
    async def ablauf(tmp_path):
        def nur_paritaet(_eingaben):
            raise NotImplementedError

        async with FakeHa() as ha:
            helfer_setzen(ha)
            fabrik = StubFabrik()
            async with laufende_app(ha, tmp_path, fabrik):
                fabrik.instanz(NEU).verhalten = nur_paritaet
                await ha.aendern(E.tuer, "on")
                await ha.aendern(E.tuer, "off")
                await warte_auf(lambda: len(fabrik.instanz(PARITAET).laeufe) == 2)
                await warte_auf(lambda: ha.geschrieben(STATUS)[-1] == "ok")
                assert len(fabrik.instanz(NEU).laeufe) == 1

    with caplog.at_level(logging.INFO):
        with_tmp(ablauf)
    assert sum("noch nicht angeboten" in r.getMessage() for r in caplog.records) == 1


def test_keine_schreibvorgaenge_ausserhalb_der_schattensensoren(caplog):
    async def ablauf(tmp_path):
        async with FakeHa() as ha:
            helfer_setzen(ha)
            ha.setzen(E.wartung, "off")
            fabrik = StubFabrik()  # Der Stub liefert Aktionen und Meldungen.
            async with laufende_app(ha, tmp_path, fabrik, takt_paritaet_s=0.05, takt_neu_s=0.02):
                await ha.aendern(E.tuer, "on")
                await ha.aendern(E.wartung, "on")
                await ha.aendern(E.wartung, "off")
                await ha.aendern(E.abgas, "unavailable", zeit=0)
                await warte_auf(lambda: len(fabrik.instanz(PARITAET).laeufe) >= 6)
            assert len(ha.rest_anfragen) > 5
            for methode, pfad, _ in ha.rest_anfragen:
                assert methode == "POST"
                assert pfad.startswith("/core/api/states/sensor.kamin_app_")
            typen = {n["type"] for n in ha.ws_nachrichten}
            assert typen == {"auth", "subscribe_events", "get_states"}

    with caplog.at_level(logging.INFO):
        with_tmp(ablauf)
    assert any("nur protokolliert: sniffer_restart" in r.getMessage() for r in caplog.records)


def test_schreibfehler_in_ha_beendet_nichts():
    async def ablauf(tmp_path):
        async with FakeHa() as ha:
            helfer_setzen(ha)
            ha.rest_status = 500
            fabrik = StubFabrik()
            async with laufende_app(ha, tmp_path, fabrik):
                await ha.aendern(E.tuer, "on")
                await ha.aendern(E.tuer, "off")
                await warte_auf(lambda: len(fabrik.instanz(NEU).laeufe) == 2)

    with_tmp(ablauf)


def test_gestoppt_status_beim_beenden():
    async def ablauf(tmp_path):
        async with FakeHa() as ha:
            helfer_setzen(ha)
            async with laufende_app(ha, tmp_path, StubFabrik()):
                pass
            assert ha.geschrieben(STATUS)[-1] == "gestoppt"

    with_tmp(ablauf)


def test_sigterm_beendet_sauber(monkeypatch):
    async def ablauf(tmp_path):
        async with FakeHa() as ha:
            helfer_setzen(ha)
            monkeypatch.setenv("SUPERVISOR_TOKEN", TOKEN)
            monkeypatch.setattr(main_modul, "VERLAUF_ORDNER", tmp_path)
            monkeypatch.setattr(main_modul, "EINSTELLUNGEN_PFAD", tmp_path / "einstellungen.json")
            monkeypatch.setattr(main_modul, "Webserver", partial(main_modul.Webserver, port_oberflaeche=0, port_integration=0, host="127.0.0.1"))

            async def nichts_bereitstellen(*_):
                return {}
            monkeypatch.setattr(main_modul, "integration_bereitstellen", nichts_bereitstellen)
            monkeypatch.setattr(main_modul, "HaClient", partial(HaClient, ws_url=ha.ws_url, rest_url=ha.rest_url))
            fabrik = StubFabrik()
            monkeypatch.setattr(main_modul, "steuerung_aus_kern", fabrik)
            aufgabe = asyncio.create_task(main_modul._start({"modus": "parallel"}))
            await warte_auf(lambda: fabrik.gebaut)
            os.kill(os.getpid(), signal.SIGTERM)
            async with asyncio.timeout(3):
                await aufgabe
            assert ha.geschrieben(STATUS)[-1] == "gestoppt"

    with_tmp(ablauf)


def test_unbekannter_modus_startet_nicht(monkeypatch):
    with pytest.raises(SystemExit):
        asyncio.run(main_modul._start({"modus": "steuern"}))


def test_entitaeten_aus_optionen():
    ent = entitaeten_aus({"entitaeten": {"abgas": "sensor.x", "can_alter": "", "fremd": "y"}})
    assert ent.abgas == "sensor.x" and ent.can_alter is None and ent.druck is None


def test_mit_echtem_kern_laufen_beide_regelstaende():
    async def ablauf(tmp_path):
        async with FakeHa() as ha:
            helfer_setzen(ha)
            async with laufende_app(ha, tmp_path, main_modul.steuerung_aus_kern) as app:
                assert set(app.steuerungen) == {PARITAET, NEU}
                await ha.aendern(E.tuer, "on")
                await warte_auf(lambda: ha.geschrieben("sensor.kamin_app_p_phase"))
                await warte_auf(lambda: ha.geschrieben(STATUS)[-1] == "ok")
                assert ha.geschrieben("sensor.kamin_app_p_phase") == ["Tür offen"]
                await warte_auf(lambda: ha.geschrieben("sensor.kamin_app_n_phase"))
                assert ha.geschrieben("sensor.kamin_app_n_phase")[-1] == "Tür offen"
            zeilen = next(tmp_path.glob("verlauf-*.jsonl")).read_text(encoding="utf-8").splitlines()
            zeile = next(z for z in map(json.loads, zeilen) if z["eingaben"]["ausloeser"] == TUER)
            assert zeile["eingaben"]["ausloeser"] == TUER
            assert zeile["ergebnis"]["uebergang"] == ["Abbrand", "Tür offen"]
            assert zeile["live"]["phase"] == "Abbrand"

    with_tmp(ablauf)
