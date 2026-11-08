"""I-Integration, Anti-Windup, dynamisches Minimum, I-Start-Rampe, Rundung."""
import pytest

from kern.regler import i_start
from kern.signale import runde
from kern.typen import ABBRAND, ANHEIZEN, BEDIENUNG, GLUT, TICK, Parameter
from kern.steuerung import Steuerung
from kern_hilfen import eingaben, klappe_nach, lauf, zustand

# Vorgaben: Kp 0.5, Tn 7 min → Schritt je Tick = e * 0.5 * 30 / 420


def i_nach(i, t, s=1000, z=None, p=None, **e):
    felder = dict(i_anteil=i, t_maximum=300.0, klappe_soll=50)
    felder.update(z or {})
    return lauf(ABBRAND, s, felder, p, t=t, **e)[0].zustand.i_anteil


@pytest.mark.parametrize("fall,i,t,s,erwartet", [
    ("integriert_auf", 50.0, 200.0, 1000, 50.7),      # 50 + 0.714
    ("integriert_ab", 50.0, 240.0, 1000, 49.3),
    ("schritt_verschwindet_in_rundung", 50.0, 219.0, 1000, 50.0),
    ("ziel_erreicht", 50.0, 220.0, 1000, 50.0),
    ("windup_oben", 95.0, 200.0, 1000, 95.0),         # u = 105 > 100, e > 0
    ("windup_oben_grenze", 90.0, 200.0, 1000, 90.7),  # u = 100, nicht > 100
    ("windup_unten", 40.0, 240.0, 1000, 40.0),        # u = 30 < 35, e < 0
    ("windup_unten_grenze", 45.0, 240.0, 1000, 44.3),  # u = 35, nicht < 35
    ("windup_unten_entgasung", 50.0, 240.0, 599, 50.0),  # u = 40 < 45
    ("minimum_entgasung_hebt_an", 40.0, 219.0, 599, 45.0),
    ("minimum_nach_entgasung", 40.0, 219.0, 600, 40.0),
    ("windup_rundet_auch", 95.04, 200.0, 1000, 95.0),
])
def test_integration(fall, i, t, s, erwartet):
    assert i_nach(i, t, s) == erwartet


def test_windup_unten_mit_vorhaltung():
    p = Parameter(regler_vorhaltung=2)
    assert i_nach(46.0, 240.0, p=p, g=2.0) == 46.0      # u = 32 < 35
    assert i_nach(46.0, 240.0, p=p, g=0.0) == 45.3


def test_maximum_begrenzt_integration():
    p = Parameter(klappe_max_abbrand=80)
    assert i_nach(79.9, 219.0, p=p, z=dict(i_anteil=79.9)) == 79.9
    assert i_nach(80.5, 220.5, p=p) == 80.0


def test_tn_mindestens_eine_minute():
    p = Parameter(regler_nachstellzeit=0)
    assert i_nach(50.0, 210.0, p=p) == 52.5


@pytest.mark.parametrize("fall,e", [
    ("kein_tick", dict(t=200.0, ausloeser=BEDIENUNG)),
    ("handbetrieb", dict(t=200.0, handbetrieb=True)),
    ("wasser_warm", dict(t=200.0, twt_roh=80.1)),
    ("uebertemperatur", dict(t=400.1)),
])
def test_integration_ruht(fall, e):
    assert lauf(ABBRAND, 1000, dict(i_anteil=50.0, t_maximum=500.0), **e)[0].zustand.i_anteil == 50.0


@pytest.mark.parametrize("e", [dict(t=200.0, twt_roh=80.0), dict(t=200.0, twt_roh=1638.0)],
                         ids=["wasser_grenze", "fuehler_offen"])
def test_integration_laeuft_an_der_grenze(e):
    assert lauf(ABBRAND, 1000, dict(i_anteil=50.0, t_maximum=300.0), **e)[0].zustand.i_anteil == 50.7


def test_integration_nur_in_abbrand():
    assert lauf(ANHEIZEN, 10, dict(i_anteil=50.0, t_maximum=120.0), t=100.0)[0].zustand.i_anteil == 50.0


def test_faktor_30s_auch_bei_anderem_takt_s():
    st = Steuerung(Parameter(), zustand(ABBRAND, 1000, i_anteil=50.0, t_maximum=300.0), takt_s=5)
    st.schritt(eingaben(t=200.0, ausloeser=TICK))
    assert st.zustand.i_anteil == 50.7


@pytest.mark.parametrize("t_vorher,erwartet", [
    (20.0, 70.0), (50.0, 70.0), (65.0, 55.0), (72.5, 47.5), (80.0, 40.0), (95.0, 40.0),
])
def test_i_start_rampe(t_vorher, erwartet):
    assert i_start(t_vorher, Parameter()) == erwartet


def test_i_start_grenzen():
    assert i_start(20.0, Parameter(klappe_max_abbrand=60)) == 60.0
    assert i_start(95.0, Parameter(klappe_min_abbrand=45)) == 45.0


def test_i_start_rundet_auf_eine_stelle():
    assert i_start(60.0, Parameter()) == 60.0
    assert i_start(61.0, Parameter()) == 59.0
    assert i_start(50.1, Parameter()) == 69.9


def test_i_start_ohne_abstand_zwischen_kalt_und_abkuehlen():
    # ta - tk wird auf 1 gehoben, keine Division durch null
    assert i_start(80.5, Parameter(t_kalt=80, t_abkuehlen=80)) == 55.0


@pytest.mark.parametrize("wert,stellen,erwartet", [
    # Werte aus ha_eval_template gegen das echte HA, 06.11.2026
    (0.5, 0, 0), (1.5, 0, 2), (2.5, 0, 2), (62.5, 0, 62), (-2.5, 0, -2), (230.4, 0, 230),
    (0.25, 1, 0.2), (0.35, 1, 0.3), (44.45, 1, 44.5), (0, 1, 0.0),
])
def test_rundung_wie_ha(wert, stellen, erwartet):
    assert runde(wert, stellen) == erwartet


def test_klappe_rundet_halbe_auf_gerade():
    assert klappe_nach(GLUT, 0, None, Parameter(klappe_glut=62.5), t=100.0) == 62
    assert klappe_nach(GLUT, 0, None, Parameter(klappe_glut=63.5), t=100.0) == 64


def test_rundung_typen_wie_ha():
    assert str(runde(230.4)) == "230"
    assert str(runde(0, 1)) == "0.0"
    assert str(runde(62.0, 1)) == "62.0"
