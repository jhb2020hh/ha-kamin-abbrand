"""Überwachung der Wasserseite eines wassergeführten Ofens. Rein, ohne Home Assistant.

Die App beobachtet hier nur und empfiehlt. Sie schaltet nie eine Pumpe, und das hier ersetzt
weder eine thermische Ablaufsicherung noch eine andere Sicherheitseinrichtung.

Jede Regel läuft nur, wenn alle Fühler, die sie braucht, zugewiesen sind und gültige Werte liefern.
Eine Regel schlägt erst nach ihrer Entprellzeit an und fällt erst zurück, wenn der Wert um die
Hysterese (2 K) auf der sicheren Seite liegt. Werte außerhalb von −20 bis 120 °C gelten als
Fühlerfehler: Ein offener Fühler meldet zum Beispiel 1638,3 °C.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

OK, HINWEIS, WARNUNG, ALARM = "ok", "hinweis", "warnung", "alarm"
STUFEN = (OK, HINWEIS, WARNUNG, ALARM)
T_MIN_GUELTIG, T_MAX_GUELTIG = -20.0, 120.0
HYSTERESE_K = 2.0
FEUER_PHASEN = ("Anheizen", "Abbrand", "Ausbrand", "Glut")      # Das Feuer erwärmt das Wasser
BRENN_PHASEN = ("Abbrand", "Ausbrand")                          # Der Rücklauf soll warm sein
FUEHLER_UNPLAUSIBEL_S = 60.0
FUEHLER_FEHLT_S = 300.0
TEMPERATUR_FUEHLER = ("t_wt", "vorlauf", "ruecklauf", "puffer_unten")
FUEHLER_TITEL = {"t_wt": "Wärmetauscher", "vorlauf": "Vorlauf", "ruecklauf": "Rücklauf", "puffer_unten": "Puffer unten",
                 "pumpe": "Pumpe"}


@dataclass(frozen=True)
class Grenzen:
    ruecklauf_min: float = 55.0         # darunter ist der Rücklauf zu kalt
    ruecklauf_dauer_s: float = 300.0
    vorlauf_warnung: float = 90.0
    vorlauf_alarm: float = 95.0
    t_wasser_max: float = 85.0
    pumpe_t_wt: float = 70.0            # so heiß darf das Wasser sein, wenn die Pumpe bei Feuer steht
    pumpe_dauer_s: float = 120.0
    puffer_voll: float = 60.0
    entprellung_s: float = 30.0

    def stimmig(self) -> "Grenzen":
        """Der Alarm liegt nie unter der Warnung."""
        if self.vorlauf_alarm >= self.vorlauf_warnung:
            return self
        return Grenzen(**{**self.__dict__, "vorlauf_alarm": self.vorlauf_warnung})


@dataclass(frozen=True)
class Messwerte:
    """`zugewiesen` nennt die Fühler, die es gibt. Ein zugewiesener Fühler ohne Wert ist `None`."""
    zugewiesen: frozenset[str] = frozenset()
    t_wt: Optional[float] = None
    vorlauf: Optional[float] = None
    ruecklauf: Optional[float] = None
    puffer_unten: Optional[float] = None
    pumpe: Optional[bool] = None
    phase: Optional[str] = None


@dataclass(frozen=True)
class Befund:
    schluessel: str
    stufe: str
    text: str           # Gleichbleibend, solange der Befund besteht: Er geht auch als Push raus
    wert: Optional[float] = None

    def als_dict(self) -> dict:
        return {"schluessel": self.schluessel, "stufe": self.stufe, "text": self.text, "wert": self.wert}


@dataclass(frozen=True)
class Ergebnis:
    stufe: str = OK
    befunde: tuple[Befund, ...] = ()

    @property
    def texte(self) -> list[str]:
        return [b.text for b in self.befunde]

    def als_dict(self) -> dict:
        return {"stufe": self.stufe, "befunde": [b.als_dict() for b in self.befunde]}


def gueltig(wert: Optional[float]) -> bool:
    return wert is not None and math.isfinite(wert) and T_MIN_GUELTIG <= wert <= T_MAX_GUELTIG


@dataclass
class _Regel:
    aktiv: bool = False
    seit: Optional[float] = None


class Wasserwache:
    """Hält die Zustände der Regeln zwischen den Läufen. Ein Lauf ist `pruefen`."""

    def __init__(self) -> None:
        self._regeln: dict[str, _Regel] = {}

    def _ruhen(self, *namen: str) -> None:
        for name in namen:
            self._regeln.pop(name, None)

    def _regel(self, name: str, jetzt: float, ein: Optional[bool], aus: Optional[bool], dauer_s: float) -> bool:
        """`ein`: Die Bedingung trifft zu. `aus`: Sie ist sicher vorbei. `None`: nicht auswertbar, die Regel ruht."""
        r = self._regeln.setdefault(name, _Regel())
        if ein is None:
            r.aktiv, r.seit = False, None
            return False
        if r.aktiv:
            if aus:
                r.aktiv, r.seit = False, None
            return r.aktiv
        if not ein:
            r.seit = None
            return False
        if r.seit is None:
            r.seit = jetzt
        if jetzt - r.seit >= dauer_s:
            r.aktiv = True
        return r.aktiv

    def _hoch(self, name: str, jetzt: float, wert: Optional[float], grenze: float, dauer_s: float,
              bedingung: bool = True) -> bool:
        """Schlägt an ab `grenze`, fällt zurück unter `grenze` − Hysterese."""
        if not gueltig(wert):
            return self._regel(name, jetzt, None, None, dauer_s)
        return self._regel(name, jetzt, bedingung and wert >= grenze, (not bedingung) or wert < grenze - HYSTERESE_K, dauer_s)

    def pruefen(self, jetzt: float, m: Messwerte, g: Grenzen) -> Ergebnis:
        g = g.stimmig()
        werte = {n: getattr(m, n) for n in TEMPERATUR_FUEHLER}
        befunde: list[Befund] = []

        for name in TEMPERATUR_FUEHLER:
            wert = werte[name]
            if name not in m.zugewiesen:
                self._ruhen(f"fuehler_{name}", f"fehlt_{name}")
                continue
            unplausibel, fehlt = wert is not None and not gueltig(wert), wert is None
            if self._regel(f"fuehler_{name}", jetzt, unplausibel, not unplausibel, FUEHLER_UNPLAUSIBEL_S):
                befunde.append(Befund(f"fuehler_{name}", WARNUNG,
                                      f"Der Fühler „{FUEHLER_TITEL[name]}“ meldet unplausible Werte. Die Regeln dazu ruhen.", wert))
            if self._regel(f"fehlt_{name}", jetzt, fehlt, not fehlt, FUEHLER_FEHLT_S):
                befunde.append(Befund(f"fehlt_{name}", HINWEIS,
                                      f"Der Fühler „{FUEHLER_TITEL[name]}“ liefert seit Minuten keinen Wert. Die Regeln dazu ruhen."))

        def hat(*fuehler: str) -> bool:
            return all(f in m.zugewiesen for f in fuehler)

        feuer = m.phase in FEUER_PHASEN
        brennt = m.phase in BRENN_PHASEN

        # Rücklauf zu kalt: nur im Abbrand und Ausbrand und nur bei laufender Pumpe.
        if hat("ruecklauf", "pumpe") and m.pumpe is not None and gueltig(m.ruecklauf):
            kontext = brennt and m.pumpe is True
            if self._regel("ruecklauf_kalt", jetzt, kontext and m.ruecklauf < g.ruecklauf_min,
                           (not kontext) or m.ruecklauf >= g.ruecklauf_min + HYSTERESE_K, g.ruecklauf_dauer_s):
                befunde.append(Befund("ruecklauf_kalt", WARNUNG,
                                      f"Der Rücklauf ist seit mehr als {g.ruecklauf_dauer_s / 60:.0f} Minuten unter {g.ruecklauf_min:.0f} °C. "
                                      "Das kann den Ofen schädigen. Bitte die Rücklaufanhebung prüfen.", m.ruecklauf))
        else:
            self._ruhen("ruecklauf_kalt")

        # Vorlauf: Warnung und Alarm sind zwei Stufen derselben Messung.
        vorlauf = m.vorlauf if hat("vorlauf") else None     # ohne Fühler ruhen beide Stufen
        vorlauf_alarm = self._hoch("vorlauf_alarm", jetzt, vorlauf, g.vorlauf_alarm, g.entprellung_s)
        vorlauf_warnung = self._hoch("vorlauf_warnung", jetzt, vorlauf, g.vorlauf_warnung, g.entprellung_s)
        if vorlauf_alarm:
            befunde.append(Befund("vorlauf_zu_hoch", ALARM,
                                  f"Der Vorlauf ist mit mehr als {g.vorlauf_alarm:.0f} °C zu heiß. Bitte sofort nachsehen.", m.vorlauf))
        elif vorlauf_warnung:
            befunde.append(Befund("vorlauf_hoch", WARNUNG, f"Der Vorlauf ist mit über {g.vorlauf_warnung:.0f} °C hoch.", m.vorlauf))

        # Wasser am Ofen zu heiß.
        if self._hoch("twt_zu_heiss", jetzt, m.t_wt if hat("t_wt") else None, g.t_wasser_max, g.entprellung_s):
            befunde.append(Befund("twt_zu_heiss", ALARM,
                                  f"Das Wasser im Wärmetauscher ist heißer als {g.t_wasser_max:.0f} °C. Bitte sofort nachsehen.", m.t_wt))

        # Wasser heiß, aber die Pumpe steht bei Feuer.
        if hat("t_wt", "pumpe") and m.pumpe is not None and gueltig(m.t_wt):
            kontext = feuer and m.pumpe is False
            if self._regel("pumpe_steht", jetzt, kontext and m.t_wt >= g.pumpe_t_wt,
                           (not kontext) or m.t_wt < g.pumpe_t_wt - HYSTERESE_K, g.pumpe_dauer_s):
                befunde.append(Befund("pumpe_steht", ALARM,
                                      f"Das Wasser im Wärmetauscher ist über {g.pumpe_t_wt:.0f} °C heiß, aber die Pumpe steht, obwohl Feuer brennt. "
                                      "Bitte sofort nachsehen.", m.t_wt))
        else:
            self._ruhen("pumpe_steht")

        # Puffer voll.
        if self._hoch("puffer_voll", jetzt, m.puffer_unten if hat("puffer_unten") else None, g.puffer_voll, g.entprellung_s):
            befunde.append(Befund("puffer_voll", HINWEIS,
                                  f"Der Puffer ist unten über {g.puffer_voll:.0f} °C warm und nimmt kaum noch Wärme auf. Besser nicht nachlegen.",
                                  m.puffer_unten))

        rang = {s: i for i, s in enumerate(STUFEN)}
        befunde.sort(key=lambda b: -rang[b.stufe])
        return Ergebnis(befunde[0].stufe if befunde else OK, tuple(befunde))
