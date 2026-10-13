#!/usr/bin/env sh
# Starts Creator Desk (Linux / macOS). The first run creates a private Python
# environment next to this file and installs what the app needs.
cd "$(dirname "$0")" || exit 1

PY=$(command -v python3 || command -v python)
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 3.10 or newer is required: https://www.python.org/downloads/"
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "First run - setting things up. This takes about a minute..."
  "$PY" -m venv .venv || exit 1
fi

if [ ! .venv/.ready -nt requirements.txt ] || [ ! .venv/.ready -nt requirements-window.txt ]; then
  .venv/bin/python -m pip install -q -r requirements.txt || { echo "Could not install the requirements - are you online?"; exit 1; }
  .venv/bin/python -m pip install -q -r requirements-window.txt >/dev/null 2>&1 || echo "(No native window available - the app will open in your browser.)"
  touch .venv/.ready
fi

exec .venv/bin/python -m creator_bot.desktop "$@"
