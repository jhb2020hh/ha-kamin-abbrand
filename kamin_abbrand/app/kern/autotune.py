"""Vorschläge für den PI-Regler im Abbrand: aus dem Verlauf oder aus einem Relaistest.

Aus dem Verlauf (Auswertung vom 15.11.2026): Im geschlossenen Kreis lässt sich die Strecke passiv nicht
messen. Treibt der Abbrand den Kreis, ist das Verhältnis Abgas/Klappe genau der Kehrwert des Reglers. Messbar
ist aber, ob und mit welcher Periode der Kreis schwingt. Daraus folgt eine Schwingungsbremse:

- Schwingt die Abgastemperatur (regelmäßige Umkehrpunkte, mindestens 8 K Spitze-Spitze, anderthalb Perioden
  in zwei Abbränden oder zwei volle Perioden), wird die wirksame
  Verstärkung des Reglers an der Schwingfrequenz gesenkt: bei Dauerschwingung auf ein Drittel (Tyreus-Luyben,
  Ku ≈ wirksame Verstärkung), bei abklingender Schwingung auf 70 %. Tn wird mindestens 2,2 Perioden.
- Der D-Anteil geht immer auf 0: Der Gradient ist ein 2-min-Mittel und wirkt an der Schwingfrequenz wie
  zusätzliche Verstärkung (bei 4 min Periode und Kd 1 verdreifacht er sie).
- Schwingt nichts, gibt es keinen Vorschlag. Kp sinkt also nur, solange der Kreis schwingt, und das
  Verfahren endet bei einem ruhigen Kreis. Ausgewertet wird nur der Verlauf seit der letzten Regleränderung.

Nur Standardbibliothek, keine Uhr, kein I/O.
"""
from __future__ import annotations

import cmath
import math
import statistics
from dataclasses import dataclass
from typing import Optional, Sequence

from kern.typen import ABBRAND, Parameter

RASTER_S = 10.0
VORLAUF_ABBRAND_S = 60.0        # der Übergang vom Anheizen zählt mit: Dort schwingt der Kreis am stärksten ein
MAX_LUECKE_S = 90.0
MIN_ABBRAND_S = 30 * 60.0       # so viel Abbrand braucht die Bewertung seit der letzten Änderung
UMKEHR_K = 8.0                  # Spitze-Spitze, ab der eine Umkehr zählt
HALBPERIODE_MIN = (0.75, 4.0)   # Perioden 1,5 bis 8 min
REGELMAESSIG = 0.5              # jede Halbperiode höchstens ±50 % vom Median des Zugs
MIN_UMKEHRPUNKTE = 4            # anderthalb Perioden …
MIN_ZUEGE = 2                   # … in mindestens zwei Abbränden, oder ein Zug über zwei volle Perioden
LANGER_ZUG = 5
DAUER_UMKEHRPUNKTE = 7          # drei volle Perioden ohne Abklingen: Dauerschwingung
DAUER_ABKLINGEN = 0.8
SCHRITT_ABKLINGEND = 0.7
TL_KP, TL_TN = 3.2, 2.2         # Tyreus-Luyben
KP_GRENZEN = (0.05, 2.0)
TN_GRENZEN = (2.0, 30.0)        # min
GRADIENT_FENSTER_S = 120.0      # wie anbindung.ableitung


@dataclass(frozen=True)
class Probe:
    zeit: float
    klappe: float
    t: float
    phase: str = ABBRAND
    tuer_offen: bool = False


@dataclass(frozen=True)
class Schwingung:
    periode_min: float
    amplitude_k: float          # größte halbe Spitze-Spitze
    zuege: int                  # Schwingzüge mit mindestens zwei Perioden
    dauer: bool                 # ein Zug über drei Perioden ohne Abklingen


@dataclass
class Vorschlag:
    ok: bool
    grund: str
    kp: Optional[float] = None
    tn: Optional[float] = None
    kd: Optional[float] = None
    periode_min: Optional[float] = None     # der erkannten Schwingung
    amplitude_k: Optional[float] = None
    wirksam: Optional[float] = None         # %/K, Verstärkung der aktuellen Einstellung an der Schwingfrequenz
    abbrand_min: float = 0.0                # ausgewerteter Abbrand
    quelle: str = "verlauf"


def pi_tyreus_luyben(ku: float, pu_min: float) -> tuple[float, float]:
    kp = min(max(ku / TL_KP, KP_GRENZEN[0]), KP_GRENZEN[1])
    tn = min(max(TL_TN * pu_min, TN_GRENZEN[0]), TN_GRENZEN[1])
    return kp, tn


def regler_frequenzgang(periode_min: float, kp: float, tn_min: float, kd: float = 0.0) -> complex:
    """Übertragung −T → Klappe Soll: PI plus D auf dem 2-min-Gradienten (K/min)."""
    w = 2 * math.pi / (periode_min * 60.0)
    pi = kp * (1 + 1 / (1j * w * max(tn_min, 1e-6) * 60.0))
    gradient = 60.0 * (1 - cmath.exp(-1j * w * GRADIENT_FENSTER_S)) / GRADIENT_FENSTER_S
    return pi + kd * gradient


# ---- Schwingung im Verlauf ------------------------------------------------

def _abschnitte(proben: Sequence[Probe]) -> list[list[Probe]]:
    gefiltert = sorted((p for p in proben if p.phase == ABBRAND and not p.tuer_offen and p.t is not None),
                       key=lambda p: p.zeit)
    abschnitte: list[list[Probe]] = []
    for probe in gefiltert:
        if abschnitte and probe.zeit - abschnitte[-1][-1].zeit <= MAX_LUECKE_S:
            abschnitte[-1].append(probe)
        else:
            abschnitte.append([probe])
    return [r for a in abschnitte if len(r := [p for p in a if p.zeit >= a[0].zeit + VORLAUF_ABBRAND_S]) >= 2]


def umkehrpunkte(werte: Sequence[tuple[float, float]], schwelle: float = UMKEHR_K) -> list[tuple[float, float]]:
    """Zickzack: ein Extremum gilt, sobald der Wert um `schwelle` zurückgelaufen ist."""
    punkte: list[tuple[float, float]] = []
    if not werte:
        return punkte
    richtung = 0
    extrem = werte[0]
    for zeit, wert in werte[1:]:
        if richtung >= 0 and wert > extrem[1] or richtung <= 0 and wert < extrem[1]:
            if richtung == 0:
                richtung = 1 if wert > extrem[1] else -1
            extrem = (zeit, wert)
        elif abs(wert - extrem[1]) >= schwelle:
            punkte.append(extrem)
            richtung = -richtung if richtung else (1 if wert > extrem[1] else -1)
            extrem = (zeit, wert)
    return punkte


def _zuege(punkte: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Folgen von Umkehrpunkten mit passender und gleichmäßiger Halbperiode."""
    zuege: list[list[tuple[float, float]]] = []
    zug: list[tuple[float, float]] = []
    for punkt in punkte:
        if zug:
            halb = (punkt[0] - zug[-1][0]) / 60.0
            passt = HALBPERIODE_MIN[0] <= halb <= HALBPERIODE_MIN[1]
            if passt and len(zug) >= 2:
                median = statistics.median((b[0] - a[0]) / 60.0 for a, b in zip(zug, zug[1:]))
                passt = abs(halb - median) <= REGELMAESSIG * median
            if not passt:
                if len(zug) >= MIN_UMKEHRPUNKTE:
                    zuege.append(zug)
                zug = [zug[-1]] if HALBPERIODE_MIN[0] <= halb <= HALBPERIODE_MIN[1] else []
        zug.append(punkt)
    if len(zug) >= MIN_UMKEHRPUNKTE:
        zuege.append(zug)
    return zuege


def schwingung(proben: Sequence[Probe]) -> tuple[Optional[Schwingung], float]:
    """Die Schwingung im Abbrand (oder None) und die Minuten Abbrand, die ausgewertet wurden."""
    alle: list[list[tuple[float, float]]] = []
    minuten = 0.0
    for abschnitt in _abschnitte(proben):
        minuten += (abschnitt[-1].zeit - abschnitt[0].zeit) / 60.0
        alle += _zuege(umkehrpunkte([(p.zeit, p.t) for p in abschnitt]))
    if not alle or (len(alle) < MIN_ZUEGE and max(len(z) for z in alle) < LANGER_ZUG):
        return None, minuten
    halbe = [(b[0] - a[0]) / 60.0 for zug in alle for a, b in zip(zug, zug[1:])]
    huebe = [abs(b[1] - a[1]) / 2 for zug in alle for a, b in zip(zug, zug[1:])]
    dauer = any(len(z) >= DAUER_UMKEHRPUNKTE and abs(z[-1][1] - z[-2][1]) >= DAUER_ABKLINGEN * abs(z[1][1] - z[0][1])
                for z in alle)
    return Schwingung(2 * statistics.median(halbe), max(huebe), len(alle), dauer), minuten


def aus_verlauf(proben: Sequence[Probe], aktuell: tuple[float, float, float]) -> Vorschlag:
    """`proben` nur seit der letzten Regleränderung; `aktuell` = (Kp, Tn in min, Kd)."""
    kp, tn, kd = aktuell
    gefunden, minuten = schwingung(proben)
    if gefunden is None and minuten < MIN_ABBRAND_S / 60:
        return Vorschlag(False, f"Noch zu wenig Abbrand seit der letzten Regleränderung ({minuten:.0f} von "
                                f"{MIN_ABBRAND_S / 60:.0f} min). Bitte nach weiteren Abbränden erneut berechnen.",
                         abbrand_min=round(minuten))
    if gefunden is None:
        if kd > 0:
            return Vorschlag(True, f"Keine Schwingung in {minuten:.0f} min Abbrand. Der D-Anteil wird trotzdem "
                                   "abgeschaltet: Er wirkt über den trägen 2-min-Gradienten nur als Unruhe.",
                             kp=kp, tn=tn, kd=0.0, abbrand_min=round(minuten))
        return Vorschlag(False, f"Regelkreis ruhig, Werte passen: keine Schwingung in {minuten:.0f} min Abbrand "
                                "seit der letzten Regleränderung.", abbrand_min=round(minuten))
    periode = gefunden.periode_min
    wirksam = abs(regler_frequenzgang(periode, kp, tn, kd))
    if gefunden.dauer:
        kp_neu, tn_neu = pi_tyreus_luyben(wirksam, periode)
        art = "Dauerschwingung"
    else:
        kp_neu, tn_neu = SCHRITT_ABKLINGEND * wirksam, TL_TN * periode
        art = "abklingende Schwingung"
    kp_neu = min(max(min(kp_neu, kp), KP_GRENZEN[0]), KP_GRENZEN[1])
    tn_neu = min(max(tn_neu, tn, TN_GRENZEN[0]), TN_GRENZEN[1])
    wirksam_neu = abs(regler_frequenzgang(periode, kp_neu, tn_neu))
    grund = (f"Die Abgastemperatur schwingt mit {periode:.1f} min Periode und bis ±{gefunden.amplitude_k:.0f} K "
             f"({art}, {gefunden.zuege} Schwingzüge in {minuten:.0f} min Abbrand). Der Regler wirkt dort mit "
             f"{wirksam:.2f} %/K" + (" (davon das meiste durch den D-Anteil)" if kd > 0 and wirksam > 1.5 * kp else "")
             + f", mit dem Vorschlag ohne D-Anteil mit {wirksam_neu:.2f} %/K.")
    return Vorschlag(True, grund, kp=round(kp_neu, 2), tn=round(tn_neu, 1), kd=0.0, periode_min=round(periode, 1),
                     amplitude_k=round(gefunden.amplitude_k, 1), wirksam=round(wirksam, 2), abbrand_min=round(minuten))


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
        kp, tn = pi_tyreus_luyben(ku, pu_min)
        return Vorschlag(True, f"{len(perioden)} Schwingungen ausgewertet, Ku = {ku:.2f} %/K.", kp=round(kp, 2),
                         tn=round(tn, 1), kd=0.0, periode_min=round(pu_min, 1), amplitude_k=round(amplitude, 1),
                         quelle="relaistest")
