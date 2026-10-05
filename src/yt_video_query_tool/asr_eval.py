"""Compare ASR systems against hand-written (gold) Bangla transcripts.

Gold files: <gold-dir>/<video_id>.txt, a plain-text transcript of the first part
of the video. The first line may be a header giving how many seconds it covers:

    # end: 120
    আজ থেকে জ্বালানি তেলের দাম লিটারে ...

Without a header the whole video is assumed. Only hypothesis words (or segments,
if a transcript has no word timestamps) that start before `end` are scored.

Metrics per video and pooled per system:
  WER   word error rate (after normalisation)
  CER   character error rate (spaces ignored)
  NUM-P / NUM-R / NUM-F1  precision/recall/F1 over numeric tokens: digit strings
        (Bangla digits mapped to ASCII, thousands separators removed) and Bangla
        number words (দেড়, আড়াই, হাজার, ...). Multiset matching, order ignored.
        This is the metric that matters for claim verification: a one-digit
        error flips the verdict.

Usage:
  yt-video-query-tool-asr-eval --gold data/asr_gold \\
      --system large-v3=data/asr_bakeoff/large-v3/transcripts \\
      --system bn-medium=data/asr_bakeoff/bn-medium/transcripts [--csv out.csv]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

from .collect import BANGLA_SUFFIXES
from .transcribe import _NUMBER_WORDS

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_ZW = re.compile("[​‌‍﻿]")
# Keep Bangla letters/marks, ASCII letters, digits and %; everything else is a separator.
_NON_WORD = re.compile(r"[^ঀ-৿A-Za-z0-9%]+")
_DIGIT_RUN = re.compile(r"(\d+)")
_THOUSANDS = re.compile(r"(?<=\d)[,٬](?=\d)")
_NUM_WORD = re.compile(
    "^(" + "|".join(sorted(map(re.escape, _NUMBER_WORDS), key=len, reverse=True)) + ")"
    + "(" + "|".join(map(re.escape, BANGLA_SUFFIXES)) + ")?$"
)


def normalize(text: str) -> list[str]:
    t = unicodedata.normalize("NFC", text or "")
    t = _ZW.sub("", t).translate(_BN_DIGITS).lower()
    t = t.replace("৳", " টাকা ")
    t = _THOUSANDS.sub("", t)
    t = _DIGIT_RUN.sub(r" \1 ", t)          # "১৫টাকা" -> "15 টাকা"
    t = t.replace("৷", " ").replace("।", " ")  # Bangla currency sign / danda
    return [w for w in _NON_WORD.sub(" ", t).split() if w]


def numeric_tokens(words: list[str]) -> list[str]:
    out = []
    for w in words:
        if w.isdigit():
            out.append(w)
        else:
            m = _NUM_WORD.match(w)
            if m:
                out.append(m.group(1))       # strip suffix: "হাজারের" -> "হাজার"
    return out


def edit_distance(a, b) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def read_gold(path: Path) -> tuple[str, float | None]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    end = None
    if lines and lines[0].lstrip().startswith("#"):
        m = re.search(r"end\s*[:=]\s*([\d.]+)", lines[0])
        end = float(m.group(1)) if m else None
        lines = lines[1:]
    return "\n".join(lines), end


def hypothesis_text(transcript: dict, end: float | None) -> str:
    parts = []
    for seg in transcript.get("segments", []):
        words = seg.get("words") or []
        if words:
            parts += [w["word"] for w in words if end is None or w["start"] < end]
        elif end is None or seg["start"] < end:
            parts.append(seg["text"])
    return " ".join(parts)


def score(ref: str, hyp: str) -> dict:
    r, h = normalize(ref), normalize(hyp)
    rn, hn = Counter(numeric_tokens(r)), Counter(numeric_tokens(h))
    hit = sum((rn & hn).values())
    return {
        "ref_words": len(r), "word_errors": edit_distance(r, h),
        "ref_chars": len("".join(r)), "char_errors": edit_distance("".join(r), "".join(h)),
        "ref_nums": sum(rn.values()), "hyp_nums": sum(hn.values()), "num_hits": hit,
        "missed_nums": sorted((rn - hn).elements()), "spurious_nums": sorted((hn - rn).elements()),
    }


def rates(s: dict) -> dict:
    p = s["num_hits"] / s["hyp_nums"] if s["hyp_nums"] else 0.0
    r = s["num_hits"] / s["ref_nums"] if s["ref_nums"] else 0.0
    return {
        "WER": s["word_errors"] / max(s["ref_words"], 1),
        "CER": s["char_errors"] / max(s["ref_chars"], 1),
        "NUM-P": p, "NUM-R": r, "NUM-F1": 2 * p * r / (p + r) if p + r else 0.0,
    }


def evaluate(gold_dir: Path, systems: dict[str, Path]) -> tuple[list[dict], dict[str, dict]]:
    rows, pooled = [], {}
    golds = sorted(gold_dir.glob("*.txt"))
    if not golds:
        raise SystemExit(f"No gold transcripts (*.txt) in {gold_dir}")
    keys = ("ref_words", "word_errors", "ref_chars", "char_errors", "ref_nums", "hyp_nums", "num_hits")
    for name, tdir in systems.items():
        total = dict.fromkeys(keys, 0)
        n = 0
        for g in golds:
            tpath = tdir / f"{g.stem}.json"
            if not tpath.exists():
                print(f"  [{name}] no transcript for {g.stem}, skipped", file=sys.stderr)
                continue
            ref, end = read_gold(g)
            s = score(ref, hypothesis_text(json.loads(tpath.read_text(encoding="utf-8")), end))
            rows.append({"system": name, "video_id": g.stem, **{k: round(v, 4) for k, v in rates(s).items()},
                         "missed_nums": " ".join(s["missed_nums"]), "spurious_nums": " ".join(s["spurious_nums"])})
            for k in keys:
                total[k] += s[k]
            n += 1
        pooled[name] = {"videos": n, **rates(total)} if n else {"videos": 0}
    return rows, pooled


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="yt-video-query-tool-asr-eval", description=__doc__.splitlines()[0])
    p.add_argument("--gold", type=Path, required=True, help="folder of <video_id>.txt gold transcripts")
    p.add_argument("--system", action="append", required=True, metavar="NAME=TRANSCRIPT_DIR")
    p.add_argument("--csv", type=Path, help="write per-video scores here")
    args = p.parse_args(argv)

    systems = {}
    for spec in args.system:
        name, _, path = spec.partition("=")
        if not path:
            p.error(f"--system must be NAME=DIR, got {spec!r}")
        systems[name] = Path(path)

    rows, pooled = evaluate(args.gold, systems)
    print(f"\n{'system':<20}{'videos':>7}{'WER':>8}{'CER':>8}{'NUM-P':>8}{'NUM-R':>8}{'NUM-F1':>8}")
    for name, s in pooled.items():
        if s["videos"]:
            print(f"{name:<20}{s['videos']:>7}{s['WER']:>8.1%}{s['CER']:>8.1%}"
                  f"{s['NUM-P']:>8.1%}{s['NUM-R']:>8.1%}{s['NUM-F1']:>8.1%}")
    if args.csv:
        with args.csv.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["system"])
            w.writeheader()
            w.writerows(rows)
        print(f"\nPer-video scores: {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
