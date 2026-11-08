"""Abgeleitete Größen eines Laufs: die top-level `variables` der Automation.

`Werte` wird EINMAL zu Laufbeginn gebildet und ändert sich danach nicht, genau wie
die Variablen in HA. Wer den DANN aktuellen Helferwert braucht (states() in einer
späteren Aktion), liest ihn aus dem Zustand. Die Feldnamen sind die Variablennamen
der Automation, damit Port und Template nebeneinander lesbar bleiben.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from kern.typen import (ABBRAND, ANHEIZEN, AUSBRAND, SIGNAL_FRISCH, SIGNAL_STILL, SIGNAL_STUMM,
                        Eingaben, Parameter, Zustand)

T_DECKEL = 1200     # Obergrenze des Helfers t_maximum / t_phasenstart
TWT_MIN = 0
TWT_MAX = 120


def runde(wert: float, stellen: int = 0):
    """`| round(n)` wie HA forgiving_round: Python-round, Halbe also auf gerade.

    Gegen das echte HA geprüft (06.11.2026): 2.5 → 2, 62.5 → 62, 0.35 → 0.3.
    Bei stellen=0 kommt eine Ganzzahl zurück ("230", nicht "230.0").
    """
    ergebnis = round(float(wert), stellen)
    return int(ergebnis) if stellen == 0 else ergebnis


def t_gueltig(t: Optional[float]) -> bool:
    """`is_number(t_raw)`: None, nan und inf sind keine Zahlen."""
    return t is not None and math.isfinite(t)


def t_oder_null(t: Optional[float]) -> float:
    """`t_raw | float(0)`."""
    return t if t_gueltig(t) else 0


def twt_plausibel(roh: Optional[float]) -> float:
    """T.WT: nur 0..120 °C gilt, alles andere wird 0 (Fühler offen meldet 1638 °C)."""
    if roh is not None and TWT_MIN <= roh <= TWT_MAX:
        return roh
    return 0


def s_phase(jetzt: float, phase_seit: float) -> int:
    """Variable `s`: ganze Sekunden, auf null hin abgeschnitten wie `| int`."""
    return int(jetzt - phase_seit)


def sp_phase(jetzt: float, phase_seit: float) -> float:
    """Template-Größe `sp`: ungerundet, mit dem dann aktuellen phase_seit."""
    return jetzt - phase_seit


# Signalbewertung im Regelstand neu. Die Grenzen sind bewusst Konstanten: Im Parallelbetrieb
# zeigt sich, ob sie passen, erst danach lohnt ein Helfer.
CAN_STUMM_S = 60       # kein Messframe, nach dem Flashen messbar
CAN_FRISCH_S = 30
FEUER_STUMM_S = 120    # bei Feuer ändert sich der Druck alle paar Sekunden
FRISCH_S = 60


def signal_bewerten(e: Eingaben, p: Parameter, phase: str) -> str:
    """Stillstand des Werts von Funkstille trennen.

    Mit CAN-Alter ist das eindeutig. Ohne zählt die jüngste Änderung von Abgas oder Druck.
    Nur bei Feuer ist die kurze Grenze zulässig: In Ruhe stand der Druck schon 14 min still.
    """
    if e.can_alter_s is not None:
        if e.can_alter_s > CAN_STUMM_S:
            return SIGNAL_STUMM
        return SIGNAL_FRISCH if e.can_alter_s <= CAN_FRISCH_S else SIGNAL_STILL
    alter = e.t_alter_s if e.dp_alter_s is None else min(e.t_alter_s, e.dp_alter_s)
    feuer = phase in (ANHEIZEN, ABBRAND, AUSBRAND) and e.dp_alter_s is not None
    if alter > (FEUER_STUMM_S if feuer else p.fuehler_timeout):
        return SIGNAL_STUMM
    return SIGNAL_FRISCH if alter < FRISCH_S else SIGNAL_STILL


def anh_min_eff(tref: float, glut: float, abk: float, p: Parameter) -> float:
    if tref >= glut:
        return p.anheizen_mindestdauer_feuer * 60
    if tref >= abk:
        return p.anheizen_mindestdauer_nachlegen * 60
    return p.anheizen_mindestdauer * 60


def ziel_min_s(tref: float, tstart: float, abk: float) -> int:
    if tref >= tstart:
        return 120
    if tref >= abk:
        return 180
    return 300


@dataclass(frozen=True)
class Werte:
    t_ok: bool
    t: float
    g: float
    tmax3: float
    tuer_offen: Optional[bool]
    phase: str
    s: int
    kalt: float
    tstart: float
    ziel: float
    glut: float
    abk: float
    uebert: float
    gstart: float
    abfall: float
    tmax: float
    anh_to: float
    fenster: float
    glut_max: float
    wart_to: float
    kmin: float
    kmax: float
    kglut: float
    kwart: float
    kman: float
    kp: float
    kuebert: float
    abk_max: float
    tref: float
    anstieg: float
    anh_min_eff: float
    twt: float
    twmax: float
    kwasser: float
    puffer_u: float
    puffer_voll: float
    tn: float
    kd: float
    ziel_min_s: int
    kmin_ent: float
    ent_s: float
    krate: float
    t_alter: int
    fuehler_to: float
    twt_anstieg: float
    feuer_anstieg: float
    kfeuer: float


def werte_ermitteln(e: Eingaben, p: Parameter, z: Zustand) -> Werte:
    t = t_oder_null(e.t)
    tref = z.t_phasenstart
    return Werte(
        t_ok=t_gueltig(e.t),
        t=t,
        g=e.g,
        tmax3=t if e.tmax3 is None else e.tmax3,
        tuer_offen=e.tuer_offen,
        phase=z.phase,
        s=s_phase(e.jetzt, z.phase_seit),
        kalt=p.t_kalt,
        tstart=p.t_abbrandstart,
        ziel=p.t_ziel,
        glut=p.t_glut,
        abk=p.t_abkuehlen,
        uebert=p.t_uebertemperatur,
        gstart=p.gradient_start,
        abfall=p.ausbrand_abfall,
        tmax=z.t_maximum,
        anh_to=p.anheizen_timeout * 60,
        fenster=p.startfenster * 60,
        glut_max=p.glut_max_dauer * 60,
        wart_to=p.wartung_timeout * 60,
        kmin=p.klappe_min_abbrand,
        kmax=p.klappe_max_abbrand,
        kglut=p.klappe_glut,
        kwart=p.klappe_wartung,
        kman=p.klappe_manuell,
        kp=p.regler_verstaerkung,
        kuebert=p.klappe_uebertemperatur,
        abk_max=p.abkuehlen_max_dauer * 60,
        tref=tref,
        anstieg=p.startanstieg,
        anh_min_eff=anh_min_eff(tref, p.t_glut, p.t_abkuehlen, p),
        twt=twt_plausibel(e.twt_roh),
        twmax=p.t_wasser_max,
        kwasser=p.klappe_wasser,
        puffer_u=e.puffer_unten,
        puffer_voll=p.puffer_voll,
        tn=max(p.regler_nachstellzeit, 1),
        kd=p.regler_vorhaltung,
        ziel_min_s=ziel_min_s(tref, p.t_abbrandstart, p.t_abkuehlen),
        kmin_ent=p.klappe_min_entgasung,
        ent_s=p.entgasung_dauer * 60,
        krate=max(p.klappe_schliessrate, 1),
        t_alter=int(e.t_alter_s),
        fuehler_to=p.fuehler_timeout,
        twt_anstieg=e.twt_anstieg,
        feuer_anstieg=p.feuer_twt_anstieg,
        kfeuer=p.klappe_anheizen_feuer,
    )
