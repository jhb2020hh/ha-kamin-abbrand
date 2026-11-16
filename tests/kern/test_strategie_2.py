"""Regelstrategie 2.0: PI mit Schwingungswächter und gerichtetem Vorhalt (kern/schwingung.py)."""
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


def _werte(t: float, g: float = 0.0, g_kurz=None, daempfung: float = 1.0, **p):
    from kern.signale import werte_ermitteln
    from kern.typen import Eingaben
    par = Parameter(regelstrategie=2, t_ziel=255, regler_vorhaltung=5, **p)
    z = Zustand(daempfung=daempfung)
    return schwingung.werte_2(werte_ermitteln(Eingaben(jetzt=0, t=t, g=g, g_kurz=g_kurz), par, z), par, z)


def test_strategie_2_eigene_werte_klassischer_kd_zaehlt_nicht():
    w = _werte(250, daempfung=0.49)
    assert w.kd == 0            # Temperatur steht: kein Vorhalt, Kd 5 der klassischen Strategie gilt nicht
    assert w.kp == pytest.approx(0.3 * 0.49)
    assert w.tn == pytest.approx(2 / 0.7)


@pytest.mark.parametrize("t,g,kd", [
    (240, -6, 0.5),     # unter dem Ziel fallend: Klappe auf, voller Vorhalt beim Fallen
    (270, 6, 0.25),     # über dem Ziel steigend: Klappe zu, Vorhalt beim Steigen
    (240, 6, 0.0),      # unter dem Ziel steigend: läuft aufs Ziel zu, kein Bremsen
    (270, -6, 0.0),     # über dem Ziel fallend: ebenso
    (255, -6, 0.0),     # genau am Ziel
])
def test_vorhalt_nur_weg_vom_ziel(t, g, kd):
    w = _werte(t, g_kurz=g)
    assert w.kd == pytest.approx(kd)
    assert w.g == g


def test_vorhalt_oeffnet_beim_fallen():
    from kern.klappe import _abbrand
    w = _werte(240, g_kurz=-8)
    z = Zustand(i_anteil=50, klappe_soll=50)
    ohne = _werte(240, g_kurz=-8, regler2_vorhaltung_fallend=0)
    assert _abbrand(z, w, 1000, 5, 50) == pytest.approx(_abbrand(z, ohne, 1000, 5, 50) + 0.5 * 8)


def test_vorhalt_kurze_steigung_vor_gradient():
    assert _werte(240, g=-1, g_kurz=-8).g == -8
    assert _werte(240, g=-3).g == -3            # ohne kurze Steigung (alte Verläufe): 2-min-Gradient


def test_vorhalt_mit_daempfung_im_quadrat():
    w = _werte(240, g_kurz=-6, daempfung=0.5)
    assert w.kd == pytest.approx(0.5 * 0.25)
