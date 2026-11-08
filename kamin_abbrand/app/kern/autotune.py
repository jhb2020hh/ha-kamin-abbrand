"""Vorschläge für Kp und Tn: aus dem Verlauf oder aus einem Relaistest.

Beide liefern nur Startwerte, denn Holzabbrand ändert sich laufend. Die Auslegung
folgt CHR aperiodisch (Störverhalten), wie am 01.11. von Hand: Kp = 0,6 / (Ks/Tg · Tu),
Tn = 4 · Tu. Nur Standardbibliothek, keine Uhr, kein I/O.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Optional, Sequence

from kern.typen import ABBRAND, Parameter

MIN_SPRUNG = 8.0            # %
SPRUNG_DAUER_S = 60.0
VORLAUF_S = 120.0
NACHLAUF_S = 360.0
SCHWELLE_K = 1.5            # ab hier gilt die Antwort als begonnen
TU_MIN, TU_MAX = 0.25, 5.0  # min
MIN_SPRUENGE = 3


@dataclass(frozen=True)
class Probe:
    zeit: float
    klappe: float
    t: float
    phase: str = ABBRAND
    tuer_offen: bool = False


@dataclass(frozen=True)
class Sprung:
    zeit: float
    hoehe: float
    tu_min: float
    ks_tg: float            # K/(%·min)


@dataclass
class Vorschlag:
    ok: bool
    grund: str
    kp: Optional[float] = None
    tn: Optional[float] = None
    tu_min: Optional[float] = None
    ks_tg: Optional[float] = None
    sprünge: int = 0
    streuung_tu: Optional[float] = None
    streuung_ks: Optional[float] = None
    quelle: str = "verlauf"
    einzelne: list[Sprung] = field(default_factory=list)


def chr_aperiodisch(ks_tg: float, tu_min: float) -> tuple[float, float]:
    return round(0.6 / (ks_tg * tu_min), 2), round(4.0 * tu_min, 1)


def _steigung(punkte: Sequence[tuple[float, float]]) -> tuple[float, float]:
    """Ausgleichsgerade: Wert am ersten Punkt und Steigung je Sekunde."""
    t0 = punkte[0][0]
    n = len(punkte)
    sx = sum(t - t0 for t, _ in punkte)
    sy = sum(w for _, w in punkte)
    sxx = sum((t - t0) ** 2 for t, _ in punkte)
    sxy = sum((t - t0) * w for t, w in punkte)
    nenner = n * sxx - sx * sx
    if nenner == 0:
        return sy / n, 0.0
    b = (n * sxy - sx * sy) / nenner
    return (sy - b * sx) / n, b


def _sprung_auswerten(proben: Sequence[Probe], i: int, j: int) -> Optional[Sprung]:
    start = proben[i]
    hoehe = proben[j].klappe - start.klappe
    vor = [(p.zeit, p.t) for p in proben[:i + 1] if p.zeit >= start.zeit - VORLAUF_S]
    if len(vor) < 3 or vor[-1][0] - vor[0][0] < VORLAUF_S * 0.7:
        return None
    a, b = _steigung(vor)
    nach = [p for p in proben[j:] if p.zeit <= start.zeit + NACHLAUF_S]
    if len(nach) < 6:
        return None
    vorzeichen = 1.0 if hoehe > 0 else -1.0
    abweichung = [(p.zeit, vorzeichen * (p.t - (a + b * (p.zeit - vor[0][0])))) for p in nach]
    beginn = next((z for z, d in abweichung if d >= SCHWELLE_K), None)
    if beginn is None:
        return None
    # Der Regler darf den Sprung nicht zurücknehmen, bevor die Antwort sichtbar ist.
    if any(vorzeichen * (p.klappe - proben[j].klappe) < -abs(hoehe) / 2 for p in nach if p.zeit <= beginn):
        return None
    # Wendetangente: steilstes Stück der Antwort, zurückverlängert bis zur Abweichung null.
    steil, nulldurchgang = 0.0, beginn
    fenster = [a for a in abweichung if a[0] >= beginn - 60]
    for k in range(len(fenster)):
        stueck = [p for p in fenster[k:] if p[0] <= fenster[k][0] + 120]
        if len(stueck) < 3 or stueck[-1][0] - stueck[0][0] < 60:
            continue
        wert, neigung = _steigung(stueck)
        if neigung > steil:
            steil, nulldurchgang = neigung, stueck[0][0] - wert / neigung
    tu = (nulldurchgang - start.zeit) / 60.0
    if steil <= 0 or not TU_MIN <= tu <= TU_MAX:
        return None
    return Sprung(start.zeit, hoehe, tu, steil * 60.0 / abs(hoehe))


def aus_verlauf(proben: Sequence[Probe], maximale_luecke_s: float = 90.0) -> Vorschlag:
    """Wertet die Klappenbewegungen der echten Klappe aus, nur Abbrand und Tür zu."""
    gefiltert = sorted((p for p in proben if p.phase == ABBRAND and not p.tuer_offen), key=lambda p: p.zeit)
    abschnitte: list[list[Probe]] = []
    for probe in gefiltert:
        if abschnitte and probe.zeit - abschnitte[-1][-1].zeit <= maximale_luecke_s:
            abschnitte[-1].append(probe)
        else:
            abschnitte.append([probe])
    sprünge: list[Sprung] = []
    for abschnitt in abschnitte:
        sperre = -math.inf
        for i, probe in enumerate(abschnitt):
            if probe.zeit < sperre:
                continue
            for j in range(i + 1, len(abschnitt)):
                if abschnitt[j].zeit - probe.zeit > SPRUNG_DAUER_S:
                    break
                if abs(abschnitt[j].klappe - probe.klappe) >= MIN_SPRUNG:
                    sprung = _sprung_auswerten(abschnitt, i, j)
                    if sprung:
                        sprünge.append(sprung)
                    sperre = probe.zeit + NACHLAUF_S
                    break
    if len(sprünge) < MIN_SPRUENGE:
        return Vorschlag(False, f"Nicht genug Daten: {len(sprünge)} auswertbare Klappensprünge, "
                                f"gebraucht werden mindestens {MIN_SPRUENGE}.",
                         sprünge=len(sprünge), einzelne=sprünge)
    tu = statistics.median(s.tu_min for s in sprünge)
    ks_tg = statistics.median(s.ks_tg for s in sprünge)
    kp, tn = chr_aperiodisch(ks_tg, tu)
    return Vorschlag(
        True, f"{len(sprünge)} Klappensprünge ausgewertet.", kp=kp, tn=tn,
        tu_min=round(tu, 2), ks_tg=round(ks_tg, 3), sprünge=len(sprünge),
        streuung_tu=round(statistics.pstdev(s.tu_min for s in sprünge), 2),
        streuung_ks=round(statistics.pstdev(s.ks_tg for s in sprünge), 3),
        einzelne=sprünge,
    )


# ---- Aktiver Relaistest --------------------------------------------------

ZYKLEN = 3
MAX_DAUER_S = 25 * 60.0
HYSTERESE_K = 2.0
NAEHE_ZIEL_K = 15.0
HUB = 15.0
LAEUFT, FERTIG, ABBRUCH = "laeuft", "fertig", "abbruch"


@dataclass(frozen=True)
class Lage:
    jetzt: float
    t: Optional[float]
    phase: str
    tuer_offen: Optional[bool]
    signal_frisch: bool
    twt: Optional[float] = None
    phase_seit: float = 0.0


@dataclass
class Schritt:
    status: str
    klappe: Optional[float] = None
    grund: str = ""
    vorschlag: Optional[Vorschlag] = None


def sperrgrund(lage: Lage, p: Parameter) -> Optional[str]:
    """Warum der Test gerade nicht starten darf, oder None."""
    if lage.phase != ABBRAND:
        return "Der Test geht nur im Abbrand."
    if lage.jetzt - lage.phase_seit < p.entgasung_dauer * 60.0:
        return "Die Entgasung läuft noch."
    if lage.t is None or abs(lage.t - p.t_ziel) > NAEHE_ZIEL_K:
        return f"Die Temperatur muss nahe am Ziel liegen (±{NAEHE_ZIEL_K:.0f} K)."
    if lage.tuer_offen is not False:
        return "Die Tür muss geschlossen sein."
    if not lage.signal_frisch:
        return "Der Fühler meldet keine frischen Werte."
    return None


class Relaistest:
    """Klappe pendelt um die Arbeitsstellung. Nie unter klappe_min_abbrand."""

    def __init__(self, p: Parameter, mitte: float, jetzt: float) -> None:
        self.p = p
        self.unten = max(mitte - HUB, p.klappe_min_abbrand)
        self.oben = min(self.unten + 2 * HUB, p.klappe_max_abbrand)
        self.start = jetzt
        self.klappe = self.oben
        self._t_hoch: list[float] = []      # Zeitpunkte, zu denen T das Ziel nach oben kreuzt
        self._extreme_max: list[float] = []
        self._extreme_min: list[float] = []
        self._min = math.inf
        self._max = -math.inf
        self._oberhalb: Optional[bool] = None

    def _abbruchgrund(self, lage: Lage) -> Optional[str]:
        p = self.p
        if lage.tuer_offen is not False:
            return "Tür geöffnet."
        if not lage.signal_frisch or lage.t is None:
            return "Der Fühler ist verstummt."
        if lage.phase != ABBRAND:
            return f"Phase ist jetzt {lage.phase}."
        if lage.t > p.t_uebertemperatur:
            return "Übertemperatur."
        if lage.twt is not None and lage.twt > p.t_wasser_max - 5:
            return "Wasserbegrenzung."
        if lage.jetzt - self.start > MAX_DAUER_S:
            return "Höchstdauer erreicht, zu wenige Schwingungen."
        return None

    def schritt(self, lage: Lage) -> Schritt:
        grund = self._abbruchgrund(lage)
        if grund:
            return Schritt(ABBRUCH, grund=grund)
        t, ziel = lage.t, self.p.t_ziel
        self._min, self._max = min(self._min, t), max(self._max, t)
        if t > ziel + HYSTERESE_K:
            if self._oberhalb is False:
                self._t_hoch.append(lage.jetzt)
                self._extreme_min.append(self._min)
                self._max = t
            self._oberhalb = True
            self.klappe = self.unten
        elif t < ziel - HYSTERESE_K:
            if self._oberhalb is True:
                self._extreme_max.append(self._max)
                self._min = t
            self._oberhalb = False
            self.klappe = self.oben
        if len(self._t_hoch) > ZYKLEN and self._extreme_max and self._extreme_min:
            return Schritt(FERTIG, self.klappe, "Test abgeschlossen.", self._auswerten())
        return Schritt(LAEUFT, self.klappe)

    def _auswerten(self) -> Vorschlag:
        perioden = [b - a for a, b in zip(self._t_hoch, self._t_hoch[1:])]
        pu_min = statistics.mean(perioden) / 60.0
        amplitude = (statistics.mean(self._extreme_max[-ZYKLEN:]) - statistics.mean(self._extreme_min[-ZYKLEN:])) / 2
        d = (self.oben - self.unten) / 2
        ku = 4 * d / (math.pi * amplitude)
        # Für eine integrierende Strecke mit Totzeit gilt Ku = π/(2·(Ks/Tg)·Tu) und Pu = 4·Tu.
        # Eingesetzt in CHR aperiodisch ergibt das diese beiden Faktoren.
        return Vorschlag(True, f"{len(perioden)} Schwingungen ausgewertet.",
                         kp=round(0.38 * ku, 2), tn=round(pu_min, 1), tu_min=round(pu_min / 4, 2),
                         streuung_tu=round(statistics.pstdev(perioden) / 240.0, 2),
                         sprünge=len(perioden), quelle="relaistest")
