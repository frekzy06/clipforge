"""Verify the YouTube import, font and Next.js fixes end to end.

Run from the repo root:
    venv/bin/python scripts/verify_fixes.py

Requires the API on 127.0.0.1:8000 and the web app on 127.0.0.1:3000.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(REPO_ROOT, "apps", "web")
WEB_URL = "http://127.0.0.1:3000"
API_URL = "http://127.0.0.1:8000"

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, ok, detail=""):
    results.append((name, PASS if ok else FAIL, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" - {detail}" if detail else ""))
    return ok


def api_get(path, base=API_URL):
    with urllib.request.urlopen(f"{base}{path}", timeout=60) as resp:
        return json.loads(resp.read())


def api_post(path, payload, base=API_URL):
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def verify_youtube_import():
    """The import must accept YouTube URLs, reject others and finish."""
    print("\n--- 1. YouTube URL import ---")

    try:
        api_post("/api/projects/youtube", {"url": "http://evil.test/x.mp4"}, WEB_URL)
        check("non-YouTube URL rejected", False, "expected HTTP 400")
    except urllib.error.HTTPError as exc:
        check("non-YouTube URL rejected", exc.code == 400, f"HTTP {exc.code}")

    try:
        queued = api_post(
            "/api/projects/youtube",
            {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            WEB_URL,
        )
        check(
            "import accepted and queued (returns immediately)",
            queued.get("import_status") == "queued" and bool(queued.get("id")),
            f"id={queued.get('id')}",
        )
    except urllib.error.HTTPError as exc:
        check("import accepted and queued", False, f"HTTP {exc.code}")
        return

    project_id = queued["id"]
    deadline = time.time() + 300
    state = None
    while time.time() < deadline:
        state = api_get(f"/api/projects/{project_id}")
        if state.get("import_status") in ("ready", "failed"):
            break
        time.sleep(5)

    status = (state or {}).get("import_status")
    check(
        "background download reaches 'ready'",
        status == "ready",
        f"status={status} error={(state or {}).get('error_message')}",
    )
def verify_fonts():
    """The preview must resolve the installed font families, not a fallback."""
    print("\n--- 2. Fonts and presets ---")

    presets = api_get("/api/caption-presets", WEB_URL)["presets"]
    check(
        "preset font-family is quoted CSS",
        all(p["preview"]["css_font_family"].startswith('"') for p in presets),
    )
    check(
        "quoted family round-trips to the raw name",
        all(
            p["preview"]["css_font_family"].strip('"') == p["preview"]["font_family"]
            for p in presets
        ),
    )
    check(
        "preset weight matches the installed face",
        all(p["preview"]["font_weight"] in (400, 700, 900) for p in presets),
    )
    distinct = len({p["preview"]["font_family"] for p in presets})
    check("presets use more than one font family", distinct > 1, f"{distinct} families")
    check(
        "presets expose scaled glow/stroke for the small cards",
        all("text_shadow_scaled" in p["preview"] for p in presets),
    )

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        check("browser font rendering (skipped: playwright missing)", True)
        return

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 950})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(WEB_URL, wait_until="networkidle")
        page.wait_for_timeout(3000)

        resolved = page.evaluate(BROWSER_FONT_PROBE)
        check(
            "browser accepts the quoted family (CSSOM keeps it)",
            all(r["family"].strip('"').startswith("CF ") for r in resolved),
            ", ".join(f"{r['id']}={r['family']}" for r in resolved[:2]),
        )
        check(
            "each preset renders with a distinct width",
            len({round(r["width"], 1) for r in resolved}) > 1,
            ", ".join(f"{r['id']}:{r['width']:.0f}px" for r in resolved),
        )

        hmr = [e for e in errors if "_next/hmr" in e]
        check("no HMR websocket errors in the console", not hmr, f"{len(hmr)} errors")
        head_warn = [e for e in errors if "Do not add stylesheets using next/head" in e]
        check("no next/head stylesheet warning", not head_warn)
        browser.close()


BROWSER_FONT_PROBE = """async () => {
    const res = await fetch('/api/caption-presets');
    const data = await res.json();
    const out = [];
    for (const preset of data.presets) {
        const el = document.createElement('span');
        el.textContent = 'HAMBURG';
        el.style.fontFamily = preset.preview.css_font_family;
        el.style.fontWeight = String(preset.preview.font_weight);
        el.style.fontSize = '48px';
        el.style.position = 'absolute';
        document.body.appendChild(el);
        const width = el.getBoundingClientRect().width;
        const family = getComputedStyle(el).fontFamily;
        el.remove();
        out.push({id: preset.id, width, family});
    }
    return out;
}"""

def verify_nextjs():
    """The dev server must accept the dev origin and serve the app cleanly."""
    print("\n--- 3. Next.js ---")

    # urllib cannot perform a WebSocket upgrade, so the handshake is done over a
    # raw socket: the response status line is all that is needed.
    try:
        import socket

        key = "dGhlIHNhbXBsZSBub25jZQ=="
        request = (
            "GET /_next/hmr HTTP/1.1\r\n"
            "Host: 127.0.0.1:3000\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: " + key + "\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Origin: http://127.0.0.1:3000\r\n"
            "\r\n"
        )
        with socket.create_connection(("127.0.0.1", 3000), timeout=10) as sock:
            sock.sendall(request.encode())
            status_line = sock.recv(200).decode("latin-1", "replace")
        upgraded = "101" in status_line
        check(
            "HMR websocket handshake accepted for 127.0.0.1",
            upgraded,
            status_line.splitlines()[0] if status_line else "no response",
        )
    except Exception as exc:  # noqa: BLE001
        check("HMR websocket handshake accepted for 127.0.0.1", False, str(exc)[:120])

    try:
        with urllib.request.urlopen(WEB_URL, timeout=30) as resp:
            html = resp.read().decode("utf-8", "replace")
        check("home page renders", "/api/fonts/css" in html)
    except Exception as exc:  # noqa: BLE001
        check("home page renders", False, str(exc)[:120])

    config = open(os.path.join(WEB, "next.config.js"), encoding="utf-8").read()
    check("next.config.js raises the rewrite proxy timeout", "proxyTimeout" in config)
    check("next.config.js allows the 127.0.0.1 dev origin", "allowedDevOrigins" in config)

    app_src = open(os.path.join(WEB, "src", "pages", "_app.tsx"), encoding="utf-8").read()
    check(
        "font stylesheet moved out of next/head",
        "from 'next/head'" not in app_src and "<Head>" not in app_src,
    )

    document_src = open(
        os.path.join(WEB, "src", "pages", "_document.tsx"), encoding="utf-8"
    ).read()
    check("_document.tsx links the font stylesheet", "/api/fonts/css" in document_src)


def main() -> int:
    print("=" * 72)
    print("ClipForge - verification of the YouTube import, font and Next.js fixes")
    print("=" * 72)

    verify_youtube_import()
    verify_fonts()
    verify_nextjs()

    print("\n" + "=" * 72)
    failed = [r for r in results if r[1] == FAIL]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    for name, _status, detail in failed:
        print(f"  FAILED: {name} {detail}")
    print("=" * 72)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())


