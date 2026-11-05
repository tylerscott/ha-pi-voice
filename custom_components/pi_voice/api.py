"""HTTP client for a pi-voice agent."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
from typing import Any

import aiohttp


class PiVoiceError(Exception):
    """pi-voice returned an error."""


class PiVoiceAuthError(PiVoiceError):
    """pi-voice rejected the API key."""


class PiVoiceConnectionError(PiVoiceError):
    """pi-voice could not be reached."""


class PiVoiceClient:
    """Talk to pi-voice's native conversation endpoint."""

    def __init__(self, session: aiohttp.ClientSession, url: str, api_key: str | None) -> None:
        self._session = session
        self._base = url.rstrip("/")
        self._api_key = api_key or None

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def async_validate(self) -> dict[str, Any]:
        """Check the agent is up and the key is accepted."""
        try:
            async with self._session.get(
                f"{self._base}/health", timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                health = await resp.json(content_type=None)
            if not isinstance(health, dict) or not health.get("ok"):
                raise PiVoiceError(f"Unexpected health response: {health!r}")
            # /health is open; /v1/models is behind the key when one is set.
            async with self._session.get(
                f"{self._base}/v1/models",
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status in (401, 403):
                    raise PiVoiceAuthError("Invalid API key")
                if resp.status >= 400:
                    raise PiVoiceError(f"HTTP {resp.status}")
            return health
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise PiVoiceConnectionError(str(err) or type(err).__name__) from err

    async def async_respond(self, payload: dict[str, Any], timeout: float) -> str:
        """Send one utterance and return the spoken reply."""
        try:
            async with self._session.post(
                f"{self._base}/api/conversation",
                json=payload,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status in (401, 403):
                    raise PiVoiceAuthError("Invalid API key")
                try:
                    data = await resp.json(content_type=None)
                except ValueError:
                    data = None
                if not isinstance(data, dict):
                    raise PiVoiceError(f"HTTP {resp.status}: non-JSON response")
                if resp.status >= 400:
                    error = data.get("error")
                    message = error.get("message") if isinstance(error, dict) else error
                    raise PiVoiceError(f"HTTP {resp.status}: {message}")
                return str(data.get("response") or "")
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise PiVoiceConnectionError(str(err) or type(err).__name__) from err

    async def async_stream(self, payload: dict[str, Any], timeout: float) -> AsyncIterator[tuple[str, Any]]:
        """Send one utterance and yield ("delta", text) events, then ("done", data)."""
        try:
            async with self._session.post(
                f"{self._base}/api/conversation/stream",
                json=payload,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=timeout, sock_read=timeout),
            ) as resp:
                if resp.status in (401, 403):
                    raise PiVoiceAuthError("Invalid API key")
                if resp.status >= 400:
                    raise PiVoiceError(f"HTTP {resp.status}: {(await resp.text())[:200]}")
                async for raw in resp.content:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    kind = event.get("type")
                    if kind == "delta":
                        yield ("delta", str(event.get("text", "")))
                    elif kind == "done":
                        yield ("done", event)
                        return
                    elif kind == "error":
                        raise PiVoiceError(str(event.get("message", "stream error")))
                raise PiVoiceError("stream ended without a result")
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise PiVoiceConnectionError(str(err) or type(err).__name__) from err
