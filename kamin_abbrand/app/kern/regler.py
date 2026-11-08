"""PI-Regler des Abbrands: I-Integration, Anti-Windup, I-Start-Rampe."""
from __future__ import annotations

from kern.signale import Werte, runde
from kern.typen import ABBRAND, TICK, Eingaben, Parameter


def klappe_minimum(w: Werte, sp: float) -> float:
    """Dynamisches Minimum: während der Entgasung gilt das höhere der beiden."""
    return max(w.kmin, w.kmin_ent) if sp < w.ent_s else w.kmin


def i_start(t_vorher: float, p: Parameter) -> float:
    """Rampe beim Eintritt in Abbrand, aus phase_setzen.

    t_vorher ist t_phasenstart zu Beginn des Scripts, also die Temperatur beim
    Eintritt ins Anheizen. Bis T kalt gilt I-Start kalt, ab T Abkühlen I-Start heiß.
    """
    ik = p.regler_i_start_kalt
    ih = p.regler_i_start_heiss
    tk = p.t_kalt
    ta = p.t_abkuehlen
    f = min(max((t_vorher - tk) / max(ta - tk, 1), 0), 1)
    return runde(min(max(ik + (ih - ik) * f, p.klappe_min_abbrand),
                     p.klappe_max_abbrand), 1)


def integration_aktiv(e: Eingaben, w: Werte, phase_frisch: str) -> bool:
    return (e.ausloeser == TICK
            and phase_frisch == ABBRAND
            and not e.handbetrieb
            and w.t_ok and w.twt <= w.twmax - 5 and w.t <= w.uebert)


def i_integrieren(i: float, w: Werte, sp: float, takt_s: float, gerundet: bool = True) -> float:
    """Neuer I-Anteil. i und sp sind die dann aktuellen Werte, nicht die vom Laufbeginn.

    gerundet=False für den Regelstand neu: Live verschluckt round(1) Schritte unter 0,05 %,
    der I-Anteil bleibt dort bei |e| unter 1,4 K stehen.
    """
    km = klappe_minimum(w, sp)
    e = w.ziel - w.t
    u = i + w.kp * e - w.kd * w.g
    if (u > w.kmax and e > 0) or (u < km and e < 0):
        return runde(i, 1) if gerundet else i
    neu = min(max(i + w.kp * e * takt_s / (w.tn * 60), km), w.kmax)
    return runde(neu, 1) if gerundet else neu
