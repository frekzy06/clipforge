"""Audio extraction utilities for ClipForge.

Extracts 16kHz mono 16-bit PCM WAV from source video using FFmpeg,
which is the exact canonical format required by Whisper / speech recognition.
"""
from __future__ import annotations

import os
import subprocess


def extract_audio_wav(video_path: str, output_wav_path: str) -> str:
    """Extracts 16kHz mono WAV from video.
    Returns the path to the output WAV file.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Source video not found: {video_path}")

    os.makedirs(os.path.dirname(os.path.abspath(output_wav_path)), exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        output_wav_path
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extraction failed: {res.stderr[-300:]}")

    return output_wav_path
