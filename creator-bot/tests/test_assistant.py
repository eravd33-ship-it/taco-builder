"""The AI assistant: the Claude request it makes, how failures read, and the desktop endpoints around it."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from creator_bot.assistant import (
    FALLBACK_BETA,
    MODEL,
    Assistant,
    AssistantError,
    explain,
    parse_ideas,
)
from creator_bot.modules.youtube_stats import ChannelStats

from .conftest import TODAY

KEY = "sk-ant-api03-this-is-a-test-key-0000"
REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def sdk_error(cls, status: int, message: str = "boom"):
    response = httpx2.Response(status, request=REQUEST, json={"error": {"message": message}})
    return cls(message, response=response, body=None)


class FakeClaude:
    """Stands in for anthropic.AsyncAnthropic: records every call and answers from a script."""

    def __init__(self, reply="שלום מהעוזר", stop_reason="end_turn"):
        self.reply, self.stop_reason = reply, stop_reason
        self.calls, self.retrieved = [], []
        self.error = self.key_error = self.gate = None
        self.models = SimpleNamespace(retrieve=self._retrieve)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _retrieve(self, model):
        self.retrieved.append(model)
        if self.key_error:
            raise self.key_error

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.gate:
            await self.gate.wait()
        if self.error:
            raise self.error
        text = self.reply(kwargs) if callable(self.reply) else self.reply
        blocks = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)]
        return SimpleNamespace(stop_reason=self.stop_reason, content=blocks)


@pytest.fixture
def claude():
    return FakeClaude()


@pytest.fixture
def desk(make_desk, claude):
    desk = make_desk()
    desk.app._assistant_factory = lambda key: Assistant(key, client=claude)
    return desk


async def configure(desk):
    await desk.ok("PUT", "/api/assistant", {"api_key": KEY})


# -- the Claude request -------------------------------------------------------------------------
async def test_the_request_uses_the_documented_parameters(claude):
    claude.reply = "  תשובה  "
    answer = await Assistant(KEY, client=claude).ask("SYSTEM", [{"role": "user", "content": "היי"}], effort="low")
    assert answer == "תשובה"  # only text blocks, trimmed (the thinking block is ignored)
    call = claude.calls[0]
    assert call["model"] == MODEL == "claude-opus-5-5"
    assert call["system"] == "SYSTEM" and call["messages"] == [{"role": "user", "content": "היי"}]
    assert call["output_config"] == {"effort": "low"} and call["max_tokens"] >= 16000
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
    assert "thinking" not in call and "temperature" not in call  # Opus 5.5: thinking is always on, sampling is fixed


async def test_refusals_and_empty_answers_are_reported(claude):
    claude.stop_reason = "refusal"
    with pytest.raises(AssistantError) as refused:
        await Assistant(KEY, client=claude).ask("s", [{"role": "user", "content": "x"}])
    assert refused.value.kind == "refusal"

    claude.stop_reason, claude.reply = "end_turn", "   "
    with pytest.raises(AssistantError) as empty:
        await Assistant(KEY, client=claude).ask("s", [{"role": "user", "content": "x"}])
    assert empty.value.kind == "empty"

    claude.stop_reason, claude.reply = "max_tokens", "חצי תשובה"
    assert (await Assistant(KEY, client=claude).ask("s", [{"role": "user", "content": "x"}])).endswith("(התשובה נקטעה)")


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (sdk_error(anthropic.AuthenticationError, 401), "key"),
        (sdk_error(anthropic.PermissionDeniedError, 403), "key"),
        (sdk_error(anthropic.RateLimitError, 429), "rate"),
        (sdk_error(anthropic.BadRequestError, 400, "Your credit balance is too low"), "billing"),
        (sdk_error(anthropic.BadRequestError, 400, "something else"), "error"),
        (sdk_error(anthropic.InternalServerError, 529), "server"),
        (anthropic.APITimeoutError(request=REQUEST), "connection"),
        (anthropic.APIConnectionError(request=REQUEST), "connection"),
    ],
)
def test_failures_become_messages_a_person_can_act_on(error, kind):
    explained = explain(error)
    assert explained.kind == kind and explained.message  # Hebrew text, never a traceback or the raw API message


async def test_an_sdk_failure_surfaces_as_an_assistant_error(claude):
    claude.error = sdk_error(anthropic.RateLimitError, 429)
    with pytest.raises(AssistantError) as error:
        await Assistant(KEY, client=claude).ask("s", [{"role": "user", "content": "x"}])
    assert error.value.kind == "rate"

    claude.key_error = sdk_error(anthropic.AuthenticationError, 401)
    with pytest.raises(AssistantError) as bad_key:
        await Assistant(KEY, client=claude).check_key()
    assert bad_key.value.kind == "key"


def test_ideas_are_parsed_leniently():
    text = '1. "איך לערוך מהר" ||| כי כולם רוצים\n- טיפים למתחילים ||| קצר וברור\nשורה בלי מפריד\n|||\n'
    assert [(i.title, i.why) for i in parse_ideas(text)] == [
        ("איך לערוך מהר", "כי כולם רוצים"),
        ("טיפים למתחילים", "קצר וברור"),
    ]
    assert [i.title for i in parse_ideas("רעיון אחד\n* רעיון שני")] == ["רעיון אחד", "רעיון שני"]  # format ignored
    assert len(parse_ideas("\n".join(f"כותרת {n} ||| למה" for n in range(20)), limit=5)) == 5
    assert parse_ideas("") == []


# -- the desktop endpoints --------------------------------------------------------------------------
async def test_nothing_works_without_a_key(desk):
    status = await desk.ok("GET", "/api/assistant")
    assert (status["configured"], status["messages"]) == (False, [])
    assert status["provider"] == "gemini" and status["model"] == "Gemini (חינם)"  # the free assistant is offered first
    assert (await desk.call("POST", "/api/assistant/chat", {"message": "היי"}))[0] == 409
    assert (await desk.call("POST", "/api/assistant/ideas", {}))[0] == 409


async def test_a_key_is_only_saved_if_it_works(desk, claude):
    for bad in ("", "hello", "sk-ant-short", "sk-ant-"):
        assert (await desk.call("PUT", "/api/assistant", {"api_key": bad}))[0] == 400
    assert claude.retrieved == []  # malformed keys never even reach Anthropic

    claude.key_error = sdk_error(anthropic.AuthenticationError, 401)
    status, data = await desk.call("PUT", "/api/assistant", {"api_key": KEY})
    assert status == 400 and "מפתח" in data["error"]
    assert not (await desk.ok("GET", "/api/assistant"))["configured"]

    claude.key_error = None
    status = await desk.ok("PUT", "/api/assistant", {"api_key": f"  {KEY}  "})
    assert claude.retrieved == [MODEL, MODEL]
    assert (status["configured"], status["source"], status["key_hint"]) == (True, "app", "…0000")
    assert KEY not in str(await desk.ok("GET", "/api/assistant"))  # the key itself is never sent back


async def test_chat_knows_the_channel_and_remembers_the_conversation(desk, claude):
    await configure(desk)
    await desk.ok("POST", "/api/videos", {"title": "ביקורת מצלמה", "date": (TODAY + timedelta(days=3)).isoformat()})
    await desk.ok("POST", "/api/ideas", {"text": "10 טיפים לעריכה"})
    await desk.ok("PUT", "/api/goals", {"uploads_per_week": 3})

    claude.reply = "תשובה ראשונה"
    assert (await desk.ok("POST", "/api/assistant/chat", {"message": "מה כדאי לי לעשות?"}))["reply"] == "תשובה ראשונה"
    first = claude.calls[0]
    for expected in ("ביקורת מצלמה", "10 טיפים לעריכה", "יעד העלאות שבועי: 3", "אל תמציא"):
        assert expected in first["system"]
    assert first["messages"] == [{"role": "user", "content": "מה כדאי לי לעשות?"}]
    assert first["output_config"] == {"effort": "low"}

    claude.reply = "תשובה שנייה"
    await desk.ok("POST", "/api/assistant/chat", {"message": "ועוד?"})
    assert [m["role"] for m in claude.calls[1]["messages"]] == [
        "user",
        "assistant",
        "user",
    ]  # history is sent each time
    assert claude.calls[1]["messages"][1]["content"] == "תשובה ראשונה"

    status = await desk.ok("GET", "/api/assistant")
    assert [(m["role"], m["text"]) for m in status["messages"]][-2:] == [
        ("user", "ועוד?"),
        ("assistant", "תשובה שנייה"),
    ]
    await desk.ok("DELETE", "/api/assistant/chat")
    assert (await desk.ok("GET", "/api/assistant"))["messages"] == []


async def test_a_long_conversation_is_trimmed_and_still_starts_with_the_user(desk, claude):
    await configure(desk)
    for number in range(20):
        await desk.ok("POST", "/api/assistant/chat", {"message": f"הודעה {number}"})
    sent = claude.calls[-1]["messages"]
    assert len(sent) <= 30 and sent[0]["role"] == "user" and sent[-1]["content"] == "הודעה 19"
    assert all(a["role"] != b["role"] for a, b in zip(sent, sent[1:], strict=False))  # strictly alternating


async def test_a_failed_question_is_not_kept_so_it_can_be_sent_again(desk, claude):
    await configure(desk)
    claude.error = sdk_error(anthropic.RateLimitError, 429)
    status, data = await desk.call("POST", "/api/assistant/chat", {"message": "שאלה"})
    assert status == 429 and "דקה" in data["error"]
    assert (await desk.ok("GET", "/api/assistant"))["messages"] == []

    claude.error = None
    await desk.ok("POST", "/api/assistant/chat", {"message": "שאלה"})
    assert len((await desk.ok("GET", "/api/assistant"))["messages"]) == 2


async def test_message_limits(desk):
    await configure(desk)
    assert (await desk.call("POST", "/api/assistant/chat", {"message": "   "}))[0] == 400
    assert (await desk.call("POST", "/api/assistant/chat", {"message": "א" * 4001}))[0] == 400


async def test_only_one_question_at_a_time(desk, claude):
    await configure(desk)
    claude.gate = asyncio.Event()
    first = asyncio.create_task(desk.call("POST", "/api/assistant/chat", {"message": "ראשונה"}))
    await asyncio.sleep(0.05)
    assert (await desk.ok("GET", "/api/assistant"))["busy"]
    assert (await desk.call("POST", "/api/assistant/chat", {"message": "שנייה"}))[0] == 409
    assert (await desk.call("POST", "/api/assistant/ideas", {}))[0] == 409
    claude.gate.set()
    assert (await first)[0] == 200
    assert not (await desk.ok("GET", "/api/assistant"))["busy"]


async def test_idea_suggestions(desk, claude):
    await configure(desk)
    claude.reply = "\n".join(f"כותרת {n} ||| סיבה {n}" for n in range(1, 6))
    ideas = (await desk.ok("POST", "/api/assistant/ideas", {"topic": "עריכה בטלפון", "avoid": ["כותרת ישנה"]}))["ideas"]
    assert ideas[0] == {"title": "כותרת 1", "why": "סיבה 1"} and len(ideas) == 5
    call = claude.calls[0]
    assert "עריכה בטלפון" in call["messages"][0]["content"]
    assert "כותרת ישנה" in call["system"] and "|||" in call["system"]  # told not to repeat, and the exact format
    assert call["output_config"] == {"effort": "medium"}

    await desk.ok("POST", "/api/assistant/ideas", {})  # no topic: still fine
    assert "רעיונות לסרטונים הבאים" in claude.calls[1]["messages"][0]["content"]
    assert (await desk.ok("GET", "/api/assistant"))["messages"] == []  # brainstorming is not part of the chat


async def test_channel_stats_are_part_of_the_context_when_known(make_desk, claude):
    desk = make_desk(youtube_api_key="K", youtube_channel="UC1")
    desk.app._assistant_factory = lambda key: Assistant(key, client=claude)
    desk.app.stats.service.save(TODAY, ChannelStats("הערוץ", 12345, 678, 9))
    await configure(desk)
    await desk.ok("POST", "/api/assistant/chat", {"message": "איך הולך לי?"})
    assert "12,345 צפיות" in claude.calls[0]["system"] and "678 מנויים" in claude.calls[0]["system"]


async def test_a_key_in_the_environment_works_and_cannot_be_removed_from_the_app(make_desk, claude):
    desk = make_desk(anthropic_api_key=KEY)
    desk.app._assistant_factory = lambda key: Assistant(key, client=claude)
    status = await desk.ok("GET", "/api/assistant")
    assert (status["configured"], status["source"]) == (True, "env")
    assert (await desk.call("DELETE", "/api/assistant"))[0] == 409
    await desk.ok("POST", "/api/assistant/chat", {"message": "היי"})

    await desk.ok("PUT", "/api/assistant", {"api_key": "sk-ant-api03-another-key-1111111"})
    assert (await desk.ok("GET", "/api/assistant"))["source"] == "app"  # a key saved in the app wins


async def test_removing_the_key_also_forgets_the_conversation(desk):
    await configure(desk)
    await desk.ok("POST", "/api/assistant/chat", {"message": "היי"})
    status = await desk.ok("DELETE", "/api/assistant")
    assert (status["configured"], status["messages"]) == (False, [])
    assert (await desk.call("POST", "/api/assistant/chat", {"message": "היי"}))[0] == 409
