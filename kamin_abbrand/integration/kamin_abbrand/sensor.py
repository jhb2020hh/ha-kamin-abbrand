"""Messwerte und Klartext."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfPressure, UnitOfTemperature, UnitOfTime

from .koordinator import KaminEntitaet

# Schlüssel, Name, Einheit, Geräteklasse, Stellen
_ZAHLEN = (
    ("abgas", "Abgastemperatur", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, 0),
    ("ziel", "Zieltemperatur", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, 0),
    ("klappe_soll", "Klappe Soll", PERCENTAGE, None, 0),
    ("klappe_ist", "Klappe Ist", PERCENTAGE, None, 0),
    ("zug", "Kaminzug", UnitOfPressure.PA, SensorDeviceClass.PRESSURE, 1),
    ("twt", "Wärmetauscher", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, 1),
    ("puffer_unten", "Puffer unten", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, 1),
    ("gradient", "Temperaturanstieg", "K/min", None, 1),
    ("i_anteil", "I-Anteil", PERCENTAGE, None, 1),
    ("betrieb_h", "Betriebsstunden", UnitOfTime.HOURS, SensorDeviceClass.DURATION, 1),
    ("amortisation_prozent", "Amortisation", PERCENTAGE, None, 1),
)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    k = entry.runtime_data
    async_add_entities([KaminText(k, "phase", "Phase"), KaminText(k, "satz", "Status"), KaminText(k, "wasser_status", "Wasser"), AbbrandDauer(k)]
                       + [KaminZahl(k, *z) for z in _ZAHLEN] + [KaminNetto(k)])


class KaminText(KaminEntitaet, SensorEntity):
    @property
    def native_value(self):
        return self.coordinator.data.get(self.schluessel)


class KaminZahl(KaminEntitaet, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, k, schluessel, name, einheit, klasse, stellen) -> None:
        super().__init__(k, schluessel, name)
        self._attr_native_unit_of_measurement = einheit
        self._attr_device_class = klasse
        self._attr_suggested_display_precision = stellen

    @property
    def native_value(self):
        return self.coordinator.data.get(self.schluessel)

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.get(self.schluessel) is not None


class AbbrandDauer(KaminEntitaet, SensorEntity):
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_suggested_display_precision = 0

    def __init__(self, k) -> None:
        super().__init__(k, "abbrand_dauer", "Abbrand-Dauer")

    @property
    def native_value(self):
        d = self.coordinator.data
        return round(d["seit_s"] / 60, 1) if d.get("phase") == "Abbrand" and d.get("seit_s") is not None else 0


class KaminNetto(KaminZahl):
    """Netto-Ersparnis. Das Währungszeichen stellt der Nutzer in der App ein, es steht in jedem Stand."""

    def __init__(self, k) -> None:
        super().__init__(k, "amortisation_netto", "Netto-Ersparnis", None, None, 0)

    @property
    def native_unit_of_measurement(self):
        return self.coordinator.data.get("waehrung") or "€"
