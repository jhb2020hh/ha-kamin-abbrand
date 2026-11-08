"""Abgeleitete Größen, wenn in HA kein passender Sensor zugewiesen ist.

Entspricht den Helfern der Live-Anlage: Derivative über 2 min, Maximum über 3 min,
Änderung über 10 min. Der Gradient wichtet wie der Derivative-Helfer nach Zeit.
"""
from __future__ import annotations

from collections import deque
from typing import Optional

GRADIENT_FENSTER_S = 120.0
MAX_FENSTER_S = 180.0
ANSTIEG_FENSTER_S = 600.0


class Ableitung:
    def __init__(self) -> None:
        self._abgas: deque[tuple[float, float]] = deque()
        self._twt: deque[tuple[float, float]] = deque()

    @staticmethod
    def _anhaengen(reihe: deque, zeit: float, wert: float, fenster_s: float) -> None:
        if reihe and zeit <= reihe[-1][0]:
            return
        reihe.append((zeit, wert))
        while reihe and reihe[0][0] < zeit - fenster_s:
            reihe.popleft()

    def abgas(self, zeit: float, wert: float) -> None:
        self._anhaengen(self._abgas, zeit, wert, MAX_FENSTER_S)

    def twt(self, zeit: float, wert: float) -> None:
        self._anhaengen(self._twt, zeit, wert, ANSTIEG_FENSTER_S)

    def gradient(self, jetzt: float) -> float:
        """K/min, auf eine Stelle gerundet. Wie der Helfer: gewichtet über das Fenster,
        das an der letzten Meldung endet. Bleiben Meldungen aus, bleibt auch der Wert."""
        if len(self._abgas) < 2:
            return 0.0
        beginn = self._abgas[-1][0] - GRADIENT_FENSTER_S
        summe = 0.0
        punkte = list(self._abgas)
        for (t0, w0), (t1, w1) in zip(punkte, punkte[1:]):
            if t1 > beginn:
                summe += (w1 - w0) / (t1 - t0) * (t1 - max(t0, beginn)) / GRADIENT_FENSTER_S
        return round(summe * 60.0, 1)

    def t_max_3min(self, jetzt: float) -> Optional[float]:
        werte = [w for z, w in self._abgas if z >= jetzt - MAX_FENSTER_S]
        return max(werte) if werte else None

    def twt_anstieg(self, jetzt: float) -> float:
        werte = [w for z, w in self._twt if z >= jetzt - ANSTIEG_FENSTER_S]
        return round(werte[-1] - werte[0], 1) if len(werte) >= 2 else 0.0
