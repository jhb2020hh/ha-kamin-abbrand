"""Alle Zweige der Klappenberechnung."""
import pytest

from kern.typen import (ABBRAND, ABKUEHLEN, ANHEIZEN, ANHEIZFEHLER, AUSBRAND, GLUT, STANDBY,
                        STOERUNG, TUER_OFFEN, WARTUNG, Parameter)
from kern.klappe import soll_berechnen
from kern.signale import werte_ermitteln
from kern_hilfen import eingaben, klappe_nach, lauf, zustand


@pytest.mark.parametrize("fall,tref,t,klappe", [
    ("tref_unter_glut", 129.9, 150.0, 100),
    ("tref_gleich_glut", 130.0, 150.0, 100),
    ("linear_mitte", 145.0, 150.0, 80),
    ("linear_viertel", 137.5, 150.0, 90),
    ("linear_kurz_vor_start", 159.9, 150.0, 60),
    ("tref_gleich_start", 160.0, 150.0, 60),
    ("tref_ueber_start", 300.0, 150.0, 60),
    ("t_faellt_unter_glut", 300.0, 129.9, 100),
    ("t_faellt_unter_glut_linear", 145.0, 129.9, 100),
])
def test_anheiz_startfaelle(fall, tref, t, klappe):
    assert klappe_nach(ANHEIZEN, 10, dict(t_phasenstart=tref, t_maximum=t), t=t) == klappe


def test_anheiz_rampe_folgt_klappe_anheizen_feuer():
    p = Parameter(klappe_anheizen_feuer=40)
    assert klappe_nach(ANHEIZEN, 10, dict(t_phasenstart=145.0, t_maximum=150.0), p, t=150.0) == 70


def test_anheizen_nutzt_frisches_t_phasenstart_im_uebergangslauf():
    # Tür zu: t_phasenstart = max(t, t_vorher) = 200, also schon im selben Lauf 60 %
    st, erg = lauf(TUER_OFFEN, 30, dict(t_phasenstart=200.0), t=150.0)
    assert st.zustand.phase == ANHEIZEN and st.zustand.t_phasenstart == 200.0
    assert erg.klappe == 60


@pytest.mark.parametrize("phase,s,z,e,klappe", [
    (STANDBY, 0, dict(klappe_soll=55), dict(t=20.0), 0),
    (TUER_OFFEN, 0, {}, dict(t=100.0, tuer_offen=True), 100),
    (STOERUNG, 0, {}, dict(t=100.0, t_alter_s=60.0), 100),
    (GLUT, 0, {}, dict(t=100.0), 10),
    (WARTUNG, 0, {}, dict(t=20.0, wartung=True), 100),
    ("unbekannt", 0, {}, dict(t=20.0), 100),
], ids=["standby", "tuer", "stoerung", "glut", "wartung", "unbekannte_phase"])
def test_feste_phasen(phase, s, z, e, klappe):
    assert klappe_nach(phase, s, z, **e) == klappe


@pytest.mark.parametrize("fall,s,z,e,klappe", [
    ("regler", 1000, dict(i_anteil=50.0, klappe_soll=50), dict(t=200.0, ausloeser="tuer"), 60),
    ("vorhaltung_null", 1000, dict(i_anteil=50.0, klappe_soll=50),
     dict(t=200.0, g=-9.0, ausloeser="tuer"), 60),
    ("minimum_danach", 1000, dict(i_anteil=10.0, klappe_soll=35),
     dict(t=220.0, ausloeser="tuer"), 35),
    ("minimum_entgasung", 599, dict(i_anteil=10.0, klappe_soll=45),
     dict(t=220.0, ausloeser="tuer"), 45),
    ("entgasung_vorbei", 600, dict(i_anteil=10.0, klappe_soll=35),
     dict(t=220.0, ausloeser="tuer"), 35),
    ("maximum", 1000, dict(i_anteil=95.0, klappe_soll=50, t_maximum=300.0),
     dict(t=200.0, ausloeser="tuer"), 100),
    ("oeffnen_sofort", 1000, dict(i_anteil=70.0, klappe_soll=35), dict(t=200.0, ausloeser="tuer"), 80),
    ("schliessrate", 1000, dict(i_anteil=40.0, klappe_soll=80), dict(t=220.0, ausloeser="tuer"), 70),
    ("schliessrate_float_soll", 1000, dict(i_anteil=40.0, klappe_soll=80.6),
     dict(t=220.0, ausloeser="tuer"), 71),
])
def test_abbrand(fall, s, z, e, klappe):
    z.setdefault("t_maximum", 300.0)
    assert klappe_nach(ABBRAND, s, z, **e) == klappe


def test_abbrand_vorhaltung():
    p = Parameter(regler_vorhaltung=2)
    assert klappe_nach(ABBRAND, 1000, dict(i_anteil=50.0, klappe_soll=50, t_maximum=300.0), p,
                       t=200.0, g=2.0, ausloeser="tuer") == 56


def test_schliessrate_mindestens_eins():
    p = Parameter(klappe_schliessrate=0)
    assert klappe_nach(ABBRAND, 1000, dict(i_anteil=40.0, klappe_soll=80, t_maximum=300.0), p,
                       t=220.0, ausloeser="tuer") == 79


def test_schliessrate_gilt_auch_ohne_tick():
    st, erg = lauf(ABBRAND, 1000, dict(i_anteil=40.0, klappe_soll=80, t_maximum=300.0),
                   t=220.0, ausloeser="bedienung")
    assert erg.klappe == 70
    assert st.schritt(eingaben(t=220.0, ausloeser="bedienung")).klappe == 60


@pytest.mark.parametrize("soll,kmax,klappe", [
    (30, 100, 60), (60, 100, 60), (75, 100, 75), (90, 80, 80),
], ids=["mindestens_60", "gleich_60", "haelt", "kappt_maximum"])
def test_ausbrand(soll, kmax, klappe):
    p = Parameter(klappe_max_abbrand=kmax)
    assert klappe_nach(AUSBRAND, 0, dict(klappe_soll=soll), p, t=180.0) == klappe


@pytest.mark.parametrize("s,t,klappe", [
    (59, 90.0, 100),
    (60, 80.0, 10),
    (600, 90.0, 10),
    (60, 79.9, 10),
    (660, 70.0, 5),
    (1259, 70.0, 0),
    (1260, 70.0, 0),
], ids=["spuelen", "ab_60_warm", "warm_bleibt", "rampe_start", "rampe_mitte", "rampe_ende",
        "rampe_vorbei"])
def test_abkuehlen(s, t, klappe):
    assert klappe_nach(ABKUEHLEN, s, t=t) == klappe



@pytest.mark.parametrize("s,klappe", [(0, 100), (2700, 50), (5399, 0)])
def test_anheizfehler_rampe(s, klappe):
    assert klappe_nach(ANHEIZFEHLER, s, t=100.0) == klappe


def test_handbetrieb_ueberschreibt_phase_aber_nicht_wartung():
    p = Parameter(klappe_manuell=33, klappe_wartung=77)
    assert klappe_nach(GLUT, 0, None, p, t=100.0, handbetrieb=True) == 33
    assert klappe_nach(WARTUNG, 0, None, p, t=20.0, wartung=True, handbetrieb=True) == 77


@pytest.mark.parametrize("phase,e,klappe", [
    (GLUT, dict(t=400.1), 10),
    (STOERUNG, dict(t=400.1, t_alter_s=60.0), 25),
    (STOERUNG, dict(t=400.0, t_alter_s=60.0), 100),
    (TUER_OFFEN, dict(t=400.1, tuer_offen=True), 25),
], ids=["kleinerer_wert_bleibt", "stoerung_begrenzt", "grenze_selbst", "tuer_begrenzt"])
def test_uebertemperatur(phase, e, klappe):
    assert klappe_nach(phase, 0, **e) == klappe


def test_uebertemperatur_gilt_nicht_in_wartung():
    # Bei heißem Ofen verlässt die Feuer-Regel die Wartung vorher,
    # darum direkt an der Berechnung geprüft.
    z = zustand(WARTUNG)
    w = werte_ermitteln(eingaben(t=500.0), Parameter(), z)
    assert soll_berechnen(eingaben(t=500.0), z, w) == 100


def test_uebertemperatur_begrenzt_auch_handbetrieb():
    assert klappe_nach(STOERUNG, 0, t=500.0, t_alter_s=60.0, handbetrieb=True) == 25


def test_uebertemperatur_wartet_nicht_auf_schliessrate():
    z = dict(i_anteil=90.0, klappe_soll=90, t_maximum=500.0)
    assert klappe_nach(ABBRAND, 1000, z, t=401.0, ausloeser="tuer") == 25


@pytest.mark.parametrize("fall,phase,z,e,klappe", [
    ("heiss_abbrand", ABBRAND, dict(i_anteil=90.0, klappe_soll=90),
     dict(t=170.0, twt_roh=85.1), 40),
    ("heiss_t_knapp", ABBRAND, dict(i_anteil=90.0, klappe_soll=90),
     dict(t=169.9, twt_roh=85.1), 100),
    ("wasser_gleich_max", ABBRAND, dict(i_anteil=90.0, klappe_soll=90),
     dict(t=170.0, twt_roh=85.0), 100),
    ("halten_abbrand", ABBRAND, dict(i_anteil=90.0, klappe_soll=40),
     dict(t=150.0, twt_roh=80.1), 40),
    ("halten_soll_zu_hoch", ABBRAND, dict(i_anteil=90.0, klappe_soll=41),
     dict(t=150.0, twt_roh=80.1), 100),
    ("halten_t_knapp", ABBRAND, dict(i_anteil=90.0, klappe_soll=40),
     dict(t=149.9, twt_roh=80.1), 100),
    ("halten_wasser_knapp", ABBRAND, dict(i_anteil=90.0, klappe_soll=40),
     dict(t=150.0, twt_roh=80.0, tmax3=300.0), 100),
    ("ausbrand", AUSBRAND, dict(klappe_soll=80), dict(t=170.0, twt_roh=86.0), 40),
    ("nicht_in_glut", GLUT, {}, dict(t=170.0, twt_roh=86.0), 10),
    ("nicht_im_handbetrieb", ABBRAND, dict(i_anteil=90.0, klappe_soll=90),
     dict(t=170.0, twt_roh=86.0, handbetrieb=True), 100),
    ("fuehler_offen_wird_null", ABBRAND, dict(i_anteil=90.0, klappe_soll=90),
     dict(t=170.0, twt_roh=1638.0, tmax3=300.0), 100),
])
def test_wasserbegrenzung(fall, phase, z, e, klappe):
    z.setdefault("t_maximum", 300.0)
    s = 1000 if phase == ABBRAND else 0
    assert klappe_nach(phase, s, z, ausloeser="tuer", **e) == klappe


def test_wasserbegrenzung_nach_uebertemperatur_nimmt_das_kleinere():
    p = Parameter(klappe_uebertemperatur=30)
    z = dict(i_anteil=90.0, klappe_soll=90, t_maximum=500.0)
    assert klappe_nach(ABBRAND, 1000, z, p, t=401.0, twt_roh=90.0, ausloeser="tuer") == 30


@pytest.mark.parametrize("k,klappe", [(-5, 0), (150, 100), (33.4, 33), (33.6, 34)])
def test_begrenzung_und_rundung(k, klappe):
    assert klappe_nach(GLUT, 0, None, Parameter(klappe_glut=k), t=100.0) == klappe


def test_schreiben_nur_bei_anderem_int_wert():
    st, erg = lauf(GLUT, 0, dict(klappe_soll=10.7), t=100.0)
    assert st.zustand.klappe_soll == 10.7 and erg.klappe == 10
    st, erg = lauf(GLUT, 0, dict(klappe_soll=11.2), t=100.0)
    assert st.zustand.klappe_soll == 10 and erg.klappe == 10
