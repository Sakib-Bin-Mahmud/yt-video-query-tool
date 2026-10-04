# yt-video-query-tool

Tools for collecting YouTube videos from news channels via the YouTube Data API v3.
The main tool is the **multi-channel research collector** (`yt-video-query-tool-collect`),
built for the Bangla broadcast-news claim-verification project. The original single-channel
API and RSS scripts are still included.

## Setup

```bash
git clone https://github.com/Sakib-Bin-Mahmud/yt-video-query-tool
cd yt-video-query-tool
make install          # creates .venv and installs the package (editable) + pytest
cp .env.example .env  # then put your API key in .env
```

Never commit `.env` or paste the key into notes or code. If a key has been exposed, regenerate it
in Google Cloud Console → APIs & Services → Credentials, and restrict it to YouTube Data API v3.

## Research collector

```bash
.venv/bin/yt-video-query-tool-collect \
    --start 2026-02-18 --end 2026-06-19 \
    --only "Jamuna TV,Somoy TV,News24" \
    --comments --thumbnails
```

or `make collect ARGS="--start 2026-02-18 --end 2026-06-19 --comments"`.

| Option | Meaning |
|---|---|
| `--channels` | CSV with `name,channel_id[,handle]` (default `channels.csv`, the top 10 Bangla news channels) |
| `--start` / `--end` | Date window, start inclusive, end exclusive (UTC) |
| `--only` | Restrict to some channels (names or IDs, comma-separated) |
| `--keywords` | Topic terms file, one per line (default: built-in Bangla + English fuel/price/market terms) |
| `--comments` / `--max-comments` | Fetch comments + replies for keyword-matched videos (default max 500 each) |
| `--thumbnails` | Download the best thumbnail for keyword-matched videos |
| `--force` | Re-collect even if output already exists |

**How it works**

- Enumerates *every* upload through each channel's uploads playlist. It doesn't use `search.list`,
  which costs 100 quota units per call and silently misses videos.
- Saves **all** videos in the window with `is_match` / `matched_terms` flags, so you can measure the
  keyword filter's recall later.
- Matches on the **title and the description**. Lines repeated across many of a channel's
  descriptions (SEO footers such as "Gas and oil, Electricity industry…", social links, hashtag
  blocks) are excluded, and tags are not matched. Bangla terms match as whole words with
  inflectional suffixes (`চালের` matches `চাল`; `চালিয়ে` does not). `title_terms` and
  `description_terms` are saved separately so you can use a stricter title-only filter.
- Re-applies matching on every run from the data already on disk, so changing the keyword list or
  matcher costs no quota.
- Flags **truncated history**. If a channel's uploads list ends before `--start`, it prints a
  warning and records `reached_start: false` and the actual `coverage_start` in `run_log.json`
  and `videos/<channel_id>.meta.json`.
- Comment failures on a single video (comments disabled, deleted video, `processingFailure`) are
  logged under `comment_errors` and skipped. A later run retries them.
- Stores comment authors only as a truncated SHA-256 hash, never names or channel IDs.
- Is resumable: channels and comment files already on disk are skipped. If the daily quota runs out
  it stops cleanly (exit code 3), so you can re-run the same command the next day.

**Output** (in `data/`, git-ignored):

```
data/videos/<channel_id>.jsonl   metadata, stats, duration, thumbnail URL, match flags
data/comments/<video_id>.jsonl   comments + replies (matched videos)
data/thumbnails/<video_id>.jpg
data/run_log.json                per-channel counts, estimated quota used
```

**Quota:** the default daily quota is 10,000 units. Listing and enrichment cost about 1 unit per
50 videos, and comments cost at least 1 unit per matched video. The playlist is read newest-first,
so the collector pages through everything uploaded *since* `--start`, not just the window.
Bangla news channels upload very heavily (often 100+ videos per day), so a 10-channel run can
take several days of quota. Start with `--only` on 2–3 channels, check `run_log.json`, and let
the resume logic spread the work across days. Use a narrower `--keywords` file before turning on
`--comments` at scale.

**Older windows (e.g. the 2022 fuel hike):** the uploads playlist reportedly exposes only a
channel's most recent ~20,000 uploads. For a busy channel that may cover only a few months back.
Check how far back each channel reaches before relying on it; windows beyond that need
date-sliced `search.list` queries, which are quota-expensive and incomplete.

**Transcripts** are not available through the API for other people's videos (`captions.download`
requires ownership). They need a separate ASR step. Check YouTube's Terms of Service on
downloading audio, or apply to the YouTube Researcher Program.

## Legacy scripts

```bash
.venv/bin/yt-video-query-tool-api   # single channel, prints matches (config at top of api.py)
.venv/bin/yt-video-query-tool-rss   # RSS feed: only the ~15 most recent videos
bash run.sh [console-script]        # runs from .venv without activating it
```

## Tests

```bash
make test
```
