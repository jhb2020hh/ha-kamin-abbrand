import asyncio
from pathlib import Path

import pytest

import anbindung
from anbindung.ha_client import HaClient, SchreibsperreFehler, naechster_backoff
from fake_ha import TOKEN, FakeHa, warte_auf


class Mitschrift:
    def __init__(self):
        self.abrufe = []
        self.ereignisse = []
        self.trennungen = 0

    async def zustaende(self, liste):
        self.abrufe.append(liste)

    async def ereignis(self, daten):
        self.ereignisse.append(daten)

    async def trennung(self):
        self.trennungen += 1


def baue_client(ha, mit, token=TOKEN, **weiteres):
    return HaClient(
        token, bei_zustaenden=mit.zustaende, bei_ereignis=mit.ereignis, bei_trennung=mit.trennung,
        ws_url=ha.ws_url, rest_url=ha.rest_url, backoff_start_s=0.01, **weiteres,
    )


async def mit_laufendem_client(ha, client, ablauf):
    aufgabe = asyncio.create_task(client.laufen())
    try:
        await ablauf()
    finally:
        aufgabe.cancel()
        await asyncio.gather(aufgabe, return_exceptions=True)
        await client.schliessen()


def test_anmeldung_abo_und_erster_abruf():
    async def ablauf():
        async with FakeHa() as ha:
            ha.setzen("sensor.a", "1")
            mit = Mitschrift()
            client = baue_client(ha, mit)

            async def pruefen():
                await warte_auf(lambda: client.verbunden)
                assert ha.ws_nachrichten[0] == {"type": "auth", "access_token": TOKEN}
                typen = [n["type"] for n in ha.ws_nachrichten[1:]]
                assert typen == ["subscribe_events", "get_states"]
                assert ha.ws_nachrichten[1]["event_type"] == "state_changed"
                assert mit.abrufe[0][0]["entity_id"] == "sensor.a"
                await ha.aendern("sensor.a", "2")
                await warte_auf(lambda: mit.ereignisse)
                assert mit.ereignisse[0]["new_state"]["state"] == "2"

            await mit_laufendem_client(ha, client, pruefen)

    asyncio.run(ablauf())


def test_falsches_token_verbindet_nicht_und_versucht_es_erneut():
    async def ablauf():
        async with FakeHa() as ha:
            mit = Mitschrift()
            client = baue_client(ha, mit, token="falsch")

            async def pruefen():
                await warte_auf(lambda: ha.verbindungen >= 3)
                assert not client.verbunden
                assert ha.anmeldungen_ok == 0
                assert mit.abrufe == []

            await mit_laufendem_client(ha, client, pruefen)

    asyncio.run(ablauf())


def test_backoff_verdoppelt_bis_60_s():
    werte = [1.0]
    for _ in range(8):
        werte.append(naechster_backoff(werte[-1]))
    assert werte == [1, 2, 4, 8, 16, 32, 60, 60, 60]


def test_reconnect_liest_zustaende_neu_und_abonniert_wieder():
    async def ablauf():
        async with FakeHa() as ha:
            ha.setzen("sensor.a", "1")
            mit = Mitschrift()
            client = baue_client(ha, mit)

            async def pruefen():
                await warte_auf(lambda: client.verbunden)
                await ha.abbrechen()
                await warte_auf(lambda: mit.trennungen == 1)
                # Änderung während der Lücke: Es gibt kein Ereignis dafür.
                ha.setzen("sensor.a", "2")
                await warte_auf(lambda: len(mit.abrufe) == 2 and client.verbunden)
                assert mit.abrufe[1][0]["state"] == "2"
                assert ha.anmeldungen_ok == 2
                await ha.aendern("sensor.a", "3")
                await warte_auf(lambda: mit.ereignisse)

            await mit_laufendem_client(ha, client, pruefen)

    asyncio.run(ablauf())


def test_server_nicht_erreichbar_beendet_nichts():
    async def ablauf():
        async with FakeHa() as ha:
            url = ha.ws_url
            rest = ha.rest_url
        mit = Mitschrift()
        client = HaClient(TOKEN, bei_zustaenden=mit.zustaende, bei_ereignis=mit.ereignis,
                          ws_url=url, rest_url=rest, backoff_start_s=0.01)
        aufgabe = asyncio.create_task(client.laufen())
        await asyncio.sleep(0.1)
        assert not aufgabe.done()
        aufgabe.cancel()
        await asyncio.gather(aufgabe, return_exceptions=True)
        await client.schliessen()

    asyncio.run(ablauf())


def test_schattensensor_wird_per_post_gesetzt():
    async def ablauf():
        async with FakeHa() as ha:
            client = baue_client(ha, Mitschrift())
            await client.setze_schattensensor("sensor.kamin_app_status", "ok", {"a": 1})
            await client.schliessen()
            assert ha.rest_anfragen == [
                ("POST", "/core/api/states/sensor.kamin_app_status", {"state": "ok", "attributes": {"a": 1}})
            ]
            assert ha.rest_token == [f"Bearer {TOKEN}"]

    asyncio.run(ablauf())


@pytest.mark.parametrize("entity_id", [
    "input_number.kamin_abbrand_klappe_soll",
    "input_select.kamin_abbrand_phase",
    "input_boolean.kamin_abbrand_wartung",
    "sensor.kamin_abbrand_gradient",
    "sensor.kamin_app_",
    "sensor.kamin_app",
    "switch.kamin_app_x",
    "sensor.kamin_app_x/../../services/cover/open_cover",
    "sensor.kamin_app_x?y=1",
    "sensor.kamin_app_X",
    "sensor.kamin_app_x\n",
    "",
])
def test_schreibsperre_wirft_und_nichts_erreicht_den_server(entity_id):
    async def ablauf():
        async with FakeHa() as ha:
            client = baue_client(ha, Mitschrift())
            with pytest.raises(SchreibsperreFehler):
                await client.setze_schattensensor(entity_id, "1")
            await client.schliessen()
            assert ha.rest_anfragen == []

    asyncio.run(ablauf())


def test_client_hat_keinen_weg_fuer_dienste():
    oeffentlich = {n for n in dir(HaClient) if not n.startswith("_")}
    # Dazu kommen: Dienste lesen, notify.<name> senden und das Schalten im Betrieb, alle streng begrenzt (siehe unten).
    assert oeffentlich == {"hole_zustaende", "hole_dienste", "laufen", "schliessen", "sende_notify",
                           "setze_schattensensor", "verbunden",
                           "schalte", "betrieb_aktiv", "betrieb_freigeben", "betrieb_sperren"}


@pytest.mark.parametrize("befehl", ["call_service", "fire_event", "execute_script", "config/core/update"])
def test_ws_positivliste_sperrt_andere_befehle(befehl):
    async def ablauf():
        async with FakeHa() as ha:
            mit = Mitschrift()
            client = baue_client(ha, mit)

            async def pruefen():
                await warte_auf(lambda: client.verbunden)
                vorher = len(ha.ws_nachrichten)
                with pytest.raises(SchreibsperreFehler):
                    await client._anfrage(befehl)
                assert len(ha.ws_nachrichten) == vorher

            await mit_laufendem_client(ha, client, pruefen)

    asyncio.run(ablauf())


def test_quelltext_enthaelt_keinen_dienstaufruf():
    for pfad in Path(anbindung.__file__).parent.glob("*.py"):
        text = pfad.read_text(encoding="utf-8")
        for verboten in ("call_service", "fire_event"):
            assert verboten not in text, f"{verboten} in {pfad.name}"
        # Nur die Ausgänge dürfen die Aktionen des Kerns kennen, sie führen sie ausschließlich im Betrieb aus.
        if pfad.name != "ausgaenge.py":
            assert "kern.typen import AKTION" not in text, f"Aktionen in {pfad.name}"
        if pfad.name != "ha_client.py":
            assert "/services" not in text, f"/services in {pfad.name}"
    # Im Client selbst gibt es genau drei Pfade: die Liste lesen, notify senden und schalte im Betrieb.
    text = (Path(anbindung.__file__).parent / "ha_client.py").read_text(encoding="utf-8")
    assert text.count('f"{self._rest_url}/services') == 3
    assert 'f"{self._rest_url}/services"' in text and "/services/notify/" in text
    assert '/services/{domain}/{dienst}"' in text
    assert text.count("/services/{domain}") == 1


def test_dienste_lesen_ist_nur_ein_get():
    async def ablauf():
        async with FakeHa() as ha:
            ha.dienste = [{"domain": "notify", "services": {"mobile_app_a": {}}}]
            client = baue_client(ha, Mitschrift())
            assert await client.hole_dienste() == ha.dienste
            await client.schliessen()
            assert ha.rest_anfragen == [("GET", "/core/api/services", {})]
            assert ha.rest_token == [f"Bearer {TOKEN}"]

    asyncio.run(ablauf())


def test_notify_wird_gesendet():
    async def ablauf():
        async with FakeHa() as ha:
            client = baue_client(ha, Mitschrift())
            await client.sende_notify("notify.mobile_app_a", "Titel", "Text", {"push": {"interruption-level": "time-sensitive"}})
            await client.sende_notify("notify.b", "T", "X")
            await client.schliessen()
            assert ha.gesendet() == [
                ("notify.mobile_app_a", {"title": "Titel", "message": "Text", "data": {"push": {"interruption-level": "time-sensitive"}}}),
                ("notify.b", {"title": "T", "message": "X"}),
            ]
            assert ha.rest_token == [f"Bearer {TOKEN}"] * 2

    asyncio.run(ablauf())


@pytest.mark.parametrize("dienst", [
    "cover.open_cover", "light.turn_on", "script.turn_on", "homeassistant.restart", "notify", "notify.", "notify.A",
    "notify.a/../../services/cover/open_cover", "notify.a?x=1", "notify.a.b", "notify.a\n", "Notify.a", " notify.a",
    "persistent_notification.create", "", None, 5,
])
def test_ungueltige_dienste_werfen_und_nichts_erreicht_den_server(dienst):
    async def ablauf():
        async with FakeHa() as ha:
            client = baue_client(ha, Mitschrift())
            with pytest.raises(SchreibsperreFehler):
                await client.sende_notify(dienst, "T", "X")
            await client.schliessen()
            assert ha.rest_anfragen == []

    asyncio.run(ablauf())


def test_notify_fehlerstatus_wird_zur_ausnahme():
    import aiohttp

    async def ablauf():
        async with FakeHa() as ha:
            ha.rest_status = 500
            client = baue_client(ha, Mitschrift())
            with pytest.raises(aiohttp.ClientResponseError):
                await client.sende_notify("notify.a", "T", "X")
            await client.schliessen()

    asyncio.run(ablauf())


# ---- Schalten: Der Weg existiert nur im Betrieb und nur für zugewiesene Ausgänge ----

VERSUCHE = [
    ("cover", "set_cover_position", "cover.k", {"position": 50}),
    ("light", "turn_on", "light.k", {"brightness_pct": 50}),
    ("light", "turn_off", "light.k", None),
    ("number", "set_value", "number.k", {"value": 1}),
    ("input_number", "set_value", "input_number.k", {"value": 1}),
    ("input_boolean", "turn_off", "input_boolean.w", None),
    ("switch", "turn_off", "switch.w", None),
    ("button", "press", "button.n", None),
    ("script", "turn_on", "script.n", None),
]


@pytest.mark.parametrize("domain, dienst, eid, daten", VERSUCHE)
def test_im_lauschen_wirft_jeder_schaltversuch(domain, dienst, eid, daten):
    async def ablauf():
        async with FakeHa() as ha:
            client = baue_client(ha, Mitschrift())
            assert client.betrieb_aktiv is False
            with pytest.raises(SchreibsperreFehler):
                await client.schalte(domain, dienst, eid, daten)
            await client.schliessen()
            assert ha.rest_anfragen == []

    asyncio.run(ablauf())


@pytest.mark.parametrize("domain, dienst, eid, daten", VERSUCHE)
def test_im_betrieb_erreichen_zugewiesene_ausgaenge_den_server(domain, dienst, eid, daten):
    async def ablauf():
        async with FakeHa() as ha:
            client = baue_client(ha, Mitschrift())
            client.betrieb_freigeben([eid])
            await client.schalte(domain, dienst, eid, daten)
            await client.schliessen()
            assert ha.rest_anfragen == [("POST", f"/core/api/services/{domain}/{dienst}", {**(daten or {}), "entity_id": eid})]
            assert ha.rest_token == [f"Bearer {TOKEN}"]

    asyncio.run(ablauf())


@pytest.mark.parametrize("domain, dienst, eid, daten", [
    ("cover", "set_cover_position", "cover.fremd", {"position": 1}),            # nicht zugewiesen
    ("switch", "turn_on", "switch.pumpe", None),                                # Dienst nicht in der Liste
    ("switch", "turn_off", "switch.pumpe", None),                               # Pumpe ist nicht zugewiesen
    ("light", "turn_on", "cover.k", {"brightness_pct": 1}),                     # Domain passt nicht zur Entität
    ("cover", "open_cover", "cover.k", None),
    ("homeassistant", "restart", "cover.k", None),
    ("script", "turn_on", "cover.k", None),
    ("automation", "turn_off", "automation.x", None),
    ("cover", "set_cover_position", "cover.k", {"position": 1, "entity_id": "cover.fremd"}),
    ("cover", "set_cover_position", "cover.k", {"device_id": "x"}),
    ("cover", "set_cover_position", "cover.k/../../x", {"position": 1}),
    ("cover", "set_cover_position", "", {"position": 1}),
    ("cover", "set_cover_position", None, {"position": 1}),
    ("cover/../homeassistant", "restart", "cover.k", None),
    (None, None, "cover.k", None),
])
def test_im_betrieb_bleibt_alles_andere_gesperrt(domain, dienst, eid, daten):
    async def ablauf():
        async with FakeHa() as ha:
            client = baue_client(ha, Mitschrift())
            client.betrieb_freigeben(["cover.k", "cover.k/../../x"])
            with pytest.raises(SchreibsperreFehler):
                await client.schalte(domain, dienst, eid, daten)
            await client.schliessen()
            assert ha.rest_anfragen == []

    asyncio.run(ablauf())


def test_sperren_schliesst_den_weg_wieder():
    async def ablauf():
        async with FakeHa() as ha:
            client = baue_client(ha, Mitschrift())
            client.betrieb_freigeben(["cover.k"])
            client.betrieb_sperren()
            with pytest.raises(SchreibsperreFehler):
                await client.schalte("cover", "set_cover_position", "cover.k", {"position": 100})
            await client.schliessen()
            assert ha.rest_anfragen == [] and client.betrieb_aktiv is False

    asyncio.run(ablauf())


def test_schalten_fehlerstatus_wird_zur_ausnahme():
    import aiohttp

    async def ablauf():
        async with FakeHa() as ha:
            ha.dienst_status = 500
            client = baue_client(ha, Mitschrift())
            client.betrieb_freigeben(["cover.k"])
            with pytest.raises(aiohttp.ClientResponseError):
                await client.schalte("cover", "set_cover_position", "cover.k", {"position": 100})
            await client.schliessen()

    asyncio.run(ablauf())
