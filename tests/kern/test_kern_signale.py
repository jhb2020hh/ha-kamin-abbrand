"""Plausibilität und abgeleitete Größen."""
import math

import pytest

from kern.signale import anh_min_eff, t_gueltig, twt_plausibel, werte_ermitteln, ziel_min_s
from kern.typen import Parameter
from kern_hilfen import JETZT, JETZT_INT, eingaben, zustand


@pytest.mark.parametrize("roh,erwartet", [
    (None, 0), (-0.1, 0), (0, 0), (61.5, 61.5), (120, 120), (120.1, 0), (1638, 0),
    (float("nan"), 0), (float("inf"), 0),
])
def test_twt_plausibilitaet(roh, erwartet):
    assert twt_plausibel(roh) == erwartet


@pytest.mark.parametrize("t,ok", [(None, False), (0.0, True), (float("nan"), False),
                                  (math.inf, False)])
def test_t_gueltig(t, ok):
    assert t_gueltig(t) is ok


@pytest.mark.parametrize("tref,erwartet", [(129.9, 120), (130, 120), (79.9, 900), (80, 120)])
def test_anh_min_eff(tref, erwartet):
    assert anh_min_eff(tref, 130, 80, Parameter()) == erwartet


def test_anh_min_eff_unterscheidet_die_drei_dauern():
    p = Parameter(anheizen_mindestdauer=15, anheizen_mindestdauer_nachlegen=4,
                  anheizen_mindestdauer_feuer=3)
    assert [anh_min_eff(tref, 130, 80, p) for tref in (130, 129.9, 80, 79.9)] == \
        [180, 240, 240, 900]


@pytest.mark.parametrize("tref,erwartet", [(160, 120), (159.9, 180), (80, 180), (79.9, 300)])
def test_ziel_min_s(tref, erwartet):
    assert ziel_min_s(tref, 160, 80) == erwartet


def werte(z=None, **e):
    return werte_ermitteln(eingaben(**e), Parameter(), z or zustand("Glut"))


def test_t_none_ist_null():
    w = werte(t=None)
    assert w.t_ok is False and w.t == 0 and w.tmax3 == 0


def test_tmax3_none_ist_t():
    assert werte(t=123.0).tmax3 == 123.0
    assert werte(t=123.0, tmax3=130.0).tmax3 == 130.0


def test_s_und_t_alter_sind_abgeschnittene_ints():
    z = zustand("Glut")
    z.phase_seit = JETZT_INT - 10
    w = werte(z, t_alter_s=59.9)
    assert w.s == 10 and isinstance(w.s, int)
    assert w.t_alter == 59 and isinstance(w.t_alter, int)
    z.phase_seit = JETZT + 0.4     # phase_seit knapp in der Zukunft: int() schneidet auf 0
    assert werte(z).s == 0


def test_mindestwerte():
    p = Parameter(regler_nachstellzeit=0.5, klappe_schliessrate=0.2)
    w = werte_ermitteln(eingaben(), p, zustand("Glut"))
    assert w.tn == 1 and w.krate == 1
