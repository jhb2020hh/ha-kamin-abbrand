"""Einmessen: einen von Hand gefahrenen Abbrand aufzeichnen und Parameter daraus ableiten.

Ablauf: Der Nutzer startet die Aufzeichnung und zündet an. Die Klappe steht auf 100 %,
geregelt wird von Hand an den Ofenklappen, über N Nachlegezyklen. Der `Zyklenzaehler`
erkennt live Feuer, Nachlegen, Ende der Handregelung und kalten Ofen. Danach schlägt
`punkte_vorschlagen` Punkte im Trend vor, der Nutzer verschiebt oder löscht sie, und
`werte_ableiten` macht daraus Parametervorschläge (Mittel über die Zyklen).

Nur Standardbibliothek, keine Uhr, kein I/O. Zeiten in Sekunden (Unix-Zeit), Gradient in K/min.
"""
from __future__ import annotations

import bisect
import math
import statistics
from collections import deque
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from kern.typen import Parameter

# ---- Ereignisse ----------------------------------------------------------

FEUER = "feuer"
NACHLEGEN = "nachlegen"
FERTIG = "fertig"
KALT = "kalt"
KEIN_FEUER = "kein_feuer"

# Feuer: Startanstieg über dem Tiefstwert seit Aufzeichnungsbeginn, wie Parameter.startanstieg,
# oder ein anhaltender Gradient wie gradient_start. Der Gradient muss FEUER_G_DAUER_S lang
# halten und mindestens FEUER_G_MIN_K Anstieg gebracht haben, sonst zündet ein Ausreißer.
FEUER_ANSTIEG_K = 20.0
FEUER_G = 3.0                   # K/min
FEUER_G_DAUER_S = 120.0
FEUER_G_MIN_K = 5.0

# Eigener Gradient, wenn die Probe keinen mitbringt: Ausgleichsgerade über die letzten 120 s.
GRADIENT_FENSTER_S = 120.0
GRADIENT_MIN_S = 30.0

# Eine Türöffnung früher als SPERRE_S nach dem Feuer oder nach dem letzten Nachlegen zählt
# nicht: Beim Anzünden und nach dem Auflegen wird die Tür oft noch einmal kurz geöffnet,
# um die Scheite zurechtzurücken. Das fängt auch Prellen des Kontakts ab.
SPERRE_S = 600.0

# Ohne Türkontakt: Nachlegen = Umkehr des Gradienten. Erst FALL_S lang deutlich fallend
# (Beginn bei g ≤ -FALL_G, die Strecke hält, solange g ≤ -FALL_G/2), danach deutlich
# steigend: g ≥ STEIG_G und mindestens STEIG_K über dem Tiefstwert seit dem Fallen. Die
# 20 K trennen echtes Nachlegen von aufflackernden Flammen im Ausbrand (01./02.11.: 13–15 K).
# Als Zeitpunkt gilt der Tiefstwert, dort lag das Holz auf.
FALL_G = 1.0                    # K/min
FALL_S = 180.0
STEIG_G = 3.0                   # K/min
STEIG_K = 20.0
# Auch ohne Tür gilt die Sperre nach Feuer und Nachlegen. Und der Tiefstwert muss mindestens
# 25 % unter dem Höchstwert des Zyklus liegen: Wer im Abbrand von Hand drosselt und wieder
# öffnet, legt nicht nach.
UMKEHR_TIEF_ANTEIL = 0.75

# Meldet der Türkontakt so lange nichts (None), zählt der Zähler ab dann über die Gradientenumkehr.
TUER_AUSFALL_S = 120.0
# Prellschutz beim N-ten Nachlegen: Fertig erst, wenn die Tür so lange zu ist.
TUER_ZU_S = 30.0

# Kalt erst, wenn der Ofen seit dem Feuer einmal deutlich über t_kalt war. Sonst meldet
# ein Kaltstart von 20 °C schon beim Feuer (40 °C) „kalt“.
KALT_ABSTAND_K = 30.0


@dataclass(frozen=True)
class Probe:
    zeit: float
    t: Optional[float]
    g: Optional[float] = None           # K/min; None = der Kern rechnet selbst aus t
    tuer_offen: Optional[bool] = None
    klappe: Optional[float] = None
    twt: Optional[float] = None


@dataclass(frozen=True)
class Ereignis:
    art: str
    zeit: float
    zyklus: int                         # 1 = erstes Feuer, nach jedem Nachlegen +1


def _gueltig(t: Optional[float]) -> bool:
    return t is not None and math.isfinite(t)


def _steigung(punkte: Sequence[tuple[float, float]]) -> Optional[float]:
    """Steigung der Ausgleichsgeraden in K/min, None bei zu kurzer Strecke."""
    if len(punkte) < 3 or punkte[-1][0] - punkte[0][0] < GRADIENT_MIN_S:
        return None
    n = len(punkte)
    z0 = punkte[0][0]
    sx = sum(z - z0 for z, _ in punkte)
    sy = sum(w for _, w in punkte)
    sxx = sum((z - z0) ** 2 for z, _ in punkte)
    sxy = sum((z - z0) * w for z, w in punkte)
    nenner = n * sxx - sx * sx
    if nenner == 0:
        return None
    return (n * sxy - sx * sy) / nenner * 60.0


class Zyklenzaehler:
    """Zählt live die Zyklen einer Einmess-Aufzeichnung. Jede Probe einmal an `schritt`.

    Je Aufruf höchstens ein Ereignis. Fällt eines auf dieselbe Probe wie ein anderes
    (ohne Türkontakt: N-tes Nachlegen und Fertig), kommt das zweite mit der nächsten Probe.
    Nach FERTIG zählt er weiter, nach KALT oder KEIN_FEUER ist die Aufzeichnung beendet.
    KALT kommt auch ohne FERTIG, wenn das Feuer vor dem N-ten Nachlegen ausgeht. KEIN_FEUER kommt
    auch nach FEUER, wenn der Ofen in kein_feuer_s nie richtig warm wird (t_kalt + 30 K).
    Mit Türkontakt kommt FERTIG erst, wenn die Tür TUER_ZU_S lang zu ist. Meldet der Kontakt
    TUER_AUSFALL_S lang nichts, zählt er ab dann über die Gradientenumkehr (tuer_ausgefallen).
    """

    def __init__(self, n_soll: int, tuerkontakt: bool, t_kalt: float = 50.0,
                 kein_feuer_s: float = 3600.0) -> None:
        self.n_soll = max(0, int(n_soll))
        self.tuerkontakt = tuerkontakt
        self.t_kalt = t_kalt
        self.kein_feuer_s = kein_feuer_s
        self.zyklus = 0
        self.nachgelegt = 0
        self.fertig = False
        self.beendet = False
        self.ereignisse: list[Ereignis] = []
        self._start: Optional[float] = None
        self._verlauf: deque[tuple[float, float]] = deque()
        self._tief: Optional[float] = None
        self._g_seit: Optional[float] = None
        self._feuer_zeit: Optional[float] = None
        self._warm = False
        self._sperre_bis = -math.inf
        self._tuer_offen = False            # zuletzt gemeldeter Türzustand, None ändert nichts
        self._fall_seit: Optional[float] = None
        self._gefallen = False
        self._tmin: Optional[float] = None
        self._tmin_zeit = 0.0
        self._zyklus_max: Optional[float] = None    # Höchstwert seit Feuer bzw. letztem Nachlegen
        self._tuer_weg_seit: Optional[float] = None
        self._zu_seit: Optional[float] = None       # seit wann die Tür durchgehend zu gemeldet ist
        self.tuer_ausgefallen = False               # True: Türkontakt ausgefallen, jetzt Gradientenumkehr

    # ---- Hilfen ----

    def _melden(self, art: str, zeit: float) -> Ereignis:
        ereignis = Ereignis(art, zeit, self.zyklus)
        self.ereignisse.append(ereignis)
        return ereignis

    def _gradient(self, probe: Probe) -> Optional[float]:
        if probe.g is not None and math.isfinite(probe.g):
            return probe.g
        return _steigung(list(self._verlauf))

    def _nachlegen(self, zeit: float, jetzt: float, t_tief: Optional[float] = None) -> Ereignis:
        self.zyklus += 1
        self.nachgelegt += 1
        self._sperre_bis = jetzt + SPERRE_S
        self._fall_seit, self._gefallen, self._tmin = None, False, None
        self._zyklus_max = t_tief
        return self._melden(NACHLEGEN, zeit)

    def _tuer_verfolgen(self, probe: Probe) -> None:
        """Türkontakt ohne Meldung: nach TUER_AUSFALL_S auf die Gradientenumkehr umschalten."""
        if not self.tuerkontakt:
            return
        if probe.tuer_offen is None:
            if self._tuer_weg_seit is None:
                self._tuer_weg_seit = probe.zeit
            if probe.zeit - self._tuer_weg_seit >= TUER_AUSFALL_S:
                self.tuerkontakt = False
                self.tuer_ausgefallen = True
        else:
            self._tuer_weg_seit = None
        if probe.tuer_offen is False:
            if self._zu_seit is None:
                self._zu_seit = probe.zeit
        elif probe.tuer_offen is True:
            self._zu_seit = None

    # ---- Ablauf ----

    def schritt(self, probe: Probe) -> Optional[Ereignis]:
        if self.beendet:
            return None
        if self._start is None:
            self._start = probe.zeit
        t = probe.t if _gueltig(probe.t) else None
        if t is not None:
            self._verlauf.append((probe.zeit, t))
        while self._verlauf and self._verlauf[0][0] < probe.zeit - GRADIENT_FENSTER_S:
            self._verlauf.popleft()
        g = self._gradient(probe) if t is not None else None
        tuer_vorher = self._tuer_offen
        if probe.tuer_offen is not None:
            self._tuer_offen = probe.tuer_offen
        self._tuer_verfolgen(probe)
        ereignis: Optional[Ereignis] = None

        if self._feuer_zeit is None:
            # Eine Öffnung, die vor dem Feuer begann (Anzünden), zählt nie: Es fehlt ihr Beginn.
            return self._auf_feuer_warten(probe, t, g)

        # Nachlegen
        if self.tuerkontakt:
            if self._tuer_offen and not tuer_vorher and probe.zeit >= self._sperre_bis:
                ereignis = self._nachlegen(probe.zeit, probe.zeit)
        elif t is not None and g is not None:
            ereignis = self._umkehr(probe.zeit, t, g)

        if t is not None:
            self._zyklus_max = t if self._zyklus_max is None else max(self._zyklus_max, t)
        if t is not None and t >= self.t_kalt + KALT_ABSTAND_K:
            self._warm = True

        if ereignis is None and not self._warm and probe.zeit - self._feuer_zeit >= self.kein_feuer_s:
            # Feuer erkannt, aber nie richtig warm geworden: wie kein Feuer
            self.beendet = True
            return self._melden(KEIN_FEUER, probe.zeit)

        if ereignis is None and not self.fertig and self.nachgelegt >= self.n_soll:
            if self.tuerkontakt:
                tuer_zu = (probe.tuer_offen is False and self._zu_seit is not None
                           and probe.zeit - self._zu_seit >= TUER_ZU_S)
            else:
                tuer_zu = True
            if tuer_zu:
                self.fertig = True
                ereignis = self._melden(FERTIG, probe.zeit)

        if ereignis is None and self._warm and t is not None and t < self.t_kalt:
            self.beendet = True
            ereignis = self._melden(KALT, probe.zeit)
        return ereignis

    def _auf_feuer_warten(self, probe: Probe, t: Optional[float], g: Optional[float]) -> Optional[Ereignis]:
        if t is not None:
            self._tief = t if self._tief is None else min(self._tief, t)
            if g is not None and g >= FEUER_G:
                if self._g_seit is None:
                    self._g_seit = probe.zeit
            else:
                self._g_seit = None
            anstieg = t - self._tief
            per_gradient = (self._g_seit is not None and probe.zeit - self._g_seit >= FEUER_G_DAUER_S
                            and anstieg >= FEUER_G_MIN_K)
            if anstieg >= FEUER_ANSTIEG_K or per_gradient:
                self._feuer_zeit = probe.zeit
                self.zyklus = 1
                self._sperre_bis = probe.zeit + SPERRE_S
                self._zyklus_max = t
                self._warm = t >= self.t_kalt + KALT_ABSTAND_K
                return self._melden(FEUER, probe.zeit)
        if probe.zeit - self._start >= self.kein_feuer_s:
            self.beendet = True
            return self._melden(KEIN_FEUER, probe.zeit)
        return None

    def _umkehr(self, zeit: float, t: float, g: float) -> Optional[Ereignis]:
        if self._fall_seit is None:
            if g <= -FALL_G:
                self._fall_seit = zeit
        elif g > -FALL_G / 2:
            self._fall_seit = None
        if self._fall_seit is not None and zeit - self._fall_seit >= FALL_S:
            self._gefallen = True
        if not self._gefallen:
            return None
        if self._tmin is None or t < self._tmin:
            self._tmin, self._tmin_zeit = t, zeit
        if g >= STEIG_G and t - self._tmin >= STEIG_K:
            tief_genug = self._zyklus_max is None or self._tmin <= UMKEHR_TIEF_ANTEIL * self._zyklus_max
            if zeit >= self._sperre_bis and tief_genug:
                return self._nachlegen(self._tmin_zeit, zeit, self._tmin)
            # In der Sperre oder zu flach (von Hand gedrosselt): Diese Umkehr zählt nie, auch später nicht.
            self._fall_seit, self._gefallen, self._tmin = None, False, None
        return None


def ereignisse_ermitteln(proben: Sequence[Probe], n_soll: int, tuerkontakt: bool,
                         t_kalt: float = 50.0, kein_feuer_s: float = 3600.0) -> list[Ereignis]:
    """Spielt den Zyklenzähler über eine fertige Aufzeichnung ab (Nachauswertung)."""
    zaehler = Zyklenzaehler(n_soll, tuerkontakt, t_kalt, kein_feuer_s)
    for probe in sorted(proben, key=lambda p: p.zeit):
        zaehler.schritt(probe)
    return list(zaehler.ereignisse)


# ---- Punkte im Trend -----------------------------------------------------

PUNKTARTEN = ("anheizen_max", "abbrand_stabil", "bester_abbrand", "glut", "abkuehlen", "zu_heiss")
PUNKT_NAMEN = {"anheizen_max": "Anheizen max", "abbrand_stabil": "Abbrand stabil",
               "bester_abbrand": "Bester Abbrand", "glut": "Glut", "abkuehlen": "Abkühlen",
               "zu_heiss": "Zu heiß"}

# Für die Auswertung rechnet der Kern den Gradienten immer selbst: Ausgleichsgerade ±60 s um
# jede Probe, also ohne Nachlauf. Der Gradient der HA-Helfer ist dafür zu unruhig.
AUSWERT_FENSTER_S = 60.0
MEDIAN_FENSTER_S = 60.0             # t am Punkt = gleitender Median ±60 s
PLATEAU_FENSTER_S = 150.0           # Plateau-Suche auf dem Median ±150 s (5 min)
PLATEAU_K = 10.0                    # zum Plateau gehört, was höchstens 10 K unter dem Höchstwert liegt
MIN_ANSTIEG_G = 1.0                 # K/min, schwächer gilt nicht als Anstieg
ANHEIZEN_ANTEIL = 0.5               # anheizen_max: Gradient unter 50 % seines Maximums
# abbrand_stabil: Der 1-min-Median hat 65 % des Anstiegs vom Tiefstwert des Zyklus bis zum
# Plateau (Niveau am Punkt „Bester Abbrand“) geschafft. t_abbrandstart ist die Schwelle
# Anheizen → Abbrand und soll deutlich unter dem Ziel liegen (bisher 160 bei Ziel 220).
ANTEIL_ABBRANDSTART = 0.65
MIN_ANSTIEG_K = 20.0                # kleinerer Anstieg vom Tiefstwert zum Plateau: kein Abbrand-stabil-Punkt
GLUT_ANTEIL = 0.5                   # letzter Zyklus: Abfall halb so steil wie am steilsten
MIN_ABFALL_G = 1.0                  # K/min, flacher gilt nicht als Abfall
ABFALL_FENSTER_S = 300.0            # Abfall langsam: Gradient dafür über ±5 min, glättet Flackern


@dataclass(frozen=True)
class Punkt:
    art: str
    zeit: float
    zyklus: int
    auto: bool = True


class _Reihe:
    """Gültige Proben einer Aufzeichnung, nach Zeit, mit Median- und Gradient-Abfragen."""

    def __init__(self, proben: Sequence[Probe]) -> None:
        gueltig = sorted((p for p in proben if _gueltig(p.t)), key=lambda p: p.zeit)
        self.z = [p.zeit for p in gueltig]
        self.t = [float(p.t) for p in gueltig]
        self._g: dict[tuple[int, float], Optional[float]] = {}

    def __len__(self) -> int:
        return len(self.z)

    def _bereich(self, von: float, bis: float) -> tuple[int, int]:
        return bisect.bisect_left(self.z, von), bisect.bisect_right(self.z, bis)

    def median(self, zeit: float, fenster: float = MEDIAN_FENSTER_S) -> Optional[float]:
        a, b = self._bereich(zeit - fenster, zeit + fenster)
        return statistics.median(self.t[a:b]) if b > a else None

    def g(self, i: int, fenster: float = AUSWERT_FENSTER_S) -> Optional[float]:
        if (i, fenster) not in self._g:
            a, b = self._bereich(self.z[i] - fenster, self.z[i] + fenster)
            self._g[(i, fenster)] = _steigung(list(zip(self.z[a:b], self.t[a:b])))
        return self._g[(i, fenster)]

    def g_breit(self, i: int) -> Optional[float]:
        return self.g(i, ABFALL_FENSTER_S)

    def g_median(self, von: float, bis: float) -> Optional[float]:
        a, b = self._bereich(von, bis)
        werte = [w for w in (self.g(i) for i in range(a, b)) if w is not None]
        return statistics.median(werte) if werte else None

    def indizes(self, von: float, bis: float) -> range:
        a, b = self._bereich(von, bis)
        return range(a, b)


def _erster(idx: Sequence[int], gradient, bedingung) -> Optional[int]:
    return next((i for i in idx if (w := gradient(i)) is not None and bedingung(w)), None)


def _zyklusgrenzen(reihe: _Reihe, ereignisse: Sequence[Ereignis]) -> list[tuple[int, float, float]]:
    """(zyklus, von, bis): Zyklus 1 ab Aufzeichnungsbeginn, jeder weitere ab seinem Nachlegen."""
    if not any(e.art == FEUER for e in ereignisse):
        return []
    starts = {1: reihe.z[0]}
    for e in ereignisse:
        if e.art == NACHLEGEN:
            starts[e.zyklus] = e.zeit
    nummern = sorted(starts)
    grenzen = []
    for k, nr in enumerate(nummern):
        bis = starts[nummern[k + 1]] if k + 1 < len(nummern) else reihe.z[-1]
        grenzen.append((nr, starts[nr], bis))
    return grenzen


def punkte_vorschlagen(proben: Sequence[Probe], ereignisse: Sequence[Ereignis],
                       t_kalt: float = 50.0) -> list[Punkt]:
    """Schlägt je Zyklus Punkte vor. Regeln (Gradient = eigene Ausgleichsgerade ±60 s):

    - anheizen_max (nur Zyklus 1): erster Zeitpunkt nach dem steilsten Anstieg, an dem der
      Gradient unter 50 % seines Maximums fällt. Dort würde man die Klappe zurücknehmen.
    - abbrand_stabil: der 1-min-Median hat 65 % des Anstiegs vom Tiefstwert des Zyklus bis
      zum Niveau am Punkt „Bester Abbrand“ erreicht.
    - bester_abbrand: Mitte des Plateaus um das Maximum des 5-min-Medians (alles höchstens
      10 K darunter, zusammenhängend).
    - glut: Zeitpunkt des Nachlegens. Im letzten Zyklus: nach dem steilsten Abfall die
      Stelle, an der der Abfall nur noch halb so steil ist (Übergang in flachen Abfall).
      Der Abfall-Gradient kommt aus ±5 min, damit Flackern ihn nicht vorzeitig halbiert.
    - abkuehlen (einmal, im letzten Zyklus nach der Glut): wo der Median die Mitte zwischen
      t am Glutpunkt und t_kalt erreicht. Fehlt, wenn die Daten nicht so weit reichen.
    - zu_heiss setzt nur der Nutzer.
    """
    reihe = _Reihe(proben)
    if len(reihe) < 3:
        return []
    grenzen = _zyklusgrenzen(reihe, ereignisse)
    punkte: list[Punkt] = []
    for k, (zyklus, von, bis) in enumerate(grenzen):
        letzter = k == len(grenzen) - 1
        idx = reihe.indizes(von, bis)
        if len(idx) < 3:
            continue
        mediane = {i: reihe.median(reihe.z[i], PLATEAU_FENSTER_S) for i in idx}
        i_spitze = max(idx, key=lambda i: mediane[i])
        anstieg = [i for i in idx if i <= i_spitze and reihe.g(i) is not None]
        if anstieg:
            i_gmax = max(anstieg, key=reihe.g)
            gmax = reihe.g(i_gmax)
            if gmax >= MIN_ANSTIEG_G:
                danach = range(i_gmax + 1, idx.stop)
                if zyklus == 1:
                    i = _erster(danach, reihe.g, lambda w: w < ANHEIZEN_ANTEIL * gmax)
                    if i is not None:
                        punkte.append(Punkt("anheizen_max", reihe.z[i], zyklus))
        # Plateau um das Maximum
        spitze = mediane[i_spitze]
        a = b = i_spitze
        while a - 1 >= idx.start and mediane[a - 1] >= spitze - PLATEAU_K:
            a -= 1
        while b + 1 < idx.stop and mediane[b + 1] >= spitze - PLATEAU_K:
            b += 1
        if i_spitze > idx.start:            # sonst gab es in diesem Zyklus keinen Anstieg
            bester = (reihe.z[a] + reihe.z[b]) / 2
            punkte.append(Punkt("bester_abbrand", bester, zyklus))
            kurz = {i: reihe.median(reihe.z[i]) for i in range(idx.start, i_spitze + 1)}
            i_tief = min(kurz, key=kurz.get)
            tief, niveau = kurz[i_tief], reihe.median(bester)
            if niveau - tief >= MIN_ANSTIEG_K:
                schwelle = tief + ANTEIL_ABBRANDSTART * (niveau - tief)
                i_stabil = next((i for i in range(i_tief, i_spitze + 1) if kurz[i] >= schwelle), None)
                if i_stabil is not None:
                    punkte.append(Punkt("abbrand_stabil", reihe.z[i_stabil], zyklus))

        if not letzter:
            punkte.append(Punkt("glut", bis, zyklus))
            continue
        abfall = [i for i in range(i_spitze, idx.stop) if reihe.g_breit(i) is not None]
        if not abfall:
            continue
        i_gmin = min(abfall, key=reihe.g_breit)
        gmin = reihe.g_breit(i_gmin)
        if gmin > -MIN_ABFALL_G:
            continue
        i_glut = _erster(range(i_gmin + 1, idx.stop), reihe.g_breit, lambda w: w > GLUT_ANTEIL * gmin)
        if i_glut is None:
            continue
        punkte.append(Punkt("glut", reihe.z[i_glut], zyklus))
        t_glut = reihe.median(reihe.z[i_glut])
        ziel = (t_glut + t_kalt) / 2
        i_abk = next((i for i in range(i_glut + 1, idx.stop) if reihe.median(reihe.z[i]) <= ziel), None)
        if i_abk is not None:
            punkte.append(Punkt("abkuehlen", reihe.z[i_abk], zyklus))
    return sorted(punkte, key=lambda p: p.zeit)


# ---- Parameter ableiten ---------------------------------------------------

ZIELE_VORGABE = {"t_ziel_eco": 220.0, "t_ziel_normal": 250.0, "t_ziel_power": 270.0}
# gradient_start soll das Feuer früh erkennen, nicht erst bei vollem Feuer: ein Drittel des
# medianen Gradienten in den ersten 5 min nach FEUER bzw. NACHLEGEN, Mittel über die Zyklen,
# vor den Feldgrenzen hart auf 1–10 K/min begrenzt.
GRADIENT_FRUEH_S = 300.0
GRADIENT_ANTEIL = 1 / 3
GRADIENT_MIN, GRADIENT_MAX = 1.0, 10.0
AUSBRAND_ANTEIL = 0.3               # ausbrand_abfall = 30 % vom Abstand Maximum → Glut


@dataclass(frozen=True)
class Vorschlag:
    feld: str
    wert: float
    alt: float
    begruendung: str
    punkte: int                         # wie viele Punkte eingingen


def _zahl(wert: float) -> str:
    if abs(wert - round(wert)) < 1e-9:
        return str(int(round(wert)))
    return f"{wert:.1f}".replace(".", ",")


def _runden(feld: str, wert: float, grenzen: Mapping[str, tuple[float, float, float]]) -> tuple[float, bool]:
    """Auf den Schritt runden und begrenzen. Zweiter Wert: True, wenn begrenzt wurde."""
    if feld not in grenzen:
        return round(wert, 1), False
    minimum, maximum, schritt = grenzen[feld]
    gerundet = round(round(wert / schritt) * schritt, 6) if schritt > 0 else wert
    begrenzt = min(max(gerundet, minimum), maximum)
    return round(begrenzt, 6), begrenzt != gerundet


def _herkunft(n: int) -> str:
    return "Aus 1 Zyklus" if n == 1 else f"Mittel aus {n} Zyklen"


def werte_ableiten(proben: Sequence[Probe], punkte: Sequence[Punkt], parameter: Parameter,
                   grenzen: Mapping[str, tuple[float, float, float]],
                   ziele_alt: Optional[Mapping[str, float]] = None,
                   ereignisse: Optional[Sequence[Ereignis]] = None) -> list[Vorschlag]:
    """Parametervorschläge aus den (vom Nutzer geprüften) Punkten. t am Punkt = Median ±60 s.

    gradient_start braucht die Zyklusanfänge aus `ereignisse` (FEUER, NACHLEGEN). Fehlen sie,
    gilt ersatzweise je Punkt „Abbrand stabil“ als Anfang die Stelle, an der der Median davor
    zuerst 20 K über seinem Tiefstwert lag, wie bei der Feuererkennung.

    Felder ohne passende Punkte fehlen. Unplausible Reihenfolgen (t_glut < t_abbrandstart
    < t_ziel, t_abkuehlen < t_glut) werden nicht korrigiert, sondern in der Begründung gemeldet.
    """
    reihe = _Reihe(proben)
    if len(reihe) == 0:
        return []
    ziele = {**ZIELE_VORGABE, **(ziele_alt or {})}
    nach_art: dict[str, list[tuple[Punkt, float]]] = {art: [] for art in PUNKTARTEN}
    for p in sorted(punkte, key=lambda p: p.zeit):
        t = reihe.median(p.zeit)
        if p.art in nach_art and t is not None:
            nach_art[p.art].append((p, t))

    roh: dict[str, tuple[float, float, str, str, int]] = {}  # feld → (wert, alt, einheit, text, punkte)

    def mittel_aus(feld: str, art: str) -> None:
        liste = nach_art[art]
        if liste:
            wert = statistics.mean(t for _, t in liste)
            roh[feld] = (wert, getattr(parameter, feld), "°C", f" beim Punkt „{PUNKT_NAMEN[art]}“", len(liste))

    mittel_aus("t_kaltstart_ende", "anheizen_max")
    mittel_aus("t_abbrandstart", "abbrand_stabil")
    if ereignisse is not None:
        anfaenge = [e.zeit for e in ereignisse if e.art in (FEUER, NACHLEGEN)]
    else:
        anfaenge, vorher = [], reihe.z[0]
        for p, _ in nach_art["abbrand_stabil"]:
            idx = reihe.indizes(vorher, p.zeit)
            vorher = p.zeit
            if not len(idx):
                continue
            i_tief = min(idx, key=lambda i: reihe.median(reihe.z[i]))
            tief = reihe.median(reihe.z[i_tief])
            i = next((i for i in range(i_tief, idx.stop) if reihe.median(reihe.z[i]) >= tief + FEUER_ANSTIEG_K),
                     i_tief)
            anfaenge.append(reihe.z[i])
    gradienten = [g for z in anfaenge
                  if (g := reihe.g_median(z, z + GRADIENT_FRUEH_S)) is not None and g > 0]
    if gradienten:
        roh_wert = GRADIENT_ANTEIL * statistics.mean(gradienten)
        wert = min(max(roh_wert, GRADIENT_MIN), GRADIENT_MAX)
        text = ", ein Drittel des Anstiegs in den ersten 5 min nach Feuer bzw. Nachlegen"
        if wert != roh_wert:
            text += f" ({_zahl(round(roh_wert, 1))} K/min, auf 1–10 K/min begrenzt)"
        roh["gradient_start"] = (wert, parameter.gradient_start, "K/min", text, len(gradienten))
    mittel_aus("t_ziel", "bester_abbrand")
    mittel_aus("t_glut", "glut")
    mittel_aus("t_abkuehlen", "abkuehlen")
    mittel_aus("t_uebertemperatur", "zu_heiss")

    # Ausbrand-Abfall: je Glutpunkt der Höchstwert seit dem vorigen Glutpunkt, davon 30 % des Abstands
    abstaende, vorher = [], reihe.z[0]
    for p, t_glut in nach_art["glut"]:
        werte = [m for i in reihe.indizes(vorher, p.zeit) if (m := reihe.median(reihe.z[i])) is not None]
        vorher = p.zeit
        if werte and max(werte) > t_glut:
            abstaende.append(max(werte) - t_glut)
    if abstaende:
        roh["ausbrand_abfall"] = (AUSBRAND_ANTEIL * statistics.mean(abstaende), parameter.ausbrand_abfall,
                                  "K", ", das sind 30 % vom Abstand Höchstwert → „Glut“", len(abstaende))

    werte: dict[str, float] = {}
    vorschlaege: dict[str, Vorschlag] = {}
    for feld, (wert, alt, einheit, text, n) in roh.items():
        gerundet, begrenzt = _runden(feld, wert, grenzen)
        werte[feld] = gerundet
        satz = f"{_herkunft(n)}: {_zahl(round(wert, 1))} {einheit}{text}."
        if begrenzt:
            satz += f" Auf {_zahl(gerundet)} {einheit} begrenzt."
        vorschlaege[feld] = Vorschlag(feld, gerundet, alt, satz, n)

    # Leistungsstufen: gleicher Abstand wie bisher, um die Änderung von Normal verschoben
    if "t_ziel" in vorschlaege:
        ziel = vorschlaege["t_ziel"]
        verschiebung = ziel.wert - ziele["t_ziel_normal"]
        for feld in ("t_ziel_eco", "t_ziel_normal", "t_ziel_power"):
            wert, begrenzt = _runden(feld, ziele[feld] + verschiebung, grenzen)
            if feld == "t_ziel_normal":
                satz = f"Wie die Zieltemperatur ({_zahl(ziel.wert)} °C)."
            else:
                satz = (f"Abstand zu Normal bleibt, um {_zahl(verschiebung)} K verschoben."
                        if verschiebung else "Abstand zu Normal bleibt, unverändert.")
            if begrenzt:
                satz += f" Auf {_zahl(wert)} °C begrenzt."
            vorschlaege[feld] = Vorschlag(feld, wert, ziele[feld], satz, ziel.punkte)

    _plausibel_pruefen(vorschlaege, werte, parameter)
    reihenfolge = ("t_kaltstart_ende", "t_abbrandstart", "gradient_start", "t_ziel", "t_ziel_eco",
                   "t_ziel_normal", "t_ziel_power", "ausbrand_abfall", "t_glut", "t_abkuehlen",
                   "t_uebertemperatur")
    return [vorschlaege[f] for f in reihenfolge if f in vorschlaege]


_NAMEN = {"t_glut": "Glut", "t_abbrandstart": "Abbrand stabil", "t_ziel": "Ziel", "t_abkuehlen": "Abkühlen"}


def _plausibel_pruefen(vorschlaege: dict[str, Vorschlag], werte: Mapping[str, float], p: Parameter) -> None:
    """Reihenfolge prüfen, neue Werte gegen neue oder bisherige. Nur warnen, nichts ändern."""
    def wert(feld: str) -> float:
        return werte.get(feld, getattr(p, feld))

    for unten, oben in (("t_glut", "t_abbrandstart"), ("t_abbrandstart", "t_ziel"), ("t_abkuehlen", "t_glut")):
        if unten not in werte and oben not in werte:
            continue
        if wert(unten) < wert(oben):
            continue
        warnung = (f" Achtung: {_NAMEN[unten]} ({_zahl(wert(unten))} °C) liegt nicht unter "
                   f"{_NAMEN[oben]} ({_zahl(wert(oben))} °C), Punkte prüfen.")
        for feld in (unten, oben):
            if feld in werte:
                v = vorschlaege[feld]
                vorschlaege[feld] = Vorschlag(v.feld, v.wert, v.alt, v.begruendung + warnung, v.punkte)
