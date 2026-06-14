.PHONY: venv install run run-rss

VENV=.venv
SCRIPT?=api_scraper.py

venv:
	python3 -m venv $(VENV)

install: venv
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/pip install -r requirements.txt

run:
	$(VENV)/bin/python $(SCRIPT)

run-rss:
	$(VENV)/bin/python rss_scrapper.py
