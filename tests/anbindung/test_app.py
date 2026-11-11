"""Ablauf der ganzen App gegen den Fake-Server, mit Stub statt Kern."""
import asyncio
import json
import logging
import os
import signal
import time
from contextlib import asynccontextmanager
from functools import partial

import pytest

from anbindung import main as main_modul
from anbindung.betrieb import zustand_laden, zustand_sichern
from anbindung.eingaenge import Eingaenge, Entitaeten
from anbindung import ansicht
from anbindung.einstellungen import EinstellungsFehler, Speicher
from anbindung.ha_client import HaClient
from anbindung.main import App, entitaeten_aus, naechstes_raster
from anbindung.schatten import Verlauf
from fake_ha import TOKEN, FakeHa, warte_auf
from kern.typen import BEDIENUNG, NEU, PARITAET, SENSOR_WEG, TICK, TUER, Zustand
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


def test_alter_wert_der_option_modus_stoert_nicht(monkeypatch):
    # Die Option gibt es nicht mehr: Der Start scheitert hier nur noch am fehlenden Token.
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="SUPERVISOR_TOKEN"):
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


# ---- Betrieb --------------------------------------------------------------------

KLAPPE, HB, NEUSTART, AUTOMATION = "cover.klappe", "number.hb", "button.neustart", "automation.steuerung"
AUF = ("cover.set_cover_position", {"position": 100, "entity_id": KLAPPE})
BESTAETIGT = {"co_melder": True, "hersteller": True, "verantwortung": True}
VERSION = "9.9"
HANDY = "notify.mobile_app_a"


def betrieb_speicher(pfad, modus="betrieb", **rollen):
    s = Speicher(pfad / "e.json")
    s.rollen_setzen({"abgas": E.abgas, "tuer": E.tuer, "wartung": E.wartung, "handbetrieb": E.handbetrieb,
                     "ausgang_klappe": KLAPPE, "ausgang_heartbeat": HB, "ausgang_neustart": NEUSTART,
                     "konkurrierende_automation": AUTOMATION} | rollen, True)
    s.bestaetigung_speichern(BESTAETIGT, 1000.0, VERSION)
    s.benachrichtigungen_setzen({"betrieb": [HANDY], "stoerung": [HANDY]})
    s.modus_setzen(modus)
    return s


def anlage_setzen(ha, abgas="165"):
    helfer_setzen(ha)
    ha.setzen(E.abgas, abgas)
    ha.setzen(E.wartung, "off")
    ha.setzen(E.handbetrieb, "off")
    ha.setzen(KLAPPE, "open", {"current_position": 100})
    ha.setzen(HB, "1", {"min": 0, "max": 1000})
    ha.setzen(AUTOMATION, "off")


@asynccontextmanager
async def betriebs_app(ha, tmp_path, fabrik, **weiteres):
    weiteres.setdefault("speicher", betrieb_speicher(tmp_path))
    weiteres.setdefault("takt_paritaet_s", 3600.0)
    weiteres.setdefault("takt_neu_s", 0.02)
    app = App(partial(HaClient, TOKEN, ws_url=ha.ws_url, rest_url=ha.rest_url, backoff_start_s=0.01),
              Eingaenge(weiteres["speicher"].entitaeten(), "kamin_abbrand"), Verlauf(tmp_path),
              steuerung_fabrik=fabrik, wache_intervall_s=0.01, **weiteres)
    app.ausgaenge._sicher_pause_s = 0.0
    stopp = asyncio.Event()
    aufgabe = asyncio.create_task(app.laufen(stopp))
    try:
        await warte_auf(lambda: app.client.verbunden)
        yield app
    finally:
        stopp.set()
        await aufgabe


def klappenbefehle(ha):
    return [(d, k) for d, k in ha.schaltbefehle() if d == "cover.set_cover_position"]


def test_im_lauschen_geht_nie_ein_klappenbefehl_raus(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            fabrik = StubFabrik()      # Der Stub liefert Sollwert und Aktionen.
            s = betrieb_speicher(tmp_path, modus="lauschen")
            async with betriebs_app(ha, tmp_path, fabrik, speicher=s, takt_paritaet_s=0.03) as app:
                await ha.aendern(E.tuer, "on")
                await warte_auf(lambda: len(fabrik.instanz(NEU).laeufe) >= 8)
                assert app.fuehrender_regelstand == PARITAET and not app.client.betrieb_aktiv
            assert ha.schaltbefehle() == []

    asyncio.run(ablauf())


def test_betrieb_stellt_klappe_heartbeat_und_aktionen_an_ihre_rollen(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            gesicherter_zustand(tmp_path, 10)
            fabrik = StubFabrik()
            async with betriebs_app(ha, tmp_path, fabrik) as app:
                await warte_auf(lambda: len(klappenbefehle(ha)) >= 2)
                await warte_auf(lambda: sum(d == "number.set_value" for d, _ in ha.schaltbefehle()) >= 2)
                await warte_auf(lambda: sum(d == "button.press" for d, _ in ha.schaltbefehle()) >= 1)
                await warte_auf(lambda: sum(d == "input_boolean.turn_off" for d, _ in ha.schaltbefehle()) >= 1)
                assert app.fuehrender_regelstand == NEU and app.im_betrieb
                assert klappenbefehle(ha)[0][1]["entity_id"] == KLAPPE
                # Nur zugewiesene Ausgänge, alles andere ist unerreichbar.
                ziele = {k["entity_id"] for _, k in ha.schaltbefehle()}
                assert ziele <= {KLAPPE, HB, NEUSTART, E.wartung}

    asyncio.run(ablauf())


def test_paritaet_schaltet_auch_im_betrieb_nichts(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            fabrik = StubFabrik()
            async with betriebs_app(ha, tmp_path, fabrik, takt_neu_s=3600.0, takt_paritaet_s=0.02):
                await warte_auf(lambda: len(fabrik.instanz(PARITAET).laeufe) >= 3)
                assert fabrik.instanz(NEU).laeufe == [] and ha.schaltbefehle() == []

    asyncio.run(ablauf())


def test_einschalten_ohne_bestaetigung_schaltet_nichts(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            s = betrieb_speicher(tmp_path, modus="lauschen")
            s.daten["betrieb_bestaetigung"] = {}
            async with betriebs_app(ha, tmp_path, StubFabrik(), speicher=s) as app:
                await warte_auf(lambda: NEU in app.letzte)
                offen = await app.betrieb_anfordern({"co_melder": True}, VERSION)
                assert [p.schluessel for p in offen] == ["bestaetigung"]
                assert not app.im_betrieb and s.modus == "lauschen" and s.bestaetigt is False
                assert await app.betrieb_anfordern(BESTAETIGT, VERSION) == []
                assert app.im_betrieb and s.modus == "betrieb" and s.bestaetigt
                eintrag = s.bestaetigung["betrieb"]
                assert eintrag["version"] == VERSION and eintrag["datum"]

    asyncio.run(ablauf())


async def rueckfall_abwarten(app, ha, s):
    await warte_auf(lambda: app.rueckfall and not app.client.betrieb_aktiv)
    assert not app.im_betrieb and s.modus == "lauschen" and not app.client.betrieb_aktiv
    assert klappenbefehle(ha)[-1] == AUF
    assert json.loads(s.pfad.read_text())["modus"] == "lauschen"
    assert app.rueckfall and app.rueckfall["klappe_offen"] is True


@pytest.mark.parametrize("ausnahme", [ValueError("kaputt"), ZeroDivisionError()])
def test_kernfehler_faehrt_klappe_auf_und_faellt_zurueck(tmp_path, ausnahme):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            fabrik = StubFabrik()
            s = betrieb_speicher(tmp_path)
            async with betriebs_app(ha, tmp_path, fabrik, speicher=s) as app:
                await warte_auf(lambda: klappenbefehle(ha))

                def kaputt(_):
                    raise ausnahme
                fabrik.instanz(NEU).verhalten = kaputt
                await rueckfall_abwarten(app, ha, s)
                await app.benachrichtiger.abwarten()
                assert any(d == HANDY and "Zurück auf Lauschen" in k["message"] for d, k in ha.gesendet())
                anzahl = len(ha.schaltbefehle())
                await asyncio.sleep(0.08)
                assert len(ha.schaltbefehle()) == anzahl

    asyncio.run(ablauf())


def test_notimplementederror_im_betrieb_faellt_zurueck(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            fabrik = StubFabrik()
            s = betrieb_speicher(tmp_path)
            async with betriebs_app(ha, tmp_path, fabrik, speicher=s) as app:
                await warte_auf(lambda: klappenbefehle(ha))

                def nicht_da(_):
                    raise NotImplementedError
                fabrik.instanz(NEU).verhalten = nicht_da
                await rueckfall_abwarten(app, ha, s)

    asyncio.run(ablauf())


def test_fehler_im_regellauf_ausserhalb_des_kerns_faellt_zurueck(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            s = betrieb_speicher(tmp_path)
            async with betriebs_app(ha, tmp_path, StubFabrik(), speicher=s) as app:
                await warte_auf(lambda: klappenbefehle(ha))

                def kaputt(*_, **__):
                    raise RuntimeError("Platte")
                app.verlauf.schreiben = kaputt
                await rueckfall_abwarten(app, ha, s)

    asyncio.run(ablauf())


def test_automation_an_faellt_sofort_zurueck(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            s = betrieb_speicher(tmp_path)
            async with betriebs_app(ha, tmp_path, StubFabrik(), speicher=s, karenz_s=3600.0) as app:
                await warte_auf(lambda: klappenbefehle(ha))
                await ha.aendern(AUTOMATION, "on")
                await rueckfall_abwarten(app, ha, s)

    asyncio.run(ablauf())


@pytest.mark.parametrize("eid, wert", [(KLAPPE, "unavailable"), (E.abgas, "unavailable"), (AUTOMATION, "unknown")])
def test_kurzer_ausfall_wird_ausgehalten_laengerer_faellt_zurueck(tmp_path, eid, wert):
    async def ablauf():
        uhr = [time.time()]
        async with FakeHa() as ha:
            anlage_setzen(ha)
            s = betrieb_speicher(tmp_path)
            async with betriebs_app(ha, tmp_path, StubFabrik(), speicher=s, uhr=lambda: uhr[0], karenz_s=30.0) as app:
                await warte_auf(lambda: klappenbefehle(ha))
                await ha.aendern(eid, wert, zeit=uhr[0])
                await asyncio.sleep(0.08)
                assert app.im_betrieb
                uhr[0] += 31
                await rueckfall_abwarten(app, ha, s)

    asyncio.run(ablauf())


def test_verbindung_lange_weg_faellt_beim_wiederverbinden_zurueck(tmp_path):
    async def ablauf():
        uhr = [time.time()]
        async with FakeHa() as ha:
            anlage_setzen(ha)
            s = betrieb_speicher(tmp_path)
            async with betriebs_app(ha, tmp_path, StubFabrik(), speicher=s, uhr=lambda: uhr[0], karenz_s=30.0) as app:
                await warte_auf(lambda: klappenbefehle(ha))
                # Wie nach einer langen Lücke: Der echte Abbruch verbindet im Test zu schnell wieder.
                app._getrennt_seit = uhr[0] - 45
                await rueckfall_abwarten(app, ha, s)

    asyncio.run(ablauf())


def test_kurze_trennung_bleibt_im_betrieb_und_sendet_nach_dem_wiederverbinden_neu(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha, abgas="30")
            gesicherter_zustand(tmp_path, 10, phase="Standby", klappe=0)
            async with betriebs_app(ha, tmp_path, main_modul.steuerung_aus_kern, karenz_s=30.0) as app:
                await warte_auf(lambda: klappenbefehle(ha))
                vorher = len(klappenbefehle(ha))
                await ha.abbrechen()
                await warte_auf(lambda: len(klappenbefehle(ha)) > vorher)
                assert app.im_betrieb

    asyncio.run(ablauf())


def test_klappe_nicht_erreichbar_beim_rueckfall_wird_nachgeholt_und_dann_gesperrt(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            s = betrieb_speicher(tmp_path)
            async with betriebs_app(ha, tmp_path, StubFabrik(), speicher=s) as app:
                await warte_auf(lambda: klappenbefehle(ha))
                ha.dienst_status = 500
                await ha.aendern(AUTOMATION, "on")
                await warte_auf(lambda: app.rueckfall)
                assert s.modus == "lauschen" and app.client.betrieb_aktiv and app.rueckfall["klappe_offen"] is False
                ha.dienst_status = None
                await ha.aendern(E.tuer, "on")
                await warte_auf(lambda: not app.client.betrieb_aktiv)
                assert klappenbefehle(ha)[-1] == AUF and app.rueckfall["klappe_offen"] is True

    asyncio.run(ablauf())


def test_schaltfehler_bricht_den_lauf_nicht_ab_und_wird_zur_stoerung(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            fabrik = StubFabrik()
            ha.dienst_status = 500
            async with betriebs_app(ha, tmp_path, fabrik) as app:
                await warte_auf(lambda: app.ausgaenge.stoerung is not None)
                alt = len(fabrik.instanz(NEU).laeufe)
                await warte_auf(lambda: len(fabrik.instanz(NEU).laeufe) > alt + 2)
                assert app.im_betrieb       # Weiter versuchen ist besser als aufgeben.
                await app.benachrichtiger.abwarten()
                assert any("keine Befehle" in k["message"] for _, k in ha.gesendet())
                ha.dienst_status = None

    asyncio.run(ablauf())


def test_beenden_oeffnet_die_klappe_vor_dem_schliessen_und_behaelt_den_modus(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            s = betrieb_speicher(tmp_path)
            async with betriebs_app(ha, tmp_path, StubFabrik(), speicher=s):
                await warte_auf(lambda: klappenbefehle(ha))
            assert klappenbefehle(ha)[-1] == AUF
            assert s.modus == "betrieb"
            pfade = [p for _, p, _ in ha.rest_anfragen]
            assert pfade.index("/core/api/services/cover/set_cover_position") < len(pfade)
            letzte_klappe = max(i for i, p in enumerate(pfade) if "set_cover_position" in p)
            gestoppt = max(i for i, (_, p, k) in enumerate(ha.rest_anfragen) if p.endswith("kamin_app_status") and k["state"] == "gestoppt")
            assert letzte_klappe < gestoppt

    asyncio.run(ablauf())


def test_beenden_haelt_das_zeitlimit_wenn_die_klappe_haengt(tmp_path, monkeypatch):
    monkeypatch.setattr(main_modul, "SICHER_TIMEOUT_S", 0.1)

    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            s = betrieb_speicher(tmp_path)
            gestartet = None
            async with betriebs_app(ha, tmp_path, StubFabrik(), speicher=s) as app:
                await warte_auf(lambda: klappenbefehle(ha))

                async def haengt(*_):
                    await asyncio.sleep(30)
                app.client.schalte = haengt
                gestartet = time.monotonic()
            assert time.monotonic() - gestartet < 2

    asyncio.run(ablauf())


def test_sigterm_im_betrieb_oeffnet_die_klappe(tmp_path, monkeypatch):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            betrieb_speicher(tmp_path)
            monkeypatch.setenv("SUPERVISOR_TOKEN", TOKEN)
            monkeypatch.setattr(main_modul, "VERLAUF_ORDNER", tmp_path)
            monkeypatch.setattr(main_modul, "EINSTELLUNGEN_PFAD", tmp_path / "e.json")
            monkeypatch.setattr(main_modul, "Webserver", partial(main_modul.Webserver, port_oberflaeche=0, port_integration=0, host="127.0.0.1"))

            async def nichts_bereitstellen(*_):
                return {}
            monkeypatch.setattr(main_modul, "integration_bereitstellen", nichts_bereitstellen)
            monkeypatch.setattr(main_modul, "HaClient", partial(HaClient, ws_url=ha.ws_url, rest_url=ha.rest_url))
            fabrik = StubFabrik()
            monkeypatch.setattr(main_modul, "steuerung_aus_kern", fabrik)
            aufgabe = asyncio.create_task(main_modul._start({}))
            await warte_auf(lambda: fabrik.gebaut)
            os.kill(os.getpid(), signal.SIGTERM)
            async with asyncio.timeout(8):
                await aufgabe
            assert klappenbefehle(ha)[-1] == AUF

    asyncio.run(ablauf())


# ---- Neustart im Betrieb ---------------------------------------------------------

def gesicherter_zustand(pfad, alter_s, phase="Abbrand", klappe=62):
    zeit = time.time() - alter_s
    z = Zustand(phase=phase, phase_seit=zeit - 100, t_maximum=230, t_phasenstart=190, i_anteil=55, klappe_soll=klappe)
    zustand_sichern(pfad / "betrieb_zustand.json", z, zeit)


def test_neustart_uebernimmt_jungen_zustand_und_bleibt_im_betrieb(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            gesicherter_zustand(tmp_path, 300)
            fabrik = StubFabrik()
            async with betriebs_app(ha, tmp_path, fabrik) as app:
                await warte_auf(lambda: len(fabrik.gebaut) == 2)
                assert fabrik.instanz(NEU).zustand.phase == "Abbrand"
                assert fabrik.instanz(NEU).zustand.t_maximum == 230
                assert fabrik.instanz(PARITAET).zustand.t_maximum == 220     # kommt von den Helfern
                await warte_auf(lambda: klappenbefehle(ha))
                assert app.im_betrieb and not app.anlaufschutz
                assert klappenbefehle(ha)[0][1]["position"] != 100

    asyncio.run(ablauf())


@pytest.mark.parametrize("alter", [601, 86400, -30])
def test_neustart_mit_altem_zustand_nimmt_die_startlogik(tmp_path, alter):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            gesicherter_zustand(tmp_path, alter)
            fabrik = StubFabrik()
            async with betriebs_app(ha, tmp_path, fabrik) as app:
                await warte_auf(lambda: len(fabrik.gebaut) == 2)
                assert fabrik.instanz(NEU).zustand.phase == "Standby" and app.anlaufschutz

    asyncio.run(ablauf())


@pytest.mark.parametrize("inhalt", ["{kaputt", "[]", '{"zeit": "x", "zustand": {}}',
                                     '{"zeit": 99999999999, "zustand": {"phase": "Abbrand"}}'])
def test_kaputter_gesicherter_zustand_gilt_nicht(tmp_path, inhalt):
    (tmp_path / "betrieb_zustand.json").write_text(inhalt, encoding="utf-8")
    assert zustand_laden(tmp_path / "betrieb_zustand.json", time.time()) is None


def test_gesicherter_zustand_ohne_datei_und_mit_unsinn():
    from pathlib import Path
    assert zustand_laden(Path("/gibt/es/nicht.json"), 1.0) is None


def test_neustart_ohne_gueltige_bedingungen_faellt_zurueck(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            ha.setzen(AUTOMATION, "on")
            s = betrieb_speicher(tmp_path)
            async with betriebs_app(ha, tmp_path, StubFabrik(), speicher=s) as app:
                await rueckfall_abwarten(app, ha, s)
                assert len(klappenbefehle(ha)) <= 3

    asyncio.run(ablauf())


def test_neustart_ohne_bestaetigung_faellt_zurueck(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            s = betrieb_speicher(tmp_path)
            s.daten["betrieb_bestaetigung"] = {}
            async with betriebs_app(ha, tmp_path, StubFabrik(), speicher=s) as app:
                await rueckfall_abwarten(app, ha, s)

    asyncio.run(ablauf())


# ---- Mit echtem Kern -------------------------------------------------------------

@pytest.mark.parametrize("abgas, erwartet", [
    ("70", 100),            # Warm, unter t_abkuehlen: Der Kern allein ginge in Standby auf 0 %.
    ("100", 100),           # Der Kern allein bliebe bei der Glutklappe von 10 %.
    ("unavailable", 100),   # Ohne Messwert ist nichts bewiesen.
])
def test_heisser_ofen_ohne_gueltigen_zustand_schliesst_die_klappe_nicht(tmp_path, abgas, erwartet):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha, abgas=abgas)
            async with betriebs_app(ha, tmp_path, main_modul.steuerung_aus_kern, karenz_s=3600.0) as app:
                await warte_auf(lambda: len(klappenbefehle(ha)) >= 1)
                await asyncio.sleep(0.1)
                assert all(k["position"] == erwartet for _, k in klappenbefehle(ha))
                assert app.anlaufschutz

    asyncio.run(ablauf())


def test_kalter_ofen_ohne_gueltigen_zustand_darf_zu(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha, abgas="30")
            async with betriebs_app(ha, tmp_path, main_modul.steuerung_aus_kern) as app:
                await warte_auf(lambda: klappenbefehle(ha))
                assert klappenbefehle(ha)[0][1]["position"] == 0 and not app.anlaufschutz

    asyncio.run(ablauf())


def test_feuer_wird_vom_kern_erkannt_und_der_anlaufschutz_endet(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha, abgas="200")      # über t_glut: Der Kern geht sofort ins Anheizen
            async with betriebs_app(ha, tmp_path, main_modul.steuerung_aus_kern) as app:
                await warte_auf(lambda: NEU in app.letzte and not app.anlaufschutz)
                assert app.letzte[NEU][0].zustand.phase == "Anheizen"
                await warte_auf(lambda: klappenbefehle(ha))
                assert klappenbefehle(ha)[0][1]["position"] >= 60

    asyncio.run(ablauf())


def test_tuer_auf_oeffnet_sofort(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            gesicherter_zustand(tmp_path, 10)
            async with betriebs_app(ha, tmp_path, main_modul.steuerung_aus_kern, takt_neu_s=3600.0):
                await ha.aendern(E.tuer, "on")
                await warte_auf(lambda: klappenbefehle(ha) and klappenbefehle(ha)[-1] == AUF)

    asyncio.run(ablauf())


def test_handbetrieb_greift_im_betrieb(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            gesicherter_zustand(tmp_path, 10)
            s = betrieb_speicher(tmp_path)
            s.parameter_setzen("klappe_manuell", 30)
            async with betriebs_app(ha, tmp_path, main_modul.steuerung_aus_kern, speicher=s, takt_neu_s=3600.0):
                await ha.aendern(E.handbetrieb, "on")
                await warte_auf(lambda: klappenbefehle(ha) and klappenbefehle(ha)[-1][1]["position"] == 30)

    asyncio.run(ablauf())


def test_wartung_bei_kaltem_ofen_greift_im_betrieb(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha, abgas="30")
            s = betrieb_speicher(tmp_path)
            s.parameter_setzen("klappe_wartung", 80)
            gesicherter_zustand(tmp_path, 10, phase="Standby", klappe=0)
            async with betriebs_app(ha, tmp_path, main_modul.steuerung_aus_kern, speicher=s, takt_neu_s=3600.0) as app:
                await ha.aendern(E.wartung, "on")
                await warte_auf(lambda: klappenbefehle(ha) and klappenbefehle(ha)[-1][1]["position"] == 80)
                assert app.letzte[NEU][0].zustand.phase == "Wartung"

    asyncio.run(ablauf())


def test_wartung_bei_heissem_ofen_wird_abgelehnt_und_ausgeschaltet(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha, abgas="200")
            gesicherter_zustand(tmp_path, 10)
            async with betriebs_app(ha, tmp_path, main_modul.steuerung_aus_kern, takt_neu_s=3600.0):
                await ha.aendern(E.wartung, "on")
                await warte_auf(lambda: ("input_boolean.turn_off", {"entity_id": E.wartung}) in ha.schaltbefehle())

    asyncio.run(ablauf())


def test_invertierter_helfer_der_automation_gilt_auch_fuer_die_sichere_stellung(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            ha.setzen("input_boolean.kamin_abbrand_klappe_invertiert", "on")
            s = betrieb_speicher(tmp_path)
            async with betriebs_app(ha, tmp_path, StubFabrik(), speicher=s):
                await warte_auf(lambda: klappenbefehle(ha))
            assert klappenbefehle(ha)[-1][1]["position"] == 0

    asyncio.run(ablauf())


def test_verlauf_zeigt_im_betrieb_die_gesendete_klappe(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha, abgas="70")
            fabrik = main_modul.steuerung_aus_kern
            async with betriebs_app(ha, tmp_path, fabrik, takt_paritaet_s=0.03, karenz_s=3600.0) as app:
                await warte_auf(lambda: len(klappenbefehle(ha)) >= 1 and PARITAET in app.letzte)
                await asyncio.sleep(0.1)
            zeilen = [json.loads(z) for z in next(tmp_path.glob("verlauf-*.jsonl")).read_text(encoding="utf-8").splitlines()]
            paritaet = [z for z in zeilen if z["regelstand"] == "paritaet" and z.get("live")]
            assert paritaet and paritaet[-1]["live"]["klappe_soll"] == 100

    asyncio.run(ablauf())


# ---- Handbetrieb der App ---------------------------------------------

def test_handbetrieb_der_app_stellt_klappe_und_endet_von_selbst(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            gesicherter_zustand(tmp_path, 10)
            s = betrieb_speicher(tmp_path)
            s.parameter_setzen("klappe_manuell", 30)
            async with betriebs_app(ha, tmp_path, main_modul.steuerung_aus_kern, speicher=s) as app:
                await warte_auf(lambda: klappenbefehle(ha))
                bis = app.hand_setzen(True, 15)
                assert bis is not None and app.hand_aktiv(app._uhr())
                await warte_auf(lambda: klappenbefehle(ha)[-1][1]["position"] == 30)
                assert ansicht.status(app)["hand"]["aktiv"] and ansicht.status(app)["handbetrieb"]
                s.daten["hand"]["bis"] = app._uhr() - 1          # Zeit abgelaufen
                await warte_auf(lambda: s.hand_bis is None)
                assert not app.hand_aktiv(app._uhr())
                assert any("Handbetrieb nach 15 min beendet" in m["text"] for m in app.meldungen)

    asyncio.run(ablauf())


def test_handbetrieb_der_app_bleibt_unter_uebertemperatur_gekappt(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha, abgas="800")
            gesicherter_zustand(tmp_path, 10)
            s = betrieb_speicher(tmp_path)
            s.parameter_setzen("klappe_manuell", 90)
            async with betriebs_app(ha, tmp_path, main_modul.steuerung_aus_kern, speicher=s) as app:
                app.hand_setzen(True)
                await warte_auf(lambda: app.letzte.get(NEU) and app.hand_aktiv(app._uhr())
                                and app.letzte[NEU][0].klappe <= s.parameter_neu.get("klappe_uebertemperatur", 25))

    asyncio.run(ablauf())


def test_handbetrieb_der_app_geht_im_lauschen_nicht_und_alter_wird_beendet(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            s = betrieb_speicher(tmp_path, modus="lauschen")
            s.daten["hand"]["bis"] = 4_000_000_000.0          # aus einem früheren Betrieb übrig
            fabrik = StubFabrik()
            async with betriebs_app(ha, tmp_path, fabrik, speicher=s, takt_paritaet_s=0.03) as app:
                await warte_auf(lambda: s.hand_bis is None)
                with pytest.raises(EinstellungsFehler):
                    app.hand_setzen(True)
                assert not ansicht.status(app)["hand"]["moeglich"]
            assert ha.schaltbefehle() == []

    asyncio.run(ablauf())


def test_lauschen_beendet_den_handbetrieb(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            anlage_setzen(ha)
            gesicherter_zustand(tmp_path, 10)
            s = betrieb_speicher(tmp_path)
            async with betriebs_app(ha, tmp_path, StubFabrik(), speicher=s) as app:
                app.hand_setzen(True, 60)
                await app._betrieb_verlassen()
                assert s.hand_bis is None and not app.hand_aktiv(app._uhr())

    asyncio.run(ablauf())
