"""Tests for the audio recorder command construction and lifecycle.

These tests don't actually capture audio. They monkeypatch ``shutil.which``
and ``subprocess.Popen`` to verify the recorder builds the right command,
handles spawn failures, mixes correctly, and cancels without invoking
ffmpeg.
"""

from __future__ import annotations

from pathlib import Path
from typing import List
import wave

import pytest

from meeting_notes import recorder as rec_module
from meeting_notes.recorder import AudioRecorder, resolve_monitor_source


class _FakeProc:
    def __init__(self, cmd: List[str], alive: bool = True, stderr: bytes = b""):
        self.cmd = cmd
        self._alive = alive
        self._stderr_buf = stderr
        self.returncode = None if alive else 1
        self.pid = 12345
        self.terminated = False
        self.killed = False
        self.signaled = []

        class _PipeStub:
            def __init__(self, data: bytes):
                self._data = data
                self._read = False

            def read(self) -> bytes:
                if self._read:
                    return b""
                self._read = True
                return self._data

        self.stderr = _PipeStub(stderr) if stderr else _PipeStub(b"")

    def poll(self):
        return None if self._alive else self.returncode

    def send_signal(self, sig):
        self.signaled.append(sig)

    def wait(self, timeout=None):
        # Pretend the process exits cleanly when waited on
        if self._alive:
            self._alive = False
            self.returncode = 0
        return self.returncode

    def terminate(self):
        self.terminated = True
        self._alive = False
        self.returncode = 0

    def kill(self):
        self.killed = True
        self._alive = False
        self.returncode = 0


@pytest.fixture
def fake_pw_only(monkeypatch):
    """Pretend pw-record exists, parec doesn't, ffmpeg exists."""
    def fake_which(name):
        return {"pw-record": "/usr/bin/pw-record", "ffmpeg": "/usr/bin/ffmpeg"}.get(name)

    monkeypatch.setattr(rec_module.shutil, "which", fake_which)


@pytest.fixture
def fake_parec_only(monkeypatch):
    """Pretend only parec exists."""
    def fake_which(name):
        return {"parec": "/usr/bin/parec", "ffmpeg": "/usr/bin/ffmpeg"}.get(name)

    monkeypatch.setattr(rec_module.shutil, "which", fake_which)


@pytest.fixture
def capture_popen(monkeypatch):
    """Capture every Popen invocation."""
    calls: List[List[str]] = []

    def fake_popen(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return _FakeProc(cmd)

    monkeypatch.setattr(rec_module.subprocess, "Popen", fake_popen)
    # Skip the post-spawn sleep so tests are fast
    monkeypatch.setattr(rec_module.time, "sleep", lambda *_: None)
    return calls


def test_mic_mode_uses_pw_record_with_no_target(tmp_path, fake_pw_only, capture_popen):
    rec = AudioRecorder(output_dir=str(tmp_path), mode="mic")
    rec.start_recording("test.wav")

    assert len(capture_popen) == 1
    cmd = capture_popen[0]
    assert cmd[0] == "pw-record"
    assert "--channels=1" in cmd
    assert not any(a.startswith("--target=") for a in cmd), \
        "mic mode with default device should not pass --target"


def test_mic_mode_passes_chosen_device(tmp_path, fake_pw_only, capture_popen):
    rec = AudioRecorder(
        output_dir=str(tmp_path),
        mode="mic",
        mic_device="alsa_input.usb-Some_Mic",
    )
    rec.start_recording("test.wav")

    cmd = capture_popen[0]
    assert "--target=alsa_input.usb-Some_Mic" in cmd


def test_system_mode_resolves_monitor(tmp_path, fake_pw_only, capture_popen, monkeypatch):
    # Force a known default sink
    monkeypatch.setattr(rec_module, "_get_default", lambda kind: "alsa_output.demo" if kind == "sink" else None)

    rec = AudioRecorder(output_dir=str(tmp_path), mode="system")
    rec.start_recording("test.wav")

    cmd = capture_popen[0]
    assert "--target=alsa_output.demo.monitor" in cmd, \
        "system mode must record from <sink>.monitor, not the sink itself"


def test_system_mode_with_explicit_sink(tmp_path, fake_pw_only, capture_popen):
    rec = AudioRecorder(
        output_dir=str(tmp_path),
        mode="system",
        system_device="alsa_output.scarlett",
    )
    rec.start_recording("test.wav")
    cmd = capture_popen[0]
    assert "--target=alsa_output.scarlett.monitor" in cmd


def test_combined_mode_spawns_two_processes(tmp_path, fake_pw_only, capture_popen, monkeypatch):
    monkeypatch.setattr(rec_module, "_get_default", lambda kind: "default-sink" if kind == "sink" else None)
    rec = AudioRecorder(output_dir=str(tmp_path), mode="combined")
    rec.start_recording("test.wav")

    assert len(capture_popen) == 2
    cmds = capture_popen
    # One should target the sink monitor
    assert any("--target=default-sink.monitor" in c for c in cmds)
    # Both should write to a temp- prefixed file
    out_args = [c[-1] for c in cmds]
    assert all("temp-" in arg for arg in out_args)


def test_parec_fallback_uses_wav_file_format(tmp_path, fake_parec_only, capture_popen):
    """parec without --file-format=wav writes raw PCM into a .wav file."""
    rec = AudioRecorder(output_dir=str(tmp_path), mode="mic")
    rec.start_recording("test.wav")

    cmd = capture_popen[0]
    assert cmd[0] == "parec"
    assert "--file-format=wav" in cmd, \
        "parec must be told to write a real WAV container"


def test_no_capture_tool_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(rec_module.shutil, "which", lambda name: None)
    rec = AudioRecorder(output_dir=str(tmp_path), mode="mic")
    with pytest.raises(RuntimeError, match="No audio capture tool"):
        rec.start_recording("test.wav")


def test_dead_process_at_startup_raises_and_clears_state(tmp_path, fake_pw_only, monkeypatch):
    """If the capture process exits immediately, recorder must NOT be 'recording'."""
    def fake_popen(cmd, *args, **kwargs):
        return _FakeProc(cmd, alive=False, stderr=b"target not found")

    monkeypatch.setattr(rec_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(rec_module.time, "sleep", lambda *_: None)

    rec = AudioRecorder(output_dir=str(tmp_path), mode="mic")
    with pytest.raises(RuntimeError, match="exited immediately"):
        rec.start_recording("test.wav")

    assert not rec.is_recording()
    assert rec.process is None
    assert rec.current_file is None


def test_cancel_does_not_invoke_ffmpeg_and_deletes_files(tmp_path, fake_pw_only, capture_popen, monkeypatch):
    """cancel_recording must not run ffmpeg and must delete the captured files."""
    monkeypatch.setattr(rec_module, "_get_default", lambda kind: "snk" if kind == "sink" else None)

    # Track all subprocess.run calls (ffmpeg goes through .run, not Popen)
    run_calls = []
    monkeypatch.setattr(rec_module.subprocess, "run", lambda *a, **kw: run_calls.append(a) or None)

    rec = AudioRecorder(output_dir=str(tmp_path), mode="combined")
    path = rec.start_recording("test.wav")

    # Pretend the temp files materialised
    for f in rec.temp_files:
        f.write_bytes(b"fake wav")
    Path(path).write_bytes(b"fake final")

    # Snapshot temp paths before cancel, since cancel clears the list
    temp_paths = list(rec.temp_files)

    rec.cancel_recording()

    assert run_calls == [], "ffmpeg must not be invoked on cancel"
    assert not Path(path).exists(), "final wav must be deleted"
    for tmp in temp_paths:
        assert not tmp.exists(), f"temp file {tmp} should have been deleted"


def _write_test_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(b"\x01\x00" * 480)


def test_failed_mix_preserves_recovery_legs(tmp_path, fake_pw_only, capture_popen, monkeypatch):
    monkeypatch.setattr(rec_module, "_get_default", lambda kind: "snk" if kind == "sink" else None)
    rec = AudioRecorder(output_dir=str(tmp_path), mode="combined")
    rec.start_recording("final.wav")
    recovery_files = list(rec.temp_files)
    for path in recovery_files:
        _write_test_wav(path)
    monkeypatch.setattr(rec, "_mix_combined", lambda inputs, output: False)

    with pytest.raises(RuntimeError, match="separate mic and system files were preserved"):
        rec.stop_recording()

    assert rec.last_temp_files == recovery_files
    assert all(path.exists() for path in recovery_files)
    assert not (tmp_path / "final.wav").exists()


def test_single_capture_requires_valid_wav(tmp_path, fake_pw_only, capture_popen):
    rec = AudioRecorder(output_dir=str(tmp_path), mode="mic")
    rec.start_recording("invalid.wav")
    (tmp_path / "invalid.wav").write_bytes(b"")

    with pytest.raises(RuntimeError, match="valid WAV"):
        rec.stop_recording()


def test_resolve_monitor_source_appends_monitor():
    assert resolve_monitor_source("foo.bar") == "foo.bar.monitor"


def test_resolve_monitor_source_uses_default(monkeypatch):
    monkeypatch.setattr(rec_module, "_get_default", lambda kind: "the-default" if kind == "sink" else None)
    assert resolve_monitor_source(None) == "the-default.monitor"


def test_resolve_monitor_source_returns_none_when_no_default(monkeypatch):
    monkeypatch.setattr(rec_module, "_get_default", lambda kind: None)
    assert resolve_monitor_source(None) is None


# -- Tool selection by target type -----------------------------------------
#
# Why these tests exist: on James' Focusrite Scarlett Solo running PipeWire
# 1.6.4, pw-record captures sink-monitor sources 45 dB QUIETER than parec on
# the same monitor, same instant, same audio. Reproduced deterministically.
# So the recorder MUST use parec for monitor capture, even when pw-record is
# available. For ordinary mic sources, pw-record is fine and is preferred.
# Regressing this would silently destroy users' system-audio recordings.


def test_monitor_capture_prefers_parec_even_when_pwrecord_available(tmp_path, monkeypatch):
    """For .monitor targets, parec is preferred over pw-record."""
    monkeypatch.setattr(
        rec_module.shutil,
        "which",
        lambda name: {"pw-record": "/usr/bin/pw-record", "parec": "/usr/bin/parec"}.get(name),
    )
    rec = AudioRecorder(output_dir=str(tmp_path), mode="system")
    cmd = rec._build_capture_cmd(
        tmp_path / "out.wav",
        channels=2,
        target="alsa_output.foo.monitor",
    )
    assert cmd[0] == "parec", (
        "monitor capture must use parec (pw-record attenuates sink monitors "
        "by 45 dB on some hardware — see Focusrite Scarlett bug)"
    )
    assert "--file-format=wav" in cmd
    assert "--device=alsa_output.foo.monitor" in cmd


def test_monitor_capture_falls_back_to_pwrecord_when_parec_missing(tmp_path, monkeypatch):
    """If parec isn't installed, fall back to pw-record for monitor capture."""
    monkeypatch.setattr(
        rec_module.shutil,
        "which",
        lambda name: {"pw-record": "/usr/bin/pw-record"}.get(name),
    )
    rec = AudioRecorder(output_dir=str(tmp_path), mode="system")
    cmd = rec._build_capture_cmd(
        tmp_path / "out.wav",
        channels=2,
        target="alsa_output.foo.monitor",
    )
    assert cmd[0] == "pw-record"
    assert "--target=alsa_output.foo.monitor" in cmd


def test_mic_capture_prefers_pwrecord(tmp_path, monkeypatch):
    """Non-monitor (mic) capture keeps pw-record as the preferred tool."""
    monkeypatch.setattr(
        rec_module.shutil,
        "which",
        lambda name: {"pw-record": "/usr/bin/pw-record", "parec": "/usr/bin/parec"}.get(name),
    )
    rec = AudioRecorder(output_dir=str(tmp_path), mode="mic")
    cmd = rec._build_capture_cmd(
        tmp_path / "out.wav",
        channels=1,
        target="alsa_input.usb-WebcamMic.analog-stereo",
    )
    assert cmd[0] == "pw-record"


def test_default_mic_capture_uses_pwrecord(tmp_path, monkeypatch):
    """When mic target is None (system default), still pw-record."""
    monkeypatch.setattr(
        rec_module.shutil,
        "which",
        lambda name: {"pw-record": "/usr/bin/pw-record", "parec": "/usr/bin/parec"}.get(name),
    )
    rec = AudioRecorder(output_dir=str(tmp_path), mode="mic")
    cmd = rec._build_capture_cmd(tmp_path / "out.wav", channels=1, target=None)
    assert cmd[0] == "pw-record"
    # No --target argument when target is None
    assert not any(a.startswith("--target=") for a in cmd)


def test_combined_mode_uses_parec_for_system_and_pwrecord_for_mic(
    tmp_path, fake_pw_only, capture_popen, monkeypatch
):
    """Integration check: in combined mode, the two spawned processes use
    different tools — pw-record for mic, parec for sink monitor."""
    # Override fake_pw_only fixture to also expose parec
    monkeypatch.setattr(
        rec_module.shutil,
        "which",
        lambda name: {
            "pw-record": "/usr/bin/pw-record",
            "parec": "/usr/bin/parec",
            "ffmpeg": "/usr/bin/ffmpeg",
        }.get(name),
    )
    monkeypatch.setattr(rec_module, "_get_default", lambda kind: "thesink" if kind == "sink" else None)

    rec = AudioRecorder(output_dir=str(tmp_path), mode="combined")
    rec.start_recording("test.wav")

    # Filter to actual capture processes (those writing a file path,
    # not the keep-awake sentinel which writes to nowhere via --raw).
    capture_cmds = [c for c in capture_popen if not any(a == "--raw" for a in c)]
    assert len(capture_cmds) == 2, f"expected 2 capture processes, got {capture_cmds}"
    tools_used = {c[0] for c in capture_cmds}
    assert "pw-record" in tools_used, "mic leg should use pw-record"
    assert "parec" in tools_used, (
        "system leg MUST use parec — pw-record attenuates monitor capture"
    )
    # Verify the parec command targets the monitor source AND writes a WAV
    parec_cmd = next(c for c in capture_cmds if c[0] == "parec")
    assert any(a == "--device=thesink.monitor" for a in parec_cmd)
    assert "--file-format=wav" in parec_cmd
