"""Gemeinsame Bausteine der Kern-Tests."""
from __future__ import annotations

from kern.steuerung import Steuerung
from kern.typen import Eingaben, Ergebnis, Parameter, Zustand

JETZT = 1_800_000_000.5     # bewusst mit Nachkommastelle: phase_seit ist int(jetzt)
JETZT_INT = float(int(JETZT))


def zustand(phase, s=0, **felder) -> Zustand:
    return Zustand(phase=phase, phase_seit=JETZT_INT - s, **felder)


def eingaben(**felder) -> Eingaben:
    felder.setdefault("jetzt", JETZT)
    felder.setdefault("t", 100.0)
    return Eingaben(**felder)


def lauf(phase, s=0, z=None, p=None, **e) -> tuple[Steuerung, Ergebnis]:
    """Ein Lauf ab Phase/Dauer; z = Zustandsfelder, e = Eingabefelder."""
    st = Steuerung(p or Parameter(), zustand(phase, s, **(z or {})))
    return st, st.schritt(eingaben(**e))


def klappe_nach(phase, s=0, z=None, p=None, **e) -> int:
    """Klappe ohne Einfluss der Phasenregeln: Es gilt der Wert des Laufs."""
    return lauf(phase, s, z, p, **e)[1].klappe
