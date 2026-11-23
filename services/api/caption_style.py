"""Caption style schema and ASS rendering for ClipForge.

A single canonical `CaptionStyle` drives both the browser preview and the
FFmpeg/libass export so the two stay in parity.

Glow is implemented the way libass supports it: a blurred, coloured event
drawn behind a sharp text event. `glow_color` therefore controls the halo
colour and `glow_radius` its blur radius, while `text_color` controls the
glyph fill itself.
"""
from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple, Union

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = 1

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_hex(value: str) -> str:
    """Normalise ``#rrggbb`` / ``rrggbb`` to lowercase ``#rrggbb``."""
    if not isinstance(value, str):
        raise ValueError("colour must be a hex string")
    match = _HEX_RE.match(value.strip())
    if not match:
        raise ValueError(f"invalid hex colour: {value!r}")
    return "#" + match.group(1).lower()


def hex_to_ass_bgr(value: str) -> str:
    """Convert ``#rrggbb`` to the ``&HAABBGGRR`` form libass expects."""
    rgb = normalize_hex(value).lstrip("#")
    r, g, b = rgb[0:2], rgb[2:4], rgb[4:6]
    return f"&H00{b}{g}{r}".upper()


def hex_to_rgb_tuple(value: str) -> Tuple[int, int, int]:
    rgb = normalize_hex(value).lstrip("#")
    return int(rgb[0:2], 16), int(rgb[2:4], 16), int(rgb[4:6], 16)


def rgba_css(color: str, alpha: float) -> str:
    """CSS rgba() string for a hex colour plus 0..1 alpha."""
    r, g, b = hex_to_rgb_tuple(color)
    return f"rgba({r}, {g}, {b}, {round(clamp(alpha, 0.0, 1.0), 3)})"


def css_font_family(family: str) -> str:
    """Quote a font family name for use in a CSS ``font-family`` value.

    ClipForge installs every weight under its own family name, so a family is
    always multi-word and ends in a digit, e.g. ``CF Anton 400``. An unquoted
    value like that is invalid CSS: the CSSOM rejects the whole declaration,
    the browser silently drops ``font-family``, and the text falls back to the
    system font. Always quote the name.
    """
    name = (family or "").strip()
    if not name:
        return "inherit"
    # Escape quotes/backslashes so the value can never break out of the string.
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def css_font_weight(weight: int) -> int:
    """Font weight to request from the browser.

    The installed faces are named ``CF <Family> <Weight>`` and each declares
    exactly its own weight, so the requested weight must match the family that
    was selected - asking for 900 from a face declared as 400 makes the
    browser synthesise a fake bold.
    """
    return int(clamp(int(weight or 400), 100, 900))


class CaptionStyle(BaseModel):
    """Canonical caption style.

    Field names are stable and are shared between the API, the browser
    preview and the exported ASS subtitle file.
    """

    schema_version: int = SCHEMA_VERSION

    # Typography
    font_family: str = "CF Anton 400"
    font_weight: int = 400
    font_size: int = 96
    uppercase: bool = True
    letter_spacing: float = 1.5
    line_spacing: float = 1.2

    # Colours (all editable in the UI)
    text_color: str = "#facc15"
    glow_color: str = "#facc15"
    stroke_color: str = "#000000"
    shadow_color: str = "#000000"
    karaoke_color: str = "#ffffff"
    caption_mode: str = "chunk"

    # Stroke / shadow
    stroke_width: float = 3.0
    shadow_depth: float = 2.0

    # Glow
    glow_enabled: bool = True
    glow_radius: float = 12.0
    glow_intensity: float = 0.9

    # Layout
    alignment: int = 2  # ASS alignment: 1 bottom, 2 centre, 3 top
    margin_v: int = 220
    margin_h: int = 80

    @field_validator("text_color", "glow_color", "stroke_color", "shadow_color", "karaoke_color")
    @classmethod
    def _validate_color(cls, v: str) -> str:
        return normalize_hex(v)

    @field_validator("caption_mode")
    @classmethod
    def _validate_caption_mode(cls, v: str) -> str:
        norm = str(v).lower().strip()
        if norm in ("1-word", "chunk", "phrase", "sentence"):
            return norm
        return "chunk"

    @field_validator("font_size")
    @classmethod
    def _validate_font_size(cls, v: int) -> int:
        return int(clamp(v, 8, 400))

    @field_validator("font_weight")
    @classmethod
    def _validate_font_weight(cls, v: int) -> int:
        return int(clamp(v, 100, 900))

    @field_validator("stroke_width")
    @classmethod
    def _validate_stroke(cls, v: float) -> float:
        return round(clamp(v, 0.0, 20.0), 2)

    @field_validator("shadow_depth")
    @classmethod
    def _validate_shadow(cls, v: float) -> float:
        return round(clamp(v, 0.0, 30.0), 2)

    @field_validator("glow_radius")
    @classmethod
    def _validate_glow_radius(cls, v: float) -> float:
        return round(clamp(v, 0.0, 60.0), 2)

    @field_validator("glow_intensity")
    @classmethod
    def _validate_glow_intensity(cls, v: float) -> float:
        return round(clamp(v, 0.0, 1.0), 3)

    @field_validator("letter_spacing")
    @classmethod
    def _validate_letter_spacing(cls, v: float) -> float:
        return round(clamp(v, -10.0, 50.0), 2)

    @field_validator("line_spacing")
    @classmethod
    def _validate_line_spacing(cls, v: float) -> float:
        return round(clamp(v, 0.5, 5.0), 2)

    @field_validator("alignment")
    @classmethod
    def _validate_alignment(cls, v: int) -> int:
        return int(v) if v in (1, 2, 3) else 2

    @field_validator("margin_v")
    @classmethod
    def _validate_margin_v(cls, v: int) -> int:
        return int(clamp(v, 0, 1920))

    @field_validator("margin_h")
    @classmethod
    def _validate_margin_h(cls, v: int) -> int:
        return int(clamp(v, 0, 540))


def _ass_time(seconds: float) -> str:
    """Format seconds as an ASS timestamp (h:mm:ss.cc)."""
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _escape_ass_text(text: str) -> str:
    """Escape text for an ASS dialogue field."""
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\N")
    )


# Ranges of the Unicode blocks that are written right-to-left. A caption that
# contains any of these is laid out right-to-left by the renderer.
_RTL_RANGES = (
    (0x0590, 0x05FF),   # Hebrew
    (0x0600, 0x06FF),   # Arabic
    (0x0700, 0x074F),   # Syriac
    (0x0750, 0x077F),   # Arabic supplement
    (0x0780, 0x07BF),   # Thaana
    (0x07C0, 0x08FF),   # NKo / Arabic extended
    (0xFB1D, 0xFDFF),   # Hebrew/Arabic presentation forms A
    (0xFE70, 0xFEFF),   # Arabic presentation forms B
    (0x10800, 0x10FFF), # Ancient RTL scripts
    (0x1E800, 0x1EFFF), # Adlam / Arabic math
)


def is_rtl_text(text: str) -> bool:
    """True when the text contains right-to-left script characters.

    Karaoke emphasis is drawn by wrapping the active word in an inline colour
    override. In a right-to-left paragraph that override splits the text into
    separate bidi runs, and libass then lays each run out independently, which
    reverses the word order. RTL captions therefore need the word order
    reversed before the overrides are applied.
    """
    for char in text:
        code = ord(char)
        for low, high in _RTL_RANGES:
            if low <= code <= high:
                return True
    return False


def build_ass(
    style: CaptionStyle,
    events: Union[List[Tuple[float, float, str]], List[Any]],
    play_res_x: int = 1080,
    play_res_y: int = 1920,
    libass_font_name: Optional[str] = None,
) -> str:
    """Build an ASS subtitle document for the given timed text events.

    Each event can be ``(start_seconds, end_seconds, text)``,
    ``(start_seconds, end_seconds, text, words)``, or a ``CaptionGroup`` instance.
    When word timestamps are present, progressive karaoke word emphasis is generated
    using `style.karaoke_color`. When the style has glow enabled a blurred halo event
    is emitted behind the text event, which is how libass renders a coloured glow.
    """
    font_name = libass_font_name or style.font_family
    text_colour = hex_to_ass_bgr(style.text_color)
    glow_colour = hex_to_ass_bgr(style.glow_color)
    stroke_colour = hex_to_ass_bgr(style.stroke_color)
    shadow_colour = hex_to_ass_bgr(style.shadow_color)
    karaoke_colour = hex_to_ass_bgr(style.karaoke_color)

    # Glow intensity drives the halo opacity: 0 -> invisible, 1 -> full.
    # ASS alpha is inverted (00 = opaque, FF = transparent).
    glow_alpha = int(round((1.0 - clamp(style.glow_intensity, 0.0, 1.0)) * 255))
    glow_colour_with_alpha = f"&H{glow_alpha:02X}{glow_colour[4:]}"

    blur = int(round(clamp(style.glow_radius, 0.0, 60.0)))
    outline = int(round(clamp(style.stroke_width, 0.0, 20.0)))
    shadow = int(round(clamp(style.shadow_depth, 0.0, 30.0)))
    spacing = round(clamp(style.letter_spacing, -10.0, 50.0), 2)

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {play_res_x}",
        f"PlayResY: {play_res_y}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        (
            f"Style: Text,{font_name},{style.font_size},{text_colour},"
            f"{text_colour},{stroke_colour},{shadow_colour},0,0,0,0,"
            f"100,100,{spacing},0,1,{outline},{shadow},{style.alignment},"
            f"{style.margin_h},{style.margin_h},{style.margin_v},1"
        ),
        (
            f"Style: Glow,{font_name},{style.font_size},{glow_colour_with_alpha},"
            f"{glow_colour_with_alpha},{glow_colour_with_alpha},{glow_colour_with_alpha},"
            f"0,0,0,0,100,100,{spacing},0,1,0,0,{style.alignment},"
            f"{style.margin_h},{style.margin_h},{style.margin_v},1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text",
    ]

    lines = list(header)
    for event in events:
        words = None
        if hasattr(event, "output_start_sec") and hasattr(event, "output_end_sec"):
            start = float(event.output_start_sec)
            end = float(event.output_end_sec)
            text = str(event.text)
            words = getattr(event, "words", None)
        elif isinstance(event, dict):
            start = float(event.get("output_start_sec", event.get("start", 0.0)))
            end = float(event.get("output_end_sec", event.get("end", 0.0)))
            text = str(event.get("text", ""))
            words = event.get("words")
        elif isinstance(event, (list, tuple)):
            if len(event) >= 4:
                start, end, text, words = event[:4]
            else:
                start, end, text = event[:3]
                words = None
        else:
            continue

        if end <= start:
            continue

        valid_words = []
        if words:
            for w in words:
                w_start = getattr(w, "output_start_sec", None)
                w_end = getattr(w, "output_end_sec", None)
                w_tok = getattr(w, "token", None)
                if w_start is None and isinstance(w, dict):
                    w_start = w.get("output_start_sec")
                    if w_start is None and "output_start_us" in w:
                        w_start = w["output_start_us"] / 1_000_000.0
                    w_end = w.get("output_end_sec")
                    if w_end is None and "output_end_us" in w:
                        w_end = w["output_end_us"] / 1_000_000.0
                    w_tok = w.get("token")
                if w_start is not None and w_end is not None and w_tok is not None:
                    valid_words.append((float(w_start), float(w_end), str(w_tok)))

        if not valid_words:
            payload = _escape_ass_text(text)
            if style.uppercase:
                payload = payload.upper()
            start_ts = _ass_time(start)
            end_ts = _ass_time(end)
            if style.glow_enabled and blur > 0:
                lines.append(
                    f"Dialogue: 0,{start_ts},{end_ts},Glow,,0,0,0,,"
                    f"{{\\blur{blur}}}{payload}"
                )
            lines.append(
                f"Dialogue: 1,{start_ts},{end_ts},Text,,0,0,0,,{payload}"
            )
        else:
            tokens = [w[2] for w in valid_words]
            if style.uppercase:
                tokens = [t.upper() for t in tokens]
            escaped_tokens = [_escape_ass_text(t) for t in tokens]

            # Right-to-left captions need the token order reversed *for the
            # karaoke events only*. The karaoke colour override splits the
            # line into separate bidi runs, and libass lays each run out
            # independently, which reverses the word order of an RTL line.
            # Reversing the tokens up front restores the correct visual order
            # while keeping the highlight on the right word. Plain payloads
            # (no colour override) render correctly and are left alone, as is
            # every left-to-right caption.
            rtl = is_rtl_text(text)
            render_tokens = list(reversed(escaped_tokens)) if rtl else escaped_tokens

            # 1. Pre-word gap
            if valid_words[0][0] > start + 0.05:
                pre_start_ts = _ass_time(start)
                pre_end_ts = _ass_time(valid_words[0][0])
                pre_payload = " ".join(escaped_tokens)
                if style.glow_enabled and blur > 0:
                    lines.append(
                        f"Dialogue: 0,{pre_start_ts},{pre_end_ts},Glow,,0,0,0,,"
                        f"{{\\blur{blur}}}{pre_payload}"
                    )
                lines.append(
                    f"Dialogue: 1,{pre_start_ts},{pre_end_ts},Text,,0,0,0,,{pre_payload}"
                )

            # 2. Word active intervals
            n_words = len(valid_words)
            for idx in range(n_words):
                w_s, w_e, _ = valid_words[idx]
                slice_start = max(start, w_s)
                if idx < n_words - 1:
                    next_s = valid_words[idx + 1][0]
                    if next_s > w_e + 0.4:
                        slice_end = min(end, w_e)
                    else:
                        slice_end = min(end, next_s)
                else:
                    slice_end = end

                if slice_end <= slice_start:
                    continue

                # Which token is the active one in the rendered order? For an
                # RTL line the tokens were reversed, so the index flips.
                active_index = (len(escaped_tokens) - 1 - idx) if rtl else idx

                t_parts = []
                for i_tok, tok in enumerate(render_tokens):
                    if i_tok == active_index:
                        t_parts.append(f"{{\\1c{karaoke_colour}&}}{tok}{{\\1c{text_colour}&}}")
                    else:
                        t_parts.append(tok)
                t_payload = " ".join(t_parts)
                start_ts = _ass_time(slice_start)
                end_ts = _ass_time(slice_end)

                if style.glow_enabled and blur > 0:
                    base_glow_tag = f"&H{glow_alpha:02X}{glow_colour[4:]}"
                    active_glow_tag = f"&H{glow_alpha:02X}{karaoke_colour[4:]}"
                    g_parts = []
                    for i_tok, tok in enumerate(render_tokens):
                        if i_tok == active_index:
                            g_parts.append(f"{{\\1c{active_glow_tag}&}}{tok}{{\\1c{base_glow_tag}&}}")
                        else:
                            g_parts.append(tok)
                    g_payload = " ".join(g_parts)
                    lines.append(
                        f"Dialogue: 0,{start_ts},{end_ts},Glow,,0,0,0,,"
                        f"{{\\blur{blur}}}{g_payload}"
                    )

                lines.append(
                    f"Dialogue: 1,{start_ts},{end_ts},Text,,0,0,0,,{t_payload}"
                )

                if idx < n_words - 1:
                    next_s = valid_words[idx + 1][0]
                    if next_s > slice_end + 0.05 and next_s <= end:
                        gap_start_ts = _ass_time(slice_end)
                        gap_end_ts = _ass_time(next_s)
                        gap_payload = " ".join(escaped_tokens)
                        if style.glow_enabled and blur > 0:
                            lines.append(
                                f"Dialogue: 0,{gap_start_ts},{gap_end_ts},Glow,,0,0,0,,"
                                f"{{\\blur{blur}}}{gap_payload}"
                            )
                        lines.append(
                            f"Dialogue: 1,{gap_start_ts},{gap_end_ts},Text,,0,0,0,,{gap_payload}"
                        )

    return "\n".join(lines) + "\n"


def _css_num(value: float) -> str:
    """Format a number for CSS without a pointless trailing `.0`."""
    rounded = round(float(value), 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:g}"


def preview_text_shadow(style: CaptionStyle) -> str:
    """CSS text-shadow approximating the libass glow for the browser preview."""
    if not style.glow_enabled or style.glow_radius <= 0:
        shadow = (
            f"{_css_num(style.shadow_depth)}px {_css_num(style.shadow_depth)}px "
            f"{_css_num(max(1, style.shadow_depth))}px "
            f"{rgba_css(style.shadow_color, 0.8)}"
        )
        return shadow
    radius = style.glow_radius
    intensity = clamp(style.glow_intensity, 0.0, 1.0)
    layers = [
        f"0 0 {_css_num(radius)}px {rgba_css(style.glow_color, intensity)}",
        f"0 0 {_css_num(radius * 2)}px {rgba_css(style.glow_color, intensity * 0.6)}",
        f"0 0 {_css_num(radius * 3)}px {rgba_css(style.glow_color, intensity * 0.35)}",
    ]
    if style.shadow_depth > 0:
        layers.append(
            f"{_css_num(style.shadow_depth)}px {_css_num(style.shadow_depth)}px "
            f"{_css_num(max(1, style.shadow_depth))}px "
            f"{rgba_css(style.shadow_color, 0.85)}"
        )
    return ", ".join(layers)


def preview_stroke(style: CaptionStyle) -> str:
    """CSS -webkit-text-stroke value approximating the ASS outline."""
    if style.stroke_width <= 0:
        return "0px transparent"
    return f"{_css_num(style.stroke_width)}px {style.stroke_color}"


def preview_text_shadow_scaled(style: CaptionStyle, scale: float) -> str:
    """`preview_text_shadow` with every length multiplied by ``scale``.

    The caption geometry is authored in export pixels (1080 wide). A small
    preview box has to scale the radii down, otherwise the outline is several
    times too thick and the text turns into an unreadable blob.
    """
    scaled = style.model_copy(
        update={
            "glow_radius": round(clamp(style.glow_radius * scale, 0.0, 60.0), 2),
            "stroke_width": round(clamp(style.stroke_width * scale, 0.0, 20.0), 2),
            "shadow_depth": round(clamp(style.shadow_depth * scale, 0.0, 30.0), 2),
        }
    )
    return preview_text_shadow(scaled)


def preview_stroke_scaled(style: CaptionStyle, scale: float) -> str:
    """`preview_stroke` with the outline width multiplied by ``scale``."""
    if style.stroke_width <= 0:
        return "0px transparent"
    width = round(clamp(style.stroke_width * scale, 0.0, 20.0), 2)
    return f"{_css_num(width)}px {style.stroke_color}"
