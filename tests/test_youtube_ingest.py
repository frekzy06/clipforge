"""Tests for the YouTube ingestion helpers.

The network is never touched: these cover URL validation, file naming and the
error messages that reach the user.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "services", "api"
))

import youtube_ingest  # noqa: E402


def test_allowed_youtube_urls():
    for good in [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "http://youtube.com/watch?v=abc",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=abc",
    ]:
        assert youtube_ingest._is_allowed_url(good) is True, good


def test_rejected_urls_are_not_youtube():
    for bad in [
        "file:///etc/passwd",
        "http://example.com/x.mp4",
        "https://evil.test/watch?v=1",
        "javascript:alert(1)",
        "",
    ]:
        assert youtube_ingest._is_allowed_url(bad) is False, bad


def test_cache_key_accepts_video_aliases_and_rejects_path_components():
    assert youtube_ingest._video_id("https://www.youtube.com/shorts/fixture0000") == "fixture0000"
    assert youtube_ingest._video_id("https://www.youtube.com/live/fixture0000") == "fixture0000"
    with pytest.raises(youtube_ingest.YouTubeIngestError, match="SOURCE_DOWNLOAD_FAILED"):
        youtube_ingest._video_id("https://www.youtube.com/watch?v=..%2F..%2Fsource")


def test_readable_error_translates_common_failures():
    assert "private" in youtube_ingest._readable_error(
        Exception("ERROR: Private video. Sign in if you've been granted access")
    ).lower()
    assert "members-only" in youtube_ingest._readable_error(
        Exception("ERROR: Join this channel to get access to members-only content")
    ).lower()
    assert "not available" in youtube_ingest._readable_error(
        Exception("ERROR: Video unavailable")
    ).lower()
    assert "Import local video" in youtube_ingest._readable_error(
        Exception("ERROR: Sign in to confirm you’re not a bot")
    )
    assert youtube_ingest._readable_error(Exception("ERROR: Both streams failed")) == "Both streams failed"
    # The generic "ERROR: " prefix is stripped for readability.
    assert youtube_ingest._readable_error(Exception("ERROR: boom")).startswith("boom")


def test_js_runtime_detection_returns_none_or_dict():
    runtime = youtube_ingest._detect_js_runtime()
    assert runtime is None or (
        len(runtime) == 1
        and all(isinstance(k, str) and isinstance(v, dict) for k, v in runtime.items())
    )
    # yt-dlp rejects anything that is not `{runtime: {"path": ...}}`.
    if runtime:
        name, config = next(iter(runtime.items()))
        assert isinstance(name, str)
        assert "path" in config and os.path.exists(config["path"])


def test_options_uses_supported_format_selector():
    options = youtube_ingest._options("/tmp/out.%(ext)s")
    assert options["format"] == "bestvideo*+bestaudio/best"
    assert options["format_sort"][:2] == ["res", "fps"]
    assert options["format_sort_force"]
    assert options["merge_output_format"] == "mp4/webm"
    assert options["postprocessor_args"]["merger+ffmpeg_o"] == ["-movflags", "-faststart"]
    assert options["noplaylist"] is True
    # The outtmpl must land inside the temporary directory, never the uploads
    # directory, so a partial download is never visible as a source file.
    assert options["outtmpl"].startswith("/tmp/out")


def test_options_js_runtimes_is_a_dict():
    """yt-dlp raises "Invalid js_runtimes format" for a list."""
    options = youtube_ingest._options("/tmp/out.%(ext)s")
    if "js_runtimes" in options:
        assert isinstance(options["js_runtimes"], dict)
        for key, value in options["js_runtimes"].items():
            assert isinstance(key, str)
            assert value is None or isinstance(value, dict)


def test_extract_heatmap_peaks():
    # Empty or missing heatmap
    assert youtube_ingest._extract_heatmap_peaks({}) == []
    assert youtube_ingest._extract_heatmap_peaks({"heatmap": "not-a-list"}) == []

    # Valid heatmap data
    info = {
        "heatmap": [
            {"start_time": 0.0, "end_time": 10.0, "value": 0.25},
            {"start_time": 40.0, "end_time": 50.0, "value": 0.95},
            {"start_time": 80.0, "end_time": 90.0, "value": 0.70},
        ]
    }
    peaks = youtube_ingest._extract_heatmap_peaks(info)
    assert len(peaks) == 3
    # Sorted by value descending
    assert peaks[0]["value"] == 0.95
    assert peaks[0]["t_sec"] == 45.0
    assert peaks[1]["value"] == 0.70
    assert peaks[1]["t_sec"] == 85.0



def test_import_stages_on_upload_volume_and_uses_the_completed_file(tmp_path, monkeypatch):
    from pathlib import Path
    import yt_dlp

    class Downloader:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download):
            path = Path(self.options["outtmpl"].replace("%(ext)s", "mp4"))
            assert path.parent.parent == tmp_path / "cache" / "videos" / "fixture0000"
            assert path.name == "source.mp4"
            path.write_bytes(b"finished")
            larger_partial = path.with_name("source.f137.mp4")
            larger_partial.write_bytes(b"incomplete" * 100)
            return {
                "title": "Fixture", "id": "fixture0000", "ext": "mp4",
                "requested_downloads": [{"filepath": str(larger_partial)}],
            }

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Downloader)
    monkeypatch.setattr(youtube_ingest, "_valid_source", lambda _: True)
    result = youtube_ingest.download("https://youtu.be/fixture0000", str(tmp_path))
    assert Path(result["path"]).read_bytes() == b"finished"
    assert {path.name for path in Path(result["path"]).parent.iterdir()} == {"source.mp4", "metadata.json"}


def test_import_merge_error_captures_disk_failure_and_cleans_scratch(tmp_path, monkeypatch):
    import yt_dlp

    class Downloader:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download):
            self.options["logger"].debug("[debug] ffmpeg version 9\nError writing trailer: No space left on device\nConversion failed!")
            raise yt_dlp.utils.DownloadError("ERROR: Postprocessing: Conversion failed!")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Downloader)
    monkeypatch.setattr(youtube_ingest.time, "sleep", lambda _: None)
    with pytest.raises(youtube_ingest.YouTubeIngestError, match="not enough free disk space"):
        youtube_ingest.download("https://youtu.be/fixture0000", str(tmp_path))
    assert list((tmp_path / "cache" / "videos" / "fixture0000").iterdir()) == []


def test_caption_track_requests_only_subtitles(tmp_path, monkeypatch):
    from pathlib import Path
    import yt_dlp

    class Downloader:
        def __init__(self, options):
            assert options["skip_download"] is True
            assert options["subtitlesformat"] == "json3"
            self.path = Path(options["outtmpl"]).with_name("captions.en.json3")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download):
            self.path.write_text('{"events":[{"tStartMs":2500,"segs":[{"utf8":"Test speech"}]}]}')
            return {"duration": 20, "requested_subtitles": {"en": {"filepath": str(self.path)}}}

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Downloader)
    track = youtube_ingest.read_caption_track("https://youtu.be/fixture")
    assert track["transcript"] == [{"source_start_us": 2_500_000, "token": "Test speech"}]


def test_options_do_not_force_ipv4_by_default():
    options = youtube_ingest._options("/tmp/out.%(ext)s")
    assert "source_address" not in options

    ipv4_options = youtube_ingest._options("/tmp/out.%(ext)s", force_ipv4=True)
    assert ipv4_options["source_address"] == "0.0.0.0"


def test_chat_command_does_not_force_ipv4(tmp_path, monkeypatch):
    import subprocess

    seen = []

    def fake_run(command, **kwargs):
        seen.append(command)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(youtube_ingest.subprocess, "run", fake_run)
    youtube_ingest._read_chat_peaks("https://youtu.be/fixture0000", str(tmp_path), 120, None)
    assert "--force-ipv4" not in seen[0]


def test_detect_available_browsers_finds_windows_profiles(monkeypatch, tmp_path):
    local = tmp_path / "Local"
    roaming = tmp_path / "Roaming"
    (local / "BraveSoftware" / "Brave-Browser" / "User Data").mkdir(parents=True)
    (local / "Microsoft" / "Edge" / "User Data").mkdir(parents=True)
    (roaming / "Mozilla" / "Firefox" / "Profiles").mkdir(parents=True)

    monkeypatch.setattr(youtube_ingest.os, "name", "nt")
    monkeypatch.setattr(youtube_ingest.shutil, "which", lambda _name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))

    found = youtube_ingest._detect_available_browsers()
    assert set(found) == {"firefox", "brave", "edge"}


def test_readable_error_handles_windows_cookie_lock_and_dpapi():
    dpapi_msg = youtube_ingest._readable_error(Exception("ERROR: Failed to decrypt with DPAPI"))
    copy_msg = youtube_ingest._readable_error(Exception("PermissionError: Could not copy Chrome cookie database"))
    assert "locked" in dpapi_msg.lower() or "cookies" in dpapi_msg.lower()
    assert "locked" in copy_msg.lower() or "cookies" in copy_msg.lower()


def test_caption_cues_extracts_word_level_offsets_when_present():
    events = [
        {
            "tStartMs": 160,
            "dDurationMs": 3000,
            "segs": [
                {"utf8": "We"},
                {"utf8": " are", "tOffsetMs": 880},
                {"utf8": " back.", "tOffsetMs": 1679},
            ],
        },
        {"tStartMs": 5000, "segs": [{"utf8": "\n"}]},
        {"tStartMs": 6000, "segs": [{"utf8": "Manual line"}]},
    ]
    cues = youtube_ingest.caption_cues(events)
    assert cues == [
        {"source_start_us": 160_000, "token": "We"},
        {"source_start_us": 1_040_000, "token": "are"},
        {"source_start_us": 1_839_000, "token": "back."},
        {"source_start_us": 6_000_000, "token": "Manual line"},
    ]


def test_caption_cues_strips_speaker_chevrons_and_sound_tags():
    events = [
        {
            "tStartMs": 1000,
            "segs": [
                {"utf8": ">>"},
                {"utf8": " [Applause]", "tOffsetMs": 200},
                {"utf8": " >> Wait", "tOffsetMs": 400},
                {"utf8": " what?!", "tOffsetMs": 800},
            ],
        },
        {"tStartMs": 3000, "segs": [{"utf8": ">> [Music] ♪ Hello there"}]},
    ]
    cues = youtube_ingest.caption_cues(events)
    assert cues == [
        {"source_start_us": 1_400_000, "token": "Wait"},
        {"source_start_us": 1_800_000, "token": "what?!"},
        {"source_start_us": 3_000_000, "token": "Hello there"},
    ]


def test_merge_time_ranges_pads_and_coalesces_nearby_windows():
    merged = youtube_ingest.merge_time_ranges(
        [(10.0, 40.0), (41.5, 70.0), (200.0, 230.0)],
        pad_s=2.0,
        gap_s=3.0,
        duration=231.0,
    )
    assert merged == [(8.0, 72.0), (198.0, 231.0)]


def test_download_sections_fetches_ranges_without_full_cache_poisoning(tmp_path, monkeypatch):
    from pathlib import Path
    import yt_dlp

    seen_options = []

    class Downloader:
        def __init__(self, options):
            seen_options.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download):
            sections = seen_options[-1]["download_ranges"]({"duration": 1800}, self)
            assert [(s["start_time"], s["end_time"]) for s in sections] == [(98.0, 142.0), (598.0, 642.0)]
            for idx, _ in enumerate(sections, 1):
                seg_path = Path(seen_options[-1]["outtmpl"].replace("%(section_number)s", str(idx)).replace("%(ext)s", "mp4"))
                seg_path.write_bytes(b"mp4-segment")
            return {"id": "fixture0000", "title": "Long Stream", "ext": "mp4", "heatmap": []}

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Downloader)
    monkeypatch.setattr(youtube_ingest, "_probe_duration", lambda _path: 44.0)
    monkeypatch.setattr(youtube_ingest, "_concat_mp4_segments", lambda _segs, out: Path(out).write_bytes(b"joined-mp4"))
    monkeypatch.setattr(youtube_ingest, "_valid_source", lambda _path: True)

    result = youtube_ingest.download_sections(
        "https://youtu.be/fixture0000",
        str(tmp_path),
        [(98.0, 142.0), (598.0, 642.0)],
        project_id="proj-1",
    )
    assert result["partial"] is True
    assert Path(result["path"]).read_bytes() == b"joined-mp4"
    assert not (tmp_path / "cache" / "videos" / "fixture0000" / "source.mp4").exists()
    assert result["section_map"] == [
        {"source_start": 98.0, "source_end": 142.0, "output_start": 0.0, "output_end": 44.0},
        {"source_start": 598.0, "source_end": 642.0, "output_start": 44.0, "output_end": 88.0},
    ]


def test_options_browser_cookies():
    opts_none = youtube_ingest._options("/tmp/out.%(ext)s", browser="none")
    assert "cookiesfrombrowser" not in opts_none

    opts_firefox = youtube_ingest._options("/tmp/out.%(ext)s", browser="firefox")
    assert opts_firefox.get("cookiesfrombrowser") == ("firefox",)

    opts_chrome = youtube_ingest._options("/tmp/out.%(ext)s", browser="chrome")
    assert opts_chrome.get("cookiesfrombrowser") == ("chrome",)


def test_detect_available_browsers():
    browsers = youtube_ingest._detect_available_browsers()
    assert isinstance(browsers, list)
    assert all(b in youtube_ingest.SUPPORTED_BROWSERS for b in browsers)


def test_readable_error_handles_subtitles_data_blocks_error():
    err = Exception("ERROR: Unable to download video subtitles for 'hi': ERROR: Did not get any data blocks")
    msg = youtube_ingest._readable_error(err)
    assert "unavailable or empty" in msg.lower()
    assert "Whisper" in msg or "Full Video" in msg


def test_yt_dlp_prefers_4k_video_with_separate_audio(tmp_path):
    from yt_dlp import YoutubeDL

    formats = [
        {"format_id": "combined", "width": 1920, "height": 1080,
         "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a"},
        {"format_id": "4k", "width": 3840, "height": 2160,
         "ext": "webm", "vcodec": "vp9", "acodec": "none"},
        {"format_id": "audio", "ext": "webm", "vcodec": "none", "acodec": "opus"},
    ]
    for index, stream in enumerate(formats):
        stream["url"] = f"https://example.invalid/{index}"
    options = youtube_ingest._options(str(tmp_path / "source.%(ext)s"), browser="none")
    with YoutubeDL({**options, "simulate": True}) as downloader:
        selected = downloader.process_ie_result(
            {"id": "fixture0000", "title": "Source quality", "formats": formats},
            download=False,
        )
    assert selected["format_id"] == "4k+audio"
    assert (selected["width"], selected["height"]) == (3840, 2160)


def test_chat_activity_counts_only_timestamped_text_messages():
    import json

    def replay(offset, renderer="liveChatTextMessageRenderer"):
        return json.dumps({"replayChatItemAction": {
            "videoOffsetTimeMsec": str(offset),
            "actions": [{"addChatItemAction": {"item": {renderer: {"id": "message"}}}}],
        }})

    lines = ["bad json", replay(-100), replay(120_000)]
    lines.extend(replay(12_000) for _ in range(3))
    lines.extend(replay(43_000 + i) for i in range(12))
    lines.extend(replay(83_000, "liveChatTickerPaidMessageItemRenderer") for _ in range(30))
    assert youtube_ingest._chat_activity(lines, 120) == [{"t_sec": 45, "value": 1.0}]


def test_chat_timeout_is_optional_and_live_recordings_are_skipped(tmp_path, monkeypatch):
    import subprocess

    def timeout(command, **kwargs):
        assert "--skip-download" in command
        assert kwargs["timeout"] == 45
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(youtube_ingest.subprocess, "run", timeout)
    assert youtube_ingest._read_chat_peaks("https://youtu.be/fixture0000", str(tmp_path), 120, None) == []
    assert youtube_ingest._reject_live({"live_status": "is_upcoming"}, incomplete=True)
    assert youtube_ingest._reject_live({"is_live": True})
    assert youtube_ingest._reject_live({"live_status": "was_live"}) is None


def test_signal_lookup_keeps_replay_and_chat_when_captions_are_missing(monkeypatch):
    import yt_dlp

    class Downloader:
        def __init__(self, options):
            assert options["skip_download"] and options["ignore_no_formats_error"]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download):
            return {
                "duration": 120, "live_status": "was_live",
                "heatmap": [{"start_time": 40, "end_time": 50, "value": 0.9}],
                "subtitles": {"live_chat": [{"protocol": "youtube_live_chat_replay"}]},
            }

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Downloader)
    monkeypatch.setattr(youtube_ingest, "_read_chat_peaks", lambda *_: [{"t_sec": 65, "value": 1.0}])
    signals = youtube_ingest.read_caption_track("https://youtu.be/fixture0000")
    assert signals["transcript"] == []
    assert signals["heatmap"] == [{"t_sec": 45, "value": 0.9}]
    assert signals["chat_peaks"] == [{"t_sec": 65, "value": 1.0}]
