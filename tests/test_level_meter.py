"""Tests for the audio level meter peak computation.

The meter itself spawns a subprocess; we don't test that here. We just
verify the pure-function part: peak amplitude over an s16le buffer.
"""

from __future__ import annotations

import struct

from meeting_notes.level_meter import _peak_s16le


def _pack(samples):
    return struct.pack(f"<{len(samples)}h", *samples)


def test_peak_of_silence_is_zero():
    assert _peak_s16le(_pack([0, 0, 0, 0])) == 0


def test_peak_picks_largest_absolute_value():
    assert _peak_s16le(_pack([100, -3000, 50, 2999])) == 3000


def test_peak_handles_int16_min():
    """-32768 has no positive equivalent in int16; clamp to 32767."""
    assert _peak_s16le(_pack([-32768])) == 32767


def test_peak_handles_int16_max():
    assert _peak_s16le(_pack([32767])) == 32767


def test_peak_empty_buffer():
    assert _peak_s16le(b"") == 0


def test_peak_handles_odd_byte_buffer():
    """A trailing single byte (incomplete sample) shouldn't crash."""
    buf = _pack([5000]) + b"\x42"
    assert _peak_s16le(buf) == 5000
