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
import logging
import math
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from anbindung.anlagenprofil import rolle_passt, zaehler_entitaeten, zaehler_nach_kwh
from anbindung.ansicht import NACHLEGEN_TEXT, nachlegen_moeglich
from anbindung.ansicht import amortisation_zeigen as ansicht_amortisation
from anbindung.ausgaenge import Ausgaenge, NEUSTART, zugewiesene_ausgaenge
from anbindung.benachrichtigung import Benachrichtiger
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
from anbindung.web import Webserver
from kern import wartung
from kern import wasser as kern_wasser
from kern.wirtschaft import energie_nach_kwh
from kern.phasen import TITEL_ANHEIZFEHLER, TITEL_STOERUNG
from kern.signale import t_gueltig
from kern.typen import (ABBRAND, ANHEIZEN, AUSBRAND, GLUT, NEU, PARITAET, SENSOR_WEG, STANDBY, TICK,
                        Parameter, Zustand)

log = logging.getLogger("kamin_abbrand")

TAKT_PARITAET_S = 30.0
TAKT_NEU_S = 5.0
WACHE_INTERVALL_S = 1.0
OPTIONEN_PFAD = Path("/data/options.json")
VERLAUF_ORDNER = Path("/config/verlauf")
EINSTELLUNGEN_PFAD = Path("/config/einstellungen.json")
START_OHNE_HELFER_NACH_S = 60.0
_WECHSEL = "_wechsel"
_AUFTRAG = "_auftrag"
# Wie lange eine vorübergehende Bedingung fehlen darf, bevor die App zurückfällt.
# Eine kurze Funkpause soll nicht jedes Mal den Betrieb beenden; danach gilt Klappe 100 %.
BEDINGUNG_KARENZ_S = 30.0
SICHER_TIMEOUT_S = 6.0          # Der Supervisor beendet nach 10 s hart
RUECKFALL_OEFFNEN_S = 600.0     # So lange versucht die App nach einem Rückfall noch, die Klappe zu öffnen
AUFTRAG_TIMEOUT_S = 20.0
ZUSTAND_SICHERN_S = 30.0
_BETRIEB_PHASEN = (ANHEIZEN, ABBRAND, AUSBRAND)
_BETRIEB_SPEICHERN_S = 300.0
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
    ) -> None:
        self.eingaenge = eingaenge
        self.speicher = speicher
        self.parameter_neu = dataclasses.replace(eingaenge.parameter)
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
        self.buchfuehrung: Optional[Buchfuehrung] = (
            Buchfuehrung(speicher.pfad.parent / "wirtschaft.json", uhr) if speicher is not None else None)
        self.steuerungen: dict[str, Any] = {}
        self._fehler: dict[str, str] = {}
        self._uebernommen = False
        self.ohne_helfer = False
        self.integration_stand: dict = {}
        self.ausgaenge = Ausgaenge(
            lambda *a: self.client.schalte(*a),
            lambda: self.speicher.rollen if self.speicher else {},
            self.eingaenge.stellung, self._kennlinie, self._attribute, self._ausgang_stoerung, uhr)
        self._karenz_s = karenz_s
        self._betrieb = False
        self._anlaufschutz = False
        self._weg_seit: dict[str, float] = {}
        self._getrennt_seit: Optional[float] = None
        self._oeffnen_bis: Optional[float] = None
        self._zustand_gesichert = 0.0
        self.rueckfall: Optional[dict] = None
        self._warteschlange: asyncio.Queue[tuple[str, tuple]] = asyncio.Queue()
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

    async def _bei_ereignis(self, daten: dict) -> None:
        for ausloeser in self.eingaenge.ereignis(daten, self._uhr()):
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
        return rollen + zaehler_entitaeten(profil)

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
        if not self._uebernommen and self.client.verbunden:
            self._ohne_helfer_starten(self._uhr())
        if not self._uebernommen or not self.client.verbunden:
            log.debug("Lauf %s übersprungen", ausloeser)
            return
        if self._oeffnen_bis is not None:
            await self._oeffnen_nachholen(self._uhr())
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
            live = self._wirklicher_zustand(jetzt)
            self.verlauf.schreiben(regelstand, eingaben, ergebnis, live=live,
                                   anzeige=self._anzeige())
            self.letzte[regelstand] = (ergebnis, jetzt)
            self.letzte_eingaben = eingaben
            if regelstand == NEU and self._betrieb:
                await self._stellen(ergebnis, eingaben, jetzt)
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
                        and self.buchfuehrung is not None):
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
        offen = offene_punkte(self, jetzt)
        schluessel = {p.schluessel for p in offen}
        self._weg_seit = {k: v for k, v in self._weg_seit.items() if k in schluessel}
        for punkt in offen:
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
        return offen

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
            offen = offene_punkte(self, jetzt)
            if offen:
                return offen
            self.speicher.modus_setzen(BETRIEB)
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
        zugewiesen = frozenset(n for n in (*kern_wasser.TEMPERATUR_FUEHLER, "pumpe") if rollen.get(n) and rolle_passt(n, profil))
        pumpe = {"on": True, "off": False}.get(ein.wert(rollen.get("pumpe")))
        return kern_wasser.Messwerte(
            zugewiesen=zugewiesen, phase=phase, pumpe=pumpe,
            **{n: ein.temperatur_c(rollen.get(n)) for n in kern_wasser.TEMPERATUR_FUEHLER})

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

    # ---- Kosten -------------------------------------------------------

    def _auflage_buchen(self, jetzt: float) -> None:
        """Eine Auflage ist der Übergang Anheizen → Abbrand des führenden Regelstands."""
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
        return {"druck": self.eingaenge.zahl(rollen.get("druck")),
                "klappe_ist": self.eingaenge.stellung(rollen.get("klappe_ist"))}

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
