"""Eigene Oberfläche über HA-Ingress und die Schnittstelle für die Integration.

Port 8099 nimmt nur Verbindungen vom Ingress-Proxy an, die Anmeldung erledigt dort HA.
Port 8100 verlangt das Token der Integration. Geschaltet wird nur über `App`: Hier gibt es
keinen eigenen Weg in Home Assistant, und ob der Betrieb erlaubt ist, entscheidet der Server.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hmac
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import aiohttp
from aiohttp import web

from anbindung.anlagenprofil import (Erzeuger, einheit_normal, fehlende_angaben, feld_passt, ist_fahrenheit,
                                     katalog, rolle_passt, rueck_kennlinie, rueck_vorgabe, rueckmeldung_prozent,
                                     zaehler_nach_kwh)
from anbindung.benachrichtigung import ARTEN, dienst_gueltig, geraete_aus_diensten, lesbarer_name
from anbindung.bereitstellung import eigener_host
from anbindung import ansicht
from anbindung.ansicht import (AUTOTUNE_GESPERRT, BEOBACHTUNG_TEXT, BETRIEB_NUR_HA_TEXT, HAND_LAUSCHEN_TEXT,
                               QUITTIEREN_LAUSCHEN_TEXT)
from anbindung.eingaenge import als_zahl
from anbindung.einstellungen import (BETRIEB, EIGENE_FELDER, FELDER, LAUSCHEN, PARAMETER_THEMEN, REGLER_FELDER,
                                     ROLLEN, EinstellungsFehler)
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
MAX_KWH = 1_000_000.0
VERSION = "0.3.9"

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
        anwendung.on_response_prepare.append(_statik_nicht_cachen)
        r = anwendung.router
        r.add_get("/", self._index)
        r.add_get("/api/status", self._status)
        r.add_get("/api/verlauf", self._verlauf)
        r.add_get("/api/wirtschaft", self._wirtschaft)
        r.add_post("/api/wirtschaft", self._wirtschaft_aendern)
        r.add_get("/api/rollen", self._rollen)
        r.add_post("/api/rollen", self._rollen_speichern)
        r.add_get("/api/profil", self._profil)
        r.add_post("/api/profil", self._profil_speichern)
        r.add_get("/api/katalog", self._katalog_abruf)
        r.add_get("/api/parameter", self._parameter)
        r.add_post("/api/parameter", self._parameter_setzen)
        r.add_post("/api/leistung", self._leistung)
        r.add_post("/api/hand", self._hand)
        r.add_post("/api/quittieren", self._quittieren)
        r.add_post("/api/autotune/verlauf", self._autotune_verlauf)
        r.add_post("/api/autotune/uebernehmen", self._autotune_uebernehmen)
        r.add_post("/api/autotune/aktiv", self._autotune_aktiv)
        r.add_get("/api/service", self._service)
        r.add_get("/api/benachrichtigungen", self._benachrichtigungen)
        r.add_post("/api/benachrichtigungen", self._benachrichtigungen_speichern)
        r.add_post("/api/benachrichtigungen/test", self._benachrichtigung_test)
        r.add_post("/api/reinigung/bestaetigen", self._reinigung_bestaetigen)
        r.add_post("/api/betrieb", self._betrieb_setzen)
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
        # Der Versionsanhang erreicht auch Browser, die ein altes app.js noch für frisch halten.
        seite = (STATIK / "index.html").read_text(encoding="utf-8")
        for datei in ("app.js", "app.css"):
            seite = seite.replace(f'statik/{datei}"', f'statik/{datei}?v={VERSION}"')
        return web.Response(text=seite, content_type="text/html", headers={"Cache-Control": "no-cache"})

    async def _status(self, _: web.Request) -> web.Response:
        return web.json_response(ansicht.status(self.app))

    async def _verlauf(self, anfrage: web.Request) -> web.Response:
        stunden = min(max(float(anfrage.query.get("stunden", 3)), 1.0), MAX_STUNDEN)
        daten = await asyncio.to_thread(ansicht.verlauf_lesen, self.ordner, stunden)
        return web.json_response(daten)

    async def _wirtschaft(self, anfrage: web.Request) -> web.Response:
        roh = anfrage.query.get("kwh")
        menge = None
        if roh not in (None, ""):
            menge = als_zahl(roh)
            if menge is None or not 0 < menge <= MAX_KWH:
                raise EinstellungsFehler(f"Die Menge muss zwischen 0 und {MAX_KWH:,.0f} kWh liegen.".replace(",", "."))
        return web.json_response(ansicht.wirtschaft_zeigen(self.app, menge))

    async def _wirtschaft_aendern(self, anfrage: web.Request) -> web.Response:
        """Eine Änderung je Anfrage: einstellungen, eroeffnung, auflage (+1 oder −1) oder referenz_uebernehmen."""
        inhalt = await anfrage.json()
        buch = self.app.buchfuehrung
        if not isinstance(inhalt, dict) or buch is None:
            raise EinstellungsFehler("Die Anfrage ist ungültig.")
        aktion, profil, jetzt = inhalt.get("aktion"), self.app.speicher.anlagenprofil, time.time()
        if aktion == "einstellungen":
            alt = dict(buch.einstellungen)
            buch.einstellungen_setzen(inhalt.get("werte"))
            self.app.verlauf.aenderung(jetzt, "wirtschaft", alt, buch.einstellungen)
        elif aktion == "eroeffnung":
            alt = dict(buch.saldo)
            buch.saldo_setzen(inhalt.get("werte"))
            self.app.verlauf.aenderung(jetzt, "eroeffnungssaldo", alt, buch.saldo)
        elif aktion == "auflage":
            delta = inhalt.get("delta")
            if delta not in (1, -1) or isinstance(delta, bool):
                raise EinstellungsFehler("Eine Auflage lässt sich nur um 1 erhöhen oder verringern.")
            buch.auflage(jetzt, delta, profil)
            self.app.verlauf.aenderung(jetzt, "auflage", None, delta)
        elif aktion == "referenz_uebernehmen":
            alt = dict(buch.einstellungen)
            buch.referenz_uebernehmen(profil)
            self.app.verlauf.aenderung(jetzt, "referenz", alt, buch.einstellungen)
        else:
            raise EinstellungsFehler("Unbekannte Aktion.")
        return web.json_response({"ok": True})

    async def _zustaende(self) -> list[dict]:
        zeit, daten = self._katalog
        if time.time() - zeit > KATALOG_GUELTIG_S:
            daten = await self.app.client.hole_zustaende()
            self._katalog = (time.time(), daten)
        return daten

    async def _zustaende_je_id(self) -> tuple[dict, bool]:
        try:
            return {z["entity_id"]: z for z in await self._zustaende()}, True
        except (VerbindungWeg, asyncio.TimeoutError):
            return {}, False

    async def _rollen(self, _: web.Request) -> web.Response:
        zustaende, verbunden = await self._zustaende_je_id()
        speicher = self.app.speicher
        profil = speicher.anlagenprofil
        return web.json_response({
            "verbunden": verbunden,
            "tuerkontakt_vorhanden": speicher.tuerkontakt_vorhanden,
            "rollen": [self._rolle_zeigen(r, speicher.rollen.get(r.schluessel), zustaende, verbunden)
                       | {"sichtbar": rolle_passt(r.schluessel, profil)} for r in ROLLEN],
            "hinweise": fehlende_angaben(profil, speicher.rollen),
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
        elif rolle.schluessel == "klappe_ist" and eid.split(".", 1)[0] not in ("cover", "light"):
            prozent = rueckmeldung_prozent(als_zahl(zustand.get("state")), einheit, self.app.speicher.anlagenprofil)
            if prozent is None:
                daten["warnung"] = (f"Einheit {einheit} ist unbekannt. Bitte unter Anlage eintragen, "
                                    "welcher Wert zu und welcher auf bedeutet.")
            elif (einheit or "%").strip() != "%":
                daten["umgerechnet"] = round(prozent)
        elif rolle.einheiten and not _einheit_erlaubt(einheit, rolle.einheiten):
            daten["warnung"] = f"Einheit ist {einheit or 'nicht angegeben'}, erlaubt sind {', '.join(rolle.einheiten)}."
        elif rolle.einheit and einheit != rolle.einheit and not (rolle.fahrenheit and ist_fahrenheit(einheit)):
            daten["warnung"] = f"Einheit ist {einheit or 'nicht angegeben'}, erwartet wird {rolle.einheit}."
        return daten

    async def _profil(self, _: web.Request) -> web.Response:
        zustaende, verbunden = await self._zustaende_je_id()
        speicher = self.app.speicher
        profil = speicher.anlagenprofil
        return web.json_response({
            "verbunden": verbunden,
            "profil": profil.als_dict(),
            "katalog": katalog(),
            "zaehler": [_zaehler_zeigen(e, zustaende, verbunden) for e in profil.erzeuger],
            "rueckmeldung": _rueckmeldung_zeigen(speicher.rollen.get("klappe_ist"), zustaende, verbunden, profil),
            "hinweise": fehlende_angaben(profil, speicher.rollen),
        })

    def _im_betrieb_gesperrt(self) -> Optional[web.Response]:
        """Zuweisungen und Anlage bestimmen, was geschaltet wird. Im Betrieb bleiben sie stehen."""
        if not self.app.im_betrieb:
            return None
        return web.json_response({"fehler": "Im Betrieb nicht änderbar. Bitte erst auf Lauschen schalten."}, status=409)

    async def _betrieb_setzen(self, anfrage: web.Request) -> web.Response:
        inhalt = await anfrage.json()
        modus = inhalt.get("modus") if isinstance(inhalt, dict) else None
        try:
            if modus == BETRIEB:
                offen = await self.app.betrieb_anfordern(inhalt.get("bestaetigung"), VERSION)
                if offen:
                    return web.json_response({"fehler": "Der Betrieb ist noch nicht erlaubt.",
                                              "offen": [p.als_dict() for p in offen]}, status=409)
                return web.json_response({"ok": True, "modus": self.app.modus})
            if modus == LAUSCHEN:
                offen = await self.app.lauschen_anfordern()
                return web.json_response({"ok": True, "modus": self.app.modus, "klappe_offen": offen})
        except asyncio.TimeoutError:
            return web.json_response({"fehler": "Die App hat nicht rechtzeitig geantwortet, der Modus bleibt, wie er ist."}, status=503)
        raise EinstellungsFehler("Unbekannter Modus.")

    async def _profil_speichern(self, anfrage: web.Request) -> web.Response:
        if (gesperrt := self._im_betrieb_gesperrt()):
            return gesperrt
        speicher = self.app.speicher
        alt = speicher.anlagenprofil.als_dict()
        profil = speicher.profil_setzen(await anfrage.json())
        self.app.verlauf.aenderung(time.time(), "anlagenprofil", alt, profil.als_dict())
        self.app.zuweisung_uebernehmen()    # Die Zähler der Erzeuger werden ab jetzt mitgelesen.
        self._katalog = (0.0, [])
        return web.json_response({"ok": True})

    async def _rollen_speichern(self, anfrage: web.Request) -> web.Response:
        if (gesperrt := self._im_betrieb_gesperrt()):
            return gesperrt
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
        if name in EIGENE_FELDER:
            # Nur in der App, ohne Live-Helfer und ohne Wert in der Automation.
            eigen = app.speicher.eigener_wert(name)
            return dataclasses.asdict(feld) | {
                "wert": app.speicher.wirksamer_eigener_wert(name),
                "quelle": "Eigener Wert der App" if eigen is not None else "Vorgabe",
                "eigen": eigen is not None, "live": None}
        eigen = app.speicher.parameter_neu.get(name)
        helfer = f"input_number.{app.eingaenge.praefix}_{name}"
        hat_helfer = app.eingaenge.eintrag(helfer) is not None
        quelle = "Eigener Wert der App" if eigen is not None else ("Live-Helfer" if hat_helfer else "Vorgabe")
        wert = eigen if eigen is not None else getattr(app.eingaenge.parameter, name)
        return dataclasses.asdict(feld) | {"wert": wert, "quelle": quelle, "eigen": eigen is not None,
                                            "live": getattr(app.eingaenge.parameter, name)}

    async def _parameter(self, _: web.Request) -> web.Response:
        profil = self.app.speicher.anlagenprofil
        return web.json_response({
            "themen": [{"titel": titel, "felder": zeigen}
                       for titel, felder in PARAMETER_THEMEN
                       if (zeigen := [self._feld_zeigen(f) for f in felder if feld_passt(f.name, profil)])],
            "regler": [self._feld_zeigen(f) for f in REGLER_FELDER],
            "vorschlag": _vorschlag_zeigen(self.app.autotune_vorschlag),
            "autotune_aktiv_gesperrt": AUTOTUNE_GESPERRT,
        })

    def _setzen(self, name: str, wert: Any) -> Any:
        speicher = self.app.speicher
        alt = speicher.eigener_wert(name) if name in EIGENE_FELDER else speicher.parameter_neu.get(name)
        neu = speicher.parameter_setzen(name, wert)
        self.app.verlauf.aenderung(time.time(), name, alt, neu)
        return neu

    async def _parameter_setzen(self, anfrage: web.Request) -> web.Response:
        inhalt = await anfrage.json()
        return web.json_response({"wert": self._setzen(str(inhalt.get("name")), inhalt.get("wert"))})

    def _leistung_setzen(self, modus: Any) -> float:
        speicher, jetzt = self.app.speicher, time.time()
        alt_modus, alt_ziel = speicher.leistung, speicher.parameter_neu.get("t_ziel")
        ziel = speicher.leistung_setzen(modus)
        self.app.verlauf.aenderung(jetzt, "leistung", alt_modus, modus)
        self.app.verlauf.aenderung(jetzt, "t_ziel", alt_ziel, ziel)
        return ziel

    async def _leistung(self, anfrage: web.Request) -> web.Response:
        inhalt = await anfrage.json()
        return web.json_response({"ziel": self._leistung_setzen(inhalt.get("modus"))})

    async def _hand(self, anfrage: web.Request) -> web.Response:
        """Handbetrieb der App: {an, dauer_min?, klappe?}. Nur im Betrieb, Ausschalten geht immer."""
        inhalt = await anfrage.json()
        if not isinstance(inhalt, dict):
            raise ValueError
        an = bool(inhalt.get("an"))
        if an and not self.app.im_betrieb:
            return web.json_response({"fehler": HAND_LAUSCHEN_TEXT}, status=409)
        if inhalt.get("klappe") is not None:
            self._setzen("klappe_manuell", inhalt["klappe"])
        dauer = inhalt.get("dauer_min")
        bis = self.app.hand_setzen(an, None if dauer is None else float(dauer))
        return web.json_response({"ok": True, "bis": bis})

    async def _quittieren(self, _: web.Request) -> web.Response:
        if not self.app.im_betrieb:
            return web.json_response({"fehler": QUITTIEREN_LAUSCHEN_TEXT}, status=409)
        return web.json_response({"ok": True} | await self.app.quittieren())

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

    async def _geraete(self) -> tuple[list[dict], bool]:
        try:
            return geraete_aus_diensten(await self.app.client.hole_dienste()), True
        except (VerbindungWeg, aiohttp.ClientError, OSError, asyncio.TimeoutError) as fehler:
            log.warning("Dienste nicht lesbar: %r", fehler)
            return [], False

    async def _benachrichtigungen(self, _: web.Request) -> web.Response:
        geraete, verbunden = await self._geraete()
        zuordnung = self.app.speicher.benachrichtigungen
        bekannt = {g["dienst"] for g in geraete}
        # Gespeicherte Geräte bleiben sichtbar, auch wenn HA sie gerade nicht liefert.
        fehlend = sorted({d for liste in zuordnung.values() for d in liste} - bekannt)
        geraete += [{"dienst": d, "name": lesbarer_name(d), "handy": d.startswith("notify.mobile_app_"),
                     "fehlt": True} for d in fehlend]
        return web.json_response({
            "verbunden": verbunden,
            "arten": [{"schluessel": a.schluessel, "titel": a.titel, "hilfe": a.hilfe, "dringend": a.dringend}
                      for a in ARTEN],
            "geraete": geraete,
            "zuordnung": zuordnung,
        })

    async def _benachrichtigungen_speichern(self, anfrage: web.Request) -> web.Response:
        inhalt = await anfrage.json()
        speicher = self.app.speicher
        alt = speicher.benachrichtigungen
        neu = speicher.benachrichtigungen_setzen(inhalt.get("zuordnung") if isinstance(inhalt, dict) else None)
        self.app.verlauf.aenderung(time.time(), "benachrichtigungen", alt, neu)
        return web.json_response({"ok": True, "zuordnung": neu})

    async def _benachrichtigung_test(self, anfrage: web.Request) -> web.Response:
        inhalt = await anfrage.json()
        dienst = inhalt.get("dienst") if isinstance(inhalt, dict) else None
        if not dienst_gueltig(dienst):
            raise EinstellungsFehler("Das ist kein Gerät für Nachrichten.")
        try:
            await self.app.client.sende_notify(
                dienst, "🔥 Kamin – Testnachricht", "Das ist eine Testnachricht der Kamin-App. Sie kam an.")
        except (VerbindungWeg, aiohttp.ClientError, OSError, asyncio.TimeoutError) as fehler:
            log.warning("Testnachricht an %s fehlgeschlagen: %r", dienst, fehler)
            return web.json_response(
                {"fehler": "Die Testnachricht ging nicht raus. Ist das Gerät in Home Assistant erreichbar?"}, status=502)
        return web.json_response({"ok": True})

    async def _reinigung_bestaetigen(self, _: web.Request) -> web.Response:
        if not self.app.speicher.anlagenprofil.wassergefuehrt:
            raise EinstellungsFehler("Die Reinigungserinnerung gibt es nur bei wassergeführtem Ofen.")
        alt = self.app.speicher.wartungsstand
        await self.app.reinigung_bestaetigen()
        self.app.verlauf.aenderung(time.time(), "reinigung", alt.letzte_reinigung, self.app.speicher.wartungsstand.letzte_reinigung)
        return web.json_response({"ok": True, "reinigung": ansicht.reinigung_zeigen(self.app)})

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
        a = daten.get("amortisation") or {}
        daten |= {"amortisation_prozent": None if a.get("prozent") is None else round(a["prozent"], 1),
                  "amortisation_netto": None if a.get("netto_eur") is None else round(a["netto_eur"], 2),
                  "waehrung": a.get("waehrung"), "wasser_status": (daten.get("wasser") or {}).get("stufe", "aus")}
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
        if name == "leistung":
            return web.json_response({"ok": True, "ziel": self._leistung_setzen(wert)})
        if name in _BEOBACHTUNG_GESPERRT:
            return await self._befehl_im_modus(name, wert)
        return web.json_response({"fehler": "Unbekannter Befehl."}, status=400)

    async def _befehl_im_modus(self, name: str, wert: Any) -> web.Response:
        """Im Lauschen immer gesperrt. Im Betrieb nur, was die App wirklich ausführen kann."""
        if name == "handbetrieb" and not wert:
            self.app.hand_setzen(False)     # Ausschalten geht immer
            return web.json_response({"ok": True})
        if not self.app.im_betrieb:
            return web.json_response({"fehler": BEOBACHTUNG_TEXT}, status=409)
        if name in _IM_BETRIEB_PARAMETER:
            self._setzen(name, wert)
            return web.json_response({"ok": True})
        if name == "handbetrieb":
            self.app.hand_setzen(bool(wert))
            return web.json_response({"ok": True})
        if name == "quittieren":
            return web.json_response({"ok": True} | await self.app.quittieren())
        if name == "fuehler_neustart":
            if not self.app.fuehler_neustart_moeglich():
                return web.json_response({"fehler": "Für den Fühler-Neustart ist kein Ausgang zugewiesen."}, status=409)
            if not await self.app.fuehler_neustart():
                return web.json_response({"fehler": "Der Fühler-Neustart ging nicht durch."}, status=502)
            return web.json_response({"ok": True})
        return web.json_response({"fehler": BETRIEB_NUR_HA_TEXT}, status=409)


_NUR_APP = {"kp": "regler_verstaerkung", "tn": "regler_nachstellzeit", "kd": "regler_vorhaltung"}
_BEOBACHTUNG_GESPERRT = {"handbetrieb", "wartung", "klappe_manuell", "t_ziel", "fuehler_neustart", "quittieren"}
_IM_BETRIEB_PARAMETER = ("klappe_manuell", "t_ziel")


async def _statik_nicht_cachen(anfrage: web.Request, antwort: web.StreamResponse) -> None:
    """JS und CSS bei jedem Laden nachfragen lassen, damit nach einem Update nie altes JS auf neuem HTML läuft."""
    if anfrage.path.startswith("/statik/"):
        antwort.headers["Cache-Control"] = "no-cache"


def _alter(zustand: dict) -> Optional[float]:
    from anbindung.eingaenge import _zeitstempel
    stempel = _zeitstempel(zustand.get("last_reported")) or _zeitstempel(zustand.get("last_updated"))
    return round(max(0.0, time.time() - stempel), 1) if stempel else None


def _einheit_erlaubt(einheit: Any, erlaubt: tuple[str, ...]) -> bool:
    return isinstance(einheit, str) and einheit.strip().casefold() in {e.casefold() for e in erlaubt}


def _rueckmeldung_zeigen(eid: Optional[str], zustaende: dict, verbunden: bool, profil: Any) -> Optional[dict]:
    """Rückmeldung der Klappe für die Anlage: Einheit, Vorgabe, Messwert und Umrechnung. None ohne Sensor."""
    if not eid or eid.split(".", 1)[0] in ("cover", "light"):
        return None
    zustand = zustaende.get(eid) if verbunden else None
    attribute = (zustand or {}).get("attributes") or {}
    einheit = attribute.get("unit_of_measurement")
    roh = als_zahl(zustand.get("state")) if zustand else None
    return {"entitaet": eid, "einheit": einheit, "vorgabe": rueck_vorgabe(einheit), "wert": roh,
            "kennlinie": rueck_kennlinie(profil, einheit),
            "prozent": rueckmeldung_prozent(roh, einheit, profil)}


def _zaehler_zeigen(erzeuger: Erzeuger, zustaende: dict, verbunden: bool) -> dict:
    daten: dict[str, Any] = {"wert": None, "einheit": None, "kwh": None, "warnung": None}
    if not erzeuger.zaehler or not verbunden:
        return daten
    zustand = zustaende.get(erzeuger.zaehler)
    if zustand is None:
        return daten | {"warnung": "Diese Entität gibt es nicht."}
    einheit = (zustand.get("attributes") or {}).get("unit_of_measurement")
    daten |= {"wert": zustand.get("state"), "einheit": einheit}
    if zustand.get("state") in ("unavailable", "unknown"):
        return daten | {"warnung": "Liefert gerade keinen Wert."}
    if einheit_normal(einheit) != erzeuger.zaehler_einheit:
        return daten | {"warnung": f"Der Sensor meldet {einheit or 'keine Einheit'}, eingestellt ist {erzeuger.zaehler_einheit}."}
    return daten | {"kwh": zaehler_nach_kwh(erzeuger, als_zahl(zustand.get("state")))}


def _vorschlag_zeigen(vorschlag: Any) -> Optional[dict]:
    if vorschlag is None:
        return None
    daten = dataclasses.asdict(vorschlag)
    daten.pop("einzelne", None)
    return daten
