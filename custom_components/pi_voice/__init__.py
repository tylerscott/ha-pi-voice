"""Use a pi-voice durable agent as a Home Assistant conversation agent."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PiVoiceAuthError, PiVoiceClient, PiVoiceConnectionError
from .const import CONF_API_KEY, CONF_URL

PLATFORMS = [Platform.CONVERSATION]

type PiVoiceConfigEntry = ConfigEntry[PiVoiceClient]


async def async_setup_entry(hass: HomeAssistant, entry: PiVoiceConfigEntry) -> bool:
    """Set up pi-voice from a config entry."""
    client = PiVoiceClient(
        async_get_clientsession(hass), entry.data[CONF_URL], entry.data.get(CONF_API_KEY)
    )
    try:
        await client.async_validate()
    except PiVoiceAuthError as err:
        raise ConfigEntryAuthFailed("pi-voice rejected the API key") from err
    except PiVoiceConnectionError as err:
        raise ConfigEntryNotReady(f"pi-voice not reachable: {err}") from err
    entry.runtime_data = client
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PiVoiceConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
