"""Schnittstellen der Oberfläche und der Integration."""
import asyncio
import json
from functools import partial
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from anbindung.eingaenge import Eingaenge
from anbindung.einstellungen import Speicher
from anbindung.ha_client import HaClient
from anbindung.main import App, steuerung_aus_kern
from anbindung.schatten import Verlauf
from anbindung.web import Webserver
from fake_ha import TOKEN, FakeHa, warte_auf
from kern.typen import NEU


def mit_web(pruefung, proxy=None):
    import tempfile

    async def ablauf(tmp):
        async with FakeHa() as ha:
            ha.setzen("sensor.a", "210", {"unit_of_measurement": "°C", "friendly_name": "Abgas"})
            ha.setzen("sensor.f", "20", {"unit_of_measurement": "K"})
            ha.setzen("cover.k", "open", {"friendly_name": "Klappe"})
            ha.setzen("input_select.kamin_abbrand_phase", "Abbrand")
            ha.setzen("input_datetime.kamin_abbrand_phase_seit", "x", {"timestamp": 1794142200})
            ha.setzen("input_number.kamin_abbrand_klappe_soll", "53")
            s = Speicher(tmp / "e.json")
            s.rollen_setzen({"abgas": "sensor.a"}, True)
            app = App(partial(HaClient, TOKEN, ws_url=ha.ws_url, rest_url=ha.rest_url, backoff_start_s=0.01),
                      Eingaenge(s.entitaeten(), "kamin_abbrand"), Verlauf(tmp), steuerung_fabrik=steuerung_aus_kern,
                      speicher=s, takt_paritaet_s=0.05, takt_neu_s=0.05, wache_intervall_s=0.01)
            web = Webserver(app, tmp, proxy=proxy)
            stopp = asyncio.Event()
            aufgabe = asyncio.create_task(app.laufen(stopp))
            await warte_auf(lambda: NEU in app.letzte)
            try:
                async with TestClient(TestServer(web.oberflaeche)) as ober, TestClient(TestServer(web.integration)) as integ:
                    await pruefung(ober, integ, app, ha, tmp)
            finally:
                stopp.set()
                await aufgabe

    with tempfile.TemporaryDirectory() as ordner:
        asyncio.run(ablauf(Path(ordner)))


def test_nur_vom_ingress_proxy():
    async def p(ober, *_):
        assert (await ober.get("/api/status")).status == 403
    mit_web(p, proxy="172.30.32.2")


def test_status_und_seite():
    async def p(ober, _, app, *__):
        assert (await ober.get("/")).status == 200
        d = await (await ober.get("/api/status")).json()
        assert d["phase"] == "Abbrand" and d["abgas"] == 210 and d["beobachtung"] is True and d["klappe_soll"] == 53
    mit_web(p)


def test_katalog_filtert_nach_domain_und_suche():
    async def p(ober, *_):
        d = await (await ober.get("/api/katalog?domains=sensor&q=abgas")).json()
        assert [t["id"] for t in d] == ["sensor.a"]
    mit_web(p)


def test_rollen_warnen_und_wechseln_ohne_neustart():
    async def p(ober, _, app, *__):
        r = await ober.post("/api/rollen", json={"rollen": {"abgas": "sensor.f", "druck": "sensor.fehlt"}, "tuerkontakt_vorhanden": False})
        assert r.status == 200
        await warte_auf(lambda: app.letzte_eingaben.t == 20)
        d = {x["schluessel"]: x for x in (await (await ober.get("/api/rollen")).json())["rollen"]}
        assert "Einheit" in d["abgas"]["warnung"] and "gibt es nicht" in d["druck"]["warnung"]
        schlecht = await ober.post("/api/rollen", json={"rollen": {"abgas": "switch.x"}})
        assert schlecht.status == 400 and "Erlaubt" in (await schlecht.json())["fehler"]
    mit_web(p)


def test_parameter_aendern_landet_im_protokoll():
    async def p(ober, _, app, ha, tmp):
        assert (await ober.post("/api/parameter", json={"name": "regler_verstaerkung", "wert": 0.9})).status == 200
        await warte_auf(lambda: app.parameter_neu.regler_verstaerkung == 0.9)
        zeile = json.loads((tmp / "aenderungen.jsonl").read_text().splitlines()[-1])
        assert zeile["was"] == "regler_verstaerkung" and zeile["neu"] == 0.9 and zeile["zeit"] > 0
    mit_web(p)


def test_autotune_verlauf_sagt_wenn_daten_fehlen_und_aktiv_ist_gesperrt():
    async def p(ober, *_):
        d = await (await ober.post("/api/autotune/verlauf", json={})).json()
        assert d["ok"] is False and "Nicht genug Daten" in d["grund"]
        assert (await ober.post("/api/autotune/uebernehmen", json={})).status == 400
        r = await ober.post("/api/autotune/aktiv", json={})
        assert r.status == 409 and "gesperrt" in (await r.json())["fehler"]
    mit_web(p)


def test_integration_braucht_token():
    async def p(_, integ, app, *__):
        assert (await integ.get("/integration/zustand")).status == 401
        k = {"Authorization": f"Bearer {app.speicher.token}"}
        d = await (await integ.get("/integration/zustand", headers=k)).json()
        assert d["phase"] == "Abbrand" and "kp" in d
    mit_web(p)


def test_integration_beobachtungsbetrieb():
    async def p(_, integ, app, ha, __):
        k = {"Authorization": f"Bearer {app.speicher.token}"}
        vorher = len(ha.rest_anfragen)
        for name in ("handbetrieb", "wartung", "leistung", "klappe_manuell", "t_ziel", "fuehler_neustart"):
            r = await integ.post("/integration/befehl", json={"name": name, "wert": 1}, headers=k)
            assert r.status == 409 and "Beobachtungsbetrieb" in (await r.json())["fehler"]
        assert all("kamin_app" in pf for _, pf, _ in ha.rest_anfragen[vorher:])
        assert (await integ.post("/integration/befehl", json={"name": "kp", "wert": 0.7}, headers=k)).status == 200
        assert app.speicher.parameter_neu["regler_verstaerkung"] == 0.7
        assert (await integ.post("/integration/befehl", json={"name": "tuerkontakt_vorhanden", "wert": False}, headers=k)).status == 200
        assert app.speicher.tuerkontakt_vorhanden is False
    mit_web(p)
