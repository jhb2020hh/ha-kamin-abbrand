"""Konfigurationsablauf und Entitäten gegen eine nachgestellte App."""
import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries  # noqa: E402
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from homeassistant.exceptions import HomeAssistantError  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from homeassistant.helpers.service_info.hassio import HassioServiceInfo  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

DOMAIN = "kamin_abbrand"
URL = "http://app:8100/integration"
DATEN = {CONF_HOST: "app", CONF_PORT: 8100, CONF_TOKEN: "geheim"}
ZUSTAND = {
    "phase": "Abbrand", "satz": "Das Feuer brennt gut.", "abgas": 221.0, "ziel": 220.0, "klappe_soll": 53, "klappe_ist": None,
    "zug": 8.6, "twt": 46.5, "puffer_unten": 33.1, "gradient": -3.9, "i_anteil": 50.8, "seit_s": 660, "betrieb_h": 12.5,
    "tuer_offen": False, "feuer": True, "stoerung": False, "nachlegen_empfohlen": False, "fuehler_stumm": False,
    "beobachtung": True, "ausgaenge_zugewiesen": False, "handbetrieb": False, "wartung": False, "tuerkontakt_vorhanden": True,
    "leistung": "Normal", "klappe_manuell": 100, "kp": 0.5, "tn": 7, "kd": 0, "version": "0.2.0",
}
BEOBACHTUNG = "Beobachtungsbetrieb: wird von der Automation gesteuert"


async def einrichten(hass, aioclient_mock):
    aioclient_mock.get(f"{URL}/zustand", json=ZUSTAND)
    eintrag = MockConfigEntry(domain=DOMAIN, data=DATEN, unique_id=DOMAIN)
    eintrag.add_to_hass(hass)
    assert await hass.config_entries.async_setup(eintrag.entry_id)
    await hass.async_block_till_done()
    return eintrag


async def test_von_hand_hinzufuegen(hass, aioclient_mock):
    aioclient_mock.get(f"{URL}/zustand", json=ZUSTAND)
    ergebnis = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert ergebnis["type"] is FlowResultType.FORM
    ergebnis = await hass.config_entries.flow.async_configure(ergebnis["flow_id"], DATEN)
    assert ergebnis["type"] is FlowResultType.CREATE_ENTRY and ergebnis["data"] == DATEN


async def test_falsches_token_wird_gemeldet(hass, aioclient_mock):
    aioclient_mock.get(f"{URL}/zustand", status=401)
    ergebnis = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    ergebnis = await hass.config_entries.flow.async_configure(ergebnis["flow_id"], DATEN)
    assert ergebnis["errors"] == {"base": "token_falsch"}


async def test_angebot_der_app(hass, aioclient_mock):
    aioclient_mock.get(f"{URL}/zustand", json=ZUSTAND)
    ergebnis = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HASSIO},
        data=HassioServiceInfo(config={"host": "app", "port": 8100, "token": "geheim"}, name="Kamin", slug="local_kamin_abbrand", uuid="1"))
    assert ergebnis["step_id"] == "hassio_confirm"
    ergebnis = await hass.config_entries.flow.async_configure(ergebnis["flow_id"], {})
    assert ergebnis["type"] is FlowResultType.CREATE_ENTRY and ergebnis["data"] == DATEN


async def test_alle_entitaeten_erscheinen(hass, aioclient_mock):
    eintrag = await einrichten(hass, aioclient_mock)
    entitaeten = er.async_entries_for_config_entry(er.async_get(hass), eintrag.entry_id)
    nach_art = {}
    for e in entitaeten:
        nach_art.setdefault(e.domain, []).append(e)
    assert {k: len(v) for k, v in nach_art.items()} == {
        "sensor": 13, "binary_sensor": 7, "switch": 3, "select": 1, "number": 5, "button": 3}
    assert hass.states.get("sensor.kamin_abbrand_abgastemperatur").state == "221.0"
    assert hass.states.get("sensor.kamin_abbrand_phase").state == "Abbrand"
    assert hass.states.get("select.kamin_abbrand_leistung").state == "Normal"
    assert hass.states.get("sensor.kamin_abbrand_abbrand_dauer").state == "11.0"


@pytest.mark.parametrize("domain, dienst, ziel, daten", [
    ("switch", "turn_on", "switch.kamin_abbrand_handbetrieb", {}),
    ("switch", "turn_on", "switch.kamin_abbrand_wartung", {}),
    ("select", "select_option", "select.kamin_abbrand_leistung", {"option": "Power"}),
    ("number", "set_value", "number.kamin_abbrand_klappe_manuell", {"value": 50}),
    ("number", "set_value", "number.kamin_abbrand_zieltemperatur_soll", {"value": 240}),
    ("button", "press", "button.kamin_abbrand_fuhler_neustart", {}),
])
async def test_beobachtungsbetrieb_lehnt_ab(hass, aioclient_mock, domain, dienst, ziel, daten):
    await einrichten(hass, aioclient_mock)
    aioclient_mock.post(f"{URL}/befehl", status=409, json={"fehler": BEOBACHTUNG})
    with pytest.raises(HomeAssistantError, match="Beobachtungsbetrieb"):
        await hass.services.async_call(domain, dienst, {"entity_id": ziel, **daten}, blocking=True)


async def test_kp_geht_an_die_app(hass, aioclient_mock):
    await einrichten(hass, aioclient_mock)
    aioclient_mock.post(f"{URL}/befehl", json={"ok": True})
    await hass.services.async_call("number", "set_value", {"entity_id": "number.kamin_abbrand_kp", "value": 0.8}, blocking=True)
    aufruf = [c for c in aioclient_mock.mock_calls if c[0] == "POST"][-1]
    assert aufruf[2] == {"name": "kp", "wert": 0.8}
