"""Research-grade multi-channel YouTube collector.

Enumerates every upload of each channel in a date window via the channel's
uploads playlist (cheap and complete, unlike search.list), enriches each video
with statistics/duration/thumbnail, flags topic keyword matches, and optionally
fetches comments and thumbnails for matched videos.

Output layout (under --out):
    videos/<channel_id>.jsonl     one row per video in the window (ALL videos,
                                  with is_match / matched_terms flags, so recall
                                  of the keyword filter can be measured later)
    comments/<video_id>.jsonl     top-level comments + replies (matched videos)
    thumbnails/<video_id>.jpg     best available thumbnail (matched videos)
    run_log.json                  per-channel counts and estimated quota used

Channels whose videos file already exists are skipped, so an interrupted run
(e.g. quota exhausted) can be resumed by re-running the same command.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

API_BASE = "https://www.googleapis.com/youtube/v3"

# Default topic terms for economic/price coverage (Bangla + English).
# Broad on purpose: every video is saved anyway and flagged, so precision is
# handled downstream. Latin terms are matched on word boundaries; Bangla terms
# as substrings (Bangla inflects by suffix: তেল -> তেলের).
DEFAULT_KEYWORDS = [
    # fuel & energy
    "জ্বালানি", "তেল", "ডিজেল", "পেট্রোল", "অকটেন", "কেরোসিন", "এলপিজি", "গ্যাস",
    # prices & markets
    "দাম", "মূল্য", "বাজার", "মূল্যস্ফীতি", "সিন্ডিকেট", "মজুত", "সংকট",
    # essentials
    "চাল", "পেঁয়াজ", "ডিম", "সয়াবিন", "ভোজ্য তেল", "চিনি", "আলু", "ডাল", "মুরগি",
    # money
    "ডলার", "টাকার মান", "বাজেট",
    # English
    "fuel", "oil", "diesel", "petrol", "octane", "lpg", "price", "prices",
    "inflation", "market", "dollar", "crisis",
]

# Estimated quota cost per call (YouTube Data API v3).
QUOTA_COST = {"channels": 1, "playlistItems": 1, "videos": 1, "commentThreads": 1}


class QuotaExceeded(RuntimeError):
    pass


class ApiError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #
@dataclass
class YouTubeClient:
    api_key: str
    session: requests.Session = field(default_factory=lambda: requests.Session())
    max_retries: int = 4
    quota_used: int = 0

    def get(self, endpoint: str, **params) -> dict:
        params["key"] = self.api_key
        url = f"{API_BASE}/{endpoint}"
        for attempt in range(self.max_retries + 1):
            resp = self.session.get(url, params=params, timeout=30)
            self.quota_used += QUOTA_COST.get(endpoint, 1)
            if resp.status_code == 200:
                return resp.json()

            reason = _error_reason(resp)
            if reason in ("quotaExceeded", "dailyLimitExceeded"):
                raise QuotaExceeded(reason)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                time.sleep(2 ** attempt)
                continue
            # Never include the URL: it contains the API key.
            raise ApiError(f"{endpoint}: HTTP {resp.status_code} ({reason or 'unknown'})")
        raise ApiError(f"{endpoint}: retries exhausted")


def _error_reason(resp) -> str | None:
    try:
        errors = resp.json().get("error", {}).get("errors", [])
        return errors[0].get("reason") if errors else None
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def parse_date(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


_DURATION_RE = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def duration_seconds(iso: str | None) -> int | None:
    if not iso:
        return None
    m = _DURATION_RE.fullmatch(iso)
    if not m:
        return None
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


class KeywordMatcher:
    def __init__(self, keywords: list[str]):
        self.bangla, self.latin = [], []
        for kw in keywords:
            kw = unicodedata.normalize("NFC", kw.strip())
            if not kw:
                continue
            if re.search(r"[A-Za-z]", kw):
                self.latin.append((kw, re.compile(rf"\b{re.escape(kw.lower())}\b")))
            else:
                self.bangla.append(kw)

    def match(self, text: str) -> list[str]:
        text = unicodedata.normalize("NFC", text or "")
        lower = text.lower()
        hits = [kw for kw in self.bangla if kw in text]
        hits += [kw for kw, rx in self.latin if rx.search(lower)]
        return hits


def load_channels(path: Path, only: set[str] | None) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("channel_id", "").strip()]
    for r in rows:
        cid = r["channel_id"].strip()
        if not (len(cid) == 24 and cid.startswith("UC")):
            raise SystemExit(f"Invalid channel_id for {r.get('name')!r}: {cid!r} (expected 24 chars starting with UC)")
        r["channel_id"] = cid
    if only:
        rows = [r for r in rows if r["channel_id"] in only or r.get("name") in only]
    return rows


def load_keywords(path: Path | None) -> list[str]:
    if not path:
        return DEFAULT_KEYWORDS
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)  # atomic: a half-written file never looks "done"


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


# --------------------------------------------------------------------------- #
# Collection steps
# --------------------------------------------------------------------------- #
def uploads_playlist_id(client: YouTubeClient, channel_id: str) -> str:
    data = client.get("channels", part="contentDetails", id=channel_id)
    items = data.get("items") or []
    if not items:
        raise ApiError(f"channel not found: {channel_id}")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def list_video_ids(client, playlist_id, start: datetime, end: datetime, stop_after_old: int = 100):
    """Yield (video_id, published_at) in [start, end).

    The uploads playlist is newest-first but not strictly (premieres, scheduled
    uploads), so we stop only after `stop_after_old` consecutive items older
    than `start` instead of at the first one.
    """
    page_token, old_streak = None, 0
    while True:
        params = dict(part="contentDetails", playlistId=playlist_id, maxResults=50)
        if page_token:
            params["pageToken"] = page_token
        data = client.get("playlistItems", **params)
        for item in data.get("items", []):
            cd = item["contentDetails"]
            published = cd.get("videoPublishedAt")
            if not published:  # private/deleted
                continue
            ts = parse_date(published)
            if ts < start:
                old_streak += 1
                if old_streak >= stop_after_old:
                    return
                continue
            old_streak = 0
            if ts < end:
                yield cd["videoId"], published
        page_token = data.get("nextPageToken")
        if not page_token:
            return


def fetch_video_details(client, video_ids: list[str]) -> dict[str, dict]:
    out = {}
    for batch in chunks(video_ids, 50):
        data = client.get("videos", part="snippet,statistics,contentDetails", id=",".join(batch))
        for v in data.get("items", []):
            out[v["id"]] = v
    return out


def best_thumbnail(thumbs: dict) -> str | None:
    for key in ("maxres", "standard", "high", "medium", "default"):
        if key in thumbs:
            return thumbs[key]["url"]
    return None


def build_row(channel: dict, video: dict, matcher: KeywordMatcher, collected_at: str) -> dict:
    sn, st, cd = video["snippet"], video.get("statistics", {}), video.get("contentDetails", {})
    text = " ".join([sn.get("title", ""), sn.get("description", ""), " ".join(sn.get("tags", []))])
    matched = matcher.match(text)
    as_int = lambda k: int(st[k]) if k in st else None
    return {
        "channel_id": channel["channel_id"],
        "channel_name": channel.get("name"),
        "video_id": video["id"],
        "url": f"https://www.youtube.com/watch?v={video['id']}",
        "published_at": sn.get("publishedAt"),
        "title": sn.get("title"),
        "description": sn.get("description"),
        "tags": sn.get("tags", []),
        "default_audio_language": sn.get("defaultAudioLanguage"),
        "live_broadcast": sn.get("liveBroadcastContent"),
        "duration_iso": cd.get("duration"),
        "duration_s": duration_seconds(cd.get("duration")),
        "view_count": as_int("viewCount"),
        "like_count": as_int("likeCount"),
        "comment_count": as_int("commentCount"),
        "thumbnail_url": best_thumbnail(sn.get("thumbnails", {})),
        "matched_terms": matched,
        "is_match": bool(matched),
        "collected_at": collected_at,
    }


def fetch_comments(client, video_id: str, max_comments: int) -> list[dict]:
    rows, page_token = [], None
    while len(rows) < max_comments:
        params = dict(part="snippet,replies", videoId=video_id, maxResults=100,
                      order="time", textFormat="plainText")
        if page_token:
            params["pageToken"] = page_token
        try:
            data = client.get("commentThreads", **params)
        except ApiError as e:
            if "403" in str(e) or "404" in str(e):  # comments disabled / video gone
                return rows
            raise
        for th in data.get("items", []):
            top = th["snippet"]["topLevelComment"]
            rows.append(_comment_row(video_id, top, parent_id=None))
            for rep in th.get("replies", {}).get("comments", []):
                rows.append(_comment_row(video_id, rep, parent_id=top["id"]))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return rows[:max_comments]


def _comment_row(video_id, c, parent_id):
    s = c["snippet"]
    return {
        "video_id": video_id,
        "comment_id": c["id"],
        "parent_id": parent_id,
        "published_at": s.get("publishedAt"),
        "updated_at": s.get("updatedAt"),
        "text": s.get("textOriginal") or s.get("textDisplay"),
        "like_count": s.get("likeCount"),
        # Author identity deliberately not stored (privacy); keep a stable hash only.
        "author_hash": _hash(s.get("authorChannelId", {}).get("value", "")),
    }


def _hash(value: str) -> str | None:
    if not value:
        return None
    import hashlib
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def download_thumbnail(session, url: str, dest: Path) -> None:
    if dest.exists() or not url:
        return
    resp = session.get(url, timeout=30)
    if resp.status_code == 200:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def collect_channel(client, channel, start, end, matcher, out: Path, args) -> dict:
    videos_path = out / "videos" / f"{channel['channel_id']}.jsonl"
    if videos_path.exists() and not args.force:
        rows = [json.loads(l) for l in videos_path.open(encoding="utf-8")]
        print(f"  [skip] {channel['name']}: {len(rows)} videos already collected")
    else:
        playlist = uploads_playlist_id(client, channel["channel_id"])
        ids = [vid for vid, _ in list_video_ids(client, playlist, start, end)]
        details = fetch_video_details(client, ids)
        now = datetime.now(timezone.utc).isoformat()
        rows = [build_row(channel, details[i], matcher, now) for i in ids if i in details]
        rows.sort(key=lambda r: r["published_at"] or "")
        write_jsonl(videos_path, rows)

    matched = [r for r in rows if r["is_match"]]
    print(f"  {channel['name']}: {len(rows)} videos in window, {len(matched)} keyword matches")

    n_comments = 0
    for r in matched:
        if args.comments:
            cpath = out / "comments" / f"{r['video_id']}.jsonl"
            if not cpath.exists() or args.force:
                comments = fetch_comments(client, r["video_id"], args.max_comments)
                write_jsonl(cpath, comments)
                n_comments += len(comments)
        if args.thumbnails:
            download_thumbnail(client.session, r["thumbnail_url"],
                               out / "thumbnails" / f"{r['video_id']}.jpg")

    return {"videos": len(rows), "matched": len(matched), "new_comments": n_comments}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="yt-video-query-tool-collect",
        description="Collect all uploads of several YouTube channels in a date window.",
    )
    p.add_argument("--channels", type=Path, default=Path("channels.csv"),
                   help="CSV with columns name,channel_id[,handle] (default: channels.csv)")
    p.add_argument("--start", required=True, help="inclusive, e.g. 2026-02-18")
    p.add_argument("--end", required=True, help="exclusive, e.g. 2026-06-19")
    p.add_argument("--out", type=Path, default=Path("data"), help="output directory")
    p.add_argument("--keywords", type=Path, help="text file, one term per line (default: built-in list)")
    p.add_argument("--only", help="comma-separated channel names or IDs to restrict to")
    p.add_argument("--comments", action="store_true", help="fetch comments for matched videos")
    p.add_argument("--max-comments", type=int, default=500, help="per video (default 500)")
    p.add_argument("--thumbnails", action="store_true", help="download thumbnails for matched videos")
    p.add_argument("--force", action="store_true", help="re-collect even if output exists")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        print("YOUTUBE_API_KEY is not set (export it or put it in .env).", file=sys.stderr)
        return 2

    start, end = parse_date(args.start), parse_date(args.end)
    if start >= end:
        print("--start must be before --end", file=sys.stderr)
        return 2

    only = {s.strip() for s in args.only.split(",")} if args.only else None
    channels = load_channels(args.channels, only)
    matcher = KeywordMatcher(load_keywords(args.keywords))
    client = YouTubeClient(api_key)
    args.out.mkdir(parents=True, exist_ok=True)

    print(f"Window {start.date()} -> {end.date()} (end exclusive), {len(channels)} channel(s)")
    log = {"started_at": datetime.now(timezone.utc).isoformat(), "start": args.start,
           "end": args.end, "channels": {}}
    status = 0
    for ch in channels:
        try:
            log["channels"][ch["channel_id"]] = {"name": ch["name"], **collect_channel(
                client, ch, start, end, matcher, args.out, args)}
        except QuotaExceeded:
            print("\nDaily API quota exhausted. Progress is saved; re-run the same "
                  "command tomorrow to resume.", file=sys.stderr)
            status = 3
            break
        except ApiError as e:
            print(f"  [error] {ch['name']}: {e}", file=sys.stderr)
            log["channels"][ch["channel_id"]] = {"name": ch["name"], "error": str(e)}
            status = 1

    log["estimated_quota_used"] = client.quota_used
    log["finished_at"] = datetime.now(timezone.utc).isoformat()
    (args.out / "run_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nEstimated quota used this run: {client.quota_used} units")
    return status


if __name__ == "__main__":
    sys.exit(main())
