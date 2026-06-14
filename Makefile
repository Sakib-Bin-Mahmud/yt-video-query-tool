.PHONY: venv install run run-rss

VENV=.venv
# default console script name (executable in venv/bin/)
SCRIPT?=yt-video-query-tool-api

venv:
	python3 -m venv $(VENV)

install: venv
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/pip install -r requirements.txt

run:
	$(VENV)/bin/$(SCRIPT)

run-rss:
	$(VENV)/bin/yt-video-query-tool-rss
