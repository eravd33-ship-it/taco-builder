"""🤖 The free assistant: Google's Gemini API, used with a free key from Google AI Studio.

Same job and same interface as `assistant.Assistant` (Claude), so the app treats them alike. It talks to the plain REST
API with httpx (no extra dependency); the key travels in a header, never in the URL, so it cannot end up in a log.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Sequence
from typing import Any

import httpx

from .assistant import AssistantError

log = logging.getLogger(__name__)

BASE_URL = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
MODEL_LABEL = "Gemini (חינם)"
PREFERRED_MODEL = "gemini-flash-latest"  # an alias Google keeps pointing at its current fast model
MAX_TOKENS = 8192  # includes the model's own "thinking", so not too small
MIN_KEY_LENGTH = 20
BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY"}
_FLASH = re.compile(r"gemini-(\d+(?:\.\d+)?)-flash(-lite)?")


def pick_model(models: Sequence[dict[str, Any]]) -> str:
    """The model to use out of the account's model list. The free tier covers the Flash family, so only those."""
    names = [
        str(model.get("name", "")).removeprefix("models/")
        for model in models
        if "generateContent" in (model.get("supportedGenerationMethods") or [])
    ]
    if PREFERRED_MODEL in names:
        return PREFERRED_MODEL
    found = [(float(m.group(1)), not m.group(2), name) for name in names if (m := _FLASH.fullmatch(name))]
    # newest version first; at the same version the full Flash beats Flash-Lite
    return max(found)[2] if found else PREFERRED_MODEL


def _error_info(response: httpx.Response) -> tuple[str, str]:
    """(Google's status word, its message) from an error response; both empty if the body is not the usual JSON."""
    try:
        error = response.json().get("error") or {}
        return str(error.get("status") or ""), str(error.get("message") or "")
    except (ValueError, AttributeError):
        return "", ""


def explain(response: httpx.Response) -> AssistantError:
    """Turn a failed response into something a person can act on."""
    status, message = _error_info(response)
    code = response.status_code
    if code in (401, 403) or (code == 400 and "api key" in message.lower()):
        return AssistantError("מפתח ה-API לא תקין או שאין לו הרשאה. אפשר ליצור חדש ב-Google AI Studio.", "key")
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        return AssistantError(
            "הגעת למגבלת השימוש החינמית של Gemini. כדאי לנסות שוב בעוד דקה, ואם זה חוזר, מחר.", "rate"
        )
    if code >= 500:
        return AssistantError("תקלה זמנית אצל גוגל. כדאי לנסות שוב עוד מעט.", "server")
    log.warning("Gemini request failed: HTTP %s %s", code, status)
    return AssistantError("הבקשה לעוזר נכשלה. כדאי לנסות שוב.", "error")


def _connection_error(error: httpx.HTTPError) -> AssistantError:
    if isinstance(error, httpx.TimeoutException):
        return AssistantError("העוזר לא ענה בזמן. כדאי לנסות שוב.", "connection")
    return AssistantError("אין חיבור לאינטרנט, או שגוגל לא זמינה כרגע.", "connection")


class GeminiAssistant:
    label = MODEL_LABEL

    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._key = api_key
        self._transport = transport
        self._model: str | None = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=BASE_URL, timeout=60, transport=self._transport, headers={"x-goog-api-key": self._key}
        )

    async def _discover_model(self, client: httpx.AsyncClient) -> str:
        response = await client.get("/models", params={"pageSize": 200})
        if response.is_error:
            raise explain(response)
        return pick_model(response.json().get("models") or [])

    async def check_key(self) -> None:
        """Free: listing the models costs nothing, but only works with a valid key."""
        try:
            async with self._client() as client:
                self._model = await self._discover_model(client)
        except httpx.HTTPError as error:
            raise _connection_error(error) from None

    async def ask(self, system: str, messages: Sequence[dict[str, str]], effort: str = "low") -> str:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [
                {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                for m in messages
            ],
            "generationConfig": {"maxOutputTokens": MAX_TOKENS},
        }
        try:
            async with self._client() as client:
                if self._model is None:
                    self._model = await self._discover_model(client)
                response = await client.post(f"/models/{self._model}:generateContent", json=body)
                if response.status_code == 404:  # the model was retired: look for the current one, once
                    self._model = await self._discover_model(client)
                    response = await client.post(f"/models/{self._model}:generateContent", json=body)
        except httpx.HTTPError as error:
            raise _connection_error(error) from None
        if response.is_error:
            raise explain(response)
        return self._read(response.json())

    @staticmethod
    def _read(data: dict[str, Any]) -> str:
        candidates = data.get("candidates") or []
        if not candidates or (data.get("promptFeedback") or {}).get("blockReason"):
            raise AssistantError("העוזר לא יכול לענות על זה. כדאי לנסח את הבקשה אחרת.", "refusal")
        candidate = candidates[0]
        if candidate.get("finishReason") in BLOCKED:
            raise AssistantError("העוזר לא יכול לענות על זה. כדאי לנסח את הבקשה אחרת.", "refusal")
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(str(part.get("text", "")) for part in parts if not part.get("thought")).strip()
        if not text:
            raise AssistantError("לא התקבלה תשובה. כדאי לנסות שוב.", "empty")
        if candidate.get("finishReason") == "MAX_TOKENS":
            text += "\n…(התשובה נקטעה)"
        return text
