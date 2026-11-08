"""Leistungsmodus."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity

from .koordinator import KaminEntitaet


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([KaminLeistung(entry.runtime_data)])


class KaminLeistung(KaminEntitaet, SelectEntity):
    _attr_options = ["Eco", "Normal", "Power"]

    def __init__(self, k) -> None:
        super().__init__(k, "leistung", "Leistung")

    @property
    def current_option(self):
        return self.coordinator.data.get("leistung")

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.befehl("leistung", option)
