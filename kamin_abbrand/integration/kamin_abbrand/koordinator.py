"""Holt den Stand von der App und leitet Befehle weiter. Regeln liegen nur in der App."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

_LOG = logging.getLogger(__name__)
_ZEITLIMIT = aiohttp.ClientTimeout(total=30)


class AppFehler(Exception):
    """Die App ist nicht erreichbar."""


class TokenFalsch(Exception):
    """Die App hat das Token abgelehnt."""


async def zustand_holen(hass: HomeAssistant, host: str, port: int, token: str) -> dict[str, Any]:
    sitzung = async_get_clientsession(hass)
    try:
        async with sitzung.get(
            f"http://{host}:{port}/integration/zustand",
            headers={"Authorization": f"Bearer {token}"}, timeout=_ZEITLIMIT,
        ) as antwort:
            if antwort.status == 401:
                raise TokenFalsch
            if antwort.status >= 300:
                raise AppFehler(f"HTTP {antwort.status}")
            return await antwort.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as fehler:
        raise AppFehler(str(fehler)) from fehler


class Koordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: Any, host: str, port: int, token: str) -> None:
        super().__init__(hass, _LOG, config_entry=entry, name=DOMAIN, update_interval=timedelta(seconds=5))
        self.host, self.port, self.token = host, port, token

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await zustand_holen(self.hass, self.host, self.port, self.token)
        except TokenFalsch as fehler:
            raise ConfigEntryAuthFailed("Token falsch") from fehler
        except AppFehler as fehler:
            raise UpdateFailed(f"App nicht erreichbar: {fehler}") from fehler

    async def befehl(self, name: str, wert: Any = None) -> None:
        """Eine Ablehnung der App wird als Fehlermeldung in HA sichtbar."""
        sitzung = async_get_clientsession(self.hass)
        try:
            async with sitzung.post(
                f"http://{self.host}:{self.port}/integration/befehl",
                json={"name": name, "wert": wert},
                headers={"Authorization": f"Bearer {self.token}"}, timeout=_ZEITLIMIT,
            ) as antwort:
                daten = await antwort.json(content_type=None)
                if antwort.status >= 300:
                    raise HomeAssistantError((daten or {}).get("fehler") or f"HTTP {antwort.status}")
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as fehler:
            raise HomeAssistantError(f"App nicht erreichbar: {fehler}") from fehler
        await self.async_request_refresh()


class KaminEntitaet(CoordinatorEntity[Koordinator]):
    _attr_has_entity_name = True

    def __init__(self, koordinator: Koordinator, schluessel: str, name: str) -> None:
        super().__init__(koordinator)
        self.schluessel = schluessel
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{schluessel}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "app")}, name="Kamin Abbrand", manufacturer="Eigenbau",
            model="Abbrandsteuerung", sw_version=(koordinator.data or {}).get("version"),
        )
