"""YouTube ingestion for ClipForge.

Uses the yt-dlp Python API instead of the ``yt-dlp`` console script: the
console script is not on the PATH of the running API process (it only exists
inside the project virtualenv), which made the import endpoint fail with
``FileNotFoundError``.

The download runs in a background thread owned by the caller; this module only
knows how to fetch one video and report where it landed.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

# YouTube needs a JavaScript runtime for full format coverage since 2025.
# yt-dlp looks for `deno` by default; Node is the runtime most developers
# already have, so it is offered explicitly.
_JS_RUNTIME_CANDIDATES = ("deno", "node", "bun")

# Prefer the highest resolution yt-dlp can retrieve, including separate streams.
_FORMAT_SELECTOR = "bestvideo*+bestaudio/best"
_VIDEO_EXTENSIONS = ("mp4", "webm", "mkv", "mov", "m4v", "flv", "3gp")

SUPPORTED_BROWSERS = (
    "none",
    "chrome",
    "firefox",
    "brave",
    "edge",
    "chromium",
    "opera",
    "vivaldi",
    "safari",
)


def _detect_available_browsers() -> list[str]:
    """Detect which supported browsers are installed on the local system."""
    found: list[str] = []
    checks = {
        "firefox": [shutil.which("firefox"), os.path.expanduser("~/.mozilla/firefox")],
        "chrome": [
            shutil.which("google-chrome"),
            shutil.which("google-chrome-stable"),
            os.path.expanduser("~/.config/google-chrome"),
        ],
        "brave": [
            shutil.which("brave"),
            shutil.which("brave-browser"),
            os.path.expanduser("~/.config/BraveSoftware"),
        ],
        "edge": [
            shutil.which("microsoft-edge"),
            shutil.which("microsoft-edge-stable"),
            os.path.expanduser("~/.config/microsoft-edge"),
        ],
        "chromium": [
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
            os.path.expanduser("~/.config/chromium"),
        ],
    }
    for name, paths in checks.items():
        if any(p and os.path.exists(p) for p in paths):
            found.append(name)
    return found


_download_lock = threading.Lock()
_logger = logging.getLogger(__name__)

class YouTubeIngestError(RuntimeError):
    """Raised when a video cannot be downloaded."""


def _detect_js_runtime() -> Optional[dict]:
    """Build the yt-dlp ``js_runtimes`` option from a runtime on this machine.

    yt-dlp expects a dict of ``{runtime_name: {"path": ...}}``; an unknown
    runtime name is ignored with a warning, so only names yt-dlp supports are
    offered here.
    """
    for name in _JS_RUNTIME_CANDIDATES:
        path = shutil.which(name)
        if path:
            return {name: {"path": path}}
    return None


def _is_allowed_url(url: str) -> bool:
    """Allow only http(s) YouTube watch/short links.

    yt-dlp performs the actual network access, so the scheme and host are
    restricted here to keep the API from being used as a generic fetcher.
    """
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    allowed = {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "www.youtu.be",
    }
    return host in allowed


def _reject_live(info: dict, *, incomplete: bool = False) -> str | None:
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        return "Use a completed stream or recording."
    return None


def _options(outtmpl: str, browser: Optional[str] = None) -> dict:
    """yt-dlp options for a single-video download."""
    options = {
        "outtmpl": outtmpl,
        "format": _FORMAT_SELECTOR,
        "format_sort": ["res", "fps", "hdr:0"],
        "format_sort_force": True,
        "merge_output_format": "mp4/webm",
        # Make merged MP4 metadata available early for browser playback.
        "postprocessor_args": {"merger+ffmpeg_o": ["-movflags", "-faststart"]},
        "noplaylist": True,
        "match_filter": _reject_live,
        "quiet": True,
        "noprogress": True,
        "restrictfilenames": False,
        "source_address": "0.0.0.0",
        "retries": 1,
        "fragment_retries": 2,
        "skip_unavailable_fragments": False,
        "socket_timeout": 30,
    }
    js_runtime = _detect_js_runtime()
    if js_runtime:
        options["js_runtimes"] = js_runtime

    chosen_browser = (browser or os.environ.get("YOUTUBE_COOKIE_BROWSER") or "none").strip().lower()
    if chosen_browser in SUPPORTED_BROWSERS and chosen_browser != "none":
        options["cookiesfrombrowser"] = (chosen_browser,)

    return options


class _DownloadLog:
    """Keep actionable FFmpeg diagnostics without publishing verbose download logs."""

    merge_error: Optional[str] = None

    def debug(self, message: str) -> None:
        if not message.startswith("[debug] ffmpeg version"):
            return
        stderr = message.lower()
        if "no space left on device" in stderr or "disk quota exceeded" in stderr:
            self.merge_error = "There is not enough free disk space to combine the YouTube audio and video."
        elif "permission denied" in stderr:
            self.merge_error = "FFmpeg could not access the import files. Check permissions on ClipForge's uploads folder."
        elif "invalid data found" in stderr or "partial file" in stderr or "moov atom not found" in stderr:
            self.merge_error = "FFmpeg could not read one of the downloaded streams. Retry the import or use a local copy."
        elif "could not find tag for codec" in stderr or "not supported in container" in stderr:
            self.merge_error = "The downloaded streams could not be combined in a supported format. Import a local video copy."

    # The downloader raises its own errors. Ignore chatty download and debug
    # messages, which can contain temporary media URLs.
    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass


def _locate_download(tmpdir: str, info: dict) -> str:
    """Find the completed output, excluding intermediate audio and video tracks."""
    extension = info.get("ext")
    extensions = (extension, *_VIDEO_EXTENSIONS) if extension in _VIDEO_EXTENSIONS else _VIDEO_EXTENSIONS
    for ext in extensions:
        path = os.path.join(tmpdir, f"source.{ext}")
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
    raise YouTubeIngestError("The download finished but no complete video file was produced.")


def _video_id(url: str) -> str:
    if _is_allowed_url(url):
        parsed = urlparse(url)
        parts = parsed.path.strip("/").split("/")
        if parsed.hostname in ("youtu.be", "www.youtu.be"):
            video_id = parts[0]
        elif parts[0] == "watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif len(parts) == 2 and parts[0] in ("shorts", "live", "embed", "v"):
            video_id = parts[1]
        else:
            video_id = ""
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            return video_id
    raise YouTubeIngestError("SOURCE_DOWNLOAD_FAILED: A single YouTube video link is required.")


def _valid_source(path: str) -> bool:
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return False
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-show_streams",
             "-show_format", "-of", "json", path],
            capture_output=True, text=True, timeout=30,
        )
        if probe.returncode or probe.stderr:
            return False
        info = json.loads(probe.stdout)
        duration = float(info.get("format", {}).get("duration", 0))
        return math.isfinite(duration) and duration > 0 and any(
            stream.get("codec_type") == "video" and stream.get("width", 0) > 0
            and stream.get("height", 0) > 0 for stream in info.get("streams", [])
        )
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        return False


def download(url: str, dest_dir: str, browser: Optional[str] = None) -> dict:
    """Return a validated cached file, or publish a fresh download atomically."""
    import yt_dlp

    video_id = _video_id(url)
    cache_dir = os.path.abspath(os.path.join(dest_dir, "cache", "videos", video_id))
    metadata_path = os.path.join(cache_dir, "metadata.json")
    with _download_lock:
        os.makedirs(cache_dir, exist_ok=True)
        try:
            with open(metadata_path, encoding="utf-8") as metadata:
                result = json.load(metadata)
            extension = result.get("ext")
            if extension in _VIDEO_EXTENSIONS:
                cached = os.path.join(cache_dir, f"source.{extension}")
                if _valid_source(cached):
                    return {**result, "path": cached}
        except (OSError, ValueError, AttributeError):
            pass

        errors: list[str] = []
        error_code = "SOURCE_DOWNLOAD_FAILED"
        for attempt in range(3):
            if attempt:
                time.sleep(2 ** (attempt - 1))
            with tempfile.TemporaryDirectory(prefix=".download-", dir=cache_dir) as scratch:
                outtmpl = os.path.join(scratch, "source.%(ext)s")
                log = _DownloadLog()
                options = {**_options(outtmpl, browser=browser), "verbose": True, "logger": log}
                if attempt == 2:
                    # Favor pre-muxed formats and let installed yt-dlp PO providers
                    # supply tokens for mweb when supported.
                    options["format"] = "best/" + _FORMAT_SELECTOR
                    options["extractor_args"] = {"youtube": {"player_client": ["default", "mweb"]}}
                try:
                    with yt_dlp.YoutubeDL(options) as ydl:
                        info = ydl.extract_info(url, download=True)
                    if not info or info.get("id") != video_id:
                        raise YouTubeIngestError("yt-dlp returned no matching video.")
                    source = _locate_download(scratch, info)
                    if not _valid_source(source):
                        error_code = "SOURCE_INVALID"
                        raise YouTubeIngestError("Downloaded file failed local ffprobe validation.")
                    extension = os.path.splitext(source)[1][1:]
                    result = {
                        "id": video_id, "title": info.get("title") or "YouTube Video",
                        "ext": extension, "heatmap": _extract_heatmap_peaks(info),
                    }
                    final_path = os.path.join(cache_dir, f"source.{extension}")
                    staged_metadata = os.path.join(scratch, "metadata.json")
                    with open(staged_metadata, "w", encoding="utf-8") as metadata:
                        json.dump(result, metadata)
                    os.replace(staged_metadata, metadata_path)
                    os.replace(source, final_path)
                    return {**result, "path": final_path}
                except (yt_dlp.utils.DownloadError, YouTubeIngestError, OSError) as exc:
                    message = log.merge_error or _readable_error(exc)
                    message = re.sub(r"https?://[^\s]+", "[URL redacted]", message)
                    errors.append(message)
                    _logger.warning("YouTube source attempt %s failed: %s", attempt + 1, message)

        diagnostic = "; ".join(dict.fromkeys(errors))
        raise YouTubeIngestError(f"{error_code}: YouTube download failed after 3 attempts. {diagnostic}")


def _extract_heatmap_peaks(info: dict) -> list[dict]:
    """Extract YouTube most-replayed heatmap peaks if available."""
    raw = info.get("heatmap") or []
    if not isinstance(raw, list):
        return []
    peaks: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        val = float(item.get("value", 0.0) or 0.0)
        start_t = float(item.get("start_time", 0.0) or 0.0)
        end_t = float(item.get("end_time", 0.0) or 0.0)
        mid_t = (start_t + end_t) / 2.0 if end_t > start_t else start_t
        peaks.append({"t_sec": round(mid_t, 2), "value": round(val, 3)})
    peaks.sort(key=lambda x: x["value"], reverse=True)
    return peaks


def _readable_error(exc: Exception, merge_error: Optional[str] = None) -> str:
    """Turn a yt-dlp error into a message that helps the user fix the URL."""
    message = str(exc).strip()
    lowered = message.lower()
    if "no space left on device" in lowered or "disk quota exceeded" in lowered:
        return "There is not enough free disk space on the uploads drive."
    if "postprocessing" in lowered or "conversion failed" in lowered:
        return merge_error or (
            "FFmpeg could not combine the YouTube audio and video. "
            "Retry the import or use a local video copy."
        )
    if "private video" in lowered:
        return "That video is private. Only videos you own or have access to can be imported."
    if "members-only" in lowered or "join this channel" in lowered:
        return "That video is members-only and cannot be imported."
    if "not available" in lowered or "unavailable" in lowered or "removed" in lowered:
        return "That video is not available."
    if "could not find" in lowered and "cookies database" in lowered:
        return (
            "Could not locate browser cookies on this device. "
            "Make sure the selected browser is installed and has an active YouTube login, or select None / import a local video."
        )
    if "cookie" in lowered and ("decrypt" in lowered or "keyring" in lowered or "locked" in lowered):
        return (
            "Could not read browser cookies (browser database may be locked). "
            "Please close the browser or select another browser in settings, or import a local video."
        )
    if "did not get any data blocks" in lowered or ("subtitles" in lowered and ("unable to download" in lowered or "not available" in lowered)):
        return (
            "YouTube caption track is unavailable or empty for this video in the selected language. "
            "Try another subtitle language, enable browser authentication, or choose 'Full Video' / 'Import local video' to transcribe audio with Whisper."
        )
    if "sign in" in lowered or "sign-in" in lowered or "confirm you" in lowered or bool(re.search(r"\bbot\b", lowered)):
        return (
            "YouTube requires sign-in or verification for this request. "
            "Select your browser (Chrome or Firefox) in settings to authenticate with your existing session, "
            "or use Import local video to continue with your original or an authorized copy."
        )
    if "unsupported url" in lowered:
        return "That URL is not a supported YouTube link."
    if "age" in lowered and "confirm" in lowered:
        return "That video is age-restricted and cannot be imported anonymously."
    # yt-dlp prefixes its messages with "ERROR: "; strip it for readability.
    if message.startswith("ERROR: "):
        message = message[7:]
    return message[:500] or "The download failed."


def _chat_activity(lines: Iterable[str], duration: float) -> list[dict]:
    """Rank 10-second message bursts using replay offsets, discarding chat text."""
    bins: Counter[int] = Counter()
    for line in lines:
        try:
            replay = json.loads(line).get("replayChatItemAction", {})
            offset = int(replay["videoOffsetTimeMsec"])
            count = sum(
                "liveChatTextMessageRenderer" in action.get("addChatItemAction", {}).get("item", {})
                for action in replay.get("actions", [])
            )
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        if count and 0 <= offset < duration * 1000:
            bins[offset // 10_000] += count
    if not bins:
        return []
    # Compare each bucket to the overall stream's message rate and skip sparse activity.
    threshold = max(5, 2 * bins.total() / max(1, math.ceil(duration / 10)))
    peak = max(bins.values())
    return [
        {"t_sec": min(bucket * 10 + 5, duration), "value": count / peak}
        for bucket, count in bins.most_common(10) if count >= threshold
    ]


def _read_chat_peaks(url: str, directory: str, duration: float, browser: str | None) -> list[dict]:
    command = [
        sys.executable, "-m", "yt_dlp", "--ignore-config", "--skip-download", "--no-playlist",
        "--force-ipv4", "--write-subs", "--sub-langs", "live_chat", "--match-filter", "!is_live",
        "--ignore-no-formats-error", "--retries", "1", "-o", os.path.join(directory, "chat.%(ext)s"),
    ]
    for name, config in (_detect_js_runtime() or {}).items():
        command.extend(["--js-runtimes", f"{name}:{config['path']}"])
    if browser:
        command.extend(["--cookies-from-browser", browser])
    try:
        subprocess.run([*command, "--", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=True, timeout=45)
        with (Path(directory) / "chat.live_chat.json").open(encoding="utf-8") as chat:
            return _chat_activity(chat, duration)
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return []


def read_caption_track(url: str, language: Optional[str] = None, browser: Optional[str] = None) -> dict:
    """Read available caption cues and replay metadata without requesting video streams."""
    import yt_dlp

    if not _is_allowed_url(url):
        raise YouTubeIngestError("Only YouTube URLs are supported.")
    if language in ("hi", "hinglish"):
        target_langs = ["hi", "hi-IN", "hi-Latn"]
    elif language == "en":
        target_langs = ["en", "en-US", "en-GB", "en-orig"]
    else:
        target_langs = ["en", "hi", "en-US"]

    with tempfile.TemporaryDirectory(prefix="clipforge-captions-") as directory:
        opts = _options(os.path.join(directory, "captions.%(ext)s"), browser=browser)
        opts.update(
            skip_download=True,
            ignore_no_formats_error=True,
            writesubtitles=True,
            writeautomaticsub=True,
            subtitleslangs=target_langs,
            subtitlesformat="json3",
            ignoreerrors=True,
        )
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as exc:
            raise YouTubeIngestError(_readable_error(exc)) from exc
        if not isinstance(info, dict) or not info or _reject_live(info):
            raise YouTubeIngestError("Use a completed stream or recording with available captions.")
        tracks = (info.get("requested_subtitles") or {}) if isinstance(info, dict) else {}
        cues = []
        for track in tracks.values():
            if not isinstance(track, dict):
                continue
            path = Path(track.get("filepath") or directory).resolve()
            if not path.is_relative_to(Path(directory).resolve()) or path.suffix != ".json3" or not path.is_file():
                continue
            try:
                events = json.loads(path.read_text(encoding="utf-8")).get("events", [])
                cues = caption_cues(events)
                if cues:
                    break
            except Exception:
                continue
        if not cues:
            for p in Path(directory).glob("*.json3"):
                try:
                    events = json.loads(p.read_text(encoding="utf-8")).get("events", [])
                    cues = caption_cues(events)
                    if cues:
                        break
                except Exception:
                    continue
        heatmap = _extract_heatmap_peaks(info)
        duration = float(info.get("duration") or 0)
        chat_tracks = (info.get("subtitles") or {}).get("live_chat", [])
        chat_peaks = []
        if duration > 0 and any(track.get("protocol") == "youtube_live_chat_replay" for track in chat_tracks):
            browser_cookies = opts.get("cookiesfrombrowser")
            chat_peaks = _read_chat_peaks(url, directory, duration, browser_cookies[0] if browser_cookies else None)
        return {
            "title": str(info.get("title") or "YouTube video"),
            "duration": duration,
            "transcript": cues,
            "heatmap": heatmap,
            "chat_peaks": chat_peaks,
        }


def caption_cues(events: list[dict]) -> list[dict]:
    """Keep actual cue starts for ranking without inventing word timings."""
    cues = []
    for event in events:
        start = event.get("tStartMs")
        if not isinstance(start, int) or start < 0:
            continue
        text = " ".join("".join(segment.get("utf8", "") for segment in event.get("segs", [])).split())
        if text and (not cues or cues[-1]["token"] != text):
            cues.append({"source_start_us": start * 1000, "token": text})
    return cues
