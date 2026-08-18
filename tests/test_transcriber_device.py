"""Tests for WhisperTranscriber device handling and CUDA fallback.

We don't import the real ``whisper`` (and hence torch) — we install a fake
``whisper`` module into ``sys.modules`` before WhisperTranscriber tries to
load it. This keeps CI fast and matches the existing pattern of avoiding
torch in the lightweight test suite.
"""

from __future__ import annotations

import sys
import types

import pytest


@pytest.fixture
def fake_whisper(monkeypatch):
    """Install a controllable fake `whisper` module for the test."""
    fake_mod = types.ModuleType("whisper")

    class FakeModel:
        def __init__(self, name, device):
            self.name = name
            self.device = device or "cpu"
            self.transcribe_calls = []

        def transcribe(self, path, **kwargs):
            self.transcribe_calls.append((path, kwargs))
            return {"segments": [], "text": "", "language": "en"}

    fake_mod._failures = []  # set by tests: list of (device, exception)
    fake_mod._loads = []     # appended (model_name, device) per call

    def load_model(model_name, device=None):
        fake_mod._loads.append((model_name, device))
        # Pop failures FIFO
        if fake_mod._failures:
            should_fail_for, exc = fake_mod._failures[0]
            if should_fail_for is None or should_fail_for == device:
                fake_mod._failures.pop(0)
                raise exc
        return FakeModel(model_name, device)

    fake_mod.load_model = load_model

    monkeypatch.setitem(sys.modules, "whisper", fake_mod)
    yield fake_mod


def _fresh_transcriber_module(monkeypatch):
    """Reload omascribe.transcriber so it picks up the fake whisper."""
    # We can just import; transcriber imports whisper lazily inside load_model.
    from omascribe import transcriber  # noqa: WPS433
    return transcriber


def test_default_device_is_cpu(fake_whisper, monkeypatch):
    transcriber_mod = _fresh_transcriber_module(monkeypatch)
    t = transcriber_mod.WhisperTranscriber("base")
    t.load_model()

    assert fake_whisper._loads == [("base", "cpu")]
    assert t.active_device == "cpu"


def test_explicit_cuda_passes_through(fake_whisper, monkeypatch):
    transcriber_mod = _fresh_transcriber_module(monkeypatch)
    t = transcriber_mod.WhisperTranscriber("base", device="cuda")
    t.load_model()

    assert fake_whisper._loads == [("base", "cuda")]
    assert t.active_device == "cuda"


def test_auto_passes_none(fake_whisper, monkeypatch):
    """`auto` lets whisper pick — we must not pass a device kwarg value."""
    transcriber_mod = _fresh_transcriber_module(monkeypatch)
    t = transcriber_mod.WhisperTranscriber("base", device="auto")
    t.load_model()

    assert fake_whisper._loads == [("base", None)]


def test_cuda_no_kernel_image_falls_back_to_cpu(fake_whisper, monkeypatch):
    """The exact error from the screenshot must trigger a CPU retry."""
    fake_whisper._failures.append((
        "cuda",
        RuntimeError(
            "CUDA error: no kernel image is available for execution on the device"
        ),
    ))

    transcriber_mod = _fresh_transcriber_module(monkeypatch)
    t = transcriber_mod.WhisperTranscriber("base", device="cuda")
    t.load_model()

    # First load attempt was cuda; after the failure we retried on cpu.
    assert fake_whisper._loads == [("base", "cuda"), ("base", "cpu")]
    assert t.active_device == "cpu"


def test_auto_falls_back_when_cuda_explodes(fake_whisper, monkeypatch):
    fake_whisper._failures.append((
        None,  # any device
        RuntimeError("CUDA error: no kernel image is available"),
    ))

    transcriber_mod = _fresh_transcriber_module(monkeypatch)
    t = transcriber_mod.WhisperTranscriber("base", device="auto")
    t.load_model()

    assert t.active_device == "cpu"


def test_non_cuda_error_on_cuda_does_not_silently_fall_back(fake_whisper, monkeypatch):
    """We don't want to mask unrelated errors as if they were CUDA issues."""
    fake_whisper._failures.append((
        "cuda",
        ValueError("unrelated boom"),
    ))

    transcriber_mod = _fresh_transcriber_module(monkeypatch)
    t = transcriber_mod.WhisperTranscriber("base", device="cuda")
    with pytest.raises(ValueError, match="unrelated boom"):
        t.load_model()


def test_cpu_path_uses_fp16_false(fake_whisper, monkeypatch, tmp_path):
    """fp16 only makes sense on CUDA. CPU runs must explicitly pass fp16=False."""
    transcriber_mod = _fresh_transcriber_module(monkeypatch)
    audio = tmp_path / "fake.wav"
    audio.write_bytes(b"\x00\x00")

    t = transcriber_mod.WhisperTranscriber("base", device="cpu")
    t.transcribe(str(audio))

    # Inspect the recorded transcribe call
    assert t.model.transcribe_calls, "transcribe should have been invoked"
    _, kwargs = t.model.transcribe_calls[0]
    assert kwargs.get("fp16") is False


def test_unknown_device_string_falls_back_to_cpu(fake_whisper, monkeypatch):
    transcriber_mod = _fresh_transcriber_module(monkeypatch)
    t = transcriber_mod.WhisperTranscriber("base", device="hocus-pocus")
    assert t.requested_device == "cpu"
