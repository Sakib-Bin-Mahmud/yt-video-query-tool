import json

from yt_video_query_tool import asr_eval as e
from yt_video_query_tool import transcribe as t


def test_normalize_digits_separators_and_glued_units():
    assert e.normalize("ডিজেল ১১৫টাকা, অকটেন ১,২৬০ লিটার।") == ["ডিজেল", "115", "টাকা", "অকটেন", "1260", "লিটার"]
    assert e.normalize("Price ১৭%") == ["price", "17", "%"]


def test_numeric_tokens_words_and_suffixes():
    words = e.normalize("সাড়ে ১৩ হাজারের বেশি, দেড় লাখ টাকা, নয়টি নয়")
    assert e.numeric_tokens(words) == ["সাড়ে", "13", "হাজার", "দেড়", "লাখ"]


def test_scores_and_number_errors():
    ref = "ডিজেলের দাম লিটারে ১৫ টাকা বেড়ে ১১৫ টাকা হয়েছে"
    hyp = "ডিজেলের দাম লিটারে ৫০ টাকা বেড়ে ১১৫ টাকা হয়েছে"     # 15 misheard as 50
    s = e.score(ref, hyp)
    r = e.rates(s)
    assert s["word_errors"] == 1 and round(r["WER"], 3) == round(1 / 9, 3)
    assert s["missed_nums"] == ["15"] and s["spurious_nums"] == ["50"]
    assert r["NUM-P"] == 0.5 and r["NUM-R"] == 0.5
    assert e.rates(e.score(ref, ref)) == {"WER": 0.0, "CER": 0.0, "NUM-P": 1.0, "NUM-R": 1.0, "NUM-F1": 1.0}


def test_gold_window_uses_word_timestamps(tmp_path):
    gold = tmp_path / "gold"
    gold.mkdir()
    (gold / "v1.txt").write_text("# end: 5\nদাম ২০ টাকা বেড়েছে\n", encoding="utf-8")
    sysdir = tmp_path / "sysA"
    sysdir.mkdir()
    (sysdir / "v1.json").write_text(json.dumps({"segments": [{"start": 0, "end": 8, "text": "...", "words": [
        {"start": 0.5, "word": "দাম"}, {"start": 1.0, "word": " ২০"}, {"start": 1.5, "word": " টাকা"},
        {"start": 2.0, "word": " বেড়েছে"}, {"start": 6.0, "word": " ৩০০"}]}]}, ensure_ascii=False), encoding="utf-8")
    rows, pooled = e.evaluate(gold, {"sysA": sysdir, "missing": tmp_path / "nope"})
    assert pooled["sysA"]["WER"] == 0.0 and pooled["sysA"]["NUM-F1"] == 1.0   # word at 6.0s is outside the window
    assert pooled["missing"] == {"videos": 0}


def test_sample_file_overrides_sampling(tmp_path):
    sf = tmp_path / "gold_videos.csv"
    sf.write_text("video_id,channel_name\nabc,News24\n", encoding="utf-8")
    got = []

    def fake_download(url, dest):
        got.append(url)
        raise RuntimeError("stop here")

    t.main(["--sample-file", str(sf), "--out", str(tmp_path / "o")], transcriber_factory=lambda: (lambda p: {}),
           download=fake_download)
    assert got == ["https://www.youtube.com/watch?v=abc"]


def test_cpu_defaults_to_int8():
    assert t.resolve_compute_type("cpu", "auto") == "int8"
    assert t.resolve_compute_type("cuda", "float16") == "float16"
