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
    assert oeffentlich == {"hole_zustaende", "laufen", "schliessen", "setze_schattensensor", "verbunden"}


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
        for verboten in ("call_service", "/services", "fire_event", "kern.typen import AKTION"):
            assert verboten not in text, f"{verboten} in {pfad.name}"
