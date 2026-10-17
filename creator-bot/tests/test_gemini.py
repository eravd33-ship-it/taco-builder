"""The free assistant (Gemini): the request it makes, how failures read, and how it sits next to Claude in the app."""

import json
from datetime import timedelta

import httpx
import pytest

from creator_bot.assistant import AssistantError
from creator_bot.gemini import PREFERRED_MODEL, GeminiAssistant, pick_model

from .conftest import TODAY

KEY = "AIzaSyTestKeyForTheFreeAssistant000000"
CLAUDE_KEY = "sk-ant-api03-this-is-a-test-key-0000"
MODELS = {
    "models": [
        {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-2.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
    ]
}


def answer(text="שלום מהעוזר", finish="STOP", **extra):
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}], **extra}


class FakeGemini:
    """Plays Google's API through httpx's mock transport: records every request, answers from a script."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.models = MODELS
        self.reply = answer()
        self.status = 200
        self.error: Exception | None = None
        self.generate_statuses: list[int] = []  # one per generate call, then self.status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error:
            raise self.error
        if request.url.path.endswith("/models"):
            if self.status != 200:
                return httpx.Response(self.status, json=self.reply)
            return httpx.Response(200, json=self.models)
        status = self.generate_statuses.pop(0) if self.generate_statuses else self.status
        return httpx.Response(status, json=self.reply if status == 200 else {"error": {"message": "x"}})

    def transport(self):
        return httpx.MockTransport(self)

    @property
    def generated(self):
        return [r for r in self.requests if r.url.path.endswith(":generateContent")]


@pytest.fixture
def google():
    return FakeGemini()


@pytest.fixture
def desk(make_desk, google):
    desk = make_desk()
    desk.app._gemini_factory = lambda key: GeminiAssistant(key, transport=google.transport())
    return desk


# -- the Gemini request -------------------------------------------------------------------------
async def test_the_request_as_google_documents_it(google):
    google.reply = answer("  תשובה  ")
    google.reply["candidates"][0]["content"]["parts"].insert(0, {"text": "מחשבה פנימית", "thought": True})
    messages = [
        {"role": "user", "content": "היי"},
        {"role": "assistant", "content": "שלום"},
        {"role": "user", "content": "ועוד?"},
    ]
    text = await GeminiAssistant(KEY, transport=google.transport()).ask("SYSTEM", messages)
    assert text == "תשובה"  # trimmed, and the model's own thoughts are not shown

    request = google.generated[0]
    assert request.method == "POST" and request.url.path == "/v1beta/models/gemini-2.5-flash:generateContent"
    assert request.headers["x-goog-api-key"] == KEY
    assert KEY not in str(request.url)  # never in the URL, so it cannot end up in a log
    body = json.loads(request.content)
    assert body["systemInstruction"] == {"parts": [{"text": "SYSTEM"}]}
    assert [(c["role"], c["parts"][0]["text"]) for c in body["contents"]] == [
        ("user", "היי"),
        ("model", "שלום"),  # Gemini calls the assistant's side "model"
        ("user", "ועוד?"),
    ]
    assert body["generationConfig"]["maxOutputTokens"] >= 4096


def test_the_model_is_picked_from_what_the_account_offers():
    flash = {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]}
    lite = {"name": "models/gemini-2.5-flash-lite", "supportedGenerationMethods": ["generateContent"]}
    newer = {"name": "models/gemini-3-flash", "supportedGenerationMethods": ["generateContent"]}
    alias = {"name": f"models/{PREFERRED_MODEL}", "supportedGenerationMethods": ["generateContent"]}
    pro = {"name": "models/gemini-3-pro", "supportedGenerationMethods": ["generateContent"]}
    no_chat = {"name": "models/gemini-9-flash", "supportedGenerationMethods": ["embedContent"]}

    assert pick_model([flash, lite, alias, newer]) == PREFERRED_MODEL  # the alias Google maintains wins
    assert pick_model([flash, lite, newer, pro, no_chat]) == "gemini-3-flash"  # else the newest Flash
    assert pick_model([lite, flash]) == "gemini-2.5-flash"  # same version: the full Flash
    assert pick_model([lite]) == "gemini-2.5-flash-lite"
    assert pick_model([pro, no_chat]) == PREFERRED_MODEL  # the free tier is Flash: never silently a Pro model
    assert pick_model([]) == PREFERRED_MODEL


async def test_the_model_is_looked_up_once_and_a_retired_one_is_replaced(google):
    assistant = GeminiAssistant(KEY, transport=google.transport())
    await assistant.ask("s", [{"role": "user", "content": "x"}])
    await assistant.ask("s", [{"role": "user", "content": "x"}])
    assert [r.url.path.endswith("/models") for r in google.requests] == [True, False, False]  # listed once

    google.generate_statuses = [404]  # the model was retired
    await assistant.ask("s", [{"role": "user", "content": "x"}])
    assert [r.url.path.endswith("/models") for r in google.requests][3:] == [False, True, False]


async def test_checking_a_key_only_lists_models(google):
    await GeminiAssistant(KEY, transport=google.transport()).check_key()
    assert [r.url.path for r in google.requests] == ["/v1beta/models"] and google.generated == []  # free, no tokens


@pytest.mark.parametrize(
    ("status", "body", "kind"),
    [
        (
            400,
            {"error": {"status": "INVALID_ARGUMENT", "message": "API key not valid. Please pass a valid API key."}},
            "key",
        ),
        (401, {"error": {"message": "nope"}}, "key"),
        (403, {"error": {"status": "PERMISSION_DENIED", "message": "x"}}, "key"),
        (429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota"}}, "rate"),
        (503, {"error": {"status": "UNAVAILABLE", "message": "overloaded"}}, "server"),
        (400, {"error": {"status": "INVALID_ARGUMENT", "message": "something else"}}, "error"),
        (418, "not json at all", "error"),
    ],
)
async def test_failures_become_messages_a_person_can_act_on(google, status, body, kind):
    google.status, google.reply = status, body
    for call in ("check_key", "ask"):
        with pytest.raises(AssistantError) as error:
            assistant = GeminiAssistant(KEY, transport=google.transport())
            await (
                assistant.check_key() if call == "check_key" else assistant.ask("s", [{"role": "user", "content": "x"}])
            )
        assert error.value.kind == kind and error.value.message  # Hebrew text, never the raw API message
        assert "quota" not in error.value.message and "overloaded" not in error.value.message


@pytest.mark.parametrize("error", [httpx.ConnectError("down"), httpx.ReadTimeout("slow")])
async def test_network_problems_are_reported_as_connection_errors(google, error):
    google.error = error
    with pytest.raises(AssistantError) as raised:
        await GeminiAssistant(KEY, transport=google.transport()).ask("s", [{"role": "user", "content": "x"}])
    assert raised.value.kind == "connection"


@pytest.mark.parametrize(
    ("reply", "kind"),
    [
        ({"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}, "refusal"),
        (answer("x", finish="SAFETY"), "refusal"),
        (answer("x", finish="PROHIBITED_CONTENT"), "refusal"),
        ({"candidates": [{"content": {"parts": []}, "finishReason": "STOP"}]}, "empty"),
        (answer("   "), "empty"),
        ({"candidates": [{"finishReason": "STOP"}]}, "empty"),
    ],
)
async def test_refusals_and_empty_answers_are_reported(google, reply, kind):
    google.reply = reply
    with pytest.raises(AssistantError) as error:
        await GeminiAssistant(KEY, transport=google.transport()).ask("s", [{"role": "user", "content": "x"}])
    assert error.value.kind == kind


async def test_a_cut_off_answer_says_so(google):
    google.reply = answer("חצי תשובה", finish="MAX_TOKENS")
    text = await GeminiAssistant(KEY, transport=google.transport()).ask("s", [{"role": "user", "content": "x"}])
    assert text.startswith("חצי תשובה") and text.endswith("(התשובה נקטעה)")


# -- in the app, next to Claude ---------------------------------------------------------------------
async def test_a_gemini_key_is_only_saved_if_it_works(desk, google):
    for bad in ("", "short", "has a space inside the key 00000000"):
        assert (await desk.call("PUT", "/api/assistant", {"provider": "gemini", "api_key": bad}))[0] == 400
    assert google.requests == []  # malformed keys never reach Google

    google.status, google.reply = 403, {"error": {"status": "PERMISSION_DENIED", "message": "x"}}
    status, data = await desk.call("PUT", "/api/assistant", {"provider": "gemini", "api_key": KEY})
    assert status == 400 and "מפתח" in data["error"]
    assert not (await desk.ok("GET", "/api/assistant"))["configured"]

    google.status = 200
    status = await desk.ok("PUT", "/api/assistant", {"provider": "gemini", "api_key": f"  {KEY}  "})
    assert (status["configured"], status["provider"], status["source"], status["key_hint"]) == (
        True,
        "gemini",
        "app",
        "…0000",
    )
    assert status["model"] == "Gemini (חינם)"
    assert KEY not in str(await desk.ok("GET", "/api/assistant"))  # the key itself is never sent back
    assert (await desk.call("PUT", "/api/assistant", {"provider": "nope", "api_key": KEY}))[0] == 400


async def test_chat_and_ideas_run_on_gemini(desk, google):
    await desk.ok("PUT", "/api/assistant", {"provider": "gemini", "api_key": KEY})
    await desk.ok("POST", "/api/videos", {"title": "ביקורת מצלמה", "date": (TODAY + timedelta(days=3)).isoformat()})

    google.reply = answer("תשובה מג'מיני")
    assert (await desk.ok("POST", "/api/assistant/chat", {"message": "מה כדאי לי?"}))["reply"] == "תשובה מג'מיני"
    body = json.loads(google.generated[-1].content)
    assert "ביקורת מצלמה" in body["systemInstruction"]["parts"][0]["text"]  # it knows the channel, like Claude does
    assert body["contents"] == [{"role": "user", "parts": [{"text": "מה כדאי לי?"}]}]

    google.reply = answer("\n".join(f"כותרת {n} ||| סיבה {n}" for n in range(1, 4)))
    ideas = (await desk.ok("POST", "/api/assistant/ideas", {"topic": "עריכה"}))["ideas"]
    assert [i["title"] for i in ideas] == ["כותרת 1", "כותרת 2", "כותרת 3"]


async def test_both_assistants_can_have_a_key_and_the_last_one_saved_is_used(desk, google, make_desk):
    claude_calls = []

    class FakeClaude:
        async def check_key(self):
            claude_calls.append("check")

        async def ask(self, system, messages, effort="low"):
            claude_calls.append("ask")
            return "תשובה מקלוד"

    desk.app._assistant_factory = lambda key: FakeClaude()
    await desk.ok("PUT", "/api/assistant", {"provider": "gemini", "api_key": KEY})
    status = await desk.ok("PUT", "/api/assistant", {"provider": "claude", "api_key": CLAUDE_KEY})
    assert status["provider"] == "claude" and status["model"] == "Claude Opus 5.5"
    assert {p["id"]: p["configured"] for p in status["providers"]} == {"gemini": True, "claude": True}
    assert (await desk.ok("POST", "/api/assistant/chat", {"message": "היי"}))["reply"] == "תשובה מקלוד"

    status = await desk.ok("PUT", "/api/assistant", {"provider": "gemini", "api_key": KEY})  # back to the free one
    assert status["provider"] == "gemini"
    google.reply = answer("שוב מג'מיני")
    assert (await desk.ok("POST", "/api/assistant/chat", {"message": "היי"}))["reply"] == "שוב מג'מיני"

    status = await desk.ok("DELETE", "/api/assistant")  # removes only the one in use
    assert status["provider"] == "claude" and status["configured"] and status["key_hint"] == "…0000"
    assert {p["id"]: p["configured"] for p in status["providers"]} == {"gemini": False, "claude": True}


async def test_a_gemini_key_in_the_environment_works_and_cannot_be_removed_from_the_app(make_desk, google):
    desk = make_desk(gemini_api_key=KEY)
    desk.app._gemini_factory = lambda key: GeminiAssistant(key, transport=google.transport())
    status = await desk.ok("GET", "/api/assistant")
    assert (status["configured"], status["provider"], status["source"]) == (True, "gemini", "env")
    assert (await desk.call("DELETE", "/api/assistant"))[0] == 409
    await desk.ok("POST", "/api/assistant/chat", {"message": "היי"})
    assert google.generated


async def test_an_old_install_with_a_claude_key_keeps_using_claude(make_desk):
    desk = make_desk()
    desk.app.db.set("assistant.api_key", CLAUDE_KEY)  # saved by the previous version, which had no "provider"
    status = await desk.ok("GET", "/api/assistant")
    assert (status["configured"], status["provider"], status["source"]) == (True, "claude", "app")
