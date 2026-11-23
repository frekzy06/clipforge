"""ClipForge Shorts Layout System.

Defines the registry of all 39 Shorts layouts, provides video face and scene analysis,
and powers a unified FFmpeg rendering engine for 9:16 (1080x1920) vertical exports.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class LayoutDefinition(BaseModel):
    id: str
    name: str
    category: str  # "focus" | "split" | "pip" | "framed" | "dynamic"
    description: str
    implemented: bool
    aspect_ratio: str = "9:16"
    target_resolution: str = "1080x1920"
    default_params: Dict[str, Any] = Field(default_factory=dict)
    parameters_schema: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Registry of 39 Layouts
# ---------------------------------------------------------------------------

LAYOUT_CATEGORIES = {
    "focus": "Single Speaker & Focus",
    "split": "Split Screen & Multi-Cam",
    "pip": "Picture-in-Picture & Overlays",
    "framed": "Framed & Presentation",
    "dynamic": "Dynamic & Hybrid Switching",
}

ALL_LAYOUTS: List[LayoutDefinition] = [
    # --- Category: focus (12 layouts) ---
    LayoutDefinition(
        id="center_crop",
        name="Center Crop",
        category="focus",
        description="Standard 9:16 portrait crop centered on the source video with manual horizontal pan adjustment.",
        implemented=True,
        default_params={"crop_x": 0.5, "zoom": 1.0},
        parameters_schema={
            "crop_x": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.5, "label": "Horizontal Pan"},
            "zoom": {"type": "float", "min": 1.0, "max": 2.0, "default": 1.0, "label": "Zoom"},
        },
    ),
    LayoutDefinition(
        id="blur_fill",
        name="Blur Fill",
        category="focus",
        description="Original video centered with aspect ratio preserved, surrounded by a dynamic moving blurred background.",
        implemented=True,
        default_params={"blur_radius": 25, "fg_scale": 1.0},
        parameters_schema={
            "blur_radius": {"type": "int", "min": 5, "max": 50, "default": 25, "label": "Blur Radius"},
            "fg_scale": {"type": "float", "min": 0.8, "max": 1.2, "default": 1.0, "label": "Foreground Scale"},
        },
    ),
    LayoutDefinition(
        id="fit_pad",
        name="Fit & Pad",
        category="focus",
        description="Full original video visible with clean pillarboxing/letterboxing on a dark matte canvas.",
        implemented=True,
        default_params={"pad_color": "#000000", "scale_ratio": 1.0},
        parameters_schema={
            "pad_color": {"type": "color", "default": "#000000", "label": "Matte Color"},
        },
    ),
    LayoutDefinition(
        id="smart_face_crop",
        name="Smart Face Crop",
        category="focus",
        description="AI face tracking automatically detects and centers the 9:16 crop window on the primary speaker.",
        implemented=True,
        default_params={"face_tracking": True, "manual_offset_x": 0.0},
        parameters_schema={
            "face_tracking": {"type": "bool", "default": True, "label": "Auto Face Tracking"},
            "manual_offset_x": {"type": "float", "min": -0.4, "max": 0.4, "default": 0.0, "label": "Fine Offset"},
        },
    ),
    LayoutDefinition(
        id="active_speaker_crop",
        name="Active Speaker Crop",
        category="focus",
        description="Automatically switches camera focus to whichever speaker is actively talking based on speech timestamps.",
        implemented=True,
        default_params={"speaker_mode": "auto", "smooth_cut_ms": 300},
        parameters_schema={
            "speaker_mode": {"type": "select", "options": ["auto", "speaker_1", "speaker_2"], "default": "auto", "label": "Speaker Target"},
        },
    ),
    LayoutDefinition(
        id="dynamic_pan_zoom",
        name="Dynamic Pan & Zoom",
        category="focus",
        description="Cinematic Ken Burns camera motion with subtle horizontal panning and punch-in across widescreen footage.",
        implemented=True,
        default_params={"motion_style": "ken_burns", "zoom_amount": 1.15, "pan_period": 8.0},
        parameters_schema={
            "zoom_amount": {"type": "float", "min": 1.05, "max": 1.3, "default": 1.15, "label": "Zoom Amount"},
            "pan_period": {"type": "float", "min": 4.0, "max": 15.0, "default": 8.0, "label": "Pan Speed (seconds)"},
        },
    ),
    LayoutDefinition(
        id="face_tracked_punch_in",
        name="Face Punch-In",
        category="focus",
        description="Periodic dramatic punch-in zooms on speaker face during high-energy moments.",
        implemented=False,
    ),
    LayoutDefinition(
        id="manual_focus_crop",
        name="Manual Focus Crop",
        category="focus",
        description="Custom keyframed focal region positioned by the creator.",
        implemented=False,
    ),
    LayoutDefinition(
        id="left_crop",
        name="Left 1/3 Crop",
        category="focus",
        description="Locks crop window to the left third of the landscape footage.",
        implemented=False,
    ),
    LayoutDefinition(
        id="right_crop",
        name="Right 1/3 Crop",
        category="focus",
        description="Locks crop window to the right third of the landscape footage.",
        implemented=False,
    ),
    LayoutDefinition(
        id="cinematic_letterbox",
        name="Cinematic Letterbox",
        category="focus",
        description="2.35:1 anamorphic cinematic letterbox framed centrally with stylized gradient borders.",
        implemented=False,
    ),
    LayoutDefinition(
        id="vertical_fill",
        name="Full Vertical Stretch",
        category="focus",
        description="Stretches aspect ratio vertically to fill entire 9:16 frame.",
        implemented=False,
    ),

    # --- Category: split (8 layouts) ---
    LayoutDefinition(
        id="split_gaming",
        name="Split Gaming (70/30)",
        category="split",
        description="Top 30% webcam reaction facecam, Bottom 70% gameplay focus. The standard gaming Shorts format.",
        implemented=True,
        default_params={"cam_pos": "auto", "split_ratio": "30_70"},
        parameters_schema={
            "cam_pos": {"type": "select", "options": ["auto", "top_left", "top_right", "bottom_left", "bottom_right", "center"], "default": "auto", "label": "Webcam Source Area"},
            "split_ratio": {"type": "select", "options": ["30_70", "40_60", "50_50"], "default": "30_70", "label": "Webcam / Game Ratio"},
        },
    ),
    LayoutDefinition(
        id="split_2_faces",
        name="Split 2-Faces (50/50)",
        category="split",
        description="Top 50% Speaker 1, Bottom 50% Speaker 2. Essential for podcast clips and interview dialogues.",
        implemented=True,
        default_params={"left_face_zoom": 1.0, "right_face_zoom": 1.0},
        parameters_schema={
            "left_face_zoom": {"type": "float", "min": 0.9, "max": 1.3, "default": 1.0, "label": "Speaker 1 Zoom"},
            "right_face_zoom": {"type": "float", "min": 0.9, "max": 1.3, "default": 1.0, "label": "Speaker 2 Zoom"},
        },
    ),
    LayoutDefinition(
        id="split_3_vertical",
        name="3-Way Vertical Split",
        category="split",
        description="Three vertically stacked panels showing host, guest, and shared reference material.",
        implemented=False,
    ),
    LayoutDefinition(
        id="split_4_grid",
        name="2x2 Quad Grid",
        category="split",
        description="Four-quadrant panel layout for roundtable discussions and multi-stream events.",
        implemented=False,
    ),
    LayoutDefinition(
        id="split_diagonal",
        name="Diagonal Split",
        category="split",
        description="Modern angled diagonal split partition separating two camera feeds.",
        implemented=False,
    ),
    LayoutDefinition(
        id="split_uneven_horizontal",
        name="Split 60/40 Asymmetric",
        category="split",
        description="60% primary focus top with 40% secondary contextual view below.",
        implemented=False,
    ),
    LayoutDefinition(
        id="split_side_by_side",
        name="Side-by-Side Dual Column",
        category="split",
        description="Two side-by-side vertical columns for direct comparisons and vs clips.",
        implemented=False,
    ),
    LayoutDefinition(
        id="split_reaction",
        name="Reaction Split",
        category="split",
        description="Creator reaction webcam pinned above source clip with interactive divider.",
        implemented=False,
    ),

    # --- Category: pip (6 layouts) ---
    LayoutDefinition(
        id="floating_facecam",
        name="Floating Facecam (PiP)",
        category="pip",
        description="Full 9:16 background gameplay/screen with a floating bordered facecam in the corner.",
        implemented=True,
        default_params={"pip_pos": "bottom_right", "pip_size": "medium", "border_color": "#ffffff"},
        parameters_schema={
            "pip_pos": {"type": "select", "options": ["bottom_right", "top_right", "bottom_left", "top_left"], "default": "bottom_right", "label": "PiP Position"},
            "pip_size": {"type": "select", "options": ["small", "medium", "large"], "default": "medium", "label": "Facecam Size"},
            "border_color": {"type": "color", "default": "#ffffff", "label": "Border Accent"},
        },
    ),
    LayoutDefinition(
        id="screen_facecam",
        name="Screen Share + Facecam",
        category="pip",
        description="Top 65% screen share or presentation, Bottom 35% presenter webcam with branded divider.",
        implemented=True,
        default_params={"screen_ratio": 65, "background_color": "#0d0f17"},
        parameters_schema={
            "screen_ratio": {"type": "int", "min": 50, "max": 75, "default": 65, "label": "Screen Height (%)"},
        },
    ),
    LayoutDefinition(
        id="pip_bottom_right",
        name="Corner PiP Bottom-Right",
        category="pip",
        description="Fixed compact bottom-right picture-in-picture box.",
        implemented=False,
    ),
    LayoutDefinition(
        id="pip_circle_facecam",
        name="Circular Facecam",
        category="pip",
        description="Circular rounded webcam badge floating with glowing outline.",
        implemented=False,
    ),
    LayoutDefinition(
        id="pip_dual_cam",
        name="Dual PiP Webcams",
        category="pip",
        description="Two floating picture-in-picture badges over background content.",
        implemented=False,
    ),
    LayoutDefinition(
        id="side_panel_facecam",
        name="Sidebar Facecam",
        category="pip",
        description="Right-aligned vertical facecam bar next to compressed presentation content.",
        implemented=False,
    ),

    # --- Category: framed (6 layouts) ---
    LayoutDefinition(
        id="framed_headline",
        name="Framed Landscape + Headline",
        category="framed",
        description="Landscape video centered on 9:16 canvas with a prominent top headline banner and caption area.",
        implemented=True,
        default_params={"headline": "WATCH THIS", "headline_color": "#ffffff", "bg_color": "#0f1117"},
        parameters_schema={
            "headline": {"type": "string", "default": "WATCH THIS", "label": "Headline Text"},
            "headline_color": {"type": "color", "default": "#ffffff", "label": "Text Color"},
            "bg_color": {"type": "color", "default": "#0f1117", "label": "Frame Background"},
        },
    ),
    LayoutDefinition(
        id="framed_card_mockup",
        name="Card / Phone Mockup",
        category="framed",
        description="Appears inside a 3D smartphone or aesthetic glassmorphic card container.",
        implemented=False,
    ),
    LayoutDefinition(
        id="framed_minimal_border",
        name="Minimal Border Frame",
        category="framed",
        description="Thin neon accent outline framing center video on deep dark canvas.",
        implemented=False,
    ),
    LayoutDefinition(
        id="framed_blurred_edges",
        name="Soft Vignette Frame",
        category="framed",
        description="Feathered soft gradient blur surrounding video edges.",
        implemented=False,
    ),
    LayoutDefinition(
        id="retro_crt_frame",
        name="Retro CRT Frame",
        category="framed",
        description="Nostalgic retro television frame with subtle scanline overlays.",
        implemented=False,
    ),
    LayoutDefinition(
        id="polaroid_frame",
        name="Polaroid Frame",
        category="framed",
        description="Vintage white instant photo frame with handwritten font caption area.",
        implemented=False,
    ),

    # --- Category: dynamic (7 layouts) ---
    LayoutDefinition(
        id="auto_hybrid",
        name="Auto Hybrid",
        category="dynamic",
        description="Intelligent AI layout switching that automatically selects optimal layouts (Smart Face, Split 2-Faces, Gaming) based on detected speakers and scenes.",
        implemented=True,
        default_params={"sensitivity": "balanced", "prefer_split_on_multi_face": True},
        parameters_schema={
            "sensitivity": {"type": "select", "options": ["conservative", "balanced", "dynamic"], "default": "balanced", "label": "Switching Pace"},
        },
    ),
    LayoutDefinition(
        id="auto_scene_switch",
        name="Scene Change Dynamic Switch",
        category="dynamic",
        description="Triggers new crop perspective or zoom on every detected scene boundary.",
        implemented=False,
    ),
    LayoutDefinition(
        id="speaker_reaction_flip",
        name="Speaker / Reaction Alternate",
        category="dynamic",
        description="Alternates between speaker punch-in and host reaction cut.",
        implemented=False,
    ),
    LayoutDefinition(
        id="gameplay_event_zoom",
        name="Gameplay Event Zoom",
        category="dynamic",
        description="Punches into gameplay action during peak audio kill/win moments.",
        implemented=False,
    ),
    LayoutDefinition(
        id="kinetic_motion_track",
        name="Kinetic Motion Follower",
        category="dynamic",
        description="Optical-flow camera panning following fast horizontal action across the frame.",
        implemented=False,
    ),
    LayoutDefinition(
        id="carousel_multicam",
        name="Carousel Multicam",
        category="dynamic",
        description="Rotates through multi-cam angles sequentially on rhythmic beat timings.",
        implemented=False,
    ),
    LayoutDefinition(
        id="slideshow_presentation",
        name="Slideshow Deck + Presenter",
        category="dynamic",
        description="Synchronized slide-card transitions with persistent presenter facecam.",
        implemented=False,
    ),
]

# Quick lookup dict
LAYOUT_REGISTRY: Dict[str, LayoutDefinition] = {l.id: l for l in ALL_LAYOUTS}

# Legacy alias mappings for backward compatibility
LEGACY_ALIASES: Dict[str, str] = {
    "crop": "center_crop",
    "blur_pad": "blur_fill",
    "fit": "fit_pad",
    "split_faces": "split_2_faces",
    "split_gaming": "split_gaming",
}


def canonical_layout_id(raw_id: str) -> str:
    """Resolve an incoming layout or legacy crop_mode identifier to its canonical ID."""
    clean = (raw_id or "").strip().lower()
    if clean in LEGACY_ALIASES:
        return LEGACY_ALIASES[clean]
    if clean in LAYOUT_REGISTRY:
        return clean
    return "center_crop"


def get_layout(layout_id: str) -> LayoutDefinition:
    """Retrieve layout definition by ID or legacy alias."""
    cid = canonical_layout_id(layout_id)
    return LAYOUT_REGISTRY.get(cid, LAYOUT_REGISTRY["center_crop"])


def list_layouts() -> List[Dict[str, Any]]:
    """Return catalog of all 39 layouts with full metadata."""
    return [l.model_dump() for l in ALL_LAYOUTS]


# ---------------------------------------------------------------------------
# Video Analysis: Face Detection, Scene Changes, & Speaker Tracking
# ---------------------------------------------------------------------------

FACE_ANALYSIS_LAYOUTS = {
    "smart_face_crop", "active_speaker_crop", "split_2_faces",
    "split_gaming", "floating_facecam", "screen_facecam", "auto_hybrid",
}


def _median_face(faces: List[Dict[str, float]]) -> Optional[Dict[str, float]]:
    if not faces:
        return None
    return {key: round(float(np.median([face[key] for face in faces])), 4) for key in ("cx", "cy", "w", "h")}


def analyze_video(
    video_path: str,
    cache_path: Optional[str] = None,
    max_samples: int = 25,
    time_range: Optional[Tuple[float, float]] = None,
) -> Dict[str, Any]:
    """Perform fast, deterministic video analysis for smart layouts and auto-hybrid.

    Samples up to `max_samples` frames in the requested range or across the video and extracts:
    - Face detection coordinates (normalized X, Y, W, H)
    - Face count summary (single speaker, 2-speaker podcast, streamer webcam)
    - Scene change cuts (color histogram variance)
    - Reuses cached JSON if available.
    """
    try:
        source = [video_path, os.stat(video_path).st_mtime_ns]
    except OSError:
        source = None
    cache_key = [2, source, list(time_range) if time_range else None, max_samples]
    if cache_path and os.path.isfile(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and data.get("cache_key") == cache_key:
                    return data
        except Exception:
            pass  # Fallback to re-analyzing

    result: Dict[str, Any] = {
        "cache_key": cache_key,
        "face_detected_ratio": 0.0,
        "multi_face_ratio": 0.0,
        "average_face_count": 0.0,
        "primary_face_cx": 0.5,
        "primary_face_cy": 0.5,
        "left_face_cx": 0.25,
        "right_face_cx": 0.75,
        "is_streamer_webcam": False,
        "webcam_region": None,
        "primary_face": None,
        "scene_cuts": [],
        "sample_frames_analyzed": 0,
        "recommended_layout": "center_crop",
    }

    if source is None or not os.path.isfile(video_path):
        return result

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return result

    try:
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)

        if total_frames <= 0:
            return result

        first, last = 0, total_frames - 1
        if time_range:
            first = max(0, min(last, int(time_range[0] * fps)))
            last = max(first, min(last, math.ceil(time_range[1] * fps) - 1))
        sample_count = min(max_samples, last - first + 1)
        sample_indices = np.linspace(first, last, num=sample_count, dtype=int).tolist()

        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        face_cascade = cv2.CascadeClassifier(cascade_path)

        analyzed_frames = 0
        prev_hist = None
        scene_cuts: List[float] = []

        primary_faces: List[Dict[str, float]] = []
        all_left_cx: List[float] = []
        all_right_cx: List[float] = []
        frames_with_face = 0
        frames_with_multi = 0
        corner_faces: Dict[Tuple[bool, bool], List[Dict[str, float]]] = {}

        for frame_idx in sample_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue

            analyzed_frames += 1
            timestamp_s = round(frame_idx / fps, 2)

            # 1. Scene cut detection via color histogram delta
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
            cv2.normalize(hist, hist, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
            if prev_hist is not None:
                diff = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA)
                if diff > 0.45:
                    scene_cuts.append(timestamp_s)
            prev_hist = hist

            # 2. Face detection
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # Downscale slightly for speed if frame is large
            if width > 1600:
                scale = 1600 / width
                small = cv2.resize(gray, (0, 0), fx=scale, fy=scale)
            else:
                scale = 1.0
                small = gray

            raw_faces = face_cascade.detectMultiScale(small, scaleFactor=1.15, minNeighbors=4, minSize=(20, 20))

            frame_faces = []
            for (fx, fy, fw, fh) in raw_faces:
                # Map back to original coordinate space
                orig_x = fx / scale
                orig_y = fy / scale
                orig_w = fw / scale
                orig_h = fh / scale

                norm_cx = (orig_x + orig_w / 2.0) / width
                norm_cy = (orig_y + orig_h / 2.0) / height
                norm_w = orig_w / width
                norm_h = orig_h / height

                frame_faces.append({
                    "cx": norm_cx,
                    "cy": norm_cy,
                    "w": norm_w,
                    "h": norm_h,
                    "area": norm_w * norm_h,
                })

            if frame_faces:
                frames_with_face += 1
                frame_faces.sort(key=lambda face: face["area"], reverse=True)
                primary_faces.append(frame_faces[0])
                # Count at most one face per corner per frame so a crowd cannot
                # trigger the webcam layout from a single sample.
                seen_corners = set()
                for face in frame_faces:
                    cx, cy = face["cx"], face["cy"]
                    if face["area"] >= 0.08 or 0.35 <= cx <= 0.65 or 0.4 <= cy <= 0.6:
                        continue
                    corner = (cx < 0.5, cy < 0.5)
                    if corner not in seen_corners:
                        corner_faces.setdefault(corner, []).append(face)
                        seen_corners.add(corner)

                for f in frame_faces:
                    if f["cx"] < 0.5:
                        all_left_cx.append(f["cx"])
                    else:
                        all_right_cx.append(f["cx"])

            if len(frame_faces) >= 2:
                frames_with_multi += 1

        total_samples = max(1, analyzed_frames)
        face_ratio = round(frames_with_face / total_samples, 2)
        multi_ratio = round(frames_with_multi / total_samples, 2)

        result["sample_frames_analyzed"] = analyzed_frames
        result["face_detected_ratio"] = face_ratio
        result["multi_face_ratio"] = multi_ratio
        result["scene_cuts"] = scene_cuts

        primary = _median_face(primary_faces)
        if primary:
            if frames_with_face >= math.ceil(total_samples * 0.25):
                result["primary_face"] = primary
            result["primary_face_cx"] = primary["cx"]
            result["primary_face_cy"] = primary["cy"]
        if all_left_cx:
            result["left_face_cx"] = round(float(np.median(all_left_cx)), 3)
        if all_right_cx:
            result["right_face_cx"] = round(float(np.median(all_right_cx)), 3)

        webcam_faces = max(corner_faces.values(), key=len, default=[])
        if len(webcam_faces) >= max(2, math.ceil(total_samples * 0.25)):
            result["webcam_region"] = _median_face(webcam_faces)
            result["is_streamer_webcam"] = True
            result["recommended_layout"] = "split_gaming"
        elif multi_ratio >= 0.25:
            result["recommended_layout"] = "split_2_faces"
        elif face_ratio >= 0.3:
            result["recommended_layout"] = "smart_face_crop"
        else:
            result["recommended_layout"] = "blur_fill"

        if cache_path and analyzed_frames:
            temp_path = None
            try:
                cache_dir = os.path.dirname(os.path.abspath(cache_path))
                os.makedirs(cache_dir, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache_dir, delete=False) as f:
                    temp_path = f.name
                    json.dump(result, f)
                os.replace(temp_path, cache_path)
            except OSError:
                if temp_path and os.path.exists(temp_path):
                    os.unlink(temp_path)

    finally:
        cap.release()

    return result


def resolve_auto_hybrid(
    video_path: str,
    timeline: List[Dict[str, Any]],
    words: Optional[List[Dict[str, Any]]] = None,
    analysis: Optional[Dict[str, Any]] = None,
    cache_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Determine layout switches for each segment in an Auto-Hybrid composition.

    Combines video computer vision (faces, scene cuts) with speech timestamps to pick
    the most engaging 9:16 layout dynamically:
    - 2 speakers detected / podcast dialogue -> split_2_faces
    - Gamer / streamer webcam pattern -> split_gaming
    - Active speaking dialogue with single face -> smart_face_crop
    - Presentation / headline segment -> framed_headline
    - Landscape / b-roll / action cut -> blur_fill or dynamic_pan_zoom
    """
    if analysis is None:
        analysis = analyze_video(video_path, cache_path=cache_path)

    hybrid_segments: List[Dict[str, Any]] = []

    for idx, seg in enumerate(timeline):
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", 0.0))
        duration = max(0.1, end - start)

        has_speech = any(
            float(word.get("source_start_us", 0)) < end * 1e6
            and float(word.get("source_end_us", 0)) > start * 1e6
            for word in (words or [])
        )

        # Selection rules based on multimodal analysis
        if analysis.get("is_streamer_webcam", False):
            chosen = "split_gaming"
            reason = "Streamer webcam + gameplay layout detected"
        elif analysis.get("multi_face_ratio", 0.0) >= 0.25:
            chosen = "split_2_faces"
            reason = "2 speakers detected in interview/podcast format"
        elif analysis.get("face_detected_ratio", 0.0) >= 0.35:
            # If fast scene cuts or long silence in this segment, add dynamic pan/zoom
            if not has_speech and duration > 4.0:
                chosen = "dynamic_pan_zoom"
                reason = "Ambient cinematic pan for dialogue pause"
            else:
                chosen = "smart_face_crop"
                reason = f"Face tracked speaker (focus x={analysis.get('primary_face_cx', 0.5):.2f})"
        else:
            # No face detected: widescreen landscape or b-roll
            if duration > 5.0:
                chosen = "dynamic_pan_zoom"
                reason = "Dynamic Ken Burns motion for landscape b-roll"
            else:
                chosen = "blur_fill"
                reason = "Blur Fill ambient presentation for landscape shot"

        hybrid_segments.append({
            "segment_index": idx,
            "start": start,
            "end": end,
            "duration": round(duration, 2),
            "layout_id": chosen,
            "reason": reason,
            "params": {
                "face_cx": analysis.get("primary_face_cx", 0.5),
                "left_face_cx": analysis.get("left_face_cx", 0.25),
                "right_face_cx": analysis.get("right_face_cx", 0.75),
            },
        })

    return hybrid_segments


# ---------------------------------------------------------------------------
# Reusable Rendering Engine (FFmpeg Filtergraph Builder)
# ---------------------------------------------------------------------------

def _facecam_filter(
    analysis: Dict[str, Any], position: str, source_aspect: float, output_aspect: float
) -> str:
    face = (analysis.get("webcam_region") or analysis.get("primary_face")) if position == "auto" else None
    ratio = output_aspect / source_aspect
    if face:
        cx, cy = face["cx"], face["cy"]
        width = max(2.6 * face["w"], 2.35 * face["h"] * ratio)
    else:
        cx = {"top_right": 5 / 6, "bottom_right": 5 / 6, "center": 0.5, "left_half": 0.25}.get(position, 1 / 6)
        cy = 0.75 if position.startswith("bottom_") else 0.5 if position in {"center", "left_half"} else 0.25
        width = min(0.5, ratio) if position == "left_half" else min(1 / 3, ratio / 2)
    width = min(width, 1, ratio)
    height = width / ratio
    x = max(0, min(1 - width, cx - width / 2))
    y = max(0, min(1 - height, cy - height / 2))
    return f"crop=w='max(2,iw*{width:.6f})':h='max(2,ih*{height:.6f})':x='iw*{x:.6f}':y='ih*{y:.6f}'"


def portrait_export_size(video_w: int, video_h: int) -> tuple[int, int]:
    """Match the source short edge, with a 1080px floor and even 9:16 dimensions."""
    units = max(60, min(video_w, video_h) // 18)
    return 18 * units, 32 * units


def build_layout_filtergraph(
    layout_id: str,
    video_w: int = 1920,
    video_h: int = 1080,
    duration: float = 30.0,
    params: Optional[Dict[str, Any]] = None,
    analysis: Optional[Dict[str, Any]] = None,
    subtitle_filter: Optional[str] = None,
    headline_text: Optional[str] = None,
    output_size: tuple[int, int] = (1080, 1920),
) -> str:
    """Build a deterministic, production-ready FFmpeg -vf filtergraph for a 9:16 canvas.

    Supports all 12 core layouts with manual adjustments, smart coordinates, and chains
    optional subtitle overlays directly to the final canvas.
    """
    cid = canonical_layout_id(layout_id)
    params = params or {}
    analysis = analysis or {}

    out_w, out_h = output_size
    scale = out_w / 1080
    source_aspect = video_w / max(1, video_h)

    base_filter: str

    if cid == "center_crop":
        crop_x = float(params.get("crop_x", 0.5))
        crop_x = max(0.0, min(1.0, crop_x))
        zoom = float(params.get("zoom", 1.0))
        zoom = max(1.0, min(2.0, zoom))

        # 9:16 crop box width in source space
        # iw / ih aspect check: crop width = ih * 9 / 16 / zoom
        base_filter = (
            f"crop=w='min(iw, ih*9/16/{zoom})':h='min(ih, iw*16/9/{zoom})':"
            f"x='(iw-ow)*{crop_x}':y='(ih-oh)/2',"
            f"scale={out_w}:{out_h}"
        )

    elif cid == "blur_fill":
        blur_r = int(params.get("blur_radius", 25))
        blur_r = max(5, min(50, blur_r))
        fg_scale = float(params.get("fg_scale", 1.0))
        fg_scale = max(0.6, min(1.3, fg_scale))

        blur_r = round(blur_r * scale)
        fg_w = int(out_w * fg_scale)
        fg_h = int(out_h * fg_scale)

        base_filter = (
            f"split[bg_in][fg_in];"
            f"[bg_in]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h},boxblur={blur_r}:5[bg];"
            f"[fg_in]scale={fg_w}:{fg_h}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
        )

    elif cid == "fit_pad":
        pad_color = str(params.get("pad_color", "black"))
        # Sanitize color string for ffmpeg
        if not re.match(r"^([a-zA-Z]+|#?[0-9a-fA-F]{6})$", pad_color):
            pad_color = "black"

        base_filter = (
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
            f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:{pad_color}"
        )

    elif cid == "smart_face_crop":
        # Determine center X from analysis or manual adjustment
        face_cx = float(analysis.get("primary_face_cx", 0.5))
        manual_offset = float(params.get("manual_offset_x", 0.0))
        target_cx = max(0.1, min(0.9, face_cx + manual_offset))

        base_filter = (
            f"crop=w='min(iw,ih*9/16)':h='min(ih,iw*16/9)':"
            f"x='max(0, min(iw-ow, {target_cx}*iw - ow/2))':y='(ih-oh)/2',"
            f"scale={out_w}:{out_h}"
        )

    elif cid == "active_speaker_crop":
        speaker_mode = str(params.get("speaker_mode", "auto"))
        if speaker_mode == "speaker_1":
            focus_x = float(analysis.get("left_face_cx", 0.3))
        elif speaker_mode == "speaker_2":
            focus_x = float(analysis.get("right_face_cx", 0.7))
        else:
            focus_x = float(analysis.get("primary_face_cx", 0.5))

        base_filter = (
            f"crop=w='min(iw,ih*9/16)':h='min(ih,iw*16/9)':"
            f"x='max(0, min(iw-ow, {focus_x}*iw - ow/2))':y='(ih-oh)/2',"
            f"scale={out_w}:{out_h}"
        )

    elif cid == "split_gaming":
        top_ratio = {"40_60": 0.4, "50_50": 0.5}.get(params.get("split_ratio"), 0.3)
        top_h = round(out_h * top_ratio / 2) * 2
        game_h = out_h - top_h
        cam_crop = _facecam_filter(analysis, str(params.get("cam_pos", "auto")), source_aspect, out_w / top_h)
        base_filter = (
            f"split[cam_in][game_in];"
            f"[cam_in]{cam_crop},scale={out_w}:{top_h},setsar=1[cam];"
            f"[game_in]scale={out_w}:{game_h}:force_original_aspect_ratio=increase,crop={out_w}:{game_h},setsar=1[game];"
            f"[cam][game]vstack"
        )

    elif cid == "split_2_faces":
        # Center each half on its detected speaker.
        left = float(analysis.get("left_face_cx", 0.25))
        right = float(analysis.get("right_face_cx", 0.75))
        crop = "crop=w='min(iw,ih*9/8)':h='min(ih,iw*8/9)'"
        center = "x='max(0,min(iw-ow,FOCUS*iw-ow/2))':y='(ih-oh)/2'"
        base_filter = (
            f"split[top_in][bot_in];"
            f"[top_in]{crop}:{center.replace('FOCUS', str(left))},scale={out_w}:{out_h // 2},setsar=1[top];"
            f"[bot_in]{crop}:{center.replace('FOCUS', str(right))},scale={out_w}:{out_h // 2},setsar=1[bot];"
            f"[top][bot]vstack"
        )

    elif cid == "floating_facecam":
        # Main background 9:16 + floating PiP in corner
        pip_pos = str(params.get("pip_pos", "bottom_right"))
        pip_size = str(params.get("pip_size", "medium"))

        if pip_size == "small":
            pw, ph = 300, 400
        elif pip_size == "large":
            pw, ph = 420, 560
        else:  # medium
            pw, ph = 360, 480

        pw, ph = round(pw * scale / 2) * 2, round(ph * scale / 2) * 2
        side, top, bottom = (round(offset * scale) for offset in (40, 80, 200))
        if pip_pos == "top_right":
            pos_expr = f"W-w-{side}:{top}"
        elif pip_pos == "top_left":
            pos_expr = f"{side}:{top}"
        elif pip_pos == "bottom_left":
            pos_expr = f"{side}:H-h-{bottom}"
        else:  # bottom_right
            pos_expr = f"W-w-{side}:H-h-{bottom}"

        border_w = round(4 * scale)
        cam_crop = _facecam_filter(analysis, "auto", source_aspect, pw / ph)
        base_filter = (
            f"split[main_in][pip_in];"
            f"[main_in]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},setsar=1[main];"
            f"[pip_in]{cam_crop},scale={pw}:{ph},setsar=1,"
            f"pad={pw+border_w*2}:{ph+border_w*2}:{border_w}:{border_w}:white[pip];"
            f"[main][pip]overlay={pos_expr}"
        )

    elif cid == "screen_facecam":
        screen_ratio = max(50, min(75, int(params.get("screen_ratio", 65))))
        screen_h = round(out_h * screen_ratio / 200) * 2
        face_h = out_h - screen_h
        position = "auto" if analysis.get("webcam_region") or analysis.get("primary_face") else "left_half"
        cam_crop = _facecam_filter(analysis, position, source_aspect, out_w / face_h)
        base_filter = (
            f"split[screen_in][face_in];"
            f"[screen_in]scale={out_w}:{screen_h}:force_original_aspect_ratio=decrease,"
            f"pad={out_w}:{screen_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[screen];"
            f"[face_in]{cam_crop},scale={out_w}:{face_h},setsar=1[face];"
            f"[screen][face]vstack"
        )

    elif cid == "framed_headline":
        # Centered video padded on the portrait canvas with top headline banner
        title = headline_text or str(params.get("headline", "WATCH THIS"))
        # Escape single quotes and colons for ffmpeg drawtext
        clean_title = title.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:").strip()
        bg_color = str(params.get("bg_color", "#0f1117")).replace("#", "0x")

        base_filter = (
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
            f"pad={out_w}:{out_h}:0:(oh-ih)/2:{bg_color},"
            f"drawtext=text='{clean_title}':fontsize={round(52 * scale)}:fontcolor=white:x=(w-text_w)/2:y={round(180 * scale)}:"
            f"box=1:boxcolor=black@0.65:boxborderw={round(16 * scale)}"
        )

    elif cid == "dynamic_pan_zoom":
        # Smooth Ken Burns oscillating camera pan across widescreen video
        pan_period = float(params.get("pan_period", 8.0))
        pan_period = max(3.0, min(20.0, pan_period))

        base_filter = (
            f"crop=w='ih*9/16':h='ih':"
            f"x='(iw-ow)/2 + (iw-ow)/3.5*sin(2*PI*t/{pan_period})':y='(ih-oh)/2',"
            f"scale={out_w}:{out_h}"
        )

    elif cid == "auto_hybrid":
        # Fallback single filtergraph when auto-hybrid is evaluated for an entire clip
        rec = analysis.get("recommended_layout", "smart_face_crop")
        if rec == "auto_hybrid":
            rec = "smart_face_crop"
        return build_layout_filtergraph(
            layout_id=rec,
            video_w=video_w,
            video_h=video_h,
            duration=duration,
            params=params,
            analysis=analysis,
            subtitle_filter=subtitle_filter,
            headline_text=headline_text,
            output_size=output_size,
        )

    else:
        # Default safety: center crop
        base_filter = f"crop=ih*9/16:ih:(iw-ow)/2:0,scale={out_w}:{out_h}"

    base_filter = f"{base_filter},setsar=1"

    # Chain subtitle filter if provided
    if subtitle_filter:
        return f"{base_filter},{subtitle_filter}"

    return base_filter
