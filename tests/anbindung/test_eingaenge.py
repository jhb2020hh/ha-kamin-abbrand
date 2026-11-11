import logging

import pytest

import dataclasses

from anbindung.eingaenge import Eingaenge, als_zahl
from testdaten import ENTITAETEN
from fake_ha import zustand
from kern.typen import BEDIENUNG, TICK, TUER, WARTUNG_AN, WARTUNG_AUS, Parameter

JETZT = 1_800_000_000.0
E = ENTITAETEN


def baue(*zustaende, entitaeten=E):
    eingaenge = Eingaenge(entitaeten, "kamin_abbrand")
    eingaenge.aufbauen(list(zustaende), JETZT)
    return eingaenge


def ereignis(eingaenge, eid, wert, zeit=JETZT, jetzt=JETZT):
    return eingaenge.ereignis({"entity_id": eid, "new_state": zustand(eid, wert, zeit=zeit)}, jetzt)


@pytest.mark.parametrize("wert,erwartet", [
    ("165.0", 165.0), ("0", 0.0), ("-3.5", -3.5),
    ("unavailable", None), ("unknown", None), ("", None), ("nan", None), ("inf", None), (None, None),
])
def test_als_zahl(wert, erwartet):
    assert als_zahl(wert) == erwartet


def test_eingaben_mit_gueltigen_werten():
    e = baue(
        zustand(E.abgas, "165.5", zeit=JETZT - 12.7),
        zustand(E.gradient, "1.5"),
        zustand(E.t_max_3min, "170"),
        zustand(E.tuer, "on"),
        zustand(E.t_wt, "61.2"),
        zustand(E.t_wt_anstieg, "4"),
        zustand(E.puffer_unten, "55"),
        zustand(E.wartung, "on"),
        zustand(E.handbetrieb, "off"),
    ).eingaben(JETZT, TICK)
    assert (e.t, e.g, e.tmax3, e.twt_roh, e.twt_anstieg, e.puffer_unten) == (165.5, 1.5, 170, 61.2, 4, 55)
    assert e.t_alter_s == 12.0
    assert e.tuer_offen and e.wartung and not e.handbetrieb
    assert e.jetzt == JETZT and e.ausloeser == TICK


@pytest.mark.parametrize("wert", ["unavailable", "unknown"])
def test_unavailable_zweig(wert):
    e = baue(*(zustand(eid, wert) for eid in (
        E.abgas, E.gradient, E.t_max_3min, E.tuer, E.t_wt, E.t_wt_anstieg, E.puffer_unten,
        E.wartung, E.handbetrieb, E.tuerkontakt_vorhanden,
    ))).eingaben(JETZT, TICK)
    assert e.t is None and e.tmax3 is None and e.twt_roh is None
    assert e.g == 0.0 and e.twt_anstieg == 0.0 and e.puffer_unten == 0.0
    assert not e.tuer_offen and not e.wartung and not e.handbetrieb
    assert e.tuerkontakt_vorhanden


def test_fehlende_entitaeten_ergeben_vorgaben():
    e = baue().eingaben(JETZT, TICK)
    assert e.t is None and e.t_alter_s == 0.0 and e.dp_alter_s is None and e.can_alter_s is None


def test_t_alter_folgt_last_reported_auch_ohne_wertaenderung():
    roh = zustand(E.abgas, "100", zeit=JETZT - 500)
    roh["last_reported"] = zustand(E.abgas, "100", zeit=JETZT - 20)["last_reported"]
    assert baue(roh).eingaben(JETZT, TICK).t_alter_s == 20


def test_t_alter_ohne_last_reported_nutzt_last_updated():
    roh = zustand(E.abgas, "100", zeit=JETZT - 30)
    del roh["last_reported"]
    assert baue(roh).eingaben(JETZT, TICK).t_alter_s == 30


def test_t_alter_ohne_zeitstempel_nutzt_empfang_der_wertaenderung():
    eingaenge = baue()
    eingaenge.ereignis({"entity_id": E.abgas, "new_state": {"state": "100"}}, JETZT - 40)
    eingaenge.ereignis({"entity_id": E.abgas, "new_state": {"state": "100"}}, JETZT - 10)
    assert eingaenge.eingaben(JETZT, TICK).t_alter_s == 40


def test_t_alter_wird_nie_negativ():
    assert baue(zustand(E.abgas, "100", zeit=JETZT + 5)).eingaben(JETZT, TICK).t_alter_s == 0


def test_dp_alter_zaehlt_nur_echte_aenderungen():
    eingaenge = baue(zustand(E.druck, "7.8", zeit=JETZT - 100))
    assert eingaenge.eingaben(JETZT, TICK).dp_alter_s == 100
    # Ein Ausfall und die Rückkehr zum gleichen Wert sind keine Druckänderung.
    ereignis(eingaenge, E.druck, "unavailable", zeit=JETZT - 50)
    assert eingaenge.eingaben(JETZT, TICK).dp_alter_s == 100
    ereignis(eingaenge, E.druck, "7.8", zeit=JETZT - 40)
    assert eingaenge.eingaben(JETZT, TICK).dp_alter_s == 100
    ereignis(eingaenge, E.druck, "7.9", zeit=JETZT - 10)
    assert eingaenge.eingaben(JETZT, TICK).dp_alter_s == 10


def test_can_alter_nur_mit_eingestellter_entitaet():
    can = "sensor.kamin_can_alter"
    assert baue(zustand(can, "12")).eingaben(JETZT, TICK).can_alter_s is None
    mit = dataclasses.replace(E, can_alter=can)
    assert baue(zustand(can, "12"), entitaeten=mit).eingaben(JETZT, TICK).can_alter_s == 12


def test_tuerkontakt_aus():
    e = baue(zustand(E.tuerkontakt_vorhanden, "off")).eingaben(JETZT, TICK)
    assert not e.tuerkontakt_vorhanden


def test_parameter_aus_allen_helfern():
    zustaende = [
        zustand(f"input_number.kamin_abbrand_{feld}", str(i + 1.5))
        for i, feld in enumerate(Parameter.feldnamen())
    ]
    parameter = baue(*zustaende).parameter
    for i, feld in enumerate(Parameter.feldnamen()):
        assert getattr(parameter, feld) == i + 1.5


def test_ungueltiger_parameter_behaelt_letzten_gueltigen_und_warnt(caplog):
    eid = "input_number.kamin_abbrand_t_ziel"
    eingaenge = baue(zustand(eid, "230"))
    parameter = eingaenge.parameter
    with caplog.at_level(logging.WARNING):
        ereignis(eingaenge, eid, "unavailable")
        ereignis(eingaenge, eid, "unavailable")
    assert parameter.t_ziel == 230
    assert sum(eid in r.getMessage() for r in caplog.records) == 1
    ereignis(eingaenge, eid, "240")
    assert eingaenge.parameter is parameter and parameter.t_ziel == 240


def test_ungueltiger_parameter_beim_start_behaelt_vorgabe():
    assert baue(zustand("input_number.kamin_abbrand_t_ziel", "unknown")).parameter.t_ziel == Parameter().t_ziel


def test_ausloeser():
    klappe = "input_number.kamin_abbrand_klappe_manuell"
    wartung_klappe = "input_number.kamin_abbrand_klappe_wartung"
    eingaenge = baue(zustand(E.wartung, "off"), zustand(E.tuer, "off"), zustand(klappe, "100"))
    assert ereignis(eingaenge, E.tuer, "on") == [TUER]
    assert ereignis(eingaenge, E.tuer, "off") == [TUER]
    assert ereignis(eingaenge, E.wartung, "on") == [WARTUNG_AN]
    assert ereignis(eingaenge, E.wartung, "on") == []
    assert ereignis(eingaenge, E.wartung, "off") == [WARTUNG_AUS]
    assert ereignis(eingaenge, E.wartung, "unavailable") == []
    assert ereignis(eingaenge, E.handbetrieb, "on") == [BEDIENUNG]
    assert ereignis(eingaenge, klappe, "80") == [BEDIENUNG]
    assert eingaenge.parameter.klappe_manuell == 80
    assert ereignis(eingaenge, wartung_klappe, "90") == [BEDIENUNG]
    assert ereignis(eingaenge, E.abgas, "100") == []
    assert ereignis(eingaenge, "sensor.fremd", "1") == []


def test_entfernte_entitaet():
    eingaenge = baue(zustand(E.abgas, "100"))
    assert eingaenge.ereignis({"entity_id": E.abgas, "new_state": None}, JETZT) == []
    assert eingaenge.eingaben(JETZT, TICK).t is None


def test_sensor_weg_erst_nach_60_s_und_einmal_je_ausfall():
    eingaenge = baue(zustand(E.abgas, "100"))
    assert not eingaenge.pruefe_sensor_weg(JETZT + 1000)
    ereignis(eingaenge, E.abgas, "unavailable", zeit=JETZT)
    assert not eingaenge.pruefe_sensor_weg(JETZT + 59)
    assert eingaenge.pruefe_sensor_weg(JETZT + 60)
    assert not eingaenge.pruefe_sensor_weg(JETZT + 61)
    ereignis(eingaenge, E.abgas, "100", zeit=JETZT + 70)
    ereignis(eingaenge, E.abgas, "unknown", zeit=JETZT + 80)
    assert not eingaenge.pruefe_sensor_weg(JETZT + 139)
    assert eingaenge.pruefe_sensor_weg(JETZT + 140)


def test_sensor_weg_unterbrechung_setzt_zurueck():
    eingaenge = baue(zustand(E.abgas, "100"))
    ereignis(eingaenge, E.abgas, "unavailable", zeit=JETZT)
    ereignis(eingaenge, E.abgas, "101", zeit=JETZT + 50)
    assert not eingaenge.pruefe_sensor_weg(JETZT + 100)


def test_sensor_weg_wechsel_unavailable_unknown_bleibt_durchgehend():
    eingaenge = baue(zustand(E.abgas, "100"))
    ereignis(eingaenge, E.abgas, "unavailable", zeit=JETZT)
    ereignis(eingaenge, E.abgas, "unknown", zeit=JETZT + 30)
    assert eingaenge.pruefe_sensor_weg(JETZT + 60)


def test_sensor_weg_bleibt_nach_neuaufbau_gemeldet():
    eingaenge = baue(zustand(E.abgas, "unavailable", zeit=JETZT - 100))
    assert eingaenge.pruefe_sensor_weg(JETZT)
    eingaenge.aufbauen([zustand(E.abgas, "unavailable", zeit=JETZT + 5)], JETZT + 10)
    assert not eingaenge.pruefe_sensor_weg(JETZT + 100)


def helfer(phase="Ausbrand"):
    return [
        zustand("input_select.kamin_abbrand_phase", phase),
        zustand("input_datetime.kamin_abbrand_phase_seit", "2026-11-06 14:52:30", {"timestamp": 1793973150}),
        zustand("input_number.kamin_abbrand_t_maximum", "220.0"),
        zustand("input_number.kamin_abbrand_t_phasenstart", "191.0"),
        zustand("input_number.kamin_abbrand_regler_i_anteil", "57.9"),
        zustand("input_number.kamin_abbrand_klappe_soll", "72.0"),
    ]


def test_startzustand_aus_helfern():
    z = baue(*helfer()).startzustand(JETZT)
    assert (z.phase, z.phase_seit, z.t_maximum, z.t_phasenstart, z.i_anteil, z.klappe_soll) == (
        "Ausbrand", 1793973150, 220, 191, 57.9, 72,
    )
    assert isinstance(z.klappe_soll, int)


@pytest.mark.parametrize("phase", ["unavailable", "unknown", "Quatsch"])
def test_startzustand_wartet_auf_gueltige_phase(phase):
    assert baue(*helfer(phase)).startzustand(JETZT) is None


def test_startzustand_ohne_timestamp_gilt_ab_jetzt():
    z = baue(zustand("input_select.kamin_abbrand_phase", "Glut")).startzustand(JETZT)
    assert z.phase == "Glut" and z.phase_seit == JETZT


@pytest.mark.parametrize("eid, wert, attribute, erwartet", [
    ("cover.klappe", "open", {"current_position": 83}, 83),
    ("cover.klappe", "closed", {"current_position": 0}, 0),
    ("light.klappe", "on", {"brightness": 212}, 83),
    ("light.klappe", "off", {}, 0),
    ("light.klappe", "on", {}, None),
    ("number.klappe", "40", {}, 40),
    ("input_number.klappe", "40.5", {}, 40.5),
    ("cover.klappe", "unavailable", {"current_position": 83}, None),
    ("light.klappe", "unknown", {}, None),
])
def test_stellung(eid, wert, attribute, erwartet):
    eingaenge = Eingaenge(E, "kamin_abbrand", zusatz=[eid])
    eingaenge.aufbauen([zustand(eid, wert, attribute)], JETZT)
    assert eingaenge.stellung(eid) == erwartet


def test_stellung_ohne_zuweisung():
    assert baue().stellung(None) is None


def test_klappe_ist_rechnet_volt_und_ma_um_und_laesst_cover_in_ruhe():
    from anbindung.anlagenprofil import profil_pruefen
    profil = profil_pruefen({"ofen": "luftgefuehrt", "rueck_zu": 1.87, "rueck_auf": 10.01})
    vorgabe = profil_pruefen({"ofen": "luftgefuehrt"})
    eingaenge = Eingaenge(E, "kamin_abbrand", zusatz=("sensor.volt", "sensor.ma", "sensor.prozent", "sensor.mv",
                                                       "cover.klappe"))
    eingaenge.aufbauen([zustand("sensor.volt", "5.79", {"unit_of_measurement": "V"}),
                        zustand("sensor.ma", "12", {"unit_of_measurement": "mA"}),
                        zustand("sensor.prozent", "40", {"unit_of_measurement": "%"}),
                        zustand("sensor.mv", "500", {"unit_of_measurement": "mV"}),
                        zustand("cover.klappe", "open", {"current_position": 70})], JETZT)
    assert eingaenge.klappe_ist("sensor.volt", profil) == pytest.approx(48.16, abs=0.01)
    assert eingaenge.klappe_ist("sensor.volt", vorgabe) == pytest.approx(47.375)
    assert eingaenge.klappe_ist("sensor.ma", vorgabe) == pytest.approx(50.0)
    assert eingaenge.klappe_ist("sensor.prozent", vorgabe) == pytest.approx(40.0)
    assert eingaenge.klappe_ist("sensor.mv", vorgabe) is None
    assert eingaenge.klappe_ist("cover.klappe", profil) == 70
    assert eingaenge.klappe_ist("sensor.gibt_es_nicht", profil) is None
    assert eingaenge.klappe_ist(None, profil) is None
