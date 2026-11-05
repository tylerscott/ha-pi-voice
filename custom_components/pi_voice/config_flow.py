"""Config flow for the pi-voice Conversation integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import PiVoiceAuthError, PiVoiceClient, PiVoiceConnectionError, PiVoiceError
from .const import (
    CONF_API_KEY,
    CONF_HANDOFF,
    CONF_PROMPT,
    CONF_TIMEOUT,
    CONF_URL,
    DEFAULT_HANDOFF,
    DEFAULT_PROMPT,
    DEFAULT_TIMEOUT,
    DOMAIN,
    MAX_HANDOFF,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        vol.Optional(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


class PiVoiceConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for pi-voice."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the agent URL and optional key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_URL] = user_input[CONF_URL].rstrip("/")
            self._async_abort_entries_match({CONF_URL: user_input[CONF_URL]})
            errors = await self._async_validate(user_input)
            if not errors:
                return self.async_create_entry(
                    title="pi-voice",
                    data=user_input,
                    options={
                        CONF_PROMPT: DEFAULT_PROMPT,
                        CONF_TIMEOUT: DEFAULT_TIMEOUT,
                        CONF_HANDOFF: DEFAULT_HANDOFF,
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start re-authentication after the key was rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new key."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_API_KEY: user_input.get(CONF_API_KEY)}
            errors = await self._async_validate(data)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
        )

    async def _async_validate(self, data: Mapping[str, Any]) -> dict[str, str]:
        client = PiVoiceClient(
            async_get_clientsession(self.hass), data[CONF_URL], data.get(CONF_API_KEY)
        )
        try:
            await client.async_validate()
        except PiVoiceAuthError:
            return {"base": "invalid_auth"}
        except PiVoiceConnectionError:
            return {"base": "cannot_connect"}
        except PiVoiceError:
            _LOGGER.exception("Unexpected response from pi-voice")
            return {"base": "unknown"}
        return {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PiVoiceOptionsFlow:
        """Return the options flow."""
        return PiVoiceOptionsFlow()


class PiVoiceOptionsFlow(OptionsFlowWithReload):
    """Prompt, hand-off and timeout."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the options form."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(CONF_PROMPT, default=options.get(CONF_PROMPT, DEFAULT_PROMPT)): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True)
                ),
                vol.Optional(CONF_HANDOFF, default=options.get(CONF_HANDOFF, DEFAULT_HANDOFF)): NumberSelector(
                    NumberSelectorConfig(min=0, max=MAX_HANDOFF, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="s")
                ),
                vol.Optional(CONF_TIMEOUT, default=options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)): NumberSelector(
                    NumberSelectorConfig(min=30, max=1800, step=10, mode=NumberSelectorMode.BOX, unit_of_measurement="s")
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
