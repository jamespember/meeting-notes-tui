"""Desktop integration helpers for status bars and notifications."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


VALID_STATES = {"ready", "recording", "processing"}


def status_path() -> Path:
    """Return the private runtime status path."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / "omascribe" / "status.json"

    state_home = os.environ.get("XDG_STATE_HOME")
    state_dir = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return state_dir / "omascribe" / "status.json"


def _process_identity(pid: int) -> tuple[str, str]:
    """Return boot and process-start identifiers to guard against PID reuse."""
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        stat_fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        start_time = stat_fields[19]
        return boot_id, start_time
    except (FileNotFoundError, IndexError, OSError):
        return "", ""


def write_status(state: str, duration: str = "") -> None:
    """Atomically publish application state for desktop integrations."""
    if state not in VALID_STATES:
        raise ValueError(f"Invalid desktop state: {state}")

    path = status_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    boot_id, start_time = _process_identity(os.getpid())
    payload = {
        "version": 1,
        "state": state,
        "duration": duration,
        "pid": os.getpid(),
        "boot_id": boot_id,
        "start_time": start_time,
    }

    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".status-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def clear_status() -> None:
    """Remove status only when it belongs to this process."""
    path = status_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        boot_id, start_time = _process_identity(os.getpid())
        if (
            payload.get("pid") == os.getpid()
            and payload.get("boot_id") == boot_id
            and payload.get("start_time") == start_time
        ):
            path.unlink()
    except (FileNotFoundError, OSError, ValueError, TypeError):
        pass


def _process_matches(payload: dict) -> bool:
    pid = int(payload["pid"])
    boot_id, start_time = _process_identity(pid)
    return bool(boot_id and start_time) and payload.get("boot_id") == boot_id and payload.get("start_time") == start_time


def bar_status() -> int:
    """Print Waybar-style JSON understood by Quattro command modules."""
    result = {
        "text": "󰗠",
        "tooltip": "Omascribe is not running",
        "class": "idle",
    }

    try:
        payload = json.loads(status_path().read_text(encoding="utf-8"))
        if not _process_matches(payload):
            raise ProcessLookupError

        state = payload.get("state")
        if state == "recording":
            duration = str(payload.get("duration") or "00:00")
            result = {
                "text": f"󰦕 {duration}",
                "tooltip": "Recording in progress",
                "class": "active",
            }
        elif state == "processing":
            result = {
                "text": "󰄬",
                "tooltip": "Processing recording",
                "class": "processing",
            }
        elif state == "ready":
            result = {
                "text": "󰗠",
                "tooltip": "Omascribe is ready",
                "class": "ready",
            }
    except (FileNotFoundError, KeyError, TypeError, ValueError, OSError, ProcessLookupError):
        pass

    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


def notify_desktop(
    body: str,
    *,
    urgency: str = "normal",
    glyph: str = "󰗠",
) -> None:
    """Send a clickable desktop notification without blocking the TUI."""
    if os.environ.get("MEETING_NOTES_DISABLE_DESKTOP_NOTIFICATIONS") == "1":
        return

    sender = shutil.which("omarchy-notification-send")
    if sender:
        command = [
            sender,
            "--app-name",
            "omascribe",
            "--urgency",
            urgency,
            "--glyph",
            glyph,
            "--exec",
            "omarchy-launch-or-focus-tui omascribe",
            "Omascribe",
            body,
        ]
    else:
        sender = shutil.which("notify-send")
        if not sender:
            return
        command = [sender, "--app-name", "omascribe", "--urgency", urgency, "Omascribe", body]

    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass
