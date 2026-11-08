"""Einstellwerte. Kp, Tn und Kd wirken nur auf die App."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime

from .koordinator import KaminEntitaet

# Befehl, Schlüssel im Zustand, Name, Min, Max, Schritt, Einheit
_LISTE = (
    ("klappe_manuell", "klappe_manuell", "Klappe manuell", 0, 100, 5, PERCENTAGE),
    ("t_ziel", "ziel", "Zieltemperatur Soll", 120, 500, 5, UnitOfTemperature.CELSIUS),
    ("kp", "kp", "Kp", 0.05, 5, 0.05, "%/K"),
    ("tn", "tn", "Tn", 1, 60, 0.5, UnitOfTime.MINUTES),
    ("kd", "kd", "Kd", 0, 20, 0.5, "%·min/K"),
)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities(KaminZahlwert(entry.runtime_data, *e) for e in _LISTE)


class KaminZahlwert(KaminEntitaet, NumberEntity):
    _attr_mode = NumberMode.BOX

    def __init__(self, k, befehl, schluessel, name, minimum, maximum, schritt, einheit) -> None:
        super().__init__(k, befehl, name)
        self._befehl, self._feld = befehl, schluessel
        self._attr_native_min_value = minimum
        self._attr_native_max_value = maximum
        self._attr_native_step = schritt
        self._attr_native_unit_of_measurement = einheit

    @property
    def native_value(self):
        return self.coordinator.data.get(self._feld)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.befehl(self._befehl, value)
