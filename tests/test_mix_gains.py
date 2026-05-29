"""Tests for the adaptive mix-gain logic in AudioRecorder.

The big real-world bug James hit: mic captured at 15% peak, system audio
captured at 1% peak (the Scarlett's sink monitor emits low-level signal
by default). Fixed 2.0× gain on each leg meant his voice dominated the
mix entirely. The fix: measure each leg's peak before mixing and apply
per-leg gain so both land near 50%.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest

from meeting_notes.recorder import AudioRecorder


def _write_wav(path: Path, samples: list[int], rate: int = 48000, channels: int = 1):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def _sine(seconds: float, amp: int, freq: float = 440.0, rate: int = 48000) -> list[int]:
    n = int(seconds * rate)
    return [int(amp * math.sin(2 * math.pi * freq * t / rate)) for t in range(n)]


def _silence(seconds: float, rate: int = 48000) -> list[int]:
    return [0] * int(seconds * rate)


@pytest.fixture
def recorder(tmp_path):
    return AudioRecorder(output_dir=str(tmp_path))


def test_measure_wav_peak_silent(tmp_path, recorder):
    path = tmp_path / "silent.wav"
    _write_wav(path, _silence(1.5))
    assert recorder._measure_wav_peak(path) == 0


def test_measure_wav_peak_matches_amplitude(tmp_path, recorder):
    path = tmp_path / "tone.wav"
    _write_wav(path, _sine(1.5, amp=10000))
    peak = recorder._measure_wav_peak(path)
    assert 9500 <= peak <= 10000, f"expected ~10000, got {peak}"


def test_measure_wav_peak_missing_file(tmp_path, recorder):
    assert recorder._measure_wav_peak(tmp_path / "missing.wav") == 0


def test_mix_gains_balance_quiet_system_against_loud_mic(tmp_path, recorder):
    """The exact scenario James hit: mic peak ~15%, system peak ~1%."""
    mic = tmp_path / "mic.wav"
    sys = tmp_path / "sys.wav"
    _write_wav(mic, _sine(1.5, amp=5000))  # ~15% peak
    _write_wav(sys, _sine(1.5, amp=330))   # ~1% peak

    mic_gain, sys_gain = recorder._compute_mix_gains([mic, sys])

    # Mic at 15% → wants ~3.3× to hit 50%
    assert 2.5 <= mic_gain <= 4.0, f"mic_gain={mic_gain}"
    # System at 1% → wants ~50× but clamped to 12×
    assert sys_gain == pytest.approx(12.0, abs=0.1), f"sys_gain={sys_gain}"


def test_mix_gains_never_attenuate(tmp_path, recorder):
    """A loud leg should never be turned DOWN, only the quiet leg boosted."""
    mic = tmp_path / "mic.wav"
    sys = tmp_path / "sys.wav"
    _write_wav(mic, _sine(1.5, amp=30000))  # ~91% peak — already loud
    _write_wav(sys, _sine(1.5, amp=10000))

    mic_gain, sys_gain = recorder._compute_mix_gains([mic, sys])
    assert mic_gain >= 1.0, "should never attenuate"
    assert sys_gain >= 1.0


def test_mix_gains_silent_leg_clamped(tmp_path, recorder):
    """Completely silent leg shouldn't try to apply infinite gain."""
    mic = tmp_path / "mic.wav"
    sys = tmp_path / "sys.wav"
    _write_wav(mic, _sine(1.5, amp=10000))
    _write_wav(sys, _silence(1.5))

    mic_gain, sys_gain = recorder._compute_mix_gains([mic, sys])
    # System peak = 0, but we floor at peak=1 → gain hits the 12x clamp
    assert sys_gain <= 12.0
    assert sys_gain >= 1.0
    assert mic_gain >= 1.0


def test_mix_gains_balanced_legs_get_modest_boost(tmp_path, recorder):
    """When both legs are already balanced, both get the same gain."""
    mic = tmp_path / "mic.wav"
    sys = tmp_path / "sys.wav"
    _write_wav(mic, _sine(1.5, amp=8000))
    _write_wav(sys, _sine(1.5, amp=8000))

    mic_gain, sys_gain = recorder._compute_mix_gains([mic, sys])
    assert mic_gain == pytest.approx(sys_gain, abs=0.05)
    assert 1.5 <= mic_gain <= 2.5  # ~2× to hit 50%
