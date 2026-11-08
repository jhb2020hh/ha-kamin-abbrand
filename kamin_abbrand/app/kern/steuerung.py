"""Ein Lauf der Phasensteuerung, in der Reihenfolge der Automation."""
from __future__ import annotations

from dataclasses import dataclass, replace

from kern import klappe, phasen, regler
from kern.phasen import Lauf, Regel
from kern.signale import signal_bewerten, sp_phase, t_gueltig, werte_ermitteln
from kern.typen import NEU, PARITAET, SIGNAL_FRISCH, Eingaben, Ergebnis, Parameter, Zustand

MAX_DT_S = 30   # längere Lücken (Neustart, Verbindung weg) zählen nicht als Regelzeit


@dataclass(frozen=True)
class Profil:
    """Was je Regelstand abweichen darf."""
    regeln: tuple[Regel, ...]
    i_takt_s: float
    neu: bool = False


def _profil(regelstand: str, takt_s: float) -> Profil:
    if regelstand == PARITAET:
        # takt_s wird hier ignoriert: Die Live-Automation rechnet fest mit 30 s.
        return Profil(regeln=phasen.REGELN_PARITAET, i_takt_s=30)
    if regelstand == NEU:
        return Profil(regeln=phasen.REGELN_NEU, i_takt_s=takt_s, neu=True)
    raise NotImplementedError(f"Regelstand {regelstand!r} ist nicht umgesetzt")


class Steuerung:
    def __init__(self, parameter: Parameter, zustand: Zustand,
                 regelstand: str = PARITAET, takt_s: float = 30):
        self.parameter = parameter
        self.zustand = zustand
        self.regelstand = regelstand
        self.takt_s = takt_s
        self._profil = _profil(regelstand, takt_s)

    def _vorbereiten(self, e: Eingaben) -> tuple[float, str]:
        """Nur Regelstand neu: vergangene Zeit, Gedächtnis und Signalzustand."""
        z = self.zustand
        dt = self.takt_s if z.letzter_lauf is None else min(max(e.jetzt - z.letzter_lauf, 0), MAX_DT_S)
        z.letzter_lauf = e.jetzt
        if e.tuer_offen:
            z.letzte_tuer = e.jetzt
        if t_gueltig(e.t):
            z.t_tief = e.t if z.t_tief is None else min(z.t_tief, e.t)
        return dt, signal_bewerten(e, self.parameter, z.phase)

    def schritt(self, e: Eingaben) -> Ergebnis:
        """Ändert self.zustand; Ergebnis.zustand ist eine Momentaufnahme davon."""
        z, profil = self.zustand, self._profil
        dt, signal = self._vorbereiten(e) if profil.neu else (profil.i_takt_s, SIGNAL_FRISCH)
        w = werte_ermitteln(e, self.parameter, z)
        lauf = Lauf(e=e, p=self.parameter, z=z, w=w, neu=profil.neu, signal=signal)

        phasen.regeln_anwenden(profil.regeln, lauf)
        if not lauf.gestoppt:
            phasen.maximum_nachfuehren(lauf)
            phasen.wasserbegrenzung(lauf)
            if regler.integration_aktiv(e, w, z.phase):
                z.i_anteil = regler.i_integrieren(
                    z.i_anteil, w, sp_phase(e.jetzt, z.phase_seit), dt, gerundet=not profil.neu)
            soll = (klappe.soll_neu(e, z, w, dt) if profil.neu
                    else klappe.soll_berechnen(e, z, w))
            klappe.soll_schreiben(z, soll)

        return Ergebnis(zustand=replace(z), klappe=int(z.klappe_soll), regel=lauf.regel,
                        uebergang=lauf.uebergang, meldungen=lauf.meldungen,
                        aktionen=lauf.aktionen, signal=signal)
