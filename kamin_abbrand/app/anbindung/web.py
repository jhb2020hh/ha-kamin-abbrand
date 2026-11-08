"""Eigene Oberfläche über HA-Ingress und die Schnittstelle für die Integration.

Port 8099 nimmt nur Verbindungen vom Ingress-Proxy an, die Anmeldung erledigt dort HA.
Port 8100 verlangt das Token der Integration. Beide ändern nur Daten der App:
Einen Weg, etwas in Home Assistant zu schalten, gibt es hier nicht.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hmac
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from aiohttp import web

from anbindung.bereitstellung import eigener_host
from anbindung import ansicht
from anbindung.ansicht import AUTOTUNE_GESPERRT, BEOBACHTUNG_TEXT
from anbindung.einstellungen import (FELDER, PARAMETER_THEMEN, REGLER_FELDER, ROLLEN, EinstellungsFehler)
from anbindung.ha_client import VerbindungWeg
from kern import autotune
from kern.typen import Parameter

log = logging.getLogger(__name__)

INGRESS_PROXY = "172.30.32.2"
PORT_OBERFLAECHE = 8099
PORT_INTEGRATION = 8100
STATIK = Path(__file__).parent.parent / "web"
KATALOG_GUELTIG_S = 10.0
MAX_STUNDEN = 12.0
VERSION = "0.2.1"

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


class Webserver:
    def __init__(
        self, app: Any, verlauf_ordner: Path, *, port_oberflaeche: int = PORT_OBERFLAECHE,
        port_integration: int = PORT_INTEGRATION, proxy: Optional[str] = INGRESS_PROXY, host: str = "0.0.0.0",
    ) -> None:
        self.app = app
        self.ordner = Path(verlauf_ordner)
        self._proxy = proxy
        self._host = host
        self._ports = (port_oberflaeche, port_integration)
        self._runner: list[web.AppRunner] = []
        self._katalog: tuple[float, list[dict]] = (0.0, [])
        self.oberflaeche = self._oberflaeche_bauen()
        self.integration = self._integration_bauen()

    # ---- Aufbau -------------------------------------------------------

    def _oberflaeche_bauen(self) -> web.Application:
        anwendung = web.Application(middlewares=[self._nur_ingress, self._fehlerbehandlung])
        r = anwendung.router
        r.add_get("/", self._index)
        r.add_get("/api/status", self._status)
        r.add_get("/api/verlauf", self._verlauf)
        r.add_get("/api/rollen", self._rollen)
        r.add_post("/api/rollen", self._rollen_speichern)
        r.add_get("/api/katalog", self._katalog_abruf)
        r.add_get("/api/parameter", self._parameter)
        r.add_post("/api/parameter", self._parameter_setzen)
        r.add_post("/api/autotune/verlauf", self._autotune_verlauf)
        r.add_post("/api/autotune/uebernehmen", self._autotune_uebernehmen)
        r.add_post("/api/autotune/aktiv", self._autotune_aktiv)
        r.add_get("/api/service", self._service)
        r.add_static("/statik/", STATIK)
        return anwendung

    def _integration_bauen(self) -> web.Application:
        anwendung = web.Application(middlewares=[self._nur_token, self._fehlerbehandlung])
        anwendung.router.add_get("/integration/zustand", self._integration_zustand)
        anwendung.router.add_post("/integration/befehl", self._integration_befehl)
        return anwendung

    async def starten(self) -> None:
        for anwendung, port in zip((self.oberflaeche, self.integration), self._ports):
            runner = web.AppRunner(anwendung, access_log=None)
            await runner.setup()
            await web.TCPSite(runner, self._host, port).start()
            self._runner.append(runner)
        log.info("Oberfläche auf Port %d, Integration auf Port %d", *self._ports)

    async def stoppen(self) -> None:
        for runner in self._runner:
            await runner.cleanup()
        self._runner.clear()

    # ---- Schutz -------------------------------------------------------

    @web.middleware
    async def _nur_ingress(self, anfrage: web.Request, handler: Handler) -> web.StreamResponse:
        if self._proxy is not None and anfrage.remote != self._proxy:
            raise web.HTTPForbidden(text="Nur über Home Assistant erreichbar")
        return await handler(anfrage)

    @web.middleware
    async def _nur_token(self, anfrage: web.Request, handler: Handler) -> web.StreamResponse:
        angabe = anfrage.headers.get("Authorization", "")
        if not hmac.compare_digest(angabe, f"Bearer {self.app.speicher.token}"):
            raise web.HTTPUnauthorized(text="Token falsch")
        return await handler(anfrage)

    @web.middleware
    async def _fehlerbehandlung(self, anfrage: web.Request, handler: Handler) -> web.StreamResponse:
        try:
            return await handler(anfrage)
        except EinstellungsFehler as fehler:
            return web.json_response({"fehler": str(fehler)}, status=400)
        except ValueError:
            return web.json_response({"fehler": "Die Anfrage ist ungültig."}, status=400)

    # ---- Oberfläche ---------------------------------------------------

    async def _index(self, _: web.Request) -> web.StreamResponse:
        return web.FileResponse(STATIK / "index.html", headers={"Cache-Control": "no-cache"})

    async def _status(self, _: web.Request) -> web.Response:
        return web.json_response(ansicht.status(self.app))

    async def _verlauf(self, anfrage: web.Request) -> web.Response:
        stunden = min(max(float(anfrage.query.get("stunden", 3)), 1.0), MAX_STUNDEN)
        daten = await asyncio.to_thread(ansicht.verlauf_lesen, self.ordner, stunden)
        return web.json_response(daten)

    async def _zustaende(self) -> list[dict]:
        zeit, daten = self._katalog
        if time.time() - zeit > KATALOG_GUELTIG_S:
            daten = await self.app.client.hole_zustaende()
            self._katalog = (time.time(), daten)
        return daten

    async def _rollen(self, _: web.Request) -> web.Response:
        try:
            zustaende = {z["entity_id"]: z for z in await self._zustaende()}
            verbunden = True
        except (VerbindungWeg, asyncio.TimeoutError):
            zustaende, verbunden = {}, False
        speicher = self.app.speicher
        return web.json_response({
            "verbunden": verbunden,
            "tuerkontakt_vorhanden": speicher.tuerkontakt_vorhanden,
            "rollen": [self._rolle_zeigen(r, speicher.rollen.get(r.schluessel), zustaende, verbunden) for r in ROLLEN],
        })

    def _rolle_zeigen(self, rolle: Any, eid: Optional[str], zustaende: dict, verbunden: bool) -> dict:
        daten = dataclasses.asdict(rolle) | {"entitaet": eid, "wert": None, "name": None, "alter_s": None, "warnung": None}
        if not eid or not verbunden:
            return daten
        zustand = zustaende.get(eid)
        if zustand is None:
            if not rolle.frei:
                daten["warnung"] = "Diese Entität gibt es nicht."
            return daten
        attribute = zustand.get("attributes") or {}
        einheit = attribute.get("unit_of_measurement")
        daten |= {"wert": zustand.get("state"), "name": attribute.get("friendly_name"), "wert_einheit": einheit,
                  "alter_s": _alter(zustand)}
        if zustand.get("state") in ("unavailable", "unknown"):
            daten["warnung"] = "Liefert gerade keinen Wert."
        elif rolle.einheit and einheit != rolle.einheit:
            daten["warnung"] = f"Einheit ist {einheit or 'nicht angegeben'}, erwartet wird {rolle.einheit}."
        return daten

    async def _rollen_speichern(self, anfrage: web.Request) -> web.Response:
        inhalt = await anfrage.json()
        speicher, alt = self.app.speicher, dict(self.app.speicher.rollen)
        alt_tuer = speicher.tuerkontakt_vorhanden
        speicher.rollen_setzen(inhalt.get("rollen") or {}, bool(inhalt.get("tuerkontakt_vorhanden", True)))
        self.app.verlauf.aenderung(time.time(), "zuweisung", {"rollen": alt, "tuer": alt_tuer},
                                   {"rollen": speicher.rollen, "tuer": speicher.tuerkontakt_vorhanden})
        self.app.zuweisung_uebernehmen()
        self._katalog = (0.0, [])
        return web.json_response({"ok": True})

    async def _katalog_abruf(self, anfrage: web.Request) -> web.Response:
        domains = tuple(d for d in anfrage.query.get("domains", "").split(",") if d)
        suche = anfrage.query.get("q", "").casefold()
        treffer = []
        for zustand in await self._zustaende():
            eid = zustand["entity_id"]
            if domains and eid.split(".", 1)[0] not in domains:
                continue
            attribute = zustand.get("attributes") or {}
            name = str(attribute.get("friendly_name") or eid)
            if suche and suche not in name.casefold() and suche not in eid:
                continue
            treffer.append({"id": eid, "name": name, "wert": zustand.get("state"),
                            "einheit": attribute.get("unit_of_measurement")})
        treffer.sort(key=lambda t: t["name"].casefold())
        return web.json_response(treffer[:80])

    def _feld_zeigen(self, feld: Any) -> dict:
        app, name = self.app, feld.name
        eigen = app.speicher.parameter_neu.get(name)
        helfer = f"input_number.{app.eingaenge.praefix}_{name}"
        hat_helfer = app.eingaenge.eintrag(helfer) is not None
        quelle = "Eigener Wert der App" if eigen is not None else ("Live-Helfer" if hat_helfer else "Vorgabe")
        wert = eigen if eigen is not None else getattr(app.eingaenge.parameter, name)
        return dataclasses.asdict(feld) | {"wert": wert, "quelle": quelle, "eigen": eigen is not None,
                                            "live": getattr(app.eingaenge.parameter, name)}

    async def _parameter(self, _: web.Request) -> web.Response:
        return web.json_response({
            "themen": [{"titel": titel, "felder": [self._feld_zeigen(f) for f in felder]}
                       for titel, felder in PARAMETER_THEMEN],
            "regler": [self._feld_zeigen(f) for f in REGLER_FELDER],
            "vorschlag": _vorschlag_zeigen(self.app.autotune_vorschlag),
            "autotune_aktiv_gesperrt": AUTOTUNE_GESPERRT,
        })

    def _setzen(self, name: str, wert: Any) -> Any:
        speicher = self.app.speicher
        alt = speicher.parameter_neu.get(name)
        neu = speicher.parameter_setzen(name, wert)
        self.app.verlauf.aenderung(time.time(), name, alt, neu)
        return neu

    async def _parameter_setzen(self, anfrage: web.Request) -> web.Response:
        inhalt = await anfrage.json()
        return web.json_response({"wert": self._setzen(str(inhalt.get("name")), inhalt.get("wert"))})

    async def _vorschlag_rechnen(self) -> Any:
        proben = await asyncio.to_thread(ansicht.proben_lesen, self.ordner)
        self.app.autotune_vorschlag = await asyncio.to_thread(autotune.aus_verlauf, proben)
        return self.app.autotune_vorschlag

    async def _autotune_verlauf(self, _: web.Request) -> web.Response:
        return web.json_response(_vorschlag_zeigen(await self._vorschlag_rechnen()))

    async def _autotune_uebernehmen(self, _: web.Request) -> web.Response:
        vorschlag = self.app.autotune_vorschlag
        if vorschlag is None or not vorschlag.ok:
            raise EinstellungsFehler("Es liegt kein gültiger Vorschlag vor.")
        self._setzen("regler_verstaerkung", vorschlag.kp)
        self._setzen("regler_nachstellzeit", vorschlag.tn)
        return web.json_response({"ok": True})

    async def _autotune_aktiv(self, _: web.Request) -> web.Response:
        return web.json_response({"fehler": AUTOTUNE_GESPERRT}, status=409)

    async def _service(self, _: web.Request) -> web.Response:
        app = self.app
        stand = ansicht.status(app)
        verlauf_mb = sum(p.stat().st_size for p in self.ordner.glob("verlauf-*.jsonl")) / 1e6 if self.ordner.exists() else 0
        return web.json_response({
            "version": VERSION,
            "verbunden": app.client.verbunden,
            "laeuft_s": time.time() - app.start_zeit,
            "betrieb_h": stand["betrieb_h"],
            "fehler": stand["fehler"],
            "meldungen": list(app.meldungen),
            "aenderungen": await asyncio.to_thread(ansicht.aenderungen_lesen, self.ordner),
            "ohne_helfer": app.ohne_helfer,
            "verlauf_mb": round(verlauf_mb, 1),
            "integration": app.integration_stand,
            "token": app.speicher.token,
            "host": eigener_host(),
            "port": self._ports[1],
        })

    # ---- Integration --------------------------------------------------

    async def _integration_zustand(self, _: web.Request) -> web.Response:
        daten = ansicht.status(self.app)
        p = self.app.parameter_neu
        self.app.parameter_neu_auffrischen()
        daten |= {"kp": p.regler_verstaerkung, "tn": p.regler_nachstellzeit, "kd": p.regler_vorhaltung,
                  "version": VERSION, "autotune_vorschlag": _vorschlag_zeigen(self.app.autotune_vorschlag)}
        return web.json_response(daten)

    async def _integration_befehl(self, anfrage: web.Request) -> web.Response:
        inhalt = await anfrage.json()
        name, wert = inhalt.get("name"), inhalt.get("wert")
        if name in _NUR_APP:
            self._setzen(_NUR_APP[name], wert)
            return web.json_response({"ok": True})
        if name == "tuerkontakt_vorhanden":
            alt = self.app.speicher.tuerkontakt_vorhanden
            self.app.speicher.tuerkontakt_setzen(bool(wert))
            self.app.verlauf.aenderung(time.time(), name, alt, bool(wert))
            return web.json_response({"ok": True})
        if name == "autotune_verlauf":
            return web.json_response({"ok": True, "vorschlag": _vorschlag_zeigen(await self._vorschlag_rechnen())})
        if name == "autotune_aktiv":
            return web.json_response({"fehler": AUTOTUNE_GESPERRT}, status=409)
        if name in _BEOBACHTUNG_GESPERRT:
            return web.json_response({"fehler": BEOBACHTUNG_TEXT}, status=409)
        return web.json_response({"fehler": "Unbekannter Befehl."}, status=400)


_NUR_APP = {"kp": "regler_verstaerkung", "tn": "regler_nachstellzeit", "kd": "regler_vorhaltung"}
_BEOBACHTUNG_GESPERRT = {"handbetrieb", "wartung", "leistung", "klappe_manuell", "t_ziel", "fuehler_neustart"}


def _alter(zustand: dict) -> Optional[float]:
    from anbindung.eingaenge import _zeitstempel
    stempel = _zeitstempel(zustand.get("last_reported")) or _zeitstempel(zustand.get("last_updated"))
    return round(max(0.0, time.time() - stempel), 1) if stempel else None


def _vorschlag_zeigen(vorschlag: Any) -> Optional[dict]:
    if vorschlag is None:
        return None
    daten = dataclasses.asdict(vorschlag)
    daten.pop("einzelne", None)
    return daten
