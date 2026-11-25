"""Unit tests for local clip discovery."""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "services", "api"))

from discovery import (
    CandidateClip,
    audio_energy_peaks,
    cache_key,
    clamp_windows,
    dedupe_overlaps,
    parse_manual_peaks,
    rank_candidates,
)


def test_candidate_clip_schema_valid():
    """Valid CandidateClip instantiates cleanly with defaults."""
    clip = CandidateClip(
        start_ms=10000,
        end_ms=40000,
        headline="Test Headline",
        hook="Hook opening sentence",
        summary="A concise summary of the clip.",
        why_this_works="High energy hook and punchy insight.",
        ai_quality=0.85,
        informativeness=0.80,
        standalone_clarity=0.90,
        audio_clarity=0.95,
        replay_signal=0.75,
        signal_source="manual_peaks",
        final_score=85.0,
        confidence=0.88,
    )
    assert clip.id.startswith("clip-")
    assert clip.start_ms == 10000
    assert clip.end_ms == 40000
    assert clip.final_score == 85.0
    assert clip.warnings == []


def test_candidate_clip_rejects_out_of_range_scores():
    """Subscores must be in [0.0, 1.0], final_score in [0.0, 100.0]."""
    valid_kwargs = {
        "start_ms": 10000,
        "end_ms": 40000,
        "headline": "H",
        "hook": "H",
        "summary": "S",
        "why_this_works": "W",
        "ai_quality": 0.8,
        "informativeness": 0.8,
        "standalone_clarity": 0.8,
        "audio_clarity": 0.8,
        "confidence": 0.8,
    }

    # ai_quality > 1.0
    with pytest.raises(ValidationError):
        CandidateClip(**{**valid_kwargs, "ai_quality": 1.2})

    # ai_quality < 0.0
    with pytest.raises(ValidationError):
        CandidateClip(**{**valid_kwargs, "ai_quality": -0.1})

    # informativeness out of range
    with pytest.raises(ValidationError):
        CandidateClip(**{**valid_kwargs, "informativeness": 1.5})

    # replay_signal out of range
    with pytest.raises(ValidationError):
        CandidateClip(**{**valid_kwargs, "replay_signal": 1.1})

    # final_score out of range
    with pytest.raises(ValidationError):
        CandidateClip(**{**valid_kwargs, "final_score": 105.0})

    with pytest.raises(ValidationError):
        CandidateClip(**{**valid_kwargs, "final_score": -5.0})


def test_candidate_clip_rejects_inverted_or_negative_timestamps():
    """end_ms must be strictly greater than start_ms, and start_ms >= 0."""
    valid_kwargs = {
        "headline": "H",
        "hook": "H",
        "summary": "S",
        "why_this_works": "W",
        "ai_quality": 0.8,
        "informativeness": 0.8,
        "standalone_clarity": 0.8,
        "audio_clarity": 0.8,
        "confidence": 0.8,
    }

    # Inverted: end < start
    with pytest.raises(ValidationError):
        CandidateClip(start_ms=50000, end_ms=40000, **valid_kwargs)

    # Equal: end == start
    with pytest.raises(ValidationError):
        CandidateClip(start_ms=30000, end_ms=30000, **valid_kwargs)

    # Negative start
    with pytest.raises(ValidationError):
        CandidateClip(start_ms=-1000, end_ms=30000, **valid_kwargs)


def test_clamp_windows_enforces_bounds_and_durations():
    """clamp_windows enforces [min_ms, max_ms] symmetrically within [0, duration_ms]."""
    clip_short = CandidateClip(
        start_ms=10000, end_ms=20000,  # 10s duration
        headline="Short", hook="H", summary="S", why_this_works="W",
        ai_quality=0.8, informativeness=0.8, standalone_clarity=0.8, audio_clarity=0.8, confidence=0.8,
    )
    clip_long = CandidateClip(
        start_ms=10000, end_ms=90000,  # 80s duration
        headline="Long", hook="H", summary="S", why_this_works="W",
        ai_quality=0.8, informativeness=0.8, standalone_clarity=0.8, audio_clarity=0.8, confidence=0.8,
    )
    clip_near_start = CandidateClip(
        start_ms=0, end_ms=15000,  # 15s duration at edge 0
        headline="Edge", hook="H", summary="S", why_this_works="W",
        ai_quality=0.8, informativeness=0.8, standalone_clarity=0.8, audio_clarity=0.8, confidence=0.8,
    )

    clamped = clamp_windows(
        [clip_short, clip_long, clip_near_start],
        duration_ms=120000,
        min_ms=30000,
        max_ms=60000,
    )

    assert len(clamped) == 3

    # Short clip (10s) expanded symmetrically by 20s (10s left, 10s right) -> 0s to 30s
    assert clamped[0].start_ms == 0
    assert clamped[0].end_ms == 30000
    assert clamped[0].end_ms - clamped[0].start_ms == 30000

    # Long clip (80s) shrunk symmetrically by 20s (10s left, 10s right) -> 20s to 80s = 60s
    assert clamped[1].start_ms == 20000
    assert clamped[1].end_ms == 80000
    assert clamped[1].end_ms - clamped[1].start_ms == 60000

    # Near start clip (0 to 15s) expanded to 30s, clamped at 0
    assert clamped[2].start_ms == 0
    assert clamped[2].end_ms == 30000

    # When video is shorter than min_ms, clips must be dropped
    assert clamp_windows([clip_short], duration_ms=25000, min_ms=30000, max_ms=60000) == []


def test_dedupe_overlaps_removes_overlaps_and_keeps_higher_score():
    """dedupe_overlaps greedily keeps higher-scoring candidate and discards overlapping windows."""
    c1 = CandidateClip(
        start_ms=10000, end_ms=40000, headline="High Score", hook="H", summary="S", why_this_works="W",
        ai_quality=0.9, informativeness=0.9, standalone_clarity=0.9, audio_clarity=0.9, confidence=0.9,
        final_score=92.0,
    )
    c2 = CandidateClip(
        start_ms=25000, end_ms=55000, headline="Overlap Lower Score", hook="H", summary="S", why_this_works="W",
        ai_quality=0.7, informativeness=0.7, standalone_clarity=0.7, audio_clarity=0.7, confidence=0.7,
        final_score=75.0,
    )
    c3 = CandidateClip(
        start_ms=43000, end_ms=73000, headline="Non-Overlap", hook="H", summary="S", why_this_works="W",
        ai_quality=0.8, informativeness=0.8, standalone_clarity=0.8, audio_clarity=0.8, confidence=0.8,
        final_score=80.0,
    )

    # c1 (10s–40s) overlaps c2 (25s–55s) -> c2 dropped.
    # c3 (43s–73s) has 3000ms gap from c1 (40s) >= min_gap_ms (2000ms) -> c3 kept.
    deduped = dedupe_overlaps([c2, c1, c3], min_gap_ms=2000)
    assert len(deduped) == 2
    assert deduped[0].headline == "High Score"
    assert deduped[1].headline == "Non-Overlap"


def test_rank_candidates_renormalizes_weights_when_replay_none():
    """When replay_signal is None, remaining 4 weights are re-normalized to sum to 1.0."""
    # Clip with replay_signal=None and all subscores=1.0
    c_none = CandidateClip(
        start_ms=10000, end_ms=40000, headline="No Replay", hook="H", summary="S", why_this_works="W",
        ai_quality=1.0, informativeness=1.0, standalone_clarity=1.0, audio_clarity=1.0,
        replay_signal=None, confidence=1.0,
    )
    ranked = rank_candidates([c_none], audience_weight=0.10)
    assert len(ranked) == 1
    # 0.35 + 0.25 + 0.20 + 0.10 = 0.90 re-normalized to 1.0 -> final_score must be 100.0
    assert ranked[0].final_score == 100.0

    # Clip with replay_signal=0.0 and all other subscores=1.0
    c_zero_replay = CandidateClip(
        start_ms=10000, end_ms=40000, headline="Zero Replay", hook="H", summary="S", why_this_works="W",
        ai_quality=1.0, informativeness=1.0, standalone_clarity=1.0, audio_clarity=1.0,
        replay_signal=0.0, confidence=1.0,
    )
    ranked_zero = rank_candidates([c_zero_replay], audience_weight=0.10)
    # 0.35 + 0.25 + 0.20 + 0.10 + 0 = 0.90 -> final_score must be 90.0
    assert ranked_zero[0].final_score == 90.0

    # Sorts descending
    ranked_both = rank_candidates([c_zero_replay, c_none], audience_weight=0.10)
    assert ranked_both[0].final_score == 100.0
    assert ranked_both[1].final_score == 90.0


def test_audio_energy_peaks_parses_ffmpeg_and_fails_gracefully():
    """audio_energy_peaks extracts local maxima above median and never raises on error."""
    ffmpeg_sample_output = (
        "frame:0    pts:0       pts_time:0.0\n"
        "lavfi.astats.Overall.RMS_level=-35.000000\n"
        "frame:1    pts:16000   pts_time:1.0\n"
        "lavfi.astats.Overall.RMS_level=-20.000000\n"
        "frame:2    pts:32000   pts_time:2.0\n"
        "lavfi.astats.Overall.RMS_level=-40.000000\n"
        "frame:3    pts:48000   pts_time:3.0\n"
        "lavfi.astats.Overall.RMS_level=-15.000000\n"
        "frame:4    pts:64000   pts_time:4.0\n"
        "lavfi.astats.Overall.RMS_level=-35.000000\n"
    )

    with patch("subprocess.run") as mock_subproc, patch("os.path.exists", return_value=True):
        mock_subproc.return_value = MagicMock(returncode=0, stdout=ffmpeg_sample_output)
        peaks = audio_energy_peaks("/fake/path.mp4", top_n=5)
        assert len(peaks) > 0
        # Loudest peak at t=3.0 (-15.0 dB)
        t_secs = [p["t_sec"] for p in peaks]
        assert 3.0 in t_secs

    # On FFmpeg error or missing file, returns [] without raising
    assert audio_energy_peaks("/nonexistent/file.mp4") == []
    with patch("subprocess.run", side_effect=RuntimeError("FFmpeg crashed")):
        with patch("os.path.exists", return_value=True):
            assert audio_energy_peaks("/fake/path.mp4") == []


def test_parse_manual_peaks():
    """parse_manual_peaks clamps to [0, duration_s] and ignores non-numeric values."""
    raw = [5.5, 120.0, -10.0, "invalid", None, float("nan"), 45.2]
    parsed = parse_manual_peaks(raw, duration_s=60.0)
    assert len(parsed) == 4
    # 5.5 inside
    assert parsed[0]["t_sec"] == 5.5
    # 120.0 clamped to 60.0
    assert parsed[1]["t_sec"] == 60.0
    # -10.0 clamped to 0.0
    assert parsed[2]["t_sec"] == 0.0
    # 45.2 inside
    assert parsed[3]["t_sec"] == 45.2


def test_cache_key_deterministic():
    """cache_key produces deterministic sha256 hex string sensitive to all inputs."""
    k1 = cache_key("hash1", "gemini-2.5-flash", "v1", {"num_clips": 3, "min_s": 30})
    k2 = cache_key("hash1", "gemini-2.5-flash", "v1", {"min_s": 30, "num_clips": 3})  # sorted dict
    k3 = cache_key("hash2", "gemini-2.5-flash", "v1", {"num_clips": 3, "min_s": 30})

    assert len(k1) == 64
    assert k1 == k2  # Dict key order normalization
    assert k1 != k3


def test_extract_media_peaks_keeps_audio_when_scene_times_out(monkeypatch):
    import discovery
    from threading import Event

    scene_started = Event()
    release_scene = Event()
    audio_peaks = [{"t_sec": 2.0, "rms_db": -9.0}]

    def scene(_):
        scene_started.set()
        release_scene.wait(2)
        return [{"t_sec": 3.0, "score": 0.5}]

    def audio(_):
        assert scene_started.wait(1)
        return audio_peaks

    monkeypatch.setattr(discovery, "SCENE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(discovery, "audio_energy_peaks", audio)
    monkeypatch.setattr(discovery, "scene_cut_peaks", scene)
    try:
        assert discovery.extract_media_peaks("sample.mp4") == (audio_peaks, [])
    finally:
        release_scene.set()
