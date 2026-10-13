"""End-to-end: real python-telegram-bot handlers, with Telegram's servers faked."""

import itertools
from datetime import date, timedelta

import pytest
from telegram import Update

from creator_bot.config import Settings
from creator_bot.core.app import build_application
from creator_bot.modules.ideas import IdeasModule
from creator_bot.modules.motivation import MotivationModule
from creator_bot.modules.routines import RoutinesModule
from creator_bot.modules.videos import VideosModule
from creator_bot.storage import Database

from .conftest import OWNER, TODAY, FakeRequest

STRANGER = 7
_ids = itertools.count(1)


@pytest.fixture
async def bot(clock):
    fake = FakeRequest()
    app = build_application(
        Settings(telegram_token="1:x"),
        module_classes=[MotivationModule, VideosModule, RoutinesModule, IdeasModule],
        db=Database(),
        clock=clock,
        request=fake,
    )
    await app.initialize()
    core = app.bot_data["core"]
    core.bot = app.bot

    async def say(text, chat_id=OWNER):
        message = {
            "message_id": next(_ids),
            "date": 0,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "Test"},
            "text": text,
        }
        if text.startswith("/"):
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        await app.process_update(Update.de_json({"update_id": next(_ids), "message": message}, app.bot))

    async def press(data, markup=None):
        query = {
            "id": str(next(_ids)),
            "from": {"id": OWNER, "is_bot": False, "first_name": "Test"},
            "chat_instance": "chat",
            "data": data,
            "message": {
                "message_id": 500,
                "date": 0,
                "chat": {"id": OWNER, "type": "private"},
                "text": "…",
                "reply_markup": markup or {"inline_keyboard": [[{"text": "x", "callback_data": data}]]},
            },
        }
        await app.process_update(Update.de_json({"update_id": next(_ids), "callback_query": query}, app.bot))

    yield core, fake, say, press
    await app.shutdown()


async def test_first_start_claims_the_bot(bot):
    core, fake, say, _ = bot
    await say("hello", chat_id=STRANGER)
    assert "/start" in fake.last_text() and core.owner_chat_id is None

    await say("/start")
    assert core.owner_chat_id == OWNER
    welcome = fake.sent()[-1]
    assert "סרטון חדש" in welcome["text"]
    assert welcome["reply_markup"]["keyboard"][0][0]["text"] == "📋 היום"

    await say("/start", chat_id=STRANGER)
    assert "בוט פרטי" in fake.last_text()
    assert core.owner_chat_id == OWNER


async def test_create_video_with_buttons_and_typed_date(bot):
    core, fake, say, press = bot
    await say("/start")

    await say("🎬 סרטון חדש")
    assert "מה השם" in fake.last_text()
    await say("איך לערוך מהר")
    assert "מתי לפרסם" in fake.last_text()
    await press("videos:date:7")
    assert "נכנס ללוח" in fake.last_text()

    await say("/video")
    await say("ולוג שבועי")
    await say("לא תאריך")
    assert "לא הצלחתי להבין" in fake.sent()[-2]["text"]
    await say("25.10")

    videos = core.get("videos").service.active()
    assert [(v.title, v.publish_date) for v in videos] == [
        ("איך לערוך מהר", TODAY + timedelta(days=7)),
        ("ולוג שבועי", date(2026, 10, 25)),
    ]


async def test_menu_button_interrupts_a_question(bot):
    core, fake, say, _ = bot
    await say("/start")
    await say("🎬 סרטון חדש")
    await say("🎯 יעדים")  # not taken as the video's title
    assert "היעדים שלך" in fake.last_text()
    assert core.get("videos").service.active() == []


async def test_daily_digest_and_marking_done(bot):
    core, fake, say, press = bot
    await say("/start")
    core.get("videos").service.create("סרטון להיום", TODAY + timedelta(days=1), TODAY)

    await core.send_digest("morning")
    digest = fake.sent()[-1]
    assert digest["chat_id"] == OWNER and "בוקר טוב" in digest["text"] and "לצלם" in digest["text"]
    done_button = fake.buttons(digest)[0]["callback_data"]
    assert done_button == "videos:done:1:film"

    await press(done_button, digest["reply_markup"])
    assert fake.sent("answerCallbackQuery")[-1]["text"].startswith("✅ צילום")
    assert fake.sent("editMessageReplyMarkup")  # the done row disappears from the digest
    assert "הבא בתור" in fake.last_text()  # motivation reacts

    await press(done_button)  # a second tap does not skip a stage
    assert fake.sent("answerCallbackQuery")[-1]["text"] == "כבר מסומן ✅"
    assert core.get("videos").service.get(1).current.key == "edit"


async def test_routine_setup_with_day_picker(bot):
    core, fake, say, press = bot
    await say("/start")
    await say("🔁 משימות קבועות")
    await press("routines:new")
    await press("routines:tpl:0")
    await press("routines:save")
    assert fake.sent("answerCallbackQuery")[-1]["text"] == "צריך לבחור לפחות יום אחד"
    await press("routines:day:6")
    await press(f"routines:day:{TODAY.weekday()}")
    await press("routines:save")
    assert "נשמר" in fake.sent("editMessageText")[-1]["text"]

    routine = core.get("routines").service.all()[0]
    assert routine.title == "💬 להגיב לתגובות" and routine.weekdays == {6, TODAY.weekday()}

    await say("/today")
    assert "להגיב לתגובות" in fake.last_text()


async def test_free_text_offers_to_save_an_idea(bot):
    core, fake, say, press = bot
    await say("/start")
    await say("סרטון על 5 טעויות של מתחילים")
    assert "לשמור כרעיון" in fake.last_text()
    await press("ideas:savefree")
    assert core.get("ideas").service.open()[0].text == "סרטון על 5 טעויות של מתחילים"

    await say("/cancel")
    await say("/idea ביקורת על מיקרופון")
    assert core.get("ideas").service.count_open() == 2


async def test_goal_setting(bot):
    core, fake, say, press = bot
    await say("/start")
    await press("motivation:goalup")
    await press("motivation:setup:3")
    assert core.get("motivation").uploads_goal == 3
    assert "3 סרטונים בשבוע" in fake.sent("editMessageText")[-1]["text"]
