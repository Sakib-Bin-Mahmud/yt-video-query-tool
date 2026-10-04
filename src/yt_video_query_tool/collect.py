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
    def __init__(self, message: str, status: int | None = None, reason: str | None = None):
        super().__init__(message)
        self.status = status
        self.reason = reason


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
            raise ApiError(f"{endpoint}: HTTP {resp.status_code} ({reason or 'unknown'})",
                           status=resp.status_code, reason=reason)
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


# Bangla letters/marks plus ZWNJ/ZWJ: a keyword must not be glued to more of these.
_BN = "ঀ-৿‌‍"

# Inflectional suffixes allowed after a Bangla keyword (case markers, classifiers,
# plurals, emphatics). Anything else glued on (e.g. চাল + িয়ে = চালিয়ে "driving")
# is treated as a different word. Longest first so the regex prefers full suffixes.
BANGLA_SUFFIXES = sorted([
    "ের", "এর", "র", "ে", "য়", "য়ে", "য়ের", "তে", "কে", "ও", "ই", "েই", "েও", "েরও", "েরই",
    "টা", "টি", "টির", "টার", "টাই", "গুলো", "গুলোর", "গুলোতে", "গুলোকে", "সহ",
    "রা", "দের", "দেরও", "বাবদ",
], key=len, reverse=True)


class KeywordMatcher:
    """Match topic terms as whole words.

    Latin terms use \\b boundaries. Bangla terms must start at a word boundary
    and may only be followed by an allowed inflectional suffix.
    """

    def __init__(self, keywords: list[str], suffixes: list[str] = BANGLA_SUFFIXES):
        suffix_alt = "|".join(re.escape(s) for s in suffixes)
        self.patterns = []
        for kw in keywords:
            kw = unicodedata.normalize("NFC", kw.strip())
            if not kw:
                continue
            if re.search(r"[A-Za-z]", kw):
                rx = re.compile(rf"\b{re.escape(kw.lower())}\b")
                self.patterns.append((kw, rx, True))
            else:
                rx = re.compile(rf"(?<![{_BN}]){re.escape(kw)}(?:{suffix_alt})?(?![{_BN}])")
                self.patterns.append((kw, rx, False))

    def match(self, text: str) -> list[str]:
        text = unicodedata.normalize("NFC", text or "")
        lower = text.lower()
        return [kw for kw, rx, latin in self.patterns if rx.search(lower if latin else text)]


def boilerplate_lines(descriptions: list[str], min_share: float = 0.05, min_count: int = 5) -> set[str]:
    """Lines repeated across many of a channel's descriptions (SEO footers,
    social links, hashtag blocks). Excluded from keyword matching."""
    counts: dict[str, int] = {}
    for d in descriptions:
        for line in {ln.strip() for ln in (d or "").splitlines() if ln.strip()}:
            counts[line] = counts.get(line, 0) + 1
    threshold = max(min_count, min_share * len(descriptions))
    return {line for line, n in counts.items() if n >= threshold}


def strip_boilerplate(description: str, boilerplate: set[str]) -> str:
    return "\n".join(ln for ln in (description or "").splitlines() if ln.strip() not in boilerplate)


def flag_matches(rows: list[dict], matcher: KeywordMatcher) -> None:
    """(Re)compute match flags in place on title + de-boilerplated description.

    Tags are stored but not matched: channels stuff them with generic SEO terms.
    """
    boiler = boilerplate_lines([r.get("description") or "" for r in rows])
    for r in rows:
        title_terms = matcher.match(r.get("title") or "")
        desc_terms = matcher.match(strip_boilerplate(r.get("description") or "", boiler))
        r["title_terms"] = title_terms
        r["description_terms"] = desc_terms
        r["matched_terms"] = sorted(set(title_terms) | set(desc_terms))
        r["is_match"] = bool(r["matched_terms"])


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


def list_video_ids(client, playlist_id, start: datetime, end: datetime,
                   stop_after_old: int = 100) -> tuple[list[tuple[str, str]], bool]:
    """Return ([(video_id, published_at) in [start, end)], reached_start).

    The uploads playlist is newest-first but not strictly (premieres, scheduled
    uploads), so we stop only after `stop_after_old` consecutive items older
    than `start` instead of at the first one.

    reached_start is False when the playlist ran out before any upload older
    than `start` appeared: the API only exposes a channel's most recent
    uploads, so for busy channels the early part of the window is missing.
    """
    out, page_token, old_streak, reached_start = [], None, 0, False
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
                reached_start = True
                old_streak += 1
                if old_streak >= stop_after_old:
                    return out, reached_start
                continue
            old_streak = 0
            if ts < end:
                out.append((cd["videoId"], published))
        page_token = data.get("nextPageToken")
        if not page_token:
            return out, reached_start


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


def build_row(channel: dict, video: dict, collected_at: str) -> dict:
    sn, st, cd = video["snippet"], video.get("statistics", {}), video.get("contentDetails", {})
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
        "collected_at": collected_at,
    }


def fetch_comments(client, video_id: str, max_comments: int) -> list[dict]:
    """Fetch comments + replies. Raises ApiError on failure; the caller decides
    whether to skip the video. On a 400 processingFailure (seen on some videos
    with order=time) it retries once with the default relevance order."""
    rows, page_token, order = [], None, "time"
    while len(rows) < max_comments:
        params = dict(part="snippet,replies", videoId=video_id, maxResults=100,
                      order=order, textFormat="plainText")
        if page_token:
            params["pageToken"] = page_token
        try:
            data = client.get("commentThreads", **params)
        except ApiError as e:
            if e.reason == "processingFailure" and order == "time" and not rows:
                order = "relevance"
                continue
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
    meta_path = out / "videos" / f"{channel['channel_id']}.meta.json"
    if videos_path.exists() and not args.force:
        rows = [json.loads(l) for l in videos_path.open(encoding="utf-8")]
        if meta_path.exists():
            reached_start = json.loads(meta_path.read_text(encoding="utf-8"))["reached_start"]
        else:  # data from an older version: infer from the earliest upload
            earliest = min((parse_date(r["published_at"]) for r in rows if r.get("published_at")), default=None)
            reached_start = earliest is not None and (earliest - start).total_seconds() < 86400
        print(f"  [resume] {channel['name']}: {len(rows)} videos on disk, re-flagging matches (no API calls)")
    else:
        playlist = uploads_playlist_id(client, channel["channel_id"])
        listed, reached_start = list_video_ids(client, playlist, start, end)
        ids = [vid for vid, _ in listed]
        details = fetch_video_details(client, ids)
        now = datetime.now(timezone.utc).isoformat()
        rows = [build_row(channel, details[i], now) for i in ids if i in details]
        rows.sort(key=lambda r: r["published_at"] or "")

    # Matching is cheap and deterministic, so it is redone on every run: changing
    # the keyword list or matcher never needs new API calls.
    flag_matches(rows, matcher)
    write_jsonl(videos_path, rows)
    coverage_start = rows[0]["published_at"] if rows else None
    meta_path.write_text(json.dumps({"reached_start": reached_start, "coverage_start": coverage_start,
                                     "window_start": start.isoformat()}, indent=2), encoding="utf-8")

    matched = [r for r in rows if r["is_match"]]
    print(f"  {channel['name']}: {len(rows)} videos in window, {len(matched)} keyword matches "
          f"({len(matched) / max(len(rows), 1):.0%})")
    if not reached_start:
        print(f"  [warning] {channel['name']}: history truncated, API uploads list starts at "
              f"{coverage_start}, after --start. The early part of the window is missing.")

    n_comments, comment_errors = 0, []
    for r in matched:
        if args.comments:
            cpath = out / "comments" / f"{r['video_id']}.jsonl"
            if not cpath.exists() or args.force:
                try:
                    comments = fetch_comments(client, r["video_id"], args.max_comments)
                except ApiError as e:
                    # One bad video (comments disabled, deleted, processingFailure)
                    # must not stop the channel. No file is written, so a later
                    # run retries it.
                    comment_errors.append({"video_id": r["video_id"], "status": e.status, "reason": e.reason})
                else:
                    write_jsonl(cpath, comments)
                    n_comments += len(comments)
        if args.thumbnails:
            download_thumbnail(client.session, r["thumbnail_url"],
                               out / "thumbnails" / f"{r['video_id']}.jpg")

    if comment_errors:
        print(f"  {len(comment_errors)} video(s) skipped for comments (see run_log.json)")
    return {"videos": len(rows), "matched": len(matched), "new_comments": n_comments,
            "reached_start": reached_start, "coverage_start": coverage_start,
            "comment_errors": comment_errors}


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
