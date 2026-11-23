import glob
import hashlib
import json
import logging
import re
import shlex
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from functools import lru_cache
from typing import Callable, List, Literal, Optional
from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field, model_validator

import caption_style as caption_style_mod
import discovery as discovery_mod
import font_library
import layouts as layout_mod
import transcription
import youtube_ingest
from caption_style import CaptionStyle
import audio as audio_mod
from timeline_map import TimelineMap, TimelineSpan, CaptionWord, CaptionGroup

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
UPLOADS_DIR = os.path.join(STORAGE_DIR, "uploads")
EXPORTS_DIR = os.path.join(STORAGE_DIR, "exports")
DB_PATH = os.path.join(STORAGE_DIR, "clipforge.db")

os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(EXPORTS_DIR, exist_ok=True)

# Load the local, git-ignored `.env` (local settings) into the
# environment. The file is never committed and the key is never echoed back
# to the browser: only a boolean "configured" flag is exposed.
ENV_PATH = os.path.join(BASE_DIR, ".env")


def _load_env_file(path: str = ENV_PATH) -> None:
    """Load KEY=VALUE pairs from a local `.env` into ``os.environ``.

    Values already present in the environment win, so a shell export can
    override the file. Malformed lines are skipped rather than fatal.
    """
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        # A broken .env must never stop the studio from starting.
        pass


_load_env_file()

# Preset caption styles offered as starting points in the UI. The user can
# edit every field of any preset and save the result to a project.
CAPTION_PRESET_STYLES: dict = {
    "hormozi-glow": {
        "label": "Hormozi Glow",
        "desc": "Bold uppercase with a tight yellow halo",
        "style": {
            "font_family": "CF Anton 400",
            "font_weight": 400,
            "text_color": "#facc15",
            "glow_color": "#facc15",
            "stroke_color": "#000000",
            "stroke_width": 3.0,
            "glow_radius": 14.0,
            "glow_intensity": 0.9,
            "shadow_depth": 2.0,
            "uppercase": True,
            "karaoke_color": "#ffffff",
            "caption_mode": "chunk",
        },
    },
    "beast-viral": {
        "label": "MrBeast Viral",
        "desc": "Heavy display face with a cyan punch",
        "style": {
            "font_family": "CF Anton 400",
            "font_weight": 400,
            "text_color": "#ffffff",
            "glow_color": "#06b6d4",
            "stroke_color": "#000000",
            "stroke_width": 4.0,
            "glow_radius": 16.0,
            "glow_intensity": 0.85,
            "uppercase": True,
            "karaoke_color": "#facc15",
            "caption_mode": "chunk",
        },
    },
    "cinema-clean": {
        "label": "Cinema Clean",
        "desc": "Minimal white type with a soft shadow",
        "style": {
            "font_family": "CF Inter 400",
            "font_weight": 400,
            "text_color": "#ffffff",
            "glow_color": "#ffffff",
            "glow_enabled": False,
            "stroke_color": "#000000",
            "stroke_width": 1.0,
            "shadow_depth": 4.0,
            "uppercase": False,
            "letter_spacing": 0.5,
            "karaoke_color": "#38bdf8",
            "caption_mode": "chunk",
        },
    },
    "fire-gradient": {
        "label": "Flame Karaoke",
        "desc": "Hot orange halo for punchy key moments",
        "style": {
            "font_family": "CF Anton 400",
            "font_weight": 400,
            "text_color": "#ffedd5",
            "glow_color": "#f97316",
            "stroke_color": "#000000",
            "stroke_width": 3.0,
            "glow_radius": 18.0,
            "glow_intensity": 0.95,
            "uppercase": True,
            "karaoke_color": "#fef08a",
            "caption_mode": "chunk",
        },
    },
    "cyber-neon": {
        "label": "Cyberpunk Neon",
        "desc": "Electric violet glow for high-retention shorts",
        "style": {
            "font_family": "CF Space Grotesk 700",
            "font_weight": 700,
            "text_color": "#fdf4ff",
            "glow_color": "#d946ef",
            "stroke_color": "#000000",
            "stroke_width": 3.0,
            "glow_radius": 20.0,
            "glow_intensity": 0.9,
            "uppercase": True,
            "karaoke_color": "#a7f3d0",
            "caption_mode": "chunk",
        },
    },
    "viral-pop": {
        "label": "Viral Pop",
        "desc": "Bubbly rounded display for playful shorts",
        "style": {
            "font_family": "CF Fredoka 700",
            "font_weight": 700,
            "text_color": "#ffffff",
            "glow_color": "#ec4899",
            "stroke_color": "#1e1b4b",
            "stroke_width": 3.0,
            "glow_radius": 12.0,
            "glow_intensity": 0.8,
            "shadow_depth": 2.0,
            "uppercase": False,
            "letter_spacing": 0.5,
            "karaoke_color": "#fde047",
            "caption_mode": "chunk",
        },
    },
    "clean-creator": {
        "label": "Clean Creator",
        "desc": "Friendly geometric sans for talking head content",
        "style": {
            "font_family": "CF Outfit 700",
            "font_weight": 700,
            "text_color": "#ffffff",
            "glow_color": "#ffffff",
            "glow_enabled": False,
            "stroke_color": "#000000",
            "stroke_width": 2.5,
            "shadow_depth": 3.0,
            "uppercase": False,
            "letter_spacing": 0.5,
            "karaoke_color": "#38bdf8",
            "caption_mode": "phrase",
        },
    },
    "gaming-split": {
        "label": "Gaming Split",
        "desc": "Aggressive outlined gaming type",
        "style": {
            "font_family": "CF Black Ops One 400",
            "font_weight": 400,
            "text_color": "#00ff88",
            "glow_color": "#00ff88",
            "stroke_color": "#000000",
            "stroke_width": 5.0,
            "glow_radius": 14.0,
            "glow_intensity": 0.9,
            "uppercase": True,
            "letter_spacing": 2.0,
            "karaoke_color": "#ffea00",
            "caption_mode": "chunk",
        },
    },
    "podcast-pro": {
        "label": "Podcast Pro",
        "desc": "Editorial serif that keeps long captions readable",
        "style": {
            "font_family": "CF Playfair Display 700",
            "font_weight": 700,
            "text_color": "#fef3c7",
            "glow_color": "#f59e0b",
            "stroke_color": "#000000",
            "stroke_width": 2.0,
            "glow_radius": 8.0,
            "glow_intensity": 0.55,
            "shadow_depth": 3.0,
            "uppercase": False,
            "karaoke_color": "#fbbf24",
            "caption_mode": "phrase",
        },
    },
    "impact": {
        "label": "Impact Slam",
        "desc": "Ultra heavy condensed type for hard cuts",
        "style": {
            "font_family": "CF Archivo Black 400",
            "font_weight": 400,
            "text_color": "#ffffff",
            "glow_color": "#ef4444",
            "stroke_color": "#000000",
            "stroke_width": 6.0,
            "glow_radius": 16.0,
            "glow_intensity": 0.95,
            "shadow_depth": 4.0,
            "uppercase": True,
            "letter_spacing": 0.0,
            "karaoke_color": "#fca5a5",
            "caption_mode": "1-word",
        },
    },
    "minimal-white": {
        "label": "Minimal White",
        "desc": "Quiet monochrome captions for documentary tone",
        "style": {
            "font_family": "CF Inter 400",
            "font_weight": 400,
            "text_color": "#f5f5f5",
            "glow_color": "#f5f5f5",
            "glow_enabled": False,
            "stroke_color": "#000000",
            "stroke_width": 1.0,
            "shadow_depth": 2.0,
            "uppercase": False,
            "letter_spacing": 1.0,
            "karaoke_color": "#a3a3a3",
            "caption_mode": "sentence",
        },
    },
    "documentary": {
        "label": "Documentary",
        "desc": "Lower-third serif for serious explainers",
        "style": {
            "font_family": "CF Libre Baskerville 400",
            "font_weight": 400,
            "text_color": "#fafaf9",
            "glow_color": "#d6d3d1",
            "glow_enabled": False,
            "stroke_color": "#0c0a09",
            "stroke_width": 2.0,
            "shadow_depth": 2.0,
            "alignment": 1,
            "margin_v": 320,
            "margin_h": 120,
            "uppercase": False,
            "letter_spacing": 0.5,
            "karaoke_color": "#fbbf24",
            "caption_mode": "sentence",
        },
    },
    "neon-glow": {
        "label": "Neon Glow",
        "desc": "Saturated pink halo, single crisp glow",
        "style": {
            "font_family": "CF Righteous 400",
            "font_weight": 400,
            "text_color": "#ffffff",
            "glow_color": "#f0abfc",
            "stroke_color": "#701a75",
            "stroke_width": 3.0,
            "glow_radius": 24.0,
            "glow_intensity": 1.0,
            "uppercase": True,
            "karaoke_color": "#22d3ee",
            "caption_mode": "chunk",
        },
    },
    "double-glow": {
        "label": "Double Glow",
        "desc": "Cyan outer bloom over a warm inner core",
        "style": {
            "font_family": "CF Titan One 400",
            "font_weight": 400,
            "text_color": "#ffffff",
            "glow_color": "#22d3ee",
            "stroke_color": "#7c3aed",
            "stroke_width": 4.0,
            "glow_radius": 28.0,
            "glow_intensity": 1.0,
            "shadow_depth": 4.0,
            "uppercase": True,
            "letter_spacing": 1.0,
            "karaoke_color": "#fbbf24",
            "caption_mode": "chunk",
        },
    },
    "karaoke-rainbow": {
        "label": "Karaoke Rainbow",
        "desc": "Colour-cycling highlight on the spoken word",
        "style": {
            "font_family": "CF Baloo 2 700",
            "font_weight": 700,
            "text_color": "#ffffff",
            "glow_color": "#a855f7",
            "stroke_color": "#000000",
            "stroke_width": 3.0,
            "glow_radius": 16.0,
            "glow_intensity": 0.85,
            "uppercase": False,
            "karaoke_color": "#22c55e",
            "caption_mode": "1-word",
        },
    },
}

# Columns of the projects table and their DDL. Used to create the table on a
# fresh database and to add any column that a older database is missing.
PROJECT_COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "name": "TEXT NOT NULL",
    "video_filename": "TEXT",
    "video_path": "TEXT",
    "duration": "REAL DEFAULT 0",
    "width": "INTEGER DEFAULT 0",
    "height": "INTEGER DEFAULT 0",
    "timeline_json": "TEXT DEFAULT '[]'",
    "caption_preset": "TEXT DEFAULT 'hormozi-glow'",
    "caption_style_json": "TEXT DEFAULT '{}'",
    "caption_mode": "TEXT DEFAULT 'chunk'",
    "caption_text": "TEXT DEFAULT ''",
    "words_json": "TEXT DEFAULT '[]'",
    "transcript_segments_json": "TEXT DEFAULT '[]'",
    "discovery_json": "TEXT DEFAULT '{}'",
    "generation_json": "TEXT DEFAULT '{}'",
    "youtube_url": "TEXT",
    "discovery_status": "TEXT DEFAULT 'idle'",
    "discovery_error": "TEXT",
    "heatmap_json": "TEXT DEFAULT '[]'",
    "layout_id": "TEXT DEFAULT 'auto_hybrid'",
    "layout_params_json": "TEXT DEFAULT '{}'",
    "import_status": "TEXT DEFAULT 'idle'",
    "export_status": "TEXT DEFAULT 'idle'",
    "export_progress": "INTEGER DEFAULT 0",
    "exported_path": "TEXT",
    "error_message": "TEXT",
    "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
}


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        video_filename TEXT,
        video_path TEXT,
        duration REAL DEFAULT 0,
        width INTEGER DEFAULT 0,
        height INTEGER DEFAULT 0,
        timeline_json TEXT DEFAULT '[]',
        caption_preset TEXT DEFAULT 'hormozi-glow',
        caption_style_json TEXT DEFAULT '{}',
        caption_mode TEXT DEFAULT 'chunk',
        caption_text TEXT DEFAULT '',
        import_status TEXT DEFAULT 'idle',
        export_status TEXT DEFAULT 'idle',
        export_progress INTEGER DEFAULT 0,
        exported_path TEXT,
        error_message TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()

    # Additive migration: `CREATE TABLE IF NOT EXISTS` never alters an existing
    # table, so a database created by an older build can be missing columns.
    existing = {
        row[1] for row in cursor.execute("PRAGMA table_info(projects)").fetchall()
    }
    for column, ddl in PROJECT_COLUMNS.items():
        if column not in existing:
            # `id` is the primary key and always exists; skip if somehow absent.
            if column == "id":
                continue
            cursor.execute(f"ALTER TABLE projects ADD COLUMN {column} {ddl}")
    conn.commit()
    conn.close()

init_db()

app = FastAPI(title="ClipForge API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_media_info(filepath: str):
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", filepath
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        return 0.0, 0, 0
    data = json.loads(res.stdout)
    duration = float(data.get("format", {}).get("duration", 0.0))
    width, height = 0, 0
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            width = int(stream.get("width", 0))
            height = int(stream.get("height", 0))
            break
    return duration, width, height

class TimelineSegment(BaseModel):
    id: str
    start: float
    end: float
    title: Optional[str] = "Clip"
    origin: Optional[Literal["gemini", "local"]] = None
    layout_id: Optional[str] = None
    caption_preset: Optional[str] = None

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    timeline: Optional[List[TimelineSegment]] = None
    caption_preset: Optional[str] = None
    caption_style: Optional[dict] = None
    caption_mode: Optional[str] = None
    caption_text: Optional[str] = None
    words: Optional[List[CaptionWord]] = None
    layout_id: Optional[str] = None
    layout_params: Optional[dict] = None

class GenerationRequest(BaseModel):
    mode: Literal["auto", "local", "highlights", "full", "transcript", "plan"] = "local"
    model: Optional[str] = None
    language: Optional[Literal["en", "hi", "hinglish"]] = None
    browser: Optional[str] = None
    num_clips: int = Field(default=5, ge=0, le=100)
    all_clips: bool = False
    min_duration: float = Field(default=30.0, ge=5, le=180)
    max_duration: float = Field(default=60.0, ge=5, le=180)
    confirm_full_download: bool = False

    @model_validator(mode="before")
    @classmethod
    def map_legacy_mode(cls, data):
        if isinstance(data, dict) and data.get("mode") == "gemini":
            return {**data, "mode": "local"}
        return data

    @model_validator(mode="after")
    def validate_options(self):
        if self.min_duration > self.max_duration:
            raise ValueError("Minimum clip duration must not exceed the maximum.")
        if self.model is not None and self.model not in transcription.ALLOWED_MODELS:
            raise ValueError("Unsupported Whisper model.")
        return self


class YouTubeImportRequest(BaseModel):
    url: str
    auto_pipeline: bool = False
    prepare_clips: bool = True
    language: Optional[Literal["en", "hi", "hinglish"]] = None
    browser: Optional[str] = None
    num_clips: int = Field(default=5, ge=0, le=100)
    all_clips: bool = False
    generation: Optional[GenerationRequest] = None
    confirm_full_download: bool = False


class ExportRequest(BaseModel):
    clip_id: Optional[str] = None
    crop_mode: str = "crop" # 'crop' | 'fit' | 'blur_pad' | 'split_gaming' | 'split_faces' or any registered layout ID
    layout_id: Optional[str] = None
    layout_params: Optional[dict] = None
    fps: int = 30
    resolution: Literal["auto", "1080x1920"] = "auto"
    caption_preset: Optional[str] = "hormozi-glow"
    caption_style: Optional[dict] = None
    caption_text: Optional[str] = None
    headline_text: Optional[str] = None


class TranscribeRequest(BaseModel):
    model: Optional[str] = None          # tiny | base | small | medium | large-v3
    language: Optional[str] = None       # None = auto-detect
    beam_size: int = 5
    vad_filter: bool = True


class FontInstallRequest(BaseModel):
    family_id: str


class DiscoverRequest(BaseModel):
    """Parameters for local clip discovery."""

    num_clips: int = 5
    all_clips: bool = False
    min_duration: float = 30.0
    max_duration: float = 60.0
    audience_weight: float = 0.10
    manual_peaks: List[float] = []
    force: bool = False


class CaptionStyleUpdate(BaseModel):
    caption_preset: Optional[str] = None
    caption_style: Optional[dict] = None
    caption_text: Optional[str] = None


def _default_style_for_preset(preset_id: str) -> dict:
    """Resolve a preset id into a full CaptionStyle payload."""
    preset = CAPTION_PRESET_STYLES.get(preset_id)
    if preset is None:
        preset = CAPTION_PRESET_STYLES["hormozi-glow"]
    return CaptionStyle(**preset["style"]).model_dump()


def _resolve_style(payload: Optional[dict], preset_id: Optional[str]) -> CaptionStyle:
    """Build a validated CaptionStyle from stored/requested values."""
    if payload:
        try:
            return CaptionStyle(**payload)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid caption style: {exc}")
    return CaptionStyle(**_default_style_for_preset(preset_id or "hormozi-glow"))


def _total_timeline_duration(timeline: List[dict]) -> float:
    """Total output duration of the timeline in seconds."""
    total = 0.0
    for seg in timeline:
        start = max(0.0, float(seg.get("start", 0)))
        end = float(seg.get("end", 0))
        total += max(0.0, end - start)
    return max(total, 0.1)


def _escape_filter_path(path: str) -> str:
    """Escape a filesystem path for use inside an FFmpeg filter argument.

    FFmpeg filter arguments use ':' and '\\' as separators, so both have to be
    escaped. Paths are generated by the app, but escaping keeps a stray colon
    in a directory name from corrupting the filter chain.
    """
    escaped = path.replace("\\", "\\\\")
    escaped = escaped.replace(":", "\\:")
    escaped = escaped.replace("'", "\\'")
    return escaped

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "ClipForge API"}


# --------------------------------------------------------------------------
# Font library
# --------------------------------------------------------------------------

@app.get("/api/fonts")
def get_fonts():
    """List the curated font catalog with install state."""
    try:
        families = font_library.list_families()
    except font_library.FontLibraryError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    categories = sorted({f["category"] for f in families})
    return {
        "families": families,
        "categories": categories,
        "installed_count": sum(1 for f in families if f["installed"]),
        "total": len(families),
    }


@app.post("/api/fonts/install")
def install_font(req: FontInstallRequest):
    """Download and install one font family from the curated catalog."""
    try:
        entry = font_library.install_family(req.family_id)
    except font_library.FontLibraryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "installed", "family": entry}


@app.post("/api/fonts/install-all")
def install_all_fonts():
    """Install every family in the catalog. This downloads a lot of files."""
    result = font_library.install_all()
    installed = sum(1 for r in result["results"] if r["status"] == "installed")
    return {
        "status": "completed",
        "installed": installed,
        "failed": len(result["results"]) - installed,
        "results": result["results"],
    }


@app.delete("/api/fonts/{family_id}")
def uninstall_font(family_id: str):
    """Remove an installed font family."""
    removed = font_library.uninstall_family(family_id)
    return {"status": "removed" if removed else "not-installed", "family_id": family_id}


@app.get("/api/fonts/css")
def get_fonts_css():
    """The generated @font-face stylesheet for the browser preview."""
    css = font_library.generate_css()
    return PlainTextResponse(css, media_type="text/css")


@app.get("/api/fonts/file/{filename}")
def get_font_file(filename: str):
    """Serve an installed font binary to the browser."""
    # Defend against path traversal: only plain file names are accepted.
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid font file name")
    path = os.path.join(font_library.FONTS_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Font file not found")
    return FileResponse(path, media_type="font/ttf")


@app.get("/api/fonts/license/{family_id}")
def get_font_license(family_id: str):
    """Return the licence text bundled with an installed family."""
    manifest = font_library.installed_manifest()
    entry = next((f for f in manifest.get("families", []) if f["id"] == family_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="Font family is not installed")
    path = os.path.join(font_library.LICENSES_DIR, entry.get("license_file", ""))
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Licence file not found")
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return {
            "family_id": family_id,
            "license": entry.get("license"),
            "source_url": entry.get("source_url"),
            "text": fh.read(),
        }

@app.get("/api/projects")
def list_projects():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, video_filename, duration, width, height, caption_preset, import_status, export_status, export_progress, exported_path, updated_at, generation_json FROM projects ORDER BY updated_at DESC")
    rows = cursor.fetchall()
    conn.close()
    projects = [dict(row) for row in rows]
    for project in projects:
        project["generation"] = json.loads(project.pop("generation_json") or "{}") or None
    return projects

@app.post("/api/projects")
def create_project(name: str = Form("Untitled Project"), file: Optional[UploadFile] = File(None)):
    project_id = str(uuid.uuid4())
    video_filename = None
    video_path = None
    duration = 0.0
    width = 0
    height = 0
    timeline = []

    if file and file.filename:
        filename = f"{project_id}_{file.filename}"
        saved_path = os.path.join(UPLOADS_DIR, filename)
        with open(saved_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        duration, width, height = get_media_info(saved_path)
        video_filename = file.filename
        video_path = saved_path
        if duration > 0:
            timeline = [{"id": f"clip-{uuid.uuid4().hex[:6]}", "start": 0.0, "end": round(min(duration, 30.0), 2), "title": "Clip 1"}]

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO projects (id, name, video_filename, video_path, duration, width, height, timeline_json, caption_preset)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (project_id, name, video_filename, video_path, duration, width, height, json.dumps(timeline), "hormozi-glow"))
    conn.commit()
    conn.close()

    return {
        "id": project_id,
        "name": name,
        "video_filename": video_filename,
        "duration": duration,
        "width": width,
        "height": height,
        "timeline": timeline,
        "caption_preset": "hormozi-glow",
        "export_status": "idle"
    }

@app.post("/api/projects/youtube")
def import_youtube(req: YouTubeImportRequest):
    """Start a YouTube import job and return its project id immediately.

    The download itself runs in a background thread: a single video regularly
    takes longer than the 30s timeout of the Next.js rewrite proxy, so the
    browser must not have to wait for the HTTP request to finish.
    """
    url = (req.url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="A YouTube URL is required")
    if not youtube_ingest._is_allowed_url(url):
        raise HTTPException(
            status_code=400,
            detail="Only http(s) YouTube URLs are supported",
        )

    generation = req.generation
    if generation is None and req.auto_pipeline:
        generation = GenerationRequest(mode="auto", language=req.language, num_clips=req.num_clips)
    if generation is not None and generation.mode != "plan" and not req.confirm_full_download:
        raise HTTPException(status_code=400, detail="Confirm the full video download first.")

    project_id = str(uuid.uuid4())
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO projects (id, name, video_filename, video_path, duration, width, height,
                              timeline_json, caption_preset, import_status, error_message, youtube_url)
        VALUES (?, ?, NULL, NULL, 0, 0, 0, '[]', 'hormozi-glow', ?, NULL, ?)
        """,
        (project_id, url[:80], "idle" if generation and generation.mode == "plan" else "queued", url),
    )
    conn.commit()
    conn.close()

    job = None
    if generation is not None:
        if req.browser and not generation.browser:
            generation.browser = req.browser
        job = _queue_generation(project_id, generation, url=url)
    else:
        threading.Thread(
            target=_run_youtube_import,
            args=(project_id, url, req.prepare_clips, req.browser),
            daemon=True,
            name=f"yt-import-{project_id[:8]}",
        ).start()

    return {
        "id": project_id,
        "name": url[:80],
        "import_status": "idle" if generation and generation.mode == "plan" else "queued",
        "timeline": [],
        "generation": job,
    }


def _set_import_status(project_id: str, status: str, error: Optional[str] = None) -> None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE projects SET import_status = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE id = ?",
        (status, error, project_id),
    )
    conn.commit()
    conn.close()


def _run_youtube_import(
    project_id: str,
    url: str,
    prepare_clips: bool = True,
    browser: Optional[str] = None,
) -> None:
    """Download the video and populate the project. Runs off the request thread."""
    try:
        _set_import_status(project_id, "downloading")
        active_browser = browser or _read_youtube_browser()
        if active_browser and active_browser != "none":
            result = youtube_ingest.download(url, UPLOADS_DIR, browser=active_browser)
        else:
            result = youtube_ingest.download(url, UPLOADS_DIR)
        saved_path = result["path"]
        duration, width, height = get_media_info(saved_path)
        if duration <= 0 or width <= 0 or height <= 0:
            raise youtube_ingest.YouTubeIngestError("SOURCE_INVALID: Downloaded file has no playable video stream.")
    except Exception as exc:  # surfaced through the import or generation status
        detail = str(exc)
        if not detail.startswith(("SOURCE_DOWNLOAD_FAILED:", "SOURCE_INVALID:")):
            detail = f"SOURCE_DOWNLOAD_FAILED: {detail}"
        _set_import_status(project_id, "failed", detail[:1000])
        return

    video_filename = os.path.basename(saved_path)
    heatmap = result.get("heatmap", [])

    # Initial candidate grid: fixed windows so the user always has something
    # to scrub through before real AI discovery exists.
    timeline = []
    clip_dur = 30.0
    if prepare_clips and duration > 0:
        num_clips_grid = min(60, max(1, int(duration // clip_dur)))
        for i in range(num_clips_grid):
            c_start = round(i * clip_dur, 2)
            c_end = round(min(duration, (i + 1) * clip_dur), 2)
            if c_end - c_start >= 5.0:
                timeline.append(
                    {
                        "id": f"clip-{i + 1}",
                        "start": c_start,
                        "end": c_end,
                        "title": f"Viral Candidate #{i + 1}",
                    }
                )
    if prepare_clips and not timeline:
        timeline = [
            {
                "id": "clip-1",
                "start": 0.0,
                "end": round(max(duration, 1.0), 2),
                "title": "Clip 1",
            }
        ]

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE projects
        SET name = ?, video_filename = ?, video_path = ?, duration = ?, width = ?, height = ?,
            timeline_json = ?, heatmap_json = ?, import_status = 'ready', error_message = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            result.get("title", "YouTube Video")[:80],
            video_filename,
            saved_path,
            duration,
            width,
            height,
            json.dumps(timeline),
            json.dumps(heatmap),
            project_id,
        ),
    )
    conn.commit()
    conn.close()

@app.get("/api/caption-presets")
def get_caption_presets():
    """Built-in caption presets plus the CSS the preview needs."""
    presets = []
    for preset_id, preset in CAPTION_PRESET_STYLES.items():
        style = CaptionStyle(**preset["style"])
        presets.append(
            {
                "id": preset_id,
                "name": preset["label"],
                "desc": preset["desc"],
                "style": style.model_dump(),
                "preview": {
                    # `font_family` is the raw family name; `css_font_family` is
                    # the quoted value the browser needs (an unquoted
                    # multi-word name ending in a digit is invalid CSS and is
                    # silently dropped, which made every preset fall back to
                    # the system font).
                    "font_family": style.font_family,
                    "css_font_family": caption_style_mod.css_font_family(style.font_family),
                    "font_weight": caption_style_mod.css_font_weight(style.font_weight),
                    "text_color": style.text_color,
                    "glow_color": style.glow_color,
                    "text_shadow": caption_style_mod.preview_text_shadow(style),
                    "stroke": caption_style_mod.preview_stroke(style),
                    # The preset cards are small, so they also carry a scaled
                    # variant of the glow/outline geometry.
                    "text_shadow_scaled": caption_style_mod.preview_text_shadow_scaled(style, 0.16),
                    "stroke_scaled": caption_style_mod.preview_stroke_scaled(style, 0.16),
                },
            }
        )
    return {"presets": presets}


# ---------------------------------------------------------------------------
# Shorts Layout System (39 layouts, auto-hybrid, and analysis)
# ---------------------------------------------------------------------------

@app.get("/api/layouts")
def get_layouts():
    """All 39 Shorts layouts with categories, descriptions, and implementation status."""
    catalog = layout_mod.list_layouts()
    return {
        "total": len(catalog),
        "layouts": catalog,
        "categories": layout_mod.LAYOUT_CATEGORIES,
    }


class ProjectLayoutUpdateRequest(BaseModel):
    layout_id: str
    layout_params: Optional[dict] = None


@app.post("/api/projects/{project_id}/layout")
def set_project_layout(project_id: str, req: ProjectLayoutUpdateRequest):
    """Set the layout and manual adjustment parameters for a project."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM projects WHERE id = ?", (project_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Project not found")

    cid = layout_mod.canonical_layout_id(req.layout_id)
    params_json = json.dumps(req.layout_params or {})
    cursor.execute(
        "UPDATE projects SET layout_id = ?, layout_params_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (cid, params_json, project_id),
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "layout_id": cid, "layout_params": req.layout_params or {}}


@app.get("/api/projects/{project_id}/layout-analysis")
def get_project_layout_analysis(project_id: str, clip_id: Optional[str] = None):
    """Perform or fetch cached video analysis (faces, speakers, scene cuts, auto-hybrid plan)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT video_path, timeline_json, words_json FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")

    video_path, timeline_json, words_json = row
    if not video_path or not os.path.isfile(video_path):
        return {
            "analysis": {
                "face_detected_ratio": 0.0,
                "multi_face_ratio": 0.0,
                "primary_face_cx": 0.5,
                "recommended_layout": "center_crop",
            },
            "auto_hybrid_plan": [],
        }

    cache_path = os.path.join(STORAGE_DIR, f"layout_analysis_{project_id}.json")
    timeline = []
    if timeline_json:
        try:
            timeline = json.loads(timeline_json)
        except Exception:
            timeline = []

    if clip_id is not None:
        timeline = [segment for segment in timeline if segment.get("id") == clip_id]
        if not timeline:
            raise HTTPException(status_code=404, detail="Clip not found in project")
    time_range = (float(timeline[0]["start"]), float(timeline[0]["end"])) if clip_id is not None else None
    analysis = layout_mod.analyze_video(video_path, cache_path=cache_path, time_range=time_range)

    words = []
    if words_json:
        try:
            words = json.loads(words_json)
        except Exception:
            words = []

    hybrid_plan = layout_mod.resolve_auto_hybrid(
        video_path=video_path,
        timeline=timeline,
        words=words,
        analysis=analysis,
        cache_path=cache_path,
    )

    return {
        "analysis": {key: value for key, value in analysis.items() if key != "cache_key"},
        "auto_hybrid_plan": hybrid_plan,
    }


def _write_env_value(key: str, value: str) -> None:
    """Persist a key/value pair in the local `.env` file.

    The file is git-ignored. Existing entries are replaced in place so the
    user never accumulates stale duplicate keys.
    """
    lines: List[str] = []
    if os.path.exists(ENV_PATH):
        try:
            with open(ENV_PATH, "r", encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except OSError:
            lines = []
    replaced = False
    out: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            name = stripped.partition("=")[0].strip()
            if name == key:
                out.append(f"{key}={value}")
                replaced = True
                continue
        out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    with open(ENV_PATH, "w", encoding="utf-8") as handle:
        handle.write("\n".join(out).strip() + "\n")
    try:
        os.chmod(ENV_PATH, 0o600)
    except OSError:
        pass


class YouTubeSettingsRequest(BaseModel):
    browser: str = "none"


def _read_youtube_browser() -> str:
    val = (os.environ.get("YOUTUBE_COOKIE_BROWSER") or "none").strip().lower()
    return val if val in youtube_ingest.SUPPORTED_BROWSERS else "none"


@app.get("/api/settings/youtube")
def get_youtube_settings():
    """Report the configured browser for YouTube authentication and detected browsers."""
    return {
        "browser": _read_youtube_browser(),
        "available_browsers": youtube_ingest._detect_available_browsers(),
        "supported_browsers": list(youtube_ingest.SUPPORTED_BROWSERS),
    }


@app.post("/api/settings/youtube")
def update_youtube_settings(req: YouTubeSettingsRequest):
    """Update the local browser used for YouTube cookie extraction."""
    browser = (req.browser or "none").strip().lower()
    if browser not in youtube_ingest.SUPPORTED_BROWSERS:
        raise HTTPException(status_code=400, detail=f"Unsupported browser '{browser}'")
    _write_env_value("YOUTUBE_COOKIE_BROWSER", browser)
    os.environ["YOUTUBE_COOKIE_BROWSER"] = browser
    return {
        "browser": browser,
        "available_browsers": youtube_ingest._detect_available_browsers(),
        "supported_browsers": list(youtube_ingest.SUPPORTED_BROWSERS),
    }


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    data = dict(row)
    data["generation"] = json.loads(data.pop("generation_json") or "{}") or None
    data["timeline"] = json.loads(data.get("timeline_json") or "[]")
    raw_words = json.loads(data.get("words_json") or "[]")
    data["words"] = raw_words
    data["segments"] = json.loads(data.pop("transcript_segments_json") or "[]")

    # Always return a complete, validated style so the client never has to
    # guess at missing fields.
    try:
        stored = json.loads(data.get("caption_style_json") or "{}")
    except json.JSONDecodeError:
        stored = {}
    if stored:
        try:
            style = CaptionStyle(**stored)
        except Exception:
            style = CaptionStyle(
                **_default_style_for_preset(data.get("caption_preset"))
            )
    else:
        style = CaptionStyle(
            **_default_style_for_preset(data.get("caption_preset"))
        )

    if data.get("caption_mode") and "caption_mode" not in stored:
        style.caption_mode = data.get("caption_mode")

    data["layout_id"] = data.get("layout_id") or "center_crop"
    try:
        data["layout_params"] = json.loads(data.get("layout_params_json") or "{}")
    except Exception:
        data["layout_params"] = {}

    data["caption_style"] = style.model_dump()
    data["caption_mode"] = style.caption_mode

    # Recompute mapped words and caption groups through the current timeline map
    tmap = TimelineMap.from_seconds_list(data["timeline"])
    word_objs = [CaptionWord(**w) for w in raw_words]
    mapped_words = tmap.map_words(word_objs)
    caption_groups = TimelineMap.group_words_into_captions(mapped_words, mode=style.caption_mode)
    data["mapped_words"] = [w.model_dump() for w in mapped_words]
    data["caption_groups"] = [cg.model_dump() for cg in caption_groups]
    data.pop("caption_style_json", None)
    return data

@app.post("/api/projects/{project_id}/upload")
def upload_project_media(project_id: str, file: UploadFile = File(...)):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM projects WHERE id = ?", (project_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Project not found")

    filename = f"{project_id}_{file.filename}"
    saved_path = os.path.join(UPLOADS_DIR, filename)
    with open(saved_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    duration, width, height = get_media_info(saved_path)
    timeline = []
    clip_dur = 30.0
    num_clips = min(60, max(1, int(duration // clip_dur)))
    for i in range(num_clips):
        c_start = round(i * clip_dur, 2)
        c_end = round(min(duration, (i + 1) * clip_dur), 2)
        if c_end - c_start >= 5.0:
            timeline.append({
                "id": f"clip-{i+1}",
                "start": c_start,
                "end": c_end,
                "title": f"Clip #{i+1}"
            })

    cursor.execute("""
    UPDATE projects 
    SET video_filename = ?, video_path = ?, duration = ?, width = ?, height = ?, timeline_json = ?, updated_at = CURRENT_TIMESTAMP
    WHERE id = ?
    """, (file.filename, saved_path, duration, width, height, json.dumps(timeline), project_id))
    conn.commit()
    conn.close()

    return {
        "id": project_id,
        "video_filename": file.filename,
        "duration": duration,
        "width": width,
        "height": height,
        "timeline": timeline
    }

@app.put("/api/projects/{project_id}")
def update_project(project_id: str, update: ProjectUpdate):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM projects WHERE id = ?", (project_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Project not found")

    fields = []
    params = []
    if update.name is not None:
        fields.append("name = ?")
        params.append(update.name)
    if update.timeline is not None:
        fields.append("timeline_json = ?")
        params.append(json.dumps([s.model_dump(exclude_none=True) for s in update.timeline]))
    if update.caption_preset is not None:
        fields.append("caption_preset = ?")
        params.append(update.caption_preset)
    if update.caption_mode is not None:
        fields.append("caption_mode = ?")
        params.append(update.caption_mode)
    if update.caption_style is not None:
        # Validate before storing so an invalid colour never reaches the DB.
        style = _resolve_style(update.caption_style, update.caption_preset)
        fields.append("caption_style_json = ?")
        params.append(json.dumps(style.model_dump()))
        fields.append("caption_mode = ?")
        params.append(style.caption_mode)
    if update.caption_text is not None:
        fields.append("caption_text = ?")
        params.append(update.caption_text[:500])
    if update.words is not None:
        fields.append("words_json = ?")
        params.append(json.dumps([w.model_dump() for w in update.words]))
    if update.layout_id is not None:
        fields.append("layout_id = ?")
        params.append(layout_mod.canonical_layout_id(update.layout_id))
    if update.layout_params is not None:
        fields.append("layout_params_json = ?")
        params.append(json.dumps(update.layout_params))

    if fields:
        fields.append("updated_at = CURRENT_TIMESTAMP")
        query = f"UPDATE projects SET {', '.join(fields)} WHERE id = ?"
        params.append(project_id)
        cursor.execute(query, params)
        conn.commit()

    conn.close()
    return {"status": "ok"}

@app.get("/api/projects/{project_id}/video")
def get_project_video(project_id: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT video_path FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    conn.close()
    if not row or not row[0] or not os.path.exists(row[0]):
        raise HTTPException(status_code=404, detail="Video file not found")
    return FileResponse(row[0])

@app.get("/api/projects/{project_id}/exported-video")
def get_exported_video(project_id: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT exported_path FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    conn.close()
    if not row or not row[0] or not os.path.exists(row[0]):
        raise HTTPException(status_code=404, detail="Exported video file not found")
    return FileResponse(row[0], media_type="video/mp4", filename=os.path.basename(row[0]))

@app.get("/api/transcription/models")
def list_transcription_models():
    """Models available for transcription, plus the detected device."""
    device, compute = transcription.detect_device()
    return {
        "models": list(transcription.ALLOWED_MODELS),
        "default_model": transcription.DEFAULT_MODEL,
        "device": device,
        "compute_type": compute,
        "cpu_threads": transcription.DEFAULT_CPU_THREADS,
    }


_asr_slot = threading.Lock()


@app.post("/api/projects/{project_id}/transcribe")
def transcribe_project(project_id: str, options: TranscribeRequest):
    """Transcribe the project's source media into word-level timestamps.

    The words are stored on the project as `words_json` and are returned
    together with their timeline-mapped output positions and caption groups,
    so the client can render karaoke captions without recomputing anything.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT video_path, timeline_json FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    if not row or not row[0] or not os.path.exists(row[0]):
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="Cannot transcribe: Project has no valid source media",
        )
    video_path = row[0]
    timeline = json.loads(row[1] or "[]")

    cursor.execute(
        "UPDATE projects SET export_status = 'idle', error_message = NULL, updated_at = CURRENT_TIMESTAMP "
        "WHERE id = ?",
        (project_id,),
    )
    conn.commit()
    conn.close()

    model_size = options.model or transcription.DEFAULT_MODEL
    try:
        with _asr_slot:
            result = transcription.transcribe_media(
                video_path,
                model_size=model_size,
                language=options.language,
                beam_size=max(1, min(int(options.beam_size), 10)),
                vad_filter=options.vad_filter,
            )
    except transcription.TranscriptionError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - surface any model failure cleanly
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}")

    words = result["words"]

    # Persist the words (source timings) on the project.
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE projects SET words_json = ?, transcript_segments_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (json.dumps(words), json.dumps(result.get("segments", [])), project_id),
    )
    conn.commit()
    conn.close()

    # Map the words onto the current output timeline and group them.
    tmap = TimelineMap.from_seconds_list(timeline)
    word_objs = [CaptionWord(**w) for w in words]
    mapped_words = tmap.map_words(word_objs)

    c_mode = "chunk"
    conn_m = sqlite3.connect(DB_PATH)
    cur_m = conn_m.cursor()
    cur_m.execute("SELECT caption_mode, caption_style_json FROM projects WHERE id = ?", (project_id,))
    row_m = cur_m.fetchone()
    conn_m.close()
    if row_m:
        if row_m[0]:
            c_mode = row_m[0]
        elif row_m[1]:
            try:
                c_mode = json.loads(row_m[1]).get("caption_mode", "chunk")
            except Exception:
                pass
    groups = TimelineMap.group_words_into_captions(mapped_words, mode=c_mode)

    return {
        "status": "completed",
        "language": result["language"],
        "language_probability": result["language_probability"],
        "duration": result["duration"],
        "model": result["model"],
        "device": result["device"],
        "word_count": len(words),
        "segments": result["segments"],
        "words": words,
        "mapped_words": [w.model_dump() for w in mapped_words],
        "caption_groups": [g.model_dump() for g in groups],
    }


# ---------------------------------------------------------------------------
# AI clip discovery
# ---------------------------------------------------------------------------

# The prompt version is part of the cache key: bumping it invalidates every
# cached discovery result.
DISCOVERY_PROMPT_VERSION = "v5"


def _project_row(project_id: str):
    """Fetch one project row as a dict, or ``None``."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def _set_discovery_state(
    project_id: str,
    status: str,
    error: Optional[str] = None,
    payload: Optional[dict] = None,
) -> None:
    """Persist the discovery job state on the project row."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if payload is None:
        cursor.execute(
            "UPDATE projects SET discovery_status = ?, discovery_error = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (status, error, project_id),
        )
    else:
        cursor.execute(
            "UPDATE projects SET discovery_status = ?, discovery_error = ?, discovery_json = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, error, json.dumps(payload), project_id),
        )
    conn.commit()
    conn.close()


def _source_hash(video_path: str, words: list) -> str:
    """Invalidate reuse when a source file or its transcript changes."""
    stat = os.stat(video_path)
    timings = [(w.get("token"), w.get("source_start_us"), w.get("source_end_us"), w.get("confidence")) for w in words]
    data = json.dumps([video_path, stat.st_size, stat.st_mtime_ns, timings], ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _discover_payload(
    row: dict, options: DiscoverRequest, report: Optional[Callable[[str], None]] = None,
) -> dict:
    """Run or reuse discovery for a saved source and transcript."""
    video_path = row.get("video_path")
    if not video_path or not os.path.isfile(video_path):
        raise ValueError("Project has no playable source media.")
    duration = float(row.get("duration") or 0) or get_media_info(video_path)[0]
    words = json.loads(row.get("words_json") or "[]")

    num_clips = None if options.all_clips else max(1, min(int(options.num_clips), 50))
    min_ms = int(max(5.0, options.min_duration) * 1000)
    max_ms = int(max(options.max_duration, options.min_duration, 5.0) * 1000)
    if int(duration * 1000) < min_ms:
        raise ValueError("The source is shorter than the requested clip duration. Choose a shorter duration.")
    audience_weight = max(0.0, min(float(options.audience_weight), 0.6))
    settings = {
        "num_clips": num_clips,
        "all_clips": options.all_clips,
        "min_ms": min_ms,
        "max_ms": max_ms,
        "audience_weight": audience_weight,
        "manual_peaks": options.manual_peaks,
    }
    peaks_to_use = options.manual_peaks or json.loads(row.get("heatmap_json") or "[]")
    manual_peaks = discovery_mod.parse_manual_peaks(peaks_to_use, duration)
    cache_id = discovery_mod.cache_key(
        _source_hash(video_path, words), "local-engine", DISCOVERY_PROMPT_VERSION,
        {**settings, "heatmap_peaks": manual_peaks},
    )
    cached = json.loads(row.get("discovery_json") or "{}")
    if not options.force and cached.get("cache_id") == cache_id and cached.get("candidates"):
        return cached

    if report:
        report("Measuring audio peaks and scene cuts…")
    energy_peaks, scene_peaks = discovery_mod.extract_media_peaks(video_path)
    result = discovery_mod.generate_local_candidates(
        words=words,
        energy_peaks=energy_peaks,
        duration_ms=int(duration * 1000),
        num_clips=num_clips,
        min_ms=min_ms,
        max_ms=max_ms,
        scene_peaks=scene_peaks,
        heatmap_peaks=manual_peaks,
        audience_weight=audience_weight,
        all_clips=options.all_clips,
    )
    return {
        "cache_id": cache_id,
        "candidates": [c.model_dump() for c in result["candidates"]],
        "dropped": result.get("dropped", 0),
        "model": result.get("model", "local-engine"),
        "prompt_version": DISCOVERY_PROMPT_VERSION,
        "settings": settings,
        "signal_sources": {
            "transcript": bool(words),
            "audio_energy": bool(energy_peaks),
            "scene_cuts": bool(scene_peaks),
            "manual_peaks": bool(manual_peaks),
        },
    }

def _run_discovery(project_id: str, options: DiscoverRequest) -> None:
    row = _project_row(project_id)
    if not row:
        return
    _set_discovery_state(project_id, "running")
    try:
        payload = _discover_payload(row, options)
        if not payload["candidates"]:
            raise ValueError("No usable cuts were found for that duration.")
        _set_discovery_state(project_id, "ready", None, payload)
    except ValueError as exc:
        _set_discovery_state(project_id, "failed", str(exc))
    except Exception:
        logger.exception("Discovery failed for project %s", project_id)
        _set_discovery_state(project_id, "failed", "Clip ranking failed. See the local server log.")


@app.post("/api/projects/{project_id}/discover")
def discover_clips(project_id: str, options: DiscoverRequest):
    """Start a clip-discovery run for the project.

    The call returns immediately and the work happens in a background thread;
    poll ``GET /api/projects/{id}/candidates`` for the result.
    """
    row = _project_row(project_id)
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")

    _set_discovery_state(project_id, "queued")
    thread = threading.Thread(
        target=_run_discovery,
        args=(project_id, options),
        daemon=True,
        name=f"discovery-{project_id[:8]}",
    )
    thread.start()
    return {"status": "running", "project_id": project_id}


@app.get("/api/projects/{project_id}/candidates")
def get_candidates(project_id: str):
    """Return the latest discovery result for the project."""
    row = _project_row(project_id)
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")

    payload = json.loads(row.get("discovery_json") or "{}")
    return {
        "status": row.get("discovery_status", "idle"),
        "error": row.get("discovery_error"),
        "candidates": payload.get("candidates", []),
        "dropped": payload.get("dropped", 0),
        "model": payload.get("model"),
        "settings": payload.get("settings", {}),
        "signal_sources": payload.get("signal_sources", {}),
        "has_transcript": bool(json.loads(row.get("words_json") or "[]")),
    }


@app.post("/api/projects/{project_id}/candidates/apply")
def apply_candidates(project_id: str, candidate_ids: List[str]):
    """Replace the project timeline with the selected candidates.

    Candidates are applied in ranked order and the timeline is rebuilt so the
    export cuts exactly the chosen windows.
    """
    row = _project_row(project_id)
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")

    payload = json.loads(row.get("discovery_json") or "{}")
    candidates = payload.get("candidates", [])
    if not candidates:
        raise HTTPException(status_code=400, detail="No discovery results available")

    wanted = set(candidate_ids or [c["id"] for c in candidates])
    chosen = [c for c in candidates if c.get("id") in wanted]
    if not chosen:
        raise HTTPException(status_code=400, detail="No matching candidates")

    timeline = _discovery_timeline(chosen)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE projects SET timeline_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (json.dumps(timeline), project_id),
    )
    conn.commit()
    conn.close()
    return {"timeline": timeline}


_generation_lock = threading.Lock()
_generation_slot = threading.Lock()
_active_generations: set[str] = set()


def _generation_state(options: GenerationRequest, status: str, detail: str, percent: Optional[int] = None) -> dict:
    return {
        "mode": options.mode, "status": status, "detail": detail, "percent": percent,
        "settings": options.model_dump(exclude={"mode", "confirm_full_download"}),
    }


def _store_generation(project_id: str, state: dict) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE projects SET generation_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(state), project_id),
        )


def _discovery_timeline(candidates: list[dict]) -> list[dict]:
    return [
        {
            "id": candidate["id"],
            "start": candidate["start_ms"] / 1000,
            "end": candidate["end_ms"] / 1000,
            "title": candidate.get("headline") or "Clip",
            "origin": "local",
        }
        for candidate in candidates
    ]


def _youtube_signal_plan(url: str, options: GenerationRequest) -> tuple[dict, dict]:
    browser = options.browser or _read_youtube_browser()
    if browser and browser != "none":
        track = youtube_ingest.read_caption_track(url, options.language, browser=browser)
    else:
        track = youtube_ingest.read_caption_track(url, options.language)
    duration = track["duration"]
    words = discovery_mod.normalize_cue_words(track["transcript"])
    heatmap = [
        peak for peak in discovery_mod.parse_manual_peaks(track["heatmap"], duration)
        if peak.get("value", 1.0) > 0
    ][:10]
    chat = discovery_mod.parse_manual_peaks(track.get("chat_peaks", []), duration)
    result = discovery_mod.generate_local_candidates(
        words=words, energy_peaks=[], duration_ms=int(duration * 1000),
        num_clips=None if options.all_clips else options.num_clips, min_ms=int(options.min_duration * 1000),
        max_ms=int(options.max_duration * 1000), heatmap_peaks=heatmap,
        chat_peaks=chat, audience_source="youtube_replay",
        all_clips=options.all_clips,
    )
    if not result["candidates"]:
        raise ValueError("No usable cuts found from YouTube captions or audience signals.")
    payload = {
        "caption_plan": True, "candidates": [candidate.model_dump() for candidate in result["candidates"]],
        "model": result["model"],
        "words": words,
        "signal_sources": {"transcript": bool(words), "youtube_replay": bool(heatmap), "youtube_chat_replay": bool(chat)},
    }
    return track, payload


def _detect_framing_and_preset(
    video_path: str, title: str, words: list, analysis_cache: str,
) -> tuple[str, str, dict]:
    """Inspect video visual content, title, and speech to determine framing layout and caption preset."""
    analysis = layout_mod.analyze_video(video_path, cache_path=analysis_cache)
    title_lower = (title or "").lower()

    is_streamer = analysis.get("is_streamer_webcam", False)
    multi_ratio = analysis.get("multi_face_ratio", 0.0)
    face_ratio = analysis.get("face_detected_ratio", 0.0)
    primary_face_cx = analysis.get("primary_face_cx", 0.5)

    gaming_terms = ("game", "gaming", "gameplay", "gta", "stream", "twitch", "live", "irl", "valorant", "minecraft", "roblox", "fortnite", "fps")
    podcast_terms = ("podcast", "interview", "conversation", "talk", "dialogue", "show", "ep.", "episode")
    advice_terms = ("how to", "advice", "business", "money", "growth", "guide", "learn", "tips", "secret", "rules", "strategy", "hormozi", "motivation")
    hype_terms = ("react", "reaction", "shout", "crazy", "insane", "challenge", "omg", "intense", "prank", "fire")

    # 1. Gaming / Stream (Webcam detected or gaming in title)
    if is_streamer or any(t in title_lower for t in gaming_terms):
        return (
            "split_gaming",
            "cyber-neon",
            {"cam_pos": "auto", "split_ratio": "30_70"},
        )

    # 2. Podcast / Interview (2 faces detected or podcast in title)
    if multi_ratio >= 0.25 or any(t in title_lower for t in podcast_terms):
        return (
            "split_2_faces",
            "cinema-clean",
            {"split_ratio": "50_50", "left_face_cx": analysis.get("left_face_cx", 0.25), "right_face_cx": analysis.get("right_face_cx", 0.75)},
        )

    # 3. High Energy / Reaction
    if any(t in title_lower for t in hype_terms):
        return (
            "smart_face_crop" if face_ratio >= 0.2 else "dynamic_pan_zoom",
            "fire-gradient",
            {"crop_x": primary_face_cx},
        )

    # 4. Talking Head / Advice / Monologue
    if face_ratio >= 0.3 or any(t in title_lower for t in advice_terms):
        return (
            "smart_face_crop",
            "hormozi-glow",
            {"crop_x": primary_face_cx, "zoom": 1.0},
        )

    # 5. Landscape / Action / General Widescreen
    return (
        "blur_fill",
        "viral-pop",
        {"blur_radius": 25, "fg_scale": 1.0},
    )


def _generation_worker(project_id: str, options: GenerationRequest, url: Optional[str]) -> None:
    stage = "queued"

    def update(status: str, detail: str, percent: Optional[int] = None):
        nonlocal stage
        stage = status
        _store_generation(project_id, _generation_state(options, status, detail, percent))

    try:
        with _generation_slot:
            planned = None
            if options.mode == "local" and url:
                current = _project_row(project_id) or {}
                payload = json.loads(current.get("discovery_json") or "{}")
                if payload.get("caption_plan"):
                    planned = payload
            if options.mode in ("auto", "plan"):
                if not url:
                    raise ValueError("A YouTube link is required for YouTube signal analysis.")
                update("ranking", "Checking YouTube captions, replay peaks, and archived chat…")
                try:
                    track, payload = _youtube_signal_plan(url, options)
                except (ValueError, OSError, youtube_ingest.YouTubeIngestError):
                    if options.mode == "plan":
                        raise
                    options = options.model_copy(update={"mode": "local"})
                else:
                    _set_discovery_state(project_id, "ready", payload=payload)
                    if options.mode == "plan":
                        with sqlite3.connect(DB_PATH) as conn:
                            conn.execute(
                                "UPDATE projects SET name = ?, duration = ?, import_status = 'idle' WHERE id = ?",
                                (track["title"][:80], track["duration"], project_id),
                            )
                        update("ready", "Cut timestamps ready. A local source is required to edit or export.")
                        return
                    planned = payload

            if url:
                update("importing", "Downloading source video…")
                _run_youtube_import(project_id, url, prepare_clips=False, browser=options.browser)

            row = _project_row(project_id)
            if not row:
                return
            video_path = row.get("video_path")
            if not video_path or not os.path.isfile(video_path):
                raise ValueError(row.get("error_message") or "SOURCE_INVALID: Project has no playable source media.")
            duration = float(row["duration"] or 0)
            if duration <= 0:
                raise ValueError("SOURCE_INVALID: The source has no playable duration.")
            if planned:
                # Caption metadata and the downloaded media can disagree on duration.
                duration_ms = int(duration * 1000)
                candidates = [
                    {**candidate, "end_ms": min(candidate["end_ms"], duration_ms)}
                    for candidate in planned["candidates"]
                    if candidate["start_ms"] < min(candidate["end_ms"], duration_ms)
                ]
                planned = {**planned, "candidates": candidates} if candidates else None
                if not planned and options.mode == "auto":
                    options = options.model_copy(update={"mode": "local"})
            if not planned and options.mode in ("local", "highlights") and duration < options.min_duration:
                raise ValueError("The source is shorter than the requested clip duration. Choose a shorter duration.")

            words = json.loads(row.get("words_json") or "[]")
            if options.mode == "auto" and planned and planned.get("words") and not words:
                words = planned["words"]
                serialized = json.dumps(words)
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute("UPDATE projects SET words_json = ? WHERE id = ?", (serialized, project_id))
                row["words_json"] = serialized
            transcript_warning = None
            if options.mode in ("transcript", "local") or (not words and options.mode == "auto" and not planned):
                update("transcribing", "Preparing speech recognition…")
                last_detail = ""
                last_percent = None
                last_report = 0.0

                def progress(fraction: float, message: str) -> None:
                    nonlocal last_detail, last_percent, last_report
                    percent = None
                    if message.startswith("transcribed "):
                        percent = min(100, max(0, round((fraction - 0.35) / 0.6 * 100)))
                        detail = "Transcribing source audio…"
                    elif message == "transcribing":
                        percent = 0
                        detail = "Transcribing source audio…"
                    else:
                        detail = message.capitalize() + "…"
                    now = time.monotonic()
                    if detail != last_detail or (percent != last_percent and now - last_report >= 1):
                        update("transcribing", detail, percent)
                        last_detail, last_percent, last_report = detail, percent, now

                try:
                    with _asr_slot:
                        result = transcription.transcribe_media(
                            video_path, model_size=options.model or transcription.DEFAULT_MODEL,
                            language=options.language,
                            progress=progress,
                        )
                except transcription.TranscriptionError as exc:
                    if options.mode != "local":
                        raise
                    transcript_warning = f"Source transcription unavailable ({exc})."
                else:
                    words = result["words"]
                    serialized = json.dumps(words)
                    with sqlite3.connect(DB_PATH) as conn:
                        written = conn.execute(
                            "UPDATE projects SET words_json = ?, transcript_segments_json = ? "
                            "WHERE id = ? AND video_path = ? AND words_json IS ?",
                            (serialized, json.dumps(result.get("segments", [])), project_id, video_path, row.get("words_json")),
                        ).rowcount
                    if not written:
                        raise ValueError("The source or transcript changed during generation. Retry from the saved project.")
                    row["words_json"] = serialized

            if options.mode == "transcript":
                detail = f"Transcribed {len(words)} words." if words else "No speech detected."
                update("ready", detail)
                return

            payload = None
            if planned or options.mode == "local":
                update("ranking", "Preparing local clip candidates…")
                _set_discovery_state(project_id, "running")
                payload = planned or _discover_payload(
                    row,
                    DiscoverRequest(
                        num_clips=options.num_clips, min_duration=options.min_duration,
                        max_duration=options.max_duration,
                        all_clips=options.all_clips,
                    ),
                    report=lambda detail: update("ranking", detail),
                )
                if not payload["candidates"]:
                    raise ValueError("No usable cuts were found for that duration.")
                timeline = _discovery_timeline(payload["candidates"])
            elif options.mode == "highlights":
                update("local", "Finding audio highlights on this device…")
                timeline = discovery_mod.local_highlights(
                    words, discovery_mod.audio_energy_peaks(video_path), duration,
                    options.min_duration, options.max_duration, options.num_clips,
                    all_clips=options.all_clips,
                )
                if not timeline:
                    raise ValueError("No usable speech or audio peaks were found. Open the editor to trim manually.")
            else:
                timeline = [{"id": uuid.uuid4().hex, "start": 0, "end": duration, "title": row["name"], "origin": "local"}]

            analysis_cache = os.path.join(STORAGE_DIR, f"layout_analysis_{project_id}.json")
            detected_layout, detected_preset, detected_params = _detect_framing_and_preset(
                video_path, row.get("name") or "", words, analysis_cache,
            )

            label = "Full video" if options.mode == "full" else "Local"
            count = len(timeline)
            detail = f"{label}: {count} {'clip' if count == 1 else 'clips'} ready."
            if transcript_warning:
                detail += f" {transcript_warning}"
            ready = _generation_state(options, "ready", detail)
            with sqlite3.connect(DB_PATH) as conn:
                written = conn.execute(
                    "UPDATE projects SET timeline_json = ?, generation_json = ?, layout_id = ?, caption_preset = ?, layout_params_json = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND video_path = ? AND timeline_json IS ? AND words_json IS ?",
                    (json.dumps(timeline), json.dumps(ready), detected_layout, detected_preset, json.dumps(detected_params), project_id, video_path, row.get("timeline_json"), row.get("words_json")),
                ).rowcount
                if not written:
                    raise ValueError("The source, transcript or cuts changed during generation. Existing edits were kept.")
                if payload is not None:
                    conn.execute(
                        "UPDATE projects SET discovery_json = ?, discovery_status = 'ready', discovery_error = NULL WHERE id = ?",
                        (json.dumps(payload), project_id),
                    )
    except Exception as exc:
        if isinstance(exc, (ValueError, transcription.TranscriptionError, youtube_ingest.YouTubeIngestError)):
            detail = str(exc)
        else:
            logger.exception("Generation failed for project %s in stage %s", project_id, stage)
            detail = "Clip generation failed. See the local server log."
        if stage == "ranking":
            _set_discovery_state(project_id, "failed", detail)
        update("failed", detail)
    finally:
        with _generation_lock:
            _active_generations.discard(project_id)


def _queue_generation(project_id: str, options: GenerationRequest, url: Optional[str] = None) -> dict:
    with _generation_lock:
        if project_id in _active_generations:
            raise HTTPException(status_code=409, detail="This project is already generating clips.")
        job = _generation_state(options, "queued", "Waiting for the generation worker…")
        _store_generation(project_id, job)
        _active_generations.add(project_id)
        threading.Thread(
            target=_generation_worker, args=(project_id, options, url), daemon=True,
            name=f"generation-{project_id[:8]}",
        ).start()
    return job


@app.post("/api/projects/{project_id}/generation", status_code=202)
def start_project_generation(project_id: str, options: GenerationRequest):
    if options.mode == "auto":
        raise HTTPException(status_code=400, detail="Use the YouTube import endpoint for automatic source analysis.")
    row = _project_row(project_id)
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    if not row.get("video_path") or not os.path.isfile(row["video_path"]):
        url = row.get("youtube_url")
        if options.mode == "local" and options.confirm_full_download and url and youtube_ingest._is_allowed_url(url):
            return _queue_generation(project_id, options, url=url)
        raise HTTPException(status_code=400, detail="Project has no playable source media.")
    return _queue_generation(project_id, options)


@app.get("/api/projects/{project_id}/generation")
def get_project_generation(project_id: str):
    with _generation_lock:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute("SELECT generation_json FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Project not found")
        job = json.loads(row[0] or "{}")
        if job and job["status"] not in ("ready", "failed") and project_id not in _active_generations:
            job = {
                **job, "status": "failed", "percent": None,
                "detail": "The server restarted during generation. Retry from the saved source or re-enter the link.",
            }
            _store_generation(project_id, job)
        return job or None


_export_lock = threading.Lock()
_rendering_projects: set[str] = set()


@app.post("/api/projects/{project_id}/export")
def export_project(project_id: str, options: ExportRequest):
    with _export_lock:
        if project_id in _rendering_projects:
            raise HTTPException(status_code=409, detail="This project is already rendering.")
        _rendering_projects.add(project_id)
    try:
        return _render_project(project_id, options)
    finally:
        with _export_lock:
            _rendering_projects.discard(project_id)


@lru_cache(maxsize=1)
def _available_h264_encoders() -> set[str]:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, check=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return set(re.findall(r"^\s*V\S*\s+(h264_(?:nvenc|vaapi|qsv))\s", result.stdout, re.MULTILINE))


def _encoder_options():
    available = _available_h264_encoders()
    if "h264_nvenc" in available:
        yield [], "format=yuv420p", ["-c:v", "h264_nvenc", "-preset", "p5",
                                    "-rc", "vbr", "-cq", "19", "-b:v", "0"]
    devices = sorted(glob.glob("/dev/dri/renderD*"))
    if "h264_vaapi" in available:
        for device in devices:
            yield ["-vaapi_device", device], "format=nv12,hwupload", [
                "-c:v", "h264_vaapi", "-rc_mode", "CQP", "-qp", "19",
            ]
    if "h264_qsv" in available:
        for device in devices:
            yield [
                "-init_hw_device", f"vaapi=va:{device}",
                "-init_hw_device", "qsv=qs@va", "-filter_hw_device", "qs",
            ], "format=nv12,hwupload=extra_hw_frames=64", [
                "-c:v", "h264_qsv", "-global_quality", "19",
            ]
    yield [], "format=yuv420p", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]


def _run_encode(cmd: list[str]) -> str | None:
    logger.info("Export FFmpeg command %s", shlex.join(cmd))
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode == 0:
        return None
    error = result.stderr[-400:].strip()
    logger.warning("Export FFmpeg exited %s: %s", result.returncode, error)
    return error


def _cut_input(path: str, start: float, duration: float) -> list[str]:
    return ["-ss", str(start), "-t", str(duration), "-protocol_whitelist", "file", "-i", path]


def _render_cuts(
    path: str, cuts: list[tuple[float, float, str, str]], fps: int,
) -> None:
    output_options = ["-c:a", "aac", "-b:a", "192k", "-r", str(fps)]
    first = min(start for start, _, _, _ in cuts)
    last = max(start + duration for start, duration, _, _ in cuts)
    # Share decode for nearby cuts, while keeping fast seeks for sparse timelines.
    # Complex layouts have internal graph labels and use the per-cut path.
    has_audio = None
    if (2 <= len(cuts) <= 8
            and last - first <= sum(cut[1] for cut in cuts) + 2
            and all(";" not in vf for _, _, vf, _ in cuts)):
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", path], capture_output=True, text=True,
        )
        streams = probe.stdout.splitlines()
        if probe.returncode == 0 and streams.count("video") == 1 and streams.count("audio") <= 1:
            has_audio = "audio" in streams

    # Retry the whole set on the next encoder to keep concat streams compatible.
    for device, upload, encoder in _encoder_options():
        if has_audio is not None:
            count = len(cuts)
            graph = [f"[0:v]split={count}" + "".join(f"[v{i}]" for i in range(count))]
            if has_audio:
                graph.append(f"[0:a]asplit={count}" + "".join(f"[a{i}]" for i in range(count)))
            outputs = []
            for i, (start, duration, vf, out) in enumerate(cuts):
                begin = round(start - first, 6)
                end = round(begin + duration, 6)
                graph.append(f"[v{i}]trim=start={begin}:end={end},setpts=PTS-{begin}/TB,{vf},{upload}[vo{i}]")
                maps = ["-map", f"[vo{i}]"]
                if has_audio:
                    graph.append(f"[a{i}]atrim=start={begin}:end={end},asetpts=PTS-{begin}/TB[ao{i}]")
                    maps += ["-map", f"[ao{i}]"]
                outputs += [*maps, *encoder, *output_options, out]
            if _run_encode([
                "ffmpeg", "-y", *device, *_cut_input(path, first, last - first),
                "-filter_complex_threads", "2", "-filter_complex", "sws_flags=lanczos;" + ";".join(graph),
                *outputs,
            ]) is None:
                return

        for i, (start, duration, vf, out) in enumerate(cuts):
            error = _run_encode([
                "ffmpeg", "-y", *device, *_cut_input(path, start, duration),
                "-sws_flags", "lanczos", "-vf", f"{vf},{upload}", *encoder,
                *output_options, out,
            ])
            if error is not None:
                break
        else:
            return
    raise RuntimeError(f"FFmpeg error processing segment {i}: {error}")


def _render_project(project_id: str, options: ExportRequest):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT video_path, timeline_json, caption_preset, caption_style_json, caption_text, words_json, width, height, layout_id, layout_params_json "
        "FROM projects WHERE id = ?",
        (project_id,),
    )
    row = cursor.fetchone()
    if not row or not row[0] or not os.path.isfile(row[0]):
        conn.close()
        raise HTTPException(status_code=400, detail="SOURCE_INVALID: Cannot export without a valid local source video.")

    video_path = row[0]
    timeline = json.loads(row[1] or "[]")
    if not timeline:
        conn.close()
        raise HTTPException(status_code=400, detail="Cannot export: Timeline is empty")

    if options.clip_id is not None:
        timeline = [segment for segment in timeline if segment.get("id") == options.clip_id]
        if not timeline:
            conn.close()
            raise HTTPException(status_code=404, detail="Clip not found in project")

    stored_preset = row[2] or options.caption_preset or "hormozi-glow"
    try:
        stored_style = json.loads(row[3] or "{}")
    except json.JSONDecodeError:
        stored_style = {}
    caption_text = row[4] or ""
    raw_words = json.loads(row[5] or "[]")

    # The request may override the stored style (e.g. unsaved preview edits).
    style_payload = options.caption_style or stored_style or None
    style = _resolve_style(style_payload, stored_preset)
    if options.caption_text is not None:
        caption_text = options.caption_text

    cursor.execute("UPDATE projects SET export_status = 'processing', export_progress = 10, error_message = NULL WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()

    render_dir = None
    try:
        render_dir = tempfile.TemporaryDirectory(prefix="render-", dir=EXPORTS_DIR)
        export_filename = f"export_{project_id}.mp4"
        out_path = os.path.join(EXPORTS_DIR, export_filename)

        # Determine layout selection and parameters
        selected_layout = options.layout_id or (options.crop_mode if options.crop_mode != "crop" else None) or (row[8] if len(row) > 8 else None) or "auto_hybrid"
        canonical_layout = layout_mod.canonical_layout_id(selected_layout)

        stored_params = {}
        if len(row) > 9 and row[9]:
            try:
                stored_params = json.loads(row[9])
            except Exception:
                stored_params = {}
        layout_params = {**stored_params, **(options.layout_params or {})}

        video_w, video_h = int(row[6] or 1920), int(row[7] or 1080)
        output_size = (
            layout_mod.portrait_export_size(video_w, video_h)
            if options.resolution == "auto" else (1080, 1920)
        )

        analysis_cache = os.path.join(STORAGE_DIR, f"layout_analysis_{project_id}.json")

        word_objs = [CaptionWord(**w) for w in raw_words]
        libass_name = font_library.libass_family_name(style.font_family)
        fonts_dir = font_library.fonts_dir()
        cuts: list[tuple[float, float, str, str]] = []
        for idx, seg in enumerate(timeline):
            seg_start = max(0.0, float(seg.get("start", 0)))
            seg_end = float(seg.get("end", 0))
            seg_duration = max(0.1, seg_end - seg_start)
            seg_out = os.path.join(render_dir.name, f"cut-{idx}.mp4")

            # FFmpeg restarts at t=0 for each cut, including after a source jump.
            ass_events = []
            if word_objs:
                mapped = TimelineMap.from_seconds_list([seg]).map_words(word_objs)
                ass_events = TimelineMap.group_words_into_captions(mapped, mode=style.caption_mode)
            if not ass_events:
                seg_text = caption_text.strip() or seg.get("title") or "DON'T MISS THIS!"
                ass_events = [(0.0, seg_duration, seg_text)]
            subtitle_filter = None
            if ass_events:
                ass_path = os.path.join(render_dir.name, f"captions-{idx}.ass")
                ass = caption_style_mod.build_ass(style, ass_events, libass_font_name=libass_name)
                with open(ass_path, "w", encoding="utf-8") as fh:
                    fh.write(ass)
                subtitle_filter = f"subtitles={_escape_filter_path(ass_path)}:fontsdir={_escape_filter_path(fonts_dir)}"

            seg_layout = options.layout_id or seg.get("layout_id") or canonical_layout
            current_seg_layout = layout_mod.canonical_layout_id(seg_layout)
            analysis_data = {}
            if current_seg_layout in layout_mod.FACE_ANALYSIS_LAYOUTS:
                analysis_data = layout_mod.analyze_video(
                    video_path, cache_path=analysis_cache, time_range=(seg_start, seg_end),
                )
            if current_seg_layout == "auto_hybrid":
                plan = layout_mod.resolve_auto_hybrid(video_path, [seg], words=raw_words, analysis=analysis_data)
                current_seg_layout = plan[0]["layout_id"]

            seg_headline = options.headline_text or seg.get("title") or caption_text

            vf_filter = layout_mod.build_layout_filtergraph(
                layout_id=current_seg_layout,
                video_w=video_w,
                video_h=video_h,
                output_size=output_size,
                duration=seg_duration,
                params=layout_params,
                analysis=analysis_data,
                subtitle_filter=subtitle_filter,
                headline_text=seg_headline,
            )

            cuts.append((seg_start, seg_duration, vf_filter, seg_out))

        _render_cuts(video_path, cuts, options.fps)
        segment_files = [cut[3] for cut in cuts]

        if len(segment_files) == 1:
            rendered = segment_files[0]
        else:
            concat_list_path = os.path.join(render_dir.name, "concat.txt")
            with open(concat_list_path, "w", encoding="utf-8") as manifest:
                for fpath in segment_files:
                    manifest.write(f"file '{os.path.basename(fpath)}'\n")
            rendered = os.path.join(render_dir.name, "combined.mp4")
            concat_cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "1", "-i", concat_list_path, "-c", "copy", rendered]
            res = subprocess.run(concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"FFmpeg error concatenating segments: {res.stderr[-400:]}")
        os.replace(rendered, out_path)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE projects 
        SET export_status = 'completed', export_progress = 100, exported_path = ?, error_message = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (out_path, project_id))
        conn.commit()
        conn.close()

        return {"status": "completed", "exported_path": out_path}

    except Exception as e:
        detail = f"FFMPEG_RENDER_FAILED: {e}"
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE projects SET export_status = 'failed', error_message = ? WHERE id = ?", (detail, project_id))
        conn.commit()
        conn.close()
        raise HTTPException(status_code=500, detail=detail)
    finally:
        if render_dir is not None:
            render_dir.cleanup()
