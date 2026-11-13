"""Buchführung der Kosten: Auflagen, Zähler und Tageszeilen in /config/wirtschaft.json.

Die Rechnung steht in kern/wirtschaft.py. Hier wird gezählt und gespeichert:

- Eine Auflage ist der Übergang Anheizen → Abbrand des führenden Regelstands, mit Türkontakt nur
  nach offener Tür (siehe main.py).
- Zähler werden bei jedem Takt gelesen. Fällt ein Stand, beginnt ein neuer Bezug ohne Buchung.
  Fehlt der Wert (unavailable), wird der Takt übersprungen. Kommt er am selben Tag wieder, wird
  der Zuwachs gebucht: Der Zähler hat ihn gezählt. Liegt Mitternacht in einer Lücke von mehr als
  einer Stunde, beginnt ein neuer Bezug, denn der Zuwachs lässt sich keinem Tag zuordnen.
- Der Wärmezähler gilt für einen Tag nur als Messung, wenn er ihn ganz gesehen hat. Fehlt ein Stück
  (`waerme_luecke`) oder war er zu lange nicht lesbar (`waerme_s`), wird die Wärme aus dem Holz geschätzt.
- Geld wird zum Preis des Tages gebucht. Spätere Preisänderungen berühren abgeschlossene Tage nie.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Optional

from anbindung.anlagenprofil import Anlagenprofil
from anbindung.eingaenge import als_zahl
from anbindung.einstellungen import EinstellungsFehler, atomar_schreiben
from kern import wirtschaft

log = logging.getLogger(__name__)

SPEICHER_INTERVALL_S = 300.0
MAX_TAKT_S = 120.0          # Längere Abstände zählen nicht als erfasste Zeit
MAX_LUECKE_S = 3600.0       # Über Mitternacht hinweg beginnt ein Zähler danach neu
VERSION = 1

# name: (minimum, maximum, Titel). Leer lassen ist erlaubt und heißt „nicht angegeben“.
EINSTELLUNGEN: dict[str, tuple[float, float, str]] = {
    "investition": (0.0, 10_000_000.0, "Investition"),
    "laufende_kosten_jahr": (0.0, 1_000_000.0, "Laufende Kosten je Jahr"),
    "jahreswaerme_kwh": (0.0, 1_000_000.0, "Erwartete Kaminwärme je Jahr"),
    "referenz_a": (0.0, 100_000.0, "Referenz a"),
    "referenz_b": (0.0, 10_000.0, "Referenz b"),
    "referenz_sockel": (0.0, 100_000.0, "Referenz Sockel"),
    "raum_wasser_verhaeltnis": (0.0, 5.0, "Verhältnis Raum- zu Wasserwärme"),
    "aufstellraum_anteil": (0.0, 1.0, "Anteil Aufstellraum"),
}
VORGABEN = {"raum_wasser_verhaeltnis": wirtschaft.VORGABE_RAUM_WASSER, "aufstellraum_anteil": wirtschaft.VORGABE_AUFSTELLRAUM}
SALDO: dict[str, tuple[float, float, str]] = {
    "kaminwaerme_kwh": (0.0, 10_000_000.0, "Kaminwärme"),
    "holz_kg": (0.0, 10_000_000.0, "Holz"),
    "holz_eur": (0.0, 10_000_000.0, "Holzkosten"),
    "einsparung_eur": (0.0, 10_000_000.0, "Einsparung"),
    "einsparung_kwh": (0.0, 10_000_000.0, "Eingesparte Energie"),
    "auflagen": (0.0, 1_000_000.0, "Auflagen"),
}


@dataclass
class Messung:
    """Was ein Takt an Zählern und Temperatur liefert. Stände in kWh, `None` heißt kein gültiger Wert."""
    waerme_kwh: Optional[float] = None
    energie_kwh: dict[str, Optional[float]] = field(default_factory=dict)
    t_aussen: Optional[float] = None


def _zahl(roh: Any, minimum: float, maximum: float, titel: str) -> Optional[float]:
    if roh is None or roh == "":
        return None
    zahl = als_zahl(roh)
    if isinstance(roh, bool) or zahl is None:
        raise EinstellungsFehler(f"{titel}: Bitte eine Zahl eingeben.")
    if not minimum <= zahl <= maximum:
        raise EinstellungsFehler(f"{titel}: Erlaubt sind {minimum:g} bis {maximum:g}.")
    return zahl


def _neue_zeile() -> dict:
    return {"auflagen": 0, "holz_kg": 0.0, "holz_eur": 0.0, "waerme_holz_kwh": 0.0, "waerme_zaehler_kwh": None,
            "verbrauch_kwh": None, "t_summe": 0.0, "t_s": 0.0, "s": 0.0, "laufend_eur": 0.0, "ohne_menge": False,
            "waerme_s": 0.0, "waerme_luecke": False, "final": False}


class Buchfuehrung:
    def __init__(self, pfad: Path, uhr: Callable[[], float]) -> None:
        self.pfad = Path(pfad)
        self._uhr = uhr
        self.einstellungen: dict[str, float] = {}
        self.saldo: dict[str, float] = {}
        self.tage: dict[str, dict] = {}
        self.zaehler: dict[str, dict] = {}
        self.letzte_zeit: Optional[float] = None
        self._gespeichert = 0.0
        self._laden()

    # ---- Datei ----------------------------------------------------------

    def _laden(self) -> None:
        try:
            with open(self.pfad, encoding="utf-8") as datei:
                daten = json.load(datei)
            if not isinstance(daten, dict):
                raise ValueError("kein Objekt")
        except FileNotFoundError:
            return
        except (OSError, ValueError) as fehler:
            # Die Datei nicht überschreiben: Sie enthält vielleicht noch Brauchbares.
            sicherung = self.pfad.with_suffix(".defekt")
            log.error("%s nicht lesbar (%r), sie wird nach %s verschoben", self.pfad, fehler, sicherung.name)
            try:
                self.pfad.replace(sicherung)
            except OSError:
                pass
            return
        self.einstellungen = {k: z for k, v in (daten.get("einstellungen") or {}).items()
                              if k in EINSTELLUNGEN and (z := als_zahl(v)) is not None}
        self.saldo = {k: z for k, v in (daten.get("saldo") or {}).items() if k in SALDO and (z := als_zahl(v)) is not None}
        self.tage = {k: v for k, v in (daten.get("tage") or {}).items() if isinstance(v, dict) and _datum(k)}
        self.zaehler = {k: v for k, v in (daten.get("zaehler") or {}).items()
                        if isinstance(v, dict) and als_zahl(v.get("stand")) is not None and als_zahl(v.get("zeit")) is not None}
        self.letzte_zeit = als_zahl(daten.get("letzte_zeit"))
        for zeile in self.tage.values():
            # Zeilen von vor 0.4.2 kennen die Abdeckung nicht. Sie gelten wie bisher als ganz erfasst.
            zeile.setdefault("waerme_s", zeile.get("s", 0.0) if zeile.get("waerme_zaehler_kwh") is not None else 0.0)
            for name, vorgabe in _neue_zeile().items():
                zeile.setdefault(name, vorgabe)

    def sichern(self, erzwingen: bool = False) -> None:
        jetzt = self._uhr()
        if not erzwingen and jetzt - self._gespeichert < SPEICHER_INTERVALL_S:
            return
        self._gespeichert = jetzt
        try:
            atomar_schreiben(self.pfad, {"version": VERSION, "einstellungen": self.einstellungen, "saldo": self.saldo,
                                         "tage": self.tage, "zaehler": self.zaehler, "letzte_zeit": self.letzte_zeit})
        except OSError as fehler:
            log.error("Buchführung nicht gespeichert: %r", fehler)

    # ---- Einstellungen ----------------------------------------------------

    def wert(self, name: str) -> Optional[float]:
        return self.einstellungen.get(name, VORGABEN.get(name))

    def einstellungen_setzen(self, roh: Any) -> None:
        """Nimmt nur bekannte Felder. Ein leeres Feld nimmt den Wert zurück. Alles oder nichts."""
        if not isinstance(roh, dict):
            raise EinstellungsFehler("Die Einstellungen sind ungültig.")
        neu = dict(self.einstellungen)
        for name, wert in roh.items():
            if name not in EINSTELLUNGEN:
                raise EinstellungsFehler(f"Unbekannte Einstellung: {name}")
            zahl = _zahl(wert, *EINSTELLUNGEN[name][:2], EINSTELLUNGEN[name][2])
            if zahl is None:
                neu.pop(name, None)
            else:
                neu[name] = zahl
        self.einstellungen = neu
        self.sichern(True)

    def saldo_setzen(self, roh: Any) -> None:
        if not isinstance(roh, dict):
            raise EinstellungsFehler("Der Eröffnungssaldo ist ungültig.")
        neu = {}
        for name, (minimum, maximum, titel) in SALDO.items():
            zahl = _zahl(roh.get(name), minimum, maximum, titel)
            if zahl:
                neu[name] = zahl
        unbekannt = set(roh) - set(SALDO)
        if unbekannt:
            raise EinstellungsFehler(f"Unbekannte Angabe: {sorted(unbekannt)[0]}")
        self.saldo = neu
        self.sichern(True)

    def konfiguration(self, profil: Anlagenprofil) -> wirtschaft.Konfiguration:
        return wirtschaft.konfiguration_aus(profil, {n: self.wert(n) for n in EINSTELLUNGEN})

    # ---- Tage -------------------------------------------------------------

    def _zeile(self, tag: str, k: wirtschaft.Konfiguration) -> dict:
        zeile = self.tage.get(tag)
        if zeile is None:
            zeile = self.tage[tag] = _neue_zeile()
            zeile["laufend_eur"] = k.laufende_kosten_jahr / 365.0   # zum Preis des Tages
        return zeile

    def _abschliessen(self, k: wirtschaft.Konfiguration, heute: str) -> None:
        """Jeder Tag vor heute wird einmal endgültig bewertet und danach nie mehr angefasst."""
        for tag, zeile in self.tage.items():
            if tag < heute and not zeile.get("final"):
                zeile |= wirtschaft.tag_auswerten(zeile, k, True)
                zeile["final"] = True

    def _vorlaeufig(self, tag: str, k: wirtschaft.Konfiguration) -> None:
        zeile = self.tage.get(tag)
        if zeile is not None and not zeile.get("final"):
            zeile |= wirtschaft.tag_auswerten(zeile, k, False)

    def auflage(self, jetzt: float, delta: int, profil: Anlagenprofil) -> None:
        """+1 für eine erkannte oder nachgetragene Auflage, −1 für eine Korrektur. Heute, nie unter 0."""
        k = self.konfiguration(profil)
        tag = _tag(jetzt)
        zeile = self._zeile(tag, k)
        n = zeile["auflagen"]
        if delta > 0:
            zeile["auflagen"] = n + 1
            if k.kg_je_auflage is None:
                zeile["ohne_menge"] = True
            else:
                kg = k.kg_je_auflage
                zeile["holz_kg"] += kg
                zeile["holz_eur"] += 0.0 if k.holz_eur_je_kg is None else kg * k.holz_eur_je_kg
                zeile["waerme_holz_kwh"] += wirtschaft.nutzwaerme_aus_holz(kg, k.holz_heizwert, k.holz_nutzungsgrad)
        elif n > 0:
            rest = (n - 1) / n      # Anteilig zurücknehmen: Auch eine geänderte kg-Angabe bleibt stimmig
            zeile["auflagen"] = n - 1
            for name in ("holz_kg", "holz_eur", "waerme_holz_kwh"):
                zeile[name] *= rest
        self._vorlaeufig(tag, k)
        self.sichern(True)

    def tick(self, jetzt: float, messung: Messung, profil: Anlagenprofil) -> None:
        """Ein Takt: Zähler lesen, Zeit und Außentemperatur erfassen, den Tag vorläufig bewerten."""
        k = self.konfiguration(profil)
        tag = _tag(jetzt)
        vorher = self.zaehler.get("waerme")
        delta, luecke = self._delta("waerme", messung.waerme_kwh, jetzt)
        if luecke and vorher is not None and (alt := self.tage.get(_tag(vorher["zeit"]))) is not None and not alt.get("final"):
            alt["waerme_luecke"] = True     # vor dem Abschließen: Dem alten Tag fehlt sein Ende
        self._abschliessen(k, tag)
        zeile = self._zeile(tag, k)
        dt = 0.0 if self.letzte_zeit is None else min(max(jetzt - self.letzte_zeit, 0.0), MAX_TAKT_S)
        self.letzte_zeit = jetzt
        zeile["s"] += dt
        if messung.t_aussen is not None and dt > 0:
            zeile["t_summe"] += messung.t_aussen * dt
            zeile["t_s"] += dt
        if messung.waerme_kwh is not None:
            zeile["waerme_s"] += dt
        if luecke:
            zeile["waerme_luecke"] = True
        if delta is not None:
            zeile["waerme_zaehler_kwh"] = (zeile["waerme_zaehler_kwh"] or 0.0) + delta
        deltas = [d for key, stand in messung.energie_kwh.items() if (d := self._delta(f"energie:{key}", stand, jetzt)[0]) is not None]
        if deltas:
            zeile["verbrauch_kwh"] = (zeile["verbrauch_kwh"] or 0.0) + sum(deltas)
        self._vorlaeufig(tag, k)
        self.sichern()

    def _delta(self, schluessel: str, stand: Optional[float], jetzt: float) -> tuple[Optional[float], bool]:
        """(Zuwachs seit dem letzten gültigen Stand, Lücke). Ohne Wert oder bei neuem Bezug ist der Zuwachs None.

        Lücke heißt: Den Tagen fehlt Energie, die der Zähler gezählt hat. Das ist so nach einer langen
        Pause über Mitternacht, in der sich der Stand bewegt hat.
        """
        if stand is None:
            return None, False
        alt = self.zaehler.get(schluessel)
        self.zaehler[schluessel] = {"stand": stand, "zeit": jetzt}
        if alt is None or stand < alt["stand"]:
            return None, False
        if jetzt - alt["zeit"] > MAX_LUECKE_S and _tag(alt["zeit"]) != _tag(jetzt):
            return None, stand != alt["stand"]
        return stand - alt["stand"], False

    def verwaiste_zaehler_entfernen(self, aktiv: set[str]) -> None:
        """Ein Zähler, der nicht mehr zugewiesen ist, beginnt bei erneuter Zuweisung neu."""
        for schluessel in [s for s in self.zaehler if s not in aktiv]:
            del self.zaehler[schluessel]

    # ---- Auswertung -------------------------------------------------------

    def zeilen(self, profil: Anlagenprofil, bis: Optional[str] = None) -> list[dict]:
        """Tage aufsteigend, jede Zeile mit Datum. Der heutige Tag ist vorläufig bewertet."""
        k = self.konfiguration(profil)
        self._abschliessen(k, bis or _tag(self._uhr()))
        return [{"tag": tag, **zeile} for tag, zeile in sorted(self.tage.items())]

    def amortisation(self, profil: Anlagenprofil) -> dict:
        return wirtschaft.amortisation(self.zeilen(profil), self.saldo, self.wert("investition"), self.wert("jahreswaerme_kwh"),
                                       self.wert("laufende_kosten_jahr") or 0.0)

    def referenz_vorschlag(self, profil: Anlagenprofil) -> wirtschaft.ReferenzVorschlag:
        """Aus eigenen, vollständig erfassten Tagen ohne Feuer."""
        tage = []
        for zeile in self.zeilen(profil):
            t = wirtschaft.mittel_t_aussen(zeile)
            if (zeile["final"] and (zeile.get("kaminwaerme_kwh") or 0.0) < wirtschaft.KAMINTAG_KWH and t is not None
                    and zeile.get("verbrauch_kwh") is not None and zeile.get("s", 0.0) >= wirtschaft.ABDECKUNG_VOLLER_TAG_S):
                tage.append((t, zeile["verbrauch_kwh"]))
        return wirtschaft.referenz_lernen(tage)

    def referenz_uebernehmen(self, profil: Anlagenprofil) -> wirtschaft.ReferenzVorschlag:
        vorschlag = self.referenz_vorschlag(profil)
        if not vorschlag.ok:
            raise EinstellungsFehler("Es liegt kein gültiger Vorschlag vor. " + vorschlag.grund)
        self.einstellungen_setzen({"referenz_a": vorschlag.a, "referenz_b": vorschlag.b, "referenz_sockel": vorschlag.sockel})
        return vorschlag


def _tag(zeit: float) -> str:
    return datetime.fromtimestamp(zeit).date().isoformat()


def _datum(text: str) -> Optional[date]:
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None
