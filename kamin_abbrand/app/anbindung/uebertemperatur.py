"""Push bei Übertemperatur von Abgas und Wasser, mit Wiederholung und Entwarnung.

Ersetzt die alten Automationen in Home Assistant. Der Kern drosselt die Klappe, gemeldet
wird hier, in Lauschen und Betrieb gleich. Reine Klasse ohne I/O, die Uhrzeit kommt als Argument.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from kern.signale import t_gueltig, twt_plausibel

ART = "uebertemperatur"
ABGAS, WASSER = "abgas", "wasser"
VERZOEGERUNG_S = {ABGAS: 30.0, WASSER: 60.0}   # wie `for:` der alten Automationen
WIEDERHOLUNG_S = 300.0
HYSTERESE_K = 10.0
TITEL_WARNUNG = "🔥 Kamin – ÜBERTEMPERATUR"
TITEL_ENTWARNUNG = "🔥 Kamin – wieder normal"

Meldung = tuple[str, str, str]      # (art, titel, text)


@dataclass
class _Stand:
    ueber_seit: Optional[float] = None
    gewarnt: bool = False
    zuletzt: float = 0.0


def wasser_gueltig(roh: Optional[float]) -> Optional[float]:
    """T.WT nach twt_plausibel. Ungültig (Fühler offen meldet 1638 °C) ist None."""
    if roh is None or twt_plausibel(roh) != roh:
        return None
    return roh


def _warntext(quelle: str, wert: float, grenze: float, klappe: float, gedrosselt: bool) -> str:
    if quelle == ABGAS:
        folge = f"Klappe auf {klappe:.0f} % gedrosselt." if gedrosselt else "Bitte am Ofen prüfen."
        return f"Abgas {wert:.0f} °C, Grenze {grenze:.0f} °C. {folge}"
    folge = (f"Klappe max {klappe:.0f} %, nicht nachlegen, Pumpe/Puffer prüfen." if gedrosselt
             else "Nicht nachlegen, Pumpe/Puffer prüfen.")
    return f"Wasser am Ofen {wert:.1f} °C, Grenze {grenze:.0f} °C. {folge}"


def _entwarntext(quelle: str, wert: float) -> str:
    if quelle == ABGAS:
        return f"Abgas wieder normal: {wert:.0f} °C."
    return f"Wasser am Ofen wieder normal: {wert:.1f} °C."


class UebertempWaechter:
    """Je Quelle: Verzögerung lang über der Grenze, dann Warnung und alle 300 s erneut.
    Unter Grenze − 10 K kommt einmal die Entwarnung, danach ist der Wächter wieder scharf.
    Ein ungültiger Wert meldet nichts und ändert nichts an einer laufenden Warnung."""

    def __init__(self, wiederholung_s: float = WIEDERHOLUNG_S, hysterese_k: float = HYSTERESE_K) -> None:
        self._wiederholung_s = wiederholung_s
        self._hysterese_k = hysterese_k
        self._stand = {ABGAS: _Stand(), WASSER: _Stand()}

    def gewarnt(self, quelle: str) -> bool:
        return self._stand[quelle].gewarnt

    def pruefen(
        self,
        jetzt: float,
        *,
        t_abgas: Optional[float],
        twt_roh: Optional[float],
        t_uebertemperatur: float,
        t_wasser_max: float,
        klappe_uebertemperatur: float,
        klappe_wasser: float,
        gedrosselt: bool = True,
    ) -> list[Meldung]:
        abgas = t_abgas if t_gueltig(t_abgas) else None
        meldungen = self._quelle(ABGAS, jetzt, abgas, t_uebertemperatur, klappe_uebertemperatur, gedrosselt)
        meldungen += self._quelle(WASSER, jetzt, wasser_gueltig(twt_roh), t_wasser_max, klappe_wasser, gedrosselt)
        return meldungen

    def _quelle(self, quelle: str, jetzt: float, wert: Optional[float], grenze: float,
                klappe: float, gedrosselt: bool) -> list[Meldung]:
        s = self._stand[quelle]
        if wert is None:
            s.ueber_seit = None     # Die Verzögerung beginnt nach einer Lücke neu.
            return []
        if wert > grenze:
            if s.gewarnt:
                if jetzt - s.zuletzt < self._wiederholung_s:
                    return []
            else:
                if s.ueber_seit is None:
                    s.ueber_seit = jetzt
                if jetzt - s.ueber_seit < VERZOEGERUNG_S[quelle]:
                    return []
                s.gewarnt = True
            s.zuletzt = jetzt
            return [(ART, TITEL_WARNUNG, _warntext(quelle, wert, grenze, klappe, gedrosselt))]
        s.ueber_seit = None
        if s.gewarnt and wert < grenze - self._hysterese_k:
            s.gewarnt = False
            return [(ART, TITEL_ENTWARNUNG, _entwarntext(quelle, wert))]
        return []
