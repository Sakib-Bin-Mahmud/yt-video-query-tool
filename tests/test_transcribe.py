import csv
import json
from pathlib import Path

from yt_video_query_tool import transcribe as t


def vid(i, ch, published, title="ডিজেলের দাম বাড়ল", dur=120, live="none"):
    return {"video_id": f"{ch[:2]}{i}", "channel_name": ch, "published_at": published,
            "title": title, "duration_s": dur, "live_broadcast": live,
            "url": f"https://www.youtube.com/watch?v={ch[:2]}{i}"}


def test_candidate_filter():
    assert t.is_candidate("ডিজেলের দাম লিটারে ১৫ টাকা বেড়েছে")
    assert t.is_candidate("দাম বেড়েছে সাড়ে সাত শতাংশ")
    assert t.is_candidate("লিটারে বিশ টাকা বাড়ানো হয়েছে")
    assert t.is_candidate("Price up 17%")
    assert not t.is_candidate("জ্বালানি তেলের দাম বাড়ানো হয়েছে")   # no number
    assert not t.is_candidate("১৯ এপ্রিল থেকে কার্যকর হবে")            # no unit/price word
    assert not t.is_candidate("এটা সঠিক নয়, দাম বাড়েনি")             # নয় is negation, not nine
    assert not t.is_candidate("দশকের সবচেয়ে বড় দাম")                  # দশক is not দশ


def test_sample_is_stratified_filtered_and_deterministic():
    videos = []
    for ch in ("News24", "ATN Bangla News"):
        for i in range(6):
            videos.append(vid(i, ch, f"2026-04-1{i}T08:00:00Z"))                # 10-15 Apr: before
            videos.append(vid(10 + i, ch, f"2026-04-2{i}T08:00:00Z"))           # 20-25 Apr: after
    videos += [
        vid(90, "News24", "2026-04-20T08:00:00Z", title="ক্রিকেটের খবর"),        # not fuel
        vid(91, "News24", "2026-04-20T08:00:00Z", dur=3600),                     # too long
        vid(92, "News24", "2026-04-20T08:00:00Z", live="live"),                  # live
        vid(93, "News24", "2026-05-20T08:00:00Z"),                               # outside window
        vid(94, "Somoy TV", "2026-04-20T08:00:00Z"),                             # channel not selected
    ]
    args = (["News24", "ATN Bangla News"], "2026-04-10", "2026-04-27", "2026-04-19", 4, 42, 30, 900)
    s1 = t.draw_sample(videos, *args)
    s2 = t.draw_sample(list(reversed(videos)), *args)
    assert [x["video_id"] for x in s1] == [x["video_id"] for x in s2]
    assert len(s1) == 8
    for ch in ("News24", "ATN Bangla News"):
        periods = [x["period"] for x in s1 if x["channel_name"] == ch]
        assert periods.count("before") == 2 and periods.count("after") == 2
    assert not {"Ne90", "Ne91", "Ne92", "Ne93", "So94"} & {x["video_id"] for x in s1}


def test_end_to_end_with_fakes_deletes_audio_and_resumes(tmp_path):
    vdir = tmp_path / "videos"
    vdir.mkdir()
    rows = [vid(1, "News24", "2026-04-15T08:00:00Z"), vid(2, "News24", "2026-04-21T08:00:00Z"),
            vid(3, "News24", "2026-04-22T08:00:00Z")]
    (vdir / "UCx.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    (vdir / "UCx.meta.json").write_text("{}", encoding="utf-8")  # must be ignored by the loader

    audio_paths, calls = [], []

    def fake_download(url, dest):
        if url.endswith("Ne3"):
            raise RuntimeError("Video unavailable")
        p = dest / (url[-3:] + ".m4a")
        p.write_bytes(b"audio")
        audio_paths.append(p)
        return p

    def fake_factory():
        calls.append("load")

        def run(path):
            return {"model": "fake", "language": "bn", "language_probability": 1.0, "audio_duration_s": 120.0,
                    "segments": [
                        {"start": 0.0, "end": 4.0, "text": "জ্বালানি তেলের দাম বেড়েছে", "avg_logprob": -0.2,
                         "no_speech_prob": 0.01, "words": []},
                        {"start": 4.0, "end": 9.5, "text": "ডিজেল লিটারে ১৫ টাকা বেড়ে ১১৫ টাকা", "avg_logprob": -0.3,
                         "no_speech_prob": 0.01, "words": [{"start": 4.0, "end": 4.5, "word": "ডিজেল", "probability": 0.9},
                                                           {"start": 5.0, "end": 5.4, "word": "১৫", "probability": 0.6}]},
                    ]}
        return run

    out = tmp_path / "asr"
    argv = ["--videos-dir", str(vdir), "--out", str(out), "--channels", "News24", "--per-channel", "3",
            "--start", "2026-04-12", "--end", "2026-04-27"]
    status = t.main(argv, transcriber_factory=fake_factory, download=fake_download)

    assert status == 1                                   # one video failed, others done
    assert audio_paths and not any(p.exists() for p in audio_paths)   # audio deleted
    assert sorted(p.stem for p in (out / "transcripts").glob("*.json")) == ["Ne1", "Ne2"]
    log = json.loads((out / "asr_log.json").read_text(encoding="utf-8"))
    assert [e["video_id"] for e in log["errors"]] == ["Ne3"]

    with (out / "candidates.csv").open(encoding="utf-8-sig") as f:
        cands = list(csv.DictReader(f))
    assert len(cands) == 2 and all(c["seg_index"] == "1" for c in cands)
    assert cands[0]["prev_text"] == "জ্বালানি তেলের দাম বেড়েছে"
    assert cands[0]["min_word_prob"] == "0.6"
    assert cands[0]["link"].endswith("?t=4")
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["News24"]["videos"] == 2 and summary["News24"]["mean_per_video"] == 1

    # Re-run: sample reused, finished videos skipped, only the failed one retried.
    calls.clear()
    audio_paths.clear()
    t.main(argv, transcriber_factory=fake_factory, download=fake_download)
    assert calls == ["load"] and audio_paths == []      # Ne3 retried (and failed again), nothing re-transcribed


def test_report_only_needs_no_models(tmp_path):
    out = tmp_path / "asr"
    (out / "transcripts").mkdir(parents=True)
    (out / "transcripts" / "a.json").write_text(json.dumps({
        "video_id": "a", "channel_name": "ATN Bangla News", "published_at": "2026-04-20", "period": "after",
        "audio_duration_s": 60, "segments": [{"start": 1, "end": 2, "text": "দাম ২০ টাকা", "words": []}]},
        ensure_ascii=False), encoding="utf-8")

    def boom():
        raise AssertionError("model must not load in --report-only")

    assert t.main(["--out", str(out), "--report-only"], transcriber_factory=boom) == 0
    assert json.loads((out / "summary.json").read_text(encoding="utf-8"))["ALL"]["candidates"] == 1
