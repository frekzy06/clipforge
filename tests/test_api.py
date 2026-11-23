"""Tests for the ClipForge API surface: fonts, caption presets and style."""
import json
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "services", "api"
))

import main as api  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(api.app) as test_client:
        yield test_client


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_font_catalog_endpoint_lists_families(client):
    res = client.get("/api/fonts")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 40
    assert len(data["families"]) == data["total"]
    assert "Display" in data["categories"]
    for family in data["families"]:
        assert family["license"] in ("OFL-1.1", "Apache-2.0")
        assert isinstance(family["installed"], bool)


def test_caption_presets_endpoint_returns_valid_styles(client):
    res = client.get("/api/caption-presets")
    assert res.status_code == 200
    presets = res.json()["presets"]
    assert len(presets) >= 5
    for preset in presets:
        # Colours must be normalised hex values.
        assert preset["style"]["text_color"].startswith("#")
        assert preset["style"]["glow_color"].startswith("#")
        assert preset["preview"]["text_shadow"]


def test_preset_preview_font_family_is_css_quoted(client):
    """The preview font-family must be quoted CSS.

    ClipForge names every installed weight `CF <Family> <Weight>`. Unquoted,
    that is invalid CSS: the CSSOM rejects the declaration and the browser
    drops `font-family`, so the caption silently renders in the system font.
    """
    res = client.get("/api/caption-presets")
    assert res.status_code == 200
    for preset in res.json()["presets"]:
        css_family = preset["preview"]["css_font_family"]
        assert css_family.startswith('"') and css_family.endswith('"'), css_family
        # The quoted value must round-trip back to the raw family name.
        assert css_family.strip('"') == preset["preview"]["font_family"]
        # The weight must match the declared face, not a hard-coded 900.
        assert 100 <= preset["preview"]["font_weight"] <= 900


def test_css_font_family_quotes_multi_word_names():
    """`css_font_family` must quote names that CSS would otherwise reject."""
    from caption_style import css_font_family, css_font_weight

    assert css_font_family("CF Anton 400") == '"CF Anton 400"'
    assert css_font_family("  CF Anton 400  ") == '"CF Anton 400"'
    assert css_font_family("") == "inherit"
    # A quote inside the name must be escaped, never terminate the string.
    assert css_font_family('Ev"il') == '"Ev\\"il"'
    # Weight is clamped into the CSS-usable range.
    assert css_font_weight(400) == 400
    assert css_font_weight(900) == 900
    assert css_font_weight(5000) == 900
    assert css_font_weight(10) == 100


def test_youtube_import_rejects_non_youtube_urls(client):
    """The import endpoint must not act as a generic fetcher."""
    for bad in [
        "file:///etc/passwd",
        "http://example.com/video.mp4",
        "https://evil.test/watch?v=1",
        "not-a-url",
    ]:
        res = client.post("/api/projects/youtube", json={"url": bad})
        assert res.status_code == 400, f"{bad} was not rejected"

    res = client.post("/api/projects/youtube", json={"url": ""})
    assert res.status_code == 400


def test_youtube_import_accepts_youtube_urls(client, monkeypatch, tmp_path):
    """A valid YouTube URL is accepted and queued for a background download."""
    calls = []

    def fake_download(url, dest_dir):
        calls.append((url, dest_dir))
        media = tmp_path / "video.mp4"
        media.write_bytes(b"not-really-a-video")
        return {"path": str(media), "title": "Test Video", "id": "abc123"}

    monkeypatch.setattr(api.youtube_ingest, "download", fake_download)

    res = client.post(
        "/api/projects/youtube",
        json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["import_status"] == "queued"
    assert data["id"]

    # The row must exist so the polling client can track the job.
    res = client.get(f"/api/projects/{data['id']}")
    assert res.status_code == 200
    assert res.json()["import_status"] in ("queued", "downloading", "ready", "failed")

    # The background worker must eventually call the downloader.
    deadline = time.time() + 10
    while time.time() < deadline and not calls:
        time.sleep(0.05)
    assert calls, "the background import never ran"
    assert calls[0][0] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_youtube_import_can_wait_for_generated_cuts(client, monkeypatch, tmp_path):
    media = tmp_path / "source.mp4"
    media.touch()
    monkeypatch.setattr(api.youtube_ingest, "download", lambda *_: {"path": str(media), "title": "Long source"})
    monkeypatch.setattr(api, "get_media_info", lambda _: (120.0, 1920, 1080))
    response = client.post(
        "/api/projects/youtube",
        json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "prepare_clips": False},
    )
    assert response.status_code == 200
    endpoint = f"/api/projects/{response.json()['id']}"
    project = client.get(endpoint).json()
    deadline = time.monotonic() + 5
    while project["import_status"] != "ready" and time.monotonic() < deadline:
        time.sleep(0.02)
        project = client.get(endpoint).json()
    assert project["import_status"] == "ready"
    assert project["duration"] == 120.0
    assert project["timeline"] == []


def test_install_unknown_font_returns_400(client):
    res = client.post("/api/fonts/install", json={"family_id": "not-a-font"})
    assert res.status_code == 400


def test_font_file_endpoint_rejects_path_traversal(client):
    """Path traversal must never resolve to a file outside the fonts dir.

    Note: the HTTP layer normalises ".." in a URL path before routing, so
    "/api/fonts/file/.." lands on the catalog route and returns the family
    list - no file is ever served. The handler's own guard is checked
    directly below.
    """
    for bad in ["..%2f..%2fmain.py", "%2e%2e%2fmain.py", ".hidden", "a/b"]:
        res = client.get(f"/api/fonts/file/{bad}")
        assert res.status_code in (400, 404), f"{bad} was not rejected"

    # ".." collapses to the catalog route, which must never serve a file.
    res = client.get("/api/fonts/file/..")
    assert "families" in res.json()
    assert b"import" not in res.content

    # The handler itself refuses anything with a path separator or a dot.
    from fastapi import HTTPException

    for bad in ["../main.py", "..\\main.py", ".hidden", "sub/dir.ttf", ""]:
        with pytest.raises(HTTPException) as exc:
            api.get_font_file(bad)
        assert exc.value.status_code == 400


def test_font_css_endpoint_is_css(client):
    res = client.get("/api/fonts/css")
    assert res.status_code == 200
    assert "text/css" in res.headers["content-type"]
    assert "@font-face" in res.text or "no families installed" in res.text


def test_project_style_round_trip(client, tmp_path):
    """A caption style must survive a save/load cycle unchanged."""
    style = {
        "font_family": "CF Anton 400",
        "font_weight": 400,
        "text_color": "#00ff00",
        "glow_color": "#ff0000",
        "glow_radius": 18.0,
        "glow_intensity": 0.75,
        "stroke_color": "#000000",
        "stroke_width": 4.0,
        "font_size": 110,
    }
    res = client.put(
        "/api/projects/does-not-exist",
        json={"caption_style": style},
    )
    assert res.status_code == 404

    # Use the real project row created by the fixture below.
    project_id = _create_project(client)
    res = client.put(
        f"/api/projects/{project_id}",
        json={"caption_style": style, "caption_text": "ROUND TRIP"},
    )
    assert res.status_code == 200

    res = client.get(f"/api/projects/{project_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["caption_text"] == "ROUND TRIP"
    assert data["caption_style"]["text_color"] == "#00ff00"
    assert data["caption_style"]["glow_color"] == "#ff0000"
    assert data["caption_style"]["glow_radius"] == 18.0
    assert data["caption_style"]["font_size"] == 110


def test_project_caption_mode_and_karaoke_color_round_trip(client):
    """Verify karaoke_color and caption_mode persist and drive caption grouping."""
    project_id = _create_project(client)

    # Put some words on the project
    words = [
        {"token": "Hello", "source_start_us": 0, "source_end_us": 400_000},
        {"token": "world.", "source_start_us": 400_000, "source_end_us": 800_000},
        {"token": "Next", "source_start_us": 800_000, "source_end_us": 1_200_000},
        {"token": "sentence!", "source_start_us": 1_200_000, "source_end_us": 1_600_000},
    ]
    timeline = [{"id": "c1", "start": 0.0, "end": 2.0}]
    client.put(f"/api/projects/{project_id}", json={"timeline": timeline, "words": words})

    # Update style with custom karaoke color and 1-word mode
    style = {
        "karaoke_color": "#22c55e",
        "caption_mode": "1-word",
    }
    res = client.put(f"/api/projects/{project_id}", json={"caption_style": style})
    assert res.status_code == 200

    res = client.get(f"/api/projects/{project_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["caption_style"]["karaoke_color"] == "#22c55e"
    assert data["caption_style"]["caption_mode"] == "1-word"
    assert data["caption_mode"] == "1-word"
    # In 1-word mode, 4 words should produce 4 caption groups
    assert len(data["caption_groups"]) == 4

    # Now update mode to sentence
    res = client.put(f"/api/projects/{project_id}", json={"caption_style": {"caption_mode": "sentence"}})
    assert res.status_code == 200
    data = client.get(f"/api/projects/{project_id}").json()
    assert data["caption_style"]["caption_mode"] == "sentence"
    # In sentence mode, splits at . and ! -> 2 groups
    assert len(data["caption_groups"]) == 2
    assert data["caption_groups"][0]["text"] == "Hello world."
    assert data["caption_groups"][1]["text"] == "Next sentence!"


def test_invalid_colour_is_rejected_on_save(client):
    project_id = _create_project(client)
    res = client.put(
        f"/api/projects/{project_id}",
        json={"caption_style": {"text_color": "not-a-colour"}},
    )
    assert res.status_code == 400


def test_caption_position_vertical_offset_round_trip(client):
    project_id = _create_project(client)
    # Save top position (margin_v: 1620)
    res = client.put(
        f"/api/projects/{project_id}",
        json={"caption_style": {"margin_v": 1620}},
    )
    assert res.status_code == 200
    data = client.get(f"/api/projects/{project_id}").json()
    assert data["caption_style"]["margin_v"] == 1620


def test_youtube_import_request_with_pipeline_options(client, monkeypatch):
    """Verify YouTube import accepts auto_pipeline, language and num_clips."""
    started_args = []
    def queue(pid, options, url=None):
        started_args.append((pid, url, options))
        return {"status": "queued"}

    monkeypatch.setattr(api, "_queue_generation", queue)
    res = client.post(
        "/api/projects/youtube",
        json={
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "auto_pipeline": True,
            "confirm_full_download": True,
            "language": "hinglish",
            "num_clips": 3,
        },
    )
    assert res.status_code == 200
    options = started_args[0][2]
    assert options.mode == "auto"
    assert options.language == "hinglish"
    assert options.num_clips == 3


def test_youtube_settings_endpoints(client):
    res = client.get("/api/settings/youtube")
    assert res.status_code == 200
    data = res.json()
    assert "browser" in data
    assert "supported_browsers" in data
    assert "firefox" in data["supported_browsers"]
    assert "chrome" in data["supported_browsers"]

    res_post = client.post("/api/settings/youtube", json={"browser": "firefox"})
    assert res_post.status_code == 200
    assert res_post.json()["browser"] == "firefox"

    res_reset = client.post("/api/settings/youtube", json={"browser": "none"})
    assert res_reset.status_code == 200
    assert res_reset.json()["browser"] == "none"


def test_youtube_settings_rejects_unsupported_browser(client):
    res = client.post("/api/settings/youtube", json={"browser": "invalid-browser-name"})
    assert res.status_code == 400



def _create_project(client):
    """Create a throwaway project row for style round-trip tests."""
    import sqlite3
    import uuid

    project_id = str(uuid.uuid4())
    conn = sqlite3.connect(api.DB_PATH)
    conn.execute(
        "INSERT INTO projects (id, name, video_filename, video_path, duration, width, height, timeline_json) "
        "VALUES (?, ?, NULL, NULL, 0, 0, 0, '[]')",
        (project_id, "style-test"),
    )
    conn.commit()
    conn.close()
    return project_id


@pytest.fixture(scope="module", autouse=True)
def cleanup_test_projects():
    """Remove the throwaway projects this module creates."""
    yield
    import sqlite3

    conn = sqlite3.connect(api.DB_PATH)
    conn.execute("DELETE FROM projects WHERE name = 'style-test'")
    conn.commit()
    conn.close()
