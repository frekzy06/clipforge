"""Analyse an exported ClipForge MP4 to verify burned-in caption colours.

Usage: python scripts/verify_export_colors.py <video> [--text #rrggbb] [--glow #rrggbb]
"""
import sys


def hex_to_rgb(value):
    value = value.strip().lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _normalize(rgb):
    """Scale a colour so its brightest channel is 1.0."""
    peak = max(rgb) or 1
    return (rgb[0] / peak, rgb[1] / peak, rgb[2] / peak)


def _distance(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def classify(r, g, b, text_rgb, glow_rgb):
    """Return 'text' if the pixel matches the text colour, 'glow' if the glow.

    Colours are compared by normalised RGB distance, so the match does not
    depend on brightness (the glow is dimmer than the glyphs) and works for
    hues where two channels are equally strong, such as magenta.
    """
    pixel = (r, g, b)
    if max(pixel) < 30:
        return None  # background

    norm = _normalize(pixel)
    text_norm = _normalize(text_rgb)
    glow_norm = _normalize(glow_rgb)

    d_text = _distance(norm, text_norm)
    d_glow = _distance(norm, glow_norm)

    if d_text <= 0.30 and d_text <= d_glow:
        return "text"
    if d_glow <= 0.30:
        return "glow"
    return None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = sys.argv[1]
    text_hex = "#00ff00"
    glow_hex = "#ff0000"
    args = sys.argv[2:]
    for i, arg in enumerate(args):
        if arg == "--text":
            text_hex = args[i + 1]
        elif arg == "--glow":
            glow_hex = args[i + 1]

    with open(path, "rb") as fh:
        data = fh.read()

    text_rgb = hex_to_rgb(text_hex)
    glow_rgb = hex_to_rgb(glow_hex)
    counts = {"text": 0, "glow": 0}
    peak = {"text": 0, "glow": 0}

    for i in range(0, len(data) - 2, 3):
        r, g, b = data[i], data[i + 1], data[i + 2]
        kind = classify(r, g, b, text_rgb, glow_rgb)
        if kind:
            counts[kind] += 1
            peak[kind] = max(peak[kind], r, g, b)

    print(f"file            : {path}")
    print(f"text colour     : {text_hex} -> {counts['text']} px (peak channel {peak['text']})")
    print(f"glow colour     : {glow_hex} -> {counts['glow']} px (peak channel {peak['glow']})")
    ok = counts["text"] > 0 and counts["glow"] > 0
    print("RESULT          :", "PASS - both colours burned in" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
