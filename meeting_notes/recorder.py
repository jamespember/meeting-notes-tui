"""Audio recording module using PipeWire/PulseAudio via pactl/pw-record/parec.

Design notes
------------
- We address devices by *name* (e.g. ``alsa_input.pci-...analog-stereo``)
  rather than by numeric index. Indexes change every time PipeWire restarts;
  names are stable across reboots.
- For system audio we record from a **source** named ``<sink-name>.monitor``,
  which both ``pw-record --target=...`` and ``parec --device=...`` accept.
  This replaces the previous code that tried to pass a sink *index* as a
  monitor target — that only worked accidentally on some systems.
- After ``Popen`` we poll briefly to detect immediate exits (bad device, busy
  device, missing tool) and surface them as a regular ``RuntimeError`` so the
  app's recording state never gets stuck "recording" with no real process.
- ``cancel_recording()`` is distinct from ``stop_recording()``: it kills the
  capture processes and deletes both temp files and the final WAV without
  invoking ffmpeg.
"""

import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Device discovery (pactl-backed; works under both PulseAudio and PipeWire)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AudioDevice:
    """A capture or playback device."""

    name: str
    description: str
    is_default: bool = False

    @property
    def display(self) -> str:
        marker = " (default)" if self.is_default else ""
        return f"{self.description}{marker}"


def _run_pactl(args: List[str], timeout: float = 2.0) -> Optional[str]:
    """Run a pactl subcommand and return stdout, or None on failure."""
    if not shutil.which("pactl"):
        logger.warning("pactl not found on PATH — device discovery disabled")
        return None
    try:
        logger.debug(f"pactl invoke: pactl {' '.join(args)}")
        result = subprocess.run(
            ["pactl", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            logger.warning(
                f"pactl {args} failed (rc={result.returncode}): {result.stderr.strip()}"
            )
            return None
        logger.debug(f"pactl {args} ok ({len(result.stdout)} bytes)")
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning(f"pactl {args} errored: {exc}")
        return None


def _get_default(kind: str) -> Optional[str]:
    """Return the default source or sink name."""
    out = _run_pactl([f"get-default-{kind}"])
    value = out.strip() if out else None
    logger.debug(f"default {kind}: {value!r}")
    return value


def _parse_pactl_list(kind: str) -> List[tuple[str, str]]:
    """Return [(name, description), ...] from `pactl list <kind>`.

    ``kind`` is "sources" or "sinks".
    """
    out = _run_pactl(["list", kind])
    if not out:
        return []

    devices: List[tuple[str, str]] = []
    current_name: Optional[str] = None
    current_desc: Optional[str] = None

    for raw_line in out.splitlines():
        line = raw_line.strip()
        if line.startswith("Name:"):
            # Flush previous block
            if current_name is not None:
                devices.append((current_name, current_desc or current_name))
            current_name = line.split(":", 1)[1].strip()
            current_desc = None
        elif line.startswith("Description:") and current_desc is None:
            current_desc = line.split(":", 1)[1].strip()
    # Flush last block
    if current_name is not None:
        devices.append((current_name, current_desc or current_name))
    return devices


def list_input_devices(include_monitors: bool = False) -> List[AudioDevice]:
    """List available microphones / capture sources.

    By default, ``.monitor`` sources are excluded — they're synthetic devices
    representing system output, not real microphones.
    """
    default = _get_default("source") or ""
    devices = []
    for name, desc in _parse_pactl_list("sources"):
        if not include_monitors and name.endswith(".monitor"):
            continue
        devices.append(AudioDevice(name=name, description=desc, is_default=(name == default)))
    logger.debug(
        f"list_input_devices(include_monitors={include_monitors}): "
        f"{len(devices)} found, default={default!r}"
    )
    return devices


def list_output_devices() -> List[AudioDevice]:
    """List available output sinks (we record from their ``.monitor``)."""
    default = _get_default("sink") or ""
    devices = []
    for name, desc in _parse_pactl_list("sinks"):
        devices.append(AudioDevice(name=name, description=desc, is_default=(name == default)))
    logger.debug(f"list_output_devices(): {len(devices)} found, default={default!r}")
    return devices


def resolve_monitor_source(sink_name: Optional[str]) -> Optional[str]:
    """Given a sink name (or None for default), return its monitor source name."""
    given = sink_name
    if not sink_name:
        sink_name = _get_default("sink")
    if not sink_name:
        logger.warning(f"resolve_monitor_source({given!r}): no sink available")
        return None
    resolved = f"{sink_name}.monitor"
    logger.debug(f"resolve_monitor_source({given!r}) -> {resolved!r}")
    return resolved


@dataclass(frozen=True)
class SinkInput:
    """An active playback stream attached to a sink."""

    index: str
    sink: str  # numeric sink index (PulseAudio uses indexes here, not names)
    application: str  # e.g. "Firefox", "WEBRTC VoiceEngine", "zoom"
    media_name: str  # e.g. "Playback", "AudioStream"
    corked: bool  # paused/idle


def _parse_pactl_sink_inputs(text: str) -> List[SinkInput]:
    """Parse the verbose output of `pactl list sink-inputs` into structured rows.

    The output is a series of "Sink Input #N" blocks with key/value pairs.
    We pull the bits we need to decide whether something is *actually
    playing* (state != IDLE/CORKED) and which sink it's attached to.
    """
    inputs: List[SinkInput] = []
    current: dict = {}

    def _flush():
        if not current:
            return
        inputs.append(SinkInput(
            index=current.get("index", ""),
            sink=current.get("sink", ""),
            application=current.get("application.name", ""),
            media_name=current.get("media.name", ""),
            corked=current.get("corked", "no").lower() in ("yes", "true"),
        ))

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("Sink Input #"):
            _flush()
            current = {"index": stripped.split("#", 1)[1].strip()}
        elif stripped.startswith("Sink:"):
            current["sink"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Corked:"):
            current["corked"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("application.name = "):
            current["application.name"] = stripped.split("=", 1)[1].strip().strip('"')
        elif stripped.startswith("media.name = "):
            current["media.name"] = stripped.split("=", 1)[1].strip().strip('"')
    _flush()
    return inputs


def list_active_sink_inputs(include_corked: bool = False) -> List[SinkInput]:
    """Return apps currently routing audio to a sink.

    By default, only un-corked (i.e. actively playing or recently active)
    streams are returned. Corked streams are paused/idle and won't produce
    audio on the sink monitor.
    """
    out = _run_pactl(["list", "sink-inputs"])
    if not out:
        logger.debug("list_active_sink_inputs: no pactl output (no sink-inputs?)")
        return []
    inputs = _parse_pactl_sink_inputs(out)
    if not include_corked:
        inputs = [si for si in inputs if not si.corked]
    if logger.isEnabledFor(10):  # DEBUG
        for si in inputs:
            logger.debug(
                f"  sink-input #{si.index}: app={si.application!r} "
                f"media={si.media_name!r} sink={si.sink!r} corked={si.corked}"
            )
    logger.debug(
        f"list_active_sink_inputs(include_corked={include_corked}): {len(inputs)} active"
    )
    return inputs


def _sink_index_to_name() -> dict[str, str]:
    """Map sink numeric index → sink name using `pactl list sinks short`."""
    out = _run_pactl(["list", "sinks", "short"])
    mapping: dict[str, str] = {}
    if not out:
        return mapping
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            mapping[parts[0].strip()] = parts[1].strip()
    return mapping


def _is_wav_effectively_silent(path: Path, threshold: int = 200) -> bool:
    """Sample a WAV file and return True if it appears to be all silence.

    Reads up to ~1 second of audio from the start, middle and end of the
    file (cheap, bounded I/O even for hour-long recordings) and returns
    True only if the peak amplitude across all sampled regions is below
    ``threshold`` (default ~-50 dBFS, well below any real noise floor).

    Used as a fast post-stop diagnostic so the app can warn the user that
    a leg of combined-mode capture produced no usable signal — without
    blocking the UI on a full file scan.
    """
    import wave

    try:
        if not path.exists() or path.stat().st_size < 1024:
            return True
        with wave.open(str(path), "rb") as wf:
            if wf.getsampwidth() != 2:
                return False  # not s16, don't claim silence we can't measure
            rate = wf.getframerate()
            n_frames = wf.getnframes()
            channels = wf.getnchannels()
            if rate == 0 or n_frames == 0:
                return True
            frames_per_probe = rate  # 1 second
            bytes_per_frame = 2 * channels

            offsets = [0]
            if n_frames > frames_per_probe * 3:
                offsets.append(max((n_frames // 2) - (frames_per_probe // 2), 0))
                offsets.append(max(n_frames - frames_per_probe, 0))

            peak = 0
            for off in offsets:
                wf.setpos(off)
                raw = wf.readframes(frames_per_probe)
                if not raw:
                    continue
                try:
                    import audioop  # type: ignore[import]

                    p = audioop.max(raw, 2)
                except Exception:
                    import struct

                    n = (len(raw) // 2) * 2
                    samples = struct.unpack(f"<{n // 2}h", raw[:n])
                    p = 0
                    for s in samples:
                        v = -s if s < 0 else s
                        if s == -32768:
                            v = 32767
                        if v > p:
                            p = v
                if p > peak:
                    peak = p
                if peak >= threshold:
                    return False
            return peak < threshold
    except Exception as exc:
        logger.debug(f"silence check on {path} failed: {exc}")
        return False


def find_busiest_sink() -> Optional[str]:
    """Return the *name* of the sink with the most active playback streams.

    "Most active" = highest count of un-corked sink-inputs. Used by the
    recorder when ``system_device`` is unset/empty: instead of blindly
    picking the system default sink (which may not be where your meeting
    app is playing), we follow the audio.

    Returns None if no sink-inputs are active.
    """
    active = list_active_sink_inputs(include_corked=False)
    if not active:
        logger.info("find_busiest_sink: no active sink-inputs found")
        return None

    counts: dict[str, int] = {}
    for si in active:
        if not si.sink:
            continue
        counts[si.sink] = counts.get(si.sink, 0) + 1
    if not counts:
        logger.warning("find_busiest_sink: active sink-inputs lack sink indexes — odd")
        return None

    busiest_index = max(counts.items(), key=lambda kv: kv[1])[0]
    name_map = _sink_index_to_name()
    name = name_map.get(busiest_index)
    logger.info(
        f"find_busiest_sink: sink #{busiest_index} ({name!r}) wins with "
        f"{counts[busiest_index]} stream(s); all counts={counts}"
    )
    return name


@dataclass(frozen=True)
class SetupHealthNote:
    """A single observation about the chosen mic/sink combination."""

    severity: str  # "info", "warn"
    message: str


def assess_setup_health(
    mic_name: Optional[str],
    system_sink_name: Optional[str],
) -> List[SetupHealthNote]:
    """Inspect the chosen mic + system-sink combination and surface advice.

    The recorder works fine with mismatched mic/output devices (e.g.
    webcam mic + Scarlett headphones — they're independent streams). But
    some combinations have practical downsides worth flagging:

    - **Webcam mics** are usually low-quality (high noise floor, narrow
      frequency response). For Whisper transcription accuracy, a USB
      headset mic or a real condenser through an interface gives much
      better results.
    - **Open speakers + webcam mic** means the webcam picks up system
      audio acoustically. The mic leg ends up containing other people's
      voices already — and the system leg captures them again from the
      monitor. The result is doubled/phased audio in the mix.
    - **HDMI/DisplayPort sink** monitors sometimes don't work reliably
      across distros; worth flagging if the user pinned one.

    We don't try to be exhaustive — just catch the common gotchas and
    nudge the user toward a better setup before they record a meeting.

    Returns a list of notes. Empty list means the setup looks healthy.
    """
    notes: List[SetupHealthNote] = []

    mic = (mic_name or "").lower()
    sink = (system_sink_name or "").lower()

    # Webcam mic detection: common substrings across Logitech, Razer,
    # generic UVC cameras, etc.
    webcam_signals = (
        "webcam",
        "usb_camera",
        "uvc",
        "viewsonic_hd",
        "c920",
        "c922",
        "c930",
        "brio",
        "kiyo",
        "facecam",
        "obsbot",
    )
    is_webcam_mic = any(s in mic for s in webcam_signals)
    if is_webcam_mic:
        notes.append(SetupHealthNote(
            severity="info",
            message=(
                "Your mic looks like a webcam mic. It works, but webcam "
                "mics typically have a high noise floor and narrow frequency "
                "response — Whisper accuracy drops on quiet or accented "
                "speech. A USB headset mic or a real interface mic will "
                "noticeably improve transcripts."
            ),
        ))

    # Built-in laptop mic: similar quality concern but less acute
    if any(s in mic for s in ("analog-stereo", "built-in", "internal")) and not is_webcam_mic:
        if "pci" in mic and "input" in mic:
            notes.append(SetupHealthNote(
                severity="info",
                message=(
                    "You're using the laptop's built-in mic. It works, "
                    "but a dedicated mic improves Whisper accuracy."
                ),
            ))

    # HDMI sink: monitor support varies wildly across distros
    if any(s in sink for s in ("hdmi", "displayport", "dp_")):
        notes.append(SetupHealthNote(
            severity="warn",
            message=(
                "You're recording from an HDMI/DisplayPort sink monitor. "
                "These often don't produce usable monitor signal under "
                "PipeWire — verify with the 'Play tone' button before "
                "trusting it for a real meeting."
            ),
        ))

    return notes


# App-name substrings that indicate the user is in (or about to start) a
# real-time meeting. We use these for two things:
#   1. Surfacing routing warnings during recording ("Zoom is on the wrong sink")
#   2. Letting the user know in the Audio Test screen whether their meeting
#      app is currently routed somewhere we'd actually capture from.
_MEETING_APP_HINTS: tuple[str, ...] = (
    "zoom",
    "meet",
    "google meet",
    "teams",
    "slack",
    "discord",
    "skype",
    "webex",
    "jitsi",
    "whereby",
    "around",
    # Browsers that often host browser-based meetings. These are noisier
    # signals (a YouTube tab also matches) — callers should treat them as
    # "could be a meeting" rather than "definitely is".
    "chromium",
    "chrome",
    "firefox",
    "brave",
)


def is_meeting_app(app_name: Optional[str]) -> bool:
    """Heuristic: does this app name look like a real-time meeting client?

    Includes browsers because browser-based Meet/Teams/Whereby are common.
    Callers needing higher precision should also check ``media.name``.
    """
    if not app_name:
        return False
    low = app_name.lower()
    return any(h in low for h in _MEETING_APP_HINTS)


def diagnose_meeting_routing(
    target_sink_name: Optional[str],
) -> List[SetupHealthNote]:
    """Compare currently-active sink-inputs against the sink we'd capture.

    Returns one note per meeting-app stream we found:
      - ``"info"`` severity when the app is correctly routed to the
        target sink (everything's fine, just letting the user know).
      - ``"warn"`` severity when the app is playing somewhere else — its
        audio will be invisible to the recorder.

    Designed for the Audio Test screen, where a user about to start a
    Zoom call should see "✓ Zoom audio will be captured" or "⚠ Zoom is
    on a different sink — your meeting will be transcribed without the
    other participants" BEFORE they hit record.
    """
    notes: List[SetupHealthNote] = []
    if not target_sink_name:
        return notes

    active = list_active_sink_inputs(include_corked=False)
    if not active:
        return notes

    idx_to_name = _sink_index_to_name()
    seen_apps: set[str] = set()
    for si in active:
        app = (si.application or "").strip()
        if not app or app in seen_apps:
            continue
        if not is_meeting_app(app):
            continue
        seen_apps.add(app)
        sink_name = idx_to_name.get(si.sink, si.sink)
        if sink_name == target_sink_name:
            notes.append(SetupHealthNote(
                severity="info",
                message=f"✓ {app} is routed to the captured sink — its audio will be in your notes.",
            ))
        else:
            notes.append(SetupHealthNote(
                severity="warn",
                message=(
                    f"⚠ {app} is playing on '{sink_name}', but we'll capture "
                    f"'{target_sink_name}'. Other participants' audio will be MISSING "
                    f"from your meeting notes. Either change the playback device "
                    f"in {app}, or unpin system_device in settings to follow the busiest sink."
                ),
            ))
    return notes


def _spawn_sink_keepawake(sink_name: str) -> Optional[subprocess.Popen]:
    """Hold the named sink awake by attaching a silent reader to its monitor.

    PipeWire auto-suspends idle sinks. A suspended sink's ``.monitor``
    source can produce zero samples, which means our recorder's monitor
    capture would stay silent even when the meeting app starts playing.

    The previous implementation wrote zeros into the sink via ``pw-cat
    --playback``. That worked to hold the sink awake, but it also fed
    those zeros into PipeWire's audio graph, which can attenuate other
    streams sharing the sink — exactly the failure mode James observed
    (system leg captured at 1% while the live meter saw 24%).

    The fix: hold the sink awake by attaching a *reader* to its monitor
    via ``parec``, discarding samples to /dev/null. This doesn't inject
    anything into the audio graph; it just registers as an interested
    monitor consumer so PipeWire keeps the sink alive. The recorder's
    own ``pw-record`` reader is independent.

    Returns the Popen handle so the caller can terminate it at stop time,
    or None if no suitable tool is available / sink isn't known.
    """
    if not sink_name:
        logger.debug("keep-awake: no sink_name provided, skipping")
        return None
    if not shutil.which("parec"):
        logger.info(
            "keep-awake: parec not on PATH — sink may auto-suspend during recording. "
            "Install pulseaudio-utils to enable."
        )
        return None
    monitor = f"{sink_name}.monitor"
    cmd = [
        "parec",
        f"--device={monitor}",
        "--rate=8000",
        "--channels=1",
        "--format=s16le",
        "--raw",
        "--latency-msec=500",
    ]
    try:
        logger.info(f"keep-awake: spawning {' '.join(cmd)} (discarding to /dev/null)")
        # We discard stdout to DEVNULL. parec will keep running forever,
        # holding the monitor active without writing anything into the
        # sink's audio graph.
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.2)
        if proc.poll() is not None:
            err = ""
            if proc.stderr is not None:
                try:
                    err = proc.stderr.read().decode("utf-8", errors="replace").strip()
                except Exception:
                    pass
            logger.warning(
                f"keep-awake: parec exited immediately (rc={proc.returncode}): "
                f"{err or '(no stderr)'}"
            )
            return None
        logger.info(f"keep-awake: parec running (pid={proc.pid}) reading {monitor}")
        return proc
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Could not start keep-awake for {sink_name}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


class AudioRecorder:
    """Audio recorder using pw-record (preferred) or parec (fallback)."""

    # How long to wait after spawn before checking the process is alive.
    # 200ms is enough for "device busy" / "command not found" style failures
    # to surface, and short enough to keep the UI snappy.
    _STARTUP_PROBE_SECONDS = 0.2

    def __init__(
        self,
        output_dir: str = "recordings",
        mode: str = "combined",
        dev_mode: bool = False,
        mic_device: Optional[str] = None,
        system_device: Optional[str] = None,
    ):
        """Initialize audio recorder.

        Args:
            output_dir: Directory to save recordings.
            mode: "mic", "system", or "combined".
            dev_mode: If True, preserve temporary files for debugging.
            mic_device: Name of the source to record mic from (None = default).
            system_device: Name of the sink whose monitor we record (None = default).
        """
        self.output_dir = Path(output_dir).expanduser()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.process: Optional[subprocess.Popen] = None
        self.current_file: Optional[Path] = None
        self.mode = mode
        self.dev_mode = dev_mode
        self.mic_device = mic_device or None
        self.system_device = system_device or None
        # Filled in at start_recording time when system_device is unset:
        # the sink we actually pointed pw-record at. Surfaced via
        # get_audio_device_info() so the user can see what we chose.
        self.resolved_system_sink: Optional[str] = None

        # Combined-mode bookkeeping
        self.mic_process: Optional[subprocess.Popen] = None
        self.system_process: Optional[subprocess.Popen] = None
        self.temp_files: List[Path] = []

        # Post-stop diagnostics, queryable by the app to surface warnings
        # to the user (e.g. "system audio leg looked silent").
        self.last_mic_silent: bool = False
        self.last_system_silent: bool = False
        self.last_temp_files: List[Path] = []

        # Keep-awake sentinel for sink-monitor capture (see _spawn_sink_keepawake)
        self._keepawake: Optional[subprocess.Popen] = None

        # Capture stderr so failures aren't silent. Bounded to avoid OOM on
        # very long recordings; pw-record/parec barely write anything once
        # they've started.
        self._stderr_files: List = []

    # ----- public API -----

    def _resolve_system_sink(self) -> Optional[str]:
        """Decide which sink to record from, and remember it.

        Resolution order:
          1. ``self.system_device`` if explicitly set (user pinned it).
          2. The sink with the most active playback streams right now.
             This is critical: meeting apps (Zoom, browser-based Meet,
             Teams) often play through a sink that is NOT the system
             default, especially when an external interface is plugged
             in. Following the audio fixes the silent-system-leg bug.
          3. The system default sink as a last resort.
        """
        if self.system_device:
            self.resolved_system_sink = self.system_device
            logger.info(f"Using pinned system sink: {self.system_device}")
            return self.system_device

        busy = find_busiest_sink()
        if busy:
            self.resolved_system_sink = busy
            active = list_active_sink_inputs(include_corked=False)
            apps = ", ".join(sorted({si.application for si in active if si.application})) or "?"
            logger.info(
                f"Auto-picked busiest sink for system audio: {busy} "
                f"(active apps: {apps})"
            )
            return busy

        default = _get_default("sink")
        self.resolved_system_sink = default
        if default:
            logger.warning(
                f"No active sink-inputs detected; falling back to default sink {default}. "
                "If your meeting app starts playback later on a different sink, "
                "the system-audio leg will capture silence."
            )
        else:
            logger.error("No default sink available — system audio capture will fail.")
        return default

    def start_recording(self, filename: Optional[str] = None) -> str:
        logger.info(
            f"=== start_recording: mode={self.mode}, mic_device={self.mic_device!r}, "
            f"system_device={self.system_device!r}, dev_mode={self.dev_mode} ==="
        )
        if self.is_recording():
            logger.error("start_recording called while already recording")
            raise RuntimeError("Already recording")

        if filename is None:
            filename = f"{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.wav"
        self.current_file = self.output_dir / filename
        logger.info(f"Recording target file: {self.current_file}")

        # Snapshot the routing state at start time so we can correlate any
        # later silent-leg diagnostics with what was actually playing.
        try:
            active = list_active_sink_inputs(include_corked=False)
            if active:
                logger.info(
                    f"Active sink-inputs at start: "
                    + ", ".join(
                        f"{si.application or '?'}→sink#{si.sink}"
                        for si in active
                    )
                )
            else:
                logger.info("No active sink-inputs at start (nothing playing)")
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"could not snapshot sink-inputs at start: {exc}")

        try:
            if self.mode == "combined":
                self._start_combined()
            elif self.mode == "system":
                sink = self._resolve_system_sink()
                self._start_single(
                    target=resolve_monitor_source(sink),
                    channels=2,
                    label="system",
                )
                self._start_keepawake(sink)
            elif self.mode == "mic":
                self._start_single(
                    target=self.mic_device,  # None => default mic
                    channels=1,
                    label="mic",
                )
            else:
                raise RuntimeError(f"Unknown recording mode: {self.mode}")
        except Exception as exc:
            logger.error(f"start_recording failed: {exc}", exc_info=True)
            # Clean up any half-spawned processes / files so the app doesn't
            # think it's recording.
            self._abort_processes()
            self.current_file = None
            raise

        logger.info(
            f"=== Recording started successfully. "
            f"Resolved sink: {self.resolved_system_sink!r}, "
            f"keep-awake: {'on' if self._keepawake else 'off'} ==="
        )
        return str(self.current_file)

    def _start_keepawake(self, sink_name: Optional[str]) -> None:
        """Spawn the keep-awake sentinel for the given sink, if applicable."""
        if not sink_name:
            logger.debug("_start_keepawake: no sink name, skipping")
            return
        self._keepawake = _spawn_sink_keepawake(sink_name)

    def _stop_keepawake(self) -> None:
        """Tear down the keep-awake sentinel if it's running."""
        if self._keepawake is None:
            return
        proc = self._keepawake
        self._keepawake = None
        logger.info(f"keep-awake: stopping pid={proc.pid}")
        try:
            proc.terminate()
            try:
                proc.wait(timeout=2)
                logger.debug(f"keep-awake: pid={proc.pid} exited cleanly")
            except subprocess.TimeoutExpired:
                logger.warning(f"keep-awake: pid={proc.pid} did not stop, killing")
                proc.kill()
                proc.wait(timeout=1)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"keep-awake stop error (ignored): {exc}")

    def stop_recording(self) -> str:
        """Stop recording and return the final file path.

        For combined mode, this also mixes the two temp WAVs with ffmpeg.
        """
        logger.info(f"Stopping audio recording (mode: {self.mode})")
        if not self.is_recording():
            raise RuntimeError("Not currently recording")

        if self.mode == "combined":
            self._stop_combined()
        else:
            self._stop_single()

        output_file = str(self.current_file) if self.current_file else ""
        self.current_file = None
        return output_file

    def cancel_recording(self) -> None:
        """Stop recording AND delete the captured file(s).

        Unlike stop_recording, this does NOT invoke ffmpeg or leave a final
        WAV on disk.
        """
        logger.info("Cancelling audio recording (discarding output)")
        final = self.current_file
        temps = list(self.temp_files)

        self._abort_processes()

        for path in [final, *temps]:
            if path and Path(path).exists():
                try:
                    Path(path).unlink()
                    logger.debug(f"Discarded {path}")
                except Exception as exc:
                    logger.warning(f"Failed to delete {path}: {exc}")

        self.current_file = None
        self.temp_files = []

    def is_recording(self) -> bool:
        if self.mode == "combined":
            return (
                (self.mic_process is not None and self.mic_process.poll() is None)
                or (self.system_process is not None and self.system_process.poll() is None)
            )
        return self.process is not None and self.process.poll() is None

    def get_recording_path(self) -> Optional[str]:
        return str(self.current_file) if self.current_file else None

    def get_audio_device_info(self) -> dict:
        """Return human-readable info about the devices being used."""
        info = {"mode": self.mode}

        sources = dict(_parse_pactl_list("sources"))
        sinks = dict(_parse_pactl_list("sinks"))

        if self.mode in ("mic", "combined"):
            mic_name = self.mic_device or _get_default("source") or ""
            info["mic_device"] = sources.get(mic_name, mic_name or "System default")

        if self.mode in ("system", "combined"):
            # Prefer the sink we actually resolved (set by start_recording);
            # fall back to the configured/default value when called before
            # recording starts (e.g. on the test screen).
            sink_name = (
                self.resolved_system_sink
                or self.system_device
                or _get_default("sink")
                or ""
            )
            label = sinks.get(sink_name, sink_name or "System default")
            suffix = " (monitor)"
            if not self.system_device and self.resolved_system_sink:
                suffix = " (monitor, auto-picked)"
            info["system_device"] = f"{label}{suffix}"

        return info

    # ----- internals -----

    def _build_capture_cmd(
        self,
        output_path: Path,
        channels: int,
        target: Optional[str],
    ) -> List[str]:
        """Build the command for a single capture process.

        Tool selection is target-dependent because of a real-world quirk
        we hit on James' setup (Focusrite Scarlett Solo, PipeWire 1.6.4):

        - ``pw-record --target=<sink>.monitor`` captures the monitor at
          a level **45 dB quieter** than ``parec --device=<sink>.monitor``
          on the same hardware, same instant, same audio. Reproduced
          deterministically: pw-record gave -52 dBFS peak, parec gave
          -7 dBFS peak.
        - For ordinary microphone sources, both tools capture at correct
          levels, but pw-record handles target= more reliably (parec on
          some setups needs --device pointing at the raw ALSA name, not
          a WirePlumber loopback filter).

        So: for ``.monitor`` sources we prefer ``parec``; for everything
        else we prefer ``pw-record``. Both fall back to the other if the
        preferred tool is missing.

        parec is always invoked with ``--file-format=wav`` so the
        resulting file is a real WAV container, not raw PCM with a
        misleading extension.
        """
        is_monitor = bool(target and target.endswith(".monitor"))

        if is_monitor:
            preferred_tools = ("parec", "pw-record")
        else:
            preferred_tools = ("pw-record", "parec")

        for tool in preferred_tools:
            if not shutil.which(tool):
                continue
            if tool == "pw-record":
                cmd = [
                    "pw-record",
                    f"--channels={channels}",
                    "--format=s16",
                    "--rate=48000",
                ]
                if target:
                    cmd.append(f"--target={target}")
                cmd.append(str(output_path))
                return cmd
            else:  # parec
                cmd = [
                    "parec",
                    f"--channels={channels}",
                    "--format=s16le",
                    "--rate=48000",
                    "--file-format=wav",
                ]
                if target:
                    cmd.append(f"--device={target}")
                cmd.append(str(output_path))
                return cmd

        raise RuntimeError(
            "No audio capture tool found. Install pipewire-pulse "
            "(provides pw-record) or pulseaudio-utils (provides parec)."
        )

    def _spawn(self, cmd: List[str], label: str) -> subprocess.Popen:
        """Spawn a capture process and verify it actually started."""
        logger.info(f"spawn[{label}]: {' '.join(cmd)}")
        # Capture stderr to a pipe so we can read it if the process dies.
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        logger.debug(f"spawn[{label}]: pid={proc.pid}")

        time.sleep(self._STARTUP_PROBE_SECONDS)
        if proc.poll() is not None:
            # Process already exited — bad device, busy, etc.
            err = ""
            if proc.stderr is not None:
                try:
                    err = proc.stderr.read().decode("utf-8", errors="replace").strip()
                except Exception:
                    pass
            logger.error(
                f"spawn[{label}]: pid={proc.pid} exited immediately "
                f"rc={proc.returncode} stderr={err!r}"
            )
            raise RuntimeError(
                f"{label} recorder exited immediately "
                f"(rc={proc.returncode}): {err or '(no stderr)'}"
            )
        logger.info(f"spawn[{label}]: pid={proc.pid} running")
        return proc

    def _start_single(self, target: Optional[str], channels: int, label: str) -> None:
        assert self.current_file is not None
        cmd = self._build_capture_cmd(self.current_file, channels, target)
        self.process = self._spawn(cmd, label)

    def _start_combined(self) -> None:
        assert self.current_file is not None
        timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        mic_file = self.output_dir / f"temp-mic-{timestamp}.wav"
        system_file = self.output_dir / f"temp-system-{timestamp}.wav"
        self.temp_files = [mic_file, system_file]
        logger.debug(f"combined: mic_file={mic_file.name}, system_file={system_file.name}")

        sink = self._resolve_system_sink()
        monitor = resolve_monitor_source(sink)
        logger.info(
            f"combined: mic_target={self.mic_device or 'default'}, "
            f"system_target={monitor or 'NONE'}"
        )

        mic_cmd = self._build_capture_cmd(mic_file, channels=1, target=self.mic_device)
        system_cmd = self._build_capture_cmd(
            system_file,
            channels=2,
            target=monitor,
        )

        self.mic_process = self._spawn(mic_cmd, "mic")
        try:
            self.system_process = self._spawn(system_cmd, "system")
        except Exception:
            # Mic spawned but system failed — kill mic so we don't leak it.
            logger.error("combined: system capture failed to start, tearing down mic")
            self._terminate(self.mic_process)
            self.mic_process = None
            raise

        # Hold the chosen sink awake so the monitor reliably produces
        # samples even when there's a lull in playback or the meeting
        # hasn't actually started yet.
        self._start_keepawake(sink)

    def _stop_single(self) -> None:
        logger.info("_stop_single: stopping capture process")
        self._terminate(self.process)
        self.process = None
        self._stop_keepawake()

    def _stop_combined(self) -> None:
        logger.info("_stop_combined: signalling mic + system capture processes")
        # Signal both first so they stop at roughly the same wall-clock
        # moment, then wait/terminate. Sequential SIGINT+wait used to let
        # one stream keep recording during the other's wait timeout.
        for label, proc in (("mic", self.mic_process), ("system", self.system_process)):
            if proc is not None and proc.poll() is None:
                try:
                    proc.send_signal(signal.SIGINT)
                    logger.debug(f"_stop_combined: SIGINT -> {label} pid={proc.pid}")
                except ProcessLookupError:
                    logger.debug(f"_stop_combined: {label} pid={proc.pid} already gone")
            elif proc is not None:
                logger.warning(
                    f"_stop_combined: {label} pid={proc.pid} had already exited "
                    f"(rc={proc.returncode}) — this leg may be truncated"
                )
        for _label, proc in (("mic", self.mic_process), ("system", self.system_process)):
            self._wait_or_kill(proc)

        self.mic_process = None
        self.system_process = None

        # Tear down the keep-awake sentinel BEFORE silence-checking, so it
        # can't keep writing zeros while we measure (it writes to the sink,
        # not the monitor, so this is belt-and-braces).
        self._stop_keepawake()

        # Reset diagnostics for this stop cycle
        self.last_mic_silent = False
        self.last_system_silent = False
        self.last_temp_files: List[Path] = []

        # Mix
        assert self.current_file is not None
        if len(self.temp_files) == 2 and all(f.exists() for f in self.temp_files):
            mic_temp, sys_temp = self.temp_files
            try:
                mic_size = mic_temp.stat().st_size
                sys_size = sys_temp.stat().st_size
                logger.info(
                    f"combined temp sizes: mic={mic_size / 1024:.0f}KB, "
                    f"system={sys_size / 1024:.0f}KB"
                )
            except Exception:
                pass

            # Quick silence check on each leg BEFORE mixing. This is the
            # diagnostic that turns "I recorded a meeting but only my voice
            # came through" from a 12-minute mystery into a clear warning.
            self.last_mic_silent = _is_wav_effectively_silent(mic_temp)
            self.last_system_silent = _is_wav_effectively_silent(sys_temp)
            logger.info(
                f"silence check: mic_silent={self.last_mic_silent}, "
                f"system_silent={self.last_system_silent}"
            )
            if self.last_mic_silent:
                logger.warning(
                    f"Mic leg ({mic_temp.name}) appears silent. "
                    "Check the configured microphone."
                )
            if self.last_system_silent:
                logger.warning(
                    f"System-audio leg ({sys_temp.name}) appears silent. "
                    f"resolved sink was {self.resolved_system_sink!r}. "
                    "Likely cause: meeting app played through a different sink, "
                    "or sink was suspended when we attached. "
                    "Use Audio Test (press 'A') and watch the routing display."
                )

            mixed = self._mix_combined(self.temp_files, self.current_file)
            # Keep temps if dev_mode, OR if either leg looked silent (so the
            # user can recover the working leg with ffmpeg manually).
            preserve = self.dev_mode or self.last_mic_silent or self.last_system_silent
            if mixed and not preserve:
                for f in self.temp_files:
                    try:
                        f.unlink()
                        logger.debug(f"deleted temp {f.name}")
                    except Exception as exc:
                        logger.warning(f"could not delete temp {f.name}: {exc}")
            elif preserve:
                self.last_temp_files = list(self.temp_files)
                if self.dev_mode:
                    logger.info(f"Dev mode: preserved temp files {self.temp_files}")
                else:
                    logger.info(
                        f"Preserved temp files due to silent leg: {self.temp_files}"
                    )
            self.temp_files = []
        else:
            logger.warning(
                "Combined recording temp files missing; cannot mix. "
                f"Existing: {[str(f) for f in self.temp_files if f.exists()]}"
            )

    def _measure_wav_peak(self, path: Path) -> int:
        """Cheap peak-amplitude measurement (0..32767) for an s16 WAV.

        Samples up to 3 one-second windows from the start, middle, and end —
        same shape as ``_is_wav_effectively_silent`` — so we don't have to
        read a multi-hundred-MB file just to compute a mix gain.
        """
        import wave

        try:
            with wave.open(str(path), "rb") as wf:
                if wf.getsampwidth() != 2:
                    return 0
                rate = wf.getframerate()
                n_frames = wf.getnframes()
                if rate == 0 or n_frames == 0:
                    return 0
                frames_per_probe = rate
                offsets = [0]
                if n_frames > frames_per_probe * 3:
                    offsets.append(max((n_frames // 2) - (frames_per_probe // 2), 0))
                    offsets.append(max(n_frames - frames_per_probe, 0))
                peak = 0
                for off in offsets:
                    wf.setpos(off)
                    raw = wf.readframes(frames_per_probe)
                    if not raw:
                        continue
                    try:
                        import audioop  # type: ignore[import]

                        p = audioop.max(raw, 2)
                    except Exception:
                        import struct
                        n = (len(raw) // 2) * 2
                        samples = struct.unpack(f"<{n // 2}h", raw[:n])
                        p = 0
                        for s in samples:
                            v = -s if s < 0 else s
                            if s == -32768:
                                v = 32767
                            if v > p:
                                p = v
                    if p > peak:
                        peak = p
                return peak
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"_measure_wav_peak({path}) failed: {exc}")
            return 0

    def _compute_mix_gains(self, inputs: List[Path]) -> tuple[float, float]:
        """Return (mic_gain, system_gain) ffmpeg should apply before amix.

        The PipeWire sink monitor on consumer interfaces (e.g. Focusrite
        Scarlett) tends to emit signal at -30 to -40 dBFS even when the
        speakers are at normal listening volume. Mic capture peaks much
        higher. A fixed 2.0× gain on each leg leaves the mix wildly
        skewed toward whoever's holding the mic.

        We target ~50% peak per leg after gain, with a safety clamp so we
        don't amplify a near-silent leg into pure noise:
          - max gain: 12× (≈ +22 dB)
          - min gain: 1×  (never attenuate; the user can do that themselves)

        Order of inputs[] is (mic, system) per _start_combined.
        """
        target = 16384  # ~50% of int16
        max_gain = 12.0
        min_gain = 1.0
        peaks = [max(self._measure_wav_peak(p), 1) for p in inputs]
        gains = []
        for peak in peaks:
            g = target / peak
            g = max(min_gain, min(max_gain, g))
            gains.append(g)
        logger.info(
            f"mix gains: mic_peak={peaks[0]} ({peaks[0] / 327.67:.1f}%) -> "
            f"gain={gains[0]:.2f}x; "
            f"system_peak={peaks[1]} ({peaks[1] / 327.67:.1f}%) -> "
            f"gain={gains[1]:.2f}x"
        )
        return gains[0], gains[1]

    def _mix_combined(self, inputs: List[Path], output: Path) -> bool:
        """Mix two WAVs using ffmpeg. Returns True on success."""
        if not shutil.which("ffmpeg"):
            logger.error("ffmpeg not found; cannot mix combined recording")
            return False

        mic_gain, sys_gain = self._compute_mix_gains(inputs)

        cmd = [
            "ffmpeg",
            "-i", str(inputs[0]),
            "-i", str(inputs[1]),
            "-filter_complex",
            f"[0:a]volume={mic_gain:.2f}[a0];[1:a]volume={sys_gain:.2f}[a1];"
            "[a0][a1]amix=inputs=2:duration=longest:normalize=0[out]",
            "-map", "[out]",
            "-ar", "48000",
            "-ac", "2",
            str(output),
            "-y",
        ]
        logger.info(f"mix: ffmpeg {' '.join(cmd[1:])}")

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=60, text=True)
            if result.returncode != 0:
                logger.error(
                    f"ffmpeg mix failed (rc={result.returncode}): "
                    f"{result.stderr[-400:].strip()}"
                )
                return False
            if not output.exists() or output.stat().st_size == 0:
                logger.error("ffmpeg reported success but output is missing/empty")
                return False
            logger.info(
                f"mix: success, output={output.name} "
                f"({output.stat().st_size / (1024 * 1024):.1f} MB)"
            )
            return True
        except subprocess.TimeoutExpired:
            logger.error("ffmpeg mix timed out")
            return False

    def _terminate(self, proc: Optional[subprocess.Popen]) -> None:
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.send_signal(signal.SIGINT)
        except ProcessLookupError:
            return
        self._wait_or_kill(proc)

    def _wait_or_kill(self, proc: Optional[subprocess.Popen]) -> None:
        if proc is None:
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            logger.warning(f"Recorder pid {proc.pid} did not exit on SIGINT, terminating")
        try:
            proc.terminate()
            proc.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            logger.warning(f"Recorder pid {proc.pid} did not respond to SIGTERM, killing")
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass

    def _abort_processes(self) -> None:
        """Kill all capture processes immediately (used by cancel/cleanup)."""
        logger.info("_abort_processes: killing all capture children")
        for label, proc in (
            ("single", self.process),
            ("mic", self.mic_process),
            ("system", self.system_process),
        ):
            if proc is not None and proc.poll() is None:
                try:
                    logger.debug(f"_abort_processes: kill {label} pid={proc.pid}")
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception as exc:
                    logger.debug(f"_abort_processes: {label} kill ignored: {exc}")
        self.process = None
        self.mic_process = None
        self.system_process = None
        self._stop_keepawake()


if __name__ == "__main__":
    import time as _t

    print("Available input devices:")
    for d in list_input_devices():
        print(f"  - {d.name}  [{d.description}]" + ("  (default)" if d.is_default else ""))

    print("\nAvailable output sinks:")
    for d in list_output_devices():
        print(f"  - {d.name}  [{d.description}]" + ("  (default)" if d.is_default else ""))

    rec = AudioRecorder()
    print("\nStarting test recording...")
    path = rec.start_recording()
    print(f"Recording to: {path}")
    _t.sleep(3)
    print("Stopping...")
    print(f"Saved: {rec.stop_recording()}")
