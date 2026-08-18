"""Modal "Audio Test" screen.

Goes beyond "show live meters". It actively probes the audio pipeline:

1. Lists which apps are currently routing audio to which sinks (so the
   user can see whether Zoom/Meet/Firefox is playing where they think
   it is).
2. Shows which sink the recorder *would* point at right now (auto-picked
   or pinned) and warns if it doesn't match the busy sink.
3. Runs a real 5-second capture with the current devices and, in
   combined mode, analyses the mic leg and the system-audio leg
   SEPARATELY so a silent system leg can't hide behind a healthy mic.
4. Can play a known sine-wave test tone through the resolved sink, so
   the user can verify the capture loop end-to-end without depending on
   any external app.

This is the screen that catches "I recorded a 12-minute meeting and only
heard myself" *before* the meeting starts.
"""

from __future__ import annotations

import tempfile
import threading
import time
import shutil
from pathlib import Path
from typing import List, Optional

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from meeting_notes.audio_test import (
    AudioTestReport,
    analyse_wav,
    diagnose,
    find_player,
    generate_test_tone,
    play_wav,
)
from meeting_notes.config import AppConfig
from meeting_notes.level_meter import MicLevelMeter
from meeting_notes.logger import get_logger
from meeting_notes.recorder import (
    AudioRecorder,
    assess_setup_health,
    diagnose_meeting_routing,
    find_busiest_sink,
    list_active_sink_inputs,
    resolve_monitor_source,
)

logger = get_logger(__name__)


_TEST_SECONDS = 5
_BAR_WIDTH = 30


def _bar(level: float) -> str:
    filled = int(round(level * _BAR_WIDTH))
    if level >= 0.9:
        color = "red"
    elif level >= 0.7:
        color = "yellow"
    else:
        color = "green"
    return (
        f"[{color}]{'█' * filled}[/{color}]"
        f"[dim]{'░' * (_BAR_WIDTH - filled)}[/dim]  "
        f"{int(level * 100):3d}%"
    )


def _verdict_tag(verdict: str) -> str:
    return {
        "pass": "[green]PASS[/green]",
        "warn": "[yellow]WARN[/yellow]",
        "fail": "[red]FAIL[/red]",
    }.get(verdict, verdict)


class AudioTestScreen(ModalScreen):
    """Modal screen that exercises the audio pipeline end-to-end."""

    CSS = """
    AudioTestScreen {
        align: center middle;
    }

    #test-dialog {
        width: 96%;
        max-width: 100;
        height: 96%;
        border: solid $primary;
        background: $surface;
        padding: 1 2;
        overflow-y: auto;
    }

    #test-title {
        text-align: center;
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }

    .test-section-label {
        color: $text-muted;
        margin-top: 1;
        text-style: bold;
    }

    .test-meter-bar {
        width: 100%;
    }

    #test-status {
        text-align: center;
        margin: 1 0;
        color: $text;
        text-style: bold;
    }

    #test-routing {
        color: $text-muted;
        margin: 1 0;
    }

    #test-routing-warning {
        color: $warning;
        text-style: bold;
        margin: 1 0;
    }

    #test-setup-health {
        color: $text-muted;
        margin: 1 0;
    }

    #test-summary,
    #test-summary-mic,
    #test-summary-sys {
        margin-top: 1;
        color: $text;
    }

    #test-findings {
        color: $text-muted;
        margin: 1 0;
    }

    #test-buttons {
        width: 100%;
        align: center middle;
        height: auto;
        margin-top: 1;
    }

    .test-button {
        margin: 0 1;
    }

    AudioTestScreen.compact #test-dialog { padding: 1; }
    AudioTestScreen.compact #test-buttons { layout: vertical; height: auto; }
    AudioTestScreen.compact .test-button { width: 100%; margin: 0; }
    """

    BINDINGS = [
        ("escape", "close", "Close"),
        ("space", "start_test", "Start"),
        ("t", "play_tone", "Play tone"),
        ("r", "refresh_routing", "Refresh routing"),
    ]

    countdown = reactive(0)

    def __init__(self, config: AppConfig, **kwargs):
        super().__init__(**kwargs)
        self.config = config
        self._mic_meter: Optional[MicLevelMeter] = None
        self._sys_meter: Optional[MicLevelMeter] = None
        self._last_render = {"mic": 0.0, "sys": 0.0}
        self._countdown_timer = None
        self._test_path: Optional[Path] = None
        self._mic_temp: Optional[Path] = None
        self._sys_temp: Optional[Path] = None
        self._test_running = False
        self._test_recorder: Optional[AudioRecorder] = None
        self._test_dir: Optional[Path] = None
        self._closed = False
        # Tracked so we always tear down the meter targeted at the right sink.
        self._resolved_sink: Optional[str] = None

    def compose(self) -> ComposeResult:
        with Container(id="test-dialog"):
            yield Static("󰍬  AUDIO TEST", id="test-title")

            instruction = {
                "mic": "speak into the microphone",
                "system": "play audio through the selected output",
                "combined": "speak and play audio through the selected output",
            }.get(self.config.recording_mode, "make some noise")
            yield Static(
                "[b]Space[/b] run test · [b]T[/b] play test tone · "
                f"[b]R[/b] refresh routing · [b]Esc[/b] close\n[dim]During capture: {instruction}[/dim]",
                id="test-status",
            )

            yield Static("", id="test-routing")
            yield Static("", id="test-routing-warning")
            yield Static("", id="test-setup-health")

            if self.config.recording_mode in ("mic", "combined"):
                yield Static("Microphone:", classes="test-section-label")
                yield Static("[dim]waiting…[/dim]", id="mic-meter", classes="test-meter-bar")

            if self.config.recording_mode in ("system", "combined"):
                yield Static("System audio (sink monitor):", classes="test-section-label")
                yield Static("[dim]waiting…[/dim]", id="sys-meter", classes="test-meter-bar")

            yield Static("", id="test-summary")
            yield Static("", id="test-summary-mic")
            yield Static("", id="test-summary-sys")
            yield Static("", id="test-findings")

            with Horizontal(id="test-buttons"):
                yield Button("Start test", variant="primary", id="start-button", classes="test-button")
                yield Button("Play tone", variant="default", id="tone-button", classes="test-button")
                yield Button("Play back", variant="default", id="play-button", classes="test-button", disabled=True)
                yield Button("Close", variant="default", id="close-button", classes="test-button")

    def on_mount(self) -> None:
        logger.info(
            f"AudioTestScreen mounted: mode={self.config.recording_mode}, "
            f"mic={self.config.mic_device!r}, system={self.config.system_device!r}"
        )
        self._refresh_routing_display()
        self._start_meters()

    def on_resize(self, event) -> None:
        self.set_class(event.size.width <= 60, "compact")

    def on_unmount(self) -> None:
        self._closed = True
        recorder = self._test_recorder
        self._test_recorder = None
        if recorder is not None and recorder.current_file is not None:
            try:
                recorder.cancel_recording()
            except Exception:
                logger.exception("audio-test: failed to cancel capture during close")
        self._stop_meters()
        if self._countdown_timer is not None:
            try:
                self._countdown_timer.stop()
            except Exception:
                pass
        # Best-effort cleanup of any test recording / temp files we made
        for path in (self._test_path, self._mic_temp, self._sys_temp):
            if path and path.exists():
                try:
                    path.unlink()
                except Exception:
                    pass
        if self._test_dir is not None:
            shutil.rmtree(self._test_dir, ignore_errors=True)
            self._test_dir = None

    # ----- routing display -----

    def action_refresh_routing(self) -> None:
        if self._test_running:
            return
        self._refresh_routing_display()
        # Restart meters so a newly busy sink gets monitored.
        self._stop_meters()
        self._start_meters()

    def _refresh_routing_display(self) -> None:
        """Update the 'what is playing right now' line + sink-mismatch warning."""
        logger.info("AudioTestScreen: refreshing routing display")
        active = list_active_sink_inputs(include_corked=False)
        busy = find_busiest_sink()

        # Resolve which sink the recorder would use
        if self.config.system_device:
            chosen = self.config.system_device
            chosen_reason = "pinned in settings"
        elif busy:
            chosen = busy
            chosen_reason = "auto-picked (busiest)"
        else:
            from meeting_notes.recorder import _get_default

            chosen = _get_default("sink") or ""
            chosen_reason = "system default (no apps playing yet)"

        self._resolved_sink = chosen or None

        lines = []
        if active:
            apps = []
            for si in active:
                tag = si.application or "?"
                if si.media_name:
                    tag = f"{tag} ({si.media_name})"
                apps.append(tag)
            lines.append(f"[b]Now playing:[/b] {', '.join(apps)}")
        else:
            lines.append("[b]Now playing:[/b] nothing (no active sink-inputs)")

        if chosen:
            lines.append(f"[b]Will record from:[/b] {chosen}  [dim]({chosen_reason})[/dim]")
        else:
            lines.append("[b]Will record from:[/b] [red]no sink available[/red]")

        try:
            self.query_one("#test-routing", Static).update("\n".join(lines))
        except Exception:
            pass

        # Sink-mismatch warning
        warning = ""
        if active and busy and chosen and busy != chosen:
            warning = (
                f"⚠ Audio is currently playing on [b]{busy}[/b], "
                f"but the recorder will capture [b]{chosen}[/b]. "
                "Either unpin system_device in settings (recommended), "
                "or change the system sink for your meeting app."
            )
        try:
            self.query_one("#test-routing-warning", Static).update(warning)
        except Exception:
            pass

        # Setup-health observations (webcam mic, HDMI sink, etc.)
        # This is a one-time advisory: the recorder works fine in mixed
        # mic-vs-sink setups (e.g. webcam mic + Scarlett headphones),
        # but some combinations have known quality downsides worth
        # surfacing before the user runs a real meeting.
        health = assess_setup_health(
            self.config.mic_device or None,
            chosen or None,
        )

        # Meeting-app routing diagnostics: if Zoom/Meet/Teams/etc. is
        # currently running, tell the user whether it's actually playing
        # through the sink we'd capture. This catches the "Zoom remembered
        # the laptop speakers" / "Meet went to HDMI" footguns BEFORE the
        # user starts a real recording.
        routing = diagnose_meeting_routing(chosen or None)
        all_notes = health + routing

        if all_notes:
            lines = []
            for note in all_notes:
                marker = "ℹ" if note.severity == "info" else "⚠"
                lines.append(f"{marker} {note.message}")
            health_text = "\n".join(lines)
            logger.info(
                f"setup health: {len(health)} setup note(s), "
                f"{len(routing)} routing note(s)"
            )
            for note in all_notes:
                logger.info(f"  [{note.severity}] {note.message}")
        else:
            health_text = "[green]✓ Mic + sink combination looks healthy.[/green]"
        try:
            self.query_one("#test-setup-health", Static).update(health_text)
        except Exception:
            pass

    # ----- meters -----

    def _start_meters(self) -> None:
        mic_device = self.config.mic_device or None
        # Use the freshly-resolved sink for the system meter so we monitor
        # the same place the recorder would. This matters when the user
        # hasn't pinned a sink and the busy one differs from the default.
        sys_device = resolve_monitor_source(
            self._resolved_sink or self.config.system_device or None
        )

        if self.config.recording_mode in ("mic", "combined"):
            mic = MicLevelMeter(on_level=self._on_mic_level, device=mic_device)
            if mic.is_available():
                mic.start()
                self._mic_meter = mic
            else:
                self._set_meter("mic", "[dim]no capture tool installed[/dim]")

        if self.config.recording_mode in ("system", "combined") and sys_device:
            sys = MicLevelMeter(on_level=self._on_sys_level, device=sys_device)
            if sys.is_available():
                sys.start()
                self._sys_meter = sys
            else:
                self._set_meter("sys", "[dim]no capture tool installed[/dim]")
        elif self.config.recording_mode in ("system", "combined"):
            self._set_meter("sys", "[dim]no default sink detected[/dim]")

    def _stop_meters(self) -> None:
        for meter in (self._mic_meter, self._sys_meter):
            if meter is not None:
                try:
                    meter.stop()
                except Exception:
                    pass
        self._mic_meter = None
        self._sys_meter = None

    def _on_mic_level(self, level: float) -> None:
        self._render_meter("mic", level)

    def _on_sys_level(self, level: float) -> None:
        self._render_meter("sys", level)

    def _render_meter(self, which: str, level: float) -> None:
        now = time.monotonic()
        if now - self._last_render[which] < 0.08:
            return
        self._last_render[which] = now

        text = _bar(level)
        widget_id = "mic-meter" if which == "mic" else "sys-meter"

        def _update():
            try:
                self.query_one(f"#{widget_id}", Static).update(text)
            except Exception:
                pass

        try:
            self.app.call_from_thread(_update)
        except Exception:
            pass

    def _set_meter(self, which: str, text: str) -> None:
        widget_id = "mic-meter" if which == "mic" else "sys-meter"
        try:
            self.query_one(f"#{widget_id}", Static).update(text)
        except Exception:
            pass

    # ----- buttons / actions -----

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start-button":
            self.action_start_test()
        elif event.button.id == "play-button":
            self.action_play_back()
        elif event.button.id == "tone-button":
            self.action_play_tone()
        elif event.button.id == "close-button":
            self.action_close()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_start_test(self) -> None:
        if self._test_running:
            return
        if self._test_dir is not None:
            shutil.rmtree(self._test_dir, ignore_errors=True)
            self._test_dir = None
            self._test_path = None
            self._mic_temp = None
            self._sys_temp = None
        self._test_running = True
        for bid in ("start-button", "play-button", "tone-button"):
            try:
                self.query_one(f"#{bid}", Button).disabled = True
            except Exception:
                pass
        instruction = {
            "mic": "speak into the microphone",
            "system": "play audio through the selected output",
            "combined": "speak and play audio through the selected output",
        }.get(self.config.recording_mode, "make some noise")
        self._set_status(f"Recording for {_TEST_SECONDS}s — {instruction}.")
        for sid in ("test-summary", "test-summary-mic", "test-summary-sys", "test-findings"):
            try:
                self.query_one(f"#{sid}", Static).update("")
            except Exception:
                pass
        self.countdown = _TEST_SECONDS
        self._countdown_timer = self.set_interval(1.0, self._tick_countdown)
        self._run_test_capture()

    def _tick_countdown(self) -> None:
        if self.countdown > 0:
            self.countdown -= 1
            if self.countdown > 0:
                self._set_status(f"Recording… {self.countdown}s left")

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#test-status", Static).update(text)
        except Exception:
            pass

    @work(exclusive=True, thread=True)
    def _run_test_capture(self) -> None:
        """Capture a short clip in a worker thread, then post the verdict."""
        logger.info(
            f"audio-test: starting {_TEST_SECONDS}s capture, "
            f"mode={self.config.recording_mode}"
        )
        try:
            tmpdir = Path(tempfile.mkdtemp(prefix="meeting-notes-test-"))
            if self._closed:
                shutil.rmtree(tmpdir, ignore_errors=True)
                return
            self._test_dir = tmpdir
            logger.debug(f"audio-test: tmpdir={tmpdir}")
            recorder = AudioRecorder(
                output_dir=str(tmpdir),
                mode=self.config.recording_mode,
                dev_mode=True,  # preserve temp files so we can analyse them
                mic_device=self.config.mic_device or None,
                system_device=self.config.system_device or None,
            )
            self._test_recorder = recorder
            if self._closed:
                recorder.cancel_recording()
                return

            try:
                recorder.start_recording("audio-test.wav")
                if self._closed:
                    recorder.cancel_recording()
                    return
            except Exception as exc:  # noqa: BLE001
                logger.error(f"audio-test: start failed: {exc}", exc_info=True)
                self._post_failure(f"Could not start capture: {exc}")
                return

            time.sleep(_TEST_SECONDS)

            try:
                path = Path(recorder.stop_recording())
                self._test_recorder = None
            except Exception as exc:  # noqa: BLE001
                logger.error(f"audio-test: stop failed: {exc}", exc_info=True)
                self._post_failure(f"Could not stop capture: {exc}")
                return

            logger.info(
                f"audio-test: stopped, output={path.name}, "
                f"resolved_sink={recorder.resolved_system_sink!r}, "
                f"mic_silent={recorder.last_mic_silent}, "
                f"system_silent={recorder.last_system_silent}"
            )

            mixed_stats = analyse_wav(path)
            mixed_report = diagnose(mixed_stats, expected_min_seconds=_TEST_SECONDS * 0.5)
            logger.info(
                f"audio-test: mixed verdict={mixed_report.verdict} "
                f"summary={mixed_report.summary!r}"
            )

            # Per-leg analysis for combined mode. recorder.last_temp_files
            # holds the preserved temp paths when dev_mode=True.
            mic_report = None
            sys_report = None
            mic_temp = None
            sys_temp = None
            if self.config.recording_mode == "combined":
                temps = recorder.last_temp_files or []
                logger.debug(f"audio-test: per-leg temps={[t.name for t in temps]}")
                for t in temps:
                    if "temp-mic" in t.name:
                        mic_temp = t
                    elif "temp-system" in t.name:
                        sys_temp = t
                if mic_temp is not None:
                    mic_report = diagnose(
                        analyse_wav(mic_temp),
                        expected_min_seconds=_TEST_SECONDS * 0.5,
                        leg="mic",
                    )
                    logger.info(
                        f"audio-test: mic leg verdict={mic_report.verdict} "
                        f"summary={mic_report.summary!r}"
                    )
                if sys_temp is not None:
                    sys_report = diagnose(
                        analyse_wav(sys_temp),
                        expected_min_seconds=_TEST_SECONDS * 0.5,
                        leg="system",
                    )
                    logger.info(
                        f"audio-test: system leg verdict={sys_report.verdict} "
                        f"summary={sys_report.summary!r}"
                    )

            self._post_report(
                mixed_report,
                path,
                mic_report=mic_report,
                sys_report=sys_report,
                mic_temp=mic_temp,
                sys_temp=sys_temp,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Test capture errored: {exc}", exc_info=True)
            self._post_failure(f"Unexpected error: {exc}")
        finally:
            recorder = self._test_recorder
            self._test_recorder = None
            if recorder is not None and recorder.current_file is not None:
                try:
                    recorder.cancel_recording()
                except Exception:
                    logger.exception("audio-test: failed to clean up recorder")
            if self._closed and self._test_dir is not None:
                shutil.rmtree(self._test_dir, ignore_errors=True)
                self._test_dir = None

    def _post_report(
        self,
        report: AudioTestReport,
        path: Path,
        *,
        mic_report: Optional[AudioTestReport] = None,
        sys_report: Optional[AudioTestReport] = None,
        mic_temp: Optional[Path] = None,
        sys_temp: Optional[Path] = None,
    ) -> None:
        def _apply():
            self._test_path = path
            self._mic_temp = mic_temp
            self._sys_temp = sys_temp
            self._test_running = False
            if self._countdown_timer is not None:
                try:
                    self._countdown_timer.stop()
                except Exception:
                    pass
                self._countdown_timer = None

            # In combined mode, the OVERALL verdict is the worst of the two
            # legs. Otherwise mixed audio looking healthy can hide a dead
            # system leg.
            overall = report.verdict
            findings: List[str] = list(report.findings)
            if mic_report is not None or sys_report is not None:
                for leg_name, leg in (("Mic", mic_report), ("System", sys_report)):
                    if leg is None:
                        continue
                    if leg.verdict == "fail":
                        overall = "fail"
                    elif leg.verdict == "warn" and overall == "pass":
                        overall = "warn"
                    # Prefix leg findings so they're obviously per-leg
                    for f in leg.findings:
                        findings.append(f"[{leg_name}] {f}")

            # Highlight imbalance between the two legs. This is the case
            # James hit: mic peak 15%, system peak 1% — the system leg is
            # technically present but 15× quieter than the mic. Our mix
            # auto-balances now, but the user still needs to know.
            if (
                mic_report is not None
                and sys_report is not None
                and mic_report.stats is not None
                and sys_report.stats is not None
            ):
                mic_peak = max(mic_report.stats.peak, 1)
                sys_peak = max(sys_report.stats.peak, 1)
                ratio = mic_peak / sys_peak
                if ratio >= 5.0:
                    findings.append(
                        f"Mic is ~{ratio:.0f}× louder than system audio. "
                        "The mix will auto-balance with gain, but for best "
                        "Whisper accuracy try turning up the system playback "
                        "volume before recording."
                    )
                elif ratio <= 0.2:
                    findings.append(
                        f"System audio is ~{1 / ratio:.0f}× louder than your mic. "
                        "The mix will auto-balance, but speak up or boost "
                        "mic gain so your voice isn't drowned out."
                    )

            self._set_status(f"{_verdict_tag(overall)}  {report.summary}")

            # Per-leg one-liners
            if mic_report is not None:
                try:
                    self.query_one("#test-summary-mic", Static).update(
                        f"[b]Mic leg:[/b] {_verdict_tag(mic_report.verdict)} · "
                        f"{mic_report.summary}"
                    )
                except Exception:
                    pass
            if sys_report is not None:
                try:
                    self.query_one("#test-summary-sys", Static).update(
                        f"[b]System leg:[/b] {_verdict_tag(sys_report.verdict)} · "
                        f"{sys_report.summary}"
                    )
                except Exception:
                    pass

            try:
                findings_widget = self.query_one("#test-findings", Static)
                if findings:
                    findings_widget.update("\n".join(f"• {f}" for f in findings))
                else:
                    findings_widget.update("")
            except Exception:
                pass

            for bid in ("start-button", "tone-button"):
                try:
                    self.query_one(f"#{bid}", Button).disabled = False
                except Exception:
                    pass
            try:
                play_button = self.query_one("#play-button", Button)
                play_button.disabled = (
                    find_player() is None
                    or not path.exists()
                )
            except Exception:
                pass

        try:
            self.app.call_from_thread(_apply)
        except Exception:
            pass

    def _post_failure(self, message: str) -> None:
        def _apply():
            self._test_running = False
            if self._countdown_timer is not None:
                try:
                    self._countdown_timer.stop()
                except Exception:
                    pass
                self._countdown_timer = None
            self._set_status(f"[red]FAIL[/red]  {message}")
            for bid in ("start-button", "tone-button"):
                try:
                    self.query_one(f"#{bid}", Button).disabled = False
                except Exception:
                    pass

        try:
            self.app.call_from_thread(_apply)
        except Exception:
            pass

    def action_play_back(self) -> None:
        if self._test_path is None or not self._test_path.exists():
            return
        path = self._test_path

        def _play():
            try:
                self.app.call_from_thread(self._set_status, f"Playing back {path.name}…")
            except Exception:
                pass
            ok = play_wav(path)
            try:
                if ok:
                    self.app.call_from_thread(self._set_status, "Playback complete.")
                else:
                    self.app.call_from_thread(
                        self._set_status,
                        "[red]Playback failed[/red] — install pw-play, paplay, aplay or ffplay.",
                    )
            except Exception:
                pass

        threading.Thread(target=_play, name="audio-test-playback", daemon=True).start()

    def action_play_tone(self) -> None:
        """Play a 440Hz sine wave through the resolved sink.

        This exercises the capture loop with a known, controllable signal
        so the user can verify that the system meter responds without
        needing Zoom/Meet/Firefox to be playing.
        """
        if self._test_running:
            return
        sink = self._resolved_sink
        if not sink:
            self._set_status("[red]No sink to play tone to.[/red]")
            return

        def _go():
            try:
                tmpdir = Path(tempfile.mkdtemp(prefix="meeting-notes-tone-"))
                tone_path = tmpdir / "tone.wav"
                generate_test_tone(tone_path, seconds=1.5, freq=440.0, amp=0.4)
                try:
                    self.app.call_from_thread(
                        self._set_status,
                        f"Playing test tone → {sink} … watch the system meter.",
                    )
                except Exception:
                    pass
                ok = play_wav(tone_path, target_sink=sink)
                try:
                    if ok:
                        self.app.call_from_thread(
                            self._set_status,
                            "Test tone played. If the system meter didn't move, "
                            "this sink isn't actually being captured.",
                        )
                    else:
                        self.app.call_from_thread(
                            self._set_status,
                            "[red]Tone playback failed.[/red] Install pw-play or paplay.",
                        )
                except Exception:
                    pass
                try:
                    tone_path.unlink()
                    tmpdir.rmdir()
                except Exception:
                    pass
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"test-tone failed: {exc}")

        threading.Thread(target=_go, name="audio-test-tone", daemon=True).start()
