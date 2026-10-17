"""The core: connects settings, storage, modules and Telegram.

The core knows nothing about videos, ideas or goals. It only provides the
plumbing every module relies on: the owner check, the main menu, routing of
buttons and typed answers, the morning digest / evening check-in, and the
scheduler.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import date, datetime
from typing import Any

from telegram import (
    BotCommand,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    Defaults,
    MessageHandler,
    TypeHandler,
    filters,
)
from telegram.request import BaseRequest

from ..config import Settings
from ..storage import Database
from . import ui
from .events import EventBus
from .module import Command, Ctx, MenuButton, Module

log = logging.getLogger(__name__)

TODAY_BUTTON = "📋 היום"
AWAITING = "awaiting"  # key in user_data: which module waits for typed input


class Core:
    def __init__(self, settings: Settings, db: Database, clock: Callable[[], datetime] | None = None) -> None:
        self.settings = settings
        self.db = db
        self.events = EventBus()
        self.modules: dict[str, Module] = {}
        self.bot: Any = None  # set once the Telegram application starts
        self._clock = clock or (lambda: datetime.now(settings.timezone))

    # -- time -------------------------------------------------------------------
    def now(self) -> datetime:
        return self._clock()

    def today(self) -> date:
        return self.now().date()

    # -- owner ------------------------------------------------------------------
    @property
    def owner_chat_id(self) -> int | None:
        return self.settings.owner_chat_id or self.db.get("core.owner_chat_id")

    def claim_owner(self, chat_id: int) -> None:
        self.db.set("core.owner_chat_id", chat_id)
        log.info("Bot owner set to chat %s", chat_id)

    # -- modules ----------------------------------------------------------------
    def load(self, module_classes: Iterable[type[Module]]) -> None:
        created: list[Module] = []
        for cls in module_classes:
            if cls.name in self.settings.disabled_modules:
                log.info("Module %s disabled by DISABLED_MODULES", cls.name)
                continue
            if not cls.is_enabled(self.settings):
                log.info("Module %s is not configured – skipped", cls.name)
                continue
            if cls.name in self.modules:
                raise ValueError(f"Two modules are named {cls.name!r}")
            module = cls(self)
            self.modules[cls.name] = module
            created.append(module)
        for module in created:  # only the new ones: loading again later must not subscribe twice
            module.setup()
        log.info("Loaded modules: %s", ", ".join(self.modules))

    def unload(self, name: str) -> None:
        module = self.modules.pop(name, None)
        if module is not None:
            module.teardown()

    def get(self, name: str) -> Any:
        return self.modules.get(name)

    # -- messaging --------------------------------------------------------------
    async def notify(self, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
        """Send a message to the owner (used by scheduled jobs and events)."""
        chat_id = self.owner_chat_id
        if self.bot is None or chat_id is None:
            return
        await self.bot.send_message(chat_id, text, reply_markup=reply_markup)

    async def ask(
        self,
        update: Update,
        context: Ctx,
        module: str,
        step: str,
        prompt: str,
        data: dict[str, Any] | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        context.user_data[AWAITING] = {"module": module, "step": step, "data": data or {}}
        await ui.reply(update, f"{prompt}\n<i>(/cancel לביטול)</i>", reply_markup)

    @staticmethod
    def pop_awaiting(context: Ctx, module: str, step: str | None = None) -> dict[str, Any] | None:
        awaiting = context.user_data.get(AWAITING)
        if not awaiting or awaiting["module"] != module or (step and awaiting["step"] != step):
            return None
        return context.user_data.pop(AWAITING)

    # -- digest -----------------------------------------------------------------
    async def digest(self, kind: str, day: date, header: str) -> tuple[str | None, InlineKeyboardMarkup | None]:
        """Build the morning digest or evening check-in from all modules' sections."""
        sections = []
        for module in self.modules.values():
            hook = module.morning_section if kind == "morning" else module.evening_section
            try:
                section = await hook(day)
            except Exception:
                log.exception("Module %s failed to build its %s section", module.name, kind)
                continue
            if section is not None:
                sections.append(section)

        open_count = sum(section.open_count for section in sections)
        footer = None
        for module in self.modules.values():
            footer = await module.digest_footer(kind, day, open_count)
            if footer:
                break

        parts = [section.text for section in sections if section.text]
        if footer:
            parts.append(footer)
        if not parts:
            if kind == "evening":
                return None, None  # nothing to say tonight – stay quiet
            parts.append("📭 אין משימות להיום.")
        buttons = [row for section in sections for row in section.buttons]
        text = header + "\n\n" + "\n\n".join(parts)
        return text, InlineKeyboardMarkup(buttons) if buttons else None

    async def send_digest(self, kind: str) -> None:
        day = self.today()
        if kind == "morning":
            header = f"☀️ <b>בוקר טוב!</b> {ui.fmt_day(day)}"
        else:
            header = "🌙 <b>צ'ק-אין של ערב</b>"
        text, markup = await self.digest(kind, day, header)
        if text:
            await self.notify(text, markup)

    # -- Telegram wiring --------------------------------------------------------
    def menu_items(self) -> list[MenuButton]:
        items = [MenuButton(TODAY_BUTTON, self.cmd_today, order=0)]
        for module in self.modules.values():
            items.extend(module.menu_buttons())
        return sorted(items, key=lambda item: item.order)

    def menu_keyboard(self) -> ReplyKeyboardMarkup:
        texts = [item.text for item in self.menu_items()]
        rows = [texts[i : i + 2] for i in range(0, len(texts), 2)]
        return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)

    def all_commands(self) -> list[Command]:
        commands = [
            Command("start", "פתיחת התפריט הראשי", self.cmd_start),
            Command("today", "המשימות להיום", self.cmd_today),
            Command("help", "עזרה ורשימת פקודות", self.cmd_help),
            Command("cancel", "ביטול הפעולה הנוכחית", self.cmd_cancel),
        ]
        for module in self.modules.values():
            commands.extend(module.commands())
        return commands

    def register(self, app: Application) -> None:
        app.bot_data["core"] = self
        app.add_handler(TypeHandler(Update, self._gate), group=-1)

        only_new = filters.UpdateType.MESSAGE
        for command in self.all_commands():
            app.add_handler(CommandHandler(command.name, self._fresh(command.handler), filters=only_new))

        menu = {item.text: item.handler for item in self.menu_items()}

        async def on_menu(update: Update, context: Ctx) -> None:
            context.user_data.pop(AWAITING, None)
            await menu[update.effective_message.text](update, context)

        app.add_handler(MessageHandler(only_new & filters.Text(list(menu)), on_menu))
        app.add_handler(CallbackQueryHandler(self._on_callback))
        app.add_handler(MessageHandler(only_new & filters.TEXT & ~filters.COMMAND, self._on_text))
        app.add_error_handler(self._on_error)

        if app.job_queue is not None:
            app.job_queue.run_daily(self._morning_job, self.settings.morning_time, name="morning")
            app.job_queue.run_daily(self._evening_job, self.settings.evening_time, name="evening")
            for module in self.modules.values():
                module.schedule(app.job_queue)

    async def _morning_job(self, context: Ctx) -> None:
        await self.send_digest("morning")

    async def _evening_job(self, context: Ctx) -> None:
        await self.send_digest("evening")

    async def post_init(self, app: Application) -> None:
        self.bot = app.bot
        await app.bot.set_my_commands([BotCommand(c.name, c.description) for c in self.all_commands()])

    @staticmethod
    def _fresh(handler: Callable[[Update, Ctx], Any]) -> Callable[[Update, Ctx], Any]:
        """A command always starts fresh: drop any pending question."""

        async def wrapped(update: Update, context: Ctx) -> None:
            context.user_data.pop(AWAITING, None)
            await handler(update, context)

        return wrapped

    async def _gate(self, update: Update, context: Ctx) -> None:
        """Only the owner may use the bot. The first /start claims ownership."""
        chat = update.effective_chat
        if chat is None:
            raise ApplicationHandlerStop
        owner = self.owner_chat_id
        private = chat.type == ChatType.PRIVATE
        if owner is None:
            if private and update.message and (update.message.text or "").startswith("/start"):
                self.claim_owner(chat.id)
                return
            if private and update.message:
                await update.message.reply_text("👋 שלח /start כדי להתחיל.")
            raise ApplicationHandlerStop
        if chat.id != owner:
            if private and update.message:
                await update.message.reply_text("🔒 זה בוט פרטי.")
            raise ApplicationHandlerStop

    async def _on_callback(self, update: Update, context: Ctx) -> None:
        query = update.callback_query
        module_name, _, rest = (query.data or "").partition(":")
        module = self.modules.get(module_name)
        toast = None if module else "הכפתור הזה כבר לא פעיל"
        try:
            if module is not None:
                action, *args = rest.split(":")
                toast = await module.on_callback(update, context, action, args)
        finally:
            try:
                await query.answer(toast)
            except TelegramError:
                pass

    async def _on_text(self, update: Update, context: Ctx) -> None:
        awaiting = context.user_data.pop(AWAITING, None)
        if awaiting and (module := self.modules.get(awaiting["module"])):
            await module.on_input(update, context, awaiting["step"], awaiting["data"])
            return
        for module in self.modules.values():
            if await module.on_free_text(update, context):
                return
        await ui.reply(update, "לא בטוח שהבנתי 🤔 אפשר לבחור מהתפריט למטה, או /help לעזרה.", self.menu_keyboard())

    async def _on_error(self, update: object, context: Ctx) -> None:
        log.error("Error while handling an update", exc_info=context.error)
        if isinstance(update, Update) and update.effective_chat and update.effective_chat.id == self.owner_chat_id:
            try:
                await update.effective_chat.send_message(
                    "😕 משהו השתבש. אפשר לנסות שוב – ואם זה חוזר, כדאי להציץ בלוגים."
                )
            except TelegramError:
                pass

    # -- core commands ----------------------------------------------------------
    async def cmd_start(self, update: Update, context: Ctx) -> None:
        user = update.effective_user
        name = ui.esc(user.first_name) if user and user.first_name else ""
        lines = [f"👋 היי {name}! אני העוזר האישי שלך לניהול הערוץ.", "", "<b>מה אני יודע לעשות:</b>"]
        lines += [
            "🎬 לתזמן כל סרטון – צילום, עריכה והעלאה – ולהזכיר בזמן",
            "🔁 להזכיר משימות קבועות (תגובות, פוסטים, אנליטיקס)",
            "💪 לשלוח מוטיבציה ולעקוב אחרי היעדים שלך",
            "💡 לשמור רעיונות לסרטונים",
        ]
        if "youtube_stats" in self.modules:
            lines.append("📈 לעקוב אחרי הצפיות והמנויים בערוץ")
        if "drive" in self.modules:
            lines.append("📁 לפתוח תיקייה בדרייב לכל סרטון ולגבות את הנתונים")
        morning = self.settings.morning_time.strftime("%H:%M")
        evening = self.settings.evening_time.strftime("%H:%M")
        lines += [
            "",
            f"⏰ כל בוקר ב-{morning} תקבל את המשימות להיום, וב-{evening} צ'ק-אין קצר.",
            "",
            "בוא נתחיל – לחץ על <b>🎬 סרטון חדש</b> 👇",
        ]
        await ui.reply(update, "\n".join(lines), self.menu_keyboard())

    async def cmd_today(self, update: Update, context: Ctx) -> None:
        day = self.today()
        text, markup = await self.digest("morning", day, f"📋 <b>המשימות להיום</b> · {ui.fmt_day(day)}")
        await ui.reply(update, text or "📭 אין משימות להיום.", markup)

    async def cmd_help(self, update: Update, context: Ctx) -> None:
        lines = ["ℹ️ <b>איך משתמשים</b>", "הכי נוח – הכפתורים בתפריט למטה 👇", "", "<b>פקודות:</b>"]
        lines += [f"/{command.name} – {command.description}" for command in self.all_commands()]
        await ui.reply(update, "\n".join(lines), self.menu_keyboard())

    async def cmd_cancel(self, update: Update, context: Ctx) -> None:
        await ui.reply(update, "👍 בוטל.", self.menu_keyboard())


def build_application(
    settings: Settings,
    module_classes: Iterable[type[Module]] | None = None,
    db: Database | None = None,
    clock: Callable[[], datetime] | None = None,
    request: BaseRequest | None = None,
) -> Application:
    from ..modules import ALL_MODULES

    core = Core(settings, db or Database(settings.db_path), clock)
    core.load(ALL_MODULES if module_classes is None else module_classes)

    defaults = Defaults(
        parse_mode=ParseMode.HTML,
        tzinfo=settings.timezone,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    builder = Application.builder().token(settings.telegram_token).defaults(defaults).post_init(core.post_init)
    if request is not None:
        builder = builder.request(request).get_updates_request(request)
    app = builder.build()
    core.register(app)
    return app
