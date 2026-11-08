"""Zuweisung der Ein- und Ausgänge und die eigenen Werte der App.

Liegt in /config/einstellungen.json und überlebt damit Updates. Der erste Start
übernimmt die bisherigen `entitaeten` aus den App-Optionen.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from anbindung.eingaenge import Entitaeten, als_zahl
from kern.typen import Parameter

log = logging.getLogger(__name__)

PFLICHT, EMPFOHLEN, OPTIONAL, ERWEITERT = "pflicht", "empfohlen", "optional", "erweitert"
EINGANG, AUSGANG = "eingang", "ausgang"
_ID_MUSTER = re.compile(r"[a-z_]+\.[a-z0-9_]+")
_STELLGLIED = ("cover", "light", "number", "input_number")


@dataclass(frozen=True)
class Rolle:
    schluessel: str
    titel: str
    hilfe: str
    domains: tuple[str, ...]
    stufe: str = OPTIONAL
    art: str = EINGANG
    einheit: Optional[str] = None
    frei: bool = False      # Auch Eintragen von Hand, zum Beispiel ein notify-Dienst


ROLLEN: tuple[Rolle, ...] = (
    Rolle("abgas", "Abgastemperatur", "Der wichtigste Messwert. Danach richtet sich die ganze Steuerung.",
          ("sensor",), PFLICHT, einheit="°C"),
    Rolle("tuer", "Türkontakt", "Meldet, ob die Ofentür offen ist. Die Klappe öffnet dann sofort.",
          ("binary_sensor",), EMPFOHLEN),
    Rolle("t_wt", "Wärmetauscher", "Wassertemperatur am Ofen. Schützt vor Überhitzung des Wassers.",
          ("sensor",), einheit="°C"),
    Rolle("puffer_unten", "Puffer unten", "Zeigt, ob der Speicher noch Wärme aufnehmen kann.",
          ("sensor",), einheit="°C"),
    Rolle("druck", "Kaminzug", "Differenzdruck am Schornstein. Dient auch als Lebenszeichen des Fühlers.",
          ("sensor",)),
    Rolle("can_alter", "Alter des Fühlersignals", "Sekunden seit dem letzten Datenpaket vom Fühler.",
          ("sensor",), einheit="s"),
    Rolle("klappe_ist", "Klappe Iststellung", "Rückmeldung, wie weit die Klappe wirklich offen ist. Nur Anzeige.",
          _STELLGLIED),
    Rolle("wartung", "Wartung", "Schalter für den Wartungsbetrieb.", ("input_boolean", "switch")),
    Rolle("handbetrieb", "Handbetrieb", "Schalter, mit dem die Klappe von Hand gestellt wird.",
          ("input_boolean", "switch")),
    Rolle("gradient", "Temperaturanstieg", "Leer lassen: Die App rechnet ihn selbst aus der Abgastemperatur.",
          ("sensor",), ERWEITERT),
    Rolle("t_max_3min", "Maximum 3 Minuten", "Leer lassen: Die App rechnet es selbst.", ("sensor",), ERWEITERT),
    Rolle("t_wt_anstieg", "Anstieg Wärmetauscher", "Leer lassen: Die App rechnet ihn selbst.",
          ("sensor",), ERWEITERT),
    Rolle("ausgang_klappe", "Klappe Soll", "Hierhin schreibt die App später die Klappenstellung.",
          _STELLGLIED, PFLICHT, AUSGANG),
    Rolle("ausgang_heartbeat", "Watchdog", "Lebenszeichen, damit die Klappe bei einem Ausfall öffnet.",
          ("number",), OPTIONAL, AUSGANG),
    Rolle("ausgang_neustart", "Fühler-Neustart", "Startet den Fühler neu, wenn er verstummt.",
          ("button", "script"), OPTIONAL, AUSGANG),
    Rolle("ausgang_meldungen", "Meldungen", "Dienst für Nachrichten, zum Beispiel notify.mobile_app_handy.",
          ("notify", "script"), OPTIONAL, AUSGANG, frei=True),
)
ROLLEN_NACH_NAME = {r.schluessel: r for r in ROLLEN}
_EINGANG_FELDER = ("abgas", "druck", "gradient", "t_max_3min", "tuer", "t_wt", "t_wt_anstieg",
                   "puffer_unten", "wartung", "handbetrieb", "can_alter")


@dataclass(frozen=True)
class Feld:
    name: str
    titel: str
    hilfe: str
    minimum: float
    maximum: float
    schritt: float
    einheit: str


def _f(name: str, titel: str, hilfe: str, minimum: float, maximum: float, schritt: float, einheit: str) -> Feld:
    return Feld(name, titel, hilfe, minimum, maximum, schritt, einheit)


PARAMETER_THEMEN: tuple[tuple[str, tuple[Feld, ...]], ...] = (
    ("Anheizen", (
        _f("t_abbrandstart", "Abbrand beginnt bei", "Ab dieser Abgastemperatur gilt das Feuer als gut angebrannt.", 80, 400, 5, "°C"),
        _f("gradient_start", "Anstieg für Feuererkennung", "So schnell muss die Temperatur steigen, damit die App ein Feuer erkennt.", 0.5, 30, 0.5, "K/min"),
        _f("startanstieg", "Mindestanstieg nach dem Öffnen", "Steigt die Temperatur nicht so weit, war es kein echtes Anheizen.", 5, 100, 5, "K"),
        _f("startfenster", "Zeit für den Mindestanstieg", "Bis dahin muss der Mindestanstieg erreicht sein.", 2, 60, 1, "min"),
        _f("anheizen_mindestdauer", "Mindestdauer kalter Ofen", "So lange bleibt die Klappe beim Kaltstart mindestens im Anheizen.", 0, 60, 1, "min"),
        _f("anheizen_mindestdauer_nachlegen", "Mindestdauer beim Nachlegen", "Für das Nachlegen auf Glut.", 0, 30, 1, "min"),
        _f("anheizen_mindestdauer_feuer", "Mindestdauer auf heißem Feuer", "Für das Nachlegen, solange der Ofen noch heiß ist.", 0, 30, 1, "min"),
        _f("anheizen_timeout", "Höchstdauer Anheizen", "Danach meldet die App einen Anheizfehler.", 5, 120, 5, "min"),
        _f("klappe_anheizen_feuer", "Klappe beim Nachlegen", "Klappenstellung, wenn auf einen heißen Ofen nachgelegt wird.", 20, 100, 5, "%"),
        _f("t_kalt", "Ofen gilt als kalt unter", "Darunter darf die Klappe ganz schließen.", 20, 100, 5, "°C"),
    )),
    ("Abbrand", (
        _f("t_ziel", "Zieltemperatur", "Auf diesen Wert regelt die App die Abgastemperatur.", 120, 500, 5, "°C"),
        _f("klappe_min_abbrand", "Klappe mindestens", "Weiter schließt die Klappe im Abbrand nicht.", 5, 100, 5, "%"),
        _f("klappe_max_abbrand", "Klappe höchstens", "Weiter öffnet die Klappe im Abbrand nicht.", 20, 100, 5, "%"),
        _f("klappe_min_entgasung", "Klappe mindestens beim Entgasen", "In den ersten Minuten braucht das Holz mehr Luft.", 5, 100, 5, "%"),
        _f("entgasung_dauer", "Dauer der Entgasung", "So lange gilt die höhere Mindeststellung.", 0, 40, 1, "min"),
        _f("klappe_schliessrate", "Schließgeschwindigkeit", "So weit schließt die Klappe höchstens je 30 s.", 1, 50, 1, "%"),
        _f("regler_i_start_kalt", "Startwert kalter Ofen", "Klappenstellung, mit der der Regler nach dem Kaltstart beginnt.", 20, 100, 5, "%"),
        _f("regler_i_start_heiss", "Startwert heißer Ofen", "Klappenstellung, mit der der Regler auf heißem Ofen beginnt.", 20, 100, 5, "%"),
    )),
    ("Glut", (
        _f("ausbrand_abfall", "Abfall für Ausbrand", "So weit muss die Temperatur vom Höchstwert fallen.", 5, 150, 5, "K"),
        _f("t_glut", "Glut beginnt unter", "Unter dieser Temperatur ist nur noch Glut da.", 60, 300, 5, "°C"),
        _f("t_abkuehlen", "Abkühlen beginnt unter", "Unter dieser Temperatur ist die Glut aus.", 40, 200, 5, "°C"),
        _f("klappe_glut", "Klappe in der Glut", "Wenig Luft hält die Glut lange.", 0, 50, 1, "%"),
        _f("glut_max_dauer", "Höchstdauer Glut", "Danach geht der Ofen in Abkühlen über.", 10, 480, 10, "min"),
        _f("abkuehlen_max_dauer", "Höchstdauer Abkühlen", "Danach geht der Ofen in Standby.", 10, 480, 10, "min"),
        _f("puffer_voll", "Puffer gilt als voll ab", "Dann rät die App vom Nachlegen ab.", 30, 95, 1, "°C"),
    )),
    ("Sicherheit", (
        _f("t_uebertemperatur", "Übertemperatur ab", "Darüber drosselt die App die Klappe sofort.", 200, 900, 10, "°C"),
        _f("klappe_uebertemperatur", "Klappe bei Übertemperatur", "Stellung, solange die Temperatur zu hoch ist.", 0, 100, 5, "%"),
        _f("t_wasser_max", "Wasser höchstens", "Darüber drosselt die App, um das Wasser zu schützen.", 60, 100, 1, "°C"),
        _f("klappe_wasser", "Klappe bei heißem Wasser", "Stellung, solange das Wasser zu heiß ist.", 0, 100, 5, "%"),
        _f("fuehler_timeout", "Fühler gilt als stumm nach", "So lange darf der Messwert ohne Meldung bleiben.", 60, 3600, 30, "s"),
        _f("feuer_twt_anstieg", "Feuer am Wasser erkannt ab", "So viel muss das Wasser in 10 Minuten steigen.", 1, 20, 0.5, "K"),
        _f("wartung_timeout", "Wartung endet nach", "Danach beendet die App die Wartung von selbst.", 5, 240, 5, "min"),
        _f("klappe_wartung", "Klappe in der Wartung", "Stellung zum Reinigen.", 0, 100, 5, "%"),
        _f("klappe_manuell", "Klappe im Handbetrieb", "Stellung, solange der Handbetrieb an ist.", 0, 100, 5, "%"),
    )),
)
REGLER_FELDER: tuple[Feld, ...] = (
    _f("regler_verstaerkung", "Verstärkung Kp", "Wie stark die Klappe auf eine Abweichung vom Ziel reagiert.", 0.05, 5, 0.05, "%/K"),
    _f("regler_nachstellzeit", "Nachstellzeit Tn", "In welcher Zeit der Regler bleibende Abweichungen ausgleicht.", 1, 60, 0.5, "min"),
    _f("regler_vorhaltung", "Vorhalt Kd", "Bremst schnelle Temperaturänderungen. 0 schaltet ihn ab.", 0, 20, 0.5, "%·min/K"),
)
FELDER = {f.name: f for _, felder in PARAMETER_THEMEN for f in felder} | {f.name: f for f in REGLER_FELDER}


class EinstellungsFehler(ValueError):
    """Die Eingabe passt nicht; der Text ist für die Oberfläche bestimmt."""


class Speicher:
    """Lädt, prüft und schreibt atomar. Alle Änderungen laufen über diese Klasse."""

    def __init__(self, pfad: Path, optionen: Optional[dict] = None) -> None:
        self.pfad = Path(pfad)
        self.daten = self._laden(optionen or {})

    def _laden(self, optionen: dict) -> dict:
        try:
            with open(self.pfad, encoding="utf-8") as datei:
                daten = json.load(datei)
            if not isinstance(daten, dict):
                raise ValueError("kein Objekt")
        except FileNotFoundError:
            daten = self._migrieren(optionen)
            try:
                self._schreiben(daten)
            except OSError as fehler:
                log.error("%s nicht anlegbar: %r", self.pfad, fehler)
            return daten
        except (OSError, ValueError) as fehler:
            # Kaputte Datei nicht überschreiben, sie enthält vielleicht noch Brauchbares.
            log.error("%s nicht lesbar (%r), die App startet ohne Zuweisungen", self.pfad, fehler)
            daten = {}
        return self._ergaenzt(daten)

    def _ergaenzt(self, daten: dict) -> dict:
        daten.setdefault("rollen", {})
        daten.setdefault("tuerkontakt_vorhanden", True)
        daten.setdefault("parameter_neu", {})
        daten.setdefault("betrieb_s", 0.0)
        daten.setdefault("token", secrets.token_urlsafe(32))
        return daten

    def _migrieren(self, optionen: dict) -> dict:
        alt = optionen.get("entitaeten") or {}
        rollen = {k: v for k, v in alt.items() if k in ROLLEN_NACH_NAME and isinstance(v, str) and v}
        log.info("Einstellungen neu angelegt, %d Zuweisungen aus den App-Optionen übernommen", len(rollen))
        return self._ergaenzt({"rollen": rollen})

    def _schreiben(self, daten: dict) -> None:
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        zwischen = self.pfad.with_suffix(".tmp")
        with open(zwischen, "w", encoding="utf-8") as datei:
            json.dump(daten, datei, ensure_ascii=False, indent=2)
            datei.flush()
            os.fsync(datei.fileno())
        os.replace(zwischen, self.pfad)

    def speichern(self) -> None:
        try:
            self._schreiben(self.daten)
        except OSError as fehler:
            log.error("Einstellungen nicht gespeichert: %r", fehler)
            raise EinstellungsFehler("Die Einstellungen konnten nicht gespeichert werden.") from fehler

    # ---- Lesen --------------------------------------------------------

    @property
    def rollen(self) -> dict[str, str]:
        return self.daten["rollen"]

    @property
    def tuerkontakt_vorhanden(self) -> bool:
        return bool(self.daten["tuerkontakt_vorhanden"])

    @property
    def parameter_neu(self) -> dict[str, float]:
        return self.daten["parameter_neu"]

    @property
    def token(self) -> str:
        return self.daten["token"]

    def entitaeten(self) -> Entitaeten:
        """Nicht zugewiesene Rollen sind None, die Vorgaben von Entitaeten gelten hier nicht."""
        return Entitaeten(**{feld: self.rollen.get(feld) for feld in _EINGANG_FELDER},
                          tuerkontakt_vorhanden=None)

    # ---- Ändern -------------------------------------------------------

    def rollen_setzen(self, rollen: dict[str, Any], tuerkontakt: bool) -> None:
        neu: dict[str, str] = {}
        for schluessel, wert in rollen.items():
            rolle = ROLLEN_NACH_NAME.get(schluessel)
            if rolle is None:
                raise EinstellungsFehler(f"Unbekannte Rolle: {schluessel}")
            if wert in (None, ""):
                continue
            if not isinstance(wert, str) or not _ID_MUSTER.fullmatch(wert):
                raise EinstellungsFehler(f"{rolle.titel}: „{wert}“ ist keine gültige Entität.")
            if wert.split(".", 1)[0] not in rolle.domains:
                raise EinstellungsFehler(f"{rolle.titel}: Erlaubt sind {', '.join(rolle.domains)}.")
            neu[schluessel] = wert
        self.daten["rollen"] = neu
        self.daten["tuerkontakt_vorhanden"] = bool(tuerkontakt)
        self.speichern()

    def parameter_setzen(self, name: str, wert: Any) -> Optional[float]:
        """None nimmt den eigenen Wert zurück, dann gilt wieder der Helfer oder die Vorgabe."""
        feld = FELDER.get(name)
        if feld is None or name not in Parameter.feldnamen():
            raise EinstellungsFehler(f"Unbekannter Wert: {name}")
        if wert is None:
            self.parameter_neu.pop(name, None)
            self.speichern()
            return None
        zahl = als_zahl(wert)
        if isinstance(wert, bool) or zahl is None:
            raise EinstellungsFehler(f"{feld.titel}: Bitte eine Zahl eingeben.")
        if not feld.minimum <= zahl <= feld.maximum:
            raise EinstellungsFehler(
                f"{feld.titel}: Erlaubt sind {feld.minimum:g} bis {feld.maximum:g} {feld.einheit}.")
        self.parameter_neu[name] = zahl
        self.speichern()
        return zahl

    def tuerkontakt_setzen(self, vorhanden: bool) -> None:
        self.daten["tuerkontakt_vorhanden"] = bool(vorhanden)
        self.speichern()

    def betrieb_addieren(self, sekunden: float) -> None:
        self.daten["betrieb_s"] = float(self.daten["betrieb_s"]) + sekunden
