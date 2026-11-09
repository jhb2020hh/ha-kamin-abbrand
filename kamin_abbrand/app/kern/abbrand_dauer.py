"""Dauer und Anzahl der Abbrände, nach den Regeln der Automation „Dauer erfassen“.

Ein Abbrand beginnt beim Öffnen der Tür oder beim Anheizen, zählt aber erst, wenn die Phase
Abbrand erreicht ist. Fehlstarts zählen also nicht. Er endet mit Glut, Abkühlen, Standby oder
Anheizfehler oder beim nächsten bestätigten Nachlegen, dann mit dem Öffnen der Tür. Ein Abbrand
über Mitternacht zählt zum Tag seines Endes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any, Mapping, Optional

from kern.typen import ABBRAND, ABKUEHLEN, ANHEIZEN, ANHEIZFEHLER, GLUT, STANDBY, TUER_OFFEN

_START = (TUER_OFFEN, ANHEIZEN)
_ENDE = (GLUT, ABKUEHLEN, STANDBY, ANHEIZFEHLER)
_KANDIDAT_GUELTIG_S = 5400.0
_DAUER_MAX_MIN = 1000.0


@dataclass
class DauerStand:
    phase: Optional[str] = None
    kandidat: float = 0.0
    start: float = 0.0
    laeuft: bool = False
    letzte_min: Optional[float] = None
    summe_gesamt_min: float = 0.0
    anzahl_gesamt: int = 0
    summe_heute_min: float = 0.0
    anzahl_heute: int = 0
    tag: Optional[str] = None
    vorbelegt: bool = False

    def als_dict(self) -> dict:
        return asdict(self)


def aus_daten(roh: Any) -> DauerStand:
    """Liest den gespeicherten Stand. Eine verdorbene Angabe zählt wie ein neuer Anfang."""
    if not isinstance(roh, Mapping):
        return DauerStand()
    try:
        return DauerStand(**{f.name: roh[f.name] for f in fields(DauerStand) if f.name in roh})
    except TypeError:
        return DauerStand()


def _buchen(s: DauerStand, dauer_min: float) -> None:
    dauer_min = round(dauer_min, 1)
    if not 0 < dauer_min < _DAUER_MAX_MIN:
        return
    s.letzte_min = dauer_min
    s.summe_gesamt_min = round(s.summe_gesamt_min + dauer_min, 1)
    s.summe_heute_min = round(s.summe_heute_min + dauer_min, 1)
    s.anzahl_gesamt += 1
    s.anzahl_heute += 1


def _bestaetigen(s: DauerStand, wann: float) -> None:
    gueltig = s.kandidat > s.start and wann - s.kandidat < _KANDIDAT_GUELTIG_S
    neu_start = s.kandidat if gueltig else wann
    if s.laeuft:
        _buchen(s, (neu_start - s.start) / 60.0)
    s.start = neu_start
    s.laeuft = True


def fortschreiben(s: DauerStand, phase: str, phase_seit: float, jetzt: float, tag: str) -> bool:
    """Übernimmt die aktuelle Phase. True, wenn sich etwas Speicherwürdiges geändert hat."""
    geaendert = False
    if s.tag != tag:
        if s.tag is not None:
            s.summe_heute_min, s.anzahl_heute = 0.0, 0
        s.tag = tag
        geaendert = True
    alt, s.phase = s.phase, phase
    if phase == alt:
        return geaendert
    if alt is None:
        return True
    wann = min(phase_seit, jetzt)
    if phase in _START:
        if alt not in _START:
            s.kandidat = wann
    elif phase == ABBRAND:
        if alt in _START:
            _bestaetigen(s, wann)
    elif phase in _ENDE and s.laeuft:
        s.laeuft = False
        _buchen(s, (wann - s.start) / 60.0)
    return True


def anzeige(s: DauerStand, jetzt: float) -> dict:
    """Werte in Minuten. Ein Schnitt ohne Abbrand ist None, nicht 0."""
    return {
        "aktuell_min": round(max(jetzt - s.start, 0.0) / 60.0, 1) if s.laeuft else None,
        "letzte_min": s.letzte_min,
        "schnitt_heute_min": round(s.summe_heute_min / s.anzahl_heute, 1) if s.anzahl_heute else None,
        "anzahl_heute": s.anzahl_heute,
        "schnitt_gesamt_min": round(s.summe_gesamt_min / s.anzahl_gesamt, 1) if s.anzahl_gesamt else None,
        "anzahl_gesamt": s.anzahl_gesamt,
    }
