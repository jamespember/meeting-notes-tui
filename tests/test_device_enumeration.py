"""Tests for the pactl-backed device enumeration in recorder.py."""

from __future__ import annotations

import pytest

from meeting_notes import recorder as rec_module


SAMPLE_PACTL_SOURCES = """
Source #45
\tName: alsa_input.usb-Focusrite-Scarlett.analog-stereo
\tDescription: Scarlett Solo Analog Stereo
Source #46
\tName: alsa_output.pci-0000_00_1f.3.analog-stereo.monitor
\tDescription: Built-in Audio Analog Stereo Monitor
Source #47
\tName: alsa_input.pci-0000_00_1f.3.analog-stereo
\tDescription: Built-in Mic
"""

SAMPLE_PACTL_SINKS = """
Sink #100
\tName: alsa_output.pci-0000_00_1f.3.analog-stereo
\tDescription: Built-in Audio
Sink #101
\tName: alsa_output.usb-Focusrite-Scarlett.analog-stereo
\tDescription: Scarlett Solo Output
"""


@pytest.fixture(autouse=True)
def _stub_pactl(monkeypatch):
    """Replace the _run_pactl shell-out with deterministic fixtures."""
    def fake_run(args, timeout=2.0):
        if args == ["list", "sources"]:
            return SAMPLE_PACTL_SOURCES
        if args == ["list", "sinks"]:
            return SAMPLE_PACTL_SINKS
        if args == ["get-default-source"]:
            return "alsa_input.pci-0000_00_1f.3.analog-stereo\n"
        if args == ["get-default-sink"]:
            return "alsa_output.pci-0000_00_1f.3.analog-stereo\n"
        return None

    monkeypatch.setattr(rec_module, "_run_pactl", fake_run)


def test_list_input_devices_excludes_monitors_by_default():
    devices = rec_module.list_input_devices()
    names = [d.name for d in devices]
    assert "alsa_input.usb-Focusrite-Scarlett.analog-stereo" in names
    assert "alsa_input.pci-0000_00_1f.3.analog-stereo" in names
    assert all(not n.endswith(".monitor") for n in names)


def test_list_input_devices_can_include_monitors():
    devices = rec_module.list_input_devices(include_monitors=True)
    names = [d.name for d in devices]
    assert any(n.endswith(".monitor") for n in names)


def test_list_input_devices_marks_default():
    devices = rec_module.list_input_devices()
    defaults = [d for d in devices if d.is_default]
    assert len(defaults) == 1
    assert defaults[0].name == "alsa_input.pci-0000_00_1f.3.analog-stereo"


def test_list_output_devices_returns_sinks():
    devices = rec_module.list_output_devices()
    names = [d.name for d in devices]
    assert "alsa_output.pci-0000_00_1f.3.analog-stereo" in names
    assert "alsa_output.usb-Focusrite-Scarlett.analog-stereo" in names


def test_list_output_devices_marks_default():
    devices = rec_module.list_output_devices()
    defaults = [d for d in devices if d.is_default]
    assert len(defaults) == 1
    assert defaults[0].name == "alsa_output.pci-0000_00_1f.3.analog-stereo"


def test_descriptions_are_used_for_display():
    devices = rec_module.list_input_devices()
    scarlett = next(d for d in devices if "Scarlett" in d.name)
    assert "Scarlett" in scarlett.display
