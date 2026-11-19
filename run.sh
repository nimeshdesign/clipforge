#!/usr/bin/env bash
# One-command start. Uses uv if available, otherwise python venv + pip.
cd "$(dirname "$0")"
if command -v uv >/dev/null || [ -x "$HOME/.local/bin/uv" ]; then
  UV=$(command -v uv || echo "$HOME/.local/bin/uv")
  [ -d .venv ] || "$UV" venv -q --python 3.12 .venv
  "$UV" pip install -q --python .venv/bin/python -r requirements.txt
else
  [ -d .venv ] || python3 -m venv .venv
  .venv/bin/python -m pip install -q -r requirements.txt
fi
exec .venv/bin/python -m uvicorn gta6clips.server:app --host 127.0.0.1 --port 8800
