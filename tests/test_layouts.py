"""Unit tests for the modular Shorts Layout System."""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "services", "api"
))

import layouts as layout_mod


def test_registry_contains_exact_39_layouts():
    catalog = layout_mod.list_layouts()
    assert len(catalog) == 39, f"Expected 39 layouts, found {len(catalog)}"


def test_12_layouts_implemented():
    catalog = layout_mod.list_layouts()
    implemented = [l for l in catalog if l["implemented"]]
    unimplemented = [l for l in catalog if not l["implemented"]]

    assert len(implemented) == 12, f"Expected 12 implemented layouts, got {len(implemented)}"
    assert len(unimplemented) == 27, f"Expected 27 unimplemented layouts, got {len(unimplemented)}"

    expected_implemented_ids = {
        "center_crop",
        "blur_fill",
        "fit_pad",
        "smart_face_crop",
        "active_speaker_crop",
        "split_gaming",
        "split_2_faces",
        "floating_facecam",
        "screen_facecam",
        "framed_headline",
        "dynamic_pan_zoom",
        "auto_hybrid",
    }
    implemented_ids = {l["id"] for l in implemented}
    assert implemented_ids == expected_implemented_ids


def test_categories_and_aspect_ratios():
    catalog = layout_mod.list_layouts()
    for item in catalog:
        assert item["aspect_ratio"] == "9:16"
        assert item["target_resolution"] == "1080x1920"
        assert item["category"] in layout_mod.LAYOUT_CATEGORIES
        assert len(item["name"]) > 0
        assert len(item["description"]) > 0


def test_legacy_aliases_resolve_correctly():
    assert layout_mod.canonical_layout_id("crop") == "center_crop"
    assert layout_mod.canonical_layout_id("blur_pad") == "blur_fill"
    assert layout_mod.canonical_layout_id("fit") == "fit_pad"
    assert layout_mod.canonical_layout_id("split_faces") == "split_2_faces"
    assert layout_mod.canonical_layout_id("split_gaming") == "split_gaming"
    # Canonical IDs pass through
    assert layout_mod.canonical_layout_id("smart_face_crop") == "smart_face_crop"
    assert layout_mod.canonical_layout_id("floating_facecam") == "floating_facecam"
    # Unknown ID defaults to center_crop
    assert layout_mod.canonical_layout_id("unknown_layout_xyz") == "center_crop"


def test_filtergraph_builder_for_all_12_layouts():
    test_analysis = {
        "primary_face_cx": 0.42,
        "left_face_cx": 0.28,
        "right_face_cx": 0.72,
        "face_detected_ratio": 0.8,
        "multi_face_ratio": 0.4,
        "is_streamer_webcam": False,
        "recommended_layout": "split_2_faces",
    }

    core_layouts = [
        "center_crop",
        "blur_fill",
        "fit_pad",
        "smart_face_crop",
        "active_speaker_crop",
        "split_gaming",
        "split_2_faces",
        "floating_facecam",
        "screen_facecam",
        "framed_headline",
        "dynamic_pan_zoom",
        "auto_hybrid",
    ]

    for lid in core_layouts:
        fg = layout_mod.build_layout_filtergraph(
            layout_id=lid,
            video_w=1920,
            video_h=1080,
            duration=15.0,
            params={"crop_x": 0.5, "headline": "BREAKING NEWS"},
            analysis=test_analysis,
        )
        assert isinstance(fg, str)
        assert len(fg) > 10
        # All filtergraphs must target 1080 and/or 1920
        assert "1080" in fg or "1920" in fg


def test_analysis_finds_webcam_in_the_open_cut_and_refreshes_old_cache(tmp_path, monkeypatch):
    import numpy as np

    sampled = []

    class Capture:
        def isOpened(self):
            return True

        def get(self, prop):
            return {
                layout_mod.cv2.CAP_PROP_FRAME_COUNT: 100,
                layout_mod.cv2.CAP_PROP_FPS: 10,
                layout_mod.cv2.CAP_PROP_FRAME_WIDTH: 160,
                layout_mod.cv2.CAP_PROP_FRAME_HEIGHT: 90,
            }[prop]

        def set(self, prop, value):
            sampled.append(value)

        def read(self):
            return True, np.zeros((90, 160, 3), dtype=np.uint8)

        def release(self):
            pass

    class Detector:
        def detectMultiScale(self, *_args, **_kwargs):
            return [(130, 60, 20, 20)] if sampled[-1] >= 60 else []

    monkeypatch.setattr(layout_mod.cv2, "VideoCapture", lambda _: Capture())
    monkeypatch.setattr(layout_mod.cv2, "CascadeClassifier", lambda _: Detector())
    source = tmp_path / "source.mp4"
    source.touch()
    cache = tmp_path / "analysis.json"
    cache.write_text('{"face_detected_ratio": 0.0}')

    found = layout_mod.analyze_video(str(source), str(cache), max_samples=4, time_range=(6, 9))
    assert min(sampled) == 60 and max(sampled) == 89
    assert found["recommended_layout"] == "split_gaming"
    assert found["webcam_region"]["cx"] == pytest.approx(0.875)
    assert found["webcam_region"]["cy"] == pytest.approx(0.778, abs=0.001)
    assert layout_mod.analyze_video(str(source), str(cache), max_samples=4, time_range=(6, 9)) == found
    assert len(sampled) == 4

    earlier = layout_mod.analyze_video(str(source), str(cache), max_samples=4, time_range=(0, 3))
    assert earlier["webcam_region"] is None


@pytest.mark.parametrize(("layout", "x", "y"), [
    ("split_gaming", 540, 288),
    ("floating_facecam", 856, 1476),
    ("screen_facecam", 540, 1584),
])
def test_facecam_layouts_render_the_detected_region(layout, x, y):
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required to verify the rendered crop")
    filters = layout_mod.build_layout_filtergraph(
        layout, video_w=640, video_h=360,
        analysis={"webcam_region": {"cx": 0.875, "cy": 0.8, "w": 0.08, "h": 0.12}},
    )
    result = subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "color=red:s=640x360:d=1,drawbox=x=450:y=230:w=190:h=130:color=blue:t=fill",
        "-vf", f"{filters},crop=2:2:{x}:{y}",
        "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ], capture_output=True, check=True, timeout=10)
    r, g, b = result.stdout[:3]
    assert b > 200 and r < 40 and g < 40


def test_filtergraph_chains_subtitle_filter():
    sub_filter = "subtitles=/path/to/test.ass:fontsdir=/path/to/fonts"
    fg = layout_mod.build_layout_filtergraph(
        layout_id="center_crop",
        video_w=1920,
        video_h=1080,
        subtitle_filter=sub_filter,
    )
    assert fg.endswith(sub_filter)
    assert "crop=" in fg


def test_auto_hybrid_resolution():
    timeline = [
        {"start": 0.0, "end": 5.0},
        {"start": 5.0, "end": 12.0},
    ]
    analysis = {
        "face_detected_ratio": 0.75,
        "multi_face_ratio": 0.6,
        "primary_face_cx": 0.45,
        "left_face_cx": 0.25,
        "right_face_cx": 0.75,
        "is_streamer_webcam": False,
    }
    hybrid = layout_mod.resolve_auto_hybrid(
        video_path="dummy.mp4",
        timeline=timeline,
        analysis=analysis,
    )
    assert len(hybrid) == 2
    assert hybrid[0]["layout_id"] == "split_2_faces"
    assert "2 speakers detected" in hybrid[0]["reason"]


def test_api_layouts_endpoint():
    from fastapi.testclient import TestClient
    import main as api

    with TestClient(api.app) as client:
        res = client.get("/api/layouts")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 39
        assert len(data["layouts"]) == 39
        assert "focus" in data["categories"]
        assert "split" in data["categories"]

        # Check that implemented layouts are accurately surfaced
        implemented = [l for l in data["layouts"] if l["implemented"]]
        assert len(implemented) == 12


def test_api_project_layout_endpoints():
    from fastapi.testclient import TestClient
    import main as api

    with TestClient(api.app) as client:
        # Create a project
        create_res = client.post("/api/projects", data={"name": "Layout Test Project"})
        assert create_res.status_code == 200
        pid = create_res.json()["id"]

        # Set layout
        set_res = client.post(
            f"/api/projects/{pid}/layout",
            json={"layout_id": "floating_facecam", "layout_params": {"pip_pos": "top_right"}},
        )
        assert set_res.status_code == 200
        assert set_res.json()["layout_id"] == "floating_facecam"

        # Check get project returns the updated layout
        get_res = client.get(f"/api/projects/{pid}")
        assert get_res.status_code == 200
        pdata = get_res.json()
        assert pdata["layout_id"] == "floating_facecam"
        assert pdata["layout_params"]["pip_pos"] == "top_right"

        # Check layout-analysis endpoint works gracefully
        analysis_res = client.get(f"/api/projects/{pid}/layout-analysis")
        assert analysis_res.status_code == 200
        assert "analysis" in analysis_res.json()


@pytest.mark.parametrize("source,layout,expected", [
    ((1920, 1080), "smart_face_crop", "1080x1920"),
    ((3840, 2160), "split_2_faces", "2160x3840"),
])
def test_export_matches_source_quality(tmp_path, monkeypatch, source, layout, expected):
    from fastapi.testclient import TestClient
    import main as api
    import subprocess
    import sqlite3

    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "quality.db"))
    monkeypatch.setattr(api, "EXPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(api, "STORAGE_DIR", str(tmp_path))
    api.init_db()

    width, height = source
    test_video = str(tmp_path / "quality.mp4")
    gen_cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration=1:size={width}x{height}:rate=2",
        "-f", "lavfi", "-i", "sine=duration=1:frequency=440",
        "-c:v", "libx264", "-c:a", "aac",
        "-pix_fmt", "yuv420p", test_video
    ]
    subprocess.run(gen_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    with TestClient(api.app) as client:
        create_res = client.post("/api/projects", data={"name": "Export Test"})
        pid = create_res.json()["id"]

        # Update project with the test video in sqlite
        conn = sqlite3.connect(api.DB_PATH)
        c = conn.cursor()
        c.execute(
            "UPDATE projects SET video_path = ?, video_filename = 'test.mp4', duration = 1.0, width = ?, height = ?, "
            "timeline_json = '[{\"id\":\"c1\",\"start\":0.0,\"end\":1.0}]' WHERE id = ?",
            (test_video, width, height, pid)
        )
        conn.commit()
        conn.close()

        # Export with smart_face_crop
        exp_res = client.post(
            f"/api/projects/{pid}/export",
            json={
                "layout_id": layout,
                "fps": 2,
                "layout_params": {"manual_offset_x": 0.05},
                "caption_text": "AI SMART LAYOUT",
            }
        )
        assert exp_res.status_code == 200, exp_res.text
        out_path = exp_res.json()["exported_path"]
        assert os.path.isfile(out_path)
        assert os.path.getsize(out_path) > 1000

        # Verify exact portrait resolution with ffprobe.
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            out_path
        ]
        res = subprocess.run(probe_cmd, stdout=subprocess.PIPE, text=True)
        assert res.stdout.strip() == expected

        timeline = [
            {"id": "c1", "start": 0.0, "end": 0.4},
            {"id": "c2", "start": 0.4, "end": 1.0},
        ]
        assert client.put(f"/api/projects/{pid}", json={"timeline": timeline}).status_code == 200
        assert client.post(f"/api/projects/{pid}/export", json={"clip_id": "missing"}).status_code == 404
        selected = client.post(f"/api/projects/{pid}/export", json={"clip_id": "c2", "layout_id": "fit_pad"})
        assert selected.status_code == 200
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", selected.json()["exported_path"]],
            stdout=subprocess.PIPE, text=True, check=True,
        )
        assert abs(float(probe.stdout) - 0.6) < 0.05
        assert [segment["id"] for segment in client.get(f"/api/projects/{pid}").json()["timeline"]] == ["c1", "c2"]





def test_combined_export_burns_each_cut_at_its_own_caption_times(tmp_path, monkeypatch, caplog):
    import logging
    import subprocess
    from fastapi.testclient import TestClient
    import main as api

    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "clips.db"))
    monkeypatch.setattr(api, "UPLOADS_DIR", str(tmp_path))
    monkeypatch.setattr(api, "EXPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(api, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(api, "_available_h264_encoders", lambda: set())
    api.init_db()
    source = tmp_path / "black.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=30:d=4",
        "-c:v", "libx264", str(source),
    ], check=True)
    with TestClient(api.app) as client, source.open("rb") as media:
        project = client.post("/api/projects", files={"file": ("black.mp4", media, "video/mp4")}).json()["id"]
        endpoint = f"/api/projects/{project}"
        assert client.put(endpoint, json={
            "timeline": [{"id": "first", "start": 0, "end": 1}, {"id": "second", "start": 2, "end": 3}],
            "words": [
                {"token": "First", "source_start_us": 100_000, "source_end_us": 500_000},
                {"token": "Second", "source_start_us": 2_600_000, "source_end_us": 2_900_000},
            ],
        }).status_code == 200
        with caplog.at_level(logging.INFO, logger=api.logger.name):
            response = client.post(f"{endpoint}/export", json={"layout_id": "fit_pad"})
        assert response.status_code == 200, response.json()
        path = response.json()["exported_path"]

    commands = [record.message for record in caplog.records if record.message.startswith("Export FFmpeg command")]
    assert len(commands) == 1 and "split=2" in commands[0] and "libx264" in commands[0]

    def bright_pixels(at):
        frame = subprocess.check_output([
            "ffmpeg", "-v", "error", "-ss", str(at), "-i", path,
            "-vf", "format=gray", "-frames:v", "1", "-f", "rawvideo", "-",
        ])
        return sum(value > 180 for value in frame)

    assert [bright_pixels(at) > 100 for at in (0.3, 0.8, 1.3, 1.8)] == [True, False, False, True]


def test_export_retries_cpu_encoder_with_fast_seek(monkeypatch, tmp_path):
    import main as api

    monkeypatch.setattr(api, "_available_h264_encoders", lambda: {"h264_nvenc", "h264_vaapi", "h264_qsv"})
    monkeypatch.setattr(api.glob, "glob", lambda _: ["/dev/dri/renderD128"])
    commands = []

    def encode(cmd):
        commands.append(cmd)
        return "Hardware initialization failed" if "libx264" not in cmd else None

    monkeypatch.setattr(api, "_run_encode", encode)
    api._render_cuts("source.mp4", [(1.25, 2.5, "scale=1080:1920", str(tmp_path / "cut.mp4"))], 30)
    assert [cmd[cmd.index("-c:v") + 1] for cmd in commands] == [
        "h264_nvenc", "h264_vaapi", "h264_qsv", "libx264",
    ]
    assert "format=nv12,hwupload" in commands[1][commands[1].index("-vf") + 1]
    assert "qsv=qs@va" in commands[2]
    for cmd in commands:
        assert cmd[cmd.index("-ss") + 1] == "1.25"
        assert cmd.index("-ss") < cmd.index("-i")
        assert cmd[cmd.index("-b:a") + 1] == "192k"
