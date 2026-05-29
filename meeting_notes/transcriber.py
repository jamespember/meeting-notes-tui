"""Transcription module using OpenAI Whisper.

Whisper auto-picks ``cuda`` when ``torch.cuda.is_available()`` reports True,
which can fail loudly on machines whose installed PyTorch wheel doesn't ship
kernels for the local GPU (the classic ``CUDA error: no kernel image is
available for execution on the device``). We default to CPU to match the
README's "CPU-based, privacy-first" promise, allow opt-in CUDA via config,
and transparently fall back to CPU if the chosen device can't actually load
the model.
"""

from pathlib import Path
from typing import Optional, Callable
from dataclasses import dataclass

from .logger import get_logger

logger = get_logger(__name__)


@dataclass
class TranscriptSegment:
    """A segment of transcribed text with timing information."""
    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    """Complete transcription result."""
    text: str
    segments: list[TranscriptSegment]
    language: str
    duration: float


_VALID_DEVICES = ("auto", "cpu", "cuda")


def _looks_like_cuda_failure(err: BaseException) -> bool:
    """Heuristic: does this exception indicate the CUDA path is unusable?"""
    msg = f"{type(err).__name__}: {err}"
    needles = (
        "no kernel image is available",
        "CUDA error",
        "CUDA driver",
        "Torch not compiled with CUDA",
        "cudaError",
        "device-side assert",
    )
    return any(n.lower() in msg.lower() for n in needles)


class WhisperTranscriber:
    """Transcribe audio files using Whisper."""

    def __init__(self, model_name: str = "base", device: str = "cpu"):
        """Initialize the transcriber.

        Args:
            model_name: Whisper model to use (tiny, base, small, medium, large)
            device: One of ``"cpu"``, ``"cuda"``, or ``"auto"``. Defaults to
                ``"cpu"`` because that matches the documented privacy-first
                CPU pipeline and avoids broken CUDA installs taking the app
                down. ``"auto"`` lets Whisper pick (CUDA when available) but
                still falls back to CPU on load failure.
        """
        if device not in _VALID_DEVICES:
            logger.warning(f"Unknown whisper device {device!r}, falling back to 'cpu'")
            device = "cpu"
        logger.info(f"Initializing WhisperTranscriber (model: {model_name}, device: {device})")
        self.model_name = model_name
        self.requested_device = device
        self.active_device: Optional[str] = None
        self.model = None  # type: ignore[assignment]

    def _resolve_device(self) -> Optional[str]:
        """Translate the requested device into something to pass to Whisper.

        Returns ``None`` for ``auto`` so Whisper does its own detection.
        """
        if self.requested_device == "auto":
            return None
        return self.requested_device

    def load_model(self):
        """Load the Whisper model (lazy loading), with CUDA-failure fallback."""
        if self.model is not None:
            return

        # Import lazily so unit tests / non-transcription code paths don't
        # need the whisper/torch wheels installed.
        import whisper  # noqa: WPS433 (intentional local import)

        target = self._resolve_device()
        try:
            logger.info(
                f"Loading Whisper {self.model_name} model "
                f"(device={target or 'auto'})..."
            )
            self.model = whisper.load_model(self.model_name, device=target)
            # whisper exposes .device on the model after load
            self.active_device = str(getattr(self.model, "device", target or "auto"))
            logger.info(f"Whisper model loaded successfully on {self.active_device}")
            return
        except Exception as exc:  # noqa: BLE001 - we want to handle anything torch throws
            if target == "cpu" or not _looks_like_cuda_failure(exc):
                logger.error(f"Whisper model load failed: {exc}", exc_info=True)
                raise

            logger.warning(
                f"Whisper failed to load on {target or 'auto'} ({exc}). "
                "Falling back to CPU."
            )
            try:
                self.model = whisper.load_model(self.model_name, device="cpu")
                self.active_device = "cpu"
                logger.info("Whisper model loaded successfully on cpu (after CUDA failure)")
            except Exception as cpu_exc:
                logger.error(f"CPU fallback also failed: {cpu_exc}", exc_info=True)
                raise

    def transcribe(
        self,
        audio_path: str,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> TranscriptResult:
        """Transcribe an audio file."""
        logger.info(f"Starting transcription: {audio_path}")
        self.load_model()

        audio_file = Path(audio_path)
        if not audio_file.exists():
            logger.error(f"Audio file not found: {audio_file}")
            raise FileNotFoundError(f"Audio file not found: {audio_file}")

        file_size_mb = audio_file.stat().st_size / (1024 * 1024)
        logger.info(f"Transcribing {audio_file.name} ({file_size_mb:.1f} MB)...")

        if self.model is None:
            logger.error("Model not loaded")
            raise RuntimeError("Model not loaded")

        # fp16 only makes sense on CUDA. Forcing fp16=False on CPU avoids
        # noisy "FP16 is not supported on CPU; using FP32 instead" warnings
        # and a small perf hit from Whisper trying anyway.
        use_fp16 = self.active_device is not None and self.active_device.startswith("cuda")

        result = self.model.transcribe(
            str(audio_file),
            language=None,
            task="transcribe",
            verbose=False,
            fp16=use_fp16,
        )

        segments = [
            TranscriptSegment(
                start=seg["start"],
                end=seg["end"],
                text=seg["text"].strip(),
            )
            for seg in result["segments"]
        ]

        duration = segments[-1].end if segments else 0.0

        logger.info(
            f"Transcription complete: {len(segments)} segments, "
            f"{duration:.1f}s duration, language: {result.get('language', 'unknown')}"
        )

        return TranscriptResult(
            text=result["text"].strip(),
            segments=segments,
            language=result.get("language", "unknown"),
            duration=duration,
        )

    def format_transcript_with_timestamps(self, result: TranscriptResult) -> str:
        """Format transcript with timestamps for each segment."""
        lines = []
        for seg in result.segments:
            timestamp = self._format_timestamp(seg.start)
            lines.append(f"**[{timestamp}]** {seg.text}")
        return "\n\n".join(lines)

    @staticmethod
    def _format_timestamp(seconds: float) -> str:
        """Format seconds as HH:MM:SS."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python transcriber.py <audio_file>")
        sys.exit(1)

    transcriber = WhisperTranscriber()
    result = transcriber.transcribe(sys.argv[1])

    print(f"\nLanguage: {result.language}")
    print(f"Duration: {result.duration:.1f}s")
    print(f"\nTranscript:\n{result.text}")
    print(f"\nWith timestamps:\n{transcriber.format_transcript_with_timestamps(result)}")
