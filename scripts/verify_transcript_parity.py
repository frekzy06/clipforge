#!/usr/bin/env python3
"""Verify preview/export timing parity for transcribed captions.

The export burns one ASS event per caption group. This script re-derives the
expected events from the stored words + timeline map, compares them with the
groups the API returns, and then confirms the rendered frames actually show
the caption at the expected times.

Usage:
    venv/bin/python scripts/verify_transcript_parity.py <project_id> [--api URL]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "services", "api"))

from timeline_map import CaptionWord, TimelineMap  # noqa: E402

DEFAULT_API = "http://127.0.0.1:8000"


def api_get(api: str, path: str) -> dict:
    with urllib.request.urlopen(f"{api}{path}", timeout=60) as resp:
        return json.loads(resp.read())


def api_post(api: str, path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{api}{path}",
        data=json.dumps(payload).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=900) as resp:
        return json.loads(resp.read())


def count_bright(frame_path: str, raw_path: str) -> int:
    """Count bright pixels in the caption band of a frame."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", frame_path,
         "-vf", "crop=1080:260:0:1450", "-f", "rawvideo",
         "-pix_fmt", "gray", raw_path],
        check=True,
    )
    with open(raw_path, "rb") as fh:
        data = fh.read()
    return sum(1 for b in data if b > 50)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project_id")
    ap.add_argument("--api", default=DEFAULT_API)
    args = ap.parse_args()

    project = api_get(args.api, f"/api/projects/{args.project_id}")
    words = project.get("words") or []
    timeline = project.get("timeline") or []
    if not words:
        print("FAIL: project has no transcribed words")
        return 1
    if not timeline:
        print("FAIL: project has an empty timeline")
        return 1

    # 1. Re-derive the expected caption groups from the stored words.
    tmap = TimelineMap.from_seconds_list(timeline)
    mapped = tmap.map_words([CaptionWord(**w) for w in words])
    caption_mode = (project.get("caption_style") or {}).get("caption_mode") or project.get("caption_mode") or "chunk"
    groups = TimelineMap.group_words_into_captions(mapped, mode=caption_mode)
    expected = [(g.output_start_sec, g.output_end_sec, g.text) for g in groups]
    print(f"words={len(words)} mapped={len(mapped)} groups={len(expected)}")
    if not expected:
        print("FAIL: no caption groups were produced")
        return 1

    # 2. Export and confirm the exporter used the same mapping.
    print("exporting...")
    api_post(args.api, f"/api/projects/{args.project_id}/export",
             {"crop_mode": "fit", "fps": 30})
    project = api_get(args.api, f"/api/projects/{args.project_id}")
    exported = project.get("exported_path")
    if not exported or not os.path.exists(exported):
        print("FAIL: export did not produce a file")
        return 1
    print(f"exported: {exported}")

    api_groups = project.get("caption_groups") or []
    api_expected = [
        (g["output_start_us"] / 1e6, g["output_end_us"] / 1e6, g["text"])
        for g in api_groups
    ]
    if len(api_expected) != len(expected):
        print(f"FAIL: group count mismatch api={len(api_expected)} local={len(expected)}")
        return 1
    drift = 0.0
    for (s1, e1, t1), (s2, e2, t2) in zip(api_expected, expected):
        if t1 != t2:
            print(f"FAIL: text mismatch {t1!r} != {t2!r}")
            return 1
        drift = max(drift, abs(s1 - s2), abs(e1 - e2))
    print(f"api/local mapping drift: {drift:.9f}s")
    if drift > 1e-6:
        print("FAIL: mapping drift exceeds 1 microsecond")
        return 1

    # 3. Confirm the rendered frames show the caption at the expected times.
    print("checking rendered frames...")
    with tempfile.TemporaryDirectory(prefix="cf-parity-") as tmpdir:
        checks = []
        for g in expected[:6]:
            mid = (g[0] + g[1]) / 2
            checks.append((mid, g[2], True))
        # A frame in a gap BEFORE the first caption must have no text, but
        # only when the first caption does not start at t=0 (in which case
        # there is no such gap to sample).
        if expected[0][0] > 0.30:
            checks.append((max(0.0, expected[0][0] - 0.25), "", False))
        # A frame in a gap BETWEEN two captions must have no text either.
        for prev, nxt in zip(expected, expected[1:]):
            gap_start = prev[1]
            gap_end = nxt[0]
            if gap_end - gap_start > 0.30:
                checks.append(((gap_start + gap_end) / 2, "", False))
                break

        ok = True
        for when, text, should_show in checks:
            frame = os.path.join(tmpdir, f"f_{when:.2f}.png")
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", f"{when:.3f}", "-i", exported,
                 "-frames:v", "1", frame],
                check=True,
            )
            bright = count_bright(frame, os.path.join(tmpdir, "band.raw"))
            shown = bright > 150
            if shown != should_show:
                ok = False
            status = "OK " if shown == should_show else "FAIL"
            label = (text[:34] if text else "(no caption expected)")
            print(f"  {status} t={when:6.2f}s bright={bright:6d} {label}")

    if not ok:
        print("FAIL: rendered frames do not match the expected caption timing")
        return 1

    print("\nPASS: preview and export timings agree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
