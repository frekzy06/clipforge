"""Transcription service for ClipForge.

Runs faster-whisper to produce word-level timestamps for a project's source
media. The results are stored on the project as `words_json` in the canonical
`CaptionWord` shape (microsecond timings), so the timeline map can remap them
onto the edited output timeline.

Model caching
-------------
The Whisper model is downloaded once from Hugging Face and then reused. The
cache lives in `services/api/storage/models/` so it is shared by every
project and never re-downloaded.

Device selection
----------------
CUDA is used when the runtime can actually load it (this needs cuBLAS, not
just a driver); otherwise the run falls back to CPU with int8 quantisation.
The probe is done once per process and the result is logged.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import tempfile
import threading
from functools import lru_cache
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import audio

logger = logging.getLogger("clipforge.transcribe")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STORAGE_DIR = os.path.join(REPO_ROOT, "services", "api", "storage")
MODELS_DIR = os.path.join(STORAGE_DIR, "models")

# Default model: a good accuracy/speed trade-off on CPU.
DEFAULT_MODEL = "base"
DEFAULT_COMPUTE_TYPE = "int8"
DEFAULT_CPU_THREADS = max(1, (os.cpu_count() or 4) // 2)

# Models the user may pick in the UI.
ALLOWED_MODELS = ("tiny", "base", "small", "medium", "large-v3")

# Phonetic mapping for Devanagari characters to Romanized Hinglish script
_DEVANAGARI_TO_LATIN = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri",
    "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "अं": "an", "अः": "ah",
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ng",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "ny",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "f", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h",
    "ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri",
    "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ं": "n", "ँ": "n", "्": "",
    "क़": "q", "ख़": "kh", "ग़": "gh", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f",
}


def transliterate_devanagari_to_hinglish(text: str) -> str:
    """Convert Devanagari characters to natural Romanized Hinglish text."""
    if not text:
        return ""
    has_devanagari = any("\u0900" <= ch <= "\u097f" for ch in text)
    if not has_devanagari:
        return text

    out = []
    i = 0
    n = len(text)
    while i < n:
        if i + 1 < n and text[i : i + 2] in _DEVANAGARI_TO_LATIN:
            out.append(_DEVANAGARI_TO_LATIN[text[i : i + 2]])
            i += 2
            continue
        ch = text[i]
        if ch in _DEVANAGARI_TO_LATIN:
            val = _DEVANAGARI_TO_LATIN[ch]
            # Consonants without a virama or trailing matra retain inherent vowel 'a'
            # except at word boundaries (Hindi schwa deletion)
            if "\u0915" <= ch <= "\u0939":
                next_ch = text[i + 1] if i + 1 < n else ""
                is_word_end = (not next_ch) or next_ch.isspace() or (not ("\u0900" <= next_ch <= "\u097f"))
                if not is_word_end and not ("\u093e" <= next_ch <= "\u094d" or next_ch in ("ं", "ँ")):
                    val += "a"
            out.append(val)
        else:
            out.append(ch)
        i += 1
    return "".join(out)


class TranscriptionError(RuntimeError):
    """Raised when a transcription run fails."""


class TranscriptionCancelled(Exception):
    """Raised when the caller asks for the run to stop."""


# --------------------------------------------------------------------------
# Device / model probing
# --------------------------------------------------------------------------

_device_cache: Optional[Tuple[str, str]] = None
_device_lock = threading.Lock()


_cuda_library_handles: list = []


def _load_cuda_library(package: str, name: str) -> None:
    # The system loader handles installed libraries and LD_LIBRARY_PATH.
    candidates = [name]
    for entry in sys.path:
        root = Path(entry) / "nvidia" / package
        for subdir in ("lib", "bin"):
            path = root / subdir / name
            if path.is_file():
                candidates.append(str(path.resolve()))
    for candidate in candidates:
        try:
            if os.name == "nt" and os.path.isabs(candidate):
                _cuda_library_handles.append(os.add_dll_directory(os.path.dirname(candidate)))
            handle = ctypes.CDLL(candidate, mode=ctypes.RTLD_GLOBAL)
        except OSError as exc:
            error = exc
        else:
            _cuda_library_handles.append(handle)
            return
    raise error


def _cuda_usable() -> bool:
    """Check the driver, runtime libraries and float16 device support."""
    if not (sys.platform.startswith("linux") or os.name == "nt"):
        return False
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() < 1:
            return False
        libraries = (
            ("cublas", "cublasLt64_12.dll", "libcublasLt.so.12"),
            ("cublas", "cublas64_12.dll", "libcublas.so.12"),
            ("cudnn", "cudnn64_9.dll", "libcudnn.so.9"),
            ("cudnn", "cudnn_ops64_9.dll", "libcudnn_ops.so.9"),
        )
        for package, windows, linux in libraries:
            _load_cuda_library(package, windows if os.name == "nt" else linux)
        return "float16" in ctranslate2.get_supported_compute_types("cuda")
    except Exception as exc:
        logger.info("CUDA unavailable: %s", exc)
        return False


def detect_device() -> Tuple[str, str]:
    """Return ``(device, compute_type)`` to use for transcription."""
    global _device_cache
    with _device_lock:
        if _device_cache is not None:
            return _device_cache
        device, compute = "cpu", DEFAULT_COMPUTE_TYPE
        if _cuda_usable():
            device, compute = "cuda", "float16"
            logger.info("transcription device: cuda/float16")
        else:
            logger.info(
                "transcription device: cpu/%s (CUDA unavailable; install "
                "cuBLAS/cuDNN to enable GPU acceleration)",
                compute,
            )
        _device_cache = (device, compute)
        return _device_cache


def model_cache_dir() -> str:
    """Directory where downloaded Whisper models are cached."""
    os.makedirs(MODELS_DIR, exist_ok=True)
    return MODELS_DIR


def _download_audio(video_path: str, target_dir: str) -> str:
    """Extract a 16 kHz mono WAV from the source media for Whisper."""
    wav_path = os.path.join(target_dir, "audio.wav")
    try:
        return audio.extract_audio_wav(video_path, wav_path)
    except (FileNotFoundError, RuntimeError) as exc:
        raise TranscriptionError(f"could not extract audio: {exc}") from exc


@lru_cache(maxsize=1)
def _load_model(model_size: str, device_override: str | None = None):
    """Load (and cache on disk) the Whisper model."""
    try:
        from faster_whisper import WhisperModel  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise TranscriptionError(
            "faster-whisper is not installed (pip install faster-whisper)"
        ) from exc

    device, compute = ("cpu", DEFAULT_COMPUTE_TYPE) if device_override == "cpu" else detect_device()
    cache = model_cache_dir()
    # faster-whisper downloads into the HF cache; point it at our storage so
    # the model is kept with the project and never re-downloaded.
    os.environ.setdefault("HF_HOME", cache)
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    logger.info("loading whisper model=%s device=%s compute=%s", model_size, device, compute)
    return WhisperModel(
        model_size,
        device=device,
        compute_type=compute,
        cpu_threads=DEFAULT_CPU_THREADS,
        download_root=cache,
    )


def transcribe_media(
    video_path: str,
    model_size: str = DEFAULT_MODEL,
    language: Optional[str] = None,
    beam_size: int = 5,
    vad_filter: bool = True,
    progress: Optional[Callable[[float, str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> dict:
    """Transcribe `video_path` and return words plus segment metadata.

    The returned dict has the shape::

        {
          "language": "en",
          "language_probability": 0.99,
          "duration": 12.5,
          "model": "base",
          "device": "cpu",
          "segments": [{"id", "start", "end", "text"}],
          "words": [CaptionWord dicts],
        }
    """
    if not os.path.exists(video_path):
        raise TranscriptionError(f"source media not found: {video_path}")

    if model_size not in ALLOWED_MODELS:
        raise TranscriptionError(
            f"unsupported model {model_size!r}; choose one of {', '.join(ALLOWED_MODELS)}"
        )

    def report(fraction: float, message: str) -> None:
        if progress:
            progress(fraction, message)

    device, _compute = detect_device()
    with tempfile.TemporaryDirectory(prefix="clipforge-asr-") as tmpdir:
        report(0.05, "extracting audio")
        wav_path = _download_audio(video_path, tmpdir)

        is_hinglish = (language or "").lower() == "hinglish"
        whisper_lang = "hi" if is_hinglish else (language if language in ("en", "hi") else None)
        transcribe_kwargs = {
            "language": whisper_lang,
            "beam_size": beam_size,
            "word_timestamps": True,
            "vad_filter": vad_filter,
        }
        if is_hinglish:
            transcribe_kwargs["initial_prompt"] = (
                "Hinglish: यार ये वीडियो देख, क्या मस्त है। Keep English words in English."
            )

        devices = ("cuda", "cpu") if device == "cuda" else ("cpu",)
        for attempt in devices:
            try:
                report(0.2, f"loading model '{model_size}' on {attempt}")
                model = _load_model(model_size) if attempt == device else _load_model(model_size, "cpu")
                report(0.35, "transcribing")
                segments_iter, info = model.transcribe(wav_path, **transcribe_kwargs)
                segments: List[dict] = []
                words: List[dict] = []
                total = max(getattr(info, "duration", 0.0) or 0.0, 0.001)
                word_index = 0

                for seg in segments_iter:
                    if should_cancel and should_cancel():
                        raise TranscriptionCancelled("transcription cancelled")

                    seg_text = (seg.text or "").strip()
                    if is_hinglish:
                        seg_text = transliterate_devanagari_to_hinglish(seg_text)

                    segments.append(
                        {
                            "id": getattr(seg, "id", len(segments)),
                            "start": float(seg.start),
                            "end": float(seg.end),
                            "text": seg_text,
                        }
                    )
                    for w in (getattr(seg, "words", None) or []):
                        token = (w.word or "").strip()
                        if not token:
                            continue
                        if is_hinglish:
                            token = transliterate_devanagari_to_hinglish(token)
                        start_us = int(round(float(w.start) * 1_000_000))
                        end_us = int(round(float(w.end) * 1_000_000))
                        # Guard against zero/negative durations from the model.
                        if end_us <= start_us:
                            end_us = start_us + 1
                        words.append(
                            {
                                "word_id": f"w-{word_index:06d}",
                                "token": token,
                                "source_start_us": start_us,
                                "source_end_us": end_us,
                                "output_start_us": None,
                                "output_end_us": None,
                                "speaker_id": None,
                                "confidence": (
                                    round(float(w.probability), 4)
                                    if getattr(w, "probability", None) is not None
                                    else None
                                ),
                                "caption_group_id": None,
                                "manual_override": False,
                            }
                        )
                        word_index += 1

                    if getattr(info, "duration", 0.0):
                        report(
                            0.35 + 0.6 * min(1.0, float(seg.end) / total),
                            f"transcribed {len(words)} words",
                        )
            except TranscriptionError:
                raise
            except (RuntimeError, OSError, ValueError) as exc:
                if attempt == "cpu":
                    raise TranscriptionError(f"transcription failed: {exc}") from exc
                logger.warning("CUDA transcription failed, retrying on CPU: %s", exc)
            else:
                device = attempt
                break

    report(0.98, "finalising")
    return {
        "language": getattr(info, "language", None),
        "language_probability": (
            round(float(info.language_probability), 4)
            if getattr(info, "language_probability", None) is not None
            else None
        ),
        "duration": float(getattr(info, "duration", 0.0) or 0.0),
        "model": model_size,
        "device": device,
        "segments": segments,
        "words": words,
    }
