"""Tests for the ClipForge caption style schema and ASS rendering."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from caption_style import (
    CaptionStyle,
    build_ass,
    hex_to_ass_bgr,
    is_rtl_text,
    normalize_hex,
    preview_stroke,
    preview_text_shadow,
    rgba_css,
)
from timeline_map import CaptionGroup, CaptionWord


def test_normalize_hex_accepts_both_forms():
    assert normalize_hex("#FACC15") == "#facc15"
    assert normalize_hex("facc15") == "#facc15"
    assert normalize_hex("  #FaCc15  ") == "#facc15"


def test_normalize_hex_rejects_garbage():
    with pytest.raises(ValueError):
        normalize_hex("not-a-colour")
    with pytest.raises(ValueError):
        normalize_hex("#fff")


def test_hex_to_ass_bgr_is_bgr_order():
    # libass wants &HAABBGGRR
    assert hex_to_ass_bgr("#ff0000") == "&H000000FF"
    assert hex_to_ass_bgr("#00ff00") == "&H0000FF00"
    assert hex_to_ass_bgr("#0000ff") == "&H00FF0000"


def test_rgba_css_uses_normalised_colour():
    assert rgba_css("#ff0000", 0.5) == "rgba(255, 0, 0, 0.5)"


def test_colour_is_validated_and_normalised():
    style = CaptionStyle(text_color="#FFFFFF", glow_color="00FF00")
    assert style.text_color == "#ffffff"
    assert style.glow_color == "#00ff00"


def test_invalid_colour_is_rejected():
    with pytest.raises(ValueError):
        CaptionStyle(text_color="purple")


def test_out_of_range_values_are_clamped():
    style = CaptionStyle(
        font_size=9999, glow_radius=-5, glow_intensity=5.0, stroke_width=100.0
    )
    assert style.font_size == 400
    assert style.glow_radius == 0.0
    assert style.glow_intensity == 1.0
    assert style.stroke_width == 20.0


def test_alignment_falls_back_to_centre():
    assert CaptionStyle(alignment=9).alignment == 2
    assert CaptionStyle(alignment=1).alignment == 1


def test_build_ass_emits_header_and_styles():
    style = CaptionStyle(font_family="CF Anton 400")
    ass = build_ass(style, [(0.0, 2.0, "Hello")])
    assert "[Script Info]" in ass
    assert "PlayResX: 1080" in ass
    assert "PlayResY: 1920" in ass
    assert "Style: Text,CF Anton 400,96" in ass
    assert "Style: Glow,CF Anton 400,96" in ass


def test_build_ass_uses_glow_layer_when_enabled():
    style = CaptionStyle(glow_enabled=True, glow_radius=12)
    ass = build_ass(style, [(0.0, 2.0, "Hello")])
    assert "Dialogue: 0,0:00:00.00,0:00:02.00,Glow" in ass
    assert "{\\blur12}" in ass


def test_build_ass_omits_glow_layer_when_disabled():
    style = CaptionStyle(glow_enabled=False)
    ass = build_ass(style, [(0.0, 2.0, "Hello")])
    assert ",Glow,,0,0,0,," not in ass
    assert "Dialogue: 1,0:00:00.00,0:00:02.00,Text" in ass


def test_build_ass_escapes_braces_and_newlines():
    ass = build_ass(CaptionStyle(uppercase=False), [(0.0, 1.0, "a{b}\nc")])
    assert "a\\{b\\}\\Nc" in ass


def test_build_ass_uppercases_when_requested():
    ass = build_ass(CaptionStyle(uppercase=True), [(0.0, 1.0, "quiet")])
    assert "QUIET" in ass
    ass = build_ass(CaptionStyle(uppercase=False), [(0.0, 1.0, "quiet")])
    assert "quiet" in ass


def test_build_ass_skips_zero_length_events():
    ass = build_ass(CaptionStyle(), [(1.0, 1.0, "nope")])
    assert "Dialogue:" not in ass


def test_build_ass_colour_is_in_ass_bgr_order():
    style = CaptionStyle(text_color="#ff0000", glow_color="#00ff00")
    ass = build_ass(style, [(0.0, 1.0, "x")])
    text_line = [ln for ln in ass.splitlines() if ln.startswith("Style: Text")][0]
    assert "&H000000FF" in text_line


def test_glow_alpha_reflects_intensity():
    # intensity 1.0 -> fully opaque halo -> alpha byte 00
    ass = build_ass(CaptionStyle(glow_intensity=1.0), [(0.0, 1.0, "x")])
    glow_line = [ln for ln in ass.splitlines() if ln.startswith("Style: Glow")][0]
    assert "&H00" in glow_line
    # intensity 0.0 -> fully transparent halo -> alpha byte FF
    ass = build_ass(CaptionStyle(glow_intensity=0.0), [(0.0, 1.0, "x")])
    glow_line = [ln for ln in ass.splitlines() if ln.startswith("Style: Glow")][0]
    assert "&HFF" in glow_line


def test_preview_shadow_contains_glow_colour():
    style = CaptionStyle(glow_enabled=True, glow_color="#ff0000", glow_radius=10)
    shadow = preview_text_shadow(style)
    assert "rgba(255, 0, 0" in shadow
    assert "10px" in shadow


def test_preview_shadow_falls_back_to_drop_shadow():
    style = CaptionStyle(glow_enabled=False, shadow_color="#000000", shadow_depth=4)
    shadow = preview_text_shadow(style)
    assert "4px 4px" in shadow
    assert "rgba(255" not in shadow


def test_preview_stroke_uses_stroke_colour():
    style = CaptionStyle(stroke_width=3, stroke_color="#ff0000")
    assert preview_stroke(style) == "3px #ff0000"
    assert preview_stroke(CaptionStyle(stroke_width=0)) == "0px transparent"


def test_karaoke_color_and_caption_mode_validation():
    # Valid karaoke_color and caption_mode
    style = CaptionStyle(karaoke_color="#22C55E", caption_mode="1-word")
    assert style.karaoke_color == "#22c55e"
    assert style.caption_mode == "1-word"

    # All valid caption modes
    for m in ("1-word", "chunk", "phrase", "sentence"):
        assert CaptionStyle(caption_mode=m).caption_mode == m

    # Invalid caption mode defaults to chunk
    assert CaptionStyle(caption_mode="invalid_mode").caption_mode == "chunk"

    # Invalid karaoke color raises ValueError
    with pytest.raises(ValueError):
        CaptionStyle(karaoke_color="not-a-color")




def test_is_rtl_text_detects_right_to_left_scripts():
    """RTL detection covers Arabic, Hebrew and presentation forms."""
    assert is_rtl_text("یہاں پر بائیہ") is True
    assert is_rtl_text("שלום") is True
    assert is_rtl_text("hello world") is False
    assert is_rtl_text("") is False


def test_build_ass_reverses_tokens_for_rtl_captions():
    """An RTL karaoke caption must emit the tokens in reversed order.

    libass lays out each bidi run independently, so the inline colour
    override would otherwise reverse the visual word order.
    """
    style = CaptionStyle(
        text_color="#facc15",
        karaoke_color="#ffffff",
        glow_enabled=False,
        uppercase=False,
    )
    words = [
        CaptionWord(token="یہاں", source_start_us=0, source_end_us=300_000, output_start_us=0, output_end_us=300_000),
        CaptionWord(token="پر", source_start_us=300_000, source_end_us=600_000, output_start_us=300_000, output_end_us=600_000),
        CaptionWord(token="بائیہ", source_start_us=600_000, source_end_us=900_000, output_start_us=600_000, output_end_us=900_000),
    ]
    group = CaptionGroup(
        id="cg-rtl",
        text="یہاں پر بائیہ",
        output_start_us=0,
        output_end_us=900_000,
        words=words,
    )

    ass = build_ass(style, [group])

    # The first karaoke event highlights the first spoken word ("یہاں"),
    # which in the reversed rendering sits last.
    first_event = next(line for line in ass.splitlines() if line.startswith("Dialogue: 1,"))
    assert first_event.rstrip().endswith("پر بائیہ") or "بائیہ" in first_event
    # Reversed order: the last spoken word appears first in the payload.
    assert first_event.index("بائیہ") < first_event.index("یہاں")


def test_build_ass_keeps_ltr_token_order():
    """A left-to-right caption must keep its word order untouched."""
    style = CaptionStyle(
        text_color="#facc15",
        karaoke_color="#ffffff",
        glow_enabled=False,
        uppercase=False,
    )
    words = [
        CaptionWord(token="one", source_start_us=0, source_end_us=300_000, output_start_us=0, output_end_us=300_000),
        CaptionWord(token="two", source_start_us=300_000, source_end_us=600_000, output_start_us=300_000, output_end_us=600_000),
        CaptionWord(token="three", source_start_us=600_000, source_end_us=900_000, output_start_us=600_000, output_end_us=900_000),
    ]
    group = CaptionGroup(
        id="cg-ltr",
        text="one two three",
        output_start_us=0,
        output_end_us=900_000,
        words=words,
    )

    ass = build_ass(style, [group])
    first_event = next(line for line in ass.splitlines() if line.startswith("Dialogue: 1,"))
    assert first_event.index("one") < first_event.index("two") < first_event.index("three")

def test_build_ass_progressive_word_emphasis():
    """Verify ASS output includes \1c highlight tags when word timestamps are provided."""
    from services.api.timeline_map import CaptionWord, CaptionGroup

    style = CaptionStyle(
        text_color="#facc15",
        karaoke_color="#22c55e",
        glow_color="#facc15",
        glow_enabled=True,
    )
    words = [
        CaptionWord(token="Never", source_start_us=0, source_end_us=500_000, output_start_us=0, output_end_us=500_000),
        CaptionWord(token="give", source_start_us=500_000, source_end_us=900_000, output_start_us=500_000, output_end_us=900_000),
        CaptionWord(token="up.", source_start_us=900_000, source_end_us=1_400_000, output_start_us=900_000, output_end_us=1_400_000),
    ]
    group = CaptionGroup(
        id="cg-test",
        text="Never give up.",
        output_start_us=0,
        output_end_us=1_400_000,
        words=words,
    )

    ass = build_ass(style, [group])
    assert "[Events]" in ass
    # Karaoke color in BGR is &H005EC522
    assert "\\1c&H005EC522&" in ass
    # Base text color in BGR is &H0015CCFA
    assert "\\1c&H0015CCFA&" in ass
    # Glow layer should also have active highlight
    assert "Dialogue: 0," in ass
    assert "Dialogue: 1," in ass
