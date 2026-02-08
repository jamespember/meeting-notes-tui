"""Transcription module using OpenAI Whisper."""

import whisper
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


class WhisperTranscriber:
    """Transcribe audio files using Whisper."""
    
    def __init__(self, model_name: str = "base"):
        """Initialize the transcriber.
        
        Args:
            model_name: Whisper model to use (tiny, base, small, medium, large)
        """
        logger.info(f"Initializing WhisperTranscriber (model: {model_name})")
        self.model_name = model_name
        self.model: Optional[whisper.Whisper] = None
        
    def load_model(self):
        """Load the Whisper model (lazy loading)."""
        if self.model is None:
            logger.info(f"Loading Whisper {self.model_name} model...")
            self.model = whisper.load_model(self.model_name)
            logger.info("Whisper model loaded successfully")
            
    def transcribe(
        self, 
        audio_path: str,
        progress_callback: Optional[Callable[[float], None]] = None
    ) -> TranscriptResult:
        """Transcribe an audio file.
        
        Args:
            audio_path: Path to the audio file
            progress_callback: Optional callback function for progress updates (0.0 to 1.0)
            
        Returns:
            TranscriptResult with text and segments
        """
        logger.info(f"Starting transcription: {audio_path}")
        self.load_model()
        
        audio_file = Path(audio_path)
        if not audio_file.exists():
            logger.error(f"Audio file not found: {audio_file}")
            raise FileNotFoundError(f"Audio file not found: {audio_file}")
        
        file_size_mb = audio_file.stat().st_size / (1024 * 1024)
        logger.info(f"Transcribing {audio_file.name} ({file_size_mb:.1f} MB)...")
        
        # Transcribe with word-level timestamps
        if self.model is None:
            logger.error("Model not loaded")
            raise RuntimeError("Model not loaded")
            
        result = self.model.transcribe(
            str(audio_file),
            language=None,  # Auto-detect
            task="transcribe",
            verbose=False
        )
        
        # Convert segments to our format
        segments = [
            TranscriptSegment(
                start=seg["start"],
                end=seg["end"],
                text=seg["text"].strip()
            )
            for seg in result["segments"]
        ]
        
        # Calculate duration from last segment
        duration = segments[-1].end if segments else 0.0
        
        logger.info(f"Transcription complete: {len(segments)} segments, {duration:.1f}s duration, language: {result.get('language', 'unknown')}")
        
        return TranscriptResult(
            text=result["text"].strip(),
            segments=segments,
            language=result.get("language", "unknown"),
            duration=duration
        )
    
    def format_transcript_with_timestamps(self, result: TranscriptResult) -> str:
        """Format transcript with timestamps for each segment.
        
        Args:
            result: TranscriptResult to format
            
        Returns:
            Formatted transcript string
        """
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
        else:
            return f"{minutes:02d}:{secs:02d}"


if __name__ == "__main__":
    # Simple test
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
