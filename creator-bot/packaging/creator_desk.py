"""Entry point for the packaged app (PyInstaller needs a plain script, not `-m`)."""

from creator_bot.desktop.__main__ import main

raise SystemExit(main())
