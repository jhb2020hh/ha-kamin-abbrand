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
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from anbindung.anlagenprofil import (ENTITAET_MUSTER, Anlagenprofil, ProfilFehler, profil_aus_bestand,
                                     profil_pruefen)
from anbindung.eingaenge import Entitaeten, als_zahl
from anbindung.benachrichtigung import ZuordnungFehler, zuordnung_pruefen
from kern import wartung, wasser
from kern.typen import Parameter

log = logging.getLogger(__name__)

PFLICHT, EMPFOHLEN, OPTIONAL, ERWEITERT = "pflicht", "empfohlen", "optional", "erweitert"
EINGANG, AUSGANG = "eingang", "ausgang"
LAUSCHEN, BETRIEB = "lauschen", "betrieb"
# Einmalige Bestätigung vor dem ersten Betrieb.
BESTAETIGUNG_PUNKTE = ("co_melder", "hersteller", "verantwortung")
OHNE_WATCHDOG = "ohne_watchdog"
# Handbetrieb der App: wählbare Dauern, danach übernimmt wieder die Automatik.
HAND_DAUERN_MIN = (15.0, 30.0, 60.0, 120.0)
HAND_DAUER_VORGABE = 30.0
_STELLGLIED = ("cover", "light", "number", "input_number")
# Kennung einer Einmess-Aufzeichnung, zugleich ihr Dateiname: Datum_Uhrzeit, bei gleicher Minute -2, -3 …
EINMESS_ID_MUSTER = re.compile(r"\d{4}-\d{2}-\d{2}_\d{4}(-\d{1,3})?")


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
    fahrenheit: bool = False    # Ein Sensor in °F wird umgerechnet, das ist also keine Warnung wert
    einheiten: tuple[str, ...] = ()     # Erlaubte Einheiten, die die App umrechnet (ohne Beachtung der Groß-/Kleinschreibung)


ROLLEN: tuple[Rolle, ...] = (
    Rolle("abgas", "Abgastemperatur", "Der wichtigste Messwert. Danach richtet sich die ganze Steuerung.",
          ("sensor",), PFLICHT, einheit="°C", fahrenheit=True),
    Rolle("tuer", "Türkontakt", "Meldet, ob die Ofentür offen ist. Die Klappe öffnet dann sofort.",
          ("binary_sensor",), EMPFOHLEN),
    Rolle("t_wt", "Wärmetauscher", "Wassertemperatur am Ofen. Schützt vor Überhitzung des Wassers.",
          ("sensor",), einheit="°C"),
    Rolle("puffer_unten", "Puffer unten", "Zeigt, ob der Speicher noch Wärme aufnehmen kann.",
          ("sensor",), einheit="°C"),
    Rolle("puffer_oben", "Puffer oben", "Obere Speichertemperatur, zeigt die verfügbare Wärme.",
          ("sensor",), einheit="°C", fahrenheit=True),
    Rolle("vorlauf", "Vorlauf", "Wassertemperatur vom Ofen zum Speicher.",
          ("sensor",), einheit="°C", fahrenheit=True),
    Rolle("ruecklauf", "Rücklauf", "Wassertemperatur zurück zum Ofen. Zu kalt schadet dem Ofen.",
          ("sensor",), einheit="°C", fahrenheit=True),
    Rolle("pumpe", "Pumpe Ein/Aus", "Meldet, ob die Pumpe läuft. Die App liest das nur und schaltet sie nicht.",
          ("binary_sensor", "switch", "input_boolean")),
    Rolle("pumpe_stellsignal", "Pumpe Stellsignal",
          "Stellsignal der Pumpe in %. Ist Ein/Aus leer, gilt die Pumpe ab der Schwelle unter Anlage als laufend.",
          ("sensor",), einheiten=("%",)),
    Rolle("waermezaehler", "Wärmemengenzähler Energie",
          "Zählerstand der Wärme, die der Ofen ans Wasser abgibt, in Wh, kWh oder MWh. Damit wird die Kaminwärme gemessen statt geschätzt.",
          ("sensor",), einheiten=("Wh", "kWh", "MWh")),
    Rolle("waermeleistung", "Wärmemengenzähler Leistung",
          "Aktuelle Leistung des Wärmemengenzählers in W oder kW. Sie wird nur angezeigt und geht nicht in die Rechnung ein.",
          ("sensor",), einheiten=("W", "kW")),
    Rolle("energiezaehler", "Energiezähler", "Zähler der bisherigen Heizung. Damit wird die Einsparung gemessen statt geschätzt.",
          ("sensor",)),
    Rolle("t_aussen", "Außentemperatur", "Vergleicht Tage mit und ohne Feuer bei gleichem Wetter.",
          ("sensor",), einheit="°C", fahrenheit=True),
    Rolle("druck", "Kaminzug", "Differenzdruck am Schornstein. Dient auch als Lebenszeichen des Fühlers.",
          ("sensor",)),
    Rolle("lueftung", "Lüftung frei", "Freigabe der Lüftung, zum Beispiel vom Unterdruckwächter. Wird nur angezeigt.",
          ("binary_sensor",)),
    Rolle("can_alter", "Alter des Fühlersignals", "Sekunden seit dem letzten Datenpaket vom Fühler.",
          ("sensor",), einheit="s"),
    Rolle("klappe_ist", "Klappe Iststellung", "Rückmeldung, wie weit die Klappe wirklich offen ist. Im Betrieb meldet eine Abweichung eine Störung.",
          (*_STELLGLIED, "sensor")),
    Rolle("wartung", "Wartung", "Schalter für den Wartungsbetrieb.", ("input_boolean", "switch")),
    Rolle("handbetrieb", "Handbetrieb", "Schalter, mit dem die Klappe von Hand gestellt wird.",
          ("input_boolean", "switch")),
    Rolle("gradient", "Temperaturanstieg", "Leer lassen: Die App rechnet ihn selbst aus der Abgastemperatur.",
          ("sensor",), ERWEITERT),
    Rolle("t_max_3min", "Maximum 3 Minuten", "Leer lassen: Die App rechnet es selbst.", ("sensor",), ERWEITERT),
    Rolle("t_wt_anstieg", "Anstieg Wärmetauscher", "Leer lassen: Die App rechnet ihn selbst.",
          ("sensor",), ERWEITERT),
    Rolle("konkurrierende_automation", "Konkurrierende Automation",
          "Automation, die die Klappe sonst steuert. Die App schaltet sie beim Wechsel auf Betrieb aus "
          "und bei Lauschen oder einem Rückfall wieder an.",
          ("automation",)),
    Rolle("ausgang_klappe", "Klappe Soll", "Hierhin schreibt die App im Betrieb die Klappenstellung.",
          _STELLGLIED, EMPFOHLEN, AUSGANG),
    Rolle("ausgang_heartbeat", "Watchdog", "Lebenszeichen, damit die Klappe bei einem Ausfall öffnet.",
          ("number",), OPTIONAL, AUSGANG),
    Rolle("ausgang_neustart", "Fühler-Neustart", "Startet den Fühler neu, wenn er verstummt.",
          ("button", "script"), OPTIONAL, AUSGANG),
)
ROLLEN_NACH_NAME = {r.schluessel: r for r in ROLLEN}
_EINGANG_FELDER = ("abgas", "druck", "gradient", "t_max_3min", "tuer", "t_wt", "t_wt_anstieg",
                   "puffer_unten", "wartung", "handbetrieb", "can_alter")
# Für Auswertungen mitgelesen, der Kern bekommt sie nicht.
ZUSATZ_ROLLEN = ("vorlauf", "ruecklauf", "pumpe", "pumpe_stellsignal", "puffer_oben", "waermezaehler", "waermeleistung", "energiezaehler", "t_aussen", "lueftung")


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


# Gehört nur zur App, nicht zum Regelkern (kein Feld von kern.typen.Parameter).
REINIGUNG_FELD = "wt_reinigung_h"
# Grenzen der Wasserseite, Vorgaben stehen in kern/wasser.py (Grenzen).
WASSER_FELDER = ("wasser_ruecklauf_min", "wasser_vorlauf_warnung", "wasser_vorlauf_alarm", "wasser_pumpe_t_wt")
# Leistungsstufen der App: Zieltemperatur je Stufe. Die Live-Automation hat eigene Helfer und bleibt unberührt.
LEISTUNG_FELDER = {"Eco": "t_ziel_eco", "Normal": "t_ziel_normal", "Power": "t_ziel_power"}
LEISTUNG_VORGABE = {"t_ziel_eco": 220.0, "t_ziel_normal": 250.0, "t_ziel_power": 270.0}
EIGENE_FELDER = (REINIGUNG_FELD, *WASSER_FELDER, *LEISTUNG_VORGABE)
_WASSER_VORGABE = {"wasser_ruecklauf_min": "ruecklauf_min", "wasser_vorlauf_warnung": "vorlauf_warnung",
                   "wasser_vorlauf_alarm": "vorlauf_alarm", "wasser_pumpe_t_wt": "pumpe_t_wt"}

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
        _f("klappe_kaltstart", "Klappe beim Kaltstart", "Klappenstellung beim ersten Anheizen (kalter Ofen, keine Glut).", 30, 100, 5, "%"),
        _f("t_kaltstart_ende", "Kaltstart offen bis", "Beim Kaltstart bleibt die Klappe bis zu dieser Abgastemperatur auf dem Kaltstart-Wert, erst dann beginnt der Abbrand.", 150, 300, 5, "°C"),
        _f("klappe_anheizen_feuer", "Klappe beim Nachlegen", "Klappenstellung, wenn auf einen heißen Ofen nachgelegt wird.", 20, 100, 5, "%"),
        _f("t_kalt", "Ofen gilt als kalt unter", "Darunter darf die Klappe ganz schließen.", 20, 100, 5, "°C"),
    )),
    ("Abbrand", (
        _f("t_ziel", "Zieltemperatur", "Auf diesen Wert regelt die App die Abgastemperatur. Die Leistungsknöpfe auf der Startseite setzen ihn neu.", 120, 500, 5, "°C"),
        _f("t_ziel_eco", "Ziel bei Eco", "Zieltemperatur für die Leistung Eco.", 120, 500, 5, "°C"),
        _f("t_ziel_normal", "Ziel bei Normal", "Zieltemperatur für die Leistung Normal.", 120, 500, 5, "°C"),
        _f("t_ziel_power", "Ziel bei Power", "Zieltemperatur für die Leistung Power.", 120, 500, 5, "°C"),
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
    ("Wasserseite", (
        _f("wasser_ruecklauf_min", "Rücklauf mindestens", "Darunter warnt die App im Abbrand bei laufender Pumpe, wenn es 5 Minuten anhält.",
           20, 80, 1, "°C"),
        _f("wasser_vorlauf_warnung", "Vorlauf hoch ab", "Darüber gibt die App eine Warnung.", 50, 110, 1, "°C"),
        _f("wasser_vorlauf_alarm", "Vorlauf zu hoch ab", "Darüber löst die App Alarm aus.", 50, 115, 1, "°C"),
        _f("wasser_pumpe_t_wt", "Pumpe steht bei Feuer ab", "So heiß darf das Wasser am Ofen sein, bevor die App einen Stillstand der Pumpe bei Feuer meldet. Sie schaltet die Pumpe nie selbst.",
           40, 100, 1, "°C"),
    )),
    ("Wartung", (
        _f(REINIGUNG_FELD, "Wärmetauscher reinigen alle",
           "Brennstunden bis zur Erinnerung. Die Herstellerangabe für deinen Ofen hat Vorrang. 0 schaltet die Erinnerung aus.",
           0, 5000, 10, "h"),
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


def atomar_schreiben(pfad: Path, daten: Any) -> None:
    """Erst vollständig in eine Nebendatei, dann umbenennen. Ein Absturz lässt nie eine halbe Datei zurück."""
    pfad = Path(pfad)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    # Eindeutiger Name: Zwei gleichzeitige Schreibvorgänge (Regellauf und Oberfläche) stören sich nicht.
    zwischen = pfad.with_name(f"{pfad.stem}.{secrets.token_hex(6)}.tmp")
    try:
        with open(zwischen, "w", encoding="utf-8") as datei:
            json.dump(daten, datei, ensure_ascii=False, indent=2)
            datei.flush()
            os.fsync(datei.fileno())
        os.replace(zwischen, pfad)
    except BaseException:
        zwischen.unlink(missing_ok=True)
        raise


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
        if not isinstance(daten["rollen"], dict):
            daten["rollen"] = {}
        # Rollen, die es nicht mehr gibt (zum Beispiel ausgang_meldungen), stören nicht und fallen beim Laden weg.
        daten["rollen"] = {k: v for k, v in daten["rollen"].items() if k in ROLLEN_NACH_NAME}
        daten.setdefault("tuerkontakt_vorhanden", True)
        daten.setdefault("parameter_neu", {})
        daten.setdefault("betrieb_s", 0.0)
        daten.setdefault("abbrand_dauer", {})
        daten.setdefault("benachrichtigungen", {})
        if not isinstance(daten.get("wasser_grenzen"), dict):
            daten["wasser_grenzen"] = {}
        if not isinstance(daten.get("leistung"), dict):
            daten["leistung"] = {}
        hand = daten.get("hand") if isinstance(daten.get("hand"), dict) else {}
        dauer = als_zahl(hand.get("dauer_min"))
        daten["hand"] = {"bis": als_zahl(hand.get("bis")),
                         "dauer_min": dauer if dauer in HAND_DAUERN_MIN else HAND_DAUER_VORGABE}
        if not isinstance(daten.get("wartung"), dict):
            # Die Zählung beginnt bei der Installation dieser Funktion.
            daten["wartung"] = {"basis_s": float(daten["betrieb_s"]), "letzte_reinigung": None, "erinnert": None}
        # Alles außer einem klaren „betrieb“ heißt Lauschen, auch eine verdorbene Datei.
        daten["modus"] = BETRIEB if daten.get("modus") == BETRIEB else LAUSCHEN
        # Nur ein klares true: Dann hat die App die konkurrierende Automation ausgeschaltet und schaltet sie wieder ein.
        daten["automation_von_app_aus"] = daten.get("automation_von_app_aus") is True
        if not isinstance(daten.get("betrieb_bestaetigung"), dict):
            daten["betrieb_bestaetigung"] = {}
        massgeblich = daten.get("einmessen_massgeblich")
        daten["einmessen_massgeblich"] = (massgeblich if isinstance(massgeblich, str)
                                          and EINMESS_ID_MUSTER.fullmatch(massgeblich) else None)
        daten.setdefault("token", secrets.token_urlsafe(32))
        daten.setdefault("anlagenprofil", self._profil_gespeichert(
            profil_aus_bestand(daten["rollen"], daten["tuerkontakt_vorhanden"])))
        return daten

    @staticmethod
    def _profil_gespeichert(profil: Anlagenprofil) -> dict:
        # Der Türkontakt liegt nur einmal in der Datei, die Integration schaltet ihn auch.
        daten = profil.als_dict()
        del daten["tuerkontakt"]
        return daten

    def _migrieren(self, optionen: dict) -> dict:
        alt = optionen.get("entitaeten") or {}
        rollen = {k: v for k, v in alt.items() if k in ROLLEN_NACH_NAME and isinstance(v, str) and v}
        log.info("Einstellungen neu angelegt, %d Zuweisungen aus den App-Optionen übernommen", len(rollen))
        return self._ergaenzt({"rollen": rollen})

    def _schreiben(self, daten: dict) -> None:
        atomar_schreiben(self.pfad, daten)

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

    @property
    def benachrichtigungen(self) -> dict[str, list[str]]:
        """Art → Geräte. Ungültige Einträge in der Datei zählen nicht (nichts senden ist sicher)."""
        try:
            return zuordnung_pruefen(self.daten["benachrichtigungen"])
        except ZuordnungFehler as fehler:
            log.error("Gespeicherte Benachrichtigungen ungültig (%s), es wird nichts gesendet", fehler)
            return {}

    @property
    def reinigung_intervall_h(self) -> float:
        wert = als_zahl(self.daten["wartung"].get("intervall_h"))
        return wartung.VORGABE_INTERVALL_H if wert is None or wert < 0 else float(wert)

    def eigener_wert(self, name: str) -> Optional[float]:
        """Der eigene Wert eines Feldes, das nur die App kennt, oder None (dann gilt die Vorgabe)."""
        if name == REINIGUNG_FELD:
            return als_zahl(self.daten["wartung"].get("intervall_h"))
        if name in LEISTUNG_VORGABE:
            return als_zahl(self.daten["leistung"].get(name))
        return als_zahl(self.daten["wasser_grenzen"].get(name))

    def wirksamer_eigener_wert(self, name: str) -> float:
        """Eigener Wert oder die Vorgabe der App für ein Feld ohne Live-Helfer."""
        if name == REINIGUNG_FELD:
            return self.reinigung_intervall_h
        eigen = self.eigener_wert(name)
        if eigen is not None:
            return eigen
        if name in LEISTUNG_VORGABE:
            return LEISTUNG_VORGABE[name]
        return getattr(wasser.Grenzen(), _WASSER_VORGABE[name])

    @property
    def leistung(self) -> Optional[str]:
        """Die in der App gewählte Leistung, oder None, solange keine gewählt wurde."""
        modus = self.daten["leistung"].get("modus")
        return modus if isinstance(modus, str) and modus in LEISTUNG_FELDER else None

    def leistung_setzen(self, modus: Any) -> float:
        """Wählt die Leistung und setzt das Ziel der App. Schreibt nichts nach Home Assistant."""
        if not isinstance(modus, str) or modus not in LEISTUNG_FELDER:
            raise EinstellungsFehler("Diese Leistung gibt es nicht.")
        ziel = self.wirksamer_eigener_wert(LEISTUNG_FELDER[modus])
        self.daten["leistung"]["modus"] = modus
        self.parameter_neu["t_ziel"] = ziel
        self.speichern()
        return ziel

    def _leistung_nachfuehren(self, name: str) -> None:
        """Das Ziel folgt der aktiven Stufe. Nimmt jemand das eigene Ziel zurück, ist auch keine Stufe mehr gewählt."""
        modus = self.leistung
        if name == "t_ziel" and "t_ziel" not in self.parameter_neu:
            self.daten["leistung"].pop("modus", None)
        elif modus and name == LEISTUNG_FELDER[modus]:
            self.parameter_neu["t_ziel"] = self.wirksamer_eigener_wert(name)

    def wasser_grenzen(self, t_wasser_max: float, puffer_voll: float) -> wasser.Grenzen:
        """Vorgaben, überschrieben von den eigenen Werten. Wasser max. und Puffer voll sind Parameter des Kerns."""
        eigene = {_WASSER_VORGABE[n]: w for n in WASSER_FELDER if (w := self.eigener_wert(n)) is not None}
        return wasser.Grenzen(**(eigene | {"t_wasser_max": t_wasser_max, "puffer_voll": puffer_voll})).stimmig()

    @property
    def wartungsstand(self) -> wartung.Stand:
        w = self.daten["wartung"]
        basis = als_zahl(w.get("basis_s"))
        return wartung.Stand(
            basis_s=float(self.daten["betrieb_s"]) if basis is None else basis,
            letzte_reinigung=als_zahl(w.get("letzte_reinigung")), erinnert=als_zahl(w.get("erinnert")))

    @property
    def anlagenprofil(self) -> Anlagenprofil:
        roh = self.daten["anlagenprofil"]
        try:
            if not isinstance(roh, dict):
                raise ProfilFehler("kein Objekt")
            return profil_pruefen(roh | {"tuerkontakt": self.tuerkontakt_vorhanden})
        except ProfilFehler as fehler:
            # Die Datei bleibt, wie sie ist, bis jemand das Profil neu speichert.
            log.error("Gespeichertes Anlagenprofil ungültig (%s), es gilt die Ableitung", fehler)
            return profil_aus_bestand(self.rollen, self.tuerkontakt_vorhanden)

    @property
    def modus(self) -> str:
        return self.daten["modus"]

    @property
    def automation_von_app_aus(self) -> bool:
        """True, solange die App die konkurrierende Automation ausgeschaltet hat und sie wieder einschalten muss."""
        return self.daten["automation_von_app_aus"]

    @property
    def bestaetigung(self) -> dict:
        return self.daten["betrieb_bestaetigung"]

    @property
    def bestaetigt(self) -> bool:
        """Ein vollständiger Eintrag mit Datum und Version, sonst gilt nichts als bestätigt."""
        eintrag = self.bestaetigung.get("betrieb")
        return (isinstance(eintrag, dict) and all(eintrag.get(p) is True for p in BESTAETIGUNG_PUNKTE)
                and bool(eintrag.get("datum")) and bool(eintrag.get("version")))

    @property
    def ohne_watchdog_bestaetigt(self) -> bool:
        eintrag = self.bestaetigung.get(OHNE_WATCHDOG)
        return isinstance(eintrag, dict) and bool(eintrag.get("datum")) and bool(eintrag.get("version"))

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
            if not isinstance(wert, str) or not ENTITAET_MUSTER.fullmatch(wert):
                raise EinstellungsFehler(f"{rolle.titel}: „{wert}“ ist keine gültige Entität.")
            if wert.split(".", 1)[0] not in rolle.domains:
                raise EinstellungsFehler(f"{rolle.titel}: Erlaubt sind {', '.join(rolle.domains)}.")
            neu[schluessel] = wert
        self.daten["rollen"] = neu
        self.daten["tuerkontakt_vorhanden"] = bool(tuerkontakt)
        self.speichern()

    def profil_setzen(self, roh: Any) -> Anlagenprofil:
        if isinstance(roh, dict):
            roh = {"tuerkontakt": self.tuerkontakt_vorhanden} | roh
        try:
            profil = profil_pruefen(roh)
        except ProfilFehler as fehler:
            raise EinstellungsFehler(str(fehler)) from fehler
        if profil.wassergefuehrt and not self.anlagenprofil.wassergefuehrt:
            # Brennstunden aus der Zeit ohne Wasserseite zählen nicht für den Wärmetauscher.
            self._wartungsstand_setzen(wartung.zuruecksetzen(float(self.daten["betrieb_s"]), self.wartungsstand))
        self.daten["anlagenprofil"] = self._profil_gespeichert(profil)
        self.daten["tuerkontakt_vorhanden"] = profil.tuerkontakt
        self.speichern()
        return profil

    def parameter_setzen(self, name: str, wert: Any) -> Optional[float]:
        """None nimmt den eigenen Wert zurück, dann gilt wieder der Helfer oder die Vorgabe."""
        feld = FELDER.get(name)
        if feld is None or (name not in Parameter.feldnamen() and name not in EIGENE_FELDER):
            raise EinstellungsFehler(f"Unbekannter Wert: {name}")
        ziel = (self.daten["wartung"] if name == REINIGUNG_FELD else
                self.daten["wasser_grenzen"] if name in WASSER_FELDER else
                self.daten["leistung"] if name in LEISTUNG_VORGABE else self.parameter_neu)
        schluessel = "intervall_h" if name == REINIGUNG_FELD else name
        if wert is None:
            ziel.pop(schluessel, None)
            self._leistung_nachfuehren(name)
            self.speichern()
            return None
        zahl = als_zahl(wert)
        if isinstance(wert, bool) or zahl is None:
            raise EinstellungsFehler(f"{feld.titel}: Bitte eine Zahl eingeben.")
        if not feld.minimum <= zahl <= feld.maximum:
            raise EinstellungsFehler(
                f"{feld.titel}: Erlaubt sind {feld.minimum:g} bis {feld.maximum:g} {feld.einheit}.")
        ziel[schluessel] = zahl
        self._leistung_nachfuehren(name)
        self.speichern()
        return zahl

    # ---- Handbetrieb der App (nur im Betrieb wirksam) ---------------

    def hand_aktiv(self, jetzt: float) -> bool:
        bis = self.daten["hand"]["bis"]
        return bis is not None and jetzt < bis

    def hand_rest_s(self, jetzt: float) -> Optional[float]:
        bis = self.daten["hand"]["bis"]
        return max(0.0, bis - jetzt) if bis is not None and jetzt < bis else None

    @property
    def hand_dauer_min(self) -> float:
        return self.daten["hand"]["dauer_min"]

    @property
    def hand_bis(self) -> Optional[float]:
        return self.daten["hand"]["bis"]

    def hand_starten(self, jetzt: float, dauer_min: Optional[float] = None) -> float:
        """Startet oder verlängert den Handbetrieb. Rückgabe: Endzeit."""
        if dauer_min is not None:
            if dauer_min not in HAND_DAUERN_MIN:
                raise EinstellungsFehler(f"Dauer {dauer_min} min ist nicht vorgesehen.")
            self.daten["hand"]["dauer_min"] = float(dauer_min)
        self.daten["hand"]["bis"] = jetzt + self.daten["hand"]["dauer_min"] * 60
        self.speichern()
        return self.daten["hand"]["bis"]

    def hand_beenden(self) -> None:
        if self.daten["hand"]["bis"] is None:
            return
        self.daten["hand"]["bis"] = None
        self.speichern()

    # ---- Einmessen ------------------------------------------------------

    @property
    def einmessen_massgeblich(self) -> Optional[str]:
        """Kennung der maßgeblichen Einmess-Aufzeichnung, nur eine Kennzeichnung."""
        return self.daten["einmessen_massgeblich"]

    def einmessen_massgeblich_setzen(self, kennung: Optional[str]) -> None:
        if kennung is not None and (not isinstance(kennung, str) or not EINMESS_ID_MUSTER.fullmatch(kennung)):
            raise EinstellungsFehler("Diese Aufzeichnung gibt es nicht.")
        if self.daten["einmessen_massgeblich"] == kennung:
            return
        self.daten["einmessen_massgeblich"] = kennung
        self.speichern()

    def tuerkontakt_setzen(self, vorhanden: bool) -> None:
        self.daten["tuerkontakt_vorhanden"] = bool(vorhanden)
        self.speichern()

    def modus_setzen(self, modus: str) -> None:
        """Erst auf der Platte, dann gilt es: Scheitert das Schreiben, wirft das EinstellungsFehler."""
        alt = self.daten["modus"]
        self.daten["modus"] = BETRIEB if modus == BETRIEB else LAUSCHEN
        try:
            self.speichern()
        except EinstellungsFehler:
            self.daten["modus"] = alt
            raise

    def automation_merken(self, von_app_aus: bool) -> None:
        """Sofort auf die Platte, damit auch ein Neustart weiß, ob die Automation wieder an muss.
        Scheitert das Schreiben, bleibt der Merker im Speicher und es wirft EinstellungsFehler."""
        if self.daten["automation_von_app_aus"] is bool(von_app_aus):
            return
        self.daten["automation_von_app_aus"] = bool(von_app_aus)
        self.speichern()

    def bestaetigung_speichern(self, roh: Any, jetzt: float, version: str) -> None:
        """Merkt sich nur vollständige Angaben, mit Datum und Version. Bestehendes bleibt."""
        if not isinstance(roh, dict):
            return
        stempel = {"datum": datetime.fromtimestamp(jetzt).astimezone().isoformat(timespec="seconds"),
                   "zeit": jetzt, "version": version}
        neu = dict(self.bestaetigung)
        if all(roh.get(p) is True for p in BESTAETIGUNG_PUNKTE):
            neu["betrieb"] = {p: True for p in BESTAETIGUNG_PUNKTE} | stempel
        if roh.get(OHNE_WATCHDOG) is True:
            neu[OHNE_WATCHDOG] = stempel
        if neu != self.bestaetigung:
            self.daten["betrieb_bestaetigung"] = neu
            self.speichern()

    def betrieb_addieren(self, sekunden: float) -> None:
        self.daten["betrieb_s"] = float(self.daten["betrieb_s"]) + sekunden

    # ---- Benachrichtigungen und Reinigung -------------------------------

    def benachrichtigungen_setzen(self, roh: Any) -> dict[str, list[str]]:
        try:
            zuordnung = zuordnung_pruefen(roh)
        except ZuordnungFehler as fehler:
            raise EinstellungsFehler(str(fehler)) from fehler
        self.daten["benachrichtigungen"] = zuordnung
        self.speichern()
        return zuordnung

    def _wartungsstand_setzen(self, stand: wartung.Stand) -> None:
        self.daten["wartung"] |= {"basis_s": stand.basis_s, "letzte_reinigung": stand.letzte_reinigung,
                                  "erinnert": stand.erinnert}

    def reinigung_bestaetigen(self, jetzt: float) -> None:
        self._wartungsstand_setzen(wartung.gereinigt(float(self.daten["betrieb_s"]), jetzt))
        self.speichern()

    def erinnerung_merken(self, jetzt: float) -> None:
        """Sofort auf die Platte: Nach einem Neustart soll die Erinnerung nicht noch einmal kommen."""
        self._wartungsstand_setzen(wartung.erinnert_am(self.wartungsstand, jetzt))
        try:
            self.speichern()
        except EinstellungsFehler:
            pass    # bereits geloggt, der Merker bleibt im Speicher
