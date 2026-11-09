"""Erinnerung an die Reinigung des Wärmetauschers. Reine Rechnung, ohne Home Assistant.

Gezählt werden Brennstunden (Anheizen, Abbrand, Ausbrand), die der Aufrufer als
Gesamtzähler in Sekunden liefert. Gemerkt wird, bei welchem Zählerstand zuletzt gereinigt wurde.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

VORGABE_INTERVALL_H = 100.0
ANTEIL_BALD = 0.10              # Unter diesem Rest wird die Reinigung schon angekündigt
ERINNERUNG_ABSTAND_S = 24 * 3600.0


@dataclass(frozen=True)
class Stand:
    """Was die App sich merkt. Zeiten sind Unix-Sekunden, `None` heißt noch nie."""
    basis_s: float                          # Brennzähler (Sekunden) bei der letzten Reinigung bzw. Installation
    letzte_reinigung: Optional[float] = None
    erinnert: Optional[float] = None        # Wann zuletzt erinnert wurde


@dataclass(frozen=True)
class Status:
    aktiv: bool             # False bei Intervall 0 (aus)
    intervall_h: float
    gefahren_h: float
    rest_h: float           # Nie negativ; 0 heißt fällig
    faellig: bool
    bald: bool              # Fällig oder weniger als 10 % Rest


def pruefen(betrieb_s: float, stand: Stand, intervall_h: float) -> Status:
    gefahren_h = max(0.0, (betrieb_s - stand.basis_s) / 3600.0)
    if intervall_h is None or intervall_h <= 0:
        return Status(False, 0.0, gefahren_h, 0.0, False, False)
    rest_h = max(0.0, intervall_h - gefahren_h)
    faellig = gefahren_h >= intervall_h
    return Status(True, float(intervall_h), gefahren_h, rest_h, faellig,
                  faellig or rest_h < ANTEIL_BALD * intervall_h)


def gereinigt(betrieb_s: float, jetzt: float) -> Stand:
    """Nach der Bestätigung beginnt die Zählung neu."""
    return Stand(basis_s=betrieb_s, letzte_reinigung=jetzt, erinnert=None)


def zuruecksetzen(betrieb_s: float, stand: Stand) -> Stand:
    """Neu anfangen zu zählen, ohne dass gereinigt wurde (etwa beim Wechsel auf wassergeführt)."""
    return Stand(basis_s=betrieb_s, letzte_reinigung=stand.letzte_reinigung, erinnert=None)


def erinnern(status: Status, stand: Stand, jetzt: float) -> bool:
    """Beim Fälligwerden sofort, danach alle 24 h, bis die Reinigung bestätigt ist."""
    if not status.aktiv or not status.faellig:
        return False
    return stand.erinnert is None or jetzt - stand.erinnert >= ERINNERUNG_ABSTAND_S


def erinnert_am(stand: Stand, jetzt: float) -> Stand:
    return replace(stand, erinnert=jetzt)
