#!/usr/bin/env python3
"""Measure Headroom's token savings on this repository.

Sends representative tool outputs through the running Headroom proxy and
reports the token reduction per payload type. Run the proxy first:

    ./scripts/setup_headroom.sh start

Usage:
    venv/bin/python scripts/measure_headroom_savings.py [--proxy URL]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PROXY = "http://127.0.0.1:8787"


def proxy_compress(proxy: str, messages: list) -> dict:
    """Compress a message list through the Headroom proxy."""
    req = urllib.request.Request(
        f"{proxy}/v1/compress",
        data=json.dumps({"messages": messages, "model": "claude-sonnet-4-5-20250929"}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read())


def as_tool_output(text: str, tool: str = "run_commands") -> list:
    """Wrap text the way an agent's tool_result block looks."""
    return [
        {"role": "user", "content": "Process this output."},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": tool, "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": text}],
        },
    ]


def collect_payloads() -> list:
    """Realistic tool outputs an agent sees while working on this repo."""
    payloads = []

    def add(label, text, tool="run_commands"):
        if text and text.strip():
            payloads.append((label, text, tool))

    def read(rel):
        path = os.path.join(REPO_ROOT, rel)
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        return ""

    # JSON produced by the app (clip grids, font manifests).
    add("font manifest (JSON)", read("services/api/storage/fonts/manifest.json"))
    add("fonts catalog (JSON)", read("assets/fonts/catalog.json"))
    clip_grid = json.dumps(
        [
            {
                "id": f"clip-{i}",
                "start": i * 1.5,
                "end": (i + 1) * 1.5,
                "title": f"Viral Candidate #{i}",
            }
            for i in range(400)
        ],
        indent=2,
    )
    add("clip grid JSON (400 items)", clip_grid)

    # Source code.
    add("main.py (code)", read("services/api/main.py"), "read_files")
    add("index.tsx (code)", read("apps/web/src/pages/index.tsx"), "read_files")
    add("font_library.py (code)", read("services/api/font_library.py"), "read_files")

    # Logs and search results.
    add(
        "build log (300 lines)",
        "\n".join(
            f"INFO     services.api.main:123 - request handled id={i} dur={i * 0.37:.2f}s"
            for i in range(300)
        ),
    )
    add(
        "grep results (400 lines)",
        "\n".join(
            f"services/api/main.py:{i}:    def handler_{i}(self, request):"
            for i in range(400)
        ),
        "search_codebase",
    )
    return payloads


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default=DEFAULT_PROXY)
    args = ap.parse_args()

    try:
        urllib.request.urlopen(f"{args.proxy}/health", timeout=5)
    except (urllib.error.URLError, OSError):
        print(f"Headroom proxy is not running at {args.proxy}")
        print("Start it with: ./scripts/setup_headroom.sh start")
        return 1

    print(f"Headroom savings via {args.proxy}")
    print(f"{'payload':26s} {'before':>8s} {'after':>8s} {'saved':>8s}   ratio")
    total_before = total_after = 0
    for label, text, tool in collect_payloads():
        result = proxy_compress(args.proxy, as_tool_output(text, tool))
        before = result["tokens_before"]
        after = result["tokens_after"]
        total_before += before
        total_after += after
        ratio = (1 - after / before) * 100 if before else 0.0
        print(f"{label:26s} {before:8d} {after:8d} {before - after:8d}  {ratio:6.1f}%")

    overall = (1 - total_after / total_before) * 100 if total_before else 0.0
    print(f"\nTOTAL {total_before} -> {total_after} tokens ({overall:.1f}% saved)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
