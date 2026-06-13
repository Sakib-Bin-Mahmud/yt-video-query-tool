import os
import requests

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

if load_dotenv:
    load_dotenv()

API_KEY = os.environ.get("YOUTUBE_API_KEY")
if not API_KEY:
    raise SystemExit(
        "Environment variable YOUTUBE_API_KEY not set. Export it and rerun (e.g. export YOUTUBE_API_KEY=your_key) or place it in a .env file."
    )

CHANNEL_ID = "UCxHoBXkY88Tb8z1Ssj6CWsQ"

published_after = "2026-04-15T00:00:00Z"
published_before = "2026-05-05T00:00:00Z"

keywords = "oil OR fuel OR তেল OR জ্বালানি OR তেলের দাম"

url = "https://www.googleapis.com/youtube/v3/search"

params = {
    "part": "snippet",
    "channelId": CHANNEL_ID,
    "type": "video",
    "maxResults": 500,
    "publishedAfter": published_after,
    "publishedBefore": published_before,
    "q": keywords,
    "key": API_KEY,
}

response = requests.get(url, params=params)
data = response.json()

print(data)

if "error" in data:
    print(data["error"])
else:
    print(f"Found {len(data.get('items', []))} items")

for item in data.get("items", []):
    video_id = item["id"]["videoId"]
    title = item["snippet"]["title"]
    date = item["snippet"]["publishedAt"]

    print(date)
    print(title)
    print("https://youtube.com/watch?v=" + video_id)
    print("-" * 500)
