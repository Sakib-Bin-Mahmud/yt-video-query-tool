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

# Usage: run.sh [console-script]
# Default console script is yt-video-query-tool-api; pass yt-video-query-tool-rss to run the RSS scraper
SCRIPT=${1:-yt-video-query-tool-api}

# prefer venv-installed console script when available
if [ -x "$VENV/bin/$SCRIPT" ]; then
  exec "$VENV/bin/$SCRIPT" "$@"
fi

if command -v "$SCRIPT" >/dev/null 2>&1; then
  exec "$SCRIPT" "$@"
fi

echo "Console script '$SCRIPT' not found. Install the package (pip install -e .) or run via .venv/bin/$SCRIPT"
exit 1
