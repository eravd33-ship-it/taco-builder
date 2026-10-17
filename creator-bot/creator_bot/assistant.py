"""🤖 The AI assistant: brainstorms video ideas and answers questions about the channel (Claude API).

Everything the user sees is Hebrew. The model gets a short, fresh snapshot of the channel (titles, goals, stats)
with every request and is told not to invent numbers; it never acts on the app, it only talks.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import anthropic

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
MODEL_LABEL = "Claude Opus 5.5"
MAX_TOKENS = 16000
# When Anthropic's safety classifiers decline a request, re-run it on the recommended fallback model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
KEY_PREFIX = "sk-ant-"

SYSTEM_BASE = """אתה עוזר אישי ליוצר תוכן ביוטיוב, בתוך אפליקציה לניהול הערוץ שלו.
- ענה בעברית, קצר, מעשי וחם. בלי הקדמות ארוכות.
- כתוב טקסט פשוט בלבד, בלי Markdown: בלי כוכביות, כותרות או טבלאות. אפשר רשימות עם מספרים או מקפים.
- יש לך רק את המידע שמופיע בהקשר שלמטה. אל תמציא מספרים, צפיות או עובדות על הערוץ. אם חסר מידע, אמור זאת או שאל.
- אין לך גישה לאינטרנט ולא לנתוני יוטיוב מעבר למה שמופיע כאן.
- אתה לא מבצע פעולות באפליקציה. אם המשתמש רוצה לתזמן סרטון או לשמור רעיון,
  הסבר באיזה מסך עושים זאת (סרטונים, רעיונות)."""

IDEAS_TASK = """משימה: הצע {count} רעיונות חדשים לסרטונים לערוץ הזה.
כתוב כל רעיון בשורה נפרדת, בפורמט המדויק הזה:
כותרת מוצעת ||| הסבר קצר במשפט אחד למה זה יעבוד
בלי מספור ובלי שום טקסט נוסף לפני או אחרי. אל תחזור על סרטונים או רעיונות שכבר קיימים או הוצעו."""


class AssistantError(Exception):
    """A failure whose `message` is ready to show to the user (Hebrew). `kind` says what went wrong."""

    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.message, self.kind = message, kind


@dataclass
class Idea:
    title: str
    why: str


def explain(error: Exception) -> AssistantError:
    """Turn an SDK exception into something a person can act on. Order matters: subclasses first."""
    if isinstance(error, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return AssistantError("מפתח ה-API לא תקין או שאין לו הרשאה. אפשר להחליף אותו בהגדרות.", "key")
    if isinstance(error, anthropic.RateLimitError):
        return AssistantError("יותר מדי בקשות בבת אחת. כדאי לנסות שוב בעוד דקה.", "rate")
    if isinstance(error, anthropic.BadRequestError) and "credit" in str(getattr(error, "message", error)).lower():
        return AssistantError("אין יתרה בחשבון Anthropic. צריך להוסיף אשראי (Billing) ואז לנסות שוב.", "billing")
    if isinstance(error, anthropic.APITimeoutError):
        return AssistantError("העוזר לא ענה בזמן. כדאי לנסות שוב.", "connection")
    if isinstance(error, anthropic.APIConnectionError):
        return AssistantError("אין חיבור לאינטרנט, או ש-Anthropic לא זמינה כרגע.", "connection")
    if isinstance(error, anthropic.APIStatusError) and error.status_code >= 500:
        return AssistantError("תקלה זמנית אצל Anthropic. כדאי לנסות שוב עוד מעט.", "server")
    log.exception("Assistant request failed", exc_info=error)
    return AssistantError("הבקשה לעוזר נכשלה. כדאי לנסות שוב.", "error")


class Assistant:
    def __init__(self, api_key: str, client: Any = None) -> None:
        # One retry, and a timeout shorter than the app's own request limit.
        self.client = client or anthropic.AsyncAnthropic(api_key=api_key, timeout=100.0, max_retries=1)

    async def check_key(self) -> None:
        """Free: looking up the model uses no tokens, but only works with a valid key."""
        try:
            await self.client.models.retrieve(MODEL)
        except anthropic.AnthropicError as error:
            raise explain(error) from None

    async def ask(self, system: str, messages: Sequence[dict[str, str]], effort: str = "low") -> str:
        try:
            response = await self.client.beta.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=list(messages),
                output_config={"effort": effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.AnthropicError as error:
            raise explain(error) from None
        if response.stop_reason == "refusal":
            raise AssistantError("העוזר לא יכול לענות על זה. כדאי לנסח את הבקשה אחרת.", "refusal")
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            raise AssistantError("לא התקבלה תשובה. כדאי לנסות שוב.", "empty")
        if response.stop_reason == "max_tokens":
            text += "\n…(התשובה נקטעה)"
        return text


def ideas_system(context: str, count: int) -> str:
    return f"{SYSTEM_BASE}\n\n{context}\n\n{IDEAS_TASK.format(count=count)}"


def chat_system(context: str) -> str:
    return f"{SYSTEM_BASE}\n\n{context}"


_BULLET = re.compile(r"^[\s\-*•·\d.)]+")


def parse_ideas(text: str, limit: int = 8) -> list[Idea]:
    """`title ||| why` per line. If the model ignored the format, fall back to one idea per line."""
    ideas: list[Idea] = []
    for line in text.splitlines():
        if "|||" not in line:
            continue
        title, _, why = line.partition("|||")
        title = _BULLET.sub("", title).strip().strip('"“”').strip()
        if title:
            ideas.append(Idea(title[:300], why.strip()[:400]))
    if not ideas:
        for line in text.splitlines():
            title = _BULLET.sub("", line).strip()
            if title:
                ideas.append(Idea(title[:300], ""))
    return ideas[:limit]
