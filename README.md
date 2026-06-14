# yt-video-query-tool

A small YouTube search scraper that uses `YOUTUBE_API_KEY` from the environment or a `.env` file.

## Setup

```bash
cd ~/pet_projects/youtube-search
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Alternatively, use the Makefile to set up the environment:

```bash
cd ~/pet_projects/youtube-search
make install
```

## Usage

### Run with the activated venv

```bash
source .venv/bin/activate
python3 api_scraper.py
```

### Run without activating the venv

`run.sh` will use `./.venv/bin/python` if the venv exists and will also load a local `.env` file:

```bash
bash run.sh
```

### Run via Makefile

```bash
make run
```

This means you do not need to keep the venv activated if you prefer not to.

## Environment

Set your API key in one of these ways:

```bash
export YOUTUBE_API_KEY=your_api_key_here
```

or create a `.env` file:

```text
YOUTUBE_API_KEY=your_api_key_here
```
