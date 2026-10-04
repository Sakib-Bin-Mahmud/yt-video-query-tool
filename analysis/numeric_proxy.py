"""Title-level numeric claim proxy, 5-18 Apr vs 19 Apr-2 May 2026.

Counts title-matched videos whose title holds a number (Bangla or ASCII digits,
or দেড়/আড়াই/সাড়ে/দ্বিগুণ) together with a unit/price word. Titles undercount
spoken claims, so this is only a cheap, no-quota preview.

Usage: python analysis/numeric_proxy.py data/videos/<channel_id>.jsonl [...]
"""
import json
import re
import sys
import unicodedata


def nfc(s: str | None) -> str:
    return unicodedata.normalize("NFC", s or "")


NUMBER = re.compile(nfc(r"[০-৯0-9]|দেড়|আড়াই|সাড়ে|দ্বিগুণ"))
UNIT = re.compile(nfc(r"টাকা|শতাংশ|%|লিটার|কেজি|ডলার|দাম|মূল্য"))
# Digits that are not claims: channel/show branding ("NEWS24", "24 Ghonta"),
# dated show stamps ("3 June 2026") and episode/season tags ("EP 16", "পর্ব ১২").
NOISE = re.compile(nfc(
    r"news\s*24|channel\s*24|24\s*ghonta|২৪\s*ঘণ্টা"
    r"|\b\d{1,2}\s+(?:january|february|march|april|may|june|july|august|september"
    r"|october|november|december)\s+\d{4}"
    r"|\b(?:ep(?:isode)?|season|part)\.?\s*[-:]?\s*[০-৯0-9]+"
    r"|(?:পর্ব|সিজন|সিজেন)\s*[-:]?\s*[০-৯0-9]+"
), re.I)
WINDOWS = [("5-18 Apr", "2026-04-05", "2026-04-19"), ("19 Apr-2 May", "2026-04-19", "2026-05-03")]
MAX_EXAMPLES = 20


def claim(title: str | None) -> bool:
    t = NOISE.sub(" ", nfc(title))
    return bool(NUMBER.search(t) and UNIT.search(t))


def report(path: str) -> None:
    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    if not rows:
        print(f"\n=== {path}: no videos")
        return
    titled = [r for r in rows if r.get("title_terms")]
    print(f"\n=== {rows[0]['channel_name']} ({len(titled)} title matches in whole window)")
    examples = []
    for label, lo, hi in WINDOWS:
        win = [r for r in titled if lo <= (r["published_at"] or "") < hi]
        hits = [r for r in win if claim(r["title"])]
        share = f"{len(hits) / len(win):.0%}" if win else "n/a"
        print(f"  {label}: {len(hits)} of {len(win)} title matches have number + unit ({share})")
        examples.append(sorted(hits, key=lambda r: r["published_at"]))
    # Balance examples across the two windows.
    pre, post = examples
    take_pre = min(len(pre), max(MAX_EXAMPLES // 2, MAX_EXAMPLES - len(post)))
    for r in pre[:take_pre] + post[: MAX_EXAMPLES - take_pre]:
        print(f"    {r['published_at'][:10]}  {r['title'][:120]}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    for path in sys.argv[1:]:
        report(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
