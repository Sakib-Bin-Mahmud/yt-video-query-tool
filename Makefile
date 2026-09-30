.PHONY: venv install run run-rss collect test

VENV=.venv
# default console script name (executable in venv/bin/)
SCRIPT?=yt-video-query-tool-api

venv:
	python3 -m venv $(VENV)

install: venv
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/pip install -e '.[dev]'

run:
	$(VENV)/bin/$(SCRIPT)

run-rss:
	$(VENV)/bin/yt-video-query-tool-rss

# e.g. make collect ARGS="--start 2026-02-18 --end 2026-06-19 --only 'Jamuna TV' --comments"
collect:
	$(VENV)/bin/yt-video-query-tool-collect $(ARGS)

test:
	$(VENV)/bin/python -m pytest -q
