#!/usr/bin/env bash
set -euo pipefail

# Load .env if present (simple parser, ignores comments)
if [ -f .env ]; then
  export $(grep -v '^\s*#' .env | sed '/^\s*$$/d' | xargs)
fi

VENV=.venv
PYTHON=python3
if [ -x "$VENV/bin/python" ]; then
  PYTHON="$VENV/bin/python"
fi

exec "$PYTHON" scraper.py
