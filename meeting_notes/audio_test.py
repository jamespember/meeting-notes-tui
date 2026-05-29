"""Audio diagnostics: analyse a captured WAV file and produce a verdict.

Used by the in-app "Test mode" to give a clear pass/warn/fail summary so
the user can confirm their audio pipeline is working without having to
commit to a full meeting recording.

Pure functions only — no subprocess, no UI. The TUI layer in app.py drives
the actual capture/playback and feeds resulting WAVs in here.
"""

from __future__ import annotations

import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List, Literal, Optional

from .logger import get_logger

logger = get_logger(__name__)


Verdict = Literal["pass", "warn", "fail"]


@dataclass
class WavStats:
    """Stats extracted from a WAV file."""

    path: Path
    exists: bool
    size_bytes: int
    duration_seconds: float
    channels: int
    sample_rate: int
    sample_width: int
    peak: int  # 0..32767 for s16
    rms: float  # 0..32767
    silent_ratio: float  # 0..1, fraction of 100ms windows below silence threshold

    @property
    def peak_pct(self) -> float:
        return min(self.peak / 32767.0, 1.0) if self.peak else 0.0

    @property
    def rms_pct(self) -> float:
        return min(self.rms / 32767.0, 1.0) if self.rms else 0.0


@dataclass
class AudioTestReport:
    """The full report shown to the user."""

    verdict: Verdict
    summary: str
    findings: List[str]
    stats: Optional[WavStats]


# Anything below this peak (out of 32767) is treated as "effectively silent"
# for a single 100ms window. This is roughly -50 dBFS, which is below the
# noise floor of most consumer mics with no input.
_SILENCE_THRESHOLD = 200

# Anything below this PEAK over the whole recording is treated as "no
# signal at all" — i.e. the capture pipeline produced nothing usable.
# Some hardware (especially USB audio interfaces like the Focusrite
# Scarlett) emit sink-monitor signals at -38 to -40 dBFS even with
# normal-loudness playback. So a strict "any window quiet = fail"
# rule punishes perfectly working setups. A peak under this absolute
# threshold (~-50 dBFS) is the real "broken pipeline" signal.
_TRUE_SILENCE_PEAK = 100


def analyse_wav(path: Path) -> WavStats:
    """Read a WAV file and compute peak/RMS/silence metrics.

    Returns a stats object even on failure (with exists=False / zeros) so
    callers don't need to special-case missing files.
    """
    p = Path(path)
    if not p.exists():
        return WavStats(
            path=p, exists=False, size_bytes=0, duration_seconds=0.0,
            channels=0, sample_rate=0, sample_width=0,
            peak=0, rms=0.0, silent_ratio=1.0,
        )

    size = p.stat().st_size
    try:
        with wave.open(str(p), "rb") as wf:
            channels = wf.getnchannels()
            sample_rate = wf.getframerate()
            sample_width = wf.getsampwidth()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
    except (wave.Error, EOFError) as exc:
        logger.warning(f"Could not parse WAV {p}: {exc}")
        return WavStats(
            path=p, exists=True, size_bytes=size, duration_seconds=0.0,
            channels=0, sample_rate=0, sample_width=0,
            peak=0, rms=0.0, silent_ratio=1.0,
        )

    duration = n_frames / sample_rate if sample_rate else 0.0

    if sample_width != 2 or not raw:
        # We only support s16 here. Other widths still get duration/size,
        # but no peak/RMS analysis.
        return WavStats(
            path=p, exists=True, size_bytes=size, duration_seconds=duration,
            channels=channels, sample_rate=sample_rate, sample_width=sample_width,
            peak=0, rms=0.0, silent_ratio=1.0,
        )

    peak, rms, silent_ratio = _analyse_s16le(raw, channels, sample_rate)

    return WavStats(
        path=p,
        exists=True,
        size_bytes=size,
        duration_seconds=duration,
        channels=channels,
        sample_rate=sample_rate,
        sample_width=sample_width,
        peak=peak,
        rms=rms,
        silent_ratio=silent_ratio,
    )


def _analyse_s16le(raw: bytes, channels: int, sample_rate: int) -> tuple[int, float, float]:
    """Return (peak, rms, silent_ratio) for an s16le buffer.

    Uses ``audioop`` when available for speed; falls back to a manual loop.
    The silent_ratio is the fraction of 100ms windows whose peak is below
    ``_SILENCE_THRESHOLD``. A value near 1.0 means "almost certainly nothing
    was captured".
    """
    sample_width = 2
    frames_per_window = max(int(sample_rate * 0.1), 1)
    bytes_per_frame = sample_width * max(channels, 1)
    bytes_per_window = frames_per_window * bytes_per_frame

    try:
        import audioop  # type: ignore[import]

        peak = audioop.max(raw, sample_width)
        rms = float(audioop.rms(raw, sample_width))
        windows = 0
        silent_windows = 0
        for offset in range(0, len(raw), bytes_per_window):
            chunk = raw[offset:offset + bytes_per_window]
            if not chunk:
                continue
            windows += 1
            if audioop.max(chunk, sample_width) < _SILENCE_THRESHOLD:
                silent_windows += 1
        silent_ratio = (silent_windows / windows) if windows else 1.0
        return peak, rms, silent_ratio
    except Exception:
        return _analyse_s16le_fallback(raw, channels, sample_rate)


def _analyse_s16le_fallback(raw: bytes, channels: int, sample_rate: int) -> tuple[int, float, float]:
    import struct

    sample_width = 2
    n = (len(raw) // sample_width) * sample_width
    if n == 0:
        return 0, 0.0, 1.0
    samples = struct.unpack(f"<{n // sample_width}h", raw[:n])

    peak = 0
    sq_sum = 0.0
    for s in samples:
        v = -s if s < 0 else s
        if s == -32768:
            v = 32767
        if v > peak:
            peak = v
        sq_sum += v * v
    rms = (sq_sum / len(samples)) ** 0.5

    # Silence windowing
    frames_per_window = max(int(sample_rate * 0.1), 1)
    samples_per_window = frames_per_window * max(channels, 1)
    silent_windows = 0
    windows = 0
    for offset in range(0, len(samples), samples_per_window):
        window = samples[offset:offset + samples_per_window]
        if not window:
            continue
        windows += 1
        wpeak = 0
        for s in window:
            v = -s if s < 0 else s
            if v > wpeak:
                wpeak = v
        if wpeak < _SILENCE_THRESHOLD:
            silent_windows += 1
    silent_ratio = (silent_windows / windows) if windows else 1.0

    return peak, rms, silent_ratio


def diagnose(
    stats: WavStats,
    expected_min_seconds: float = 1.0,
    leg: str = "audio",
) -> AudioTestReport:
    """Turn raw WAV stats into a human-friendly verdict.

    Args:
        stats: result of analyse_wav().
        expected_min_seconds: warn if the recording is shorter than this.
        leg: ``"mic"``, ``"system"``, or ``"audio"`` (generic). Used to
            tailor the wording of findings ("boost mic gain" vs "turn up
            playback volume").

    Verdict scale:
      - ``pass``: real audio captured, reasonable level, not clipping.
      - ``warn``: captured something, but it's quiet/clipping/short.
      - ``fail``: file missing/empty/silent; the pipeline is broken.
    """
    findings: List[str] = []

    if not stats.exists:
        return AudioTestReport(
            verdict="fail",
            summary="No file was produced.",
            findings=["The recorder didn't write any output. "
                      "Most likely the capture command failed to spawn — "
                      "check ~/.config/meeting-notes/errors.log."],
            stats=stats,
        )

    if stats.size_bytes < 1024:
        return AudioTestReport(
            verdict="fail",
            summary="Output file is empty (<1 KB).",
            findings=[f"Only {stats.size_bytes} bytes written. "
                      "The capture process likely exited immediately."],
            stats=stats,
        )

    if stats.duration_seconds < expected_min_seconds:
        findings.append(
            f"Recording is only {stats.duration_seconds:.2f}s "
            f"(expected at least {expected_min_seconds:.1f}s)."
        )

    # Genuinely silent = pipeline broken. We treat the SYSTEM leg more
    # leniently than the mic: sink monitors on some hardware (e.g.
    # Focusrite Scarlett) emit signal at -38 dBFS, which trips the
    # 95%-silent-windows heuristic even though real audio is being
    # captured. For system leg, we only FAIL when the absolute peak is
    # below _TRUE_SILENCE_PEAK; otherwise it's a quiet-but-real recording
    # and we fall through to the level-grading logic below.
    is_pipeline_silent = (
        stats.peak < _TRUE_SILENCE_PEAK
        if leg == "system"
        else stats.silent_ratio >= 0.95
    )
    if is_pipeline_silent:
        if leg == "system":
            cause = (
                "Likely causes: nothing was actually playing through the captured sink "
                "during the test, the meeting app was routed to a different sink, "
                "or the sink was muted/auto-suspended. "
                "Try the 'Play tone' button to verify the capture loop works at all."
            )
        elif leg == "mic":
            cause = (
                "Likely causes: wrong microphone selected, mic is muted in the OS, "
                "or you didn't speak during the test."
            )
        else:
            cause = (
                "Likely causes: wrong device selected, source muted, "
                "or no audio was playing during the test."
            )
        return AudioTestReport(
            verdict="fail",
            summary="Recording is silent.",
            findings=[
                f"Peak amplitude was only {stats.peak} out of 32767 — "
                f"capture pipeline produced essentially no signal.",
                cause,
            ],
            stats=stats,
        )

    # Loud enough to call it a real recording. Now grade quality.
    if stats.peak >= 32700:
        if leg == "mic":
            clip_advice = "Reduce mic gain or move further from the source."
        elif leg == "system":
            clip_advice = "Lower playback volume on the source app or the system mixer."
        else:
            clip_advice = "Reduce input gain."
        findings.append(
            f"Peak hit {stats.peak_pct * 100:.0f}% — input is clipping. {clip_advice}"
        )
        verdict: Verdict = "warn"
    elif stats.peak < 1500:
        if leg == "system":
            quiet_advice = (
                "Either the source app is playing very quietly, "
                "or the sink's monitor gain is low. "
                "Whisper struggles with audio this quiet — turn up playback before recording."
            )
        elif leg == "mic":
            quiet_advice = (
                "Whisper may struggle. Boost input gain or move closer to the mic."
            )
        else:
            quiet_advice = "Audio is very quiet; Whisper may struggle. Increase input gain."
        findings.append(
            f"Peak only {stats.peak_pct * 100:.0f}%. {quiet_advice}"
        )
        verdict = "warn"
    else:
        verdict = "pass"

    if stats.silent_ratio >= 0.5:
        if leg == "system":
            silence_advice = (
                "Make sure something is actually playing through the captured sink "
                "for the duration of the test."
            )
        elif leg == "mic":
            silence_advice = "Consider testing again while actually speaking."
        else:
            silence_advice = "Consider testing again while audio is playing."
        findings.append(
            f"{stats.silent_ratio * 100:.0f}% of the recording is silent. "
            f"{silence_advice}"
        )
        if verdict == "pass":
            verdict = "warn"

    summary_parts = [
        f"{stats.duration_seconds:.1f}s",
        f"peak {stats.peak_pct * 100:.0f}%",
        f"RMS {stats.rms_pct * 100:.0f}%",
        f"{stats.channels}ch @ {stats.sample_rate} Hz",
    ]
    summary = " · ".join(summary_parts)

    if verdict == "pass" and not findings:
        findings.append("Audio looks healthy. You're good to record a real meeting.")

    return AudioTestReport(
        verdict=verdict,
        summary=summary,
        findings=findings,
        stats=stats,
    )


# ---------------------------------------------------------------------------
# Playback helper (kept thin; UI layer wires this up)
# ---------------------------------------------------------------------------


def find_player() -> Optional[List[str]]:
    """Return a command prefix for playing back a WAV file, or None."""
    for tool, args in (
        ("pw-play", ["pw-play"]),
        ("paplay", ["paplay"]),
        ("aplay", ["aplay", "-q"]),
        ("ffplay", ["ffplay", "-autoexit", "-nodisp", "-loglevel", "error"]),
    ):
        if shutil.which(tool):
            return args
    return None


def play_wav(path: Path, timeout: float = 30.0, target_sink: Optional[str] = None) -> bool:
    """Play a WAV file synchronously.

    When ``target_sink`` is supplied (and the player supports it — only
    paplay/pw-play do), playback is routed to that specific sink. This is
    used by the test-tone feature so the capture loop is exercised on the
    sink the recorder is actually pointed at, not whatever the system
    default happens to be.
    """
    base = find_player()
    if base is None:
        logger.warning("No audio player found (pw-play/paplay/aplay/ffplay)")
        return False

    cmd = list(base)
    if target_sink:
        tool = cmd[0]
        if tool == "paplay":
            cmd.append(f"--device={target_sink}")
        elif tool == "pw-play":
            cmd.append(f"--target={target_sink}")
        # aplay/ffplay don't expose a Pulse/PipeWire sink override; we'll
        # just play to default. The caller is expected to know this.

    try:
        result = subprocess.run(
            [*cmd, str(path)],
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            logger.warning(
                f"Playback exited with rc={result.returncode}: "
                f"{result.stderr[-200:] if result.stderr else b''!r}"
            )
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.warning("Playback timed out")
        return False


def generate_test_tone(
    path: Path,
    seconds: float = 1.5,
    freq: float = 440.0,
    amp: float = 0.5,
    rate: int = 48000,
) -> None:
    """Write a mono s16le WAV containing a sine wave.

    Used by the audio test screen as a known signal we can route to the
    configured sink, so the user can verify the capture loop end-to-end
    without depending on a meeting/browser app being active.
    """
    import math
    import struct
    import wave

    n = int(seconds * rate)
    peak = int(max(0.0, min(amp, 1.0)) * 32767)
    samples = [
        int(peak * math.sin(2 * math.pi * freq * t / rate))
        for t in range(n)
    ]
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack(f"<{n}h", *samples))
