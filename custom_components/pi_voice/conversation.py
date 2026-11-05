"""Conversation agent that forwards Assist utterances to pi-voice."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
import logging
import re
from typing import Any, Literal

from homeassistant.components import conversation, persistent_notification
from homeassistant.components.assist_satellite import AssistSatelliteEntityFeature
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.const import ATTR_ENTITY_ID, ATTR_SUPPORTED_FEATURES, MATCH_ALL, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, intent
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PiVoiceConfigEntry
from .api import PiVoiceAuthError, PiVoiceConnectionError, PiVoiceError
from .const import (
    CONF_HANDOFF,
    CONF_KEEP_LISTENING,
    CONF_PROMPT,
    CONF_TIMEOUT,
    DEFAULT_HANDOFF,
    DEFAULT_KEEP_LISTENING,
    DEFAULT_PROMPT,
    DEFAULT_TIMEOUT,
    DOMAIN,
    HANDOFF_MESSAGE,
    NO_ANSWER_MESSAGE,
)

_LOGGER = logging.getLogger(__name__)

_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_SYMBOLS = re.compile(r"(\*\*|__|\*|`+|^#+\s*|^\s*[-*+]\s+|^>\s*)", re.MULTILINE)
_QUESTION_ENDINGS = ("?", ";", "？")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PiVoiceConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the pi-voice conversation entity."""
    async_add_entities([PiVoiceConversationEntity(entry)])


async def _anext(events: AsyncIterator[tuple[str, Any]]) -> tuple[str, Any] | None:
    """First event of the stream, or None when it ends at once."""
    async for event in events:
        return event
    return None


def _speakable(text: str) -> str:
    """Strip markdown so it is not spoken literally."""
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_SYMBOLS.sub("", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


class PiVoiceConversationEntity(conversation.ConversationEntity, conversation.AbstractConversationAgent):
    """pi-voice as an Assist conversation agent."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supports_streaming = True

    def __init__(self, entry: PiVoiceConfigEntry) -> None:
        """Initialize the agent."""
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="pi-voice",
            manufacturer="pi-durable",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=entry.data.get("url"),
        )

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """The model handles any language."""
        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        """Register as an agent when added."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister the agent when removed."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    # --- context -------------------------------------------------------------

    def _device_context(self, user_input: conversation.ConversationInput) -> dict[str, str]:
        """Name, area and best music player of the voice device that heard the request."""
        context: dict[str, str] = {}
        device = dr.async_get(self.hass).async_get(user_input.device_id) if user_input.device_id else None
        if device is None:
            return context
        context["device_name"] = device.name_by_user or device.name or ""
        area_entry = ar.async_get(self.hass).async_get_area(device.area_id) if device.area_id else None
        if area_entry is None:
            return context
        context["area"] = area_entry.name
        if player := self._music_player(area_entry.id):
            entity_id, platform = player
            context["media_player"] = entity_id
            context["media_player_platform"] = platform
        return context

    def _music_player(self, area_id: str) -> tuple[str, str] | None:
        """Best media player in an area: can search, else skip tracks, else play media; available first."""
        devices = dr.async_get(self.hass)
        best: tuple[tuple[bool, int], str, str] | None = None
        for entry in er.async_get(self.hass).entities.values():
            if entry.domain != "media_player" or entry.disabled:
                continue
            entry_area = entry.area_id
            if entry_area is None and entry.device_id:
                device = devices.async_get(entry.device_id)
                entry_area = device.area_id if device else None
            if entry_area != area_id:
                continue
            state = self.hass.states.get(entry.entity_id)
            if state is None:
                continue
            features = state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)
            if features & MediaPlayerEntityFeature.SEARCH_MEDIA:
                rank = 3
            elif features & MediaPlayerEntityFeature.NEXT_TRACK:
                rank = 2
            elif features & MediaPlayerEntityFeature.PLAY_MEDIA:
                rank = 1
            else:
                continue
            score = (state.state != STATE_UNAVAILABLE, rank)
            if best is None or score > best[0]:
                best = (score, entry.entity_id, entry.platform)
        return (best[1], best[2]) if best else None

    # --- turn ----------------------------------------------------------------

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Stream pi-voice's reply into the chat log; hand off if the first words take too long."""
        options = self.entry.options
        satellite_id = getattr(user_input, "satellite_id", None)
        payload = {
            "text": user_input.text,
            "conversation_id": chat_log.conversation_id,
            "language": user_input.language,
            "device_id": user_input.device_id,
            "satellite_id": satellite_id,
            "instructions": options.get(CONF_PROMPT, DEFAULT_PROMPT),
            "extra_system_prompt": user_input.extra_system_prompt,
            **self._device_context(user_input),
        }
        timeout = float(options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT))
        payload["timeout_ms"] = int(timeout * 1000)

        events = self.entry.runtime_data.async_stream(payload, timeout)
        first = self.hass.async_create_background_task(
            _anext(events), name=f"{DOMAIN} first delta {chat_log.conversation_id}"
        )
        collected: list[str] = []
        meta: dict[str, Any] = {}
        try:
            if handoff := float(options.get(CONF_HANDOFF, DEFAULT_HANDOFF)):
                done, _ = await asyncio.wait({first}, timeout=handoff)
                if not done:
                    _LOGGER.info("pi-voice gave no words within %.0f s; handing off", handoff)
                    self._hand_off(first, events, collected, satellite_id)
                    return self._reply(user_input, chat_log, HANDOFF_MESSAGE)
            first_event = await asyncio.shield(first)
        except asyncio.CancelledError:
            _LOGGER.warning("Assist cancelled the turn before the first words; announcing later")
            self._hand_off(first, events, collected, satellite_id)
            raise
        except PiVoiceError as err:
            return self._error(user_input, chat_log, self._failure_message(err))

        try:
            async for _content in chat_log.async_add_delta_content_stream(
                user_input.agent_id, self._deltas(first_event, events, collected, meta)
            ):
                pass
        except asyncio.CancelledError:
            _LOGGER.warning("Assist cancelled the turn mid-answer; announcing the rest later")
            self._hand_off(None, events, collected, satellite_id)
            raise
        except PiVoiceError as err:
            if not collected:
                return self._error(user_input, chat_log, self._failure_message(err))
            _LOGGER.warning("pi-voice stream ended early: %s", err)
        if meta.get("silent") and not collected:
            # The agent closed the exchange (a bare thanks, goodbye, or Whisper hearing "thank you" in silence):
            # end without speaking.
            return self._silent(user_input, chat_log)
        result = conversation.async_get_result_from_chat_log(user_input, chat_log)
        if options.get(CONF_KEEP_LISTENING, DEFAULT_KEEP_LISTENING):
            # Stay in the conversation unless the agent said the exchange is over.
            result.continue_conversation = bool(meta.get("continue_conversation", True))
        return result

    @staticmethod
    async def _deltas(
        first_event: tuple[str, Any] | None,
        events: AsyncIterator[tuple[str, Any]],
        collected: list[str],
        meta: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any]]:
        """Turn pi-voice events into chat-log deltas, remembering the text for late delivery."""
        yield {"role": "assistant"}
        pending = [first_event] if first_event is not None else []
        async def _all() -> AsyncIterator[tuple[str, Any]]:
            for item in pending:
                yield item
            async for item in events:
                yield item
        got_text = False
        async for kind, data in _all():
            if kind == "delta" and data:
                collected.append(data)
                got_text = True
                yield {"content": data}
            elif kind == "done":
                meta.update(data)
                if not got_text and not data.get("silent"):
                    text = str(data.get("response") or NO_ANSWER_MESSAGE)
                    collected.append(text)
                    yield {"content": text}
                return

    def _failure_message(self, err: PiVoiceError) -> str:
        if isinstance(err, PiVoiceAuthError):
            self.entry.async_start_reauth(self.hass)
            return "pi-voice rejected the API key."
        if isinstance(err, PiVoiceConnectionError):
            _LOGGER.warning("pi-voice request failed: %s", err)
            return "Sorry, I couldn't reach the assistant in time."
        _LOGGER.error("pi-voice returned an error: %s", err)
        return "Sorry, the assistant had a problem."

    @staticmethod
    def _needs_follow_up(text: str) -> bool:
        return text.rstrip().endswith(_QUESTION_ENDINGS)

    def _hand_off(
        self,
        first: asyncio.Task[tuple[str, Any] | None] | None,
        events: AsyncIterator[tuple[str, Any]],
        collected: list[str],
        satellite_id: str | None,
    ) -> None:
        """Let the stream finish in the background and deliver the full answer later."""
        self.hass.async_create_background_task(
            self._async_announce_when_done(first, events, collected, satellite_id),
            name=f"{DOMAIN} announce",
        )

    async def _async_announce_when_done(
        self,
        first: asyncio.Task[tuple[str, Any] | None] | None,
        events: AsyncIterator[tuple[str, Any]],
        collected: list[str],
        satellite_id: str | None,
    ) -> None:
        try:
            if first is not None:
                first_event = await first
                if first_event is not None and first_event[0] == "delta":
                    collected.append(first_event[1])
                elif first_event is not None and first_event[0] == "done" and not collected:
                    collected.append(str(first_event[1].get("response") or ""))
            async for kind, data in events:
                if kind == "delta":
                    collected.append(data)
                elif kind == "done" and not collected:
                    collected.append(str(data.get("response") or ""))
            message = _speakable("".join(collected))
            if not message:
                return
        except PiVoiceError as err:
            message = self._failure_message(err)
        await self._async_deliver(satellite_id, message)

    async def _async_deliver(self, satellite_id: str | None, message: str) -> None:
        """Speak a late answer on the satellite that asked; a question starts a new conversation."""
        state = self.hass.states.get(satellite_id) if satellite_id else None
        features = state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) if state else 0
        if features & AssistSatelliteEntityFeature.START_CONVERSATION and self._needs_follow_up(message):
            try:
                await self.hass.services.async_call(
                    "assist_satellite",
                    "start_conversation",
                    {ATTR_ENTITY_ID: satellite_id, "start_message": message, "extra_system_prompt": message},
                    blocking=True,
                )
            except HomeAssistantError as err:
                _LOGGER.warning("Could not start a conversation on %s: %s", satellite_id, err)
            else:
                return
        if features & AssistSatelliteEntityFeature.ANNOUNCE:
            try:
                await self.hass.services.async_call(
                    "assist_satellite", "announce", {ATTR_ENTITY_ID: satellite_id, "message": message}, blocking=True
                )
            except HomeAssistantError as err:
                _LOGGER.warning("Could not announce on %s: %s", satellite_id, err)
            else:
                return
        persistent_notification.async_create(self.hass, message, title="pi-voice")

    @staticmethod
    def _reply(
        user_input: conversation.ConversationInput, chat_log: conversation.ChatLog, reply: str
    ) -> conversation.ConversationResult:
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=user_input.agent_id, content=_speakable(reply) or NO_ANSWER_MESSAGE)
        )
        # Sets continue_conversation when the reply ends in a question mark.
        return conversation.async_get_result_from_chat_log(user_input, chat_log)

    @staticmethod
    def _silent(
        user_input: conversation.ConversationInput, chat_log: conversation.ChatLog
    ) -> conversation.ConversationResult:
        """End the exchange with nothing to say and the microphone closed."""
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech("")
        return conversation.ConversationResult(
            response=response, conversation_id=chat_log.conversation_id, continue_conversation=False
        )

    @staticmethod
    def _error(
        user_input: conversation.ConversationInput, chat_log: conversation.ChatLog, message: str
    ) -> conversation.ConversationResult:
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN, message)
        return conversation.ConversationResult(response=response, conversation_id=chat_log.conversation_id)
