"""Schnittstellen der Oberfläche und der Integration."""
import asyncio
import json
import time
from functools import partial
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from anbindung.eingaenge import Eingaenge
from anbindung.einstellungen import Speicher
from anbindung.ha_client import HaClient
from anbindung.main import App, steuerung_aus_kern
from anbindung.schatten import Verlauf
from anbindung.web import VERSION, Webserver
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


def test_profil_lesen_speichern_und_protokollieren():
    async def p(ober, _, app, ha, tmp):
        ha.setzen("sensor.gas", "100", {"unit_of_measurement": "m³"})
        d = await (await ober.get("/api/profil")).json()
        assert d["profil"]["ofen"] == "luftgefuehrt" and d["profil"]["erzeuger"] == []
        assert any("Kein Wärmeerzeuger" in x["text"] for x in d["hinweise"])
        assert [a["schluessel"] for a in d["katalog"]["arten"]][:2] == ["erdgas", "heizoel"]
        profil = {"ofen": "wassergefuehrt", "erzeuger": [{"art": "erdgas", "heizwert": 10, "zustandszahl": 1,
                                                          "zaehler": "sensor.gas", "zaehler_einheit": "m³"}]}
        assert (await ober.post("/api/profil", json=profil)).status == 200
        await warte_auf(lambda: app.eingaenge.zahl("sensor.gas") == 100)
        web_d = await (await ober.get("/api/profil")).json()
        assert web_d["profil"]["erzeuger"][0]["preis"] is None
        assert web_d["zaehler"][0]["kwh"] == 1000 and web_d["zaehler"][0]["warnung"] is None
        assert any("Erdgas: Preis fehlt" in x["text"] for x in web_d["hinweise"])
        assert json.loads((tmp / "aenderungen.jsonl").read_text().splitlines()[-1])["was"] == "anlagenprofil"
    mit_web(p)


def test_wirtschaft_vergleich_je_menge_und_fehlende_angaben():
    async def p(ober, _, app, ha, tmp):
        d = await (await ober.get("/api/wirtschaft")).json()
        assert d["vergleich"]["menge_kwh"] == 1000 and d["vergleich"]["zeilen"] == []
        assert [f["schluessel"] for f in d["vergleich"]["fehlt"]] == ["holz"]
        assert "Rechenhilfe" in d["rechenhilfe"] and d["waehrung"] == "€"
        profil = {"ofen": "luftgefuehrt",
                  "holz": {"preis": 0.18, "preis_einheit": "kg", "heizwert": 4.2, "nutzungsgrad_ofen": 0.8},
                  "erzeuger": [{"art": "erdgas", "anteil": 100, "preis": 0.12, "preis_einheit": "kWh", "nutzungsgrad": 0.9},
                               {"art": "heizoel", "anteil": 0}]}
        assert (await ober.post("/api/profil", json=profil)).status == 200
        d = await (await ober.get("/api/wirtschaft?kwh=500")).json()
        zeilen = {z["schluessel"]: z for z in d["vergleich"]["zeilen"]}
        assert d["vergleich"]["menge_kwh"] == 500 and list(zeilen) == ["holz", "erdgas"]
        assert zeilen["holz"]["je_kwh"] == pytest.approx(0.0536, abs=5e-5)
        assert zeilen["erdgas"]["kosten"] == pytest.approx(66.67, abs=0.01)
        assert [f["schluessel"] for f in d["vergleich"]["fehlt"]] == ["heizoel"]
        assert any("Heizöl: Preis fehlt" in x["text"] for x in d["hinweise"])
        for schlecht in ("0", "-3", "abc", "1e12"):
            assert (await ober.get(f"/api/wirtschaft?kwh={schlecht}")).status == 400
    mit_web(p)


def test_wirtschaft_amortisation_auflagen_saldo_und_referenz():
    async def p(ober, _, app, ha, tmp):
        profil = {"ofen": "luftgefuehrt", "holz": {"preis": 0.2, "preis_einheit": "kg", "heizwert": 4.0, "kg_je_auflage": 5},
                  "erzeuger": [{"art": "erdgas", "anteil": 100, "preis": 0.1, "preis_einheit": "kWh", "nutzungsgrad": 0.9}]}
        assert (await ober.post("/api/profil", json=profil)).status == 200
        d = await (await ober.get("/api/wirtschaft")).json()
        assert d["amortisation"]["status"] == "unbekannt" and d["heute_auflagen"] == 0 and d["kg_je_auflage"] == 5
        assert d["referenz"]["ok"] is False and d["einstellungen"]["aufstellraum_anteil"]["vorgabe"] == 0.4
        ok = await ober.post("/api/wirtschaft", json={"aktion": "einstellungen", "werte": {"investition": 1000, "jahreswaerme_kwh": 3000}})
        assert ok.status == 200
        assert (await ober.post("/api/wirtschaft", json={"aktion": "eroeffnung", "werte": {
            "einsparung_eur": 300, "holz_eur": 100, "kaminwaerme_kwh": 200}})).status == 200
        for _ in range(2):
            assert (await ober.post("/api/wirtschaft", json={"aktion": "auflage", "delta": 1})).status == 200
        assert (await ober.post("/api/wirtschaft", json={"aktion": "auflage", "delta": -1})).status == 200
        d = await (await ober.get("/api/wirtschaft")).json()
        assert d["heute_auflagen"] == 1 and d["tage"][0]["holz_kg"] == 5 and d["tage"][0]["vorlaeufig"] is True
        a = d["amortisation"]
        assert a["status"] == "prognose" and a["art"] == "schaetzung" and a["prozent"] > 15
        s = await (await ober.get("/api/status")).json()
        assert s["amortisation"]["status"] == "prognose" and s["amortisation"]["waehrung"] == "€"
        daten = json.loads((tmp / "wirtschaft.json").read_text())
        assert daten["einstellungen"]["investition"] == 1000 and daten["saldo"]["holz_eur"] == 100
        protokoll = [json.loads(z)["was"] for z in (tmp / "aenderungen.jsonl").read_text().splitlines()]
        assert protokoll[-5:] == ["wirtschaft", "eroeffnungssaldo", "auflage", "auflage", "auflage"]
    mit_web(p)


@pytest.mark.parametrize("anfrage", [
    {"aktion": "auflage", "delta": 2}, {"aktion": "auflage", "delta": True}, {"aktion": "auflage"},
    {"aktion": "einstellungen", "werte": {"investition": -5}}, {"aktion": "einstellungen", "werte": {"x": 1}},
    {"aktion": "eroeffnung", "werte": {"holz_eur": "viel"}}, {"aktion": "referenz_uebernehmen"}, {"aktion": "gibtsnicht"}, [], {}])
def test_wirtschaft_lehnt_ungueltiges_mit_text_ab(anfrage):
    async def p(ober, *_):
        r = await ober.post("/api/wirtschaft", json=anfrage)
        assert r.status == 400 and (await r.json())["fehler"]
    mit_web(p)


def test_waermemengenzaehler_rollen_titel_einheiten_und_sichtbarkeit():
    async def p(ober, _, app, ha, tmp):
        ha.setzen("sensor.wz", "12", {"unit_of_measurement": "MWh"})
        ha.setzen("sensor.wl", "4", {"unit_of_measurement": "kWh"})
        ha.setzen("sensor.wl2", "4000", {"unit_of_measurement": "W"})
        luft = {r["schluessel"]: r for r in (await (await ober.get("/api/rollen")).json())["rollen"]}
        assert luft["waermezaehler"]["titel"] == "Wärmemengenzähler Energie"
        assert luft["waermeleistung"]["titel"] == "Wärmemengenzähler Leistung"
        assert not luft["waermezaehler"]["sichtbar"] and not luft["waermeleistung"]["sichtbar"]
        assert (await ober.post("/api/profil", json={"ofen": "wassergefuehrt"})).status == 200
        rollen = {"abgas": "sensor.a", "waermezaehler": "sensor.wz", "waermeleistung": "sensor.wl"}
        assert (await ober.post("/api/rollen", json={"rollen": rollen})).status == 200
        d = {r["schluessel"]: r for r in (await (await ober.get("/api/rollen")).json())["rollen"]}
        assert d["waermezaehler"]["sichtbar"] and d["waermezaehler"]["warnung"] is None          # MWh ist erlaubt
        assert "erlaubt sind W, kW" in d["waermeleistung"]["warnung"]                            # kWh ist keine Leistung
        rollen["waermeleistung"] = "sensor.wl2"
        await ober.post("/api/rollen", json={"rollen": rollen})
        d = {r["schluessel"]: r for r in (await (await ober.get("/api/rollen")).json())["rollen"]}
        assert d["waermeleistung"]["warnung"] is None
        assert (await ober.post("/api/rollen", json={"rollen": {"waermeleistung": "switch.x"}})).status == 400
        assert "Energie" in " ".join(h["text"] for h in (await (await ober.get("/api/rollen")).json())["hinweise"])
    mit_web(p)


def test_wasser_grenzen_nur_bei_wassergefuehrt_mit_vorgabe_und_pruefung():
    async def p(ober, _, app, *__):
        def felder(d):
            return {f["name"]: f for t in d["themen"] for f in t["felder"]}
        assert not any(n.startswith("wasser_") for n in felder(await (await ober.get("/api/parameter")).json()))
        await ober.post("/api/profil", json={"ofen": "wassergefuehrt"})
        f = felder(await (await ober.get("/api/parameter")).json())
        assert f["wasser_ruecklauf_min"]["wert"] == 55 and f["wasser_ruecklauf_min"]["quelle"] == "Vorgabe"
        assert f["wasser_vorlauf_warnung"]["wert"] == 90 and f["wasser_vorlauf_alarm"]["wert"] == 95
        assert (await ober.post("/api/parameter", json={"name": "wasser_vorlauf_warnung", "wert": 85})).status == 200
        f = felder(await (await ober.get("/api/parameter")).json())
        assert f["wasser_vorlauf_warnung"]["wert"] == 85 and f["wasser_vorlauf_warnung"]["eigen"] is True
        assert app.speicher.wasser_grenzen(85, 60).vorlauf_warnung == 85
        assert (await ober.post("/api/parameter", json={"name": "wasser_vorlauf_warnung", "wert": 500})).status == 400
        assert (await ober.post("/api/parameter", json={"name": "wasser_vorlauf_warnung", "wert": None})).status == 200
        assert felder(await (await ober.get("/api/parameter")).json())["wasser_vorlauf_warnung"]["wert"] == 90
    mit_web(p)


def test_status_zeigt_wasserkarte_nur_bei_wassergefuehrt():
    async def p(ober, integ, app, *__):
        assert (await (await ober.get("/api/status")).json())["wasser"] is None
        await ober.post("/api/profil", json={"ofen": "wassergefuehrt"})
        w = (await (await ober.get("/api/status")).json())["wasser"]
        assert w["stufe"] == "ok" and w["befunde"] == [] and w["leistung_kw"] is None
        k = {"Authorization": f"Bearer {app.speicher.token}"}
        assert (await (await integ.get("/integration/zustand", headers=k)).json())["wasser_status"] == "ok"
    mit_web(p)


def test_profil_fehler_nennt_den_grund():
    async def p(ober, *_):
        r = await ober.post("/api/profil", json={"ofen": "x"})
        assert r.status == 400 and "Ofenart" in (await r.json())["fehler"]
        assert (await ober.post("/api/profil", data="{kaputt")).status == 400
    mit_web(p)


def test_zaehler_warnt_bei_falscher_einheit_und_fehlender_entitaet():
    async def p(ober, _, app, ha, __):
        ha.setzen("sensor.kwh", "5", {"unit_of_measurement": "kWh"})
        await ober.post("/api/profil", json={"ofen": "luftgefuehrt", "erzeuger": [
            {"art": "erdgas", "zaehler": "sensor.kwh", "zaehler_einheit": "m³"},
            {"art": "strom", "zaehler": "sensor.gibtsnicht", "zaehler_einheit": "kWh"}]})
        a, b = (await (await ober.get("/api/profil")).json())["zaehler"]
        assert "meldet kWh" in a["warnung"] and a["kwh"] is None and "gibt es nicht" in b["warnung"]
    mit_web(p)


def test_luftgefuehrt_zeigt_keine_wasserrollen_und_behaelt_zuweisungen():
    async def p(ober, _, app, *__):
        await ober.post("/api/rollen", json={"rollen": {"abgas": "sensor.a", "t_wt": "sensor.f"}, "tuerkontakt_vorhanden": True})
        await ober.post("/api/profil", json={"ofen": "luftgefuehrt"})
        d = {x["schluessel"]: x for x in (await (await ober.get("/api/rollen")).json())["rollen"]}
        assert not any(d[r]["sichtbar"] for r in ("t_wt", "vorlauf", "ruecklauf", "pumpe", "puffer_unten", "puffer_oben", "waermezaehler"))
        assert d["abgas"]["sichtbar"] and d["energiezaehler"]["sichtbar"] and d["t_wt"]["entitaet"] == "sensor.f"
        await ober.post("/api/profil", json={"ofen": "wassergefuehrt", "puffer": True, "pumpe_extern": True})
        d = {x["schluessel"]: x for x in (await (await ober.get("/api/rollen")).json())["rollen"]}
        assert all(d[r]["sichtbar"] for r in ("t_wt", "vorlauf", "pumpe", "puffer_oben"))
        rollen = await (await ober.get("/api/rollen")).json()
        assert any("Vorlauf" in x["text"] for x in rollen["hinweise"])
    mit_web(p)


def test_einstellungen_zeigen_nur_passende_felder():
    async def p(ober, *_):
        namen = lambda d: {f["name"] for t in d["themen"] for f in t["felder"]}
        await ober.post("/api/profil", json={"ofen": "luftgefuehrt"})
        d = await (await ober.get("/api/parameter")).json()
        assert "t_wasser_max" not in namen(d) and "t_ziel" in namen(d)
        await ober.post("/api/profil", json={"ofen": "wassergefuehrt"})
        assert "t_wasser_max" in namen(await (await ober.get("/api/parameter")).json())
    mit_web(p)


def test_wasserparameter_nur_wassergefuehrt_und_wert_bleibt():
    wasser = {"t_wasser_max", "klappe_wasser", "feuer_twt_anstieg"}

    async def p(ober, _, app, *__):
        namen = lambda d: {f["name"] for t in d["themen"] for f in t["felder"]}
        await ober.post("/api/profil", json={"ofen": "wassergefuehrt", "puffer": True})
        d = await (await ober.get("/api/parameter")).json()
        assert wasser | {"puffer_voll"} <= namen(d)
        assert (await ober.post("/api/parameter", json={"name": "t_wasser_max", "wert": 90})).status == 200
        assert (await ober.post("/api/parameter", json={"name": "puffer_voll", "wert": 70})).status == 200
        await ober.post("/api/profil", json={"ofen": "luftgefuehrt", "puffer": True})
        d = await (await ober.get("/api/parameter")).json()
        assert not (wasser | {"puffer_voll"}) & namen(d) and "t_ziel" in namen(d)
        assert app.speicher.parameter_neu["t_wasser_max"] == 90 and app.speicher.parameter_neu["puffer_voll"] == 70
        await ober.post("/api/profil", json={"ofen": "wassergefuehrt", "puffer": True})
        d = await (await ober.get("/api/parameter")).json()
        wert = {f["name"]: f["wert"] for t in d["themen"] for f in t["felder"]}
        assert wert["t_wasser_max"] == 90 and wert["puffer_voll"] == 70
    mit_web(p)


def test_fahrenheit_an_abgas_und_neuen_rollen_ohne_warnung():
    async def p(ober, _, app, ha, __):
        ha.setzen("sensor.abgas_f", "392", {"unit_of_measurement": "°F"})
        ha.setzen("sensor.aussen_f", "50", {"unit_of_measurement": "°F"})
        ha.setzen("sensor.wt_f", "150", {"unit_of_measurement": "°F"})
        await ober.post("/api/profil", json={"ofen": "wassergefuehrt"})
        await ober.post("/api/rollen", json={"rollen": {"abgas": "sensor.abgas_f", "t_aussen": "sensor.aussen_f", "t_wt": "sensor.wt_f"}})
        await warte_auf(lambda: app.letzte_eingaben.t == 200)
        status = await (await ober.get("/api/status")).json()
        assert status["abgas"] == 200 and app.eingaenge.temperatur_c("sensor.aussen_f") == 10
        d = {x["schluessel"]: x for x in (await (await ober.get("/api/rollen")).json())["rollen"]}
        assert d["abgas"]["warnung"] is None and d["t_aussen"]["warnung"] is None
        # T.WT geht unverändert in den Kern, deshalb bleibt hier die Warnung.
        assert "Einheit" in d["t_wt"]["warnung"]
    mit_web(p)


def test_profil_schreibt_nichts_nach_home_assistant():
    async def p(ober, _, app, ha, __):
        vorher = len(ha.rest_anfragen)
        await ober.post("/api/profil", json={"ofen": "wassergefuehrt", "erzeuger": [{"art": "strom"}]})
        await asyncio.sleep(0.1)
        assert all("/core/api/states/sensor.kamin_app_" in pf for _, pf, _ in ha.rest_anfragen[vorher:])
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
        assert d["amortisation_prozent"] is None and d["amortisation_netto"] == 0 and d["waehrung"] == "€"
        app.buchfuehrung.einstellungen_setzen({"investition": 800})
        app.buchfuehrung.saldo_setzen({"einsparung_eur": 300.456, "kaminwaerme_kwh": 100})
        d = await (await integ.get("/integration/zustand", headers=k)).json()
        assert d["amortisation_prozent"] == 37.6 and d["amortisation_netto"] == 300.46
    mit_web(p)


def test_integration_im_lauschen():
    async def p(_, integ, app, ha, __):
        k = {"Authorization": f"Bearer {app.speicher.token}"}
        vorher = len(ha.rest_anfragen)
        for name in ("handbetrieb", "wartung", "klappe_manuell", "t_ziel", "fuehler_neustart"):
            r = await integ.post("/integration/befehl", json={"name": name, "wert": 1}, headers=k)
            assert r.status == 409 and "Lauschen" in (await r.json())["fehler"]
        assert all("kamin_app" in pf for _, pf, _ in ha.rest_anfragen[vorher:])
        assert (await integ.post("/integration/befehl", json={"name": "kp", "wert": 0.7}, headers=k)).status == 200
        assert app.speicher.parameter_neu["regler_verstaerkung"] == 0.7
        assert (await integ.post("/integration/befehl", json={"name": "tuerkontakt_vorhanden", "wert": False}, headers=k)).status == 200
        assert app.speicher.tuerkontakt_vorhanden is False
    mit_web(p)


# ---- Benachrichtigungen und Reinigung ---------------------------------------

DIENSTE = [
    {"domain": "light", "services": {"turn_on": {}}},
    {"domain": "notify", "services": {"send_message": {}, "persistent_notification": {}, "zebra": {},
                                       "mobile_app_handy_b": {}, "mobile_app_handy_a": {}}},
]


def test_benachrichtigungen_matrix_geraete_und_speichern():
    async def p(ober, _, app, ha, tmp):
        ha.dienste = DIENSTE
        d = await (await ober.get("/api/benachrichtigungen")).json()
        assert d["verbunden"] is True and d["zuordnung"] == {}
        assert [g["dienst"] for g in d["geraete"]] == ["notify.mobile_app_handy_a", "notify.mobile_app_handy_b", "notify.zebra"]
        assert d["geraete"][0]["name"] == "Handy A" and d["geraete"][0]["handy"] is True
        assert [a["schluessel"] for a in d["arten"]] == ["nachlegen", "stoerung", "reinigung", "betrieb",
                                                         "wasser_hinweis", "wasser_warnung", "wasser_alarm"]
        assert all(a["titel"] for a in d["arten"])
        r = await ober.post("/api/benachrichtigungen", json={"zuordnung": {"stoerung": ["notify.mobile_app_handy_a"]}})
        assert r.status == 200
        assert app.speicher.benachrichtigungen == {"stoerung": ["notify.mobile_app_handy_a"]}
        assert (await (await ober.get("/api/benachrichtigungen")).json())["zuordnung"] == {"stoerung": ["notify.mobile_app_handy_a"]}
        for schlecht in ({"zuordnung": {"stoerung": ["light.turn_on"]}}, {"zuordnung": {"stoerung": ["notify.send_message"]}},
                         {"zuordnung": {"gibt_es_nicht": []}}, {"zuordnung": []}, {}):
            assert (await ober.post("/api/benachrichtigungen", json=schlecht)).status == 400
        assert app.speicher.benachrichtigungen == {"stoerung": ["notify.mobile_app_handy_a"]}
    mit_web(p)


def test_benachrichtigungen_ohne_verbindung_klare_meldung_und_zuordnung_bleibt():
    async def p(ober, _, app, ha, tmp):
        app.speicher.benachrichtigungen_setzen({"nachlegen": ["notify.mobile_app_handy_a"]})
        ha.rest_status = 500
        d = await (await ober.get("/api/benachrichtigungen")).json()
        assert d["verbunden"] is False
        assert d["zuordnung"] == {"nachlegen": ["notify.mobile_app_handy_a"]}
        assert [(g["dienst"], g.get("fehlt")) for g in d["geraete"]] == [("notify.mobile_app_handy_a", True)]
    mit_web(p)


def test_testnachricht():
    async def p(ober, _, app, ha, tmp):
        r = await ober.post("/api/benachrichtigungen/test", json={"dienst": "notify.mobile_app_handy_a"})
        assert r.status == 200
        (dienst, inhalt), = ha.gesendet()
        assert dienst == "notify.mobile_app_handy_a" and "Testnachricht" in inhalt["title"] and "data" not in inhalt
        for schlecht in ({"dienst": "light.turn_on"}, {"dienst": "notify.send_message"}, {"dienst": "notify.a/../x"}, {}):
            assert (await ober.post("/api/benachrichtigungen/test", json=schlecht)).status == 400
        assert len(ha.gesendet()) == 1
        ha.rest_status = 500
        r = await ober.post("/api/benachrichtigungen/test", json={"dienst": "notify.mobile_app_handy_a"})
        assert r.status == 502 and "Testnachricht" in (await r.json())["fehler"]
    mit_web(p)


def test_reinigung_nur_bei_wassergefuehrt_mit_karte_und_bestaetigung():
    async def p(ober, _, app, ha, tmp):
        s = app.speicher
        assert (await (await ober.get("/api/status")).json())["reinigung"] is None
        assert (await ober.post("/api/reinigung/bestaetigen")).status == 400
        themen = [t["titel"] for t in (await (await ober.get("/api/parameter")).json())["themen"]]
        assert "Wartung" not in themen

        assert (await ober.post("/api/profil", json={"ofen": "wassergefuehrt"})).status == 200
        themen = {t["titel"]: t["felder"] for t in (await (await ober.get("/api/parameter")).json())["themen"]}
        feld = themen["Wartung"][0]
        assert feld["name"] == "wt_reinigung_h" and feld["wert"] == 100 and feld["quelle"] == "Vorgabe" and feld["live"] is None
        assert "Hersteller" in feld["hilfe"]
        assert (await ober.post("/api/parameter", json={"name": "wt_reinigung_h", "wert": 50})).status == 200
        assert s.reinigung_intervall_h == 50

        r = (await (await ober.get("/api/status")).json())["reinigung"]
        assert r["intervall_h"] == 50 and not r["faellig"] and not r["bald"]
        s.daten["betrieb_s"] = 48 * 3600
        r = (await (await ober.get("/api/status")).json())["reinigung"]
        assert r["bald"] and not r["faellig"] and r["rest_h"] == 2
        s.daten["betrieb_s"] = 60 * 3600
        assert (await (await ober.get("/api/status")).json())["reinigung"]["faellig"]

        assert (await ober.post("/api/reinigung/bestaetigen")).status == 200
        r = (await (await ober.get("/api/status")).json())["reinigung"]
        assert not r["faellig"] and r["gefahren_h"] == 0 and r["letzte"] is not None

        await ober.post("/api/parameter", json={"name": "wt_reinigung_h", "wert": 0})
        assert (await (await ober.get("/api/status")).json())["reinigung"] is None
    mit_web(p)


# ---- Lauschen und Betrieb -------------------------------------------------------

BESTAETIGT = {"co_melder": True, "hersteller": True, "verantwortung": True}
KLAPPE, HB, AUTO = "cover.k", "number.hb", "automation.a"


async def bereit_machen(ober, app, ha, **rollen):
    ha.setzen(KLAPPE, "open", {"current_position": 100})
    ha.setzen(HB, "0", {"min": 0, "max": 1000})
    ha.setzen(AUTO, "off")
    alle = {"abgas": "sensor.a", "ausgang_klappe": KLAPPE, "ausgang_heartbeat": HB, "konkurrierende_automation": AUTO} | rollen
    r = await ober.post("/api/rollen", json={"rollen": {k: v for k, v in alle.items() if v}, "tuerkontakt_vorhanden": False})
    assert r.status == 200
    await warte_auf(lambda: all(app.eingaenge.eintrag(e) for e in alle.values() if e))


async def einschalten(ober, **bestaetigung):
    return await ober.post("/api/betrieb", json={"modus": "betrieb", "bestaetigung": BESTAETIGT | bestaetigung})


async def _ohne_motor(ober, app, ha):
    await ober.post("/api/profil", json={"ofen": "luftgefuehrt", "klappe_motorisiert": False})


async def _ohne_klappe(ober, app, ha):
    await bereit_machen(ober, app, ha, ausgang_klappe=None)


async def _klappe_weg(ober, app, ha):
    await ha.aendern(KLAPPE, "unavailable")
    await warte_auf(lambda: app.eingaenge.wert(KLAPPE) == "unavailable")


async def _ohne_abgas(ober, app, ha):
    await bereit_machen(ober, app, ha, abgas=None)


async def _abgas_weg(ober, app, ha):
    await ha.aendern("sensor.a", "unavailable")
    await warte_auf(lambda: app.eingaenge.wert("sensor.a") == "unavailable")


async def _abgas_unplausibel(ober, app, ha):
    await ha.aendern("sensor.a", "1638.3", {"unit_of_measurement": "°C"})
    await warte_auf(lambda: app.eingaenge.wert("sensor.a") == "1638.3")


async def _abgas_alt(ober, app, ha):
    await ober.post("/api/parameter", json={"name": "fuehler_timeout", "wert": 60})
    await ha.aendern("sensor.a", "210", {"unit_of_measurement": "°C"}, zeit=time.time() - 3600)
    ha.setzen("sensor.a", "210", {"unit_of_measurement": "°C"}, zeit=time.time() - 3600)
    await warte_auf(lambda: time.time() - app.eingaenge.eintrag("sensor.a").gemeldet > 3000)
    app.parameter_neu_auffrischen()


async def _getrennt(ober, app, ha):
    app.client._verbunden = False


async def _neu_fehlt(ober, app, ha):
    app.steuerungen.pop(NEU)


async def _automation_an(ober, app, ha):
    await ha.aendern(AUTO, "on")
    await warte_auf(lambda: app.eingaenge.wert(AUTO) == "on")


async def _automation_unbekannt(ober, app, ha):
    await ha.aendern(AUTO, "unavailable")
    await warte_auf(lambda: app.eingaenge.wert(AUTO) == "unavailable")


async def _ohne_watchdog(ober, app, ha):
    await bereit_machen(ober, app, ha, ausgang_heartbeat=None)


@pytest.mark.parametrize("stoeren, schluessel", [
    (_ohne_motor, "motorisiert"), (_ohne_klappe, "klappe"), (_klappe_weg, "klappe_erreichbar"), (_ohne_abgas, "abgas"),
    (_abgas_weg, "abgas_frisch"), (_abgas_unplausibel, "abgas_frisch"), (_abgas_alt, "abgas_frisch"), (_getrennt, "verbunden"),
    (_neu_fehlt, "neu"), (_automation_an, "automation"), (_automation_unbekannt, "automation"), (_ohne_watchdog, "watchdog"),
])
def test_jede_bedingung_sperrt_einzeln(stoeren, schluessel):
    async def p(ober, _, app, ha, tmp):
        await bereit_machen(ober, app, ha)
        await stoeren(ober, app, ha)
        vorher = len(ha.schaltbefehle())
        r = await einschalten(ober)
        d = await r.json()
        assert r.status == 409 and [x["schluessel"] for x in d["offen"]] == [schluessel] and d["offen"][0]["text"]
        assert app.modus == "lauschen" and app.speicher.modus == "lauschen" and not app.client.betrieb_aktiv
        assert len(ha.schaltbefehle()) == vorher == 0
        status = await (await ober.get("/api/status")).json()
        assert status["beobachtung"] is True and status["modus"] == "lauschen"
        assert schluessel in [x["schluessel"] for x in status["betrieb"]["offene_punkte"]]
    mit_web(p)


@pytest.mark.parametrize("fehlt", ["co_melder", "hersteller", "verantwortung"])
def test_jede_bestaetigung_sperrt_einzeln(fehlt):
    async def p(ober, _, app, ha, tmp):
        await bereit_machen(ober, app, ha)
        r = await ober.post("/api/betrieb", json={"modus": "betrieb", "bestaetigung": BESTAETIGT | {fehlt: False}})
        d = await r.json()
        assert r.status == 409 and [x["schluessel"] for x in d["offen"]] == ["bestaetigung"]
        assert app.modus == "lauschen" and not app.speicher.bestaetigt
    mit_web(p)


def test_betrieb_ohne_watchdog_nur_mit_ausdruecklicher_bestaetigung():
    async def p(ober, _, app, ha, tmp):
        await bereit_machen(ober, app, ha, ausgang_heartbeat=None)
        assert (await einschalten(ober)).status == 409
        r = await einschalten(ober, ohne_watchdog=True)
        assert r.status == 200 and app.modus == "betrieb"
        gespeichert = json.loads((tmp / "e.json").read_text())["betrieb_bestaetigung"]["ohne_watchdog"]
        assert gespeichert["datum"] and gespeichert["version"] == VERSION
        await warte_auf(lambda: ha.schaltbefehle())
        assert not any(d == "number.set_value" for d, _ in ha.schaltbefehle())
    mit_web(p)


def test_umschalten_hin_und_zurueck():
    async def p(ober, _, app, ha, tmp):
        await bereit_machen(ober, app, ha)
        assert (await ober.get("/api/status")).status == 200
        r = await einschalten(ober)
        assert r.status == 200 and (await r.json())["modus"] == "betrieb"
        datei = json.loads((tmp / "e.json").read_text())
        assert datei["modus"] == "betrieb"
        best = datei["betrieb_bestaetigung"]["betrieb"]
        assert best["co_melder"] and best["hersteller"] and best["verantwortung"] and best["version"] == VERSION and best["datum"]
        await warte_auf(lambda: any(d == "cover.set_cover_position" for d, _ in ha.schaltbefehle()))
        await warte_auf(lambda: any(d == "number.set_value" for d, _ in ha.schaltbefehle()))
        status = await (await ober.get("/api/status")).json()
        assert status["modus"] == "betrieb" and status["beobachtung"] is False and status["quelle"] == "app"
        assert status["betrieb"]["bestaetigt"] is True

        r = await ober.post("/api/betrieb", json={"modus": "lauschen"})
        d = await r.json()
        assert r.status == 200 and d["modus"] == "lauschen" and d["klappe_offen"] is True
        klappe = [k for x, k in ha.schaltbefehle() if x == "cover.set_cover_position"]
        assert klappe[-1] == {"position": 100, "entity_id": KLAPPE}
        assert json.loads((tmp / "e.json").read_text())["modus"] == "lauschen"
        anzahl = len(ha.schaltbefehle())
        await asyncio.sleep(0.15)
        assert len(ha.schaltbefehle()) == anzahl and not app.client.betrieb_aktiv
    mit_web(p)


def test_umschalten_sendet_push_betrieb():
    async def p(ober, _, app, ha, tmp):
        app.speicher.benachrichtigungen_setzen({"betrieb": ["notify.mobile_app_a"]})
        await bereit_machen(ober, app, ha)
        await einschalten(ober)
        await ober.post("/api/betrieb", json={"modus": "lauschen"})
        await app.benachrichtiger.abwarten()
        texte = [k["message"] for _, k in ha.gesendet()]
        assert any("steuert jetzt" in t for t in texte) and any(t.startswith("Lauschen") for t in texte)
        assert all(k.get("data") == {"push": {"interruption-level": "time-sensitive"}} for _, k in ha.gesendet())
    mit_web(p)


def test_unbekannter_modus_und_kaputte_anfrage():
    async def p(ober, _, app, ha, tmp):
        assert (await ober.post("/api/betrieb", json={"modus": "steuern"})).status == 400
        assert (await ober.post("/api/betrieb", json=[])).status == 400
        assert (await ober.post("/api/betrieb", data="{kaputt")).status == 400
        assert app.modus == "lauschen"
    mit_web(p)


def test_betrieb_ist_nur_ueber_ingress_erreichbar():
    async def p(ober, *_):
        assert (await ober.post("/api/betrieb", json={"modus": "betrieb"})).status == 403
    mit_web(p, proxy="172.30.32.2")


def test_zuweisung_und_profil_im_betrieb_gesperrt():
    async def p(ober, _, app, ha, tmp):
        await bereit_machen(ober, app, ha)
        await einschalten(ober)
        for pfad, inhalt in (("/api/rollen", {"rollen": {"abgas": "sensor.a"}}), ("/api/profil", {"ofen": "luftgefuehrt", "klappe_invertiert": True})):
            r = await ober.post(pfad, json=inhalt)
            assert r.status == 409 and "Lauschen" in (await r.json())["fehler"]
        assert app.speicher.rollen["ausgang_klappe"] == KLAPPE and app.speicher.anlagenprofil.klappe_invertiert is False
    mit_web(p)


def test_integration_im_betrieb():
    async def p(ober, integ, app, ha, tmp):
        k = {"Authorization": f"Bearer {app.speicher.token}"}
        await bereit_machen(ober, app, ha)
        await einschalten(ober)
        await warte_auf(lambda: ha.schaltbefehle())

        for name, wert in (("t_ziel", 250), ("klappe_manuell", 40)):
            r = await integ.post("/integration/befehl", json={"name": name, "wert": wert}, headers=k)
            assert r.status == 200 and app.speicher.parameter_neu[name] == wert
        assert (await integ.post("/integration/befehl", json={"name": "t_ziel", "wert": 9999}, headers=k)).status == 400

        r = await integ.post("/integration/befehl", json={"name": "fuehler_neustart"}, headers=k)
        assert r.status == 409 and "kein Ausgang" in (await r.json())["fehler"]
        for name in ("handbetrieb", "wartung"):
            r = await integ.post("/integration/befehl", json={"name": name, "wert": True}, headers=k)
            assert r.status == 409 and "Home Assistant" in (await r.json())["fehler"]
        r = await integ.post("/integration/befehl", json={"name": "autotune_aktiv"}, headers=k)
        assert r.status == 409 and "gesperrt" in (await r.json())["fehler"]
        r = await ober.post("/api/autotune/aktiv", json={})
        assert r.status == 409 and "gesperrt" in (await r.json())["fehler"]
    mit_web(p)


def test_fuehler_neustart_im_betrieb():
    async def p(ober, integ, app, ha, tmp):
        k = {"Authorization": f"Bearer {app.speicher.token}"}
        ha.setzen("button.n", "x")
        await bereit_machen(ober, app, ha, ausgang_neustart="button.n")
        r = await integ.post("/integration/befehl", json={"name": "fuehler_neustart"}, headers=k)
        assert r.status == 409 and "Lauschen" in (await r.json())["fehler"] and ha.schaltbefehle() == []
        await einschalten(ober)
        assert (await integ.post("/integration/befehl", json={"name": "fuehler_neustart"}, headers=k)).status == 200
        assert ("button.press", {"entity_id": "button.n"}) in ha.schaltbefehle()
    mit_web(p)


def test_rolle_konkurrierende_automation_wird_nur_gelesen():
    async def p(ober, _, app, ha, tmp):
        await bereit_machen(ober, app, ha)
        d = {x["schluessel"]: x for x in (await (await ober.get("/api/rollen")).json())["rollen"]}
        assert d["konkurrierende_automation"]["domains"] == ["automation"] and d["konkurrierende_automation"]["art"] == "eingang"
        schlecht = await ober.post("/api/rollen", json={"rollen": {"abgas": "sensor.a", "konkurrierende_automation": "switch.x"}})
        assert schlecht.status == 400
        await einschalten(ober)
        await warte_auf(lambda: ha.schaltbefehle())
        assert AUTO not in {k["entity_id"] for _, k in ha.schaltbefehle()}
    mit_web(p)


def test_neue_installation_startet_im_lauschen():
    import tempfile
    with tempfile.TemporaryDirectory() as ordner:
        s = Speicher(Path(ordner) / "e.json")
        assert s.modus == "lauschen" and s.bestaetigt is False
        kaputt = Path(ordner) / "k.json"
        kaputt.write_text('{"modus": "steuern", "betrieb_bestaetigung": 5}', encoding="utf-8")
        s = Speicher(kaputt)
        assert s.modus == "lauschen" and s.bestaetigt is False


def test_seite_laedt_js_und_css_mit_version_und_ohne_cache():
    async def p(ober, *_):
        r = await ober.get("/")
        seite = await r.text()
        assert r.headers["Cache-Control"] == "no-cache"
        assert f'statik/app.js?v={VERSION}"' in seite and f'statik/app.css?v={VERSION}"' in seite
        for datei in ("app.js", "app.css"):
            r = await ober.get(f"/statik/{datei}?v={VERSION}")
            assert r.status == 200 and r.headers["Cache-Control"] == "no-cache"
    mit_web(p)


def test_leistung_setzt_nur_das_ziel_der_app():
    async def p(ober, integ, app, ha, __):
        ha.setzen("input_select.kamin_abbrand_leistungsmodus", "Normal")
        await warte_auf(lambda: app.eingaenge.wert("input_select.kamin_abbrand_leistungsmodus") == "Normal")
        vorher = len(ha.rest_anfragen)
        assert not app.im_betrieb
        s = await (await ober.get("/api/status")).json()
        assert s["leistung"] == "Normal" and s["leistung_live"] == "Normal" and s["leistung_eigen"] is False

        r = await ober.post("/api/leistung", json={"modus": "Power"})
        assert r.status == 200 and (await r.json())["ziel"] == 270
        assert app.speicher.leistung == "Power" and app.speicher.parameter_neu["t_ziel"] == 270
        s = await (await ober.get("/api/status")).json()
        assert s["leistung"] == "Power" and s["leistung_live"] == "Normal" and s["leistung_eigen"] is True
        assert all("kamin_app" in pf for _, pf, _ in ha.rest_anfragen[vorher:])

        assert (await ober.post("/api/leistung", json={"modus": "Turbo"})).status == 400
        assert (await ober.post("/api/leistung", json={"modus": ["Eco"]})).status == 400
        k = {"Authorization": f"Bearer {app.speicher.token}"}
        assert (await integ.post("/integration/befehl", json={"name": "leistung", "wert": "Eco"}, headers=k)).status == 200
        assert app.speicher.parameter_neu["t_ziel"] == 220
    mit_web(p)


def test_ziel_folgt_der_aktiven_stufe_und_ruecknahme_beendet_sie():
    async def p(ober, _, app, __, ___):
        await ober.post("/api/leistung", json={"modus": "Eco"})
        await ober.post("/api/parameter", json={"name": "t_ziel_eco", "wert": 200})
        assert app.speicher.parameter_neu["t_ziel"] == 200
        await ober.post("/api/parameter", json={"name": "t_ziel_power", "wert": 300})
        assert app.speicher.parameter_neu["t_ziel"] == 200
        await ober.post("/api/parameter", json={"name": "t_ziel_eco", "wert": None})
        assert app.speicher.parameter_neu["t_ziel"] == 220
        await ober.post("/api/parameter", json={"name": "t_ziel", "wert": None})
        assert app.speicher.leistung is None and "t_ziel" not in app.speicher.parameter_neu
    mit_web(p)


def test_startseite_nennt_fehlende_angaben_ohne_kosten():
    async def p(ober, _, app, __, ___):
        s = await (await ober.get("/api/status")).json()
        assert all(x["bereich"] != "Kosten" for x in s["einrichtung"])
        app.speicher.rollen_setzen({}, True)
        s = await (await ober.get("/api/status")).json()
        assert any("Abgastemperatur" in x["text"] for x in s["einrichtung"])
    mit_web(p)
