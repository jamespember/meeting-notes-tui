"""Tests for desktop status and notification integration."""

import json
import os

from meeting_notes import desktop


def test_status_is_private_and_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    desktop.write_status("recording", "01:23")

    path = desktop.status_path()
    payload = json.loads(path.read_text())
    assert payload["version"] == 1
    assert payload["state"] == "recording"
    assert payload["duration"] == "01:23"
    assert payload["pid"] == os.getpid()
    assert payload["boot_id"]
    assert payload["start_time"]
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert list(path.parent.glob(".status-*")) == []


def test_bar_status_reports_recording(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    desktop.write_status("recording", "05:42")

    assert desktop.bar_status() == 0

    result = json.loads(capsys.readouterr().out)
    assert result["text"].endswith("05:42")
    assert result["class"] == "active"


def test_bar_status_ignores_stale_or_malformed_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    path = desktop.status_path()
    path.parent.mkdir(parents=True)
    path.write_text("not json")

    desktop.bar_status()

    assert json.loads(capsys.readouterr().out)["class"] == "idle"


def test_bar_status_ignores_pid_identity_mismatch(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    desktop.write_status("recording", "09:00")
    path = desktop.status_path()
    payload = json.loads(path.read_text())
    payload["start_time"] = "stale-process"
    path.write_text(json.dumps(payload))

    desktop.bar_status()

    assert json.loads(capsys.readouterr().out)["class"] == "idle"


def test_clear_status_only_removes_current_process(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    desktop.write_status("ready")
    desktop.clear_status()
    assert not desktop.status_path().exists()

    desktop.status_path().write_text(json.dumps({"pid": os.getpid() + 1}))
    desktop.clear_status()
    assert desktop.status_path().exists()


def test_invalid_status_is_rejected():
    try:
        desktop.write_status("idle")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid status should be rejected")


def test_omarchy_notification_is_clickable(monkeypatch):
    calls = []
    monkeypatch.delenv("MEETING_NOTES_DISABLE_DESKTOP_NOTIFICATIONS", raising=False)
    monkeypatch.setattr(desktop.shutil, "which", lambda command: "/bin/omarchy-notification-send")
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda command, **kwargs: calls.append((command, kwargs)))

    desktop.notify_desktop("Safe status")

    command, kwargs = calls[0]
    assert "--exec" in command
    assert "omarchy-launch-or-focus-tui meeting-notes" in command
    assert command[-1] == "Safe status"
    assert kwargs["start_new_session"] is True
