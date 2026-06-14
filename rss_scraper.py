#!/usr/bin/env python3
"""Deprecated shim: use the installed console script instead.

Kept for backward compatibility. Prefer installing the package and running
`yt-video-query-tool-rss` (in your venv/bin or via pipx).
"""
import sys

print("[DEPRECATED] rss_scraper.py is a shim. Use 'yt-video-query-tool-rss' instead.")
try:
    from yt_video_query_tool.rss import main
except Exception:
    print("Failed to import package entrypoint. Install package with 'pip install -e .' or run the console script from .venv/bin/")
    raise

if __name__ == "__main__":
    main()
