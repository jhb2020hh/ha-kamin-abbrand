"""Einstieg der App im Parallelbetrieb: lesen, rechnen, protokollieren.

Alle Läufe gehen durch eine Warteschlange und damit nacheinander, wie `mode: queued`
der Automation. Ergebnis.aktionen und .meldungen werden nur protokolliert.
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

from anbindung.bereitstellung import integration_bereitstellen
from anbindung.eingaenge import Eingaenge, Entitaeten
from anbindung.einstellungen import ROLLEN, AUSGANG, Speicher
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
from kern.typen import ABBRAND, ANHEIZEN, AUSBRAND, NEU, PARITAET, SENSOR_WEG, TICK, Parameter, Zustand

log = logging.getLogger("kamin_abbrand")

TAKT_PARITAET_S = 30.0
TAKT_NEU_S = 5.0
WACHE_INTERVALL_S = 1.0
OPTIONEN_PFAD = Path("/data/options.json")
VERLAUF_ORDNER = Path("/config/verlauf")
EINSTELLUNGEN_PFAD = Path("/config/einstellungen.json")
START_OHNE_HELFER_NACH_S = 60.0
_WECHSEL = "_wechsel"
_BETRIEB_PHASEN = (ANHEIZEN, ABBRAND, AUSBRAND)
_BETRIEB_SPEICHERN_S = 300.0

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
        self.steuerungen: dict[str, Any] = {}
        self._fehler: dict[str, str] = {}
        self._uebernommen = False
        self.ohne_helfer = False
        self.integration_stand: dict = {}
        self._warteschlange: asyncio.Queue[tuple[str, tuple[str, ...]]] = asyncio.Queue()

    # ---- Rückrufe des Clients -----------------------------------------

    async def _bei_zustaenden(self, zustaende: list[dict]) -> None:
        jetzt = self._uhr()
        self.eingaenge.aufbauen(zustaende, jetzt)
        self.schatten.vergessen()
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

    def _uebernehmen(self, jetzt: float) -> None:
        """Einmalig beim ersten Start. Nach einem Reconnect rechnen die Instanzen weiter."""
        if self.eingaenge.startzustand(jetzt) is None:
            log.warning("Phase noch nicht lesbar, warte auf Home Assistant")
            return
        self._instanzen_bauen(self.eingaenge.startzustand)
        log.info("Zustand aus Home Assistant übernommen: %s", self.eingaenge.startzustand(jetzt))

    def _ohne_helfer_starten(self, jetzt: float) -> None:
        """Andere Anlage ohne die Helfer der Live-Automation: Die App beginnt im Standby."""
        if self._uebernommen or self._erster_kontakt is None or not self.eingaenge.phase_fehlt():
            return
        if jetzt - self._erster_kontakt < self._start_ohne_helfer_nach_s:
            return
        log.warning("Keine Helfer der Live-Automation gefunden, die App startet im Standby")
        self.ohne_helfer = True
        self._instanzen_bauen(self.eingaenge.startzustand_leer)

    def _instanzen_bauen(self, start: Callable[[float], Any]) -> None:
        for regelstand in BEIDE:
            # Je Instanz ein eigener Zustand. paritaet rechnet mit den Live-Helfern,
            # neu zusätzlich mit den eigenen Werten aus den Einstellungen.
            zustand = start(self._uhr())
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
        return [eid for rolle in ROLLEN
                if (eid := self.speicher.rollen.get(rolle.schluessel))
                and (rolle.art == AUSGANG or rolle.schluessel == "klappe_ist")]

    def zuweisung_uebernehmen(self) -> None:
        """Wirkt ohne App-Neustart, der Wechsel läuft zwischen zwei Läufen."""
        self._warteschlange.put_nowait((_WECHSEL, ()))

    async def _wechseln(self) -> None:
        if self.speicher is None:
            return
        zustaende = await self.client.hole_zustaende()
        self.eingaenge.tauschen(self.speicher.entitaeten(), self.zusatz_entitaeten(), zustaende, self._uhr())
        log.info("Neue Zuweisung der Ein- und Ausgänge übernommen")

    # ---- Läufe --------------------------------------------------------

    async def _ausfuehren(self, ausloeser: str, regelstaende: tuple[str, ...]) -> None:
        if ausloeser == _WECHSEL:
            if self.client.verbunden:
                await self._wechseln()
            return
        if not self._uebernommen and self.client.verbunden:
            self._ohne_helfer_starten(self._uhr())
        if not self._uebernommen or not self.client.verbunden:
            log.debug("Lauf %s übersprungen", ausloeser)
            return
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
                continue
            except Exception as fehler:
                self._fehler_merken(regelstand, eingaben, repr(fehler))
                continue
            self._fehler.pop(regelstand, None)
            live = self.eingaenge.startzustand(jetzt)
            self.verlauf.schreiben(regelstand, eingaben, ergebnis, live=live,
                                   anzeige=self._anzeige())
            self.letzte[regelstand] = (ergebnis, jetzt)
            self.letzte_eingaben = eingaben
            if regelstand == PARITAET and ausloeser == TICK:
                self._betrieb_zaehlen(live or ergebnis.zustand, jetzt)
            for aktion in ergebnis.aktionen:
                log.info("[%s] Aktion nur protokolliert: %s", regelstand, aktion)
            for meldung in ergebnis.meldungen:
                log.info("[%s] Meldung nur protokolliert: %s: %s", regelstand, meldung.titel, meldung.text)
                if regelstand == PARITAET:
                    self.meldungen.appendleft({"zeit": jetzt, "text": f"{meldung.titel}: {meldung.text}"})
            if ergebnis.uebergang:
                log.info("[%s] %s → %s (%s)", regelstand, *ergebnis.uebergang, ergebnis.regel)
                if regelstand == PARITAET:
                    self.meldungen.appendleft({"zeit": jetzt, "text": "{} → {}".format(*ergebnis.uebergang)})
            await self.schatten.ergebnis(regelstand, ergebnis, jetzt)

        await self._status_schreiben()

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
            wert, self._uhr(), neu_aktiv=NEU in self.steuerungen, fehler=dict(self._fehler)
        )

    async def _abgleichen(self) -> None:
        """Holt last_reported nach. state_changed kommt nur bei geänderten Werten, die
        Automation rechnet t_alter aber aus jeder Meldung, auch bei gleichem Wert."""
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
            except Exception:
                log.exception("Fehler im Lauf %s", ausloeser)

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
    if optionen.get("modus", "parallel") != "parallel":
        raise SystemExit(f"Modus {optionen.get('modus')!r} gibt es in Phase 1 nicht")
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
