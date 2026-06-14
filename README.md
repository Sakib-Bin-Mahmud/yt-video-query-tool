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

After installing (editable or normal), the package provides console scripts that are available on the environment `bin/`:

```bash
source .venv/bin/activate
# run the installed console scripts
yt-video-query-tool-api
yt-video-query-tool-rss
```

### Run without activating the venv

`run.sh` will prefer the venv-installed console scripts (or any installed console script on PATH):

```bash
bash run.sh                # runs yt-video-query-tool-api by default
bash run.sh yt-video-query-tool-rss  # run the RSS scraper instead
```

### Run via Makefile

```bash
make install
make run                  # runs yt-video-query-tool-api using .venv/bin/
make run-rss              # runs yt-video-query-tool-rss
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
