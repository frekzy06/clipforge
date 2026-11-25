"""Unit and regression tests for TimelineMap and caption word mapping."""
import pytest
from services.api.timeline_map import TimelineMap, TimelineSpan, CaptionWord, CaptionGroup


def test_timeline_span_math():
    span = TimelineSpan.from_seconds("s1", 2.5, 7.5, "Span 1")
    assert span.source_start_us == 2_500_000
    assert span.source_end_us == 7_500_000
    assert span.duration_us == 5_000_000


def test_timeline_map_point_mapping():
    # Two cuts: 0.0 - 5.0 (5s) and 10.0 - 15.0 (5s). Total output duration: 10s.
    tmap = TimelineMap.from_seconds_list([
        {"id": "c1", "start": 0.0, "end": 5.0},
        {"id": "c2", "start": 10.0, "end": 15.0},
    ])
    assert tmap.total_duration_sec == 10.0

    # Inside span 1
    assert tmap.map_source_point_to_output(2_000_000) == 2_000_000  # 2.0s -> 2.0s

    # Cut out between spans (e.g. 7.0s)
    assert tmap.map_source_point_to_output(7_000_000) is None

    # Inside span 2 (e.g. 12.0s source should map to 5s + 2s = 7s output)
    assert tmap.map_source_point_to_output(12_000_000) == 7_000_000


def test_timeline_map_word_mapping():
    # Spans: [1.0s - 4.0s] and [8.0s - 12.0s]
    tmap = TimelineMap.from_seconds_list([
        {"id": "c1", "start": 1.0, "end": 4.0},
        {"id": "c2", "start": 8.0, "end": 12.0},
    ])

    words = [
        # Word 1: 0.0s - 0.5s (before span 1 -> dropped)
        CaptionWord(token="Hey", source_start_us=0, source_end_us=500_000),
        # Word 2: 1.2s - 1.8s (inside span 1 -> output: 0.2s - 0.8s)
        CaptionWord(token="welcome", source_start_us=1_200_000, source_end_us=1_800_000),
        # Word 3: 3.5s - 4.5s (straddles cut at 4.0s -> clamped to 3.5s - 4.0s -> output: 2.5s - 3.0s)
        CaptionWord(token="everyone", source_start_us=3_500_000, source_end_us=4_500_000),
        # Word 4: 5.0s - 6.0s (in cut area -> dropped)
        CaptionWord(token="skipme", source_start_us=5_000_000, source_end_us=6_000_000),
        # Word 5: 9.0s - 9.5s (in span 2 -> span 1 is 3s, so offset in span 2 is 1.0s -> output: 4.0s - 4.5s)
        CaptionWord(token="today", source_start_us=9_000_000, source_end_us=9_500_000),
    ]

    mapped = tmap.map_words(words)
    tokens = [w.token for w in mapped]
    assert tokens == ["welcome", "everyone", "today"]

    # Check timings
    assert mapped[0].output_start_us == 200_000
    assert mapped[0].output_end_us == 800_000

    assert mapped[1].output_start_us == 2_500_000
    assert mapped[1].output_end_us == 3_000_000  # Clamped to cut boundary!

    assert mapped[2].output_start_us == 4_000_000
    assert mapped[2].output_end_us == 4_500_000


def test_caption_grouping():
    words = [
        CaptionWord(token="Never", source_start_us=0, source_end_us=500_000, output_start_us=0, output_end_us=500_000),
        CaptionWord(token="give", source_start_us=500_000, source_end_us=900_000, output_start_us=500_000, output_end_us=900_000),
        CaptionWord(token="up.", source_start_us=900_000, source_end_us=1_400_000, output_start_us=900_000, output_end_us=1_400_000),
        CaptionWord(token="Keep", source_start_us=2_000_000, source_end_us=2_400_000, output_start_us=2_000_000, output_end_us=2_400_000),
        CaptionWord(token="going!", source_start_us=2_400_000, source_end_us=2_900_000, output_start_us=2_400_000, output_end_us=2_900_000),
    ]

    groups = TimelineMap.group_words_into_captions(words, max_words=4)
    assert len(groups) == 2
    assert groups[0].text == "Never give up."
    assert groups[0].output_start_us == 0
    assert groups[0].output_end_us == 1_400_000

    assert groups[1].text == "Keep going!"
    assert groups[1].output_start_us == 2_000_000
    assert groups[1].output_end_us == 2_900_000


def test_cut_inside_a_word_clamps_cleanly():
    """A cut in the middle of a word must clamp, not crash or invert."""
    word = CaptionWord(token="hello", source_start_us=1_000_000, source_end_us=1_500_000)
    # Cut starts in the middle of the word and ends after it.
    tmap = TimelineMap.from_seconds_list([
        {"id": "a", "start": 1.2, "end": 3.0},
    ])
    mapped = tmap.map_words([word])
    assert len(mapped) == 1
    w = mapped[0]
    # Clamped to the part of the word that survives the cut.
    assert w.output_start_us == 0
    assert w.output_end_us == 300_000
    assert w.output_end_us > w.output_start_us


def test_word_entirely_outside_the_cut_is_dropped():
    word = CaptionWord(token="gone", source_start_us=5_000_000, source_end_us=5_500_000)
    tmap = TimelineMap.from_seconds_list([{"id": "a", "start": 0.0, "end": 2.0}])
    assert tmap.map_words([word]) == []


def test_mapped_words_never_have_negative_duration():
    """Every mapped word must have end > start, even across odd cuts."""
    words = [
        CaptionWord(token="a", source_start_us=0, source_end_us=900_000),
        CaptionWord(token="b", source_start_us=900_000, source_end_us=1_100_000),
        CaptionWord(token="c", source_start_us=1_100_000, source_end_us=2_000_000),
    ]
    tmap = TimelineMap.from_seconds_list([
        {"id": "x", "start": 0.3, "end": 0.75},
        {"id": "y", "start": 1.5, "end": 1.9},
    ])
    for w in tmap.map_words(words):
        assert w.output_end_us > w.output_start_us
        assert w.output_start_us >= 0


def test_caption_groups_from_mapped_words_are_ordered_and_valid():
    """Groups must be ordered and non-overlapping for a safe ASS file."""
    words = [
        CaptionWord(token="one", source_start_us=0, source_end_us=400_000),
        CaptionWord(token="two", source_start_us=400_000, source_end_us=800_000),
        CaptionWord(token="three", source_start_us=800_000, source_end_us=1_400_000),
    ]
    tmap = TimelineMap.from_seconds_list([{"id": "a", "start": 0.0, "end": 2.0}])
    mapped = tmap.map_words(words)
    groups = TimelineMap.group_words_into_captions(mapped)
    assert groups
    prev_end = -1
    for g in groups:
        assert g.output_end_us > g.output_start_us
        assert g.output_start_us >= prev_end
        prev_end = g.output_end_us
        assert g.text.strip()


def test_caption_grouping_modes():
    """Verify all 4 caption grouping modes ('1-word', 'chunk', 'phrase', 'sentence')."""
    raw_tokens = ["Never", "give", "up.", "Keep", "pushing", "forward", "every", "single", "day", "until", "you", "win!"]
    words = []
    t = 0
    for tok in raw_tokens:
        words.append(CaptionWord(
            token=tok,
            source_start_us=t,
            source_end_us=t + 300_000,
            output_start_us=t,
            output_end_us=t + 300_000,
        ))
        t += 350_000  # 50ms pause between words

    # 1. '1-word' mode: exactly 1 word per group
    groups_1word = TimelineMap.group_words_into_captions(words, mode="1-word")
    assert len(groups_1word) == len(words)
    assert [g.text for g in groups_1word] == raw_tokens
    for g in groups_1word:
        assert len(g.words) == 1

    # 2. 'chunk' mode: 2-4 words per group, split on punctuation (. ! ?)
    groups_chunk = TimelineMap.group_words_into_captions(words, mode="chunk")
    assert groups_chunk[0].text == "Never give up."
    for g in groups_chunk:
        assert len(g.words) <= 4

    # 3. 'phrase' mode: up to 8 words per group
    groups_phrase = TimelineMap.group_words_into_captions(words, mode="phrase")
    # First sentence "Never give up." has punctuation so it splits there
    assert groups_phrase[0].text == "Never give up."
    # Second phrase can fit up to 8 words
    assert len(groups_phrase[1].words) <= 8

    # 4. 'sentence' mode: splits on sentence end punctuation (. ! ?)
    groups_sentence = TimelineMap.group_words_into_captions(words, mode="sentence")
    assert len(groups_sentence) == 2
    assert groups_sentence[0].text == "Never give up."
    assert groups_sentence[1].text == "Keep pushing forward every single day until you win!"
