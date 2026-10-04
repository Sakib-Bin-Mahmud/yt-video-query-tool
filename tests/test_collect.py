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
    listed, reached = c.list_video_ids(client, "UU", c.parse_date("2026-04-01"),
                                       c.parse_date("2026-06-01"), stop_after_old=2)
    assert [v for v, _ in listed] == ["in1", "in2"]
    assert reached
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


# --------------------------------------------------------------------------- #
# Fixes from the 3-channel pilot
# --------------------------------------------------------------------------- #
def test_bangla_whole_word_with_inflection():
    m = c.KeywordMatcher(["চাল", "দাম", "তেল", "জ্বালানি তেল"])
    assert m.match("আফগানিস্তানে হামলা চালিয়ে") == []          # চালিয়ে is a verb, not rice
    assert m.match("মদ্যপ অবস্থায় গাড়ি চালিয়ে গ্রেফতার") == []
    assert m.match("চালের দাম বাড়ল") == ["চাল", "দাম"]       # inflected forms still match
    assert m.match("দামে স্বস্তি নেই") == ["দাম"]
    assert m.match("তেলের জন্য লম্বা লাইন") == ["তেল"]
    assert m.match("জ্বালানি তেলের মূল্য") == ["তেল", "জ্বালানি তেল"]
    assert m.match("তেলাপিয়া মাছ") == []                     # তেলাপিয়া (tilapia) is not তেল
    assert m.match("নতুনদাম") == []                           # must start at a word boundary


def test_boilerplate_description_lines_are_ignored():
    footer = "Trading news, Gas and oil, Electricity industry"
    rows = [{"title": f"খেলার খবর {i}", "description": f"Story {i}\n{footer}"} for i in range(40)]
    rows.append({"title": "ডিজেলের দাম বাড়ল", "description": f"Fuel prices up\n{footer}"})
    c.flag_matches(rows, c.KeywordMatcher(["oil", "fuel", "দাম"]))
    assert sum(r["is_match"] for r in rows) == 1
    hit = rows[-1]
    assert hit["title_terms"] == ["দাম"] and hit["description_terms"] == ["fuel"]


def test_tags_are_not_matched():
    rows = [{"title": "Cricket", "description": "", "tags": ["oil", "price"]}]
    c.flag_matches(rows, c.KeywordMatcher(["oil", "price"]))
    assert not rows[0]["is_match"]


def test_comments_processing_failure_retries_with_relevance():
    orders = []

    def handler(p):
        orders.append(p["order"])
        if p["order"] == "time":
            return FakeResp(400, {"error": {"errors": [{"reason": "processingFailure"}]}})
        return FakeResp(200, {"items": [{"snippet": {"topLevelComment": {"id": "c1", "snippet": {"textOriginal": "x"}}}}]})

    client = c.YouTubeClient("k", session=FakeSession({"commentThreads": handler}))
    assert [r["comment_id"] for r in c.fetch_comments(client, "v", 10)] == ["c1"]
    assert orders == ["time", "relevance"]


def _channel_routes(videos, comment_handler, playlist_dates=("2026-04-20T10:00:00Z", "2026-04-21T10:00:00Z")):
    return {
        "channels": lambda p: FakeResp(200, {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}),
        "playlistItems": lambda p: FakeResp(200, {"items": [
            playlist_item(v["id"], d) for v, d in zip(videos, playlist_dates)]}),
        "videos": lambda p: FakeResp(200, {"items": videos}),
        "commentThreads": comment_handler,
    }


def _run(tmp_path, monkeypatch, routes, extra=()):
    (tmp_path / "ch.csv").write_text("name,channel_id\nTest,UC" + "a" * 22 + "\n", encoding="utf-8")
    session = FakeSession(routes)
    monkeypatch.setattr(c.requests, "Session", lambda: session)
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    out = tmp_path / "data"
    argv = ["--channels", str(tmp_path / "ch.csv"), "--start", "2026-04-01", "--end", "2026-05-01",
            "--out", str(out), "--comments", *extra]
    return c.main(argv), out, session


def test_one_failing_video_does_not_stop_the_channel(tmp_path, monkeypatch):
    vids = [video("bad", "ডিজেলের দাম"), video("good", "চালের দাম", "2026-04-21T10:00:00Z")]

    def comments(p):
        if p["videoId"] == "bad":
            return FakeResp(403, {"error": {"errors": [{"reason": "commentsDisabled"}]}})
        return FakeResp(200, {"items": []})

    status, out, _ = _run(tmp_path, monkeypatch, _channel_routes(vids, comments))
    assert status == 0
    assert (out / "comments" / "good.jsonl").exists()
    assert not (out / "comments" / "bad.jsonl").exists()        # retried on a later run
    log = json.loads((out / "run_log.json").read_text(encoding="utf-8"))
    ch = log["channels"]["UC" + "a" * 22]
    assert ch["comment_errors"] == [{"video_id": "bad", "status": 403, "reason": "commentsDisabled"}]


def test_truncated_history_is_flagged(tmp_path, monkeypatch, capsys):
    vids = [video("v1", "দাম")]
    status, out, _ = _run(tmp_path, monkeypatch, _channel_routes(vids, lambda p: FakeResp(200, {"items": []})))
    meta = json.loads((out / "videos" / ("UC" + "a" * 22 + ".meta.json")).read_text(encoding="utf-8"))
    assert meta["reached_start"] is False                          # playlist ended inside the window
    assert "history truncated" in capsys.readouterr().out


def test_resume_reflags_old_data_without_api_calls(tmp_path, monkeypatch):
    cid = "UC" + "a" * 22
    vdir = tmp_path / "data" / "videos"
    vdir.mkdir(parents=True)
    old_rows = [  # written by the old matcher: false positive on চালিয়ে
        {"video_id": "v1", "title": "গাড়ি চালিয়ে গ্রেফতার", "description": "", "published_at": "2026-04-01T01:00:00Z",
         "matched_terms": ["চাল"], "is_match": True, "thumbnail_url": None},
        {"video_id": "v2", "title": "চালের দাম বাড়ল", "description": "", "published_at": "2026-04-02T01:00:00Z",
         "matched_terms": ["চাল", "দাম"], "is_match": True, "thumbnail_url": None},
    ]
    (vdir / f"{cid}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in old_rows), encoding="utf-8")
    status, out, session = _run(tmp_path, monkeypatch, _channel_routes([], lambda p: FakeResp(200, {"items": []})))
    rows = [json.loads(l) for l in (vdir / f"{cid}.jsonl").open(encoding="utf-8")]
    assert [r["is_match"] for r in rows] == [False, True]
    assert [e for e, _ in session.calls] == ["commentThreads"]   # only the true match fetches comments
