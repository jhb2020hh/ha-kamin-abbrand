"""Nachbau der Supervisor-Schnittstellen von Home Assistant für die Tests."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer

TOKEN = "test-token"


def iso(zeit: Optional[float] = None) -> str:
    wert = datetime.now(timezone.utc) if zeit is None else datetime.fromtimestamp(zeit, timezone.utc)
    return wert.isoformat()


def zustand(eid: str, wert: str, attribute: Optional[dict] = None, zeit: Optional[float] = None) -> dict:
    stempel = iso(zeit)
    return {
        "entity_id": eid, "state": wert, "attributes": attribute or {},
        "last_changed": stempel, "last_reported": stempel, "last_updated": stempel,
    }


class FakeHa:
    def __init__(self) -> None:
        self.zustaende: dict[str, dict] = {}
        self.ws_nachrichten: list[dict] = []
        self.rest_anfragen: list[tuple[str, str, dict]] = []
        self.rest_token: list[str] = []
        self.verbindungen = 0
        self.anmeldungen_ok = 0
        self.rest_status = 200
        self.dienst_status: Optional[int] = None    # Nur für Dienste außer notify, None = wie rest_status
        self.dienste: list[dict] = []
        self._offen: set[web.WebSocketResponse] = set()
        self._abos: dict[web.WebSocketResponse, int] = {}
        app = web.Application()
        app.router.add_get("/core/websocket", self._ws)
        app.router.add_route("*", "/{pfad:.*}", self._rest)
        self._server = TestServer(app)

    async def __aenter__(self) -> "FakeHa":
        await self._server.start_server()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.abbrechen()
        await self._server.close()

    @property
    def ws_url(self) -> str:
        return str(self._server.make_url("/core/websocket"))

    @property
    def rest_url(self) -> str:
        return str(self._server.make_url("/core/api"))

    def setzen(self, eid: str, wert: str, attribute: Optional[dict] = None, zeit: Optional[float] = None) -> None:
        """Ändert einen Zustand ohne Ereignis, etwa während die Verbindung fehlt."""
        self.zustaende[eid] = zustand(eid, wert, attribute, zeit)

    async def aendern(self, eid: str, wert: str, attribute: Optional[dict] = None, zeit: Optional[float] = None) -> None:
        alt = self.zustaende.get(eid)
        self.setzen(eid, wert, attribute, zeit)
        for ws, kennung in list(self._abos.items()):
            await ws.send_json({
                "id": kennung, "type": "event",
                "event": {"event_type": "state_changed", "data": {
                    "entity_id": eid, "old_state": alt, "new_state": self.zustaende[eid],
                }},
            })

    async def abbrechen(self) -> None:
        for ws in list(self._offen):
            await ws.close()

    def gesendet(self) -> list[tuple[str, dict]]:
        """Alle Aufrufe von notify.<name>: (Dienst, Inhalt)."""
        vorspann = "/core/api/services/notify/"
        return [("notify." + p[len(vorspann):], k) for m, p, k in self.rest_anfragen
                if m == "POST" and p.startswith(vorspann)]

    def schaltbefehle(self) -> list[tuple[str, dict]]:
        """Alle Dienstaufrufe außer notify: ("domain.dienst", Inhalt)."""
        vorspann = "/core/api/services/"
        return [(p[len(vorspann):].replace("/", "."), k) for m, p, k in self.rest_anfragen
                if m == "POST" and p.startswith(vorspann) and not p.startswith(vorspann + "notify/")]

    def geschrieben(self, eid: str) -> list[str]:
        pfad = f"/core/api/states/{eid}"
        return [k["state"] for m, p, k in self.rest_anfragen if m == "POST" and p == pfad]

    async def _ws(self, anfrage: web.Request) -> web.WebSocketResponse:
        self.verbindungen += 1
        ws = web.WebSocketResponse()
        await ws.prepare(anfrage)
        self._offen.add(ws)
        await ws.send_json({"type": "auth_required", "ha_version": "2026.11.0"})
        try:
            nachricht = await ws.receive_json()
            self.ws_nachrichten.append(nachricht)
            if nachricht.get("access_token") != TOKEN:
                await ws.send_json({"type": "auth_invalid", "message": "falsches Token"})
                return ws
            self.anmeldungen_ok += 1
            await ws.send_json({"type": "auth_ok", "ha_version": "2026.11.0"})
            async for roh in ws:
                if roh.type != WSMsgType.TEXT:
                    break
                await self._befehl(ws, roh.json())
        finally:
            self._offen.discard(ws)
            self._abos.pop(ws, None)
            await ws.close()
        return ws

    async def _befehl(self, ws: web.WebSocketResponse, nachricht: dict) -> None:
        self.ws_nachrichten.append(nachricht)
        kennung = nachricht["id"]
        if nachricht["type"] == "subscribe_events":
            self._abos[ws] = kennung
            await ws.send_json({"id": kennung, "type": "result", "success": True, "result": None})
        elif nachricht["type"] == "get_states":
            await ws.send_json({
                "id": kennung, "type": "result", "success": True,
                "result": list(self.zustaende.values()),
            })
        else:
            await ws.send_json({"id": kennung, "type": "result", "success": False,
                                "error": {"code": "unknown_command"}})

    async def _rest(self, anfrage: web.Request) -> web.Response:
        inhalt = await anfrage.json() if anfrage.can_read_body else {}
        self.rest_anfragen.append((anfrage.method, anfrage.path, inhalt))
        self.rest_token.append(anfrage.headers.get("Authorization", ""))
        if anfrage.method == "GET" and anfrage.path == "/core/api/services":
            return web.json_response(self.dienste, status=self.rest_status)
        dienst = anfrage.path.startswith("/core/api/services/") and not anfrage.path.startswith("/core/api/services/notify/")
        status = self.dienst_status if dienst and self.dienst_status is not None else self.rest_status
        return web.json_response({}, status=status)


async def warte_auf(bedingung, timeout_s: float = 3.0) -> None:
    """Wartet, bis die Bedingung wahr ist, sonst schlägt der Test fehl."""
    async with asyncio.timeout(timeout_s):
        while not bedingung():
            await asyncio.sleep(0.005)
