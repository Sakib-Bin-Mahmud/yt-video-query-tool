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

## Usage

```bash
# with venv active
python3 scraper.py

# or without activating venv
bash run.sh

# or via Makefile
make run
```

## Environment

Set your API key in one of these ways:

```bash
export YOUTUBE_API_KEY=your_api_key_here
```

or create a `.env` file:

```text
YOUTUBE_API_KEY=your_api_key_here
```
