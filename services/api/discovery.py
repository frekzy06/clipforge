"""Rank local clip candidates using speech, audio, scene cuts, and audience signals."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import subprocess
import time
import uuid
from bisect import bisect_left, bisect_right
from concurrent.futures import ThreadPoolExecutor
from itertools import accumulate
from statistics import quantiles
from string import punctuation
from typing import Any, Dict, List, Optional, Sequence, Tuple, TypedDict, Union

import cv2
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)
SCENE_TIMEOUT_SECONDS = 30


class CandidateClip(BaseModel):
    """Pydantic v2 model for a recommended video clip candidate.

    All subscores are normalized in [0.0, 1.0].
    The composite `final_score` is scaled in [0.0, 100.0].
    Timestamps are integer milliseconds in the source video.
    """
    id: str = Field(default_factory=lambda: f"clip-{uuid.uuid4().hex[:8]}")
    start_ms: int
    end_ms: int
    headline: str
    hook: str
    summary: str
    why_this_works: str
    ai_quality: float
    informativeness: float
    standalone_clarity: float
    audio_clarity: float
    replay_signal: Optional[float] = None
    signal_source: str = "ai_heuristic"
    final_score: float = 0.0
    confidence: float = 0.0
    suggested_layout: Optional[str] = None
    suggested_subtitle_preset: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)

    @field_validator("ai_quality", "informativeness", "standalone_clarity", "audio_clarity", "confidence")
    @classmethod
    def _validate_subscore(cls, v: float) -> float:
        val = float(v)
        if val < 0.0 or val > 1.0:
            raise ValueError(f"Subscore must be between 0.0 and 1.0, got {v}")
        return val

    @field_validator("replay_signal")
    @classmethod
    def _validate_replay_signal(cls, v: Optional[float]) -> Optional[float]:
        if v is not None:
            val = float(v)
            if val < 0.0 or val > 1.0:
                raise ValueError(f"replay_signal must be between 0.0 and 1.0, got {v}")
            return val
        return None

    @field_validator("final_score")
    @classmethod
    def _validate_final_score(cls, v: float) -> float:
        val = float(v)
        if val < 0.0 or val > 100.0:
            raise ValueError(f"final_score must be between 0.0 and 100.0, got {v}")
        return val

    @model_validator(mode="after")
    def _validate_timestamps(self) -> CandidateClip:
        if self.start_ms < 0:
            raise ValueError(f"start_ms must be non-negative, got {self.start_ms}")
        if self.end_ms <= self.start_ms:
            raise ValueError(
                f"end_ms ({self.end_ms}) must be strictly greater than start_ms ({self.start_ms})"
            )
        return self


def normalize_cue_words(cues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Adapt start-only caption cues to the word shape the local engine expects.

    Cues carry only ``source_start_us``; the local sentence engine needs an end
    for every word. The next cue's start is the natural end (cues are
    contiguous); the final cue falls back to start + 2s.
    """
    words: List[Dict[str, Any]] = []
    for index, cue in enumerate(cues):
        token = str(cue.get("token") or "").strip()
        start_us = int(cue.get("source_start_us") or 0)
        if not token or start_us < 0:
            continue
        raw_end = cue.get("source_end_us")
        if raw_end is None:
            nxt = cues[index + 1].get("source_start_us") if index + 1 < len(cues) else None
            raw_end = nxt if nxt is not None and int(nxt) > start_us else start_us + 2_000_000
        words.append({"token": token, "source_start_us": start_us, "source_end_us": max(int(raw_end), start_us + 1)})
    return words


class _Sentence(TypedDict):
    start_ms: int
    end_ms: int
    text: str
    n_words: int
    terminal: bool
    gap_before_ms: float
    gap_after_ms: float


_TRAILING_QUOTES = "\"'”’"


def _word_gap_ms(words: list[dict], index: int) -> float:
    """Return the silence before a word, measured from the source start if first."""
    if index == len(words):
        return 0.0
    previous_end = words[index - 1]["source_end_us"] if index else 0
    return max(0.0, (words[index]["source_start_us"] - previous_end) / 1000)


def build_sentences(words: list[dict], pause_ms: int = 300) -> list[_Sentence]:
    """Split time-ordered CaptionWords at punctuation, pauses, or the run limit."""
    sentences: list[_Sentence] = []

    def emit(start: int, stop: int) -> None:
        tokens = [word["token"] for word in words[start:stop]]
        sentences.append({
            "start_ms": round(words[start]["source_start_us"] / 1000),
            "end_ms": round(words[stop - 1]["source_end_us"] / 1000),
            "text": " ".join(tokens),
            "n_words": stop - start,
            "terminal": tokens[-1].rstrip(_TRAILING_QUOTES).endswith((".", "?", "!")),
            "gap_before_ms": _word_gap_ms(words, start),
            "gap_after_ms": _word_gap_ms(words, stop),
        })

    start = 0
    for index, word in enumerate(words):
        if index > start and (
            words[index - 1]["token"].rstrip(_TRAILING_QUOTES).endswith((".", "?", "!"))
            or _word_gap_ms(words, index) >= pause_ms
        ):
            emit(start, index)
            start = index
        while index > start and (
            index - start >= 60 or word["source_end_us"] - words[start]["source_start_us"] > 25_000_000
        ):
            split = next(
                (stop for stop in range(index, start, -1)
                 if words[stop - 1]["token"].rstrip(_TRAILING_QUOTES).endswith(",")),
                start + (index - start + 1) // 2,
            )
            emit(start, split)
            start = split
    if start < len(words):
        emit(start, len(words))
    return sentences


def audio_energy_peaks(video_path: str, top_n: int = 30) -> List[Dict[str, float]]:
    """Compute per-second audio RMS levels via FFmpeg astats/ametadata.

    Returns:
        List of [{"t_sec": float, "rms_db": float}] for local maxima above the median.
        Never raises: on any failure (missing file, FFmpeg error), returns [].
    """
    if not video_path or not os.path.exists(video_path):
        return []

    try:
        cmd = [
            "ffmpeg", "-v", "error",
            "-i", video_path,
            "-vn",
            "-af", "aresample=16000,asetnsamples=n=16000,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
            "-f", "null", "-",
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
        if res.returncode != 0:
            return []

        samples: List[Tuple[float, float]] = []
        cur_time: Optional[float] = None

        time_re = re.compile(r"pts_time:([0-9.]+)")
        rms_re = re.compile(r"lavfi\.astats\.Overall\.RMS_level=([-\d.]+|-?inf)")

        for line in res.stdout.splitlines():
            t_match = time_re.search(line)
            if t_match:
                try:
                    cur_time = float(t_match.group(1))
                except ValueError:
                    cur_time = None
            r_match = rms_re.search(line)
            if r_match and cur_time is not None:
                val_str = r_match.group(1)
                try:
                    rms_val = -90.0 if "inf" in val_str else float(val_str)
                    samples.append((cur_time, rms_val))
                except ValueError:
                    pass
                cur_time = None

        if not samples:
            return []

        rms_values = sorted(s[1] for s in samples)
        median_rms = rms_values[len(rms_values) // 2]

        peaks: List[Dict[str, float]] = []
        n = len(samples)
        for i in range(n):
            t, rms = samples[i]
            if rms <= median_rms:
                continue
            is_max = True
            if i > 0 and samples[i - 1][1] > rms:
                is_max = False
            if i < n - 1 and samples[i + 1][1] > rms:
                is_max = False
            if is_max:
                peaks.append({"t_sec": round(t, 2), "rms_db": round(rms, 2)})

        # Sort by loudest RMS descending, take top_n, and return in chronological order
        peaks.sort(key=lambda p: p["rms_db"], reverse=True)
        peaks = peaks[:top_n]
        peaks.sort(key=lambda p: p["t_sec"])
        return peaks

    except Exception:
        return []


def parse_manual_peaks(peaks: List[Any], duration_s: float) -> List[Dict[str, float]]:
    """Clamp manual and heatmap peak timestamps and preserve normalized values."""
    if not peaks or duration_s <= 0:
        return []

    valid_peaks: List[Dict[str, float]] = []
    for p in peaks:
        try:
            val = float(p.get("t_sec") if isinstance(p, dict) else p)
            if not math.isfinite(val):
                continue
            clamped = max(0.0, min(float(duration_s), val))
            peak = {"t_sec": round(clamped, 3)}
            if isinstance(p, dict) and "value" in p:
                value = float(p["value"])
                if not math.isfinite(value):
                    continue
                peak["value"] = _clamp01(value)
            valid_peaks.append(peak)
        except (ValueError, TypeError):
            continue

    return valid_peaks


def extract_media_peaks(video_path: str) -> tuple[list[dict], list[dict]]:
    """Run both detectors with independent failures and a bounded scene wait."""
    pool = ThreadPoolExecutor(max_workers=2)
    deadline = time.monotonic() + SCENE_TIMEOUT_SECONDS
    audio = pool.submit(audio_energy_peaks, video_path)
    scene = pool.submit(scene_cut_peaks, video_path)
    try:
        try:
            energy_peaks = audio.result()
        except Exception as exc:
            logger.warning("Audio peak extraction failed: %s", exc)
            energy_peaks = []
        try:
            scene_peaks = scene.result(timeout=max(0, deadline - time.monotonic()))
        except Exception as exc:
            logger.warning("Scene cut extraction failed or timed out: %r", exc)
            scene_peaks = []
        return energy_peaks, scene_peaks
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def scene_cut_peaks(video_path: str, top_n: int = 40) -> list[dict]:
    """Sample local frames for scene changes, returning [] on any read failure."""
    try:
        if top_n <= 0 or not os.path.isfile(video_path):
            return []
        deadline = time.monotonic() + SCENE_TIMEOUT_SECONDS
        samples: list[tuple[float, float]] = []
        capture = cv2.VideoCapture(video_path)
        try:
            if not capture.isOpened():
                return []
            fps = capture.get(cv2.CAP_PROP_FPS)
            if not math.isfinite(fps) or fps <= 0:
                fps = 30.0
            frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
            long_video_step = math.ceil(frame_count / 1200) if math.isfinite(frame_count) else 1
            step = max(1, round(fps), long_video_step)
            previous = None
            frame_index = 0
            while len(samples) < 1199 and capture.grab():
                if time.monotonic() >= deadline:
                    return []
                if frame_index % step == 0:
                    ok, frame = capture.retrieve()
                    if not ok or frame is None:
                        return []
                    timestamp = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                    if not math.isfinite(timestamp) or timestamp <= 0:
                        timestamp = frame_index / fps
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    height, width = gray.shape
                    if width > 320:
                        size = (320, max(1, round(height * 320 / width)))
                        gray = cv2.resize(gray, size, interpolation=cv2.INTER_AREA)
                    if previous is not None:
                        diff = cv2.mean(cv2.absdiff(gray, previous))[0] / 255
                        samples.append((timestamp, diff))
                    previous = gray
                frame_index += 1
        finally:
            capture.release()

        if not samples:
            return []
        diffs = [diff for _, diff in samples]
        percentile_90 = quantiles(diffs, n=10, method="inclusive")[-1] if len(diffs) > 1 else diffs[0]
        peaks = [
            (timestamp, diff) for index, (timestamp, diff) in enumerate(samples)
            if diff > 0.5 * percentile_90
            and (index == 0 or diff >= diffs[index - 1])
            and (index == len(diffs) - 1 or diff >= diffs[index + 1])
        ]
        strongest = sorted(peaks, key=lambda peak: peak[1], reverse=True)[:top_n]
        return [
            {"t_sec": round(timestamp, 2), "score": _clamp01(diff / 0.5)}
            for timestamp, diff in sorted(strongest)
        ]
    except Exception:
        return []


def rank_candidates(
    cands: Sequence[Union[CandidateClip, Dict[str, Any]]],
    audience_weight: float = 0.10,
    weights: Optional[Dict[str, float]] = None,
) -> List[CandidateClip]:
    """Rank candidate clips by composite scoring with automatic weight re-normalization.

    Defaults:
        editorial (ai_quality): 0.35
        informativeness: 0.25
        standalone (standalone_clarity): 0.20
        audio (audio_clarity): 0.10
        audience (replay_signal): audience_weight (default 0.10)

    When replay_signal is None, audience weight is omitted and remaining weights
    are re-normalized to sum to 1.0. Returns candidates sorted by final_score descending.
    """
    if not cands:
        return []

    base_weights = {
        "editorial": 0.35,
        "informativeness": 0.25,
        "standalone": 0.20,
        "audio": 0.10,
        "audience": audience_weight,
    }
    if weights:
        if "ai_quality" in weights:
            base_weights["editorial"] = weights["ai_quality"]
        elif "editorial" in weights:
            base_weights["editorial"] = weights["editorial"]
        if "informativeness" in weights:
            base_weights["informativeness"] = weights["informativeness"]
        if "standalone_clarity" in weights:
            base_weights["standalone"] = weights["standalone_clarity"]
        elif "standalone" in weights:
            base_weights["standalone"] = weights["standalone"]
        if "audio_clarity" in weights:
            base_weights["audio"] = weights["audio_clarity"]
        elif "audio" in weights:
            base_weights["audio"] = weights["audio"]
        if "replay_signal" in weights:
            base_weights["audience"] = weights["replay_signal"]
        elif "audience" in weights:
            base_weights["audience"] = weights["audience"]

    ranked: List[CandidateClip] = []
    for item in cands:
        c = item.model_copy() if isinstance(item, CandidateClip) else CandidateClip(**item)

        if c.replay_signal is not None:
            w_sum = (
                base_weights["editorial"] +
                base_weights["informativeness"] +
                base_weights["standalone"] +
                base_weights["audio"] +
                base_weights["audience"]
            )
            w_sum = w_sum if w_sum > 0 else 1.0
            score = (
                (base_weights["editorial"] * c.ai_quality) +
                (base_weights["informativeness"] * c.informativeness) +
                (base_weights["standalone"] * c.standalone_clarity) +
                (base_weights["audio"] * c.audio_clarity) +
                (base_weights["audience"] * c.replay_signal)
            ) / w_sum
        else:
            w_sum = (
                base_weights["editorial"] +
                base_weights["informativeness"] +
                base_weights["standalone"] +
                base_weights["audio"]
            )
            w_sum = w_sum if w_sum > 0 else 1.0
            score = (
                (base_weights["editorial"] * c.ai_quality) +
                (base_weights["informativeness"] * c.informativeness) +
                (base_weights["standalone"] * c.standalone_clarity) +
                (base_weights["audio"] * c.audio_clarity)
            ) / w_sum

        c.final_score = round(max(0.0, min(100.0, score * 100.0)), 1)
        ranked.append(c)

    ranked.sort(key=lambda x: x.final_score, reverse=True)
    return ranked


def dedupe_overlaps(
    cands: Sequence[Union[CandidateClip, Dict[str, Any]]],
    min_gap_ms: int = 2000,
) -> List[CandidateClip]:
    """Greedy deduplication by final_score, discarding overlapping candidate clips."""
    if not cands:
        return []

    clip_objs: List[CandidateClip] = [
        item if isinstance(item, CandidateClip) else CandidateClip(**item)
        for item in cands
    ]
    clip_objs.sort(key=lambda c: c.final_score, reverse=True)

    kept: List[CandidateClip] = []
    for c in clip_objs:
        overlap = False
        for k in kept:
            # Overlap test taking min_gap_ms separation into account
            if max(c.start_ms, k.start_ms) < min(c.end_ms, k.end_ms) + min_gap_ms:
                overlap = True
                break
        if not overlap:
            kept.append(c)

    return kept


HOOK_KEYWORDS = [
    "never", "always", "nobody", "everyone", "secret", "shocking", "huge", "crazy",
    "listen", "look at", "here's why", "literally", "impossible", "biggest", "worst", "stop", "wait",
]
_HOOK_PATTERN = re.compile(r"\b(?:" + "|".join(map(re.escape, HOOK_KEYWORDS)) + r")\b", re.IGNORECASE)
_DANGLING_STARTS = {"and", "but", "so", "or", "then", "because", "um", "uh", "like", "yeah"}
_HEADLINE_FILLERS = {"um", "uh", "like", "yeah", "so"}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _bare_token(token: str) -> str:
    return token.strip(punctuation + "“”‘’").lower()


def _hook_score(words: list[dict], sentences: list[_Sentence], start_ms: int) -> float:
    if not words:
        return 0.0
    first = sentences[0]["text"].replace("’", "'")
    keyword = any(_HOOK_PATTERN.search(sentence["text"].replace("’", "'")) for sentence in sentences[:2])
    opener = 0.6
    if _bare_token(words[0]["token"]) in _DANGLING_STARTS:
        opener = 0.0
    elif "?" in first or first.rstrip(_TRAILING_QUOTES).endswith("!") or _HOOK_PATTERN.search(first):
        opener = 1.0
    opening_words = sum(word["source_start_us"] < (start_ms + 3000) * 1000 for word in words)
    return _clamp01(0.40 * opener + 0.35 * min(1.0, opening_words / 12) + 0.25 * keyword)


def _truncate_on_word(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    prefix = text[:limit]
    if text[limit].isspace() or prefix[-1].isspace():
        return prefix.rstrip()
    return prefix.rpartition(" ")[0] or prefix


def _window_for_anchor(
    sentences: list[_Sentence], starts: list[int], clear_edges: set[int], anchor: int,
    duration_ms: int, min_ms: int, max_ms: int,
) -> tuple[int, int] | None:
    """Choose sentence indices, favoring windows that center the anchor."""
    left = bisect_left(starts, anchor - 0.75 * max_ms)
    right = bisect_right(starts, anchor)
    own = right - 1 if right > left and sentences[right - 1]["end_ms"] >= anchor else None
    start_indices = sorted(
        (index for index in range(left, right)
         if starts[index] in clear_edges and (sentences[index]["gap_before_ms"] >= 300 or index == own)),
        key=lambda index: sentences[index]["gap_before_ms"], reverse=True,
    )[:2]
    if own is not None and starts[own] in clear_edges and own not in start_indices:
        start_indices.append(own)

    target = (min_ms + max_ms) / 2
    best = None
    best_score = -math.inf
    for first in start_indices:
        start = starts[first]
        end_limit = min(duration_ms, start + max_ms)
        right = bisect_right(starts, end_limit)
        end_scores = []
        for last in range(first, right):
            sentence = sentences[last]
            end = sentence["end_ms"]
            if end <= anchor or end not in clear_edges or not start + min_ms <= end <= end_limit:
                continue
            score = (
                sentence["terminal"] + min(1.0, sentence["gap_after_ms"] / 800)
                - 2.0 * abs(end - start - target) / max_ms
            )
            end_scores.append((last, score))
        if not end_scores:
            continue
        last, score = max(end_scores, key=lambda entry: entry[1])
        position = (anchor - start) / (sentences[last]["end_ms"] - start)
        if 0.25 <= position <= 0.85:
            return first, last
        if score > best_score:
            best = first, last
            best_score = score
    return best


def _score_local_window(
    start: int, end: int, words: list[dict], sentences: list[_Sentence], first_word_us: int | None,
    energy: float | None, scene: float | None, replay: float | None, has_audience: bool, warnings: list[str],
) -> tuple[CandidateClip, float]:
    signals = [(score, weight) for score, weight in ((energy, 0.5), (scene, 0.3), (replay, 0.2)) if score is not None]
    engagement = (
        sum(score * weight for score, weight in signals) / sum(weight for _, weight in signals)
        if signals else 0.0
    )
    hook_score = _hook_score(words, sentences, start)
    coherence = audio_clarity = 0.0
    hook = summary = ""
    headline = "Audio peak" if energy is not None else "Audience peak"
    why = "Includes a measured audio energy peak." if energy is not None else "Includes an audience peak."
    window_ms = end - start
    wps = len(words) / (window_ms / 1000)
    speed = _clamp01(wps / 2.5)
    if words:
        first, last = sentences[0], sentences[-1]
        coverage = _clamp01((words[-1]["source_end_us"] - words[0]["source_start_us"]) / (window_ms * 1000))
        start_ok = first["gap_before_ms"] >= 300 or words[0]["source_start_us"] == first_word_us
        dangling_start = _bare_token(words[0]["token"]) in _DANGLING_STARTS
        end_ok = last["terminal"]
        dangling_end = not end_ok and last["gap_after_ms"] < 400
        coherence = _clamp01(
            0.25 + 0.15 * start_ok - 0.30 * dangling_start + 0.15 * end_ok - 0.30 * dangling_end
            + 0.10 * speed + 0.10 * speed * coverage
        )
        confidences = [word.get("confidence") for word in words]
        mean_confidence = sum(0.7 if value is None else value for value in confidences) / len(words)
        audio_clarity = _clamp01(0.5 * coverage + 0.5 * mean_confidence)
        opening = first["text"].split()
        first_content = next((i for i, token in enumerate(opening) if _bare_token(token) not in _HEADLINE_FILLERS), 0)
        headline = _truncate_on_word(" ".join(opening[first_content:]), 60)
        hook = " ".join(opening[:15])
        summary = last["text"][:200]
        why = f"Opens after a {first['gap_before_ms']:g}ms pause and lands on a complete phrase"
        if energy is not None:
            why += " with loud-reaction energy."
        if replay:
            why += "; audience peak inside."

    return CandidateClip(
        id=f"local-{start}-{end}", start_ms=start, end_ms=end,
        headline=headline, hook=hook, summary=summary, why_this_works=why,
        ai_quality=hook_score, informativeness=_clamp01(0.6 * speed + 0.4 * coherence),
        standalone_clarity=coherence, audio_clarity=audio_clarity, replay_signal=replay,
        signal_source="manual_peaks" if has_audience else "local_heuristic", confidence=0.45,
        suggested_layout="auto_hybrid", suggested_subtitle_preset="hormozi-glow",
        warnings=warnings,
    ), engagement


def generate_local_candidates(
    words: list[dict], energy_peaks: list[dict], duration_ms: int, num_clips: int | None = 5,
    min_ms: int = 30000, max_ms: int = 60000, scene_peaks: list[dict] | None = None,
    heatmap_peaks: list[dict] | None = None, audience_weight: float = 0.10,
    chat_peaks: list[dict] | None = None, audience_source: str = "manual_peaks",
    all_clips: bool = False,
) -> dict:
    """Rank sentence-aligned clips locally, with flagged bounds for sparse speech."""
    result = {"candidates": [], "dropped": 0, "prompt_version": "local-v1", "model": "local-engine"}
    if not 0 < min_ms <= min(max_ms, duration_ms) or (num_clips is not None and num_clips <= 0 and not all_clips):
        return result

    words = sorted(
        (word for word in words if word.get("token", "").strip()
         and 0 <= word["source_start_us"] < min(word["source_end_us"], duration_ms * 1000)),
        key=lambda word: word["source_start_us"],
    )
    sentences = build_sentences(words)
    starts = [sentence["start_ms"] for sentence in sentences]
    word_starts = [round(word["source_start_us"] / 1000) for word in words]
    furthest_ends = list(accumulate((round(word["source_end_us"] / 1000) for word in words), max))
    edges = set(starts) | {sentence["end_ms"] for sentence in sentences}
    clear_edges = {
        edge for edge in edges
        if (index := bisect_left(word_starts, edge)) == 0 or furthest_ends[index - 1] <= edge
    }
    offsets = list(accumulate((sentence["n_words"] for sentence in sentences), initial=0))
    energy = [
        (round(peak["t_sec"] * 1000), peak["rms_db"]) for peak in energy_peaks
        if 0 <= peak["t_sec"] * 1000 <= duration_ms and math.isfinite(peak["rms_db"])
    ]
    scene = [
        (round(peak["t_sec"] * 1000), _clamp01(peak["score"])) for peak in scene_peaks or []
        if 0 <= peak["t_sec"] * 1000 <= duration_ms and math.isfinite(peak["score"])
    ]
    def audience_signal(peaks: list[dict] | None) -> list[tuple[int, float]]:
        return [
            (round(peak["t_sec"] * 1000), _clamp01(peak.get("value", 1.0))) for peak in peaks or []
            if 0 <= peak["t_sec"] * 1000 <= duration_ms and math.isfinite(peak.get("value", 1.0))
        ]

    audience = audience_signal(heatmap_peaks)
    chat = audience_signal(chat_peaks)

    def sentence_hook(index: int) -> float:
        stop = min(len(sentences), index + 2)
        return _hook_score(words[offsets[index]:offsets[stop]], sentences[index:stop], starts[index])

    hooks = sorted(
        (index for index, sentence in enumerate(sentences) if sentence["gap_before_ms"] >= 300),
        key=sentence_hook, reverse=True,
    )[:5]
    anchors = [timestamp for timestamp, _ in sorted(energy, key=lambda peak: peak[1], reverse=True)[:15]]
    anchors.extend(timestamp for timestamp, _ in audience + chat)
    anchors.extend(starts[index] for index in hooks)
    if not anchors and starts:
        anchors.append(starts[0])

    scored = []
    for anchor in dict.fromkeys(anchors):
        warnings = []
        window = _window_for_anchor(sentences, starts, clear_edges, anchor, duration_ms, min_ms, max_ms)
        if window is not None:
            first, last = window
            start, end = starts[first], sentences[last]["end_ms"]
            window_words = words[offsets[first]:offsets[last + 1]]
            window_sentences = sentences[first:last + 1]
        else:
            start = max(0, anchor - round(0.4 * min_ms))
            end = min(duration_ms, start + min_ms)
            start = max(0, end - min_ms)
            left, right = bisect_left(word_starts, start), bisect_left(word_starts, end)
            window_words = [word for word in words[left:right] if round(word["source_end_us"] / 1000) <= end]
            window_sentences = build_sentences(window_words)
            if window_sentences:
                window_sentences[0]["gap_before_ms"] = _word_gap_ms(words, left)
                window_sentences[-1]["gap_after_ms"] = _word_gap_ms(words, right)
            warnings.append("synthetic-bounds")

        if not 0 <= start < end <= duration_ms or not min_ms <= end - start <= max_ms:
            result["dropped"] += 1
            continue
        if not 0.25 <= (anchor - start) / (end - start) <= 0.85:
            warnings.append("peak-position")
        rms = max((value for timestamp, value in energy if start <= timestamp <= end), default=None)
        replay = max((value for timestamp, value in audience if start <= timestamp <= end), default=None)
        chat_peak = max((value for timestamp, value in chat if start <= timestamp <= end), default=None)
        engagement_peak = max((value for value in (replay, chat_peak) if value is not None), default=None)
        if not window_words and rms is None and engagement_peak is None:
            result["dropped"] += 1
            continue
        candidate, engagement = _score_local_window(
            start, end, window_words, window_sentences, words[0]["source_start_us"] if words else None,
            energy=None if rms is None else _clamp01((rms + 48) / 48),
            scene=max((value for timestamp, value in scene if start <= timestamp <= end), default=None),
            replay=engagement_peak, has_audience=bool(audience or chat), warnings=warnings,
        )
        if chat_peak is not None:
            candidate.signal_source = "youtube_chat_replay" if replay is None else "youtube_replay_and_chat"
            if not window_words and rms is None and replay is None:
                candidate.headline = "Livestream chat spike"
                candidate.why_this_works = "Chat message volume spiked in this window."
            else:
                candidate.why_this_works += " Chat message volume also spiked in this window."
        elif replay is not None:
            candidate.signal_source = audience_source
            if audience_source == "youtube_replay" and not window_words and rms is None:
                candidate.headline = "Most-replayed moment"
                candidate.why_this_works = "Includes a peak from YouTube’s Most Replayed graph."
        scored.append((candidate, engagement))

    # Engagement breaks score ties without altering the default ranking weights.
    scored.sort(key=lambda item: item[1], reverse=True)
    ranked = rank_candidates([candidate for candidate, _ in scored], audience_weight=audience_weight)
    deduped = dedupe_overlaps(ranked, min_gap_ms=2000)
    result["candidates"] = deduped if (all_clips or num_clips is None or num_clips <= 0) else deduped[:num_clips]
    result["dropped"] += len(ranked) - len(result["candidates"])
    return result


def local_highlights(
    words: list[dict], energy_peaks: list[dict], duration_s: float,
    min_duration: float, max_duration: float, num_clips: int,
    all_clips: bool = False,
) -> list[dict]:
    """Adapt local discovery candidates to the generation timeline shape."""
    result = generate_local_candidates(
        words, energy_peaks, int(duration_s * 1000), num_clips,
        math.ceil(min_duration * 1000), math.floor(max_duration * 1000),
        all_clips=all_clips,
    )
    return [
        {"id": clip.id, "start": clip.start_ms / 1000, "end": clip.end_ms / 1000,
         "title": clip.headline, "origin": "local"}
        for clip in result["candidates"]
    ]


def clamp_windows(
    cands: Sequence[Union[CandidateClip, Dict[str, Any]]],
    duration_ms: int,
    min_ms: int = 30000,
    max_ms: int = 60000,
) -> List[CandidateClip]:
    """Enforce clip duration within [min_ms, max_ms] and media boundaries [0, duration_ms].

    Expands or shrinks windows symmetrically. Drops candidates that cannot satisfy min_ms.
    """
    if not cands or duration_ms < min_ms:
        return []

    clamped: List[CandidateClip] = []
    for item in cands:
        c = item if isinstance(item, CandidateClip) else CandidateClip(**item)
        start = max(0, min(c.start_ms, duration_ms))
        end = max(0, min(c.end_ms, duration_ms))
        if end <= start:
            continue

        length = end - start

        # Shrink symmetrically if exceeding max_ms
        if length > max_ms:
            excess = length - max_ms
            shrink_left = excess // 2
            shrink_right = excess - shrink_left
            start += shrink_left
            end -= shrink_right

        # Expand symmetrically if below min_ms
        elif length < min_ms:
            deficit = min_ms - length
            expand_left = deficit // 2
            expand_right = deficit - expand_left
            start -= expand_left
            end += expand_right

            if start < 0:
                end += -start
                start = 0
            if end > duration_ms:
                start -= (end - duration_ms)
                end = duration_ms
            start = max(0, start)

        final_len = end - start
        if final_len >= min_ms:
            c_copy = c.model_copy()
            c_copy.start_ms = int(start)
            c_copy.end_ms = int(end)
            clamped.append(c_copy)

    return clamped


def cache_key(
    source_hash: str,
    model: str,
    prompt_version: str,
    settings_dict: Dict[str, Any],
) -> str:
    """Generate deterministic SHA256 hex cache key for discovery settings and source."""
    serialized = json.dumps(settings_dict, sort_keys=True)
    payload = f"{source_hash}:{model}:{prompt_version}:{serialized}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
