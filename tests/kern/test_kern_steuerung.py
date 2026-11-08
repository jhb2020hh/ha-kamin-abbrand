"""Reihenfolge innerhalb eines Laufs: alte Variablen gegen frische Helferwerte."""
import pytest

from kern.steuerung import Steuerung
from kern.typen import (ABBRAND, ABKUEHLEN, ANHEIZEN, AUSBRAND, GLUT, NEU, PARITAET,
                        SENSOR_WEG, SIGNAL_FRISCH, STANDBY, STOERUNG, TUER_OFFEN, WARTUNG,
                        WARTUNG_AN, Parameter, Zustand)
from kern_hilfen import JETZT, JETZT_INT, eingaben, lauf, zustand


def test_unbekannter_regelstand_wird_abgelehnt():
    with pytest.raises(NotImplementedError):
        Steuerung(Parameter(), Zustand(), regelstand="gibt_es_nicht")


def test_regelstand_vorgabe_ist_paritaet():
    assert Steuerung(Parameter(), Zustand()).regelstand == PARITAET


def test_parameter_sind_zur_laufzeit_austauschbar():
    st = Steuerung(Parameter(klappe_glut=10), zustand(GLUT))
    assert st.schritt(eingaben(t=100.0)).klappe == 10
    st.parameter = Parameter(klappe_glut=20)
    assert st.schritt(eingaben(t=100.0)).klappe == 20


def test_ergebnis_ist_momentaufnahme_und_zustand_bleibt_dasselbe_objekt():
    z = zustand(GLUT)
    st = Steuerung(Parameter(), z)
    erg = st.schritt(eingaben(t=100.0))
    assert st.zustand is z and erg.zustand is not z and erg.zustand == z
    assert erg.signal == SIGNAL_FRISCH


def test_phase_setzen_stempelt_int_sekunden_und_begrenzt_auf_1200():
    st, _ = lauf(STANDBY, 100, t=1638.0)
    z = st.zustand
    assert z.phase == ANHEIZEN and z.phase_seit == JETZT_INT
    assert z.t_maximum == 1200 and z.t_phasenstart == 1200


def test_phase_setzen_ohne_messwert_schreibt_null():
    st, erg = lauf(ABBRAND, 100, dict(t_maximum=300.0, t_phasenstart=150.0), t=None,
                   ausloeser=SENSOR_WEG)
    assert st.zustand.t_maximum == 0 and st.zustand.t_phasenstart == 0
    assert erg.klappe == 100    # kein stop: Die Klappe öffnet noch im selben Lauf


@pytest.mark.parametrize("t,t_vorher,erwartet", [(150.0, 200.0, 200.0), (250.0, 200.0, 250.0)])
def test_tuer_zu_merkt_den_hoeheren_wert(t, t_vorher, erwartet):
    st, _ = lauf(TUER_OFFEN, 30, dict(t_phasenstart=t_vorher), t=t)
    assert st.zustand.t_phasenstart == erwartet


def test_tuer_auf_merkt_den_hoeheren_wert_nicht():
    st, _ = lauf(ABBRAND, 1000, dict(t_phasenstart=300.0, t_maximum=300.0), t=200.0, tuer_offen=True)
    assert st.zustand.t_phasenstart == 200.0


def test_i_start_liest_t_vorher_nicht_das_neue_t_phasenstart():
    st, _ = lauf(ANHEIZEN, 900, dict(t_phasenstart=65.0, i_anteil=99.0, t_maximum=160.0), t=170.0)
    assert st.zustand.phase == ABBRAND and st.zustand.t_phasenstart == 170.0
    assert st.zustand.i_anteil < 99.0


def test_uebergang_nach_abbrand_integriert_und_stellt_klappe_im_selben_lauf():
    # I-Start 55.0, sp = 0.5 < Entgasung; Integration: e = 50 → +1.8; P: 25
    st, erg = lauf(ANHEIZEN, 900, dict(t_phasenstart=65.0, t_maximum=160.0, klappe_soll=100),
                   t=170.0)
    assert st.zustand.i_anteil == 56.8
    assert erg.klappe == 90     # 81.8, aber Schließrate 100 - 10
    assert erg.uebergang == (ANHEIZEN, ABBRAND)


def test_uebergang_nach_abbrand_ohne_tick_integriert_nicht():
    st, _ = lauf(ANHEIZEN, 900, dict(t_phasenstart=65.0, t_maximum=160.0), t=170.0,
                 ausloeser="bedienung")
    assert st.zustand.i_anteil == 55.0


def test_i_integration_nutzt_frische_phase_auch_beim_verlassen():
    st, _ = lauf(ABBRAND, 500, dict(i_anteil=50.0, t_maximum=300.0), t=120.0)
    assert st.zustand.phase != ABBRAND and st.zustand.i_anteil == 50.0


def test_entgasung_liest_frisches_sp_nach_uebergang():
    # alte Phase lief 1000 s, neue Phase beginnt: Das Minimum der Entgasung gilt sofort
    st, erg = lauf(ANHEIZEN, 1000, dict(t_phasenstart=95.0, t_maximum=300.0, klappe_soll=46), t=220.0)
    assert st.zustand.phase == ABBRAND
    assert st.zustand.i_anteil == 45.0  # I-Start 40, im selben Tick aufs Minimum gehoben
    assert erg.klappe == 45


def test_klappe_liest_neue_phase():
    st, erg = lauf(GLUT, 100, dict(klappe_soll=10), t=79.0)
    assert st.zustand.phase == ABKUEHLEN and erg.klappe == 100    # sp < 60


@pytest.mark.parametrize("fall,phase,s,z,e,t_max", [
    ("nachfuehren_anheizen", ANHEIZEN, 10, dict(t_maximum=100.0), dict(t=120.0), 120.0),
    ("nachfuehren_abbrand", ABBRAND, 1000, dict(t_maximum=200.0), dict(t=210.0), 210.0),
    ("kein_rueckgang", ABBRAND, 1000, dict(t_maximum=200.0), dict(t=190.0), 200.0),
    ("nicht_in_glut", GLUT, 0, dict(t_maximum=100.0), dict(t=120.0), 100.0),
    ("deckel", ABBRAND, 1000, dict(t_maximum=1000.0), dict(t=1300.0), 1200),
])
def test_maximum_nachfuehren(fall, phase, s, z, e, t_max):
    assert lauf(phase, s, z, **e)[0].zustand.t_maximum == t_max


def test_maximum_prueft_neue_phase_und_frisches_t_maximum():
    # Glut → Anheizen: tmax alt = 500, frisch = t; t > frisch ist falsch, also bleibt t
    st, _ = lauf(GLUT, 100, dict(t_maximum=500.0), t=100.0, g=4.0)
    assert st.zustand.phase == ANHEIZEN and st.zustand.t_maximum == 100.0


@pytest.mark.parametrize("fall,z,e,t_max", [
    ("zieht_runter", dict(t_maximum=300.0), dict(t=200.0, twt_roh=80.1), 200.0),
    ("wasser_grenze", dict(t_maximum=300.0), dict(t=200.0, twt_roh=80.0), 300.0),
    ("abstand_eins", dict(t_maximum=201.0), dict(t=200.0, twt_roh=80.1), 200.0),
    ("abstand_kleiner_eins", dict(t_maximum=200.9), dict(t=200.0, twt_roh=80.1), 200.9),
    ("fuehler_offen", dict(t_maximum=300.0), dict(t=200.0, twt_roh=1638.0), 300.0),
])
def test_wasserbegrenzung_fuehrt_maximum_nach(fall, z, e, t_max):
    assert lauf(ABBRAND, 1000, z, **e)[0].zustand.t_maximum == t_max


def test_wasserbegrenzung_nutzt_alte_variable_tmax():
    # Abstand unter 1 K zur Variable tmax: Das Maximum bleibt stehen
    st, _ = lauf(ABBRAND, 1000, dict(t_maximum=200.5), t=200.0, twt_roh=90.0)
    assert st.zustand.t_maximum == 200.5
    # Im Übergangslauf ist tmax alt, das Schreiben ändert nichts mehr: frisch ist schon t
    st, _ = lauf(ANHEIZEN, 900, dict(t_maximum=500.0, t_phasenstart=20.0), t=170.0, twt_roh=90.0)
    assert st.zustand.phase == ABBRAND and st.zustand.t_maximum == 170.0


@pytest.mark.parametrize("tmax,ziel_t_max", [(300.0, 220), (200.0, 200.0)], ids=["ziel", "maximum"])
def test_ausbrand_setzt_maximum_auf_min_aus_altem_tmax_und_ziel(tmax, ziel_t_max):
    st, _ = lauf(ABBRAND, 900, dict(t_maximum=tmax), t=170.0, tmax3=170.0)
    assert st.zustand.phase == AUSBRAND and st.zustand.t_maximum == ziel_t_max
    assert st.zustand.t_phasenstart == 170.0


def test_stop_kein_messwert_laesst_alles_unveraendert():
    z = dict(klappe_soll=47, i_anteil=48.0, t_maximum=300.0, t_phasenstart=150.0)
    st, erg = lauf(ABBRAND, 1000, z, t=None)
    assert erg.klappe == 47 and erg.uebergang is None and erg.meldungen == []
    assert st.zustand == zustand(ABBRAND, 1000, **z)


def test_stop_wartung_abgelehnt_laesst_klappe_unveraendert():
    st, erg = lauf(GLUT, 100, dict(klappe_soll=33), t=120.0, wartung=True, ausloeser=WARTUNG_AN)
    assert erg.klappe == 33 and st.zustand.phase == GLUT and len(erg.meldungen) == 1


def test_ohne_stop_laeuft_klappe_auch_ohne_messwert():
    # Wartung beenden ohne Messwert: Standby, Klappe 0
    st, erg = lauf(WARTUNG, 100, dict(klappe_soll=100), t=None)
    assert st.zustand.phase == STANDBY and erg.klappe == 0


def test_wartung_timeout_im_selben_lauf_schon_klappe_der_neuen_phase():
    _, erg = lauf(WARTUNG, 3600, dict(klappe_soll=100), t=20.0, wartung=True)
    assert erg.klappe == 0


def test_stoerung_ohne_frischen_wert_bleibt_und_stempelt_nicht_neu():
    st, erg = lauf(STOERUNG, 100, dict(klappe_soll=100), t=90.0, t_alter_s=61.0)
    assert st.zustand.phase == STOERUNG and st.zustand.phase_seit == JETZT_INT - 100
    assert erg.klappe == 100


def test_ein_uebergang_je_lauf():
    # Tür auf und Abbrand-Bedingungen gleichzeitig: nur die erste Regel zählt
    st, erg = lauf(ANHEIZEN, 900, dict(t_maximum=170.0), t=170.0, tuer_offen=True)
    assert st.zustand.phase == TUER_OFFEN and erg.uebergang == (ANHEIZEN, TUER_OFFEN)


def test_wartung_timeout_gibt_den_vorrang_vor_feuer_ab():
    # Feuer-Regel steht vor Timeout
    st, erg = lauf(WARTUNG, 3600, t=60.0, wartung=True)
    assert st.zustand.phase == ANHEIZEN


def test_jetzt_mit_nachkommastelle_gibt_sp_aber_ganzes_s():
    st = Steuerung(Parameter(), zustand(ABKUEHLEN, 59))
    # s = 59, sp = 59.5 → noch Spülen; bei 60.0 nicht mehr
    assert st.schritt(eingaben(t=90.0, jetzt=JETZT)).klappe == 100
    assert st.schritt(eingaben(t=90.0, jetzt=JETZT + 0.5)).klappe == 10
