"""The REAL anthropic SDK against a local fake of the API: checks the request that would actually go on the wire."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))
from fake_anthropic import SMOKE_KEY, SMOKE_REPLY, FakeAnthropic  # noqa: E402

from creator_bot.assistant import FALLBACK_BETA, MODEL, Assistant, AssistantError  # noqa: E402


@pytest.fixture
def api(monkeypatch):
    fake = FakeAnthropic()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", fake.url)
    yield fake
    fake.close()


async def test_a_real_request_has_the_expected_shape(api):
    answer = await Assistant(SMOKE_KEY).ask("SYSTEM", [{"role": "user", "content": "שלום"}], effort="medium")
    assert answer == SMOKE_REPLY  # the thinking block is skipped, the text block returned

    request = api.requests[0]
    assert request["method"] == "POST" and request["path"].startswith("/v1/messages")
    assert request["headers"]["x-api-key"] == SMOKE_KEY
    assert FALLBACK_BETA in request["headers"]["anthropic-beta"]
    body = request["body"]
    assert body["model"] == MODEL and body["system"] == "SYSTEM" and body["fallbacks"] == "default"
    assert body["messages"] == [{"role": "user", "content": "שלום"}]
    assert body["output_config"] == {"effort": "medium"} and body["max_tokens"] >= 16000
    assert "thinking" not in body and "temperature" not in body


async def test_checking_a_key_costs_nothing_and_a_bad_key_is_explained(api):
    await Assistant(SMOKE_KEY).check_key()
    assert api.requests[0]["method"] == "GET" and api.requests[0]["path"] == f"/v1/models/{MODEL}"
    assert not [r for r in api.requests if r["path"].startswith("/v1/messages")]  # no tokens were used

    with pytest.raises(AssistantError) as error:
        await Assistant("sk-ant-the-wrong-key-00000000").check_key()
    assert error.value.kind == "key" and "מפתח" in error.value.message
    with pytest.raises(AssistantError) as error:
        await Assistant("sk-ant-the-wrong-key-00000000").ask("s", [{"role": "user", "content": "x"}])
    assert error.value.kind == "key"


async def test_the_whole_desktop_flow_through_the_real_sdk(api, make_desk):
    desk = make_desk(anthropic_api_key=SMOKE_KEY)
    desk.app._assistant_factory = Assistant
    status = await desk.ok("GET", "/api/assistant")
    assert (status["configured"], status["source"]) == (True, "env")
    assert (await desk.ok("POST", "/api/assistant/chat", {"message": "שלום"}))["reply"] == SMOKE_REPLY
    ideas = await desk.ok("POST", "/api/assistant/ideas", {"topic": "עריכה"})
    assert ideas["ideas"][0]["title"]  # an unformatted reply still yields something to show
