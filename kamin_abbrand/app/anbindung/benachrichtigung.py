"""Push-Nachrichten: welche Art an welches Gerät geht, Entprellung und Versand.

Die Vorgabe ist leer: Ohne Haken wird nichts gesendet. Der Versand läuft in einer eigenen
Aufgabe mit Zeitlimit, damit er weder die Warteschlange der Regelläufe aufhält noch einen
Lauf abbricht. Ein Sendefehler wird nur geloggt.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger(__name__)

ENTPRELLUNG_S = 600.0
SENDE_TIMEOUT_S = 15.0
_DIENST = re.compile(r"notify\.[a-z0-9_]+")
# Diese Dienste sind keine Geräte: send_message braucht eine Entität, persistent_notification bleibt in HA.
NICHT_ANBIETEN = frozenset({"notify.send_message", "notify.persistent_notification"})
_DRINGEND = {"push": {"interruption-level": "time-sensitive"}}


@dataclass(frozen=True)
class Art:
    schluessel: str
    titel: str          # Für die Oberfläche
    hilfe: str
    dringend: bool = False      # Wird durch Fokus-Modi durchgelassen
    push_titel: str = "🔥 Kamin"


ARTEN: tuple[Art, ...] = (
    Art("nachlegen", "Nachlegen", "Die Glut ist da und der Puffer nimmt noch Wärme auf."),
    Art("stoerung", "Störung", "Fühler stumm oder eingefroren, Feuer nicht angegangen.", True, "🔥 Kamin – Störung"),
    Art("reinigung", "Wärmetauscher reinigen", "Die eingestellten Brennstunden sind erreicht.", False, "🔥 Kamin – Reinigung"),
    Art("betrieb", "Betrieb", "Moduswechsel, Klappe folgt nicht.", True, "🔥 Kamin – Betrieb"),
    Art("wasser_hinweis", "Wasser: Hinweis", "Zum Beispiel Puffer voll, besser nicht nachlegen.", False, "🔥 Kamin – Wasser"),
    Art("wasser_warnung", "Wasser: Warnung", "Vorlauf hoch oder Rücklauf zu kalt.", False, "🔥 Kamin – Wasser"),
    Art("wasser_alarm", "Wasser: Alarm", "Wasser zu heiß oder Pumpe steht bei Feuer.", True, "🔥 Kamin – Wasser-Alarm"),
)
ARTEN_NACH_NAME = {a.schluessel: a for a in ARTEN}


class ZuordnungFehler(ValueError):
    """Die Zuordnung passt nicht; der Text ist für die Oberfläche bestimmt."""


def dienst_gueltig(dienst: Any) -> bool:
    return isinstance(dienst, str) and bool(_DIENST.fullmatch(dienst)) and dienst not in NICHT_ANBIETEN


def zuordnung_pruefen(roh: Any) -> dict[str, list[str]]:
    """Art → Liste von notify-Diensten. Unbekannte Arten und ungültige Dienste sind ein Fehler."""
    if not isinstance(roh, dict):
        raise ZuordnungFehler("Die Zuordnung ist ungültig.")
    neu: dict[str, list[str]] = {}
    for art, dienste in roh.items():
        if art not in ARTEN_NACH_NAME:
            raise ZuordnungFehler(f"Unbekannte Nachrichtenart: {art}")
        if not isinstance(dienste, list):
            raise ZuordnungFehler(f"{ARTEN_NACH_NAME[art].titel}: Erwartet wird eine Liste von Geräten.")
        for dienst in dienste:
            if not dienst_gueltig(dienst):
                raise ZuordnungFehler(f"{ARTEN_NACH_NAME[art].titel}: „{dienst}“ ist kein Gerät für Nachrichten.")
        gewaehlt = list(dict.fromkeys(dienste))
        if gewaehlt:
            neu[art] = gewaehlt
    return neu


def lesbarer_name(dienst: str) -> str:
    name = dienst.split(".", 1)[-1]
    if name.startswith("mobile_app_"):
        name = name[len("mobile_app_"):]
    return name.replace("_", " ").strip().title() or dienst


def geraete_aus_diensten(dienste: list[dict]) -> list[dict]:
    """Aus der Dienstliste von HA: Domain notify, mobile_app_* zuerst, der Rest danach."""
    gefunden: set[str] = set()
    for eintrag in dienste:
        if isinstance(eintrag, dict) and eintrag.get("domain") == "notify":
            gefunden |= {f"notify.{n}" for n in (eintrag.get("services") or {})}
    geraete = [{"dienst": d, "name": lesbarer_name(d), "handy": d.startswith("notify.mobile_app_")}
               for d in gefunden if dienst_gueltig(d)]
    geraete.sort(key=lambda g: (not g["handy"], g["name"].casefold(), g["dienst"]))
    return geraete


Sender = Callable[[str, str, str, Optional[dict]], Awaitable[None]]


class Benachrichtiger:
    def __init__(
        self,
        zuordnung: Callable[[], dict[str, list[str]]],
        sender: Sender,
        uhr: Callable[[], float],
        *,
        entprellung_s: float = ENTPRELLUNG_S,
        timeout_s: float = SENDE_TIMEOUT_S,
    ) -> None:
        self._zuordnung = zuordnung
        self._sender = sender
        self._uhr = uhr
        self._entprellung_s = entprellung_s
        self._timeout_s = timeout_s
        self._zuletzt: dict[tuple[str, str], float] = {}
        self._aufgaben: set[asyncio.Task] = set()

    def melden(self, art: str, text: str, titel: Optional[str] = None) -> list[str]:
        """Plant den Versand und kehrt sofort zurück. Gibt die Geräte zurück, an die gesendet wird.

        Ohne zugeordnetes Gerät und innerhalb der Entprellung passiert nichts."""
        beschreibung = ARTEN_NACH_NAME.get(art)
        if beschreibung is None:
            log.error("Unbekannte Nachrichtenart %r", art)
            return []
        ziele = [d for d in self._zuordnung().get(art, []) if dienst_gueltig(d)]
        if not ziele:
            return []
        jetzt = self._uhr()
        schluessel = (art, text)
        letzte = self._zuletzt.get(schluessel)
        if letzte is not None and jetzt - letzte < self._entprellung_s:
            return []
        try:
            schleife = asyncio.get_running_loop()
        except RuntimeError:
            log.error("Nachricht %s nicht gesendet: keine laufende Ereignisschleife", art)
            return []
        self._zuletzt[schluessel] = jetzt
        for alt in [k for k, z in self._zuletzt.items() if jetzt - z >= self._entprellung_s]:
            del self._zuletzt[alt]
        daten = _DRINGEND if beschreibung.dringend else None
        aufgabe = schleife.create_task(self._senden(ziele, titel or beschreibung.push_titel, text, daten, art))
        self._aufgaben.add(aufgabe)
        aufgabe.add_done_callback(self._aufgaben.discard)
        return ziele

    async def _senden(self, ziele: list[str], titel: str, text: str, daten: Optional[dict], art: str) -> None:
        for dienst in ziele:
            try:
                async with asyncio.timeout(self._timeout_s):
                    await self._sender(dienst, titel, text, daten)
            except asyncio.CancelledError:
                raise
            except Exception as fehler:     # Auch ein Fehler hier darf keinen Regellauf berühren
                log.warning("Nachricht %s an %s nicht gesendet: %r", art, dienst, fehler)

    async def abwarten(self) -> None:
        """Für Tests und das Beenden: wartet, bis alle geplanten Nachrichten durch sind."""
        if self._aufgaben:
            await asyncio.gather(*list(self._aufgaben), return_exceptions=True)

    def abbrechen(self) -> None:
        for aufgabe in list(self._aufgaben):
            aufgabe.cancel()
