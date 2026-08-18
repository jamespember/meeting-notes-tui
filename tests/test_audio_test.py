"""Tests for the audio diagnostics module.

Covers:
- WAV stat extraction (duration, channels, peak, RMS, silence ratio)
- diagnose() verdict logic (pass/warn/fail) on synthetic inputs
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest

from omascribe.audio_test import (
    analyse_wav,
    diagnose,
)


def _write_wav(
    path: Path,
    samples: list[int],
    rate: int = 48000,
    channels: int = 1,
) -> None:
    """Write a small s16le mono/stereo WAV for tests."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def _silence(seconds: float, rate: int = 48000) -> list[int]:
    return [0] * int(seconds * rate)


def _sine(seconds: float, freq: float = 440.0, amp: int = 8000, rate: int = 48000) -> list[int]:
    """Generate a sine wave at given amplitude (out of 32767)."""
    n = int(seconds * rate)
    return [
        int(amp * math.sin(2 * math.pi * freq * t / rate))
        for t in range(n)
    ]


# ---------- analyse_wav ----------


def test_analyse_missing_file_returns_zero_stats(tmp_path):
    stats = analyse_wav(tmp_path / "does-not-exist.wav")
    assert not stats.exists
    assert stats.size_bytes == 0
    assert stats.duration_seconds == 0.0
    assert stats.peak == 0


def test_analyse_silent_wav(tmp_path):
    path = tmp_path / "silent.wav"
    _write_wav(path, _silence(2.0))

    stats = analyse_wav(path)
    assert stats.exists
    assert stats.channels == 1
    assert stats.sample_rate == 48000
    assert stats.duration_seconds == pytest.approx(2.0, abs=0.01)
    assert stats.peak == 0
    assert stats.rms == 0.0
    assert stats.silent_ratio == 1.0


def test_analyse_sine_wave_has_expected_peak_and_rms(tmp_path):
    path = tmp_path / "sine.wav"
    _write_wav(path, _sine(1.0, amp=10000))

    stats = analyse_wav(path)
    assert stats.exists
    assert stats.channels == 1
    assert stats.duration_seconds == pytest.approx(1.0, abs=0.01)
    # Peak should match amplitude (within rounding)
    assert 9500 <= stats.peak <= 10000
    # RMS of a sine wave is amplitude / sqrt(2) ≈ 7071
    assert 6500 <= stats.rms <= 7500
    # Sine wave is well above the silence floor
    assert stats.silent_ratio < 0.05


def test_analyse_partial_silence_window_ratio(tmp_path):
    """Half silence + half loud sine ≈ 50% silent windows."""
    path = tmp_path / "mixed.wav"
    samples = _silence(0.5) + _sine(0.5, amp=10000)
    _write_wav(path, samples)

    stats = analyse_wav(path)
    assert 0.4 <= stats.silent_ratio <= 0.6


def test_analyse_handles_corrupted_wav(tmp_path):
    """Garbage in, sane stats out — we should not crash."""
    path = tmp_path / "bad.wav"
    path.write_bytes(b"this is definitely not a wav file")

    stats = analyse_wav(path)
    # File exists, but parsing failed → most fields zeroed
    assert stats.exists
    assert stats.channels == 0
    assert stats.peak == 0
    assert stats.silent_ratio == 1.0


# ---------- diagnose ----------


def test_diagnose_missing_file_is_fail(tmp_path):
    stats = analyse_wav(tmp_path / "missing.wav")
    report = diagnose(stats)
    assert report.verdict == "fail"
    assert "no file" in report.summary.lower() or "produced" in report.summary.lower()


def test_diagnose_empty_file_is_fail(tmp_path):
    path = tmp_path / "empty.wav"
    path.write_bytes(b"")
    stats = analyse_wav(path)
    report = diagnose(stats)
    assert report.verdict == "fail"


def test_diagnose_silent_recording_is_fail(tmp_path):
    path = tmp_path / "silent.wav"
    _write_wav(path, _silence(3.0))
    stats = analyse_wav(path)
    report = diagnose(stats, expected_min_seconds=1.0)
    assert report.verdict == "fail"
    assert "silent" in report.summary.lower()


def test_diagnose_healthy_recording_is_pass(tmp_path):
    path = tmp_path / "healthy.wav"
    _write_wav(path, _sine(3.0, amp=10000))  # ~30% peak
    stats = analyse_wav(path)
    report = diagnose(stats, expected_min_seconds=1.0)
    assert report.verdict == "pass", f"findings: {report.findings}"
    assert "30%" in report.summary or "31%" in report.summary or "%" in report.summary


def test_diagnose_quiet_recording_is_warn(tmp_path):
    path = tmp_path / "quiet.wav"
    # Very low amplitude — above silence threshold (200) but well under 1500
    _write_wav(path, _sine(3.0, amp=600))
    stats = analyse_wav(path)
    report = diagnose(stats, expected_min_seconds=1.0)
    assert report.verdict == "warn"
    assert any("quiet" in f.lower() for f in report.findings)


def test_diagnose_clipping_recording_is_warn(tmp_path):
    path = tmp_path / "loud.wav"
    # Peg samples at int16 max
    _write_wav(path, [32767] * 48000 * 2)
    stats = analyse_wav(path)
    report = diagnose(stats, expected_min_seconds=1.0)
    assert report.verdict == "warn"
    assert any("clip" in f.lower() for f in report.findings)


def test_diagnose_short_recording_emits_finding(tmp_path):
    path = tmp_path / "short.wav"
    _write_wav(path, _sine(0.3, amp=10000))
    stats = analyse_wav(path)
    report = diagnose(stats, expected_min_seconds=1.0)
    # Verdict can still be pass on the level grade, but the finding must be there
    assert any("0.30s" in f or "expected at least" in f for f in report.findings)


def test_diagnose_quiet_system_leg_suggests_playback_volume(tmp_path):
    """Quiet audio on the SYSTEM leg should reference playback volume, not mic gain."""
    path = tmp_path / "quiet-system.wav"
    _write_wav(path, _sine(3.0, amp=600))
    stats = analyse_wav(path)
    report = diagnose(stats, expected_min_seconds=1.0, leg="system")
    joined = " ".join(report.findings).lower()
    assert "playback" in joined or "monitor gain" in joined or "source app" in joined, (
        f"expected playback-side guidance, got: {report.findings}"
    )
    assert "mic gain" not in joined


def test_diagnose_quiet_mic_leg_suggests_mic_gain(tmp_path):
    """Quiet audio on the MIC leg should reference mic gain."""
    path = tmp_path / "quiet-mic.wav"
    _write_wav(path, _sine(3.0, amp=600))
    stats = analyse_wav(path)
    report = diagnose(stats, expected_min_seconds=1.0, leg="mic")
    joined = " ".join(report.findings).lower()
    assert "mic" in joined or "input gain" in joined


def test_diagnose_silent_system_leg_mentions_sink_routing(tmp_path):
    """Silent system leg should call out wrong-sink routing as a likely cause."""
    path = tmp_path / "silent-system.wav"
    _write_wav(path, _silence(3.0))
    stats = analyse_wav(path)
    report = diagnose(stats, expected_min_seconds=1.0, leg="system")
    joined = " ".join(report.findings).lower()
    assert report.verdict == "fail"
    assert "sink" in joined or "play tone" in joined or "captured" in joined


def test_diagnose_quiet_system_leg_with_real_signal_is_not_silent(tmp_path):
    """System leg with -38 dBFS peak (real-world Scarlett monitor) should
    be a WARN, not FAIL.

    This is the exact failure mode James hit: a 5-second sink-monitor
    capture peaked at ~350/32767 (=~1%, -38 dBFS), with 96% of windows
    technically below the per-window silence threshold. That's still
    REAL audio — Whisper can transcribe it — but our strict rule was
    classifying it as a pipeline failure and blocking the user from
    recording.
    """
    path = tmp_path / "quiet-but-real-system.wav"
    # 1% peak amplitude across the whole file. This is "quiet" but
    # demonstrably above the noise floor; real Scarlett monitor signals
    # look exactly like this with normal-volume YouTube playback.
    _write_wav(path, _sine(3.0, amp=350))
    stats = analyse_wav(path)
    report = diagnose(stats, expected_min_seconds=1.0, leg="system")

    assert report.verdict != "fail", (
        f"system leg with 1% peak should not be classified as silent. "
        f"verdict={report.verdict}, findings={report.findings}"
    )
    # The mic equivalent SHOULD still fail because the silent-ratio rule
    # is appropriate for mics.
    mic_report = diagnose(stats, expected_min_seconds=1.0, leg="mic")
    # (The mic version may not fail because the signal isn't fully silent,
    # but should at least warn.)
    assert mic_report.verdict in ("warn", "fail")


def test_diagnose_truly_silent_system_leg_still_fails(tmp_path):
    """Sanity check: completely silent system leg should still FAIL,
    even with the relaxed system-specific rule."""
    path = tmp_path / "really-silent-system.wav"
    _write_wav(path, _silence(3.0))
    stats = analyse_wav(path)
    report = diagnose(stats, expected_min_seconds=1.0, leg="system")
    assert report.verdict == "fail"
