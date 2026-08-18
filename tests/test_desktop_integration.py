"""Tests for desktop status and notification integration."""

import json
import os

from omascribe import desktop


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
    assert "omarchy-launch-or-focus-tui omascribe" in command
    assert command[-1] == "Safe status"
    assert kwargs["start_new_session"] is True


def test_panel_data_reports_status_and_recent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "older.md").write_text("---\ntitle: Older meeting\ndate: 2026-01-10\nword_count: 120\n---\n")
    (notes / "newer.md").write_text("---\ntitle: Newer meeting\ndate: 2026-01-15\nword_count: 340\n---\n")

    config = tmp_path / "config.yaml"
    config.write_text(f"notes_dir: {notes}\n")
    monkeypatch.setattr(desktop.config, "load_config", lambda: _FakeConfig(str(notes)))
    monkeypatch.setattr(desktop.config, "get_config_path", lambda: config)

    desktop.write_status("recording", "02:10")

    assert desktop.panel_data() == 0

    result = json.loads(capsys.readouterr().out)
    assert result["status"]["state"] == "recording"
    assert result["status"]["duration"] == "02:10"
    assert result["notes_dir"] == str(notes)
    assert result["config_path"] == str(config)
    assert [n["title"] for n in result["recent"]] == ["Newer meeting", "Older meeting"]
    assert result["recent"][0]["words"] == "340"


def test_panel_data_degrades_gracefully(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(desktop.config, "load_config", lambda: _FakeConfig(str(tmp_path / "nonexistent")))
    monkeypatch.setattr(desktop.config, "get_config_path", lambda: tmp_path / "config.yaml")

    desktop.panel_data()

    result = json.loads(capsys.readouterr().out)
    assert result["status"]["state"] == "ready"
    assert result["recent"] == []


class _FakeConfig:
    def __init__(self, notes_dir):
        self.notes_dir = notes_dir
