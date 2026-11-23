"""Deterministic source-to-output timeline map and caption word models.

Consistently represents timeline/edit durations in integer microseconds (us)
per REQUIREMENTS §6:
    The source-to-output timeline map is the single truth for cutting,
    reordering, speed changes and subtitle remapping.

    Every word:
        word_id, token, source_start_us, source_end_us,
        output_start_us, output_end_us, speaker_id nullable,
        confidence nullable, caption_group_id, manual_override.
"""
from __future__ import annotations

import math
import uuid
from typing import List, Optional, Sequence, Tuple
from pydantic import BaseModel, Field


class TimelineSpan(BaseModel):
    """A slice of source media mapped to the final output timeline.
    All times in microseconds (us).
    """
    id: str
    source_start_us: int
    source_end_us: int
    title: Optional[str] = None

    @property
    def duration_us(self) -> int:
        return max(0, self.source_end_us - self.source_start_us)

    @classmethod
    def from_seconds(cls, id: str, start_sec: float, end_sec: float, title: Optional[str] = None) -> TimelineSpan:
        start_us = int(round(start_sec * 1_000_000))
        end_us = int(round(end_sec * 1_000_000))
        return cls(id=id, source_start_us=start_us, source_end_us=end_us, title=title)


class CaptionWord(BaseModel):
    """Canonical model for a single aligned spoken word."""
    word_id: str = Field(default_factory=lambda: f"w-{uuid.uuid4().hex[:8]}")
    token: str
    source_start_us: int
    source_end_us: int
    output_start_us: Optional[int] = None
    output_end_us: Optional[int] = None
    speaker_id: Optional[str] = None
    confidence: Optional[float] = None
    caption_group_id: Optional[str] = None
    manual_override: bool = False

    @property
    def duration_us(self) -> int:
        return max(0, self.source_end_us - self.source_start_us)

    @property
    def source_start_sec(self) -> float:
        return self.source_start_us / 1_000_000.0

    @property
    def source_end_sec(self) -> float:
        return self.source_end_us / 1_000_000.0

    @property
    def output_start_sec(self) -> Optional[float]:
        return self.output_start_us / 1_000_000.0 if self.output_start_us is not None else None

    @property
    def output_end_sec(self) -> Optional[float]:
        return self.output_end_us / 1_000_000.0 if self.output_end_us is not None else None


class CaptionGroup(BaseModel):
    """A grouped display chunk of words (e.g. 2-5 words or 1 phrase) for on-screen display."""
    id: str = Field(default_factory=lambda: f"cg-{uuid.uuid4().hex[:8]}")
    text: str
    output_start_us: int
    output_end_us: int
    words: List[CaptionWord] = Field(default_factory=list)

    @property
    def output_start_sec(self) -> float:
        return self.output_start_us / 1_000_000.0

    @property
    def output_end_sec(self) -> float:
        return self.output_end_us / 1_000_000.0


class TimelineMap:
    """Manages ordered timeline spans and maps words from source to output."""

    def __init__(self, spans: Sequence[TimelineSpan]):
        # Keep non-empty spans in order
        self.spans: List[TimelineSpan] = [s for s in spans if s.source_end_us > s.source_start_us]

    @classmethod
    def from_seconds_list(cls, segments: Sequence[dict]) -> TimelineMap:
        """Helper to build from list of {'id', 'start', 'end'} dicts in seconds."""
        spans = []
        for seg in segments:
            spans.append(TimelineSpan.from_seconds(
                id=str(seg.get("id") or uuid.uuid4().hex[:6]),
                start_sec=float(seg.get("start", 0.0)),
                end_sec=float(seg.get("end", 0.0)),
                title=seg.get("title")
            ))
        return cls(spans)

    @property
    def total_duration_us(self) -> int:
        return sum(s.duration_us for s in self.spans)

    @property
    def total_duration_sec(self) -> float:
        return self.total_duration_us / 1_000_000.0

    def map_source_point_to_output(self, source_us: int) -> Optional[int]:
        """Maps a point in source media to its position on output timeline, or None if cut out."""
        elapsed_output_us = 0
        for span in self.spans:
            if span.source_start_us <= source_us <= span.source_end_us:
                offset_in_span = source_us - span.source_start_us
                return elapsed_output_us + offset_in_span
            elapsed_output_us += span.duration_us
        return None

    def map_words(self, words: Sequence[CaptionWord]) -> List[CaptionWord]:
        """Deterministic mapping of words through the timeline cuts.
        
        Rules:
        - Words entirely outside any active span are omitted.
        - Words spanning across a cut boundary are clipped/clamped cleanly.
        - Preserves word order and guarantees output_end_us >= output_start_us.
        """
        mapped: List[CaptionWord] = []
        elapsed_output_us = 0

        for span in self.spans:
            span_start = span.source_start_us
            span_end = span.source_end_us
            span_dur = span.duration_us

            for w in words:
                # Check overlap between word [w.source_start_us, w.source_end_us] and span [span_start, span_end]
                overlap_start = max(w.source_start_us, span_start)
                overlap_end = min(w.source_end_us, span_end)

                if overlap_end > overlap_start:
                    # Overlaps this span
                    out_start = elapsed_output_us + (overlap_start - span_start)
                    out_end = elapsed_output_us + (overlap_end - span_start)

                    # Create mapped copy
                    mapped_word = w.model_copy()
                    mapped_word.output_start_us = out_start
                    mapped_word.output_end_us = out_end
                    mapped.append(mapped_word)

            elapsed_output_us += span_dur

        return mapped

    @staticmethod
    def group_words_into_captions(
        words: Sequence[CaptionWord],
        max_words: int = 4,
        max_duration_sec: float = 2.5,
        max_pause_sec: float = 0.5,
        mode: str = "chunk",
    ) -> List[CaptionGroup]:
        """Group mapped words into readable caption groups based on segmentation mode.
        
        Supported modes:
        - '1-word': Single word punch / pop (1 word per group).
        - 'chunk': 2-4 words per group (default).
        - 'phrase': 5-8 words per group.
        - 'sentence': Split primarily on end-punctuation (. ? !).
        """
        valid_words = [w for w in words if w.output_start_us is not None and w.output_end_us is not None and w.output_end_us > w.output_start_us]
        if not valid_words:
            return []

        # Sort by output_start_us
        valid_words.sort(key=lambda w: w.output_start_us)

        norm_mode = (mode or "chunk").lower().strip()
        if norm_mode == "1-word":
            effective_max_words = 1
            effective_max_dur_us = int(max(max_duration_sec, 2.0) * 1_000_000)
            effective_max_pause_us = int(max_pause_sec * 1_000_000)
        elif norm_mode == "phrase":
            effective_max_words = max(max_words, 8) if max_words != 4 else 8
            effective_max_dur_us = int(max(max_duration_sec, 5.0) * 1_000_000)
            effective_max_pause_us = int(max(max_pause_sec, 0.8) * 1_000_000)
        elif norm_mode == "sentence":
            effective_max_words = 999
            effective_max_dur_us = int(max(max_duration_sec, 12.0) * 1_000_000)
            effective_max_pause_us = int(max(max_pause_sec, 1.2) * 1_000_000)
        else:  # "chunk"
            effective_max_words = max_words
            effective_max_dur_us = int(max_duration_sec * 1_000_000)
            effective_max_pause_us = int(max_pause_sec * 1_000_000)

        groups: List[CaptionGroup] = []
        cur_chunk: List[CaptionWord] = []

        def flush():
            if not cur_chunk:
                return
            g_id = f"cg-{uuid.uuid4().hex[:8]}"
            text = " ".join(w.token for w in cur_chunk)
            g_start = cur_chunk[0].output_start_us
            g_end = cur_chunk[-1].output_end_us
            # assign group id to words
            words_copy = []
            for w in cur_chunk:
                wc = w.model_copy()
                wc.caption_group_id = g_id
                words_copy.append(wc)
            groups.append(CaptionGroup(
                id=g_id,
                text=text,
                output_start_us=g_start,
                output_end_us=g_end,
                words=words_copy
            ))
            cur_chunk.clear()

        for w in valid_words:
            if not cur_chunk:
                cur_chunk.append(w)
                continue

            prev = cur_chunk[-1]
            pause = w.output_start_us - prev.output_end_us
            group_duration = w.output_end_us - cur_chunk[0].output_start_us
            is_sentence_end = prev.token.rstrip("\"'”’)").endswith((".", "!", "?"))

            if norm_mode == "1-word":
                should_break = True
            elif norm_mode == "sentence":
                should_break = (
                    is_sentence_end or
                    len(cur_chunk) >= effective_max_words or
                    group_duration > effective_max_dur_us or
                    pause > effective_max_pause_us
                )
            else:  # "chunk" or "phrase"
                should_break = (
                    len(cur_chunk) >= effective_max_words or
                    group_duration > effective_max_dur_us or
                    pause > effective_max_pause_us or
                    is_sentence_end
                )

            if should_break:
                flush()
            cur_chunk.append(w)

        flush()
        return groups
