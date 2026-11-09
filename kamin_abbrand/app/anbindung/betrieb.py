"""Bedingungen für den Betrieb und der gesicherte Zustand. Entschieden wird nur hier, auf dem Server."""
from __future__ import annotations

import dataclasses
import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from anbindung.einstellungen import atomar_schreiben
from kern.signale import T_DECKEL
from kern.typen import NEU, PHASEN, Zustand

log = logging.getLogger(__name__)

ZUSTAND_GUELTIG_S = 600.0
NEU_FRISCH_MIN_S = 60.0
_WEG = ("unavailable", "unknown")
_T_UNTEN = -40.0


@dataclass(frozen=True)
class Punkt:
    schluessel: str
    text: str
    hart: bool = True      # Hart wirkt sofort, sonst erst nach der Karenz (siehe main.BEDINGUNG_KARENZ_S)

    def als_dict(self) -> dict:
        return {"schluessel": self.schluessel, "text": self.text}


def offene_punkte(app: Any, jetzt: float) -> list[Punkt]:
    """Leer heißt: Betrieb erlaubt. Liest nur, ändert nichts."""
    s = app.speicher
    if s is None:
        return [Punkt("einstellungen", "Die Einstellungen der App fehlen.")]
    offen: list[Punkt] = []
    rollen = s.rollen
    ein = app.eingaenge

    if not s.anlagenprofil.klappe_motorisiert:
        offen.append(Punkt("motorisiert", "Unter Anlage ist keine motorisierte Klappe eingetragen."))
    klappe = rollen.get("ausgang_klappe")
    if not klappe:
        offen.append(Punkt("klappe", "„Klappe Soll“ ist nicht zugewiesen."))
    elif (eintrag := ein.eintrag(klappe)) is None or eintrag.zustand in _WEG:
        offen.append(Punkt("klappe_erreichbar", "Die Klappe ist in Home Assistant nicht erreichbar.", hart=False))

    if not rollen.get("abgas"):
        offen.append(Punkt("abgas", "Der Abgasfühler ist nicht zugewiesen."))
    elif not abgas_frisch(app, rollen["abgas"], jetzt):
        offen.append(Punkt("abgas_frisch", "Der Abgasfühler liefert keinen frischen, plausiblen Wert.", hart=False))

    if not app.client.verbunden:
        offen.append(Punkt("verbunden", "Keine Verbindung zu Home Assistant.", hart=False))

    if not neu_liefert(app, jetzt):
        offen.append(Punkt("neu", "Der Regelstand „neu“ liefert noch keine Ergebnisse.", hart=False))

    automation = rollen.get("konkurrierende_automation")
    if automation:
        wert = ein.wert(automation)
        if wert != "off":
            offen.append(Punkt("automation", "Die konkurrierende Automation muss aus sein. Abschalten musst du sie selbst.",
                               hart=wert == "on"))

    if not rollen.get("ausgang_heartbeat") and not s.ohne_watchdog_bestaetigt:
        offen.append(Punkt("watchdog", "Watchdog zuweisen oder bestätigen, dass die Klappe bei einem Ausfall nicht schließen kann."))
    if not s.bestaetigt:
        offen.append(Punkt("bestaetigung", "Bestätigung fehlt: CO-Melder, Hersteller oder Schornsteinfeger, eigene Verantwortung."))
    return offen


def abgas_frisch(app: Any, eid: str, jetzt: float) -> bool:
    eintrag = app.eingaenge.eintrag(eid)
    t = app.eingaenge.temperatur_c(eid)
    if eintrag is None or t is None or not _T_UNTEN <= t <= T_DECKEL:
        return False
    return jetzt - eintrag.gemeldet <= app.parameter_neu.fuehler_timeout


def neu_liefert(app: Any, jetzt: float) -> bool:
    stand = app.letzte.get(NEU)
    if NEU not in app.steuerungen or NEU in app._fehler or stand is None:
        return False
    return jetzt - stand[1] <= max(NEU_FRISCH_MIN_S, 3 * app._takte[NEU])


# ---- Gesicherter Zustand ----------------------------------------------------

_PFLICHTFELDER = {"phase", "phase_seit", "t_maximum", "t_phasenstart", "i_anteil", "klappe_soll"}
_FELDER = {f.name for f in dataclasses.fields(Zustand)}


def zustand_sichern(pfad: Path, zustand: Zustand, jetzt: float) -> None:
    """Atomar, ein Plattenfehler wird nur geloggt."""
    try:
        atomar_schreiben(pfad, {"zeit": jetzt, "zustand": dataclasses.asdict(zustand)})
    except OSError as fehler:
        log.error("Zustand nicht gesichert: %r", fehler)


def zustand_laden(pfad: Path, jetzt: float, gueltig_s: float = ZUSTAND_GUELTIG_S) -> Optional[Zustand]:
    """Nur ein vollständiger, plausibler und junger Zustand gilt, sonst None."""
    try:
        with open(pfad, encoding="utf-8") as datei:
            daten = json.load(datei)
        zeit = float(daten["zeit"])
        roh = daten["zustand"]
        if not isinstance(roh, dict) or not _PFLICHTFELDER <= set(roh) or roh["phase"] not in PHASEN:
            return None
        if not math.isfinite(zeit) or not 0 <= jetzt - zeit <= gueltig_s:
            return None
        zustand = Zustand(**{k: v for k, v in roh.items() if k in _FELDER})
        zahlen = [zustand.phase_seit, zustand.t_maximum, zustand.t_phasenstart, zustand.i_anteil, zustand.klappe_soll]
        if not all(isinstance(z, (int, float)) and not isinstance(z, bool) and math.isfinite(z) for z in zahlen):
            return None
        if not 0 <= zustand.klappe_soll <= 100:
            return None
        return zustand
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as fehler:
        log.warning("Gesicherter Zustand nicht lesbar: %r", fehler)
        return None
