"""Acceptance tests for local discovery."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from statistics import pstdev
from string import punctuation
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "services", "api"))

import discovery
import main as api
from discovery import build_sentences, generate_local_candidates


def load_sample(genre):
    path = Path(__file__).with_name("fixtures") / f"{genre}_sample.json"
    return json.loads(path.read_text(encoding="utf-8"))


def local_candidates(sample, min_ms, max_ms, num_clips=5):
    return generate_local_candidates(
        sample["words"], sample["energy_peaks"], round(sample["duration"] * 1000),
        num_clips, min_ms, max_ms, heatmap_peaks=sample["heatmap"],
    )


def word(token, start_ms, end_ms):
    return {"token": token, "source_start_us": round(start_ms * 1000), "source_end_us": round(end_ms * 1000)}


@pytest.mark.parametrize("genre,min_ms", [("podcast", 30000), ("gaming", 15000), ("educational", 30000)])
def test_local_clips_have_word_boundaries_complete_phrases_and_varied_lengths(genre, min_ms):
    sample = load_sample(genre)
    max_ms = 60000
    result = local_candidates(sample, min_ms, max_ms)
    assert result["prompt_version"] == "local-v1"
    assert result["model"] == "local-engine"
    candidates = result["candidates"]
    assert 2 <= len(candidates) <= 5
    assert candidates == sorted(candidates, key=lambda candidate: candidate.final_score, reverse=True)
    assert candidates == local_candidates(sample, min_ms, max_ms)["candidates"]

    intervals = [(round(w["source_start_us"] / 1000), round(w["source_end_us"] / 1000)) for w in sample["words"]]
    word_starts, word_ends = map(set, zip(*intervals))
    sentences = build_sentences(sample["words"])
    openings = {sentence["start_ms"]: sentence for sentence in sentences}
    endings = {sentence["end_ms"]: sentence for sentence in sentences}
    durations = []
    for candidate in candidates:
        start, end = candidate.start_ms, candidate.end_ms
        assert start in word_starts and end in word_ends
        assert not any(left < start < right or left < end < right for left, right in intervals)
        assert 0 <= start < end <= sample["duration"] * 1000
        assert min_ms <= end - start <= max_ms
        assert end - start != (min_ms + max_ms) / 2
        durations.append((end - start) / 1000)

        opening = openings[start]["text"].split()
        assert opening[0].strip(punctuation).lower() not in {"and", "but", "because", "so", "or", "then"}
        assert opening[0][0].isupper()
        ending = endings[end]
        assert ending["terminal"] or ending["gap_after_ms"] >= (500 if genre == "podcast" else 400)
        while opening and opening[0].strip(punctuation).lower() in {"um", "uh", "like", "yeah", "so"}:
            opening.pop(0)
        assert 0 < len(candidate.headline) <= 60 and candidate.headline != "Local highlight 1"
        assert opening[:len(candidate.headline.split())] == candidate.headline.split()
        assert candidate.confidence == 0.45
        expected_replay = max((peak["value"] for peak in sample["heatmap"] if start <= peak["t_sec"] * 1000 <= end), default=None)
        assert candidate.replay_signal == expected_replay
    assert pstdev(durations) > 3.0


def test_gaming_cuts_center_both_loud_peaks():
    sample = load_sample("gaming")
    candidates = local_candidates(sample, 15000, 60000)["candidates"]
    loud_peaks = [peak["t_sec"] for peak in sample["energy_peaks"] if peak["rms_db"] > -12]
    assert loud_peaks == [42.5, 115.0]
    for peak in loud_peaks:
        candidate = next((c for c in candidates if c.start_ms <= peak * 1000 <= c.end_ms), None)
        assert candidate is not None, f"No cut covers the reaction at {peak}s"
        position = (peak * 1000 - candidate.start_ms) / (candidate.end_ms - candidate.start_ms)
        assert 0.25 <= position <= 0.85


def test_uncenterable_peak_keeps_word_boundaries_and_warns():
    words = [word("Opening", 800, 2000), word("thought.", 2080, 7000), word("Conclusion.", 7600, 17800)]
    result = generate_local_candidates(words, [{"t_sec": 2.0, "rms_db": -10}], 20000, 1, 15000, 20000)
    clip, = result["candidates"]
    assert (clip.start_ms, clip.end_ms) == (800, 17800)
    assert clip.warnings == ["peak-position"]


def test_educational_clips_need_neither_heatmap_nor_energy_peaks():
    sample = load_sample("educational")
    assert sample["heatmap"] == []
    candidates = local_candidates({**sample, "energy_peaks": []}, 30000, 60000)["candidates"]
    assert candidates
    assert all(c.replay_signal is None and c.signal_source == "local_heuristic" for c in candidates)
    assert generate_local_candidates([], [], 420000, 5, 30000, 60000)["candidates"] == []


def test_sentence_segmentation_tracks_pauses_and_quoted_punctuation():
    words = [word('"Ready?"', 10.125, 190.625), word("Wait", 200, 400), word("here", 700, 900)]
    assert build_sentences(words) == [
        {"start_ms": 10, "end_ms": 191, "text": '"Ready?"', "n_words": 1, "terminal": True,
         "gap_before_ms": 10.125, "gap_after_ms": 9.375},
        {"start_ms": 200, "end_ms": 400, "text": "Wait", "n_words": 1, "terminal": False,
         "gap_before_ms": 9.375, "gap_after_ms": 300},
        {"start_ms": 700, "end_ms": 900, "text": "here", "n_words": 1, "terminal": False,
         "gap_before_ms": 300, "gap_after_ms": 0},
    ]


def test_sentence_run_limits_prefer_last_comma_and_otherwise_split_in_half():
    short_words = [word("pause," if i in (9, 40) else "word", i * 100, i * 100 + 80) for i in range(61)]
    by_count = build_sentences(short_words)
    assert [sentence["n_words"] for sentence in by_count] == [41, 20]
    assert by_count[0]["text"].endswith("pause,")
    long_words = [word("word", i * 1000, i * 1000 + 900) for i in range(30)]
    by_duration = build_sentences(long_words)
    assert [sentence["n_words"] for sentence in by_duration] == [13, 17]
    assert all(sentence["end_ms"] - sentence["start_ms"] <= 25000 for sentence in by_duration)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setattr(api, "UPLOADS_DIR", str(tmp_path))
    api.init_db()
    with TestClient(api.app) as client:
        yield client


def create_project(client, sample, monkeypatch, scene_peaks=()):
    monkeypatch.setattr(api, "get_media_info", lambda _: (sample["duration"], 1920, 1080))
    monkeypatch.setattr(discovery, "audio_energy_peaks", lambda *_args, **_kwargs: sample["energy_peaks"])
    monkeypatch.setattr(discovery, "scene_cut_peaks", lambda _: list(scene_peaks))
    response = client.post("/api/projects", files={"file": ("source.mp4", b"fixture", "video/mp4")})
    assert response.status_code == 200
    return f"/api/projects/{response.json()['id']}"


def wait_for_result(client, path):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200
        result = response.json()
        if result["status"] in {"ready", "failed"}:
            return result
        time.sleep(0.02)
    pytest.fail(f"Pipeline did not finish at {path}: {result}")


def test_local_discovery_applies_candidates(client, monkeypatch):
    sample = load_sample("educational")
    path = create_project(client, sample, monkeypatch, scene_peaks=[{"t_sec": 61.2, "score": 0.6}])
    assert client.put(path, json={"words": sample["words"]}).status_code == 200
    started = client.post(f"{path}/discover", json={})
    assert started.status_code == 200
    result = wait_for_result(client, f"{path}/candidates")
    assert result["status"] == "ready", result
    assert result["candidates"] and result["model"] == "local-engine"
    assert result["signal_sources"] == {"transcript": True, "audio_energy": True, "scene_cuts": True, "manual_peaks": False}
    applied = client.post(f"{path}/candidates/apply", json=[result["candidates"][0]["id"]])
    assert applied.status_code == 200
    assert applied.json()["timeline"][0]["origin"] == "local"


def test_discovery_cache_preserves_heatmap_values(monkeypatch, tmp_path):
    sample = load_sample("podcast")
    media = tmp_path / "source.mp4"
    media.touch()
    row = {
        "video_path": str(media), "duration": sample["duration"],
        "words_json": json.dumps(sample["words"]), "heatmap_json": json.dumps(sample["heatmap"]),
    }
    measure = Mock(return_value=sample["energy_peaks"])
    monkeypatch.setattr(discovery, "audio_energy_peaks", measure)
    monkeypatch.setattr(discovery, "scene_cut_peaks", lambda _: [])

    local = api._discover_payload(row, api.DiscoverRequest())
    assert {candidate["replay_signal"] for candidate in local["candidates"]} == {0.72, 0.64, 0.88, 0.69}
    row["discovery_json"] = json.dumps(local)
    assert api._discover_payload(row, api.DiscoverRequest()) == local
    measure.assert_called_once()


def test_scene_peaks_use_frame_timestamps_and_release_capture_on_failure(monkeypatch, tmp_path):
    media = tmp_path / "scene.mp4"
    media.touch()
    tones = [0, 10, 150, 145, 60, 55]
    frame_index = -1
    retrieved = []

    def grab():
        nonlocal frame_index
        frame_index += 1
        return frame_index < len(tones) * 2

    def retrieve():
        retrieved.append(frame_index)
        return True, SimpleNamespace(shape=(180, 480), tone=tones[frame_index // 2])

    cv2 = discovery.cv2
    capture = Mock()
    capture.grab.side_effect = grab
    capture.retrieve.side_effect = retrieve
    capture.get.side_effect = lambda prop: {
        cv2.CAP_PROP_FPS: 2, cv2.CAP_PROP_FRAME_COUNT: len(tones) * 2,
        cv2.CAP_PROP_POS_MSEC: frame_index * 500 + 130,
    }[prop]
    resize = Mock(side_effect=lambda frame, size, **_: frame)
    monkeypatch.setattr(cv2, "VideoCapture", lambda _: capture)
    monkeypatch.setattr(cv2, "cvtColor", lambda frame, _: frame)
    monkeypatch.setattr(cv2, "resize", resize)
    monkeypatch.setattr(cv2, "absdiff", lambda frame, previous: abs(frame.tone - previous.tone))
    monkeypatch.setattr(cv2, "mean", lambda delta: (delta,))

    peaks = discovery.scene_cut_peaks(str(media))
    assert retrieved == [0, 2, 4, 6, 8, 10]
    assert resize.call_count == len(tones) and resize.call_args.args[1] == (320, 120)
    assert [peak["t_sec"] for peak in peaks] == [2.13, 4.13]
    assert [peak["score"] for peak in peaks] == pytest.approx([1.0, 2 * 85 / 255])
    capture.release.assert_called_once()
    frame_index = -1
    capture.retrieve.side_effect = RuntimeError("decoder failed")
    assert discovery.scene_cut_peaks(str(media)) == []
    assert capture.release.call_count == 2
