"""Exercise the generation worker without contacting YouTube."""
import subprocess
import threading
import time

from pathlib import Path
from types import SimpleNamespace

import pytest
import yt_dlp
from fastapi.testclient import TestClient

import main as api
from main import get_media_info as real_media_info
from youtube_ingest import download as real_download


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "jobs.db"))
    media = tmp_path / "source.mp4"
    media.touch()
    monkeypatch.setattr(api.youtube_ingest, "download", lambda *_: {"path": str(media), "title": "Long source"})
    monkeypatch.setattr(api, "get_media_info", lambda _: (120.0, 1920, 1080))
    monkeypatch.setattr(api.discovery_mod, "audio_energy_peaks", lambda *args, **kwargs: [{"t_sec": 40, "rms_db": -10}])
    monkeypatch.setattr(api.discovery_mod, "scene_cut_peaks", lambda *_: [])
    api.init_db()
    with TestClient(api.app) as client:
        yield client


def wait_for_job(client, project_id, statuses, *, percent=None):
    path = f"/api/projects/{project_id}/generation"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = client.get(path)
        assert result.status_code == 200
        job = result.json()
        if job["status"] in statuses and (percent is None or job["percent"] == percent):
            return job
        time.sleep(0.02)
    pytest.fail(f"Job did not reach {statuses}: {job}")


def test_generation_finishes_after_start_request_returns(client, monkeypatch):
    release = threading.Event()

    def transcribe(*args, progress, **kwargs):
        progress(0.65, "transcribed 1 words")
        assert release.wait(5)
        return {"words": [{"token": "Highlight", "source_start_us": 30_000_000, "source_end_us": 31_000_000}]}

    def generate_local_candidates(**options):
        assert options["words"][0]["token"] == "Highlight"
        return {"candidates": [api.discovery_mod.CandidateClip(
            id="cut", start_ms=30_000, end_ms=50_123, headline="The highlight",
            hook="Highlight", summary="A highlight", why_this_works="Self-contained",
            ai_quality=.9, informativeness=.8, standalone_clarity=.9, audio_clarity=.8,
        )], "dropped": 0}

    monkeypatch.setattr(api.transcription, "transcribe_media", transcribe)
    monkeypatch.setattr(api.discovery_mod, "generate_local_candidates", generate_local_candidates)
    start = client.post("/api/projects/youtube", json={
        "url": "https://www.youtube.com/watch?v=fixture",
        "confirm_full_download": True,
        "generation": {"min_duration": 15, "max_duration": 30},
    })
    assert start.status_code == 200
    project_id = start.json()["id"]
    try:
        job = wait_for_job(client, project_id, {"transcribing"}, percent=50)
        assert job["percent"] == 50
        project = client.get(f"/api/projects/{project_id}").json()
        assert project["timeline"] == []
        duplicate = client.post(f"/api/projects/{project_id}/generation", json={})
        assert duplicate.status_code == 409
    finally:
        release.set()

    assert wait_for_job(client, project_id, {"ready", "failed"})["status"] == "ready"
    project = client.get(f"/api/projects/{project_id}").json()
    assert project["timeline"] == [{
        "id": "cut", "start": 30.0, "end": 50.123, "title": "The highlight", "origin": "local",
    }]
    assert project["words"][0]["token"] == "Highlight"


def test_legacy_generation_request_runs_locally_and_cloud_settings_are_gone(client, monkeypatch):
    monkeypatch.setattr(api.transcription, "transcribe_media", lambda *a, **kw: {"words": []})
    response = client.post("/api/projects/youtube", json={
        "url": "https://www.youtube.com/watch?v=fixture", "confirm_full_download": True,
        "generation": {"mode": "gemini", "min_duration": 15, "max_duration": 30},
    })
    assert response.status_code == 200
    project_id = response.json()["id"]
    job = wait_for_job(client, project_id, {"ready", "failed"})
    assert job["status"] == "ready" and job["mode"] == "local"
    assert client.get(f"/api/projects/{project_id}").json()["timeline"][0]["origin"] == "local"
    assert client.get("/api/settings/gemini").status_code == 404


def test_background_transcription_preserves_trimmed_timeline(client, monkeypatch):
    source = client.post("/api/projects/youtube", json={
        "url": "https://www.youtube.com/watch?v=fixture", "prepare_clips": False,
    }).json()["id"]
    path = f"/api/projects/{source}"
    deadline = time.monotonic() + 5
    while client.get(path).json()["import_status"] != "ready" and time.monotonic() < deadline:
        time.sleep(0.02)
    timeline = [{"id": "kept", "start": 18, "end": 40, "title": "Existing cut"}]
    assert client.put(path, json={"timeline": timeline}).status_code == 200

    def transcribe(*args, **kwargs):
        return {
            "words": [{"token": "Caption", "source_start_us": 20_000_000, "source_end_us": 21_000_000}],
            "segments": [{"id": 1, "start": 20, "end": 21, "text": "Caption"}],
        }

    monkeypatch.setattr(api.transcription, "transcribe_media", transcribe)
    assert client.post(f"{path}/generation", json={"mode": "transcript"}).status_code == 202
    assert wait_for_job(client, source, {"ready", "failed"})["status"] == "ready"
    saved = client.get(path).json()
    assert saved["timeline"] == timeline
    assert saved["segments"][0]["text"] == "Caption"
    assert saved["mapped_words"][0]["output_start_us"] == 2_000_000


def test_interrupted_generation_is_reported_and_preserves_source(client):
    project = client.post("/api/projects", data={"name": "Interrupted"}).json()["id"]
    state = api._generation_state(api.GenerationRequest(mode="local"), "local", "Picking clips…")
    api._store_generation(project, state)
    persisted = client.get(f"/api/projects/{project}/generation").json()
    assert persisted["status"] == "failed"
    assert "restarted" in persisted["detail"]
    assert client.get(f"/api/projects/{project}").json()["generation"] == persisted


def test_caption_plan_without_media_and_confirmed_render_reuses_timestamps(client, monkeypatch):
    downloads = []
    original_download = api.youtube_ingest.download

    def download(*args):
        downloads.append(True)
        return original_download(*args)

    monkeypatch.setattr(api.youtube_ingest, "download", download)
    monkeypatch.setattr(api.youtube_ingest, "read_caption_track", lambda *_: {
        "title": "Caption source", "duration": 120, "heatmap": [{"t_sec": 40}],
        "transcript": [{"source_start_us": 30_000_000, "token": "Speech"}],
    })
    def transcribe(*args, **kwargs):
        raise api.transcription.TranscriptionError("ASR unavailable")

    monkeypatch.setattr(api.transcription, "transcribe_media", transcribe)
    response = client.post("/api/projects/youtube", json={
        "url": "https://www.youtube.com/watch?v=fixture",
        "confirm_full_download": True,
        "generation": {"mode": "plan", "min_duration": 15, "max_duration": 30},
    })
    assert response.status_code == 200
    project_id = response.json()["id"]
    job = wait_for_job(client, project_id, {"ready", "failed"})
    assert job["status"] == "ready" and job["mode"] == "plan"
    path = f"/api/projects/{project_id}"
    assert client.get(path).json()["video_filename"] is None
    candidate = client.get(f"{path}/candidates").json()["candidates"][0]
    assert 15_000 <= candidate["end_ms"] - candidate["start_ms"] <= 30_000
    assert downloads == []

    retry = {"mode": "local"}
    assert client.post(f"{path}/generation", json=retry).status_code == 400
    assert client.post(f"{path}/generation", json={**retry, "confirm_full_download": True}).status_code == 202
    downloaded_job = wait_for_job(client, project_id, {"ready", "failed"})
    assert downloaded_job["status"] == "ready"
    assert "Source transcription unavailable" in downloaded_job["detail"]
    timeline = client.get(path).json()["timeline"][0]
    assert timeline["id"] == candidate["id"]
    assert timeline["origin"] == "local"
    assert len(downloads) == 1


@pytest.fixture
def playable_source(tmp_path):
    source = tmp_path / "fixture.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=blue:size=160x90:rate=5",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000", "-t", "30",
        "-c:v", "libx264", "-c:a", "aac", str(source),
    ], check=True, capture_output=True)
    return source


def test_auto_uses_signal_cuts_with_playable_editable_source(client, monkeypatch, playable_source):
    importing = threading.Event()
    release_download = threading.Event()

    def download(*args):
        importing.set()
        assert release_download.wait(5)
        return {"path": str(playable_source), "title": "Signal source"}

    def unexpected_transcription(*args, **kwargs):
        pytest.fail("Usable signal cuts should skip local discovery transcription")

    monkeypatch.setattr(api.youtube_ingest, "download", download)
    monkeypatch.setattr(api, "get_media_info", real_media_info)
    monkeypatch.setattr(api.transcription, "transcribe_media", unexpected_transcription)
    monkeypatch.setattr(api.youtube_ingest, "read_caption_track", lambda *_: {
        "title": "Signal source", "duration": 30, "heatmap": [{"t_sec": 15}],
        "transcript": [],
    })

    response = client.post("/api/projects/youtube", json={
        "url": "https://youtu.be/fixture0000", "confirm_full_download": True,
        "generation": {"mode": "auto", "min_duration": 5, "max_duration": 6},
    })
    assert response.status_code == 200
    project_id = response.json()["id"]
    path = f"/api/projects/{project_id}"
    try:
        assert importing.wait(5)
        assert client.get(f"{path}/generation").json()["status"] == "importing"
        candidate = client.get(f"{path}/candidates").json()["candidates"][0]
    finally:
        release_download.set()

    job = wait_for_job(client, project_id, {"ready", "failed"})
    assert (job["status"], job["mode"]) == ("ready", "auto"), job
    project = client.get(path).json()
    assert project["video_filename"] == playable_source.name
    assert project["import_status"] == "ready"
    clip = project["timeline"][0]
    assert (clip["id"], clip["start"], clip["end"]) == (
        candidate["id"], candidate["start_ms"] / 1000, candidate["end_ms"] / 1000,
    )
    media = client.get(f"{path}/video", headers={"Range": "bytes=0-15"})
    assert media.status_code == 206 and media.content == playable_source.read_bytes()[:16]
    trimmed = {**clip, "start": clip["start"] + 0.25}
    assert client.put(path, json={"timeline": [trimmed, *project["timeline"][1:]]}).status_code == 200
    assert client.get(path).json()["timeline"][0] == trimmed


def test_youtube_local_candidates_and_real_batch_export(client, monkeypatch, tmp_path, playable_source):
    video_id = "fixture0000"
    downloads = []
    def read_signals(*args, **kwargs):
        assert downloads == []
        return {"title": "Fixture", "duration": 30, "transcript": [], "heatmap": [], "chat_peaks": []}
    monkeypatch.setattr(api.youtube_ingest, "read_caption_track", read_signals)
    monkeypatch.setattr(api, "UPLOADS_DIR", str(tmp_path))
    monkeypatch.setattr(api, "EXPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(api, "get_media_info", real_media_info)
    monkeypatch.setattr(api.discovery_mod, "audio_energy_peaks", lambda *_a, **_kw: [])
    monkeypatch.setattr(api.youtube_ingest, "download", real_download)
    monkeypatch.setattr(yt_dlp, "YoutubeDL", lambda options: downloader(options, downloads, playable_source))

    def transcribe(source_path, **kwargs):
        assert Path(source_path) == tmp_path / "cache" / "videos" / video_id / "source.mp4"
        return {"words": [
            {"token": f"Highlight {start}.", "source_start_us": start * 1_000_000,
             "source_end_us": (start + 1) * 1_000_000} for start in (2, 14, 26)
        ]}

    monkeypatch.setattr(api.transcription, "transcribe_media", transcribe)
    response = client.post("/api/projects/youtube", json={
        "url": f"https://youtu.be/{video_id}", "confirm_full_download": True,
        "generation": {"mode": "auto", "min_duration": 5, "max_duration": 6, "num_clips": 2},
    })
    assert response.status_code == 200
    project_id = response.json()["id"]
    path = f"/api/projects/{project_id}"
    job = wait_for_job(client, project_id, {"ready", "failed"})
    assert job["status"] == "ready" and job["mode"] == "local", job
    result = client.get(f"{path}/candidates").json()
    assert result["status"] == "ready" and len(result["candidates"]) == 2
    assert result["model"] == "local-engine"
    assert all(clip["origin"] == "local" for clip in client.get(path).json()["timeline"])

    # Exercise both individual and combined export with the real FFmpeg binary.
    clip = result["candidates"][0]
    for selection, expected_duration in [
        ({"clip_id": clip["id"]}, 5), ({}, 10),
    ]:
        export = client.post(f"{path}/export", json={"fps": 5, "layout_id": "center_crop", **selection})
        assert export.status_code == 200, export.text
        assert export.json()["status"] == "completed"
        duration, width, height = real_media_info(export.json()["exported_path"])
        assert (width, height) == (1080, 1920)
        assert duration == pytest.approx(expected_duration, abs=0.5)
    assert client.get(f"{path}/exported-video").status_code == 200

    second = client.post("/api/projects/youtube", json={
        "url": f"https://www.youtube.com/watch?v={video_id}", "confirm_full_download": True,
        "generation": {"mode": "full"},
    })
    assert second.status_code == 200
    assert wait_for_job(client, second.json()["id"], {"ready", "failed"})["status"] == "ready"
    assert len(downloads) == 1


def downloader(options, calls, source, failures=0):
    class Downloader:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download):
            calls.append(options)
            path = Path(options["outtmpl"].replace("%(ext)s", "mp4"))
            if len(calls) <= failures:
                path.with_suffix(".part").write_bytes(b"interrupted")
                raise yt_dlp.utils.DownloadError("Unable to download video data: HTTP Error 403: Forbidden")
            path.write_bytes(source.read_bytes())
            return {"id": "fixture0000", "title": "Fixture", "ext": "mp4"}
    return Downloader()


@pytest.mark.parametrize("failures", [1, 2])
def test_download_403_retries_then_repairs_corrupted_cache(monkeypatch, tmp_path, playable_source, failures):
    attempts, sleeps = [], []
    monkeypatch.setattr(yt_dlp, "YoutubeDL", lambda opts: downloader(opts, attempts, playable_source, failures))
    monkeypatch.setattr(api.youtube_ingest, "time", SimpleNamespace(sleep=sleeps.append))
    url = "https://youtu.be/fixture0000"
    first = real_download(url, str(tmp_path))
    assert sleeps == [1, 2][:failures]
    assert all(attempt["source_address"] == "0.0.0.0" for attempt in attempts)
    if failures == 2:
        assert attempts[-1]["format"] != attempts[0]["format"]
    assert real_download(url, str(tmp_path)) == first
    assert len(attempts) == failures + 1
    Path(first["path"]).write_bytes(b"corrupted")
    assert real_download(url, str(tmp_path)) == first
    assert len(attempts) == failures + 2
    assert api.youtube_ingest._valid_source(first["path"])
    assert {path.name for path in Path(first["path"]).parent.iterdir()} == {"source.mp4", "metadata.json"}


@pytest.mark.parametrize("mode", ["full", "auto"])
def test_repeated_source_403_is_fatal_with_diagnostic(client, monkeypatch, tmp_path, mode):
    attempts = []
    monkeypatch.setattr(api, "UPLOADS_DIR", str(tmp_path))
    monkeypatch.setattr(api.youtube_ingest, "download", real_download)
    monkeypatch.setattr(yt_dlp, "YoutubeDL", lambda opts: downloader(opts, attempts, None, failures=3))
    monkeypatch.setattr(api.youtube_ingest, "time", SimpleNamespace(sleep=lambda _: None))
    monkeypatch.setattr(api.youtube_ingest, "read_caption_track", lambda *_: {
        "title": "Signal source", "duration": 120, "heatmap": [{"t_sec": 40}], "transcript": [],
    })
    response = client.post("/api/projects/youtube", json={
        "url": "https://youtu.be/fixture0000", "confirm_full_download": True,
        "generation": {"mode": mode},
    })
    assert response.status_code == 200
    project_id = response.json()["id"]
    job = wait_for_job(client, project_id, {"ready", "failed"})
    assert (job["status"], job["mode"]) == ("failed", mode)
    assert "SOURCE_DOWNLOAD_FAILED" in job["detail"] and "403: Forbidden" in job["detail"]
    project = client.get(f"/api/projects/{project_id}").json()
    assert project["import_status"] == "failed" and project["video_filename"] is None
    assert len(attempts) == 3
    assert list((tmp_path / "cache" / "videos" / "fixture0000").iterdir()) == []


@pytest.mark.parametrize("track_signals,expected_source", [
    ({"heatmap": [{"t_sec": 42, "value": 0.9}]}, "youtube_replay"),
    ({"chat_peaks": [{"t_sec": 42, "value": 1.0}]}, "youtube_chat_replay"),
    ({"heatmap": [{"t_sec": 42, "value": 0.9}], "chat_peaks": [{"t_sec": 42, "value": 1.0}]},
     "youtube_replay_and_chat"),
])
def test_explicit_plan_uses_audience_peaks_without_downloading(client, monkeypatch, track_signals, expected_source):
    monkeypatch.setattr(api.youtube_ingest, "read_caption_track", lambda *_: {
        "title": "Audience activity", "duration": 120, "heatmap": [], "transcript": [], **track_signals,
    })
    def unexpected_download(*args, **kwargs):
        pytest.fail("Signal-first planning should not request video bytes")

    monkeypatch.setattr(api.youtube_ingest, "download", unexpected_download)
    response = client.post("/api/projects/youtube", json={
        "url": "https://youtu.be/fixture0000", "confirm_full_download": True,
        "generation": {"mode": "plan", "min_duration": 15, "max_duration": 30},
    })
    assert response.status_code == 200
    project_id = response.json()["id"]
    job = wait_for_job(client, project_id, {"ready", "failed"})
    assert job["status"] == "ready" and job["mode"] == "plan"
    candidates = client.get(f"/api/projects/{project_id}/candidates").json()["candidates"]
    clip = candidates[0]
    assert clip["start_ms"] < 42_000 < clip["end_ms"]
    assert clip["signal_source"] == expected_source
