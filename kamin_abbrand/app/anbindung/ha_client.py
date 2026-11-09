"""Verbindung zu Home Assistant über den Supervisor-Proxy.

Lesen: WebSocket mit get_states und subscribe_events (state_changed).
Schreiben: POST /api/states/sensor.kamin_app_*, Nachrichten über den Dienst notify.<name>
und, ausschließlich im Betrieb, feste Dienste auf den zugewiesenen Ausgängen.
Die Liste der Dienste wird nur gelesen (GET /api/services).

Die Schreibsperre ist Absicht: Der Zugang des Supervisors hat Administratorrechte,
deshalb liegt die Grenze hier im Client. Es gibt keine Methode, die irgendetwas
anderes in Home Assistant auslöst, und WebSocket-Befehle laufen durch eine Positivliste.
Ein Dienst wird nur aufgerufen, wenn sein Name vollständig auf notify.[a-z0-9_]+ passt
oder wenn `schalte` ihn durchlässt: Betrieb freigegeben, Entität zugewiesen, Dienst in der festen Liste.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable, Iterable, Optional

import aiohttp

log = logging.getLogger(__name__)

WS_URL = "ws://supervisor/core/websocket"
REST_URL = "http://supervisor/core/api"

# Vollständiges Muster statt startswith: Die ID landet im URL-Pfad.
_SCHATTEN_MUSTER = re.compile(r"sensor\.kamin_app_[a-z0-9_]+")
_NOTIFY_MUSTER = re.compile(r"notify\.[a-z0-9_]+")
_ENTITAET_MUSTER = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")
_ERLAUBTE_WS_BEFEHLE = frozenset({"get_states", "subscribe_events"})
# Domain und Dienst mit den Feldern, die mitgehen dürfen. Sonst ist nichts schaltbar:
# Eine Pumpe (switch.turn_on) oder ein homeassistant.restart kann hier nicht durch.
_ERLAUBTE_DIENSTE: dict[tuple[str, str], frozenset[str]] = {
    ("cover", "set_cover_position"): frozenset({"position"}),
    ("light", "turn_on"): frozenset({"brightness_pct", "transition"}),
    ("light", "turn_off"): frozenset(),
    ("number", "set_value"): frozenset({"value"}),
    ("input_number", "set_value"): frozenset({"value"}),
    ("input_boolean", "turn_off"): frozenset(),
    ("switch", "turn_off"): frozenset(),
    ("button", "press"): frozenset(),
    ("script", "turn_on"): frozenset(),
}

BACKOFF_START_S = 1.0
BACKOFF_MAX_S = 60.0
ANFRAGE_TIMEOUT_S = 15.0
REST_TIMEOUT_S = 5.0
# get_states liefert alle Zustände, die aiohttp-Vorgabe von 4 MiB kann zu klein sein.
WS_MAX_NACHRICHT = 64 * 1024 * 1024
WS_HEARTBEAT_S = 30.0


class SchreibsperreFehler(Exception):
    """Ein Vorgang außerhalb der erlaubten Schattensensoren wurde versucht."""


class AuthFehler(Exception):
    """Home Assistant hat das Token abgelehnt."""


class VerbindungWeg(Exception):
    """Es besteht gerade keine WebSocket-Verbindung."""


def naechster_backoff(aktuell: float, maximum: float = BACKOFF_MAX_S) -> float:
    return min(aktuell * 2, maximum)


class HaClient:
    def __init__(
        self,
        token: str,
        *,
        bei_zustaenden: Callable[[list[dict]], Awaitable[None]],
        bei_ereignis: Callable[[dict], Awaitable[None]],
        bei_trennung: Optional[Callable[[], Awaitable[None]]] = None,
        ws_url: str = WS_URL,
        rest_url: str = REST_URL,
        backoff_start_s: float = BACKOFF_START_S,
        backoff_max_s: float = BACKOFF_MAX_S,
        anfrage_timeout_s: float = ANFRAGE_TIMEOUT_S,
        rest_timeout_s: float = REST_TIMEOUT_S,
    ) -> None:
        self._token = token
        self._bei_zustaenden = bei_zustaenden
        self._bei_ereignis = bei_ereignis
        self._bei_trennung = bei_trennung
        self._ws_url = ws_url
        self._rest_url = rest_url.rstrip("/")
        self._backoff_start_s = backoff_start_s
        self._backoff_max_s = backoff_max_s
        self._anfrage_timeout_s = anfrage_timeout_s
        self._rest_timeout_s = rest_timeout_s

        self._sitzung: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._naechste_id = 1
        self._offen: dict[int, asyncio.Future] = {}
        self._verbunden = False
        self._betrieb_aktiv = False
        self._ausgaenge: frozenset[str] = frozenset()

    @property
    def verbunden(self) -> bool:
        """True, sobald Anmeldung, Abo und erster Zustandsabruf durch sind."""
        return self._verbunden

    # ---- Lebenszyklus -------------------------------------------------

    async def laufen(self) -> None:
        """Hält die Verbindung, bis die Aufgabe abgebrochen wird."""
        backoff = self._backoff_start_s
        while True:
            try:
                await self._eine_verbindung()
            except asyncio.CancelledError:
                raise
            except AuthFehler as fehler:
                log.error("Anmeldung abgelehnt: %s", fehler)
            except (aiohttp.ClientError, OSError, asyncio.TimeoutError, VerbindungWeg) as fehler:
                log.warning("Verbindung zu Home Assistant fehlgeschlagen: %r", fehler)
            except Exception:
                # Auch ein Fehler in einem Rückruf darf die Verbindung nicht dauerhaft beenden.
                log.exception("Unerwarteter Fehler in der Verbindung")
            war_verbunden = self._verbunden
            await self._aufraeumen()
            if war_verbunden:
                backoff = self._backoff_start_s
            log.info("Neuer Verbindungsversuch in %.1f s", backoff)
            await asyncio.sleep(backoff)
            backoff = naechster_backoff(backoff, self._backoff_max_s)

    async def schliessen(self) -> None:
        await self._aufraeumen()
        if self._sitzung is not None:
            await self._sitzung.close()
            self._sitzung = None

    def _hole_sitzung(self) -> aiohttp.ClientSession:
        if self._sitzung is None or self._sitzung.closed:
            self._sitzung = aiohttp.ClientSession()
        return self._sitzung

    async def _eine_verbindung(self) -> None:
        async with self._hole_sitzung().ws_connect(
            self._ws_url, heartbeat=WS_HEARTBEAT_S, max_msg_size=WS_MAX_NACHRICHT
        ) as ws:
            self._ws = ws
            await self._anmelden(ws)
            leser = asyncio.create_task(self._lesen(ws))
            try:
                # Erst abonnieren, dann lesen: So geht zwischen beidem nichts verloren.
                await self._anfrage("subscribe_events", event_type="state_changed")
                zustaende = await self._anfrage("get_states")
                await self._bei_zustaenden(zustaende)
                self._verbunden = True
                log.info("Verbunden, %d Zustände gelesen", len(zustaende))
                await leser
            finally:
                leser.cancel()
                await asyncio.gather(leser, return_exceptions=True)

    async def _anmelden(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        try:
            async with asyncio.timeout(self._anfrage_timeout_s):
                nachricht = await ws.receive_json()
                if nachricht.get("type") != "auth_required":
                    raise AuthFehler(f"unerwartete Begrüßung: {nachricht.get('type')}")
                await ws.send_json({"type": "auth", "access_token": self._token})
                antwort = await ws.receive_json()
        except TypeError as fehler:
            # receive_json meldet so, dass statt Text das Schließen ankam.
            raise VerbindungWeg("während der Anmeldung geschlossen") from fehler
        if antwort.get("type") != "auth_ok":
            raise AuthFehler(str(antwort.get("message", antwort.get("type"))))

    async def _lesen(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        try:
            async for roh in ws:
                if roh.type != aiohttp.WSMsgType.TEXT:
                    break
                nachricht = roh.json()
                # Home Assistant kann mehrere Nachrichten als Liste bündeln.
                for einzel in nachricht if isinstance(nachricht, list) else [nachricht]:
                    await self._verteilen(einzel)
        finally:
            self._offene_abbrechen()
        raise VerbindungWeg("WebSocket geschlossen")

    async def _verteilen(self, nachricht: dict) -> None:
        typ = nachricht.get("type")
        if typ == "result":
            zukunft = self._offen.pop(nachricht.get("id"), None)
            if zukunft is None or zukunft.done():
                return
            if nachricht.get("success"):
                zukunft.set_result(nachricht.get("result"))
            else:
                zukunft.set_exception(VerbindungWeg(f"Befehl abgelehnt: {nachricht.get('error')}"))
        elif typ == "event":
            ereignis = nachricht.get("event") or {}
            if ereignis.get("event_type") == "state_changed":
                try:
                    await self._bei_ereignis(ereignis.get("data") or {})
                except Exception:
                    log.exception("Fehler beim Verarbeiten eines Ereignisses")

    async def _aufraeumen(self) -> None:
        war_verbunden = self._verbunden
        self._verbunden = False
        self._offene_abbrechen()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        if war_verbunden and self._bei_trennung is not None:
            await self._bei_trennung()

    def _offene_abbrechen(self) -> None:
        for zukunft in self._offen.values():
            if not zukunft.done():
                zukunft.set_exception(VerbindungWeg("Verbindung getrennt"))
        self._offen.clear()

    # ---- Lesen --------------------------------------------------------

    async def _anfrage(self, befehl: str, **felder: Any) -> Any:
        if befehl not in _ERLAUBTE_WS_BEFEHLE:
            raise SchreibsperreFehler(f"WebSocket-Befehl nicht erlaubt: {befehl}")
        if self._ws is None or self._ws.closed:
            raise VerbindungWeg("keine Verbindung")
        kennung = self._naechste_id
        self._naechste_id += 1
        zukunft = asyncio.get_running_loop().create_future()
        self._offen[kennung] = zukunft
        try:
            await self._ws.send_json({"id": kennung, "type": befehl, **felder})
            async with asyncio.timeout(self._anfrage_timeout_s):
                return await zukunft
        finally:
            self._offen.pop(kennung, None)

    async def hole_zustaende(self) -> list[dict]:
        """Liest alle Zustände. Wirft VerbindungWeg oder TimeoutError."""
        return await self._anfrage("get_states")

    # ---- Schreiben ----------------------------------------------------

    async def setze_schattensensor(
        self, entity_id: str, zustand: str, attribute: Optional[dict] = None
    ) -> None:
        """Einziger Schreibweg der App. Wirft SchreibsperreFehler bei jeder anderen ID."""
        if not _SCHATTEN_MUSTER.fullmatch(entity_id):
            raise SchreibsperreFehler(f"Schreiben nicht erlaubt: {entity_id!r}")
        async with self._hole_sitzung().post(
            f"{self._rest_url}/states/{entity_id}",
            json={"state": zustand, "attributes": attribute or {}},
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=aiohttp.ClientTimeout(total=self._rest_timeout_s),
        ) as antwort:
            if antwort.status >= 300:
                raise aiohttp.ClientResponseError(
                    antwort.request_info, antwort.history, status=antwort.status,
                    message=f"Schreiben von {entity_id} fehlgeschlagen",
                )

    async def hole_dienste(self) -> list[dict]:
        """Liest die Dienste von Home Assistant (nur lesen). Wirft aiohttp-Fehler oder TimeoutError."""
        async with self._hole_sitzung().get(
            f"{self._rest_url}/services",
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=aiohttp.ClientTimeout(total=self._rest_timeout_s),
        ) as antwort:
            antwort.raise_for_status()
            daten = await antwort.json()
        return daten if isinstance(daten, list) else []

    async def sende_notify(
        self, dienst: str, titel: str, text: str, daten: Optional[dict] = None
    ) -> None:
        """Schickt eine Nachricht. Wirft SchreibsperreFehler, wenn `dienst` kein notify.<name> ist."""
        if not isinstance(dienst, str) or not _NOTIFY_MUSTER.fullmatch(dienst):
            raise SchreibsperreFehler(f"Dienst nicht erlaubt: {dienst!r}")
        inhalt: dict[str, Any] = {"title": titel, "message": text}
        if daten:
            inhalt["data"] = daten
        async with self._hole_sitzung().post(
            f"{self._rest_url}/services/notify/{dienst.split('.', 1)[1]}",
            json=inhalt,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=aiohttp.ClientTimeout(total=self._rest_timeout_s),
        ) as antwort:
            antwort.raise_for_status()

    # ---- Schalten, nur im Betrieb ---------------------------------------

    @property
    def betrieb_aktiv(self) -> bool:
        return self._betrieb_aktiv

    def betrieb_freigeben(self, ausgaenge: Iterable[str]) -> None:
        """Öffnet `schalte` für genau diese Entitäten. Eine neue Liste ersetzt die alte."""
        self._ausgaenge = frozenset(ausgaenge)
        self._betrieb_aktiv = True

    def betrieb_sperren(self) -> None:
        self._betrieb_aktiv = False
        self._ausgaenge = frozenset()

    async def schalte(self, domain: str, dienst: str, entity_id: str, daten: Optional[dict] = None) -> None:
        """Ruft einen Dienst auf einem zugewiesenen Ausgang auf. Wirft SchreibsperreFehler,
        bevor etwas den Server erreicht, wenn eine der Bedingungen nicht stimmt."""
        if not self._betrieb_aktiv:
            raise SchreibsperreFehler("Schalten ist nur im Betrieb möglich.")
        felder = _ERLAUBTE_DIENSTE.get((domain, dienst)) if isinstance(domain, str) and isinstance(dienst, str) else None
        if felder is None:
            raise SchreibsperreFehler(f"Dienst nicht erlaubt: {domain!r}.{dienst!r}")
        if (not isinstance(entity_id, str) or not _ENTITAET_MUSTER.fullmatch(entity_id)
                or entity_id not in self._ausgaenge or entity_id.split(".", 1)[0] != domain):
            raise SchreibsperreFehler(f"Kein zugewiesener Ausgang: {entity_id!r}")
        daten = daten or {}
        if not set(daten) <= felder:
            raise SchreibsperreFehler(f"Felder nicht erlaubt: {sorted(set(daten) - felder)}")
        async with self._hole_sitzung().post(
            f"{self._rest_url}/services/{domain}/{dienst}",
            json={**daten, "entity_id": entity_id},
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=aiohttp.ClientTimeout(total=self._rest_timeout_s),
        ) as antwort:
            antwort.raise_for_status()
