"""Knöpfe. Die App nennt den Grund, wenn einer gerade gesperrt ist."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity

from .koordinator import KaminEntitaet

_LISTE = (("autotune_verlauf", "Auto-Tune aus Verlauf"), ("autotune_aktiv", "Auto-Tune aktiv"), ("fuehler_neustart", "Fühler-Neustart"))


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities(KaminKnopf(entry.runtime_data, *e) for e in _LISTE)


class KaminKnopf(KaminEntitaet, ButtonEntity):
    async def async_press(self) -> None:
        await self.coordinator.befehl(self.schluessel)
