"""Hinzufügen unter Geräte & Dienste, von Hand oder nach dem Angebot der App."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .const import DOMAIN, STANDARD_PORT
from .koordinator import AppFehler, TokenFalsch, zustand_holen


class KaminConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    _angebot: dict[str, Any] | None = None

    async def _pruefen(self, daten: dict[str, Any]) -> str | None:
        try:
            await zustand_holen(self.hass, daten[CONF_HOST], daten[CONF_PORT], daten[CONF_TOKEN])
        except TokenFalsch:
            return "token_falsch"
        except AppFehler:
            return "nicht_erreichbar"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        fehler: dict[str, str] = {}
        if user_input is not None:
            grund = await self._pruefen(user_input)
            if grund is None:
                return self.async_create_entry(title="Kamin Abbrand", data=user_input)
            fehler["base"] = grund
        schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT, default=STANDARD_PORT): int,
            vol.Required(CONF_TOKEN): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=fehler)

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        angebot = {CONF_HOST: discovery_info.config["host"], CONF_PORT: discovery_info.config["port"],
                   CONF_TOKEN: discovery_info.config["token"]}
        self._abort_if_unique_id_configured(updates=angebot)
        self._angebot = angebot
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._angebot is not None
        fehler: dict[str, str] = {}
        if user_input is not None:
            grund = await self._pruefen(self._angebot)
            if grund is None:
                return self.async_create_entry(title="Kamin Abbrand", data=self._angebot)
            fehler["base"] = grund
        return self.async_show_form(step_id="hassio_confirm", errors=fehler)
