"""Kamin Abbrand: zeigt die App als Gerät in Home Assistant."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant

from .koordinator import Koordinator

PLATTFORMEN = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH, Platform.SELECT, Platform.NUMBER, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    koordinator = Koordinator(hass, entry, entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_TOKEN])
    await koordinator.async_config_entry_first_refresh()
    entry.runtime_data = koordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATTFORMEN)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATTFORMEN)
