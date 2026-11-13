"""Einstieg der App: lesen, rechnen, protokollieren und im Betrieb die Klappe stellen.

Alle Läufe gehen durch eine Warteschlange und damit nacheinander, wie `mode: queued`
der Automation. Im Lauschen werden Ergebnis.aktionen nur protokolliert und es wird nichts
geschaltet. Im Betrieb führt der Regelstand `neu`; sicherer Zustand ist immer Klappe 100 %.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from collections import deque
from datetime import datetime
import logging
import math
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from anbindung.anlagenprofil import PUMPE_ROLLEN, pumpe_laeuft, rolle_passt, zaehler_entitaeten, zaehler_nach_kwh
from anbindung.ansicht import NACHLEGEN_TEXT, QUITTIEREN_LAUSCHEN_TEXT, nachlegen_moeglich
from anbindung.ansicht import amortisation_zeigen as ansicht_amortisation
from anbindung.ausgaenge import Ausgaenge, NEUSTART, zugewiesene_ausgaenge
from anbindung.benachrichtigung import Benachrichtiger
from anbindung import einmessen as einmess
from anbindung.betrieb import Punkt, offene_punkte, zustand_laden, zustand_sichern
from anbindung.bereitstellung import integration_bereitstellen
from anbindung.buchfuehrung import Buchfuehrung, Messung
from anbindung.eingaenge import Eingaenge, Entitaeten, als_zahl
from anbindung.einstellungen import AUSGANG, BETRIEB, LAUSCHEN, ROLLEN, ZUSATZ_ROLLEN, EinstellungsFehler, Speicher
from anbindung.ha_client import HaClient, VerbindungWeg
from anbindung.schatten import (
    STATUS_FEHLER,
    STATUS_GESTOPPT,
    STATUS_OK,
    STATUS_START,
    STATUS_WARTET,
    Schatten,
    Verlauf,
)
from anbindung.uebertemperatur import UebertempWaechter, wasser_gueltig
from anbindung.web import Webserver
from kern import wartung
from kern import wasser as kern_wasser
from kern import abbrand_dauer
from kern.wirtschaft import energie_nach_kwh, leistung_nach_kw
from kern.einmessen import FERTIG, FEUER, KALT, KEIN_FEUER, NACHLEGEN, Ereignis
from kern.phasen import TITEL_ANHEIZFEHLER, TITEL_STOERUNG
from kern.signale import t_gueltig
from kern.typen import (ABBRAND, ABKUEHLEN, ANHEIZEN, ANHEIZFEHLER, AUSBRAND, GLUT, NEU, PARITAET, QUITTIEREN, SENSOR_WEG, STANDBY, STOERUNG,
                        TICK, Parameter, Zustand)

log = logging.getLogger("kamin_abbrand")

AUFLAGE_SPERRE_S = 600.0    # Tür öffnen so kurz nach einer Auflage ist Schüren, kein Nachlegen

TAKT_PARITAET_S = 30.0
TAKT_NEU_S = 5.0
WACHE_INTERVALL_S = 1.0
OPTIONEN_PFAD = Path("/data/options.json")
VERLAUF_ORDNER = Path("/config/verlauf")
EINSTELLUNGEN_PFAD = Path("/config/einstellungen.json")
START_OHNE_HELFER_NACH_S = 60.0
_WECHSEL = "_wechsel"
_AUFTRAG = "_auftrag"
_AUTOMATION = "_automation"         # Konkurrierende Automation abgleichen: im Betrieb aus, sonst nach Merker wieder an
_KLAPPE_FREMD = "_klappe_fremd"     # Jemand anderes hat Klappe Soll beschrieben: einmal neu senden
# Wie lange eine vorübergehende Bedingung fehlen darf, bevor die App zurückfällt.
# Eine kurze Funkpause soll nicht jedes Mal den Betrieb beenden; danach gilt Klappe 100 %.
BEDINGUNG_KARENZ_S = 30.0
SICHER_TIMEOUT_S = 6.0          # Der Supervisor beendet nach 10 s hart
RUECKFALL_OEFFNEN_S = 600.0     # So lange versucht die App nach einem Rückfall noch, die Klappe zu öffnen
EINMESS_FEHLER_MAX = 3          # Fehler in Folge beim Einmessen, dann übernimmt die Automatik
AUFTRAG_TIMEOUT_S = 20.0
ZUSTAND_SICHERN_S = 30.0
_BETRIEB_PHASEN = (ANHEIZEN, ABBRAND, AUSBRAND)
_BETRIEB_SPEICHERN_S = 300.0
AUTOMATION_WARTEN_S = 10.0      # So lange wartet der Wechsel auf Betrieb, bis die Automation „off“ meldet
FREMD_FENSTER_S = 600.0         # Fremdes Schreiben auf Klappe Soll: mehr als FREMD_VIELE in diesem Fenster
FREMD_VIELE = 20
WASSER_ERINNERUNG_S = 900.0
# Nur diese Kern-Meldungen sind Störungen. Die übrigen (Glutphase, Wartung, „Messwerte wieder da“)
# gehören zum Normalbetrieb und gehen nicht als Störung raus.
_STOERUNG_TITEL = frozenset({TITEL_STOERUNG, TITEL_ANHEIZFEHLER})

BEIDE = (PARITAET, NEU)


def naechstes_raster(jetzt: float, takt_s: float) -> float:
    """Nächster Rasterpunkt echt nach `jetzt`. 30 s ergibt :00/:30 wie time_pattern."""
    return (math.floor(jetzt / takt_s) + 1) * takt_s


def lade_optionen(pfad: Path) -> dict:
    with open(pfad, encoding="utf-8") as datei:
        return json.load(datei)


def entitaeten_aus(optionen: dict) -> Entitaeten:
    bekannt = {f.name for f in dataclasses.fields(Entitaeten)}
    werte = {k: v for k, v in (optionen.get("entitaeten") or {}).items() if k in bekannt and v}
    return Entitaeten(**werte)


def steuerung_aus_kern(
    parameter: Parameter, zustand: Zustand, regelstand: str, takt_s: float
) -> Any:
    from kern.steuerung import Steuerung
    return Steuerung(parameter, zustand, regelstand=regelstand, takt_s=takt_s)


class App:
    def __init__(
        self,
        client_fabrik: Callable[..., HaClient],
        eingaenge: Eingaenge,
        verlauf: Verlauf,
        *,
        steuerung_fabrik: Callable[..., Any],
        uhr: Callable[[], float] = time.time,
        takt_paritaet_s: float = TAKT_PARITAET_S,
        takt_neu_s: float = TAKT_NEU_S,
        wache_intervall_s: float = WACHE_INTERVALL_S,
        speicher: Optional[Speicher] = None,
        start_ohne_helfer_nach_s: float = START_OHNE_HELFER_NACH_S,
        karenz_s: float = BEDINGUNG_KARENZ_S,
        automation_warten_s: float = AUTOMATION_WARTEN_S,
    ) -> None:
        self.eingaenge = eingaenge
        self.speicher = speicher
        self.parameter_neu = dataclasses.replace(eingaenge.parameter)
        self.dauer: Optional[abbrand_dauer.DauerStand] = None
        self.start_zeit = uhr()
        self.letzte: dict[str, tuple[Any, float]] = {}
        self.letzte_eingaben: Any = None
        self.meldungen: deque[dict] = deque(maxlen=40)
        self.autotune_vorschlag: Any = None
        self._start_ohne_helfer_nach_s = start_ohne_helfer_nach_s
        self._erster_kontakt: Optional[float] = None
        self._betrieb_gespeichert = 0.0
        self.verlauf = verlauf
        self._steuerung_fabrik = steuerung_fabrik
        self._uhr = uhr
        self._takte = {PARITAET: takt_paritaet_s, NEU: takt_neu_s}
        self._wache_intervall_s = wache_intervall_s

        self.client = client_fabrik(
            bei_zustaenden=self._bei_zustaenden,
            bei_ereignis=self._bei_ereignis,
            bei_trennung=self._bei_trennung,
        )
        self.schatten = Schatten(self.client.setze_schattensensor)
        self.benachrichtiger = Benachrichtiger(
            lambda: self.speicher.benachrichtigungen if self.speicher else {},
            self.client.sende_notify, uhr)
        self.wasserwache = kern_wasser.Wasserwache()
        self.wasser_ergebnis: Optional[kern_wasser.Ergebnis] = None
        self._wasser_gemeldet: dict[str, tuple[str, float]] = {}
        self.uebertemperatur = UebertempWaechter()
        self.buchfuehrung: Optional[Buchfuehrung] = (
            Buchfuehrung(speicher.pfad.parent / "wirtschaft.json", uhr) if speicher is not None else None)
        # Nach dem Start gilt die Tür als geöffnet: Ein Neustart mitten im Anheizen verliert keine Auflage.
        self._tuer_seit_auflage = True
        self._letzte_auflage: Optional[float] = None
        self.steuerungen: dict[str, Any] = {}
        self._fehler: dict[str, str] = {}
        self._uebernommen = False
        self.ohne_helfer = False
        self.integration_stand: dict = {}
        self.ausgaenge = Ausgaenge(
            lambda *a: self.client.schalte(*a),
            lambda: self.speicher.rollen if self.speicher else {},
            self.klappe_ist_prozent, self._kennlinie, self._attribute, self._ausgang_stoerung, uhr)
        self._karenz_s = karenz_s
        self._automation_warten_s = automation_warten_s
        self._automation_an_seit: Optional[float] = None
        self._fremd_zeiten: deque[float] = deque()
        self._fremd_gemeldet = False
        self._fremd_viele_gemeldet = False
        self._betrieb = False
        self._anlaufschutz = False
        self._weg_seit: dict[str, float] = {}
        self._getrennt_seit: Optional[float] = None
        self._oeffnen_bis: Optional[float] = None
        self._zustand_gesichert = 0.0
        self.rueckfall: Optional[dict] = None
        self._warteschlange: asyncio.Queue[tuple[str, tuple]] = asyncio.Queue()
        self.einmess_ablage: Optional[einmess.Ablage] = (
            einmess.Ablage(speicher.pfad.parent / einmess.ORDNER) if speicher is not None else None)
        self.einmessung: Optional[einmess.Aufzeichnung] = None
        self._einmess_fehler = 0
        self._einmess_tuer_auf = False
        self._einmess_tuer_gemeldet = False
        self._einmess_abschluss: set[str] = set()
        self._einmess_aufgaben: set[asyncio.Task] = set()
        if self.einmess_ablage is not None:
            # Keine Handregelung über einen Neustart hinweg: Was noch läuft, ist beendet.
            try:
                self.einmess_ablage.verwaiste_beenden()
            except Exception:
                log.exception("Laufende Aufzeichnungen nach dem Neustart nicht beendet")
        if speicher is not None and speicher.modus == BETRIEB:
            # Der gespeicherte Modus gilt erst nach der Prüfung im ersten Lauf (_betrieb_pruefen).
            log.warning("Neustart im Betrieb, die Bedingungen werden erneut geprüft")
            self._betrieb_aufnehmen()

    # ---- Rückrufe des Clients -----------------------------------------

    async def _bei_zustaenden(self, zustaende: list[dict]) -> None:
        jetzt = self._uhr()
        self.eingaenge.aufbauen(zustaende, jetzt)
        self.schatten.vergessen()
        self.ausgaenge.vergessen()
        if self._erster_kontakt is None:
            self._erster_kontakt = jetzt
        if not self._uebernommen:
            self._uebernehmen(jetzt)
        await self.schatten.status(
            STATUS_START if self._uebernommen else STATUS_WARTET, jetzt
        )
        if self._automation_eid() and (self._betrieb or self.speicher.automation_von_app_aus):
            # Nach dem Start oder einer Lücke: im Betrieb muss sie aus sein, im Lauschen wieder an.
            self._warteschlange.put_nowait((_AUTOMATION, ()))

    async def _bei_ereignis(self, daten: dict) -> None:
        self._einmessen_tuer(daten)
        ausloeser_liste = self.eingaenge.ereignis(daten, self._uhr())
        self._klappe_fremd_pruefen(daten)
        eid = daten.get("entity_id")
        if (self._betrieb and eid and eid == self._automation_eid()
                and (daten.get("new_state") or {}).get("state") == "on"):
            self._warteschlange.put_nowait((_AUTOMATION, ()))
        for ausloeser in ausloeser_liste:
            self._warteschlange.put_nowait((ausloeser, BEIDE))

    async def _bei_trennung(self) -> None:
        log.warning("Verbindung getrennt, keine Läufe bis zum Wiederverbinden")
        if self._betrieb and self._getrennt_seit is None:
            self._getrennt_seit = self._uhr()

    def _uebernehmen(self, jetzt: float) -> None:
        """Einmalig beim ersten Start. Nach einem Reconnect rechnen die Instanzen weiter."""
        if self._betrieb:
            self._im_betrieb_uebernehmen(jetzt)
            return
        if self.eingaenge.startzustand(jetzt) is None:
            log.warning("Phase noch nicht lesbar, warte auf Home Assistant")
            return
        self._instanzen_bauen(self.eingaenge.startzustand)
        log.info("Zustand aus Home Assistant übernommen: %s", self.eingaenge.startzustand(jetzt))

    def _im_betrieb_uebernehmen(self, jetzt: float) -> None:
        """Die Helfer der Automation stehen im Betrieb still und taugen nicht als Startzustand von neu."""
        gesichert = zustand_laden(self._zustand_pfad, jetzt)
        if gesichert is None:
            # Startlogik des Kerns: Standby. Ein heißer Ofen darf dabei nicht zugehen, siehe _stellen.
            self._anlaufschutz = True
            log.warning("Kein gültiger gesicherter Zustand, Start in Standby mit offener Klappe bis zum Beweis des Gegenteils")
        else:
            log.info("Gesicherter Zustand übernommen: %s", gesichert)
        ein = self.eingaenge
        self._instanzen_bauen(lambda j: ein.startzustand(j) or ein.startzustand_leer(j),
                              lambda j: gesichert or ein.startzustand_leer(j))

    def _ohne_helfer_starten(self, jetzt: float) -> None:
        """Andere Anlage ohne die Helfer der Live-Automation: Die App beginnt im Standby."""
        if self._uebernommen or self._erster_kontakt is None or not self.eingaenge.phase_fehlt():
            return
        if jetzt - self._erster_kontakt < self._start_ohne_helfer_nach_s:
            return
        log.warning("Keine Helfer der Live-Automation gefunden, die App startet im Standby")
        self.ohne_helfer = True
        self._instanzen_bauen(self.eingaenge.startzustand_leer)

    def _instanzen_bauen(self, start: Callable[[float], Any],
                         start_neu: Optional[Callable[[float], Any]] = None) -> None:
        for regelstand in BEIDE:
            # Je Instanz ein eigener Zustand. paritaet rechnet mit den Live-Helfern,
            # neu zusätzlich mit den eigenen Werten aus den Einstellungen.
            zustand = (start_neu if regelstand == NEU and start_neu else start)(self._uhr())
            parameter = self.eingaenge.parameter if regelstand == PARITAET else self.parameter_neu
            try:
                self.steuerungen[regelstand] = self._steuerung_fabrik(
                    parameter, zustand, regelstand, self._takte[regelstand]
                )
            except NotImplementedError:
                if regelstand == PARITAET:
                    raise
                log.info("Regelstand %s wird vom Kern noch nicht angeboten", regelstand)
        self._uebernommen = True

    @property
    def fuehrender_regelstand(self) -> str:
        """Nur dieser Regelstand löst Nachrichten aus: im Betrieb `neu`, sonst `paritaet`."""
        return NEU if self._betrieb else PARITAET

    @property
    def im_betrieb(self) -> bool:
        return self._betrieb

    @property
    def modus(self) -> str:
        return BETRIEB if self._betrieb else LAUSCHEN

    @property
    def anlaufschutz(self) -> bool:
        return self._anlaufschutz

    @property
    def _zustand_pfad(self) -> Path:
        return self.speicher.pfad.parent / "betrieb_zustand.json"

    def klappe_ist_prozent(self, eid: Optional[str]) -> Optional[float]:
        """Iststellung in %, Volt oder mA nach der Rückmelde-Kennlinie im Anlagenprofil umgerechnet."""
        if self.speicher is None:
            return self.eingaenge.stellung(eid)
        return self.eingaenge.klappe_ist(eid, self.speicher.anlagenprofil)

    def _kennlinie(self) -> tuple[bool, float]:
        """Wie die Stellung am Ausgang anliegt. Gibt es die Helfer der Automation, gelten deren Werte."""
        profil = self.speicher.anlagenprofil
        praefix = self.eingaenge.praefix
        invertiert = self.eingaenge.wert(f"input_boolean.{praefix}_klappe_invertiert")
        y0 = self.eingaenge.zahl(f"input_number.{praefix}_aktor_y0")
        return (invertiert == "on" if invertiert in ("on", "off") else profil.klappe_invertiert,
                profil.klappe_y0 if y0 is None else y0)

    def _attribute(self, eid: str) -> dict:
        eintrag = self.eingaenge.eintrag(eid)
        return eintrag.attribute if eintrag else {}

    def _ausgang_stoerung(self, text: str) -> None:
        log.error("Störung am Ausgang: %s", text)
        self.meldungen.appendleft({"zeit": self._uhr(), "text": text})
        self.benachrichtiger.melden("betrieb", text)

    def parameter_neu_auffrischen(self) -> None:
        eigene = self.speicher.parameter_neu if self.speicher else {}
        for feld in Parameter.feldnamen():
            setattr(self.parameter_neu, feld, eigene.get(feld, getattr(self.eingaenge.parameter, feld)))

    def tuerkontakt_vorhanden(self) -> bool:
        return self.speicher.tuerkontakt_vorhanden if self.speicher else True

    def zusatz_entitaeten(self) -> list[str]:
        """Was die Oberfläche anzeigt, ohne dass der Kern es braucht."""
        if not self.speicher:
            return []
        profil = self.speicher.anlagenprofil
        rollen = [eid for rolle in ROLLEN
                  if (eid := self.speicher.rollen.get(rolle.schluessel))
                  and (rolle.art == AUSGANG or rolle.schluessel == "klappe_ist"
                       or rolle.schluessel == "konkurrierende_automation"
                       or (rolle.schluessel in ZUSATZ_ROLLEN and rolle_passt(rolle.schluessel, profil)))]
        return rollen + zaehler_entitaeten(profil) + self._dauer_helfer()

    def _dauer_helfer(self) -> list[str]:
        """Helfer der Live-Automation, aus denen die bisherigen Laufzeiten einmalig übernommen werden."""
        p = self.eingaenge.praefix
        return [f"counter.{p}_anzahl_gesamt", f"counter.{p}_anzahl_heute",
                f"input_number.{p}_dauer_summe_gesamt", f"input_number.{p}_dauer_summe_heute",
                f"input_number.{p}_dauer_letzte", f"input_boolean.{p}_dauer_lauft",
                f"input_datetime.{p}_dauer_start", f"input_datetime.{p}_dauer_kandidat",
                f"input_boolean.{p}_klappe_stoerung"]

    def zuweisung_uebernehmen(self) -> None:
        """Wirkt ohne App-Neustart, der Wechsel läuft zwischen zwei Läufen."""
        self._warteschlange.put_nowait((_WECHSEL, ()))

    async def _wechseln(self) -> None:
        if self.speicher is None:
            return
        zustaende = await self.client.hole_zustaende()
        self.eingaenge.tauschen(self.speicher.entitaeten(), self.zusatz_entitaeten(), zustaende, self._uhr())
        if self.buchfuehrung is not None:
            # Ein Zähler, der nicht mehr gelesen wird, beginnt bei erneuter Zuweisung neu.
            self.buchfuehrung.verwaiste_zaehler_entfernen({"waerme"} | {f"energie:{k}" for k in self._messung().energie_kwh})
        log.info("Neue Zuweisung der Ein- und Ausgänge übernommen")

    # ---- Läufe --------------------------------------------------------

    async def _ausfuehren(self, ausloeser: str, regelstaende: tuple) -> None:
        if ausloeser == _AUFTRAG:
            await self._auftrag_erledigen(*regelstaende)
            return
        if ausloeser == _WECHSEL:
            if self.client.verbunden:
                await self._wechseln()
            return
        if ausloeser == _AUTOMATION:
            if self.client.verbunden:
                await self._automation_abgleichen(self._uhr())
            return
        if ausloeser == _KLAPPE_FREMD:
            if self._betrieb and self.client.verbunden and self.ausgaenge.gesendet is not None:
                # Das aktuelle Soll, auch beim Einmessen (100 %) oder im Anlaufschutz.
                await self.ausgaenge.klappe(self.ausgaenge.gesendet, self._uhr(), zwingen=True)
            return
        if not self._uebernommen and self.client.verbunden:
            self._ohne_helfer_starten(self._uhr())
        if not self._uebernommen or not self.client.verbunden:
            log.debug("Lauf %s übersprungen", ausloeser)
            return
        if self._oeffnen_bis is not None:
            await self._oeffnen_nachholen(self._uhr())
        self._hand_pruefen(self._uhr())
        if ausloeser == TICK and PARITAET in regelstaende:
            await self._abgleichen()

        for regelstand in regelstaende:
            steuerung = self.steuerungen.get(regelstand)
            if steuerung is None:
                continue
            jetzt = self._uhr()
            eingaben = self.eingaenge.eingaben(jetzt, ausloeser)
            if regelstand == NEU:
                self.parameter_neu_auffrischen()
                vorhanden = self.tuerkontakt_vorhanden()
                eingaben = dataclasses.replace(
                    eingaben, tuerkontakt_vorhanden=vorhanden,
                    tuer_offen=eingaben.tuer_offen if vorhanden else False,
                    # Handbetrieb der App wirkt nur hier (Regelstand neu, nur im Betrieb). Beim Einmessen
                    # hält das den I-Anteil an, die Phasen laufen weiter, gestellt wird in _stellen.
                    handbetrieb=eingaben.handbetrieb or self.hand_aktiv(jetzt) or self.einmessen_handregelung,
                )
            try:
                ergebnis = steuerung.schritt(eingaben)
            except NotImplementedError:
                if regelstand == PARITAET:
                    self._fehler_merken(regelstand, eingaben, "NotImplementedError")
                else:
                    log.info("Regelstand %s wird vom Kern noch nicht angeboten", regelstand)
                    del self.steuerungen[regelstand]
                    if self._betrieb:
                        await self._zurueckfallen("Der Regelstand „neu“ liefert keine Ergebnisse.")
                continue
            except Exception as fehler:
                self._fehler_merken(regelstand, eingaben, repr(fehler))
                if regelstand == NEU and self._betrieb:
                    await self._zurueckfallen(f"Fehler im Regelkern: {fehler!r}")
                continue
            self._fehler.pop(regelstand, None)
            if regelstand == NEU and self._betrieb and self.einmessen_handregelung:
                ergebnis = self._einmessen_ergebnis(steuerung, ergebnis)
            live = self._wirklicher_zustand(jetzt)
            if regelstand == self.fuehrender_regelstand:
                self._dauer_verfolgen(live or ergebnis.zustand, jetzt)
                self._tuer_merken(eingaben.tuer_offen, jetzt)
            self.verlauf.schreiben(regelstand, eingaben, ergebnis, live=live,
                                   anzeige=self._anzeige())
            self.letzte[regelstand] = (ergebnis, jetzt)
            self.letzte_eingaben = eingaben
            if regelstand == NEU and self._betrieb:
                await self._stellen(ergebnis, eingaben, jetzt)
                if ausloeser == TICK and self.einmessung is not None:
                    await self._einmessen_schritt(eingaben, ergebnis, jetzt)
            if regelstand == PARITAET and ausloeser == TICK:
                self._betrieb_zaehlen(live or ergebnis.zustand, jetzt)
            for aktion in ergebnis.aktionen:
                if regelstand == NEU and self._betrieb:
                    await self.ausgaenge.aktion(aktion)
                else:
                    log.info("[%s] Aktion nur protokolliert: %s", regelstand, aktion)
            for meldung in ergebnis.meldungen:
                log.info("[%s] Meldung nur protokolliert: %s: %s", regelstand, meldung.titel, meldung.text)
                if regelstand == self.fuehrender_regelstand:
                    self.meldungen.appendleft({"zeit": jetzt, "text": f"{meldung.titel}: {meldung.text}"})
                if regelstand == self.fuehrender_regelstand and meldung.titel in _STOERUNG_TITEL:
                    self.benachrichtiger.melden("stoerung", meldung.text, meldung.titel)
            if ergebnis.uebergang:
                log.info("[%s] %s → %s (%s)", regelstand, *ergebnis.uebergang, ergebnis.regel)
                if regelstand == self.fuehrender_regelstand:
                    self.meldungen.appendleft({"zeit": jetzt, "text": "{} → {}".format(*ergebnis.uebergang)})
                if (regelstand == self.fuehrender_regelstand and ergebnis.uebergang == (ANHEIZEN, ABBRAND)
                        and self.buchfuehrung is not None and self._auflage_erlaubt()):
                    self._auflage_buchen(jetzt)
                if (regelstand == self.fuehrender_regelstand and ergebnis.uebergang[1] == GLUT
                        and self.speicher is not None and nachlegen_moeglich(self)):
                    self.benachrichtiger.melden("nachlegen", NACHLEGEN_TEXT)
            await self.schatten.ergebnis(regelstand, ergebnis, jetzt)

        if ausloeser == TICK and PARITAET in regelstaende:
            self._buchen(self._uhr())
        await self._betrieb_pruefen(self._uhr())
        await self._wartung_pruefen(self._uhr())
        await self._wasser_pruefen(self._uhr())
        self._uebertemperatur_pruefen(self._uhr())
        await self._wirtschaft_pruefen(self._uhr())
        await self._status_schreiben()

    # ---- Betrieb ------------------------------------------------------

    def _wirklicher_zustand(self, jetzt: float) -> Optional[Zustand]:
        """Was der Ofen wirklich tut: im Lauschen die Helfer der Automation, im Betrieb neu."""
        if not self._betrieb:
            return self.eingaenge.startzustand(jetzt)
        neu = self.steuerungen.get(NEU)
        if neu is None:
            return None
        gesendet = self.ausgaenge.gesendet
        return neu.zustand if gesendet is None else dataclasses.replace(neu.zustand, klappe_soll=gesendet)

    async def _stellen(self, ergebnis: Any, eingaben: Any, jetzt: float) -> None:
        soll = ergebnis.klappe
        if self._anlaufschutz:
            # Der Kern startet nach Standby und ginge bis t_abkuehlen ganz zu. Bis er das Feuer
            # einordnet oder der Ofen sicher kalt ist, bleibt die Klappe deshalb offen.
            kalt = t_gueltig(eingaben.t) and eingaben.t < self.parameter_neu.t_kalt
            if ergebnis.zustand.phase != STANDBY or kalt:
                self._anlaufschutz = False
                log.info("Anlaufschutz beendet (Phase %s, %s °C)", ergebnis.zustand.phase, eingaben.t)
            else:
                soll = 100
        if self.einmessen_handregelung:
            # Einmessen: Geregelt wird von Hand an den Ofenklappen. Übertemperatur und heißes
            # Wasser drosseln hier nicht, sie werden nur gemeldet (Wunsch des Nutzers).
            soll = 100
        await self.ausgaenge.klappe(soll, jetzt)
        if eingaben.ausloeser == TICK:
            await self.ausgaenge.heartbeat(jetzt)
        if ergebnis.uebergang or jetzt - self._zustand_gesichert >= ZUSTAND_SICHERN_S:
            self._zustand_gesichert = jetzt
            zustand_sichern(self._zustand_pfad, ergebnis.zustand, jetzt)

    def _betrieb_aufnehmen(self) -> None:
        self._betrieb = True
        self._weg_seit.clear()
        self._getrennt_seit = None
        self._oeffnen_bis = None
        self.ausgaenge.zuruecksetzen()
        self.client.betrieb_freigeben(zugewiesene_ausgaenge(self.speicher.rollen))
        self._automation_an_seit = None
        self._fremd_zeiten.clear()
        self._fremd_gemeldet = self._fremd_viele_gemeldet = False

    async def _betrieb_pruefen(self, jetzt: float) -> None:
        """Fällt eine Bedingung im Lauf weg, geht die App zurück auf Lauschen und öffnet die Klappe."""
        if not self._betrieb:
            return
        if self._getrennt_seit is not None:
            weg = jetzt - self._getrennt_seit
            self._getrennt_seit = None
            if weg >= self._karenz_s:
                await self._zurueckfallen(f"Die Verbindung zu Home Assistant war {weg:.0f} s unterbrochen.")
                return
        await self._automation_im_betrieb(jetzt)
        offen = offene_punkte(self, jetzt)
        schluessel = {p.schluessel for p in offen}
        self._weg_seit = {k: v for k, v in self._weg_seit.items() if k in schluessel}
        for punkt in offen:
            if (punkt.schluessel == "automation" and self._automation_an_seit is not None
                    and jetzt - self._automation_an_seit < self._karenz_s):
                continue    # Die App hat sie eben wieder ausgeschaltet, nach der Karenz gilt die harte Prüfung.
            if punkt.hart or jetzt - self._weg_seit.setdefault(punkt.schluessel, jetzt) >= self._karenz_s:
                await self._zurueckfallen(punkt.text)
                return

    async def _klappe_sicher_oeffnen(self) -> bool:
        try:
            async with asyncio.timeout(SICHER_TIMEOUT_S):
                return await self.ausgaenge.sicher_oeffnen()
        except asyncio.TimeoutError:
            log.error("Klappe innerhalb von %.0f s nicht geöffnet", SICHER_TIMEOUT_S)
            return False

    async def _betrieb_verlassen(self) -> bool:
        """Klappe 100 %, Modus Lauschen. Reicht der Versuch nicht, bleibt nur der Weg zur Klappe offen."""
        jetzt = self._uhr()
        self._betrieb = False
        self._hand_aus(jetzt, "Handbetrieb beendet, weil die App auf Lauschen geht.")
        self._anlaufschutz = False
        self._weg_seit.clear()
        self._getrennt_seit = None
        try:
            self.speicher.modus_setzen(LAUSCHEN)
        except EinstellungsFehler:
            log.error("Modus Lauschen nicht gespeichert, die Prüfung nach einem Neustart greift")
        self.verlauf.aenderung(jetzt, "modus", BETRIEB, LAUSCHEN)
        klappe = self.speicher.rollen.get("ausgang_klappe")
        offen = await self._klappe_sicher_oeffnen()
        if offen or not klappe:
            self.client.betrieb_sperren()
        else:
            self.client.betrieb_freigeben([klappe])
            self._oeffnen_bis = jetzt + RUECKFALL_OEFFNEN_S
        self._einmessen_beenden(self._uhr(), einmess.ENDE_LAUSCHEN)
        try:
            await self._automation_zurueckgeben()
        except Exception:
            log.exception("Konkurrierende Automation nicht wieder eingeschaltet")
        return offen

    # ---- Konkurrierende Automation ----------------------------------
    # Die App schaltet sie beim Wechsel auf Betrieb aus und merkt sich das (Speicher.automation_von_app_aus).
    # Beim Verlassen des Betriebs, auch bei jedem Rückfall, schaltet sie sie wieder ein.

    def _automation_eid(self) -> Optional[str]:
        return self.speicher.rollen.get("konkurrierende_automation") if self.speicher is not None else None

    async def _automation_schalten(self, eid: str, an: bool) -> None:
        self.client.automation_freigeben(eid)
        await self.client.automation_schalten(eid, an)

    def _automation_merken(self, von_app_aus: bool) -> None:
        try:
            self.speicher.automation_merken(von_app_aus)
        except EinstellungsFehler:
            log.error("Merker der Automation nicht gespeichert, er gilt bis zum Neustart")

    def _betrieb_melden(self, text: str, jetzt: float) -> None:
        log.warning(text)
        self.meldungen.appendleft({"zeit": jetzt, "text": text})
        self.benachrichtiger.melden("betrieb", text)

    async def _automation_vor_betrieb(self) -> None:
        """Ist sie an, wird sie ausgeschaltet. Meldet sie nicht rechtzeitig „off“, bleibt die App im Lauschen
        (EinstellungsFehler) und die Automation wird wieder eingeschaltet, damit die Klappe nicht ohne Regelung bleibt."""
        eid = self._automation_eid()
        if not eid or self.eingaenge.wert(eid) != "on":
            return
        try:
            await self._automation_schalten(eid, False)
            async with asyncio.timeout(self._automation_warten_s):
                while self.eingaenge.wert(eid) != "off":
                    await asyncio.sleep(0.05)
        except Exception as fehler:
            log.error("Automation %s nicht ausgeschaltet: %r", eid, fehler)
            try:
                await self._automation_schalten(eid, True)
            except Exception as zweiter:
                log.error("Automation %s nicht wieder eingeschaltet: %r", eid, zweiter)
            raise EinstellungsFehler(f"Die Automation {eid} ließ sich nicht ausschalten. "
                                     "Die App bleibt im Lauschen.") from fehler
        self._automation_merken(True)
        log.warning("Automation %s ausgeschaltet, die App übernimmt die Klappe", eid)

    async def _automation_im_betrieb(self, jetzt: float) -> None:
        """Im Betrieb wieder eingeschaltet: einmal ausschalten und melden. Bleibt sie an, gilt nach der
        Karenz die harte Prüfung in offene_punkte und die App fällt zurück."""
        eid = self._automation_eid()
        if not eid or self.eingaenge.wert(eid) != "on":
            self._automation_an_seit = None
            return
        if self._automation_an_seit is not None:
            return
        self._automation_an_seit = jetzt
        self._betrieb_melden(f"Die konkurrierende Automation {eid} ist im Betrieb eingeschaltet. "
                             "Die App schaltet sie wieder aus.", jetzt)
        try:
            await self._automation_schalten(eid, False)
        except Exception as fehler:
            log.error("Automation %s nicht ausgeschaltet: %r", eid, fehler)
            return
        self._automation_merken(True)

    async def _automation_zurueckgeben(self) -> None:
        """Wieder einschalten, wenn die App sie ausgeschaltet hat. Ein Fehler wird gemeldet und hält nichts auf;
        der Merker bleibt dann stehen und beim nächsten Verbinden versucht die App es erneut."""
        self._automation_an_seit = None
        if self.speicher is None or not self.speicher.automation_von_app_aus:
            return
        eid = self._automation_eid()
        jetzt = self._uhr()
        if eid:
            try:
                await self._automation_schalten(eid, True)
            except Exception as fehler:
                log.error("Automation %s nicht eingeschaltet: %r", eid, fehler)
                self._betrieb_melden(f"Die Automation {eid} ließ sich nicht wieder einschalten. "
                                     "Bitte in Home Assistant prüfen.", jetzt)
                return
            log.warning("Automation %s wieder eingeschaltet", eid)
            self.meldungen.appendleft({"zeit": jetzt, "text": f"Die Automation {eid} ist wieder eingeschaltet."})
        self._automation_merken(False)

    async def _automation_abgleichen(self, jetzt: float) -> None:
        if self._betrieb:
            await self._automation_im_betrieb(jetzt)
        else:
            await self._automation_zurueckgeben()

    def _klappe_fremd_pruefen(self, daten: dict) -> None:
        """Schreibt jemand anderes auf Klappe Soll, sendet die App ihr Soll einmal je Ereignis neu."""
        if not self._betrieb or self.speicher is None:
            return
        eid = daten.get("entity_id")
        jetzt = self._uhr()
        wert = self.ausgaenge.fremder_wert(eid, daten.get("new_state"), jetzt)
        if wert is None:
            return
        log.warning("%s von außen auf %.1f gesetzt, die App sendet %s %% neu", eid, wert, self.ausgaenge.gesendet)
        self._warteschlange.put_nowait((_KLAPPE_FREMD, ()))
        zeiten = self._fremd_zeiten
        zeiten.append(jetzt)
        while zeiten and jetzt - zeiten[0] > FREMD_FENSTER_S:
            zeiten.popleft()
        if not self._fremd_gemeldet:
            self._fremd_gemeldet = True
            self._betrieb_melden(f"Klappe Soll wird von außen überschrieben ({eid}). "
                                 "Prüfe Automationen, die diesen Helfer schreiben.", jetzt)
        if len(zeiten) > FREMD_VIELE and not self._fremd_viele_gemeldet:
            self._fremd_viele_gemeldet = True
            rat = ("Prüfe, ob die richtige konkurrierende Automation zugewiesen ist." if self._automation_eid() else
                   "Weise unter Ein-/Ausgänge die konkurrierende Automation zu, dann schaltet die App sie im Betrieb aus.")
            self._betrieb_melden(f"Klappe Soll wurde in {FREMD_FENSTER_S / 60:.0f} min mehr als {FREMD_VIELE}-mal "
                                 f"von außen überschrieben ({eid}). {rat}", jetzt)

    # ---- Handbetrieb -------------------------------------------------

    def hand_aktiv(self, jetzt: float) -> bool:
        """Handbetrieb der App: nur im Betrieb und bis zur gespeicherten Endzeit."""
        return bool(self._betrieb and self.speicher is not None and self.speicher.hand_aktiv(jetzt))

    def hand_setzen(self, an: bool, dauer_min: Optional[float] = None) -> Optional[float]:
        """Startet, verlängert oder beendet den Handbetrieb. Rückgabe: Endzeit oder None."""
        jetzt = self._uhr()
        if not an:
            self._hand_aus(jetzt, "Handbetrieb beendet, die Automatik übernimmt.")
            return None
        if not self._betrieb:
            raise EinstellungsFehler("Handbetrieb geht nur im Betrieb.")
        if self.einmessen_handregelung:
            raise EinstellungsFehler("Beim Einmessen steht die Klappe auf 100 %, geregelt wird an den Ofenklappen. "
                                     "Handbetrieb geht erst danach.")
        war = self.speicher.hand_aktiv(jetzt)
        bis = self.speicher.hand_starten(jetzt, dauer_min)
        klappe = self.parameter_neu.klappe_manuell
        if not war:
            self.verlauf.aenderung(jetzt, "handbetrieb", False, True)
        self.meldungen.appendleft({"zeit": jetzt, "text": f"Handbetrieb {'verlängert' if war else 'an'}: "
                                                          f"Klappe {klappe:.0f} % für "
                                                          f"{self.speicher.hand_dauer_min:.0f} min."})
        return bis

    def _hand_aus(self, jetzt: float, text: str) -> None:
        if self.speicher is None or self.speicher.hand_bis is None:
            return
        try:
            self.speicher.hand_beenden()
        except EinstellungsFehler:
            log.error("Ende des Handbetriebs nicht gespeichert")
        self.verlauf.aenderung(jetzt, "handbetrieb", True, False)
        self.meldungen.appendleft({"zeit": jetzt, "text": text})

    def _hand_pruefen(self, jetzt: float) -> None:
        """Endet nach Ablauf von selbst. Steht nach einem Neustart im Lauschen noch einer, gilt er nicht mehr."""
        if self.speicher is None or self.speicher.hand_bis is None:
            return
        if not self._betrieb:
            self._hand_aus(jetzt, "Handbetrieb beendet, weil die App im Lauschen ist.")
        elif not self.speicher.hand_aktiv(jetzt):
            text = f"Handbetrieb nach {self.speicher.hand_dauer_min:.0f} min beendet, die Automatik übernimmt."
            self._hand_aus(jetzt, text)
            self.benachrichtiger.melden("betrieb", text)

    # ---- Einmessen ----------------------------------------------------
    # Grundsatz: Ein Fehler im Einmess-Code führt zur Automatik, nie zu einer festgehaltenen Klappe.

    @property
    def einmessen_handregelung(self) -> bool:
        """Die App hält die Klappe auf 100 %, bis das N-te Nachlegen erkannt ist."""
        return self.einmessung is not None and self.einmessung.handregelung

    def einmessen_kurz(self, jetzt: Optional[float] = None) -> Optional[dict]:
        if self.einmessung is None:
            return None
        return self.einmessung.kurz(self._uhr() if jetzt is None else jetzt)

    def einmessen_laeuft(self, jetzt: Optional[float] = None) -> Optional[dict]:
        if self.einmessung is None:
            return None
        return self.einmessung.zeigen(self._uhr() if jetzt is None else jetzt)

    def einmessen_laufend(self) -> frozenset:
        """Kennungen, die laufen oder gerade abgeschlossen werden (noch ohne Ende in der Datei)."""
        laufend = set(self._einmess_abschluss)
        if self.einmessung is not None:
            laufend.add(self.einmessung.kennung)
        return frozenset(laufend)

    def einmessen_hindernis(self, jetzt: Optional[float] = None) -> Optional[str]:
        """None, wenn eine Aufzeichnung starten darf, sonst der Grund für die Oberfläche.

        Entscheidend ist die Abgastemperatur. Die Phase gilt nur, wenn es keinen gültigen Wert gibt."""
        if self.einmess_ablage is None:
            return "Ohne Einstellungen der App nicht möglich."
        if self.einmessung is not None:
            return "Es läuft schon eine Aufzeichnung."
        if not self._betrieb:
            return "Nur im Betrieb möglich. Bitte erst auf Betrieb schalten."
        t = self.eingaenge.temperatur_c(self.speicher.rollen.get("abgas"))
        if t_gueltig(t):
            if t < self.parameter_neu.t_kalt:
                return None
            return f"Der Ofen ist noch warm ({t:.0f} °C). Einmessen beginnt mit kaltem Ofen."
        neu = self.steuerungen.get(NEU)
        if neu is not None and neu.zustand.phase in (STANDBY, ABKUEHLEN):
            return None
        return "Kein gültiger Abgaswert und der Ofen ist nicht aus. Einmessen beginnt mit kaltem Ofen."

    async def einmessen_starten(self, zyklen: Any, push: Any, notiz: Any) -> None:
        """Startet eine Aufzeichnung, zwischen zwei Regelläufen. EinstellungsFehler mit Grund, wenn nicht."""
        n, push, notiz = einmess.start_pruefen(zyklen, push, notiz)

        async def arbeit() -> None:
            jetzt = self._uhr()
            grund = self.einmessen_hindernis(jetzt)
            if grund:
                raise EinstellungsFehler(grund)
            self._hand_aus(jetzt, "Handbetrieb beendet, das Einmessen beginnt.")
            self.parameter_neu_auffrischen()
            tuerkontakt = self.tuerkontakt_vorhanden() and bool(self.speicher.rollen.get("tuer"))
            self.einmessung = einmess.Aufzeichnung.starten(self.einmess_ablage, jetzt, n, notiz, push, tuerkontakt,
                                                           self.parameter_neu.t_kalt)
            self._einmess_fehler = 0
            self._einmess_tuer_auf = False
            self._einmess_tuer_gemeldet = False
            self.verlauf.aenderung(jetzt, "einmessen", None, {"id": self.einmessung.kennung, "n": n})
            log.warning("Einmessen gestartet (%s, %d Nachlegezyklen), die Klappe bleibt auf 100 %%",
                        self.einmessung.kennung, n)
            self._einmessen_melden("Aufzeichnung gestartet, jetzt anzünden.", jetzt)
        await self._ins_warteschlange(arbeit)

    async def einmessen_abbrechen(self) -> None:
        async def arbeit() -> None:
            if self.einmessung is None:
                raise EinstellungsFehler("Es läuft keine Aufzeichnung.")
            self._einmessen_beenden(self._uhr(), einmess.ENDE_ABGEBROCHEN)
        await self._ins_warteschlange(arbeit)

    def _einmessen_melden(self, text: str, jetzt: float) -> None:
        self.meldungen.appendleft({"zeit": jetzt, "text": f"Einmessen: {text}"})
        if self.einmessung is not None and self.einmessung.kopf["push"]:
            # Jede Nachricht kommt je Aufzeichnung einmal, die Entprellung würde die nächste verschlucken.
            self.benachrichtiger.melden("einmessen", text, entprellen=False)

    def _einmessen_tuer(self, daten: dict) -> None:
        """Merkt sich jedes Öffnen der Tür. So zählt auch ein Öffnen, das zwischen zwei TICKs schon wieder zu ist."""
        if self.einmessung is None or not self.eingaenge.ent.tuer or daten.get("entity_id") != self.eingaenge.ent.tuer:
            return
        if (daten.get("new_state") or {}).get("state") == "on":
            self._einmess_tuer_auf = True

    def _einmessen_ergebnis(self, steuerung: Any, ergebnis: Any) -> Any:
        """Während der Handregelung steht die Klappe wirklich auf 100 %. Das gilt auch für den Zustand,
        den die App veröffentlicht und sichert, und für den Kern: Nach der Übergabe schließt er von 100 % aus."""
        steuerung.zustand.klappe_soll = 100
        steuerung.zustand.klappe_genau = 100.0
        return dataclasses.replace(ergebnis, klappe=100,
                                   zustand=dataclasses.replace(ergebnis.zustand, klappe_soll=100, klappe_genau=100.0))

    async def _einmessen_schritt(self, eingaben: Any, ergebnis: Any, jetzt: float) -> None:
        """Eine Probe je TICK des Regelstands neu. Ein Fehler hier darf die Regelung nie stören:
        Nach EINMESS_FEHLER_MAX Fehlern in Folge endet die Aufzeichnung und die Automatik übernimmt."""
        aufzeichnung = self.einmessung
        try:
            gesendet = self.ausgaenge.gesendet
            tuer = None
            if aufzeichnung.kopf["tuerkontakt"]:
                tuer = True if self._einmess_tuer_auf else eingaben.tuer_offen
            self._einmess_tuer_auf = False
            zeile = {"zeit": jetzt, "t": eingaben.t, "g": eingaben.g, "tuer": tuer,
                     "klappe": gesendet if gesendet is not None else ergebnis.klappe,
                     "twt": wasser_gueltig(eingaben.twt_roh), "pumpe": self._pumpe_prozent(),
                     "phase": ergebnis.zustand.phase}
            ereignis = aufzeichnung.schritt(zeile)
            self._einmess_fehler = 0
            if aufzeichnung.zaehler.tuer_ausgefallen and not self._einmess_tuer_gemeldet:
                self._einmess_tuer_gemeldet = True
                self._einmessen_melden("Der Türkontakt meldet nichts. Nachlegen wird jetzt am Temperaturverlauf "
                                       "erkannt.", jetzt)
            if ereignis is not None:
                self._einmessen_ereignis(ereignis, jetzt)
        except Exception:
            self._einmess_fehler += 1
            log.exception("Einmessen: Probe nicht verarbeitet (%d. Fehler in Folge)", self._einmess_fehler)
            if self.einmessung is aufzeichnung and self._einmess_fehler >= EINMESS_FEHLER_MAX:
                self._einmessen_beenden(jetzt, einmess.ENDE_FEHLER)
        if self.einmessung is aufzeichnung and jetzt - aufzeichnung.kopf["start"] >= einmess.HOECHSTDAUER_S:
            self._einmessen_beenden(jetzt, einmess.ENDE_HOECHSTDAUER)

    def _einmessen_ereignis(self, ereignis: Ereignis, jetzt: float) -> None:
        aufzeichnung = self.einmessung
        log.info("Einmessen %s: %s (Zyklus %d)", aufzeichnung.kennung, ereignis.art, ereignis.zyklus)
        if ereignis.art == FEUER:
            self._einmessen_melden("Feuer erkannt.", jetzt)
        elif ereignis.art == NACHLEGEN:
            self._einmessen_melden(f"Nachlegen {aufzeichnung.zaehler.nachgelegt} von {aufzeichnung.kopf['n']} erkannt.",
                                   jetzt)
        elif ereignis.art == FERTIG:
            self._einmessen_uebergeben()
            self._einmessen_melden("Handregelung beendet, die Automatik übernimmt.", jetzt)
        elif ereignis.art == KALT:
            self._einmessen_beenden(jetzt, einmess.ENDE_KALT)
        elif ereignis.art == KEIN_FEUER:
            self._einmessen_beenden(jetzt, einmess.ENDE_KEIN_FEUER)

    def _einmessen_uebergeben(self) -> None:
        """Die Klappe steht wirklich auf 100 %. Von dort schließt der Kern mit seiner Schließrate,
        statt auf seinen eigenen Wert aus der Handregelung zu springen. Der I-Anteil stand still."""
        neu = self.steuerungen.get(NEU)
        if neu is not None:
            neu.zustand.klappe_soll = 100
            neu.zustand.klappe_genau = 100.0

    def _einmessen_beenden(self, jetzt: float, grund: str) -> None:
        """Beendet die Aufzeichnung sofort: erst an die Automatik übergeben, dann melden. Ende schreiben,
        Punkte vorschlagen, maßgeblich setzen und aufräumen laufen als Hintergrundaufgabe, der Regellauf
        wartet nicht darauf."""
        aufzeichnung = self.einmessung
        if aufzeichnung is None:
            return
        try:
            handregelung = aufzeichnung.handregelung
            grund = aufzeichnung.grund_fuer(grund)
            if handregelung:
                self._einmessen_uebergeben()
            text = f"Aufzeichnung beendet ({einmess.ENDE_TEXTE.get(grund, grund)})."
            if handregelung:
                text += " Die Automatik übernimmt."
            self._einmessen_melden(text, jetzt)
            self.verlauf.aenderung(jetzt, "einmessen", {"id": aufzeichnung.kennung}, {"ende_grund": grund})
            log.warning("Einmessen %s beendet: %s", aufzeichnung.kennung, grund)
        except Exception:
            log.exception("Einmessen: Ende nicht sauber gemeldet")
        finally:
            self.einmessung = None
            self._einmess_fehler = 0
        self._einmess_abschluss.add(aufzeichnung.kennung)
        try:
            aufgabe = asyncio.get_running_loop().create_task(self._einmessen_abschliessen(aufzeichnung, jetzt, grund))
        except RuntimeError:
            self._einmess_abschluss.discard(aufzeichnung.kennung)
            log.error("Einmessen %s: keine laufende Ereignisschleife, Ende nicht gespeichert", aufzeichnung.kennung)
            return
        self._einmess_aufgaben.add(aufgabe)
        aufgabe.add_done_callback(self._einmess_aufgaben.discard)

    async def _einmessen_abschliessen(self, aufzeichnung: einmess.Aufzeichnung, jetzt: float, grund: str) -> None:
        try:
            kopf, geschrieben = await asyncio.to_thread(aufzeichnung.beenden, jetzt, grund)
            if not geschrieben:
                log.error("Einmessen %s: Ende nicht gespeichert, die Liste zeigt sie als Fehler", kopf["id"])
                return
            massgeblich = self.speicher.einmessen_massgeblich
            if massgeblich is None or await asyncio.to_thread(self.einmess_ablage.kopf, massgeblich) is None:
                self.speicher.einmessen_massgeblich_setzen(kopf["id"])     # genau eine ist maßgeblich
            schuetzen = {self.speicher.einmessen_massgeblich} | set(self.einmessen_laufend())
            await asyncio.to_thread(self.einmess_ablage.aufraeumen, schuetzen)
        except Exception:
            log.exception("Einmessen %s: Abschluss fehlgeschlagen", aufzeichnung.kennung)
        finally:
            self._einmess_abschluss.discard(aufzeichnung.kennung)

    async def einmessen_abwarten(self) -> None:
        """Für Tests und das Beenden: wartet, bis alle Abschlüsse geschrieben sind."""
        if self._einmess_aufgaben:
            await asyncio.gather(*list(self._einmess_aufgaben), return_exceptions=True)

    async def _zurueckfallen(self, grund: str) -> None:
        log.error("Rückfall auf Lauschen: %s", grund)
        offen = await self._betrieb_verlassen()
        self.rueckfall = {"zeit": self._uhr(), "grund": grund, "klappe_offen": offen}
        folge = ("Die Klappe steht auf 100 %." if offen
                 else "Die Klappe konnte NICHT geöffnet werden, bitte sofort prüfen!")
        text = f"Zurück auf Lauschen: {grund} {folge}"
        self.meldungen.appendleft({"zeit": self.rueckfall["zeit"], "text": text})
        self.benachrichtiger.melden("betrieb", text)

    async def _oeffnen_nachholen(self, jetzt: float) -> None:
        """Nach einem Rückfall, bei dem die Klappe nicht erreichbar war."""
        if await self.ausgaenge.klappe(100, jetzt, zwingen=True):
            self._oeffnen_bis = None
            self.client.betrieb_sperren()
            if self.rueckfall:
                self.rueckfall["klappe_offen"] = True
            self.benachrichtiger.melden("betrieb", "Die Klappe wurde nachträglich auf 100 % geöffnet.")
        elif jetzt >= self._oeffnen_bis:
            self._oeffnen_bis = None
            self.client.betrieb_sperren()
            self.benachrichtiger.melden("betrieb", "Die Klappe ließ sich nicht öffnen. Bitte sofort am Ofen prüfen!")

    async def _ins_warteschlange(self, arbeit: Callable[[], Any]) -> Any:
        """Läuft zwischen zwei Regelläufen, also nie mitten in einem Lauf."""
        zukunft = asyncio.get_running_loop().create_future()
        self._warteschlange.put_nowait((_AUFTRAG, (zukunft, arbeit)))
        async with asyncio.timeout(AUFTRAG_TIMEOUT_S):
            return await zukunft

    async def _auftrag_erledigen(self, zukunft: asyncio.Future, arbeit: Callable[[], Any]) -> None:
        if zukunft.cancelled():     # Die Anfrage ist längst abgelaufen, dann gilt sie auch nicht mehr.
            return
        try:
            ergebnis = await arbeit()
        except Exception as fehler:
            if not zukunft.done():
                zukunft.set_exception(fehler)
            return
        if not zukunft.done():
            zukunft.set_result(ergebnis)

    async def betrieb_anfordern(self, bestaetigung: Any, version: str) -> list[Punkt]:
        """Leere Liste: Die App steuert jetzt. Sonst die offenen Punkte, es hat sich nichts geändert."""
        async def arbeit() -> list[Punkt]:
            jetzt = self._uhr()
            self.speicher.bestaetigung_speichern(bestaetigung, jetzt, version)
            if self._betrieb:
                return []
            offen = offene_punkte(self, jetzt, start=True)
            if offen:
                return offen
            await self._automation_vor_betrieb()
            jetzt = self._uhr()
            try:
                self.speicher.modus_setzen(BETRIEB)
            except EinstellungsFehler:
                await self._automation_zurueckgeben()
                raise
            self._betrieb_aufnehmen()
            # Der Kern hat bis hierher nur mitgerechnet, also gilt derselbe Schutz wie beim Start.
            self._anlaufschutz = True
            self.rueckfall = None
            self.verlauf.aenderung(jetzt, "modus", LAUSCHEN, BETRIEB)
            log.warning("Betrieb eingeschaltet, die App steuert die Klappe")
            self.benachrichtiger.melden("betrieb", "Betrieb: Die App steuert jetzt die Klappe.")
            return []
        return await self._ins_warteschlange(arbeit)

    async def lauschen_anfordern(self) -> bool:
        """Klappe auf 100 %, dann Lauschen. True, wenn die Klappe bestätigt offen ist."""
        async def arbeit() -> bool:
            if not self._betrieb:
                return True
            offen = await self._betrieb_verlassen()
            folge = "Die Klappe steht auf 100 %." if offen else "Die Klappe konnte NICHT geöffnet werden, bitte prüfen!"
            log.warning("Zurück auf Lauschen, %s", folge)
            self.benachrichtiger.melden("betrieb", f"Lauschen: Die App steuert die Klappe nicht mehr. {folge}")
            return offen
        return await self._ins_warteschlange(arbeit)

    async def quittieren(self) -> dict:
        """Fehler von Hand bestätigen: Klappenstörung zurücksetzen, Störung oder Anheizfehler verlassen.

        Läuft zwischen zwei Regelläufen. Rückgabe: was quittiert wurde und die Phase danach.
        """
        async def arbeit() -> dict:
            if not self._betrieb:
                raise EinstellungsFehler(QUITTIEREN_LAUSCHEN_TEXT)
            neu = self.steuerungen.get(NEU)
            phase_vorher = neu.zustand.phase if neu is not None else None
            klappe = self.ausgaenge.stoerung_quittieren()
            if klappe:
                text = f"Klappenstörung quittiert: {klappe}"
                log.warning(text)
                self.meldungen.appendleft({"zeit": self._uhr(), "text": text})
            await self._ausfuehren(QUITTIEREN, (NEU,))
            phase = neu.zustand.phase if neu is not None else None
            return {"klappe": klappe, "phase_vorher": phase_vorher, "phase": phase,
                    "fehler_offen": phase in (STOERUNG, ANHEIZFEHLER)}
        return await self._ins_warteschlange(arbeit)

    async def fuehler_neustart(self) -> bool:
        async def arbeit() -> bool:
            return self._betrieb and await self.ausgaenge.fuehler_neustart()
        return await self._ins_warteschlange(arbeit)

    def fuehler_neustart_moeglich(self) -> bool:
        return bool(self.speicher.rollen.get(NEUSTART))

    async def _beim_beenden_sichern(self) -> None:
        """SIGTERM: vor dem Schließen der Verbindung öffnen. Der Modus bleibt für den Neustart stehen."""
        neu = self.steuerungen.get(NEU)
        if self._betrieb and neu is not None:
            zustand_sichern(self._zustand_pfad, neu.zustand, self._uhr())
        if not await self._klappe_sicher_oeffnen():
            log.error("Klappe beim Beenden nicht geöffnet")
        self.client.betrieb_sperren()

    async def _wartung_pruefen(self, jetzt: float) -> None:
        """Sensor zur Reinigung des Wärmetauschers und die Erinnerung per Push."""
        if self.speicher is None:
            return
        wassergefuehrt = self.speicher.anlagenprofil.wassergefuehrt
        stand = self.speicher.wartungsstand
        status = wartung.pruefen(float(self.speicher.daten["betrieb_s"]), stand,
                                 self.speicher.reinigung_intervall_h if wassergefuehrt else 0.0)
        await self.schatten.reinigung(status, stand.letzte_reinigung, jetzt)
        if wassergefuehrt and wartung.erinnern(status, stand, jetzt):
            # Erst merken, dann senden: Auch ein Neustart danach wiederholt die Meldung nicht.
            self.speicher.erinnerung_merken(jetzt)
            self.benachrichtiger.melden(
                "reinigung",
                f"Der Wärmetauscher sollte gereinigt werden: {status.gefahren_h:.0f} von "
                f"{status.intervall_h:.0f} Brennstunden erreicht. Danach in der App „Gereinigt“ bestätigen.")

    # ---- Wasserseite --------------------------------------------------

    def _wasser_messwerte(self, phase: Optional[str]) -> kern_wasser.Messwerte:
        """Die App liest nur. Temperaturen in °C, ein Fühler ohne gültige Zahl bleibt None."""
        ein, rollen, profil = self.eingaenge, self.speicher.rollen, self.speicher.anlagenprofil
        zugewiesen = frozenset(n for n in kern_wasser.TEMPERATUR_FUEHLER if rollen.get(n) and rolle_passt(n, profil))
        if self.pumpe_zugewiesen():
            zugewiesen |= {"pumpe"}
        return kern_wasser.Messwerte(
            zugewiesen=zugewiesen, phase=phase, pumpe=self.pumpe_laeuft(),
            **{n: ein.temperatur_c(rollen.get(n)) for n in kern_wasser.TEMPERATUR_FUEHLER})

    def pumpe_zugewiesen(self) -> bool:
        """Ein/Aus oder Stellsignal, passend zum Profil."""
        rollen, profil = self.speicher.rollen, self.speicher.anlagenprofil
        return any(rollen.get(r) and rolle_passt(r, profil) for r in PUMPE_ROLLEN)

    def pumpe_stellsignal(self) -> Optional[float]:
        """Stellsignal in %, None ohne Zuweisung oder Zahl."""
        eid = self.speicher.rollen.get("pumpe_stellsignal")
        if not eid or not rolle_passt("pumpe_stellsignal", self.speicher.anlagenprofil):
            return None
        return self.eingaenge.zahl(eid)

    def pumpe_laeuft(self) -> Optional[bool]:
        """Ein/Aus hat Vorrang. Sonst läuft die Pumpe ab der Schwelle im Anlagenprofil, sonst None."""
        rollen, profil = self.speicher.rollen, self.speicher.anlagenprofil
        ein_aus = rollen.get("pumpe") if rolle_passt("pumpe", profil) else None
        return pumpe_laeuft(bool(ein_aus), self.eingaenge.wert(ein_aus), self.pumpe_stellsignal(), profil.pumpe_laeuft_ab)

    async def _wasser_pruefen(self, jetzt: float) -> None:
        """Wasserstatus für Start, Sensor und Push. Nur der führende Regelstand löst eine Nachricht aus."""
        if self.speicher is None:
            return
        try:
            if not self.speicher.anlagenprofil.wassergefuehrt:
                self.wasser_ergebnis = None
                self.wasserwache = kern_wasser.Wasserwache()
            else:
                stand = self.letzte.get(self.fuehrender_regelstand)
                phase = stand[0].zustand.phase if stand else None
                self.parameter_neu_auffrischen()
                grenzen = self.speicher.wasser_grenzen(self.parameter_neu.t_wasser_max, self.parameter_neu.puffer_voll)
                self.wasser_ergebnis = self.wasserwache.pruefen(jetzt, self._wasser_messwerte(phase), grenzen)
                if stand:
                    self._wasser_melden(self.wasser_ergebnis, jetzt)
            await self.schatten.wasser(self.wasser_ergebnis, jetzt)
        except Exception:
            log.exception("Wasserseite nicht ausgewertet")

    def _wasser_melden(self, ergebnis: kern_wasser.Ergebnis, jetzt: float) -> None:
        """Eine Nachricht, sobald ein Befund auftritt oder schwerer wird. Ein Alarm erinnert alle 15 Minuten."""
        aktuell = {b.schluessel: b for b in ergebnis.befunde}
        for schluessel in [k for k in self._wasser_gemeldet if k not in aktuell]:
            del self._wasser_gemeldet[schluessel]
        for schluessel, befund in aktuell.items():
            stufe, zeit = self._wasser_gemeldet.get(schluessel, (None, 0.0))
            if stufe == befund.stufe and not (befund.stufe == kern_wasser.ALARM and jetzt - zeit >= WASSER_ERINNERUNG_S):
                continue
            self._wasser_gemeldet[schluessel] = (befund.stufe, jetzt)
            self.benachrichtiger.melden(f"wasser_{befund.stufe}", befund.text)

    def _uebertemperatur_pruefen(self, jetzt: float) -> None:
        """Push bei Abgas oder Wasser über der Grenze, in Lauschen und Betrieb gleich.

        Die Grenzen kommen aus parameter_neu, die ohne eigene Werte den Helfern folgen."""
        eingaben = self.letzte_eingaben
        if eingaben is None:
            return
        try:
            self.parameter_neu_auffrischen()
            p = self.parameter_neu
            wasser = self.speicher is None or self.speicher.anlagenprofil.wassergefuehrt
            meldungen = self.uebertemperatur.pruefen(
                jetzt, t_abgas=eingaben.t, twt_roh=eingaben.twt_roh if wasser else None,
                t_uebertemperatur=p.t_uebertemperatur, t_wasser_max=p.t_wasser_max,
                klappe_uebertemperatur=p.klappe_uebertemperatur, klappe_wasser=p.klappe_wasser,
                gedrosselt=not self.einmessen_handregelung)
            for art, titel, text in meldungen:
                log.warning("%s: %s", titel, text)
                self.meldungen.appendleft({"zeit": jetzt, "text": text})
                # Der Wächter taktet selbst, die Entprellung würde die Wiederholung verschlucken.
                self.benachrichtiger.melden(art, text, titel, entprellen=False)
        except Exception:
            log.exception("Übertemperatur nicht geprüft")

    # ---- Kosten -------------------------------------------------------

    def _tuer_merken(self, tuer_offen: bool, jetzt: float) -> None:
        """Holz kommt nur durch die Tür. Kurz nach einer Auflage zählt das Öffnen nicht neu: Da wird geschürt."""
        if tuer_offen and (self._letzte_auflage is None or jetzt - self._letzte_auflage >= AUFLAGE_SPERRE_S):
            self._tuer_seit_auflage = True

    def _auflage_erlaubt(self) -> bool:
        """Mit Türkontakt nur, wenn die Tür seit der letzten Auflage offen war. Flammt die Glut von selbst
        wieder auf (Ausbrand → Anheizen → Abbrand), ist kein Holz dazugekommen."""
        mit_kontakt = self.tuerkontakt_vorhanden() and bool(self.speicher.rollen.get("tuer"))
        return self._tuer_seit_auflage or not mit_kontakt

    def _auflage_buchen(self, jetzt: float) -> None:
        """Eine Auflage ist der Übergang Anheizen → Abbrand des führenden Regelstands nach offener Tür."""
        self._tuer_seit_auflage = False
        self._letzte_auflage = jetzt
        try:
            self.buchfuehrung.auflage(jetzt, 1, self.speicher.anlagenprofil)
        except Exception:
            log.exception("Auflage nicht gebucht")

    def _messung(self) -> Messung:
        """Zählerstände in kWh. Was nicht lesbar ist oder eine unbekannte Einheit hat, bleibt None."""
        ein, rollen, profil = self.eingaenge, self.speicher.rollen, self.speicher.anlagenprofil

        def stand(eid: Optional[str]) -> Optional[float]:
            eintrag = ein.eintrag(eid)
            zahl = als_zahl(eintrag.zustand) if eintrag else None
            return None if zahl is None else energie_nach_kwh(zahl, eintrag.attribute.get("unit_of_measurement"))

        energie: dict[str, Optional[float]] = {}
        for erzeuger in profil.erzeuger:
            if erzeuger.zaehler:
                eintrag = ein.eintrag(erzeuger.zaehler)
                energie[erzeuger.zaehler] = zaehler_nach_kwh(erzeuger, als_zahl(eintrag.zustand) if eintrag else None)
        if not energie and rollen.get("energiezaehler"):
            # Zähler der Erzeuger haben Vorrang, damit nichts doppelt zählt.
            energie[rollen["energiezaehler"]] = stand(rollen["energiezaehler"])
        waerme = stand(rollen.get("waermezaehler")) if rolle_passt("waermezaehler", profil) else None
        return Messung(waerme, energie, ein.temperatur_c(rollen.get("t_aussen")))

    def _buchen(self, jetzt: float) -> None:
        if self.buchfuehrung is None:
            return
        try:
            self.buchfuehrung.tick(jetzt, self._messung(), self.speicher.anlagenprofil)
        except Exception:
            log.exception("Buchführung fehlgeschlagen")

    async def _wirtschaft_pruefen(self, jetzt: float) -> None:
        if self.buchfuehrung is None:
            return
        try:
            await self.schatten.amortisation(ansicht_amortisation(self), jetzt)
        except Exception:
            log.exception("Amortisation nicht geschrieben")

    async def reinigung_bestaetigen(self) -> None:
        self.speicher.reinigung_bestaetigen(self._uhr())
        await self._wartung_pruefen(self._uhr())

    def _anzeige(self) -> Optional[dict]:
        if self.speicher is None:
            return None
        rollen = self.speicher.rollen
        parameter = self.parameter_neu if self._betrieb else self.eingaenge.parameter
        return {"druck": self.eingaenge.zahl(rollen.get("druck")),
                "klappe_ist": self.klappe_ist_prozent(rollen.get("klappe_ist")),
                "ziel": parameter.t_ziel,
                "leistung_kw": self._leistung_kw(),
                "pumpe": self._pumpe_prozent()}

    def _pumpe_prozent(self) -> Optional[float]:
        """Für die Verlaufsgrafik: das Stellsignal, ohne Stellsignal Ein/Aus als 100 oder 0 %."""
        stellsignal = self.pumpe_stellsignal()
        if stellsignal is not None:
            return stellsignal
        laeuft = self.pumpe_laeuft()
        return None if laeuft is None else (100.0 if laeuft else 0.0)

    def _leistung_kw(self) -> Optional[float]:
        eid = self.speicher.rollen.get("waermeleistung")
        if not eid or not rolle_passt("waermeleistung", self.speicher.anlagenprofil):
            return None
        eintrag = self.eingaenge.eintrag(eid)
        if eintrag is None:
            return None
        return leistung_nach_kw(als_zahl(eintrag.zustand), eintrag.attribute.get("unit_of_measurement"))

    def _dauer_vorbelegen(self, s: abbrand_dauer.DauerStand, tag: str) -> None:
        """Übernimmt einmalig die Summen der Live-Automation, damit die Statistik nicht bei null beginnt."""
        ein, p = self.eingaenge, self.eingaenge.praefix
        anzahl = ein.zahl(f"counter.{p}_anzahl_gesamt")
        if anzahl is None or s.vorbelegt or s.anzahl_gesamt:
            return

        def zahl(eid: str) -> float:
            wert = ein.zahl(eid)
            return 0.0 if wert is None else wert

        def zeitpunkt(eid: str) -> float:
            eintrag = ein.eintrag(eid)
            return (als_zahl(eintrag.attribute.get("timestamp")) or 0.0) if eintrag else 0.0

        s.vorbelegt = True
        s.tag = tag
        s.anzahl_gesamt = int(anzahl)
        s.summe_gesamt_min = zahl(f"input_number.{p}_dauer_summe_gesamt")
        s.anzahl_heute = int(zahl(f"counter.{p}_anzahl_heute"))
        s.summe_heute_min = zahl(f"input_number.{p}_dauer_summe_heute")
        s.letzte_min = ein.zahl(f"input_number.{p}_dauer_letzte")
        s.start = zeitpunkt(f"input_datetime.{p}_dauer_start")
        s.kandidat = zeitpunkt(f"input_datetime.{p}_dauer_kandidat")
        s.laeuft = ein.wert(f"input_boolean.{p}_dauer_lauft") == "on"
        log.info("Laufzeiten übernommen: %d Abbrände, %.1f min", s.anzahl_gesamt, s.summe_gesamt_min)

    def _dauer_verfolgen(self, zustand: Any, jetzt: float) -> None:
        """Eine reine Anzeige: Ein Fehler hier darf die Regelung nie stören."""
        if self.speicher is None:
            return
        try:
            if self.dauer is None:
                self.dauer = abbrand_dauer.aus_daten(self.speicher.daten.get("abbrand_dauer"))
            s, tag = self.dauer, datetime.fromtimestamp(jetzt).date().isoformat()
            vorher = s.vorbelegt
            self._dauer_vorbelegen(s, tag)
            if abbrand_dauer.fortschreiben(s, zustand.phase, zustand.phase_seit, jetzt, tag) or s.vorbelegt != vorher:
                self.speicher.daten["abbrand_dauer"] = s.als_dict()
                self.speicher.speichern()
        except Exception:
            log.exception("Laufzeiten nicht erfasst")

    def _betrieb_zaehlen(self, zustand: Any, jetzt: float) -> None:
        if self.speicher is None or zustand.phase not in _BETRIEB_PHASEN:
            return
        self.speicher.betrieb_addieren(self._takte[PARITAET])
        if jetzt - self._betrieb_gespeichert >= _BETRIEB_SPEICHERN_S:
            self._betrieb_gespeichert = jetzt
            try:
                self.speicher.speichern()
            except ValueError:
                pass    # bereits geloggt, der Zähler bleibt im Speicher

    def _fehler_merken(self, regelstand: str, eingaben: Any, text: str) -> None:
        log.exception("Fehler im Kern, Regelstand %s", regelstand)
        self._fehler[regelstand] = text
        self.verlauf.schreiben(regelstand, eingaben, fehler=text)

    async def _status_schreiben(self) -> None:
        wert = STATUS_FEHLER if self._fehler else STATUS_OK
        await self.schatten.status(
            wert, self._uhr(), neu_aktiv=NEU in self.steuerungen, fehler=dict(self._fehler), modus=self.modus
        )

    async def _abgleichen(self) -> None:
        """Holt last_reported nach. state_changed kommt nur bei geänderten Werten, die
        Automation rechnet t_alter aber aus jeder Meldung, auch bei gleichem Wert. Bleibt im
        Betrieb: Sonst sähe ein gleichbleibender Abgaswert aus wie ein verstummter Fühler."""
        try:
            self.eingaenge.aufbauen(await self.client.hole_zustaende(), self._uhr())
        except (VerbindungWeg, asyncio.TimeoutError) as fehler:
            log.warning("Abgleich fehlgeschlagen: %r", fehler)

    # ---- Aufgaben -----------------------------------------------------

    async def _arbeiter(self) -> None:
        while True:
            ausloeser, regelstaende = await self._warteschlange.get()
            try:
                await self._ausfuehren(ausloeser, regelstaende)
            except Exception as fehler:
                log.exception("Fehler im Lauf %s", ausloeser)
                if self._betrieb:
                    await self._zurueckfallen(f"Fehler im Regellauf: {fehler!r}")

    async def _takt(self, regelstand: str) -> None:
        takt_s = self._takte[regelstand]
        ziel = self._uhr()
        while True:
            # Vom letzten Ziel aus rechnen: Wacht sleep minimal zu früh auf,
            # gäbe es sonst denselben Rasterpunkt zweimal.
            jetzt = self._uhr()
            ziel = naechstes_raster(max(jetzt, ziel), takt_s)
            await asyncio.sleep(ziel - jetzt)
            self._warteschlange.put_nowait((TICK, (regelstand,)))

    async def _sensor_wache(self) -> None:
        while True:
            await asyncio.sleep(self._wache_intervall_s)
            if self.client.verbunden and self.eingaenge.pruefe_sensor_weg(self._uhr()):
                log.warning("Abgas seit 60 s ohne Wert")
                self._warteschlange.put_nowait((SENSOR_WEG, BEIDE))

    async def laufen(self, stopp: asyncio.Event) -> None:
        if self.speicher is not None:
            self.eingaenge.tauschen(self.speicher.entitaeten(), self.zusatz_entitaeten(), [], self._uhr())
        aufgaben = [
            asyncio.create_task(self.client.laufen()),
            asyncio.create_task(self._arbeiter()),
            asyncio.create_task(self._takt(PARITAET)),
            asyncio.create_task(self._takt(NEU)),
            asyncio.create_task(self._sensor_wache()),
        ]
        try:
            await stopp.wait()
        finally:
            for aufgabe in aufgaben:
                aufgabe.cancel()
            await asyncio.gather(*aufgaben, return_exceptions=True)
            if self._betrieb or self._oeffnen_bis is not None:
                await self._beim_beenden_sichern()
            try:
                async with asyncio.timeout(SICHER_TIMEOUT_S):
                    await self.einmessen_abwarten()
            except asyncio.TimeoutError:
                log.error("Abschluss des Einmessens beim Beenden nicht fertig geworden")
            self.benachrichtiger.abbrechen()
            if self.buchfuehrung is not None:
                self.buchfuehrung.sichern(True)
            if self.client.verbunden:
                await self.schatten.status(STATUS_GESTOPPT, self._uhr())
            await self.client.schliessen()
            if self.speicher is not None:
                try:
                    self.speicher.speichern()
                except ValueError:
                    pass
            log.info("Beendet")


async def _start(optionen: dict) -> None:
    # Die Option `modus` gibt es nicht mehr, ein alter Wert in den Optionen stört nicht.
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise SystemExit("SUPERVISOR_TOKEN fehlt, homeassistant_api muss aktiv sein")

    speicher = Speicher(EINSTELLUNGEN_PFAD, optionen)
    verlauf = Verlauf(VERLAUF_ORDNER)
    app = App(
        lambda **rueckrufe: HaClient(token, **rueckrufe),
        Eingaenge(speicher.entitaeten(), optionen.get("helfer_praefix", "kamin_abbrand")),
        verlauf,
        steuerung_fabrik=steuerung_aus_kern,
        speicher=speicher,
    )
    stopp = asyncio.Event()
    schleife = asyncio.get_running_loop()
    for signal_nummer in (signal.SIGTERM, signal.SIGINT):
        schleife.add_signal_handler(signal_nummer, stopp.set)
    web = Webserver(app, VERLAUF_ORDNER)
    await web.starten()
    app.integration_stand = await integration_bereitstellen(speicher.token, token)
    try:
        await app.laufen(stopp)
    finally:
        await web.stoppen()


def main(pfad: Optional[Path] = None) -> None:
    optionen = lade_optionen(pfad or OPTIONEN_PFAD)
    logging.basicConfig(
        level=str(optionen.get("log_stufe", "info")).upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    asyncio.run(_start(optionen))


if __name__ == "__main__":
    main()
