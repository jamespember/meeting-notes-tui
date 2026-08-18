"""Tests for meeting-app routing diagnostics.

These cover the helpers that let the app warn the user when Zoom / Meet /
Teams etc. are playing audio through a sink we're not capturing. This is
the high-stakes case where ignoring the warning means recording a
12-minute meeting that only contains your own voice.
"""

from __future__ import annotations

from omascribe import recorder as rec_module
from omascribe.recorder import (
    SinkInput,
    diagnose_meeting_routing,
    is_meeting_app,
)


# ---- is_meeting_app --------------------------------------------------------


def test_is_meeting_app_recognises_zoom():
    assert is_meeting_app("zoom")
    assert is_meeting_app("Zoom")
    assert is_meeting_app("ZOOM Workplace")


def test_is_meeting_app_recognises_google_meet_via_chrome():
    """Browser-based Meet shows up as 'Chrome' or 'Chromium' on Linux."""
    assert is_meeting_app("Chromium")
    assert is_meeting_app("Google Chrome")


def test_is_meeting_app_recognises_teams_slack_discord():
    assert is_meeting_app("Microsoft Teams")
    assert is_meeting_app("Slack")
    assert is_meeting_app("Discord")


def test_is_meeting_app_rejects_non_meeting_apps():
    assert not is_meeting_app("Spotify")
    assert not is_meeting_app("VLC media player")
    assert not is_meeting_app("mpv")
    assert not is_meeting_app(None)
    assert not is_meeting_app("")


# ---- diagnose_meeting_routing ---------------------------------------------


def _make_si(idx: str, sink: str, app: str, corked: bool = False) -> SinkInput:
    return SinkInput(
        index=idx, sink=sink, application=app, media_name="Playback", corked=corked,
    )


def test_routing_diagnose_returns_empty_when_no_target(monkeypatch):
    monkeypatch.setattr(rec_module, "list_active_sink_inputs", lambda **kw: [])
    assert diagnose_meeting_routing(None) == []
    assert diagnose_meeting_routing("") == []


def test_routing_diagnose_returns_empty_when_nothing_playing(monkeypatch):
    monkeypatch.setattr(rec_module, "list_active_sink_inputs", lambda **kw: [])
    assert diagnose_meeting_routing("alsa_output.foo") == []


def test_routing_diagnose_zoom_on_captured_sink_is_info(monkeypatch):
    """Zoom is on the sink we'd capture → reassuring info note."""
    monkeypatch.setattr(
        rec_module,
        "list_active_sink_inputs",
        lambda **kw: [_make_si("1", "470", "Zoom")],
    )
    monkeypatch.setattr(
        rec_module,
        "_sink_index_to_name",
        lambda: {"470": "alsa_output.scarlett"},
    )

    notes = diagnose_meeting_routing("alsa_output.scarlett")
    assert len(notes) == 1
    assert notes[0].severity == "info"
    assert "zoom" in notes[0].message.lower()
    assert "captured" in notes[0].message.lower()


def test_routing_diagnose_zoom_on_other_sink_is_warn(monkeypatch):
    """Zoom is playing through speakers but we'd capture the Scarlett."""
    monkeypatch.setattr(
        rec_module,
        "list_active_sink_inputs",
        lambda **kw: [_make_si("1", "350", "Zoom")],
    )
    monkeypatch.setattr(
        rec_module,
        "_sink_index_to_name",
        lambda: {"350": "alsa_output.builtin", "470": "alsa_output.scarlett"},
    )

    notes = diagnose_meeting_routing("alsa_output.scarlett")
    assert len(notes) == 1
    assert notes[0].severity == "warn"
    msg = notes[0].message.lower()
    assert "zoom" in msg
    assert "missing" in msg or "not" in msg


def test_routing_diagnose_ignores_non_meeting_apps(monkeypatch):
    """Spotify playing on the wrong sink doesn't warrant a meeting warning."""
    monkeypatch.setattr(
        rec_module,
        "list_active_sink_inputs",
        lambda **kw: [_make_si("1", "350", "Spotify")],
    )
    monkeypatch.setattr(
        rec_module,
        "_sink_index_to_name",
        lambda: {"350": "alsa_output.builtin"},
    )
    notes = diagnose_meeting_routing("alsa_output.scarlett")
    assert notes == []


def test_routing_diagnose_dedupes_multiple_chromium_streams(monkeypatch):
    """Browser-based Meet often has 3 Chromium streams to the same sink.
    We should still surface just one note for the app, not three."""
    monkeypatch.setattr(
        rec_module,
        "list_active_sink_inputs",
        lambda **kw: [
            _make_si("1", "470", "Chromium"),
            _make_si("2", "470", "Chromium"),
            _make_si("3", "470", "Chromium"),
        ],
    )
    monkeypatch.setattr(
        rec_module,
        "_sink_index_to_name",
        lambda: {"470": "alsa_output.scarlett"},
    )
    notes = diagnose_meeting_routing("alsa_output.scarlett")
    assert len(notes) == 1


def test_routing_diagnose_handles_mixed_apps(monkeypatch):
    """Zoom on the right sink + Chrome on the wrong sink → one info + one warn."""
    monkeypatch.setattr(
        rec_module,
        "list_active_sink_inputs",
        lambda **kw: [
            _make_si("1", "470", "Zoom"),
            _make_si("2", "350", "Chromium"),
        ],
    )
    monkeypatch.setattr(
        rec_module,
        "_sink_index_to_name",
        lambda: {"350": "alsa_output.builtin", "470": "alsa_output.scarlett"},
    )
    notes = diagnose_meeting_routing("alsa_output.scarlett")
    severities = {n.severity for n in notes}
    assert "info" in severities
    assert "warn" in severities
