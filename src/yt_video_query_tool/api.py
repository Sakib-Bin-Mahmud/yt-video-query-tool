import os
import requests

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()

API_KEY = os.environ.get("YOUTUBE_API_KEY")
if not API_KEY:
    raise SystemExit("Environment variable YOUTUBE_API_KEY not set.")

CHANNEL_ID = "UCxHoBXkY88Tb8z1Ssj6CWsQ"

PUBLISHED_AFTER = "2026-03-15T00:00:00Z"
PUBLISHED_BEFORE = "2026-05-05T00:00:00Z"

KEYWORDS = [
    "তেল",
    "জ্বালানি",
    "তেলের দাম",
    "পেট্রোল",
    "ডিজেল",
    "জ্বালানি তেল",
    "fuel",
    "oil price",
    "oil",
    "price",
    "crude",
    "crisis",
]

CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
PLAYLIST_URL = "https://www.googleapis.com/youtube/v3/playlistItems"


def get_uploads_playlist_id():
    response = requests.get(
        CHANNELS_URL,
        params={"part": "contentDetails", "id": CHANNEL_ID, "key": API_KEY},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    return data["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]


def fetch_all_videos(uploads_playlist_id):
    videos = []
    page_token = None

    while True:
        params = {
            "part": "snippet",
            "playlistId": uploads_playlist_id,
            "maxResults": 50,
            "key": API_KEY,
        }
        if page_token:
            params["pageToken"] = page_token

        response = requests.get(PLAYLIST_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if "error" in data:
            raise RuntimeError(data["error"])

        for item in data.get("items", []):
            published_at = item["snippet"]["publishedAt"]
            if published_at < PUBLISHED_AFTER:
                return videos
            if published_at <= PUBLISHED_BEFORE:
                videos.append(item)

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return videos


def matches_keywords(text: str) -> bool:
    text = text.lower()
    return any(keyword.lower() in text for keyword in KEYWORDS)


def main():
    uploads_playlist_id = get_uploads_playlist_id()
    videos = fetch_all_videos(uploads_playlist_id)

    print(f"Fetched {len(videos)} videos in date range")

    filtered = []
    for item in videos:
        snippet = item["snippet"]
        searchable_text = f"{snippet.get('title', '')} {snippet.get('description', '')}"
        if matches_keywords(searchable_text):
            filtered.append(item)

    print(f"Found {len(filtered)} matching videos\n")

    for item in filtered:
        snippet = item["snippet"]
        video_id = snippet["resourceId"]["videoId"]
        print(snippet["publishedAt"])
        print(snippet["title"])
        print(f"https://youtube.com/watch?v={video_id}")
        print("-" * 80)


if __name__ == "__main__":
    main()
