"""Zustände."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity

from .koordinator import KaminEntitaet

_LISTE = (
    ("tuer_offen", "Tür offen", BinarySensorDeviceClass.DOOR),
    ("feuer", "Feuer", BinarySensorDeviceClass.HEAT),
    ("stoerung", "Störung", BinarySensorDeviceClass.PROBLEM),
    ("nachlegen_empfohlen", "Nachlegen empfohlen", None),
    ("fuehler_stumm", "Fühler stumm", BinarySensorDeviceClass.PROBLEM),
    ("beobachtung", "Lauschen", None),
    ("ausgaenge_zugewiesen", "Ausgänge zugewiesen", None),
)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities(KaminBinaer(entry.runtime_data, *e) for e in _LISTE)


class KaminBinaer(KaminEntitaet, BinarySensorEntity):
    def __init__(self, k, schluessel, name, klasse) -> None:
        super().__init__(k, schluessel, name)
        self._attr_device_class = klasse

    @property
    def is_on(self):
        return self.coordinator.data.get(self.schluessel)
