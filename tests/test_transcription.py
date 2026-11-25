"""Tests for the ClipForge transcription service."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "services", "api"
))

import transcription  # noqa: E402
from transcription import TranscriptionError  # noqa: E402


def _stub_audio(monkeypatch):
    """Replace the ffmpeg audio extraction with a stub."""
    def _extract(video, target_dir):
        path = os.path.join(target_dir, "audio.wav")
        with open(path, "wb") as fh:
            fh.write(b"RIFF")
        return path
    monkeypatch.setattr(transcription, "_download_audio", _extract)


class _Word:
    def __init__(self, word, start, end, probability):
        self.word = word
        self.start = start
        self.end = end
        self.probability = probability


class _Segment:
    def __init__(self, idx, start, end, text, words):
        self.id = idx
        self.start = start
        self.end = end
        self.text = text
        self.words = words


class _Info:
    language = "en"
    language_probability = 0.99
    duration = 2.0


class _Model:
    """A stand-in Whisper model that returns fixed segments."""

    segments = [
        _Segment(
            0, 0.0, 2.0, " hello world",
            [_Word(" hello", 0.0, 0.5, 0.9), _Word(" world", 0.5, 1.2, 0.8)],
        )
    ]
    info_duration = 2.0

    def __init__(self, *a, **kw):
        pass

    def transcribe(self, wav_path, **kwargs):
        assert os.path.exists(wav_path), "audio should be extracted first"
        info = _Info()
        info.duration = self.info_duration
        return iter(self.segments), info


def test_allowed_models_are_whisper_sizes():
    assert "tiny" in transcription.ALLOWED_MODELS
    assert "base" in transcription.ALLOWED_MODELS
    assert transcription.DEFAULT_MODEL in transcription.ALLOWED_MODELS


def test_detect_device_returns_known_pair():
    device, compute = transcription.detect_device()
    assert device in ("cpu", "cuda")
    assert isinstance(compute, str) and compute


def test_cuda_probe_never_raises():
    # Must be a bool even on a machine without CUDA libraries.
    assert isinstance(transcription._cuda_usable(), bool)


def test_transcribe_media_rejects_missing_file():
    with pytest.raises(TranscriptionError):
        transcription.transcribe_media("/nonexistent/file.mp4")


def test_transcribe_media_rejects_unknown_model(tmp_path):
    sample = tmp_path / "s.mp4"
    sample.write_bytes(b"not really a video")
    with pytest.raises(TranscriptionError) as exc:
        transcription.transcribe_media(str(sample), model_size="not-a-model")
    assert "unsupported model" in str(exc.value).lower()


def test_model_cache_dir_is_created():
    path = transcription.model_cache_dir()
    assert os.path.isdir(path)


def test_transcribe_media_extracts_audio_and_returns_words(tmp_path, monkeypatch):
    """Full run with a stubbed model: words come back with us timings."""
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"fake")
    _stub_audio(monkeypatch)
    monkeypatch.setattr(transcription, "_load_model", lambda model_size: _Model())

    result = transcription.transcribe_media(str(media), model_size="tiny")

    assert result["language"] == "en"
    assert len(result["words"]) == 2
    first, second = result["words"]
    # Microsecond timings, exactly as REQUIREMENTS 6 specifies.
    assert first["source_start_us"] == 0
    assert first["source_end_us"] == 500_000
    assert second["source_start_us"] == 500_000
    assert second["source_end_us"] == 1_200_000
    # Confidence is carried through.
    assert first["confidence"] == 0.9
    # Words start un-mapped; the timeline map assigns output positions.
    assert first["output_start_us"] is None
    assert first["manual_override"] is False
    assert first["word_id"] == "w-000000"
    assert result["segments"][0]["text"] == "hello world"


def test_transcribe_media_zero_length_word_is_fixed(tmp_path, monkeypatch):
    """A zero-duration word must be given a 1us duration, not dropped."""
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"fake")
    _stub_audio(monkeypatch)

    class _ZeroModel(_Model):
        segments = [
            _Segment(0, 1.0, 1.0, " x", [_Word("x", 1.0, 1.0, 0.5)])
        ]
        info_duration = 1.0

    monkeypatch.setattr(transcription, "_load_model", lambda model_size: _ZeroModel())
    result = transcription.transcribe_media(str(media), model_size="tiny")
    word = result["words"][0]
    assert word["source_end_us"] > word["source_start_us"]


def test_transcribe_media_reports_progress(tmp_path, monkeypatch):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"fake")
    _stub_audio(monkeypatch)
    monkeypatch.setattr(transcription, "_load_model", lambda model_size: _Model())
    seen = []
    transcription.transcribe_media(
        str(media),
        model_size="tiny",
        progress=lambda frac, msg: seen.append((frac, msg)),
    )
    assert seen, "progress callback should be invoked"
    assert any(f >= 0.9 for f, _ in seen), "progress should reach the end"


def test_transcribe_media_skips_empty_tokens(tmp_path, monkeypatch):
    """Whitespace-only tokens must not become words."""
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"fake")
    _stub_audio(monkeypatch)

    class _BlankModel(_Model):
        segments = [
            _Segment(0, 0.0, 1.0, "  a", [_Word("   ", 0.0, 0.5, 0.5)])
        ]
        info_duration = 1.0

    monkeypatch.setattr(transcription, "_load_model", lambda model_size: _BlankModel())
    result = transcription.transcribe_media(str(media), model_size="tiny")
    assert result["words"] == []


def test_transliterate_devanagari_to_hinglish():
    # Empty or pure Latin remains unchanged
    assert transcription.transliterate_devanagari_to_hinglish("") == ""
    assert transcription.transliterate_devanagari_to_hinglish("hello world") == "hello world"

    # Hindi words
    assert transcription.transliterate_devanagari_to_hinglish("नमस्ते") == "namaste"
    assert transcription.transliterate_devanagari_to_hinglish("भारत") == "bhaarat"
    assert transcription.transliterate_devanagari_to_hinglish("ये वीडियो देखो") == "ye veediyo dekho"


def test_transcribe_media_hinglish_mode(tmp_path, monkeypatch):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"fake")
    _stub_audio(monkeypatch)

    class _HindiModel(_Model):
        segments = [
            _Segment(0, 0.0, 1.5, "नमस्ते दोस्त", [_Word("नमस्ते", 0.0, 0.8, 0.95), _Word("दोस्त", 0.8, 1.5, 0.9)])
        ]
        info_duration = 1.5

    captured_kwargs = {}
    class _SpyModel(_HindiModel):
        def transcribe(self, wav_path, **kwargs):
            captured_kwargs.update(kwargs)
            return super().transcribe(wav_path, **kwargs)

    monkeypatch.setattr(transcription, "_load_model", lambda model_size: _SpyModel())
    res = transcription.transcribe_media(str(media), model_size="tiny", language="hinglish")

    assert captured_kwargs["language"] == "hi"
    assert "initial_prompt" in captured_kwargs
    assert "Hinglish" in captured_kwargs["initial_prompt"]
    # Devanagari text should be transliterated into Roman script
    assert res["words"][0]["token"] == "namaste"
    assert res["words"][1]["token"] == "dost"
    assert "namaste dost" in res["segments"][0]["text"]



def test_cuda_inference_failure_retries_with_cpu_and_vad(tmp_path, monkeypatch):
    media = tmp_path / "clip.mp4"
    media.touch()
    _stub_audio(monkeypatch)
    monkeypatch.setattr(transcription, "detect_device", lambda: ("cuda", "float16"))
    attempts = []

    class FailingModel:
        def transcribe(self, wav_path, **kwargs):
            def segments():
                yield _Model.segments[0]
                raise RuntimeError("cuDNN initialization failed")

            return segments(), _Info()

    class CpuModel(_Model):
        def transcribe(self, wav_path, **kwargs):
            assert kwargs["vad_filter"] is True
            return super().transcribe(wav_path, **kwargs)

    def load_model(model_size, device=None):
        attempts.append(device)
        return CpuModel() if device == "cpu" else FailingModel()

    monkeypatch.setattr(transcription, "_load_model", load_model)
    result = transcription.transcribe_media(str(media))
    assert attempts == [None, "cpu"]
    assert result["device"] == "cpu"
    assert [word["token"] for word in result["words"]] == ["hello", "world"]
