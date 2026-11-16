"""Ein Lauf der Phasensteuerung, in der Reihenfolge der Automation."""
from __future__ import annotations

from dataclasses import dataclass, replace

from kern import klappe, phasen, regler, schwingung
from kern.phasen import Lauf, Regel
from kern.signale import nachlauf_aktiv, signal_bewerten, sp_phase, t_gueltig, werte_ermitteln
from kern.typen import (ABBRAND, AKTION_BRENNSTOFF_HOLZ, ANHEIZEN, ANHEIZFEHLER, AUSBRAND, BRIKETTS, HOLZ, NEU,
                        PARITAET, SIGNAL_FRISCH, STANDBY, TICK, TUER_OFFEN, Eingaben, Ergebnis, Meldung, Parameter,
                        Zustand)

MAX_DT_S = 30   # längere Lücken (Neustart, Verbindung weg) zählen nicht als Regelzeit


@dataclass(frozen=True)
class Profil:
    """Was je Regelstand abweichen darf."""
    regeln: tuple[Regel, ...]
    i_takt_s: float
    neu: bool = False


# In diesen Phasen brennen Briketts oder schwelen auf der Glut: Der Nachlauf zählt von dort an neu.
BRIKETT_FEUER = (TUER_OFFEN, ANHEIZEN, ABBRAND, AUSBRAND, ANHEIZFEHLER)
TITEL = "🔥 Kamin"


def brikett_parameter(p: Parameter) -> Parameter:
    """Der Parametersatz für Briketts: die Holzwerte, ersetzt durch die Brikett-Felder."""
    return replace(p, startfenster=p.startfenster_brikett, anheizen_timeout=p.anheizen_timeout_brikett,
                   klappe_min_abbrand=p.klappe_min_abbrand_brikett, klappe_glut=p.klappe_glut_brikett,
                   glut_max_dauer=p.glut_max_dauer_brikett, abkuehlen_max_dauer=p.abkuehlen_max_dauer_brikett)


def brikett_nachfuehren(e: Eingaben, z: Zustand, p: Parameter) -> tuple[list[str], list[Meldung]]:
    """Nach den Regeln: Nachlauf verlängern, beim Zurückstellen auf Holz halten, am Ende freigeben.

    Der Zeitstempel wird nie verkürzt. Nur Regelstand neu.
    """
    aktionen: list[str] = []
    meldungen: list[Meldung] = []
    vorher, z.brennstoff = z.brennstoff, e.brennstoff
    warm = t_gueltig(e.t) and e.t >= p.t_abkuehlen
    kalt = t_gueltig(e.t) and e.t < p.t_kalt
    bis = e.jetzt + p.brikett_nachlauf * 60

    def verlaengern() -> None:
        z.brikett_nachlauf_bis = bis if z.brikett_nachlauf_bis is None else max(z.brikett_nachlauf_bis, bis)

    if e.brennstoff == BRIKETTS and (z.phase in (ABBRAND, AUSBRAND) or (z.phase in BRIKETT_FEUER and warm)):
        verlaengern()
    elif vorher == BRIKETTS and e.brennstoff == HOLZ and not (z.phase == STANDBY and kalt):
        # Holz auf die Restglut: Die Kohle darunter glüht weiter.
        verlaengern()
    if (z.phase == STANDBY and kalt and z.brikett_nachlauf_bis is not None
            and e.jetzt >= z.brikett_nachlauf_bis):
        z.brikett_nachlauf_bis = None
        if e.brennstoff == BRIKETTS:
            z.brennstoff = HOLZ
            aktionen.append(AKTION_BRENNSTOFF_HOLZ)
            meldungen.append(Meldung(TITEL, "Brikett-Nachlauf beendet, der Ofen ist kalt. "
                                            "Brennstoff steht wieder auf Holz."))
    return aktionen, meldungen


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

    @staticmethod
    def _strategie_2(e: Eingaben, z: Zustand, p: Parameter, w):
        """Regelstrategie 2.0: Schwingungswächter im Abbrand, Reglerwerte gedämpft, gerichteter Vorhalt."""
        if z.phase == ABBRAND and not e.handbetrieb and w.t_ok:
            if e.ausloeser == TICK:
                schwingung.beobachten(z, w.t, e.jetzt)
        elif z.phase != ABBRAND:
            schwingung.zuruecksetzen(z)
        return schwingung.werte_2(w, p, z)

    def schritt(self, e: Eingaben) -> Ergebnis:
        """Ändert self.zustand; Ergebnis.zustand ist eine Momentaufnahme davon."""
        z, profil = self.zustand, self._profil
        dt, signal = self._vorbereiten(e) if profil.neu else (profil.i_takt_s, SIGNAL_FRISCH)
        brikett = profil.neu and e.brennstoff == BRIKETTS
        p = brikett_parameter(self.parameter) if brikett else self.parameter
        nachlauf = profil.neu and nachlauf_aktiv(e, z, self.parameter, brikett)
        w = werte_ermitteln(e, p, z, brikett=brikett, nachlauf=nachlauf)
        lauf = Lauf(e=e, p=p, z=z, w=w, neu=profil.neu, signal=signal)

        phasen.regeln_anwenden(profil.regeln, lauf)
        if not lauf.gestoppt:
            phasen.maximum_nachfuehren(lauf)
            phasen.wasserbegrenzung(lauf)
            if profil.neu and schwingung.strategie_2(p):
                w = self._strategie_2(e, z, p, w)
            if regler.integration_aktiv(e, w, z.phase):
                z.i_anteil = regler.i_integrieren(
                    z.i_anteil, w, sp_phase(e.jetzt, z.phase_seit), dt, gerundet=not profil.neu)
            soll = (klappe.soll_neu(e, z, w, dt) if profil.neu
                    else klappe.soll_berechnen(e, z, w))
            klappe.soll_schreiben(z, soll)
        if profil.neu:
            aktionen, meldungen = brikett_nachfuehren(e, z, self.parameter)
            lauf.aktionen.extend(aktionen)
            lauf.meldungen.extend(meldungen)

        return Ergebnis(zustand=replace(z), klappe=int(z.klappe_soll), regel=lauf.regel,
                        uebergang=lauf.uebergang, meldungen=lauf.meldungen,
                        aktionen=lauf.aktionen, signal=signal)
