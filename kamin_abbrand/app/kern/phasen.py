"""Phasenregeln: der `choose`-Block der Automation und das Script phase_setzen.

Die Regeln stehen in der Reihenfolge der Automation, die erste passende gewinnt.
Bedingungen sehen nur die Variablen vom Laufbeginn; das reicht, weil sich vor der
Auswahl kein Helfer ändert.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from kern.regler import i_start
from kern.signale import T_DECKEL, Werte, runde, t_oder_null
from kern.typen import (ABBRAND, ABKUEHLEN, AKTION_SNIFFER_RESTART, AKTION_WARTUNG_AUS,
                        ANHEIZEN, ANHEIZFEHLER, AUSBRAND, GLUT, SENSOR_WEG, SIGNAL_FRISCH,
                        SIGNAL_STUMM, STANDBY, STOERUNG, TUER_OFFEN, WARTUNG, WARTUNG_AN, Eingaben, Meldung,
                        Parameter, Zustand)

TITEL = "🔥 Kamin"
TITEL_STOERUNG = "🔥 Kamin – Störung"
TITEL_ANHEIZFEHLER = "🔥 Kamin – Anheizfehler"


@dataclass
class Lauf:
    """Alles, was ein Lauf liest und sammelt."""
    e: Eingaben
    p: Parameter
    z: Zustand
    w: Werte
    regel: Optional[str] = None
    uebergang: Optional[tuple[str, str]] = None
    meldungen: list[Meldung] = field(default_factory=list)
    aktionen: list[str] = field(default_factory=list)
    gestoppt: bool = False
    neu: bool = False                   # Regelstand neu
    signal: str = SIGNAL_FRISCH

    def push(self, titel: str, text: str) -> None:
        self.meldungen.append(Meldung(titel, text))


@dataclass(frozen=True)
class Regel:
    alias: str
    bedingung: Callable[[Lauf], bool]
    aktion: Callable[[Lauf], None]


def phase_setzen(lauf: Lauf, phase: str) -> None:
    """Script kamin_abbrand_phase_setzen. alt und t_vorher gelten zu seinem Start."""
    z, e = lauf.z, lauf.e
    alt = z.phase
    t_vorher = z.t_phasenstart
    t_jetzt = min(t_oder_null(e.t), T_DECKEL)

    z.phase = phase
    z.phase_seit = float(int(e.jetzt))
    z.t_tief = t_jetzt
    # Live schreibt ohne Messwert 0 und verliert damit die Herkunftstemperatur.
    if not (lauf.neu and not lauf.w.t_ok):
        z.t_maximum = t_jetzt
        # Türöffnen ist kein Nachlegen: Startfall = T vor dem ersten Öffnen.
        if (alt, phase) in ((TUER_OFFEN, ANHEIZEN), (ANHEIZEN, TUER_OFFEN)):
            z.t_phasenstart = t_vorher
        elif alt == STANDBY and phase == ANHEIZEN:
            z.t_phasenstart = min(t_jetzt, t_vorher)
        else:
            z.t_phasenstart = t_jetzt
    if phase == ABBRAND and alt != ABBRAND:
        z.i_anteil = i_start(t_vorher, lauf.p)
    lauf.uebergang = (alt, phase)


def regeln_anwenden(regeln: tuple[Regel, ...], lauf: Lauf) -> None:
    """Ein Übergang je Lauf: die erste Regel mit erfüllter Bedingung, dann Schluss."""
    for regel in regeln:
        if regel.bedingung(lauf):
            lauf.regel = regel.alias
            regel.aktion(lauf)
            return


def maximum_nachfuehren(lauf: Lauf) -> None:
    """Prüft die neue Phase und das dann aktuelle t_maximum."""
    z, w = lauf.z, lauf.w
    if z.phase in (ANHEIZEN, ABBRAND) and w.t_ok and w.t > z.t_maximum:
        z.t_maximum = min(w.t, T_DECKEL)


def wasserbegrenzung(lauf: Lauf) -> None:
    """Prüft die neue Phase, vergleicht aber mit der alten Variable tmax."""
    z, w = lauf.z, lauf.w
    if z.phase == ABBRAND and w.t_ok and w.twt > w.twmax - 5 and abs(w.t - w.tmax) >= 1:
        z.t_maximum = min(w.t, T_DECKEL)


# ---------- Aktionen ----------

def _nach(phase: str) -> Callable[[Lauf], None]:
    return lambda lauf: phase_setzen(lauf, phase)


def _stoerung_fuehler(lauf: Lauf) -> None:
    phase_setzen(lauf, STOERUNG)
    lauf.push(TITEL_STOERUNG, "Kein Messwert vom LEDA LUC seit 60 s. Zuluftklappe wird geöffnet.")


def _wartung_ablehnen(lauf: Lauf) -> None:
    lauf.aktionen.append(AKTION_WARTUNG_AUS)
    lauf.push(TITEL, f"Wartung abgelehnt: Ofen ist noch warm ({runde(lauf.w.t)} °C). "
                     "Reinigung nur bei kaltem Ofen.")
    lauf.gestoppt = True


def _feuer_in_wartung(lauf: Lauf) -> None:
    lauf.aktionen.append(AKTION_WARTUNG_AUS)
    phase_setzen(lauf, ANHEIZEN)
    lauf.push(TITEL, "Feuer erkannt – Wartung beendet, Klappe offen (Anheizen).")


def _wartung_timeout(lauf: Lauf) -> None:
    lauf.aktionen.append(AKTION_WARTUNG_AUS)
    phase_setzen(lauf, STANDBY)
    lauf.push(TITEL, "Wartungsmodus nach Timeout automatisch beendet.")


def _wartung_beendet(lauf: Lauf) -> None:
    w = lauf.w
    phase_setzen(lauf, ANHEIZEN if (w.t_ok and w.t > w.kalt) else STANDBY)


def _stopp(lauf: Lauf) -> None:
    lauf.gestoppt = True


def _stoerung_pruefen(lauf: Lauf) -> None:
    w = lauf.w
    if w.t_alter < 60:
        phase_setzen(lauf, ANHEIZEN if w.t > w.kalt else STANDBY)
        lauf.push(TITEL, f"LUC-Messwerte wieder da ({runde(w.t)} °C).")


def _eingefroren(lauf: Lauf) -> None:
    w = lauf.w
    phase_setzen(lauf, STOERUNG)
    lauf.push(TITEL_STOERUNG,
              f"LUC-Messwert seit {runde(w.t_alter / 60)} min unverändert ({runde(w.t)} °C), "
              f"T.WT {runde(w.twt, 1)} °C ({runde(w.twt_anstieg, 1)} K in 10 min). "
              "Zuluftklappe wird geöffnet, CAN-Sniffer wird neu gestartet.")
    lauf.aktionen.append(AKTION_SNIFFER_RESTART)


def _ohne_feuer_zurueck(lauf: Lauf) -> None:
    w = lauf.w
    if w.tref < w.kalt and w.t < w.kalt:
        ziel = STANDBY
    elif w.t >= w.glut:
        ziel = AUSBRAND
    elif w.t >= w.abk:
        ziel = GLUT
    else:
        ziel = ABKUEHLEN
    phase_setzen(lauf, ziel)


def _anheiz_timeout(lauf: Lauf) -> None:
    w = lauf.w
    phase_setzen(lauf, ANHEIZFEHLER)
    lauf.push(TITEL_ANHEIZFEHLER,
              f"Abbrandtemperatur ({runde(w.tstart)} °C) nicht erreicht, "
              f"aktuell {runde(w.t)} °C. Feuer prüfen.")


def _zusammengefallen(lauf: Lauf) -> None:
    w = lauf.w
    phase_setzen(lauf, ANHEIZFEHLER)
    lauf.push(TITEL_ANHEIZFEHLER,
              f"Feuer nach {runde(w.s / 60)} min zusammengefallen ({runde(w.t)} °C) – "
              "die Scheite brennen nicht richtig (zu feucht/zu dick?). "
              "Klappe offen, Feuer prüfen.")


def _nach_glut(lauf: Lauf) -> None:
    w = lauf.w
    phase_setzen(lauf, GLUT)
    if w.puffer_u >= w.puffer_voll:
        text = (f"🪵 Glutphase ({runde(w.t)} °C) – Puffer fast voll "
                f"(unten {runde(w.puffer_u)} °C): besser NICHT nachlegen, "
                "die Wärme kann nicht mehr abgegeben werden.")
    else:
        text = (f"🪵 Glutphase ({runde(w.t)} °C) – jetzt nachlegen, falls gewünscht: "
                "2 Scheite quer, zusammen ca. 1,9 kg (Arena Aqua 2.0, Nennleistung).")
    lauf.push(TITEL, text)


def _nach_ausbrand(lauf: Lauf) -> None:
    w = lauf.w
    phase_setzen(lauf, AUSBRAND)
    lauf.z.t_maximum = min(w.tmax, w.ziel)


# ---------- Regeln ----------

def _in(lauf: Lauf, phase: str) -> bool:
    return lauf.w.phase == phase


def _kein_feuer(lauf: Lauf) -> bool:
    w = lauf.w
    return (_in(lauf, ANHEIZEN) and w.s >= w.fenster and w.t < w.tstart and w.g < w.gstart
            and w.twt_anstieg < w.feuer_anstieg
            and (w.tmax < w.tref + w.anstieg
                 or (w.tref >= w.kalt and w.tmax3 <= w.tmax - w.abfall / 2)))


def _eingefroren_erkannt(lauf: Lauf) -> bool:
    w = lauf.w
    return (w.phase not in (WARTUNG, STOERUNG) and w.t_ok and w.t_alter > w.fuehler_to
            and (w.phase in (ABBRAND, AUSBRAND) or w.twt_anstieg >= w.feuer_anstieg))


REGELN_PARITAET: tuple[Regel, ...] = (
    Regel("Fühler/LUC ausgefallen → Störung",
          lambda l: l.e.ausloeser == SENSOR_WEG and not l.e.wartung,
          _stoerung_fuehler),
    Regel("Wartung bei heißem Ofen abgelehnt",
          lambda l: l.e.ausloeser == WARTUNG_AN and l.w.t_ok and l.w.t > l.w.kalt,
          _wartung_ablehnen),
    Regel("Wartung beginnen",
          lambda l: l.e.wartung and not _in(l, WARTUNG),
          _nach(WARTUNG)),
    Regel("Feuer trotz Wartung → Wartung beenden, Anheizen",
          lambda l: (l.e.wartung and _in(l, WARTUNG) and l.w.t_ok
                     and (l.w.t > l.w.kalt or l.w.g >= l.w.gstart)),
          _feuer_in_wartung),
    Regel("Wartung Timeout",
          lambda l: l.e.wartung and _in(l, WARTUNG) and l.w.s >= l.w.wart_to,
          _wartung_timeout),
    Regel("Wartung beendet",
          lambda l: not l.e.wartung and _in(l, WARTUNG),
          _wartung_beendet),
    Regel("Kein Messwert → Phase halten",
          lambda l: not l.w.t_ok,
          _stopp),
    Regel("Störung: erst bei frischem Messwert beenden, sonst halten",
          lambda l: _in(l, STOERUNG),
          _stoerung_pruefen),
    Regel("Messwert eingefroren → Störung",
          _eingefroren_erkannt,
          _eingefroren),
    Regel("Tür auf → Klappe auf",
          lambda l: l.w.tuer_offen and l.w.phase not in (TUER_OFFEN, WARTUNG),
          _nach(TUER_OFFEN)),
    Regel("Standby → Anheizen (Temperaturanstieg)",
          lambda l: _in(l, STANDBY) and (l.w.g >= l.w.gstart or l.w.t >= l.w.glut),
          _nach(ANHEIZEN)),
    Regel("Tür zu → Anheizen",
          lambda l: _in(l, TUER_OFFEN) and l.w.tuer_offen is False,
          _nach(ANHEIZEN)),
    Regel("Anheizen → Abbrand",
          lambda l: (_in(l, ANHEIZEN) and l.w.t >= l.w.tstart
                     and (l.w.s >= l.w.anh_min_eff
                          or (l.w.t >= l.w.ziel and l.w.s >= l.w.ziel_min_s))
                     # Kaltstart hält Anheizen bis T Kaltstart-Ende, Notbremse Timeout
                     and (l.w.tref >= l.w.abk or l.w.t >= l.w.t_kalt_ende
                          or l.w.s >= l.w.anh_to)),
          _nach(ABBRAND)),
    Regel("Anheizen ohne echtes Feuer (Tür nur geöffnet / Glut nur aufgeflammt / nur gereinigt)"
          " → still zurück, kalter Ofen direkt Standby",
          _kein_feuer,
          _ohne_feuer_zurueck),
    Regel("Anheizen Timeout → Anheizfehler",
          lambda l: _in(l, ANHEIZEN) and l.w.s >= l.w.anh_to and l.w.t < l.w.tstart,
          _anheiz_timeout),
    Regel("Abbrand bricht nach < 15 min zusammen → Anheizfehler",
          lambda l: _in(l, ABBRAND) and l.w.t < l.w.glut and l.w.s < 900,
          _zusammengefallen),
    Regel("Abbrand → Glut (Temperatur unter Glutschwelle)",
          lambda l: _in(l, ABBRAND) and l.w.t < l.w.glut and l.w.twt <= l.w.twmax - 5,
          _nach_glut),
    Regel("Abbrand → Ausbrand (nach ≥ 15 min, 3 min ≥ Abfall unter min(Maximum, Ziel))",
          lambda l: (_in(l, ABBRAND) and l.w.s >= 900
                     and l.w.tmax3 <= min(l.w.tmax, l.w.ziel) - l.w.abfall
                     and l.w.twt <= l.w.twmax - 5),
          _nach_ausbrand),
    Regel("Ausbrand → Anheizen (Nachlegen erkannt)",
          lambda l: _in(l, AUSBRAND) and l.w.g >= l.w.gstart,
          _nach(ANHEIZEN)),
    Regel("Ausbrand → Glut",
          lambda l: _in(l, AUSBRAND) and l.w.t < l.w.glut and l.w.twt <= l.w.twmax - 5,
          _nach_glut),
    Regel("Glut → Anheizen (Nachlegen erkannt)",
          lambda l: _in(l, GLUT) and l.w.g >= l.w.gstart,
          _nach(ANHEIZEN)),
    Regel("Glut → Abkühlen",
          lambda l: _in(l, GLUT) and (l.w.t < l.w.abk or l.w.s >= l.w.glut_max),
          _nach(ABKUEHLEN)),
    Regel("Abkühlen → Anheizen (Nachlegen erkannt)",
          lambda l: (_in(l, ABKUEHLEN) and l.w.s >= 180 and l.w.g >= l.w.gstart
                     and l.w.t > l.w.kalt),
          _nach(ANHEIZEN)),
    Regel("Abkühlen → Standby (kalt oder Höchstdauer)",
          lambda l: (_in(l, ABKUEHLEN)
                     and ((l.w.s >= 1260 and l.w.t < l.w.abk) or l.w.s >= l.w.abk_max)),
          _nach(STANDBY)),
    Regel("Anheizfehler → Anheizen (Temperaturanstieg)",
          lambda l: _in(l, ANHEIZFEHLER) and l.w.g >= l.w.gstart,
          _nach(ANHEIZEN)),
    Regel("Anheizfehler → Standby (nach 90 min)",
          lambda l: _in(l, ANHEIZFEHLER) and l.w.s >= 5400,
          _nach(STANDBY)),
)


# ---------- Regelstand neu: dieselbe Liste mit gezielten Abweichungen ----------

# Phantom-Auflagen 01.11. (173 → 188 °C) und 02.11. (191 → 204 °C): Flammen flackerten im
# Ausbrand auf, Tür blieb zu, Gradient ≥ 3. Der Anstieg blieb unter dem Startanstieg von 20 K.
TUER_FENSTER_S = 300


def _nachlegen_belegt(lauf: Lauf) -> bool:
    """Gradient allein reicht nicht: Tür kurz zuvor offen oder deutlicher Anstieg.

    Ohne Türkontakt bleibt es beim Gradienten. Der Anstieg als zweiter Weg stellt sicher,
    dass ein defekter Kontakt echtes Nachlegen nicht in der Glut-Klappe von 10 % erstickt.
    """
    e, z, w = lauf.e, lauf.z, lauf.w
    if not e.tuerkontakt_vorhanden:
        return True
    if z.letzte_tuer is not None and e.jetzt - z.letzte_tuer <= TUER_FENSTER_S:
        return True
    return z.t_tief is not None and w.t - z.t_tief >= w.anstieg


def _stumm_erkannt(lauf: Lauf) -> bool:
    w = lauf.w
    return (w.phase not in (WARTUNG, STOERUNG) and w.t_ok and lauf.signal == SIGNAL_STUMM
            and (w.phase in (ANHEIZEN, ABBRAND, AUSBRAND) or w.twt_anstieg >= w.feuer_anstieg))


def _stoerung_pruefen_neu(lauf: Lauf) -> None:
    w = lauf.w
    if lauf.signal == SIGNAL_FRISCH:
        phase_setzen(lauf, ANHEIZEN if w.t > w.kalt else STANDBY)
        lauf.push(TITEL, f"LUC-Messwerte wieder da ({runde(w.t)} °C).")


def _regeln_neu() -> tuple[Regel, ...]:
    ersetzt = {
        "Störung: erst bei frischem Messwert beenden, sonst halten":
            lambda r: Regel(r.alias, r.bedingung, _stoerung_pruefen_neu),
        "Messwert eingefroren → Störung":
            lambda r: Regel(r.alias, _stumm_erkannt, r.aktion),
        "Ausbrand → Anheizen (Nachlegen erkannt)":
            lambda r: Regel(r.alias, lambda l: r.bedingung(l) and _nachlegen_belegt(l), r.aktion),
        "Glut → Anheizen (Nachlegen erkannt)":
            lambda r: Regel(r.alias, lambda l: r.bedingung(l) and _nachlegen_belegt(l), r.aktion),
    }
    regeln = [ersetzt[r.alias](r) if r.alias in ersetzt else r for r in REGELN_PARITAET]
    regeln = [Regel(r.alias, lambda l, r=r: r.bedingung(l) and l.signal != SIGNAL_STUMM, r.aktion)
              if r.bedingung is _kein_feuer else r for r in regeln]
    # Die Tür muss auch ohne Messwert wirken: Klappe auf, wenn jemand die Tür öffnet.
    tuer = next(r for r in regeln if r.alias == "Tür auf → Klappe auf")
    regeln.remove(tuer)
    stopp = next(i for i, r in enumerate(regeln) if r.alias == "Kein Messwert → Phase halten")
    regeln.insert(stopp, tuer)
    return tuple(regeln)


REGELN_NEU = _regeln_neu()
