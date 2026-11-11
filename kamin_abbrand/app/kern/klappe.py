"""Klappen-Sollstellung: das Template `klappe` der Automation.

Phase, phase_seit, T-Phasenstart, I-Anteil und
klappe_soll stammen aus dem dann aktuellen Zustand, alles andere aus `Werte`.
"""
from __future__ import annotations

from kern.regler import klappe_minimum
from kern.signale import Werte, runde, sp_phase
from kern.typen import (ABBRAND, ABKUEHLEN, ANHEIZEN, ANHEIZFEHLER, AUSBRAND, GLUT,
                        STANDBY, STOERUNG, TUER_OFFEN, WARTUNG, Eingaben, Zustand)


def _anheizen(z: Zustand, w: Werte) -> float:
    tr = z.t_phasenstart
    if tr < w.abk:                      # Kaltstart: keine Glut vor dem Anheizen
        return w.kkalt
    if tr < w.glut or w.t < w.glut:
        return 100
    if tr >= w.tstart:
        return w.kfeuer
    return 100 + (w.kfeuer - 100) * (tr - w.glut) / max(w.tstart - w.glut, 1)


def _abbrand(z: Zustand, w: Werte, sp: float, schliessen: float, basis: float) -> float:
    km = klappe_minimum(w, sp)
    k = min(max(z.i_anteil + w.kp * (w.ziel - w.t) - w.kd * w.g, km), w.kmax)
    return max(k, basis - schliessen)


def _abkuehlen(w: Werte, sp: float) -> float:
    if sp < 60:
        return 100
    if w.t_ok and w.t >= w.abk:
        return w.kglut
    return w.kglut * max(1 - (sp - 60) / 1200, 0)


def _phasenwert(z: Zustand, w: Werte, sp: float, schliessen: float, basis: float) -> float:
    p = z.phase
    if p == STANDBY:
        return 0
    if p in (TUER_OFFEN, STOERUNG):
        return 100
    if p == ANHEIZEN:
        return _anheizen(z, w)
    if p == ABBRAND:
        return _abbrand(z, w, sp, schliessen, basis)
    if p == AUSBRAND:
        return min(max(z.klappe_soll, 60), w.kmax)
    if p == GLUT:
        return w.kglut
    if p == ABKUEHLEN:
        return _abkuehlen(w, sp)
    if p == ANHEIZFEHLER:
        return 100 * max(1 - sp / 5400, 0)
    if p == WARTUNG:
        return w.kwart
    return 100     # unbekannte Phase: Startwert des Namespace


def soll_berechnen(e: Eingaben, z: Zustand, w: Werte) -> int:
    return runde(soll_roh(e, z, w))


def soll_neu(e: Eingaben, z: Zustand, w: Werte, dt: float) -> int:
    """Regelstand neu: Schließrate nach Zeit, Soll ungerundet weitergeführt."""
    basis = z.klappe_soll if z.klappe_genau is None else z.klappe_genau
    k = soll_roh(e, z, w, schliessen=w.krate * dt / 30, basis=basis, neu=True)
    z.klappe_genau = k
    return runde(k)


def soll_roh(e: Eingaben, z: Zustand, w: Werte, schliessen: float | None = None,
             basis: float | None = None, neu: bool = False) -> float:
    p = z.phase
    k = _phasenwert(z, w, sp_phase(e.jetzt, z.phase_seit),
                    w.krate if schliessen is None else schliessen,
                    z.klappe_soll if basis is None else basis)
    if e.handbetrieb and p != WARTUNG:
        k = w.kman
    # Live deckelt auch in Störung. Friert T über der Grenze ein, bliebe die Klappe bei 25 %,
    # obwohl die Störung sie öffnen soll und der Wert nicht mehr stimmt.
    if w.t_ok and w.t > w.uebert and p != WARTUNG and not (neu and p == STOERUNG):
        k = min(k, w.kuebert)
    wasser_zu_heiss = ((w.twt > w.twmax and w.t >= w.glut + 40)
                       or (w.twt > w.twmax - 5 and z.klappe_soll <= w.kwasser
                           and w.t >= w.glut + 20))
    if wasser_zu_heiss and p in (ABBRAND, AUSBRAND) and not e.handbetrieb:
        k = min(k, w.kwasser)
    # Nie im Betrieb ganz zu: Glut oder Feuer brauchen Luft, auch wenn eine Rampe auf 0 % läuft.
    if (neu and p in (STANDBY, ABKUEHLEN, ANHEIZFEHLER) and not e.handbetrieb
            and ((w.t_ok and w.t >= w.abk) or w.twt_anstieg >= w.feuer_anstieg)):
        k = max(k, w.kglut)
    return min(max(k, 0), 100)


def soll_schreiben(z: Zustand, klappe: int) -> None:
    """Geschrieben wird nur bei anderem int-Wert, wie im letzten `if` der Automation."""
    if klappe != int(z.klappe_soll):
        z.klappe_soll = klappe
