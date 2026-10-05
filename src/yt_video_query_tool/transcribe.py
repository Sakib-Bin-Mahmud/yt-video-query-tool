"""ASR pilot: sample videos -> transient audio download -> Whisper transcript ->
candidate numeric claims for annotation.

Pipeline (all steps resumable):
  1. sample   stratified random sample of fuel-related videos per channel,
              balanced before/after an event date -> <out>/sample.csv
              (reused on later runs so the sample stays fixed; --resample to redraw)
  2. download best audio stream with yt-dlp into a temporary folder
  3. transcribe with faster-whisper (Bangla), keeping segment and word
              timestamps and confidences -> <out>/transcripts/<video_id>.json
              The audio is deleted immediately afterwards. Only transcripts are kept.
  4. report   segments that contain a number and a price/unit word ->
              <out>/candidates.csv (with empty annotation columns) and a
              per-channel summary printed and saved to <out>/summary.json

Install the optional dependencies first:  pip install -e ".[asr]"
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import statistics
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from .collect import KeywordMatcher

# Narrow fuel/energy-price terms used to pick the pilot sample (title only).
FUEL_TERMS = [
    "জ্বালানি", "তেল", "ডিজেল", "পেট্রোল", "অকটেন", "কেরোসিন", "এলপিজি", "পাম্প",
    "fuel", "diesel", "petrol", "octane", "kerosene", "lpg",
]

DEFAULT_CHANNELS = "News24,Channel 24,Independent Television,ATN Bangla News,BanglaVision NEWS"


def _nfc(s: str | None) -> str:
    return unicodedata.normalize("NFC", s or "")


# Numbers: digits (Bangla/ASCII) or Bangla number words as whole words. "নয়"
# (nine / "is not") is left out on purpose: it is mostly the negation.
_NUMBER_WORDS = [
    "এক", "দুই", "তিন", "চার", "পাঁচ", "ছয়", "সাত", "আট", "দশ", "এগারো", "বারো", "পনেরো",
    "বিশ", "কুড়ি", "পঁচিশ", "ত্রিশ", "চল্লিশ", "পঞ্চাশ", "ষাট", "সত্তর", "আশি", "নব্বই",
    "একশ", "একশো", "শত", "হাজার", "লাখ", "লক্ষ", "কোটি", "দেড়", "আড়াই", "সাড়ে",
    "অর্ধেক", "দ্বিগুণ", "তিনগুণ",
]
_NUMBER_MATCHER = KeywordMatcher(_NUMBER_WORDS)
_DIGIT = re.compile(r"[০-৯0-9]")
# Units and price words: substring match is fine (টাকায়, দামে, লিটারে ...).
_UNIT = re.compile(_nfc(r"টাকা|পয়সা|শতাংশ|%|পার্সেন্ট|লিটার|কেজি|ডলার|দাম|মূল্য|ভাগ|ইউনিট|মণ|হালি|টন|গুণ"))


def has_number(text: str) -> bool:
    text = _nfc(text)
    return bool(_DIGIT.search(text) or _NUMBER_MATCHER.match(text))


def is_candidate(text: str) -> bool:
    """Cheap recall-oriented filter: a number together with a price/unit word."""
    return has_number(text) and bool(_UNIT.search(_nfc(text)))


# --------------------------------------------------------------------------- #
# 1. Sampling
# --------------------------------------------------------------------------- #
def load_videos(videos_dir: Path) -> list[dict]:
    rows = []
    for path in sorted(videos_dir.glob("*.jsonl")):
        with path.open(encoding="utf-8") as f:
            rows.extend(json.loads(line) for line in f if line.strip())
    return rows


def draw_sample(videos, channels, start, end, event_date, per_channel, seed,
                min_duration, max_duration) -> list[dict]:
    fuel = KeywordMatcher(FUEL_TERMS)
    rng = random.Random(seed)
    sample = []
    for ch in channels:
        pool = [
            v for v in videos
            if v.get("channel_name") == ch
            and start <= (v.get("published_at") or "") < end
            and (v.get("live_broadcast") in (None, "none"))
            and v.get("duration_s") is not None and min_duration <= v["duration_s"] <= max_duration
            and fuel.match(v.get("title") or "")
        ]
        pool.sort(key=lambda v: v["video_id"])  # deterministic before shuffling
        before = [v for v in pool if v["published_at"] < event_date]
        after = [v for v in pool if v["published_at"] >= event_date]
        rng.shuffle(before)
        rng.shuffle(after)
        half = per_channel // 2
        take_b = min(len(before), max(half, per_channel - len(after)))
        picked = before[:take_b] + after[: per_channel - take_b]
        for v in picked:
            sample.append({
                "video_id": v["video_id"], "channel_name": ch, "published_at": v["published_at"],
                "period": "before" if v["published_at"] < event_date else "after",
                "duration_s": v["duration_s"], "title": v.get("title"), "url": v["url"],
            })
        print(f"  {ch}: pool {len(pool)} ({len(before)} before / {len(after)} after) -> sampled {len(picked)}")
    return sample


SAMPLE_FIELDS = ["video_id", "channel_name", "published_at", "period", "duration_s", "title", "url"]


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so Excel shows Bangla correctly.
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# --------------------------------------------------------------------------- #
# 2-3. Download + transcribe
# --------------------------------------------------------------------------- #
def download_audio(url: str, dest_dir: Path) -> Path:
    """Download the best audio-only stream (no ffmpeg needed)."""
    import yt_dlp

    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio",
        "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return Path(ydl.prepare_filename(info))


def resolve_compute_type(device: str, compute_type: str) -> str:
    """'auto' -> float16 on a CUDA GPU, int8 on CPU.

    Whisper weights are stored in float16, which CPUs cannot run efficiently;
    CTranslate2's own default then falls back to float32, the slowest option
    (about 6x real time for large-v3 in the pilot). int8 is typically 2-4x faster.
    """
    if compute_type != "auto":
        return compute_type
    if device == "cpu":
        return "int8"
    try:
        import ctranslate2
        has_cuda = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        has_cuda = False
    return "float16" if has_cuda and device in ("auto", "cuda") else "int8"


class WhisperTranscriber:
    def __init__(self, model: str, device: str, compute_type: str):
        from faster_whisper import WhisperModel

        compute_type = resolve_compute_type(device, compute_type)
        print(f"Loading {model} (device={device}, compute_type={compute_type})")
        self.model_name = model
        self.model = WhisperModel(model, device=device, compute_type=compute_type)

    def __call__(self, audio_path: Path) -> dict:
        segments, info = self.model.transcribe(
            str(audio_path), language="bn", beam_size=5, vad_filter=True, word_timestamps=True,
        )
        segs = []
        for s in segments:  # generator: decoding happens here
            segs.append({
                "start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip(),
                "avg_logprob": round(s.avg_logprob, 4), "no_speech_prob": round(s.no_speech_prob, 4),
                "words": [{"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word,
                           "probability": round(w.probability, 4)} for w in (s.words or [])],
            })
        return {"model": self.model_name, "language": info.language,
                "language_probability": round(info.language_probability, 4),
                "audio_duration_s": round(info.duration, 1), "segments": segs}


def transcribe_sample(sample, out: Path, transcriber_factory, download=download_audio) -> dict:
    tdir = out / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    todo = [s for s in sample if not (tdir / f"{s['video_id']}.json").exists()]
    print(f"Transcripts: {len(sample) - len(todo)} done, {len(todo)} to go")
    if not todo:
        return {"transcribed": 0, "errors": []}

    transcriber = transcriber_factory()
    errors, done = [], 0
    for i, s in enumerate(todo, 1):
        t0 = time.time()
        with tempfile.TemporaryDirectory(prefix="ytasr_") as tmp:  # audio removed on exit
            try:
                audio = download(s["url"], Path(tmp))
                result = transcriber(audio)
            except Exception as e:  # one bad video must not stop the pilot
                errors.append({"video_id": s["video_id"], "error": f"{type(e).__name__}: {e}"[:300]})
                print(f"  [{i}/{len(todo)}] {s['video_id']} FAILED: {type(e).__name__}")
                continue
        record = {**{k: s[k] for k in ("video_id", "channel_name", "published_at", "period", "title", "url")},
                  **result, "transcribed_at": datetime.now(timezone.utc).isoformat()}
        tmp_out = tdir / f"{s['video_id']}.json.tmp"
        tmp_out.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp_out.replace(tdir / f"{s['video_id']}.json")
        done += 1
        print(f"  [{i}/{len(todo)}] {s['channel_name']} {s['video_id']} "
              f"{result.get('audio_duration_s', 0):.0f}s audio in {time.time() - t0:.0f}s")
    return {"transcribed": done, "errors": errors}


# --------------------------------------------------------------------------- #
# 4. Candidate claims + summary
# --------------------------------------------------------------------------- #
CANDIDATE_FIELDS = [
    "video_id", "channel_name", "published_at", "period", "seg_index", "start", "end",
    "link", "prev_text", "text", "next_text", "avg_logprob", "min_word_prob",
    # annotation columns, filled by hand
    "is_numeric_claim", "verifiable", "commodity", "value", "unit", "direction",
    "reference_time", "verdict", "notes",
]


def build_candidates(tdir: Path) -> tuple[list[dict], dict]:
    rows, per_channel = [], {}
    for path in sorted(tdir.glob("*.json")):
        t = json.loads(path.read_text(encoding="utf-8"))
        segs = t.get("segments", [])
        n = 0
        for i, s in enumerate(segs):
            if not is_candidate(s["text"]):
                continue
            n += 1
            probs = [w["probability"] for w in s.get("words", [])]
            rows.append({
                "video_id": t["video_id"], "channel_name": t["channel_name"],
                "published_at": t["published_at"], "period": t.get("period"),
                "seg_index": i, "start": s["start"], "end": s["end"],
                "link": f"https://youtu.be/{t['video_id']}?t={int(s['start'])}",
                "prev_text": segs[i - 1]["text"] if i > 0 else "",
                "text": s["text"],
                "next_text": segs[i + 1]["text"] if i + 1 < len(segs) else "",
                "avg_logprob": s.get("avg_logprob"),
                "min_word_prob": min(probs) if probs else "",
            })
        ch = per_channel.setdefault(t["channel_name"], {"videos": 0, "audio_min": 0.0, "counts": []})
        ch["videos"] += 1
        ch["audio_min"] += (t.get("audio_duration_s") or 0) / 60
        ch["counts"].append(n)

    summary = {}
    all_counts = []
    for ch, d in sorted(per_channel.items()):
        c = d["counts"]
        all_counts += c
        summary[ch] = _stats(c) | {"audio_min": round(d["audio_min"], 1)}
    summary["ALL"] = _stats(all_counts) | {
        "audio_min": round(sum(d["audio_min"] for d in per_channel.values()), 1)}
    return rows, summary


def _stats(counts: list[int]) -> dict:
    if not counts:
        return {"videos": 0}
    return {
        "videos": len(counts),
        "candidates": sum(counts),
        "mean_per_video": round(statistics.mean(counts), 2),
        "median_per_video": statistics.median(counts),
        "share_videos_ge5": round(sum(c >= 5 for c in counts) / len(counts), 2),
    }


def print_summary(summary: dict) -> None:
    print("\nCandidate numeric segments (number + price/unit word). These are candidates, not verified claims:")
    print(f"  {'channel':<24}{'videos':>7}{'audio min':>10}{'cands':>7}{'mean':>7}{'median':>8}{'>=5':>6}")
    for ch, s in summary.items():
        if not s.get("videos"):
            continue
        print(f"  {ch:<24}{s['videos']:>7}{s['audio_min']:>10}{s['candidates']:>7}"
              f"{s['mean_per_video']:>7}{s['median_per_video']:>8}{s['share_videos_ge5']:>6.0%}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="yt-video-query-tool-transcribe", description=__doc__.splitlines()[0])
    p.add_argument("--videos-dir", type=Path, default=Path("data/videos"))
    p.add_argument("--out", type=Path, default=Path("data/asr"))
    p.add_argument("--channels", default=DEFAULT_CHANNELS, help="comma-separated channel names")
    p.add_argument("--start", default="2026-04-12", help="inclusive date")
    p.add_argument("--end", default="2026-04-27", help="exclusive date")
    p.add_argument("--event-date", default="2026-04-19", help="sample is balanced before/after this")
    p.add_argument("--per-channel", type=int, default=10)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--min-duration", type=int, default=30, help="seconds")
    p.add_argument("--max-duration", type=int, default=900, help="seconds; skips long talk shows")
    p.add_argument("--model", default="large-v3", help="faster-whisper model name or local path")
    p.add_argument("--device", default="auto", help="auto, cpu or cuda")
    p.add_argument("--compute-type", default="auto", help="e.g. float16 (GPU), int8 (CPU)")
    p.add_argument("--resample", action="store_true", help="redraw the sample even if sample.csv exists")
    p.add_argument("--sample-file", type=Path,
                   help="use this CSV (needs video_id, url; other sample columns optional) instead of drawing a sample, "
                        "e.g. the same gold videos for every model in an ASR comparison")
    p.add_argument("--sample-only", action="store_true", help="draw/show the sample, then stop")
    p.add_argument("--report-only", action="store_true", help="skip download/ASR, rebuild candidates from transcripts")
    return p


def main(argv=None, transcriber_factory=None, download=download_audio) -> int:
    args = build_parser().parse_args(argv)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    sample_path = out / "sample.csv"

    if args.report_only:
        sample = []
    elif args.sample_file:
        sample = [{**{k: "" for k in SAMPLE_FIELDS}, **r} for r in read_csv(args.sample_file)]
        for r in sample:
            r["url"] = r["url"] or f"https://www.youtube.com/watch?v={r['video_id']}"
        print(f"Using sample file: {len(sample)} videos ({args.sample_file})")
    elif sample_path.exists() and not args.resample:
        sample = read_csv(sample_path)
        print(f"Using existing sample: {len(sample)} videos ({sample_path})")
    else:
        channels = [c.strip() for c in args.channels.split(",") if c.strip()]
        videos = load_videos(args.videos_dir)
        print(f"Drawing sample from {len(videos)} videos")
        sample = draw_sample(videos, channels, args.start, args.end, args.event_date,
                             args.per_channel, args.seed, args.min_duration, args.max_duration)
        write_csv(sample_path, sample, SAMPLE_FIELDS)
        print(f"Sample: {len(sample)} videos, "
              f"{sum(int(s['duration_s']) for s in sample) / 60:.0f} min of audio -> {sample_path}")
    if args.sample_only:
        return 0

    log = {"started_at": datetime.now(timezone.utc).isoformat()}
    if sample:
        factory = transcriber_factory or (lambda: WhisperTranscriber(args.model, args.device, args.compute_type))
        log |= transcribe_sample(sample, out, factory, download)
        if log["errors"]:
            print(f"  {len(log['errors'])} video(s) failed (see {out / 'asr_log.json'}); a re-run retries them")

    rows, summary = build_candidates(out / "transcripts")
    write_csv(out / "candidates.csv", rows, CANDIDATE_FIELDS)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log["finished_at"] = datetime.now(timezone.utc).isoformat()
    (out / "asr_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print_summary(summary)
    print(f"\nAnnotate: {out / 'candidates.csv'} ({len(rows)} rows)")
    return 1 if log.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
