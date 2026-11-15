"""Leistungsmodus und Brennstoff."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity

from .koordinator import KaminEntitaet


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([KaminLeistung(entry.runtime_data), KaminBrennstoff(entry.runtime_data)])


class KaminLeistung(KaminEntitaet, SelectEntity):
    _attr_options = ["Eco", "Normal", "Power"]

    def __init__(self, k) -> None:
        super().__init__(k, "leistung", "Leistung")

    @property
    def current_option(self):
        return self.coordinator.data.get("leistung")

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.befehl("leistung", option)


class KaminBrennstoff(KaminEntitaet, SelectEntity):
    """Brennstoff der Auflage. Umstellen geht in jeder Phase, die App stellt nach dem Brikett-Nachlauf selbst zurück."""
    _attr_options = ["Holz", "Briketts"]
    _attr_icon = "mdi:fire"

    def __init__(self, k) -> None:
        super().__init__(k, "brennstoff", "Brennstoff")

    @property
    def current_option(self):
        return self.coordinator.data.get("brennstoff")

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.befehl("brennstoff", option)
