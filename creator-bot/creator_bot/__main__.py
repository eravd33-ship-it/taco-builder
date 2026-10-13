"""Run the bot:  python -m creator_bot"""

import logging
from pathlib import Path

from telegram import Update

from .config import Settings, load_dotenv
from .core.app import build_application


def main() -> None:
    load_dotenv(Path.cwd() / ".env")
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # it would log the bot token in URLs
    settings = Settings.from_env()
    app = build_application(settings)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
