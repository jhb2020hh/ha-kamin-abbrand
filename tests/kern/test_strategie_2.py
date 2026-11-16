"""Regelstrategie 2.0: PI ohne D-Anteil mit Schwingungswächter (kern/schwingung.py)."""
from __future__ import annotations

import math

import pytest

from kern import schwingung
from kern.typen import Parameter, Zustand

def _sinus(z: Zustand, amplitude: float, periode_s: float, dauer_s: float, trend_k_min: float = 0.0,
           t0: float = 0.0, start: float = 1000.0) -> list[float]:
    """Füttert den Wächter alle 5 s und gibt die Dämpfung je Takt zurück."""
    verlauf = []
    for s in range(0, int(dauer_s), 5):
        t = 255 + amplitude * math.sin(2 * math.pi * s / periode_s) + trend_k_min * s / 60
        schwingung.beobachten(z, t, start + t0 + s)
        verlauf.append(z.daempfung)
    return verlauf


def test_dauerschwingung_wird_gedaempft():
    z = Zustand()
    _sinus(z, 10, 180, 900)
    assert z.daempfung == pytest.approx(0.7 ** 2, abs=0.01) or z.daempfung < 0.75
    assert z.sw_gedaempft is not None


def test_schwingung_auf_fallendem_trend_wird_erkannt():
    """Wie am 16.11. 18:43: ±10 K um einen Abfall von 3 K/min. Ein gleitendes Mittel verpasst das."""
    z = Zustand()
    _sinus(z, 10, 180, 600, trend_k_min=-3)
    assert z.daempfung < 1.0


def test_kleines_flackern_daempft_nicht():
    z = Zustand()
    _sinus(z, 3, 120, 3600)
    assert z.daempfung == 1.0


def test_langsame_drift_daempft_nicht():
    z = Zustand()
    _sinus(z, 20, 1800, 3600)       # 30-min-Welle des Abbrands ist keine Regelschwingung
    assert z.daempfung == 1.0


def test_untergrenze_und_erholung():
    z = Zustand()
    _sinus(z, 12, 180, 3600)
    assert z.daempfung == pytest.approx(schwingung.DAEMPFUNG_MIN)
    _sinus(z, 0, 180, 3 * schwingung.ERHOLUNG_S + 10, t0=4000)
    assert z.daempfung == pytest.approx(0.3 * 1.1 ** 3, abs=0.01)


def test_zuruecksetzen_behaelt_daempfung():
    z = Zustand(daempfung=0.5)
    _sinus(z, 10, 180, 200)
    schwingung.zuruecksetzen(z)
    assert z.daempfung == 0.5 and z.sw_wellen == () and z.sw_extrem is None


def test_werte_aus_json_liste():
    """Aus betrieb_zustand.json kommt sw_wellen als Liste zurück."""
    z = Zustand(sw_wellen=[100.0, 90.0])
    _sinus(z, 10, 180, 400)
    assert isinstance(z.sw_wellen, tuple)


def test_strategie_2_ohne_d_anteil():
    from kern.signale import werte_ermitteln
    from kern.typen import Eingaben
    p = Parameter(regelstrategie=2, regler_vorhaltung=5)
    z = Zustand(daempfung=0.49)
    w = schwingung.werte_2(werte_ermitteln(Eingaben(jetzt=0, t=250), p, z), p, z)
    assert w.kd == 0
    assert w.kp == pytest.approx(0.3 * 0.49)
    assert w.tn == pytest.approx(2 / 0.7)
