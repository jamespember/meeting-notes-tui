"""Tests for assess_setup_health() — the mic/sink combination heuristic.

This catches "you can record fine but your setup choices will limit
quality" cases like webcam mics and HDMI sink monitors. The recorder
itself works fine in these configurations; the heuristic just nudges
the user toward a better setup before a real meeting.
"""

from __future__ import annotations

from meeting_notes.recorder import assess_setup_health


def test_webcam_mic_is_flagged():
    """James' setup: ViewSonic webcam → low-quality mic warning."""
    notes = assess_setup_health(
        mic_name="alsa_input.usb-2e7e_ViewSonic_HD_webcam_0165184900000000-03.analog-stereo",
        system_sink_name="alsa_output.usb-Focusrite_Scarlett_Solo_USB_Y76U6362965B44-00.HiFi__Line__sink",
    )
    assert len(notes) >= 1
    joined = " ".join(n.message.lower() for n in notes)
    assert "webcam" in joined
    # Should mention an alternative
    assert "headset" in joined or "interface mic" in joined or "dedicated mic" in joined


def test_logitech_c920_webcam_is_flagged():
    """Common Logitech webcam should also be flagged."""
    notes = assess_setup_health(
        mic_name="alsa_input.usb-046d_HD_Pro_Webcam_C920_xxx-02.analog-stereo",
        system_sink_name="alsa_output.pci-0000_00_1f.3.analog-stereo",
    )
    assert any("webcam" in n.message.lower() for n in notes)


def test_real_microphone_through_interface_passes():
    """A proper mic through a Scarlett or similar shouldn't get the webcam note."""
    notes = assess_setup_health(
        mic_name="alsa_input.usb-Focusrite_Scarlett_Solo_USB-00.analog-stereo",
        system_sink_name="alsa_output.usb-Focusrite_Scarlett_Solo_USB-00.HiFi__Line__sink",
    )
    # No webcam warning
    assert not any("webcam" in n.message.lower() for n in notes)


def test_hdmi_sink_is_flagged():
    """HDMI/DisplayPort sink monitors are unreliable across distros."""
    notes = assess_setup_health(
        mic_name="alsa_input.usb-headset",
        system_sink_name="alsa_output.pci-0000_01_00.1.hdmi-stereo",
    )
    joined = " ".join(n.message.lower() for n in notes)
    assert "hdmi" in joined or "displayport" in joined
    # HDMI gets a "warn" severity, not info
    assert any(n.severity == "warn" for n in notes if "hdmi" in n.message.lower())


def test_displayport_sink_is_flagged():
    notes = assess_setup_health(
        mic_name="alsa_input.usb-headset",
        system_sink_name="alsa_output.pci-0000_01_00.1.displayport-stereo",
    )
    assert any("hdmi" in n.message.lower() or "displayport" in n.message.lower()
               for n in notes)


def test_healthy_setup_returns_no_notes():
    """A proper headset mic + analog sink shouldn't trigger anything."""
    notes = assess_setup_health(
        mic_name="alsa_input.usb-Sennheiser_GAME_ONE-00.mono-fallback",
        system_sink_name="alsa_output.usb-Sennheiser_GAME_ONE-00.analog-stereo",
    )
    assert notes == []


def test_empty_inputs_return_no_notes():
    """Default (unconfigured) devices shouldn't trigger spurious warnings."""
    assert assess_setup_health(None, None) == []
    assert assess_setup_health("", "") == []


def test_builtin_laptop_mic_gets_quality_note():
    """Built-in laptop mics get a softer-than-webcam quality nudge."""
    notes = assess_setup_health(
        mic_name="alsa_input.pci-0000_00_1f.3.analog-stereo",
        system_sink_name="alsa_output.pci-0000_00_1f.3.analog-stereo",
    )
    # Either webcam-style or laptop-built-in language is fine
    joined = " ".join(n.message.lower() for n in notes)
    assert "built-in" in joined or "laptop" in joined or "dedicated" in joined
