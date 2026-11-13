"""Einstellungen, Migration, Ableitungen, Wechsel zur Laufzeit, Start ohne Helfer."""
import asyncio
import dataclasses
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
from anbindung.ansicht import verlauf_lesen
from datetime import date
import time
from kern.typen import ABBRAND, GLUT, NEU, PARITAET, STANDBY, Zustand

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
                                          ("ausgang_heartbeat", "number.h"), ("ausgang_neustart", "script.n")])
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


BESTAND = {"rollen": {"abgas": "sensor.a", "t_wt": "sensor.w", "puffer_unten": "sensor.p"}, "tuerkontakt_vorhanden": False,
           "parameter_neu": {"t_ziel": 200.0}, "betrieb_s": 1234.5, "token": "geheim-und-lang", "kuenftig": {"x": 1}}


def test_bestehende_datei_laedt_ohne_datenverlust(tmp_path):
    pfad = tmp_path / "e.json"
    pfad.write_text(json.dumps(BESTAND), encoding="utf-8")
    s = Speicher(pfad)
    assert s.anlagenprofil.wassergefuehrt and s.anlagenprofil.puffer and s.anlagenprofil.tuerkontakt is False
    s.speichern()
    gelesen = json.loads(pfad.read_text(encoding="utf-8"))
    assert {k: gelesen[k] for k in BESTAND} == BESTAND and "anlagenprofil" in gelesen


def test_neue_rollen_sind_zuweisbar_aber_kein_kern_eingang(tmp_path):
    rollen = {"vorlauf": "sensor.v", "ruecklauf": "sensor.r", "pumpe": "binary_sensor.p", "puffer_oben": "sensor.po",
              "waermezaehler": "sensor.wz", "energiezaehler": "sensor.ez", "t_aussen": "sensor.ta"}
    s = Speicher(tmp_path / "e.json")
    s.rollen_setzen(rollen, True)
    assert s.rollen == rollen
    ent = dataclasses.asdict(s.entitaeten())
    assert not set(rollen) & set(ent) and not set(rollen.values()) & set(ent.values())


def test_profil_speichern_und_neu_laden(tmp_path):
    pfad = tmp_path / "e.json"
    s = Speicher(pfad)
    s.rollen_setzen({"abgas": "sensor.a", "t_wt": "sensor.w"}, True)
    p = s.profil_setzen({"ofen": "luftgefuehrt", "tuerkontakt": False, "waehrung": "CHF",
                         "erzeuger": [{"art": "erdgas", "anteil": 100}]})
    # Das Umstellen auf luftgeführt löscht keine Zuweisung.
    neu = Speicher(pfad)
    assert neu.anlagenprofil == p and neu.rollen["t_wt"] == "sensor.w"
    assert neu.tuerkontakt_vorhanden is False and "tuerkontakt" not in neu.daten["anlagenprofil"]


def test_tuerkontakt_gibt_es_nur_einmal(tmp_path):
    s = Speicher(tmp_path / "e.json")
    s.profil_setzen({"ofen": "luftgefuehrt"})
    s.tuerkontakt_setzen(False)
    assert s.anlagenprofil.tuerkontakt is False
    s.profil_setzen({"ofen": "luftgefuehrt"})     # Ohne Angabe bleibt, was gilt.
    assert s.tuerkontakt_vorhanden is False


def test_ungueltiges_profil_aendert_nichts(tmp_path):
    s = Speicher(tmp_path / "e.json")
    vorher = dict(s.daten)
    with pytest.raises(EinstellungsFehler, match="Ofenart"):
        s.profil_setzen({"ofen": "unbekannt"})
    with pytest.raises(EinstellungsFehler):
        s.profil_setzen("kein objekt")
    assert s.daten == vorher


@pytest.mark.parametrize("kaputt", [{"ofen": "unbekannt"}, "kein objekt", {"ofen": "luftgefuehrt", "erzeuger": [{"art": "kohle"}]}])
def test_kaputtes_gespeichertes_profil_bleibt_erhalten(tmp_path, kaputt):
    pfad = tmp_path / "e.json"
    pfad.write_text(json.dumps(BESTAND | {"anlagenprofil": kaputt}), encoding="utf-8")
    s = Speicher(pfad)
    assert s.anlagenprofil.wassergefuehrt       # Ableitung aus den Rollen
    s.speichern()
    assert json.loads(pfad.read_text(encoding="utf-8"))["anlagenprofil"] == kaputt


def test_abgas_in_fahrenheit_erreicht_den_kern_in_celsius():
    ein = Eingaenge(Entitaeten(abgas="sensor.a"), "kamin_abbrand")
    ein.aufbauen([{"entity_id": "sensor.a", "state": "212", "attributes": {"unit_of_measurement": "°F"}}], 1000.0)
    assert ein.eingaben(1000.0, "tick").t == pytest.approx(100.0)
    assert ein.temperatur_c("sensor.a") == pytest.approx(100.0)
    ein.aufbauen([{"entity_id": "sensor.a", "state": "392", "attributes": {"unit_of_measurement": "°F"}}], 1060.0)
    assert ein.ableitung.t_max_3min(1060.0) == pytest.approx(200.0)


def test_abgas_in_celsius_bleibt_unveraendert():
    ein = Eingaenge(Entitaeten(abgas="sensor.a"), "kamin_abbrand")
    ein.aufbauen([{"entity_id": "sensor.a", "state": "213.37", "attributes": {"unit_of_measurement": "°C"}}], 1000.0)
    assert ein.eingaben(1000.0, "tick").t == 213.37
    ein.aufbauen([{"entity_id": "sensor.a", "state": "unavailable"}], 1001.0)
    assert ein.eingaben(1001.0, "tick").t is None and ein.temperatur_c("sensor.a") is None


def test_neue_temperaturen_werden_umgerechnet_und_alte_kerneingaenge_nicht():
    ent = Entitaeten(t_wt="sensor.w", puffer_unten="sensor.p")
    ein = Eingaenge(ent, "kamin_abbrand", ("sensor.v",))
    f = {"unit_of_measurement": "°F"}
    ein.aufbauen([{"entity_id": e, "state": "212", "attributes": f} for e in ("sensor.v", "sensor.w", "sensor.p")], 1000.0)
    assert ein.temperatur_c("sensor.v") == pytest.approx(100.0)
    e = ein.eingaben(1000.0, "tick")
    assert e.twt_roh == 212 and e.puffer_unten == 212


def _app_ohne_verbindung(tmp_path, speicher):
    return App(partial(HaClient, TOKEN), Eingaenge(speicher.entitaeten(), "kamin_abbrand"), Verlauf(tmp_path),
               steuerung_fabrik=steuerung_aus_kern, speicher=speicher)


def test_zusatz_liest_neue_rollen_und_erzeugerzaehler_nur_passend_zum_profil(tmp_path):
    s = Speicher(tmp_path / "e.json")
    s.rollen_setzen({"abgas": "sensor.a", "vorlauf": "sensor.v", "t_aussen": "sensor.ta", "klappe_ist": "cover.k"}, True)
    s.profil_setzen({"ofen": "wassergefuehrt", "erzeuger": [{"art": "erdgas", "zaehler": "sensor.gas", "zaehler_einheit": "m³"}]})
    app = _app_ohne_verbindung(tmp_path, s)
    helfer = set(app._dauer_helfer())
    assert set(app.zusatz_entitaeten()) == {"sensor.v", "sensor.ta", "cover.k", "sensor.gas"} | helfer
    s.profil_setzen({"ofen": "luftgefuehrt", "erzeuger": []})
    assert set(app.zusatz_entitaeten()) == {"sensor.ta", "cover.k"} | helfer


def _zustaende(werte):
    return [{"entity_id": eid, "state": str(w), "attributes": a, "last_changed": "2026-11-09T20:00:00+00:00"}
            for eid, (w, a) in werte.items()]


def test_laufzeiten_werden_einmalig_aus_den_live_helfern_uebernommen(tmp_path):
    s = Speicher(tmp_path / "e.json")
    s.rollen_setzen({"abgas": "sensor.a"}, True)
    app = _app_ohne_verbindung(tmp_path, s)
    app.eingaenge.tauschen(s.entitaeten(), app.zusatz_entitaeten(), _zustaende({
        "counter.kamin_abbrand_anzahl_gesamt": (102, {}), "counter.kamin_abbrand_anzahl_heute": (7, {}),
        "input_number.kamin_abbrand_dauer_summe_gesamt": (4932.0, {}), "input_number.kamin_abbrand_dauer_summe_heute": (294.0, {}),
        "input_number.kamin_abbrand_dauer_letzte": (20.5, {}), "input_boolean.kamin_abbrand_dauer_lauft": ("on", {}),
        "input_datetime.kamin_abbrand_dauer_start": ("x", {"timestamp": 1000.0}),
    }), 2000.0)
    app._dauer_verfolgen(Zustand(phase=ABBRAND, phase_seit=1200.0), 2000.0)
    assert app.dauer.anzahl_gesamt == 102 and app.dauer.letzte_min == 20.5 and app.dauer.laeuft and app.dauer.start == 1000.0
    assert s.daten["abbrand_dauer"]["anzahl_gesamt"] == 102
    app._dauer_verfolgen(Zustand(phase=GLUT, phase_seit=2600.0), 2600.0)
    assert app.dauer.anzahl_gesamt == 103 and app.dauer.letzte_min == 26.7
    neu = _app_ohne_verbindung(tmp_path, Speicher(tmp_path / "e.json"))
    neu._dauer_verfolgen(Zustand(phase=GLUT, phase_seit=2600.0), 2700.0)
    assert neu.dauer.anzahl_gesamt == 103


def test_ohne_live_helfer_beginnt_die_statistik_bei_null(tmp_path):
    s = Speicher(tmp_path / "e.json")
    s.rollen_setzen({"abgas": "sensor.a"}, True)
    app = _app_ohne_verbindung(tmp_path, s)
    app._dauer_verfolgen(Zustand(phase=STANDBY, phase_seit=0.0), 100.0)
    assert app.dauer.anzahl_gesamt == 0 and not app.dauer.vorbelegt


def test_verlauf_liefert_leistung_ziel_und_klappe_ist(tmp_path):
    s = Speicher(tmp_path / "e.json")
    s.rollen_setzen({"abgas": "sensor.a", "waermeleistung": "sensor.w", "klappe_ist": "sensor.ist"}, True)
    s.profil_setzen({"ofen": "wassergefuehrt", "erzeuger": []})
    app = _app_ohne_verbindung(tmp_path, s)
    app.eingaenge.tauschen(s.entitaeten(), app.zusatz_entitaeten(), _zustaende({
        "sensor.w": (1700, {"unit_of_measurement": "W"}), "sensor.ist": (17, {}),
    }), 2000.0)
    a = app._anzeige()
    assert a["leistung_kw"] == 1.7 and a["klappe_ist"] == 17.0 and a["ziel"] == app.eingaenge.parameter.t_ziel
    jetzt = time.time()
    tag = date.fromtimestamp(jetzt).isoformat()
    (tmp_path / f"verlauf-{tag}.jsonl").write_text(
        json.dumps({"regelstand": "paritaet", "eingaben": {"jetzt": jetzt - 10, "t": 200.0, "twt_roh": 60.0}, "anzeige": a,
                    "live": {"klappe_soll": 40, "phase": "Abbrand"}}) + "\n", encoding="utf-8")
    d = verlauf_lesen(tmp_path, 1, jetzt)
    assert d["leistung"] == [1.7] and d["ziel"] == [a["ziel"]] and d["klappe_ist"] == [17.0] and d["twt"] == [60.0]


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
        assert "call_service" not in text, pfad
        # Dienste gibt es nur für notify.<name>, für schalte im Betrieb und für die freigegebene Automation,
        # und nur im Client.
        assert pfad.name == "ha_client.py" or "/services/" not in text, pfad
        assert text.count("/services/") == (3 if pfad.name == "ha_client.py" else 0), pfad
        assert ("/services/notify/" in text and "/services/{domain}/{dienst}" in text
                and "/services/automation/{dienst}" in text) or pfad.name != "ha_client.py"


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


def test_entfallene_rolle_ausgang_meldungen_stoert_beim_laden_nicht(tmp_path):
    import json
    pfad = tmp_path / "e.json"
    pfad.write_text(json.dumps({"rollen": {"abgas": "sensor.a", "ausgang_meldungen": "notify.handy"}}), encoding="utf-8")
    s = Speicher(pfad)
    assert s.rollen == {"abgas": "sensor.a"}
    s.rollen_setzen(s.rollen, True)
    assert "ausgang_meldungen" not in json.loads(pfad.read_text(encoding="utf-8"))["rollen"]


def test_handbetrieb_im_speicher(tmp_path):
    s = Speicher(tmp_path / "e.json")
    assert s.hand_bis is None and s.hand_dauer_min == 30 and not s.hand_aktiv(1000.0)
    bis = s.hand_starten(1000.0, 15)
    assert bis == 1000.0 + 900 and s.hand_aktiv(1899.0) and not s.hand_aktiv(1900.0)
    assert s.hand_rest_s(1600.0) == 300
    with pytest.raises(EinstellungsFehler):
        s.hand_starten(1000.0, 7)
    # bleibt über einen Neustart erhalten, eine verdorbene Angabe fällt auf die Vorgabe zurück
    assert Speicher(tmp_path / "e.json").hand_bis == bis
    s.hand_beenden()
    assert Speicher(tmp_path / "e.json").hand_bis is None
    (tmp_path / "f.json").write_text('{"hand": {"bis": "x", "dauer_min": 999}}')
    f = Speicher(tmp_path / "f.json")
    assert f.hand_bis is None and f.hand_dauer_min == 30


@pytest.mark.parametrize("roh, erwartet", [(True, True), (False, False), ("ja", False), (1, False), (None, False)])
def test_merker_der_automation_laden_und_pruefen(tmp_path, roh, erwartet):
    pfad = tmp_path / "e.json"
    pfad.write_text(json.dumps({"rollen": {}, "automation_von_app_aus": roh}), encoding="utf-8")
    assert Speicher(pfad).automation_von_app_aus is erwartet


def test_merker_der_automation_wird_sofort_gespeichert(tmp_path):
    s = Speicher(tmp_path / "e.json")
    assert s.automation_von_app_aus is False
    s.automation_merken(True)
    assert Speicher(tmp_path / "e.json").automation_von_app_aus is True
    s.automation_merken(False)
    assert Speicher(tmp_path / "e.json").automation_von_app_aus is False
