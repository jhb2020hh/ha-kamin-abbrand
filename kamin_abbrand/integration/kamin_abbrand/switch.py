"""Schalter. Handbetrieb schaltet im Betrieb den Handbetrieb der App (Ausschalten immer), Wartung liest die App nur."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity

from .koordinator import KaminEntitaet

_LISTE = (("handbetrieb", "Handbetrieb"), ("wartung", "Wartung"), ("tuerkontakt_vorhanden", "Türkontakt vorhanden"))


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities(KaminSchalter(entry.runtime_data, *e) for e in _LISTE)


class KaminSchalter(KaminEntitaet, SwitchEntity):
    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.get(self.schluessel))

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.befehl(self.schluessel, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.befehl(self.schluessel, False)
