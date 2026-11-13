"""Einmessen in der App: Ablage, Ablauf im Betrieb gegen den Fake-Server und die Schnittstelle."""
import asyncio
import json
import math
import time
from contextlib import asynccontextmanager
from functools import partial

import pytest

from anbindung import ansicht
from anbindung import einmessen as einmess
from anbindung.eingaenge import Eingaenge
from anbindung.einstellungen import EinstellungsFehler, Speicher
from anbindung.ha_client import HaClient
from anbindung.main import App, steuerung_aus_kern
from anbindung.schatten import Verlauf
from anbindung.uebertemperatur import TITEL_WARNUNG
from fake_ha import TOKEN, FakeHa, warte_auf
from kern.einmessen import PUNKTARTEN
from kern.typen import NEU, TICK
from test_app import HANDY, anlage_setzen, betrieb_speicher, gesicherter_zustand
from test_web import bereit_machen, einschalten, mit_web
from testdaten import ENTITAETEN as E

TITEL = "🔥 Kamin – Einmessen"
T0 = 1_800_000_000.0


# ---- Synthetischer Abbrand ------------------------------------------------------

def abbrand(zyklen=2, nachlauf_min=95, takt=30, spitze=250.0):
    """(Sekunden ab Beginn, t, Tür offen): kalt, Anzünden, je Zyklus Anstieg, Plateau, Abfall bis 150 °C,
    dann Nachlegen mit 60 s offener Tür. Der letzte Zyklus fällt `nachlauf_min` lang auf 20 °C zu."""
    werte, s = [], 0

    def probe(t, offen=False):
        nonlocal s
        werte.append((s, t, offen))
        s += takt

    for k in range(0, 300, takt):
        probe(20.0, offen=k >= 180)
    von = 20.0
    for k in range(zyklen):
        for x in range(0, 900, takt):
            l = 1 / (1 + math.exp(-(x / 60 - 5) / 1.2))
            l0 = 1 / (1 + math.exp(5 / 1.2))
            probe(von + (spitze - von) * (l - l0) / (1 - l0), offen=k > 0 and x < 60)
        for _ in range(0, 1500, takt):
            probe(spitze)
        letzter, x = k == zyklen - 1, 0
        while True:
            t = 20 + (spitze - 20) * math.exp(-x / 60 / 40)
            if (not letzter and t <= 150) or (letzter and x >= nachlauf_min * 60):
                break
            probe(t)
            x += takt
        von = t
    return werte


def proben_schreiben(ablage, start=T0, zyklen=3, takt=10):
    """Eine fertige Aufzeichnung direkt in der Ablage, ohne App: zyklen − 1 Nachlegen."""
    a = einmess.Aufzeichnung.starten(ablage, start, zyklen - 1, "Probe", False, True, 50.0)
    for s, t, offen in abbrand(zyklen, takt=takt):
        a.schritt({"zeit": start + s, "t": t, "g": None, "tuer": offen, "klappe": 100, "twt": None,
                   "pumpe": None, "phase": "Abbrand"})
    return a.beenden(start + s, a.grund_fuer(einmess.ENDE_KALT))[0]


# ---- Ablage --------------------------------------------------------------------

def test_ablage_anlegen_liste_und_loeschen(tmp_path):
    ablage = einmess.Ablage(tmp_path / "einmessen")
    assert ablage.liste() == []
    alt = proben_schreiben(ablage, T0)
    neu = proben_schreiben(ablage, T0 + 86400)
    gleich = einmess.Aufzeichnung.starten(ablage, T0 + 86400 + 10, 1, "", False, False, 50.0)
    assert gleich.kennung == neu["id"] + "-2"
    assert [k["id"] for k in ablage.liste()] == [gleich.kennung, neu["id"], alt["id"]]
    kopf = ablage.kopf(alt["id"])
    assert kopf["ende_grund"] == "fertig" and kopf["nachgelegt"] == 2 and kopf["handregelung_ende"] is not None
    assert kopf["punkte"] and all(p["auto"] for p in kopf["punkte"]) and not kopf["punkte_bearbeitet"]
    assert {p["art"] for p in kopf["punkte"]} >= {"anheizen_max", "bester_abbrand", "glut"}
    assert len(ablage.proben(alt["id"])) > 100
    assert ablage.loeschen(alt["id"]) is True
    assert not (tmp_path / "einmessen" / f"{alt['id']}.json").exists()
    assert not (tmp_path / "einmessen" / f"{alt['id']}.jsonl").exists()
    assert ablage.loeschen(alt["id"]) is False
    assert [k["id"] for k in ablage.liste()] == [gleich.kennung, neu["id"]]


@pytest.mark.parametrize("kennung", ["../einstellungen", "2026-11-12_1830/../x", "", None, "2026-11-12"])
def test_ablage_prueft_die_kennung(tmp_path, kennung):
    ablage = einmess.Ablage(tmp_path)
    assert not einmess.kennung_gueltig(kennung)
    with pytest.raises(EinstellungsFehler):
        ablage.kopf(kennung)
    with pytest.raises(EinstellungsFehler):
        ablage.loeschen(kennung)


def test_kaputte_dateien_werden_uebersprungen(tmp_path):
    ordner = tmp_path / "einmessen"
    ablage = einmess.Ablage(ordner)
    gut = proben_schreiben(ablage, T0, zyklen=2)
    (ordner / "2026-01-01_0000.json").write_text("{kaputt", encoding="utf-8")
    (ordner / "2026-01-02_0000.json").write_text(json.dumps({"id": "anders", "start": 1, "n": 1}), encoding="utf-8")
    (ordner / "notizen.json").write_text("{}", encoding="utf-8")
    assert [k["id"] for k in ablage.liste()] == [gut["id"]]
    assert ablage.kopf("2026-01-01_0000") is None
    with open(ordner / f"{gut['id']}.jsonl", "a", encoding="utf-8") as datei:
        datei.write('{"zeit": 1, "t": \n[1, 2]\n{"t": 5}\n')
    zeilen = ablage.proben(gut["id"])
    assert all("zeit" in z for z in zeilen) and zeilen == sorted(zeilen, key=lambda z: z["zeit"])


def test_punkte_setzen_pruefen_und_zuruecksetzen(tmp_path):
    ablage = einmess.Ablage(tmp_path)
    kopf = proben_schreiben(ablage)
    kennung, auto = kopf["id"], kopf["punkte"]
    behalten = [{k: p[k] for k in ("art", "zeit", "zyklus")} for p in auto[1:]]
    geschoben = behalten + [{"art": "zu_heiss", "zeit": kopf["start"] + 3000, "zyklus": 1}]
    neu = ablage.punkte_setzen(kennung, geschoben)
    assert neu["punkte_bearbeitet"] and len(neu["punkte"]) == len(auto)
    assert [p["auto"] for p in neu["punkte"] if p["art"] == "zu_heiss"] == [False]
    assert sum(p["auto"] for p in neu["punkte"]) == len(auto) - 1
    for falsch in ([{"art": "gibt_es_nicht", "zeit": kopf["start"], "zyklus": 1}],
                   [{"art": "glut", "zeit": kopf["start"] - 10, "zyklus": 1}],
                   [{"art": "glut", "zeit": kopf["ende"] + 10, "zyklus": 1}],
                   [{"art": "glut", "zeit": kopf["start"], "zyklus": 0}],
                   [{"art": "glut", "zeit": kopf["start"], "zyklus": True}],
                   [{"art": "glut", "zeit": "x", "zyklus": 1}], "kein", None):
        with pytest.raises(EinstellungsFehler):
            ablage.punkte_setzen(kennung, falsch)
    assert ablage.kopf(kennung)["punkte_bearbeitet"]
    # Knapp neben der Aufzeichnung (bis 5 s) wird an den Rand gesetzt
    rand = ablage.punkte_setzen(kennung, [{"art": "glut", "zeit": kopf["start"] - 4, "zyklus": 1},
                                          {"art": "abkuehlen", "zeit": kopf["ende"] + 4.5, "zyklus": 2}])
    assert [p["zeit"] for p in rand["punkte"]] == [kopf["start"], kopf["ende"]]
    zurueck = ablage.punkte_zuruecksetzen(kennung)
    assert not zurueck["punkte_bearbeitet"] and zurueck["punkte"] == auto and zurueck["ende_grund"] == "fertig"


def test_laufende_aufzeichnung_ist_fuer_punkte_gesperrt_und_wird_nach_neustart_beendet(tmp_path):
    ablage = einmess.Ablage(tmp_path / "einmessen")
    a = einmess.Aufzeichnung.starten(ablage, T0, 2, "", False, True, 50.0)
    for s, t, offen in abbrand(1)[:80]:
        a.schritt({"zeit": T0 + s, "t": t, "tuer": offen})
    with pytest.raises(EinstellungsFehler, match="läuft noch"):
        ablage.punkte_setzen(a.kennung, [])
    s = Speicher(tmp_path / "e.json")
    app = App(partial(HaClient, TOKEN, ws_url="ws://x", rest_url="http://x"), Eingaenge(s.entitaeten(), "kamin_abbrand"),
              Verlauf(tmp_path), steuerung_fabrik=steuerung_aus_kern, speicher=s)
    kopf = ablage.kopf(a.kennung)
    assert app.einmessung is None
    assert kopf["ende_grund"] == "neustart" and kopf["ende"] == T0 + 79 * 30 and kopf["punkte"]
    assert einmess.ende_text(kopf) == "App neu gestartet"


def test_massgeblich_im_speicher(tmp_path):
    s = Speicher(tmp_path / "e.json")
    assert s.einmessen_massgeblich is None
    s.einmessen_massgeblich_setzen("2026-11-12_1830")
    assert Speicher(s.pfad).einmessen_massgeblich == "2026-11-12_1830"
    with pytest.raises(EinstellungsFehler):
        s.einmessen_massgeblich_setzen("../x")
    s.einmessen_massgeblich_setzen(None)
    assert Speicher(s.pfad).einmessen_massgeblich is None
    daten = json.loads(s.pfad.read_text()) | {"einmessen_massgeblich": "../../etc"}
    s.pfad.write_text(json.dumps(daten))
    assert Speicher(s.pfad).einmessen_massgeblich is None


def test_start_angaben_werden_geprueft():
    assert einmess.start_pruefen(2, 1, "  Buche  ") == (2, True, "Buche")
    assert einmess.start_pruefen(5.0, False, None) == (5, False, "")
    for zyklen in (0, 6, 2.5, True, "2", None):
        with pytest.raises(EinstellungsFehler):
            einmess.start_pruefen(zyklen, False, "")
    for notiz in (5, "x" * 201):
        with pytest.raises(EinstellungsFehler):
            einmess.start_pruefen(1, False, notiz)


# ---- Ablauf in der App ------------------------------------------------------------

@asynccontextmanager
async def einmess_app(ha, tmp_path, zeit, speicher=None, zustand="Standby", **weiteres):
    s = speicher or betrieb_speicher(tmp_path)
    gesicherter_zustand(tmp_path, 10, phase=zustand, klappe=0)
    app = App(partial(HaClient, TOKEN, ws_url=ha.ws_url, rest_url=ha.rest_url, backoff_start_s=0.01),
              Eingaenge(s.entitaeten(), "kamin_abbrand"), Verlauf(tmp_path), steuerung_fabrik=steuerung_aus_kern,
              speicher=s, uhr=lambda: zeit[0], takt_paritaet_s=3600.0, takt_neu_s=3600.0, wache_intervall_s=0.01,
              karenz_s=3600.0, **weiteres)
    app.ausgaenge._sicher_pause_s = 0.0
    stopp = asyncio.Event()
    aufgabe = asyncio.create_task(app.laufen(stopp))
    try:
        await warte_auf(lambda: app.client.verbunden and NEU in app.steuerungen)
        yield app
    finally:
        stopp.set()
        await aufgabe


async def schritt(app, ha, zeit, t, tuer=False, dt=30.0):
    zeit[0] += dt
    ha.setzen(E.abgas, f"{t:.1f}", {"unit_of_measurement": "°C"}, zeit=zeit[0])
    ha.setzen(E.tuer, "on" if tuer else "off", zeit=zeit[0])
    app.eingaenge.aufbauen(list(ha.zustaende.values()), zeit[0])
    await app._ausfuehren(TICK, (NEU,))


def einmess_push(ha):
    return [k["message"] for _, k in ha.gesendet() if k["title"] == TITEL]


def kalt_anlage(ha, abgas="20"):
    anlage_setzen(ha, abgas=abgas)
    ha.setzen(E.abgas, abgas, {"unit_of_measurement": "°C"})


def test_start_nur_im_betrieb_und_bei_kaltem_ofen(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            kalt_anlage(ha, "200")
            zeit = [time.time()]
            s = betrieb_speicher(tmp_path, modus="lauschen")
            async with einmess_app(ha, tmp_path, zeit, speicher=s, zustand="Abbrand") as app:
                assert app.einmessen_hindernis().startswith("Nur im Betrieb möglich")
                with pytest.raises(EinstellungsFehler, match="Nur im Betrieb"):
                    await app.einmessen_starten(2, False, "")
                assert app.einmessung is None and app.einmess_ablage.liste() == []
                await schritt(app, ha, zeit, 200)
                assert await app.betrieb_anfordern({}, "9.9") == []
                await schritt(app, ha, zeit, 200)
                assert app.steuerungen[NEU].zustand.phase not in ("Standby", "Abkühlen")
                with pytest.raises(EinstellungsFehler, match=r"noch warm \(200 °C\)\. Einmessen beginnt mit kaltem"):
                    await app.einmessen_starten(2, False, "")
                app.steuerungen[NEU].zustand.phase = "Standby"     # Phase allein reicht nicht, t zählt
                assert app.einmessen_hindernis() == "Der Ofen ist noch warm (200 °C). Einmessen beginnt mit kaltem Ofen."
                ha.setzen(E.abgas, "unavailable")
                app.eingaenge.aufbauen(list(ha.zustaende.values()), zeit[0])
                assert app.einmessen_hindernis() is None          # ohne gültiges t gilt die Phase
                app.steuerungen[NEU].zustand.phase = "Abbrand"
                assert app.einmessen_hindernis().startswith("Kein gültiger Abgaswert")
                await schritt(app, ha, zeit, 40)
                assert app.einmessen_hindernis() is None
                app.hand_setzen(True, 15)
                await app.einmessen_starten(2, False, "")
                assert app.einmessung is not None and not app.hand_aktiv(zeit[0])
                with pytest.raises(EinstellungsFehler, match="schon"):
                    await app.einmessen_starten(1, False, "")
                with pytest.raises(EinstellungsFehler):
                    app.hand_setzen(True, 15)

    asyncio.run(ablauf())


def test_abbrand_mit_zwei_nachlegen_handregelung_automatik_nachlauf_und_vorschlaege(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            kalt_anlage(ha)
            zeit = [time.time()]
            s = betrieb_speicher(tmp_path)
            s.benachrichtigungen_setzen({"einmessen": [HANDY], "uebertemperatur": [HANDY], "betrieb": [HANDY]})
            s.parameter_setzen("t_uebertemperatur", 200)
            s.parameter_setzen("klappe_uebertemperatur", 25)
            async with einmess_app(ha, tmp_path, zeit, speicher=s) as app:
                app.benachrichtiger._uhr = lambda: zeit[0]
                await schritt(app, ha, zeit, 20)
                await app.einmessen_starten(2, True, "Buche")
                kennung = app.einmessung.kennung
                assert ansicht.status(app, zeit[0])["einmessen"]["handregelung"] is True
                handregelung, automatik = [], []
                for _, t, offen in abbrand(3):
                    vorher = app.einmessen_handregelung
                    await schritt(app, ha, zeit, t, offen)
                    if vorher and app.einmessen_handregelung:
                        handregelung.append(app.ausgaenge.gesendet)
                        assert app.letzte_eingaben.handbetrieb      # I-Anteil steht, Phasen laufen
                    elif app.einmessung is not None and app.einmessung.nachlauf:
                        automatik.append(app.ausgaenge.gesendet)
                    if app.einmessung is None:
                        break
                await app.benachrichtiger.abwarten()
                await app.einmessen_abwarten()
                assert handregelung and set(handregelung) == {100}
                assert automatik and min(automatik) < 100
                assert min(automatik) <= 25            # nach der Übergabe drosselt die Automatik wieder
                assert app.einmessung is None
                kopf = app.einmess_ablage.kopf(kennung)
                assert kopf["ende_grund"] == "fertig" and kopf["nachgelegt"] == 2 and kopf["notiz"] == "Buche"
                assert kopf["punkte"] and s.einmessen_massgeblich == kennung
                assert einmess_push(ha) == [
                    "Aufzeichnung gestartet, jetzt anzünden.", "Feuer erkannt.", "Nachlegen 1 von 2 erkannt.",
                    "Nachlegen 2 von 2 erkannt.", "Handregelung beendet, die Automatik übernimmt.",
                    "Aufzeichnung beendet (Fertig)."]
                ueber = [k["message"] for _, k in ha.gesendet() if k["title"] == TITEL_WARNUNG]
                assert ueber and "gedrosselt" not in ueber[0] and "Bitte am Ofen prüfen" in ueber[0]
                assert any(m["text"] == "Einmessen: Aufzeichnung beendet (Fertig)." for m in app.meldungen)
                daten = einmess.auswerten(app.einmess_ablage, kennung, app.parameter_neu, {}, {}, {})
                assert [e["art"] for e in daten["ereignisse"]] == ["feuer", "nachlegen", "nachlegen", "fertig", "kalt"]
                assert daten["vorschlaege"] and len(daten["verlauf"]["zeit"]) <= einmess.MAX_VERLAUF_PUNKTE
                assert set(daten["verlauf"]["tuer"]) <= {0, 1} and 100 in daten["verlauf"]["klappe"]

    asyncio.run(ablauf())


def test_abbrechen_uebergibt_sofort_an_die_automatik_ohne_push(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            kalt_anlage(ha)
            zeit = [time.time()]
            s = betrieb_speicher(tmp_path)
            s.benachrichtigungen_setzen({"einmessen": [HANDY]})
            async with einmess_app(ha, tmp_path, zeit, speicher=s) as app:
                await app.einmessen_starten(1, False, "")
                for _, t, offen in abbrand(1)[:60]:
                    await schritt(app, ha, zeit, t, offen)
                assert app.einmessen_handregelung and app.ausgaenge.gesendet == 100
                kennung = app.einmessung.kennung
                await app.einmessen_abbrechen()
                await app.einmessen_abwarten()
                assert app.einmessung is None and not app.einmessen_handregelung
                assert app.steuerungen[NEU].zustand.klappe_genau == 100.0
                for _ in range(10):
                    await schritt(app, ha, zeit, 250)
                assert app.ausgaenge.gesendet < 100
                assert app.einmess_ablage.kopf(kennung)["ende_grund"] == "abgebrochen"
                with pytest.raises(EinstellungsFehler, match="keine Aufzeichnung"):
                    await app.einmessen_abbrechen()
                await app.benachrichtiger.abwarten()
                assert einmess_push(ha) == []
                assert any("Aufzeichnung beendet (Abgebrochen)" in m["text"] for m in app.meldungen)

    asyncio.run(ablauf())


def test_lauschen_beendet_die_aufzeichnung(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            kalt_anlage(ha)
            zeit = [time.time()]
            async with einmess_app(ha, tmp_path, zeit) as app:
                await app.einmessen_starten(2, False, "")
                kennung = app.einmessung.kennung
                await schritt(app, ha, zeit, 20)
                assert await app.lauschen_anfordern() is True
                await app.einmessen_abwarten()
                assert app.einmessung is None and not app.im_betrieb
                assert app.einmess_ablage.kopf(kennung)["ende_grund"] == "lauschen"

    asyncio.run(ablauf())


def test_ohne_feuer_endet_nach_60_min_und_nach_8_h_hoechstdauer(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            kalt_anlage(ha)
            zeit = [time.time()]
            async with einmess_app(ha, tmp_path, zeit) as app:
                await app.einmessen_starten(1, False, "")
                kennung = app.einmessung.kennung
                for _ in range(130):
                    await schritt(app, ha, zeit, 20)
                assert app.einmessung is None
                await app.einmessen_abwarten()
                assert app.einmess_ablage.kopf(kennung)["ende_grund"] == "kein_feuer"

                await schritt(app, ha, zeit, 20)
                await app.einmessen_starten(5, False, "")
                kennung = app.einmessung.kennung
                for _, t, offen in abbrand(1)[:50]:
                    await schritt(app, ha, zeit, t, offen)
                assert app.einmessung.zaehler.zyklus == 1
                while app.einmessung is not None:
                    await schritt(app, ha, zeit, 250, dt=1800.0)
                await app.einmessen_abwarten()
                kopf = app.einmess_ablage.kopf(kennung)
                assert kopf["ende_grund"] == "hoechstdauer" and kopf["ende"] - kopf["start"] >= einmess.HOECHSTDAUER_S
                assert kopf["ende"] - kopf["start"] < einmess.HOECHSTDAUER_S + 1800

    asyncio.run(ablauf())


def test_plattenfehler_stoert_die_regelung_nicht(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            kalt_anlage(ha)
            zeit = [time.time()]
            async with einmess_app(ha, tmp_path, zeit) as app:
                await app.einmessen_starten(1, False, "")
                app.einmess_ablage.ordner = tmp_path / "e.json" / "gibt_es_nicht"   # unter einer Datei: OSError
                await schritt(app, ha, zeit, 20)
                assert app.ausgaenge.gesendet == 100 and app.im_betrieb
                await app.einmessen_abbrechen()
                await app.einmessen_abwarten()
                assert app.einmessung is None and app.im_betrieb
                assert app.speicher.einmessen_massgeblich is None     # nicht geschrieben, also nicht maßgeblich

    asyncio.run(ablauf())


# ---- Schnittstelle -------------------------------------------------------------

async def fertige_aufzeichnung(app):
    return await asyncio.to_thread(proben_schreiben, app.einmess_ablage, time.time() - 6 * 3600)


def test_api_ablauf_start_status_abbrechen_und_fehler():
    async def p(ober, _, app, ha, tmp):
        d = await (await ober.get("/api/einmessen")).json()
        assert d == {"laeuft": None, "liste": [], "startbar": False,
                     "startbar_grund": "Nur im Betrieb möglich. Bitte erst auf Betrieb schalten.",
                     "push_geraet": False, "max_zyklen": 5}
        r = await ober.post("/api/einmessen/start", json={"zyklen": 2, "push": False, "notiz": ""})
        assert r.status == 400 and "Nur im Betrieb" in (await r.json())["fehler"]
        await bereit_machen(ober, app, ha)
        assert (await einschalten(ober)).status == 200
        await ha.aendern("sensor.a", "25", {"unit_of_measurement": "°C"})
        await warte_auf(lambda: app.eingaenge.zahl("sensor.a") == 25)
        app.speicher.benachrichtigungen_setzen({"einmessen": ["notify.x"]})
        d = await (await ober.get("/api/einmessen")).json()
        assert d["startbar"] is True and d["startbar_grund"] is None and d["push_geraet"] is True
        for falsch in ({"zyklen": 6}, {"zyklen": 0}, {"zyklen": "2"}, {"zyklen": 2, "notiz": 3}):
            r = await ober.post("/api/einmessen/start", json=falsch)
            assert r.status == 400 and (await r.json())["fehler"]
        assert (await ober.post("/api/einmessen/start", data="kaputt")).status == 400
        r = await ober.post("/api/einmessen/start", json={"zyklen": 2, "push": False, "notiz": "Eiche"})
        d = await r.json()
        assert r.status == 200 and d["startbar"] is False and d["startbar_grund"] == "Es läuft schon eine Aufzeichnung."
        laeuft = d["laeuft"]
        assert set(laeuft) == {"id", "start", "n", "zyklus", "nachgelegt", "handregelung", "nachlauf", "seit_s", "push",
                               "notiz"}
        assert laeuft["n"] == 2 and laeuft["handregelung"] is True and laeuft["nachlauf"] is False
        assert laeuft["notiz"] == "Eiche" and laeuft["push"] is False and laeuft["zyklus"] == 0
        assert [k["ende_text"] for k in d["liste"]] == ["Läuft"]
        kennung = laeuft["id"]
        status = await (await ober.get("/api/status")).json()
        assert set(status["einmessen"]) == {"id", "zyklus", "n", "nachgelegt", "handregelung", "nachlauf", "seit_s"}
        assert status["einmessen"]["id"] == kennung and status["satz"].startswith("Einmessen")
        await warte_auf(lambda: app.ausgaenge.gesendet == 100)
        await warte_auf(lambda: len(app.einmess_ablage.proben(kennung)) >= 3)
        assert (await ober.post("/api/einmessen/start", json={"zyklen": 1})).status == 400
        r = await ober.post(f"/api/einmessen/{kennung}/loeschen", json={})
        assert r.status == 400 and "abbrechen" in (await r.json())["fehler"]
        assert (await ober.post(f"/api/einmessen/{kennung}/punkte", json={"punkte": []})).status == 400
        assert (await ober.post(f"/api/einmessen/{kennung}/uebernehmen", json={"feld": "t_ziel"})).status == 400
        detail = await (await ober.get(f"/api/einmessen/{kennung}")).json()
        assert detail["kopf"]["ende"] is None and detail["kopf"]["ende_text"] == "Läuft"
        assert len(detail["verlauf"]["zeit"]) >= 3 and set(detail["verlauf"]["klappe"]) == {100}

        d = await (await ober.post("/api/einmessen/abbrechen", json={})).json()
        assert d["laeuft"] is None and d["startbar"] is True
        await app.einmessen_abwarten()
        d = await (await ober.get("/api/einmessen")).json()
        (kopf,) = d["liste"]
        assert kopf["ende_grund"] == "abgebrochen" and kopf["ende_text"] == "Abgebrochen" and kopf["massgeblich"] is True
        assert set(kopf) == {"id", "start", "ende", "n", "nachgelegt", "notiz", "ende_grund", "ende_text", "massgeblich",
                             "punkte_bearbeitet"}
        assert (await (await ober.get("/api/status")).json())["einmessen"] is None
        r = await ober.post("/api/einmessen/abbrechen", json={})
        assert r.status == 400 and (await r.json())["fehler"] == "Es läuft keine Aufzeichnung."
        for pfad in ("/api/einmessen/abc", "/api/einmessen/2020-01-01_0000", "/api/einmessen/..%2Fe",
                     "/api/einmessen/start"):
            r = await ober.get(pfad)
            assert r.status in (400, 404) and (r.status == 404 or (await r.json())["fehler"])
        assert (await ober.get("/api/einmessen/2020-01-01_0000")).status == 400
    mit_web(p)


def test_api_auswertung_punkte_massgeblich_uebernehmen_loeschen():
    async def p(ober, _, app, ha, tmp):
        erste = await fertige_aufzeichnung(app)
        kopf = await fertige_aufzeichnung(app)
        kennung = kopf["id"]
        d = await (await ober.get(f"/api/einmessen/{kennung}")).json()
        assert set(d) == {"kopf", "verlauf", "ereignisse", "punkte", "punktarten", "vorschlaege"}
        assert set(d["verlauf"]) == {"zeit", "t", "klappe", "twt", "pumpe", "tuer", "phase"}
        assert len(d["verlauf"]["zeit"]) <= 1500
        assert [a["art"] for a in d["punktarten"]] == list(PUNKTARTEN)
        assert d["punktarten"][0] == {"art": "anheizen_max", "name": "Anheizen max",
                                      "hilfe": einmess.PUNKT_HILFE["anheizen_max"], "felder": ["t_kaltstart_ende"],
                                      "feld_titel": ["Kaltstart offen bis"]}
        assert d["punktarten"][2]["felder"][:2] == ["t_ziel", "t_ziel_eco"]
        assert [e["art"] for e in d["ereignisse"]][:3] == ["feuer", "nachlegen", "nachlegen"]
        assert d["ereignisse"][1]["text"] == "Nachlegen 1 von 2 erkannt"
        assert d["punkte"] and all(set(p) == {"art", "zeit", "zyklus", "auto"} for p in d["punkte"])
        felder = {v["feld"]: v for v in d["vorschlaege"]}
        assert {"t_ziel", "t_ziel_eco", "t_ziel_normal", "t_ziel_power", "t_glut"} <= set(felder)
        ziel = felder["t_ziel"]
        assert set(ziel) == {"feld", "titel", "einheit", "wert", "alt", "begruendung", "punkte", "gleich"}
        assert ziel["titel"] == "Zieltemperatur" and ziel["einheit"] == "°C" and ziel["wert"] % 5 == 0
        assert ziel["gleich"] == (ziel["wert"] == ziel["alt"])

        punkte = [{k: p[k] for k in ("art", "zeit", "zyklus")} for p in d["punkte"] if p["art"] != "glut"]
        r = await ober.post(f"/api/einmessen/{kennung}/punkte", json={"punkte": punkte})
        d2 = await r.json()
        assert r.status == 200 and d2["kopf"]["punkte_bearbeitet"] is True
        assert not any(p["art"] == "glut" for p in d2["punkte"])
        assert "t_glut" not in {v["feld"] for v in d2["vorschlaege"]}
        for falsch in ({"punkte": [{"art": "x", "zeit": kopf["start"], "zyklus": 1}]},
                       {"punkte": [{"art": "glut", "zeit": kopf["start"] - 10, "zyklus": 1}]},
                       {"punkte": [{"art": "glut", "zeit": kopf["start"], "zyklus": 0}]}, {"punkte": "x"}, []):
            r = await ober.post(f"/api/einmessen/{kennung}/punkte", json=falsch)
            assert r.status == 400 and (await r.json())["fehler"]
        d3 = await (await ober.post(f"/api/einmessen/{kennung}/punkte_zuruecksetzen", json={})).json()
        assert d3["kopf"]["punkte_bearbeitet"] is False and d3["punkte"] == d["punkte"]

        uebersicht = await (await ober.get("/api/einmessen")).json()
        assert [k["id"] for k in uebersicht["liste"]] == [kennung, erste["id"]]
        assert not any(k["massgeblich"] for k in uebersicht["liste"])
        uebersicht = await (await ober.post(f"/api/einmessen/{kennung}/massgeblich", json={})).json()
        assert [k["massgeblich"] for k in uebersicht["liste"]] == [True, False]
        uebersicht = await (await ober.post(f"/api/einmessen/{erste['id']}/massgeblich", json={})).json()
        assert [k["massgeblich"] for k in uebersicht["liste"]] == [False, True]
        assert (await ober.post("/api/einmessen/2020-01-01_0000/massgeblich", json={})).status == 400

        r = await ober.post(f"/api/einmessen/{kennung}/uebernehmen", json={"feld": "t_ziel"})
        e = await r.json()
        assert r.status == 200 and e == {"feld": "t_ziel", "wert": ziel["wert"], "titel": "Zieltemperatur"}
        assert app.speicher.parameter_neu["t_ziel"] == ziel["wert"]
        eco = felder["t_ziel_eco"]["wert"]
        e = await (await ober.post(f"/api/einmessen/{kennung}/uebernehmen", json={"feld": "t_ziel_eco"})).json()
        assert e["wert"] == eco and app.speicher.daten["leistung"]["t_ziel_eco"] == eco
        aenderungen = [json.loads(z) for z in (tmp / "aenderungen.jsonl").read_text().splitlines()]
        assert [a["was"] for a in aenderungen][-2:] == ["t_ziel", "t_ziel_eco"]
        d = await (await ober.get(f"/api/einmessen/{kennung}")).json()
        assert {v["feld"]: v["gleich"] for v in d["vorschlaege"]}["t_ziel"] is True
        for falsch in ({"feld": "gibt_es_nicht"}, {"feld": "klappe_glut"}, {}):
            r = await ober.post(f"/api/einmessen/{kennung}/uebernehmen", json=falsch)
            assert r.status == 400 and (await r.json())["fehler"]

        uebersicht = await (await ober.post(f"/api/einmessen/{erste['id']}/loeschen", json={})).json()
        assert [k["id"] for k in uebersicht["liste"]] == [kennung]
        assert app.speicher.einmessen_massgeblich == kennung       # die neueste beendete rückt nach
        assert uebersicht["liste"][0]["massgeblich"] is True
        assert (await ober.post(f"/api/einmessen/{erste['id']}/loeschen", json={})).status == 400
        assert (await ober.get(f"/api/einmessen/{erste['id']}")).status == 400
    mit_web(p)


# ---- Nachbesserungen ----------------------------------------------------------------

def test_aufraeumen_behaelt_50_und_schont_die_maßgebliche(tmp_path):
    ablage = einmess.Ablage(tmp_path)
    kennungen = []
    for k in range(53):
        a = einmess.Aufzeichnung.starten(ablage, T0 + k * 3600, 1, "", False, False, 50.0)
        a.beenden(T0 + k * 3600 + 60, einmess.ENDE_ABGEBROCHEN)
        kennungen.append(a.kennung)
    weg = ablage.aufraeumen({kennungen[0]})
    assert weg == kennungen[1:4]
    rest = [k["id"] for k in ablage.liste()]
    assert len(rest) == 50 and kennungen[0] in rest and kennungen[-1] in rest


def test_verlauf_behaelt_letzte_probe_und_tuerflanken():
    zeilen = [{"zeit": T0 + i, "t": 100.0, "tuer": i == 1234} for i in range(4001)]
    v = einmess.verlauf_zeigen(zeilen)
    assert len(v["zeit"]) <= einmess.MAX_VERLAUF_PUNKTE + 2
    assert v["zeit"][-1] == T0 + 4000 and T0 + 1234 in v["zeit"]
    assert v["tuer"][v["zeit"].index(T0 + 1234)] == 1 and v["tuer"][v["zeit"].index(T0 + 1235)] == 0


def test_aufzeichnung_ohne_ende_die_nicht_laeuft_gilt_als_fehler():
    kopf = {"id": "2026-11-12_1830", "start": T0, "ende": None, "n": 1, "nachgelegt": 0, "notiz": "",
            "ende_grund": None, "punkte_bearbeitet": False}
    assert einmess.kopf_zeigen(kopf, None)["ende_grund"] == "fehler"
    assert einmess.kopf_zeigen(kopf, None)["ende_text"] == "Fehler beim Aufzeichnen"
    laeuft = einmess.kopf_zeigen(kopf, None, frozenset({"2026-11-12_1830"}))
    assert laeuft["ende_grund"] is None and laeuft["ende_text"] == "Läuft"


def test_fehler_im_einmessen_uebergibt_nach_drei_fehlern_an_die_automatik(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            kalt_anlage(ha)
            zeit = [time.time()]
            s = betrieb_speicher(tmp_path)
            s.benachrichtigungen_setzen({"einmessen": [HANDY]})
            async with einmess_app(ha, tmp_path, zeit, speicher=s) as app:
                await app.einmessen_starten(1, True, "")
                kennung = app.einmessung.kennung

                def kaputt(_zeile):
                    raise RuntimeError("kaputt")
                app.einmessung.schritt = kaputt
                for _ in range(2):
                    await schritt(app, ha, zeit, 20)
                    assert app.einmessen_handregelung and app.ausgaenge.gesendet == 100
                await schritt(app, ha, zeit, 20)
                assert app.einmessung is None and not app.einmessen_handregelung
                await schritt(app, ha, zeit, 20)
                assert app.ausgaenge.gesendet == 0               # Standby: die Automatik stellt wieder
                await app.einmessen_abwarten()
                await app.benachrichtiger.abwarten()
                assert app.einmess_ablage.kopf(kennung)["ende_grund"] == "fehler"
                assert einmess_push(ha)[-1] == "Aufzeichnung beendet (Fehler beim Aufzeichnen). Die Automatik übernimmt."

    asyncio.run(ablauf())


def test_klappe_soll_100_wird_auch_veroeffentlicht_und_gesichert(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            kalt_anlage(ha)
            zeit = [time.time()]
            s = betrieb_speicher(tmp_path)
            s.parameter_setzen("klappe_manuell", 30)
            async with einmess_app(ha, tmp_path, zeit, speicher=s) as app:
                await app.einmessen_starten(1, False, "")
                for _, t, offen in abbrand(1)[:40]:
                    await schritt(app, ha, zeit, t, offen)
                assert ha.geschrieben("sensor.kamin_app_n_klappe_soll")[-1] == "100"
                assert app.letzte[NEU][0].klappe == 100
                gesichert = json.loads((tmp_path / "betrieb_zustand.json").read_text())["zustand"]
                assert gesichert["klappe_soll"] == 100

    asyncio.run(ablauf())


def test_tuerkontakt_faellt_aus_dann_zaehlt_die_gradientenumkehr(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            kalt_anlage(ha)
            zeit = [time.time()]
            async with einmess_app(ha, tmp_path, zeit) as app:
                await app.einmessen_starten(2, False, "")
                kennung = app.einmessung.kennung
                werte = abbrand(3)
                for s_, t, offen in werte:
                    if app.einmessung is None:
                        break
                    if s_ > 1500:                        # nach dem Feuer meldet der Kontakt nichts mehr
                        zeit[0] += 30
                        ha.setzen(E.abgas, f"{t:.1f}", {"unit_of_measurement": "°C"}, zeit=zeit[0])
                        ha.setzen(E.tuer, "unavailable", zeit=zeit[0])
                        app.eingaenge.aufbauen(list(ha.zustaende.values()), zeit[0])
                        await app._ausfuehren(TICK, (NEU,))
                    else:
                        await schritt(app, ha, zeit, t, offen)
                    if app.einmessung is not None and app.einmessung.zaehler.tuer_ausgefallen:
                        assert not app.einmessung.zaehler.tuerkontakt
                await app.einmessen_abwarten()
                kopf = app.einmess_ablage.kopf(kennung)
                assert kopf["ende_grund"] == "fertig" and kopf["nachgelegt"] == 2
                assert any("Türkontakt meldet nichts" in m["text"] for m in app.meldungen)

    asyncio.run(ablauf())


def test_kurzes_oeffnen_zwischen_zwei_ticks_zaehlt(tmp_path):
    from fake_ha import zustand
    async def ablauf():
        async with FakeHa() as ha:
            kalt_anlage(ha)
            zeit = [time.time()]
            async with einmess_app(ha, tmp_path, zeit) as app:
                await app.einmessen_starten(1, False, "")
                for _, t, _offen in abbrand(1)[:90]:
                    await schritt(app, ha, zeit, t, False)
                assert app.einmessung.zaehler.zyklus == 1 and app.einmessung.zaehler.nachgelegt == 0
                # Tür auf und gleich wieder zu, beides vor dem nächsten TICK
                app._einmessen_tuer({"entity_id": E.tuer, "new_state": zustand(E.tuer, "on")})
                app._einmessen_tuer({"entity_id": E.tuer, "new_state": zustand(E.tuer, "off")})
                await schritt(app, ha, zeit, 240, False)
                assert app.einmessung.zaehler.nachgelegt == 1
                assert app.einmess_ablage.proben(app.einmessung.kennung)[-1]["tuer"] is True

    asyncio.run(ablauf())


def test_nach_neustart_stellt_die_automatik(tmp_path):
    async def ablauf():
        async with FakeHa() as ha:
            kalt_anlage(ha)
            zeit = [time.time()]
            ablage = einmess.Ablage(tmp_path / "einmessen")
            alt = einmess.Aufzeichnung.starten(ablage, zeit[0] - 600, 2, "", False, True, 50.0)
            alt.schritt({"zeit": zeit[0] - 590, "t": 20.0, "tuer": False})
            async with einmess_app(ha, tmp_path, zeit) as app:
                assert app.einmessung is None and not app.einmessen_handregelung
                await schritt(app, ha, zeit, 20)
                assert app.ausgaenge.gesendet == 0
                assert ablage.kopf(alt.kennung)["ende_grund"] == "neustart"

    asyncio.run(ablauf())


@pytest.mark.parametrize("stoerung", ["kern", "verbindung"])
def test_rueckfall_waehrend_der_handregelung_beendet_sie(tmp_path, stoerung):
    async def ablauf():
        async with FakeHa() as ha:
            kalt_anlage(ha)
            zeit = [time.time()]
            async with einmess_app(ha, tmp_path, zeit) as app:
                await app.einmessen_starten(2, False, "")
                kennung = app.einmessung.kennung
                await schritt(app, ha, zeit, 20)
                if stoerung == "kern":
                    def kaputt(_):
                        raise RuntimeError("Kern kaputt")
                    app.steuerungen[NEU].schritt = kaputt
                else:
                    app._getrennt_seit = zeit[0] - 4000
                await schritt(app, ha, zeit, 20)
                assert not app.im_betrieb and app.rueckfall is not None
                assert app.einmessung is None
                await app.einmessen_abwarten()
                assert app.einmess_ablage.kopf(kennung)["ende_grund"] == "lauschen"

    asyncio.run(ablauf())
