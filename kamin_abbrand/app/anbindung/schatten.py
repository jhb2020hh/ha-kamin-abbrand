"""Ausgabe der App: Schattensensoren in HA und JSONL-Verlauf auf der Platte."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import aiohttp

from kern.typen import NEU, PARITAET, Eingaben, Ergebnis, Zustand

log = logging.getLogger(__name__)

SCHREIB_INTERVALL_S = 60.0
AUFBEWAHRUNG_TAGE = 30

STATUS_ID = "sensor.kamin_app_status"
STATUS_START = "start"
STATUS_WARTET = "wartet"
STATUS_OK = "ok"
STATUS_FEHLER = "fehler"
STATUS_GESTOPPT = "gestoppt"

_KUERZEL = {PARITAET: "p", NEU: "n"}
_NAME = {PARITAET: "Parität", NEU: "Neu"}
_VERLAUF_MUSTER = re.compile(r"verlauf-(\d{4}-\d{2}-\d{2})\.jsonl")

Schreiber = Callable[[str, str, Optional[dict]], Awaitable[None]]


class Schatten:
    """Schreibt nur bei Änderung oder wenn der letzte Schreibvorgang 60 s zurückliegt."""

    def __init__(self, schreiber: Schreiber, intervall_s: float = SCHREIB_INTERVALL_S) -> None:
        self._schreiber = schreiber
        self._intervall_s = intervall_s
        self._geschrieben: dict[str, tuple[str, float]] = {}

    def vergessen(self) -> None:
        """Nach einem Neustart von HA fehlen die Zustände, also alles neu schreiben."""
        self._geschrieben.clear()

    async def _setzen(self, eid: str, wert: str, attribute: dict, jetzt: float) -> None:
        letzter = self._geschrieben.get(eid)
        if letzter and letzter[0] == wert and jetzt - letzter[1] < self._intervall_s:
            return
        try:
            await self._schreiber(eid, wert, attribute)
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as fehler:
            # Kein Eintrag im Merker: Der nächste Lauf versucht es erneut.
            log.warning("%s nicht geschrieben: %r", eid, fehler)
            return
        self._geschrieben[eid] = (wert, jetzt)

    async def ergebnis(self, regelstand: str, ergebnis: Ergebnis, jetzt: float) -> None:
        kuerzel, name = _KUERZEL[regelstand], _NAME[regelstand]
        zustand = ergebnis.zustand
        werte: tuple[tuple[str, str, dict], ...] = (
            ("phase", zustand.phase, {"icon": "mdi:fireplace"}),
            ("klappe_soll", str(ergebnis.klappe), {"unit_of_measurement": "%"}),
            ("i_anteil", f"{zustand.i_anteil:.2f}", {"unit_of_measurement": "%"}),
            ("signal", ergebnis.signal, {}),
        )
        await asyncio.gather(*(
            self._setzen(
                f"sensor.kamin_app_{kuerzel}_{feld}", wert,
                {"friendly_name": f"Kamin App {name} {feld}", **attribute}, jetzt,
            )
            for feld, wert, attribute in werte
        ))

    async def reinigung(self, status: Any, letzte_reinigung: Optional[float], jetzt: float) -> None:
        """ok/faellig, bei luftgeführtem Ofen oder Intervall 0 aus."""
        wert = "aus" if not status.aktiv else ("faellig" if status.faellig else "ok")
        attribute: dict[str, Any] = {"friendly_name": "Kamin App Wärmetauscher reinigen", "icon": "mdi:broom"}
        if status.aktiv:
            attribute |= {
                "reststunden": round(status.rest_h, 1), "intervall_stunden": status.intervall_h,
                "gefahrene_stunden": round(status.gefahren_h, 1),
                "letzte_reinigung": (datetime.fromtimestamp(letzte_reinigung).astimezone().isoformat(timespec="seconds")
                                     if letzte_reinigung else None),
            }
        await self._setzen("sensor.kamin_app_wt_reinigung", wert, attribute, jetzt)
        # Eigener Sensor für die Restlaufzeit: Ein Zahlenwert lässt sich in Home Assistant anzeigen und auswerten.
        await self._setzen("sensor.kamin_app_wt_reinigung_rest", f"{status.rest_h:.1f}" if status.aktiv else "unknown", {
            "friendly_name": "Kamin App Wärmetauscher Restlaufzeit", "icon": "mdi:timer-sand", "unit_of_measurement": "h",
            "device_class": "duration", "state_class": "measurement"}, jetzt)

    async def wasser(self, ergebnis: Any, jetzt: float) -> None:
        """ok/hinweis/warnung/alarm, bei luftgeführtem Ofen „aus“. Die Texte stehen als Attribut."""
        attribute: dict[str, Any] = {"friendly_name": "Kamin App Wasser Status", "icon": "mdi:water-thermometer"}
        if ergebnis is not None:
            attribute["texte"] = ergebnis.texte
        await self._setzen("sensor.kamin_app_wasser_status", "aus" if ergebnis is None else ergebnis.stufe, attribute, jetzt)

    async def amortisation(self, daten: Optional[dict], jetzt: float) -> None:
        """Prozent der Investition, die der Ofen eingespielt hat. Netto, Rest und Art als Attribute."""
        if daten is None:
            return
        prozent = daten.get("prozent")
        attribute: dict[str, Any] = {
            "friendly_name": "Kamin App Amortisation", "icon": "mdi:cash-check", "unit_of_measurement": "%",
            "status": daten["status"], "art": daten["art"], "netto": _runden(daten.get("netto_eur")),
            "rest": _runden(daten.get("rest_eur")), "prognose_jahre": _runden(daten.get("prognose_jahre"), 1),
            "waehrung": daten.get("waehrung")}
        await self._setzen("sensor.kamin_app_amortisation", "unknown" if prozent is None else f"{prozent:.1f}", attribute, jetzt)

    async def status(self, wert: str, jetzt: float, **attribute: Any) -> None:
        await self._setzen(
            STATUS_ID, wert, {"friendly_name": "Kamin App Status", **attribute}, jetzt
        )


def _runden(wert: Optional[float], stellen: int = 2) -> Optional[float]:
    return None if wert is None else round(wert, stellen)


LIVE_FELDER = ("phase", "phase_seit", "klappe_soll", "i_anteil", "t_maximum", "t_phasenstart")


class Verlauf:
    """Eine JSON-Zeile je Lauf, eine Datei je Tag, Aufbewahrung 30 Tage."""

    def __init__(self, ordner: Path, aufbewahrung_tage: int = AUFBEWAHRUNG_TAGE) -> None:
        self._ordner = Path(ordner)
        self._aufbewahrung_tage = aufbewahrung_tage
        self._tag: Optional[date] = None

    def schreiben(
        self,
        regelstand: str,
        eingaben: Eingaben,
        ergebnis: Optional[Ergebnis] = None,
        fehler: Optional[str] = None,
        live: Optional[Zustand] = None,
        anzeige: Optional[dict] = None,
    ) -> None:
        """Ein Plattenfehler stoppt keinen Lauf, er wird nur geloggt.

        live ist der Zustand der Live-Automation in diesem Moment, für den Vergleich.
        anzeige sind zusätzliche Messwerte für die Verlaufsgrafik.
        """
        tag = datetime.fromtimestamp(eingaben.jetzt).date()
        zeile = {
            "regelstand": regelstand,
            "eingaben": asdict(eingaben),
            "ergebnis": asdict(ergebnis) if ergebnis is not None else None,
        }
        if fehler is not None:
            zeile["fehler"] = fehler
        if live is not None:
            zeile["live"] = {feld: getattr(live, feld) for feld in LIVE_FELDER}
        if anzeige:
            zeile["anzeige"] = anzeige
        try:
            self._ordner.mkdir(parents=True, exist_ok=True)
            if tag != self._tag:
                self._tag = tag
                self._alte_loeschen(tag)
            with open(self._ordner / f"verlauf-{tag.isoformat()}.jsonl", "a", encoding="utf-8") as datei:
                datei.write(json.dumps(zeile, ensure_ascii=False, default=str) + "\n")
        except OSError as plattenfehler:
            log.error("Verlauf nicht geschrieben: %r", plattenfehler)

    def aenderung(self, zeit: float, was: str, alt: Any, neu: Any) -> None:
        """Jede Änderung an Reglerwerten und Zuweisungen, mit Zeitstempel. Eigene Datei,
        damit die Auswertung des Vergleichs nur Läufe sieht."""
        zeile = {"zeit": zeit, "was": was, "alt": alt, "neu": neu}
        try:
            self._ordner.mkdir(parents=True, exist_ok=True)
            with open(self._ordner / "aenderungen.jsonl", "a", encoding="utf-8") as datei:
                datei.write(json.dumps(zeile, ensure_ascii=False, default=str) + "\n")
        except OSError as fehler:
            log.error("Änderung nicht protokolliert: %r", fehler)

    def _alte_loeschen(self, heute: date) -> None:
        grenze = heute - timedelta(days=self._aufbewahrung_tage)
        for pfad in self._ordner.glob("verlauf-*.jsonl"):
            treffer = _VERLAUF_MUSTER.fullmatch(pfad.name)
            if treffer is None:
                continue
            try:
                alt = date.fromisoformat(treffer.group(1)) < grenze
            except ValueError:
                continue
            if alt:
                pfad.unlink(missing_ok=True)
                log.info("Verlauf %s gelöscht", pfad.name)
