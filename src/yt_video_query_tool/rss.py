import requests
import feedparser
from datetime import datetime, timezone

CHANNEL_ID = "UCxHoBXkY88Tb8z1Ssj6CWsQ"

RSS_URL = (
    f"https://www.youtube.com/feeds/videos.xml"
    f"?channel_id={CHANNEL_ID}"
)

PUBLISHED_AFTER = datetime.fromisoformat(
    "2026-03-15T00:00:00+00:00"
)

PUBLISHED_BEFORE = datetime.fromisoformat(
    "2026-05-05T00:00:00+00:00"
)

KEYWORDS = [
    "তেল",
    "জ্বালানি",
    "তেলের দাম",
    "পেট্রোল",
    "ডিজেল",
    "জ্বালানি তেল",
    "fuel",
    "oil",
    "oil price",
    "price",
    "crude",
    "crisis",
]


def matches_keywords(text: str) -> bool:
    text = text.lower()
    return any(k.lower() in text for k in KEYWORDS)


def fetch_feed():
    response = requests.get(RSS_URL, timeout=30)

    response.raise_for_status()

    return feedparser.parse(response.text)


def parse_entry(entry):
    published = datetime(
        *entry.published_parsed[:6],
        tzinfo=timezone.utc,
    )

    return {
        "title": entry.title,
        "link": entry.link,
        "published": published,
        "video_id": entry.yt_videoid,
    }


def main():
    feed = fetch_feed()

    videos = []

    for entry in feed.entries:
        video = parse_entry(entry)

        if not (
            PUBLISHED_AFTER
            <= video["published"]
            <= PUBLISHED_BEFORE
        ):
            continue

        if not matches_keywords(video["title"]):
            continue

        videos.append(video)

    print(f"Found {len(videos)} matching videos\n")

    videos.sort(
        key=lambda x: x["published"],
        reverse=True,
    )

    for video in videos:
        print(video["published"])
        print(video["title"])
        print(video["link"])
        print("-" * 80)


if __name__ == "__main__":
    main()
