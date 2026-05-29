"""Real-time audio level meter for the mic, used by the recording view.

We run a *separate* lightweight capture process alongside the actual
recorder, reading raw PCM samples and computing peak amplitude every ~100ms.
PipeWire/Pulse allow multiple simultaneous readers of the same source, so
this doesn't interfere with the main recording.

We keep this small and dependency-free (no numpy) — peak amplitude on s16le
is just ``max(abs(int16))`` which we get from ``audioop`` (stdlib) or fall
back to a manual loop.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import threading
from typing import Callable, Optional

from .logger import get_logger

logger = get_logger(__name__)


# 8 kHz mono s16le — plenty for a level meter, ~16 KB/s of stderr-free PCM.
_RATE = 8000
_CHANNELS = 1
_FORMAT = "s16le"
_BYTES_PER_SAMPLE = 2
_CHUNK_MS = 100
_CHUNK_BYTES = (_RATE * _BYTES_PER_SAMPLE * _CHUNK_MS) // 1000


def _peak_s16le(buf: bytes) -> int:
    """Return peak absolute amplitude (0..32767) for an s16le byte buffer."""
    if not buf:
        return 0
    try:
        # audioop is the fast path; deprecated in 3.13 but still available
        # in 3.14 via a third-party shim if installed. Fall back to struct.
        import audioop  # type: ignore[import]

        return audioop.max(buf, _BYTES_PER_SAMPLE)
    except Exception:
        # Trim any odd trailing byte
        n = (len(buf) // _BYTES_PER_SAMPLE) * _BYTES_PER_SAMPLE
        if n == 0:
            return 0
        samples = struct.unpack(f"<{n // _BYTES_PER_SAMPLE}h", buf[:n])
        peak = 0
        for s in samples:
            if s == -32768:
                v = 32767
            else:
                v = -s if s < 0 else s
            if v > peak:
                peak = v
        return peak


class MicLevelMeter:
    """Background thread that samples mic peak amplitude.

    Usage:
        meter = MicLevelMeter(on_level=callback, device=None)
        meter.start()
        # ... callback fires every ~100ms with level in [0.0, 1.0]
        meter.stop()

    The callback receives a single float ``0.0..1.0``. It runs on the
    meter's worker thread; UI integrations should use ``call_from_thread``
    or similar.
    """

    def __init__(
        self,
        on_level: Callable[[float], None],
        device: Optional[str] = None,
    ):
        self._on_level = on_level
        self._device = device or None
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def is_available(self) -> bool:
        """Return True if the system has the tools we need."""
        return bool(shutil.which("parec") or shutil.which("pw-record"))

    def start(self) -> None:
        if self._thread is not None:
            return
        cmd = self._build_cmd()
        if cmd is None:
            logger.info("No level-meter capture tool available; meter disabled")
            return

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Failed to start level meter: {exc}")
            self._proc = None
            return

        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="mic-level-meter", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc is not None:
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            except Exception:
                pass
            self._proc = None
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None

    # ----- internals -----

    def _build_cmd(self) -> Optional[list[str]]:
        """Build the raw-PCM capture command, preferring parec."""
        if shutil.which("parec"):
            cmd = [
                "parec",
                f"--rate={_RATE}",
                f"--channels={_CHANNELS}",
                f"--format={_FORMAT}",
                "--raw",
                "--latency-msec=50",
            ]
            if self._device:
                cmd.append(f"--device={self._device}")
            return cmd

        if shutil.which("pw-record"):
            # pw-record writes WAV by default; --format=s16 + raw stdout
            # gives us PCM on stdout when the output path is "-".
            cmd = [
                "pw-record",
                f"--rate={_RATE}",
                f"--channels={_CHANNELS}",
                "--format=s16",
                "--latency=50ms",
            ]
            if self._device:
                cmd.append(f"--target={self._device}")
            cmd.append("-")
            return cmd

        return None

    def _loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stdout = self._proc.stdout
        try:
            while not self._stop.is_set():
                buf = stdout.read(_CHUNK_BYTES)
                if not buf:
                    # capture process exited
                    break
                peak = _peak_s16le(buf)
                level = min(peak / 32767.0, 1.0)
                try:
                    self._on_level(level)
                except Exception as exc:  # noqa: BLE001 - never let UI errors kill the meter
                    logger.debug(f"level meter callback failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"level meter loop ended: {exc}")
