import json

import pytest

from yt_video_query_tool import collect as c


class FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.content = b"img"

    def json(self):
        return self._payload


class FakeSession:
    """Routes requests by endpoint name to canned responses."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, timeout=None):
        endpoint = url.rsplit("/", 1)[-1]
        self.calls.append((endpoint, dict(params or {})))
        handler = self.routes.get(endpoint)
        if handler is None:
            return FakeResp(200, {})
        return handler(params or {})


def playlist_item(vid, published):
    return {"contentDetails": {"videoId": vid, "videoPublishedAt": published}}


def video(vid, title, published="2026-04-20T10:00:00Z"):
    return {
        "id": vid,
        "snippet": {"title": title, "description": "", "publishedAt": published,
                    "thumbnails": {"high": {"url": f"https://i.ytimg.com/{vid}.jpg"}}},
        "statistics": {"viewCount": "100", "commentCount": "2"},
        "contentDetails": {"duration": "PT3M5S"},
    }


def test_keyword_matcher_bangla_suffix_and_latin_boundaries():
    m = c.KeywordMatcher(["তেল", "oil"])
    assert m.match("তেলের দাম বাড়ল") == ["তেল"]
    assert m.match("Oil prices rise") == ["oil"]
    assert m.match("water will boil") == []  # no substring hit inside 'boil'


def test_duration_seconds():
    assert c.duration_seconds("PT1H2M3S") == 3723
    assert c.duration_seconds("P1DT1M") == 86460
    assert c.duration_seconds(None) is None


def test_list_video_ids_window_and_tolerant_stop():
    pages = [
        {"items": [playlist_item("new", "2026-07-01T00:00:00Z"),   # after end
                   playlist_item("in1", "2026-04-20T00:00:00Z"),
                   playlist_item("old1", "2026-01-01T00:00:00Z"),  # older, but streak < limit
                   playlist_item("in2", "2026-04-18T00:00:00Z"),   # out-of-order: still kept
                   playlist_item("old2", "2026-01-01T00:00:00Z"),
                   playlist_item("old3", "2026-01-01T00:00:00Z")],
         "nextPageToken": "p2"},
        {"items": [playlist_item("never", "2026-04-19T00:00:00Z")]},
    ]
    it = iter(pages)
    session = FakeSession({"playlistItems": lambda p: FakeResp(200, next(it))})
    client = c.YouTubeClient("k", session=session)
    ids = [v for v, _ in c.list_video_ids(client, "UU", c.parse_date("2026-04-01"),
                                          c.parse_date("2026-06-01"), stop_after_old=2)]
    assert ids == ["in1", "in2"]
    assert len(session.calls) == 1  # stopped before fetching page 2


def test_quota_exceeded_is_raised_and_key_not_leaked():
    err = {"error": {"errors": [{"reason": "quotaExceeded"}]}}
    client = c.YouTubeClient("SECRET", session=FakeSession({"videos": lambda p: FakeResp(403, err)}))
    with pytest.raises(c.QuotaExceeded):
        client.get("videos", id="x")

    bad = c.YouTubeClient("SECRET", session=FakeSession({"videos": lambda p: FakeResp(400, {})}))
    with pytest.raises(c.ApiError) as e:
        bad.get("videos", id="x")
    assert "SECRET" not in str(e.value)


def test_load_channels_rejects_malformed_id(tmp_path):
    f = tmp_path / "ch.csv"
    f.write_text("name,channel_id\nJamuna,UCN6sm8iHiPd0cnoUardDAnwv\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        c.load_channels(f, None)


def test_end_to_end_run_and_resume(tmp_path, monkeypatch):
    (tmp_path / "ch.csv").write_text("name,channel_id\nTest,UC" + "a" * 22 + "\n", encoding="utf-8")
    routes = {
        "channels": lambda p: FakeResp(200, {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}),
        "playlistItems": lambda p: FakeResp(200, {"items": [
            playlist_item("v1", "2026-04-20T10:00:00Z"),
            playlist_item("v2", "2026-04-21T10:00:00Z")]}),
        "videos": lambda p: FakeResp(200, {"items": [
            video("v1", "ডিজেলের দাম লিটারে ১৫ টাকা বাড়ল"),
            video("v2", "Cricket highlights", "2026-04-21T10:00:00Z")]}),
        "commentThreads": lambda p: FakeResp(200, {"items": [{
            "snippet": {"topLevelComment": {"id": "c1", "snippet": {
                "textOriginal": "সব কিছুর দাম বাড়ছে", "authorChannelId": {"value": "UCx"}}}},
            "replies": {"comments": [{"id": "c2", "snippet": {"textOriginal": "ঠিক"}}]}}]}),
    }
    session = FakeSession(routes)
    monkeypatch.setattr(c.requests, "Session", lambda: session)
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")

    out = tmp_path / "data"
    argv = ["--channels", str(tmp_path / "ch.csv"), "--start", "2026-04-01", "--end", "2026-05-01",
            "--out", str(out), "--comments", "--thumbnails"]
    assert c.main(argv) == 0

    rows = [json.loads(l) for l in (out / "videos" / ("UC" + "a" * 22 + ".jsonl")).open(encoding="utf-8")]
    assert [r["video_id"] for r in rows] == ["v1", "v2"]
    assert rows[0]["is_match"] and "দাম" in rows[0]["matched_terms"]
    assert not rows[1]["is_match"]
    assert rows[0]["duration_s"] == 185

    comments = [json.loads(l) for l in (out / "comments" / "v1.jsonl").open(encoding="utf-8")]
    assert [x["comment_id"] for x in comments] == ["c1", "c2"]
    assert comments[1]["parent_id"] == "c1"
    assert "authorChannelId" not in json.dumps(comments) and comments[0]["author_hash"]
    assert (out / "thumbnails" / "v1.jpg").exists()
    assert not (out / "comments" / "v2.jsonl").exists()

    n_before = len(session.calls)
    assert c.main(argv) == 0  # resume: nothing re-fetched
    assert len(session.calls) == n_before
