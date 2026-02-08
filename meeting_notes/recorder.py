"""Audio recording module using PulseAudio/PipeWire via pactl."""

import subprocess
import os
import signal
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from .logger import get_logger

logger = get_logger(__name__)


class AudioRecorder:
    """Simple audio recorder using pactl for PipeWire/PulseAudio compatibility."""
    
    def __init__(self, output_dir: str = "recordings", mode: str = "combined", dev_mode: bool = False):
        """
        Initialize audio recorder.
        
        Args:
            output_dir: Directory to save recordings
            mode: Recording mode - "mic", "system", or "combined" (default)
            dev_mode: If True, preserve temporary files for debugging (default: False)
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.process: Optional[subprocess.Popen] = None
        self.current_file: Optional[Path] = None
        self.mode = mode  # "mic", "system", or "combined" (default)
        self.dev_mode = dev_mode  # Preserve temp files for debugging
        
        # For combined mode: track background processes
        self.mic_process: Optional[subprocess.Popen] = None
        self.system_process: Optional[subprocess.Popen] = None
        self.temp_files: List[Path] = []
        
        # Cache default sink ID for system audio recording
        self._default_sink_id: Optional[str] = None
        
    def start_recording(self, filename: Optional[str] = None) -> str:
        """Start recording audio to a WAV file.
        
        Args:
            filename: Optional filename. If not provided, uses timestamp.
            
        Returns:
            Path to the output file.
        """
        logger.info(f"Starting audio recording (mode: {self.mode})")
        if self.is_recording():
            logger.error("Attempted to start recording while already recording")
            raise RuntimeError("Already recording")
        
        if filename is None:
            timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            filename = f"{timestamp}.wav"
            
        self.current_file = self.output_dir / filename
        logger.info(f"Recording to: {self.current_file}")
        
        import shutil
        
        if self.mode == "combined":
            # Record BOTH mic and system audio, then mix with ffmpeg
            logger.debug("Using combined recording mode")
            return self._start_combined_recording()
        elif self.mode == "system":
            # System audio only - let pw-record use default monitor
            logger.debug("Using system audio only mode")
            return self._start_single_recording(
                target=None,  # No target = default sink monitor
                channels=2
            )
        else:  # "mic" mode
            # Microphone only - use system default input
            logger.debug("Using microphone only mode")
            return self._start_single_recording(
                target=None,  # Use system default microphone
                channels=1
            )
    
    def _start_single_recording(self, target: Optional[str], channels: int) -> str:
        """Start recording from a single source."""
        import shutil
        
        if shutil.which("pw-record"):
            cmd = [
                "pw-record",
                f"--channels={channels}",
                "--format=s16",
                "--rate=48000"
            ]
            if target:
                cmd.insert(1, f"--target={target}")
            cmd.append(str(self.current_file))
        else:
            cmd = [
                "parec",
                f"--device={target}",
                f"--channels={channels}",
                "--format=s16le",
                "--rate=48000",
                str(self.current_file)
            ]
        
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        return str(self.current_file)
    
    def _start_combined_recording(self) -> str:
        """Start recording from BOTH mic and system audio simultaneously."""
        import shutil
        
        # Create temp files for separate recordings
        timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        mic_file = self.output_dir / f"temp-mic-{timestamp}.wav"
        system_file = self.output_dir / f"temp-system-{timestamp}.wav"
        self.temp_files = [mic_file, system_file]
        
        # Start microphone recording (use system default input)
        if shutil.which("pw-record"):
            mic_cmd = [
                "pw-record",
                # No --target flag = use system default microphone
                "--channels=1",
                "--format=s16",
                "--rate=48000",
                str(mic_file)
            ]
        else:
            mic_cmd = [
                "parec",
                # No --device flag = use system default microphone
                "--channels=1",
                "--format=s16le",
                "--rate=48000",
                str(mic_file)
            ]
        
        self.mic_process = subprocess.Popen(
            mic_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Start system audio recording
        # Get default sink ID and record from it (pw-record interprets sink as sink.monitor)
        default_sink = self._get_default_sink_id()
        if shutil.which("pw-record"):
            system_cmd = [
                "pw-record",
                f"--target={default_sink}",  # Use sink ID, pw-record uses its monitor
                "--channels=2",
                "--format=s16",
                "--rate=48000",
                str(system_file)
            ]
        else:
            # For parec, append .monitor to sink name if we have one
            parec_device = f"{default_sink}.monitor" if default_sink else ""
            parec_cmd_parts = [
                "parec",
                "--channels=2",
                "--format=s16le",
                "--rate=48000",
            ]
            if parec_device:
                parec_cmd_parts.insert(1, f"--device={parec_device}")
            parec_cmd_parts.append(str(system_file))
            system_cmd = parec_cmd_parts
        
        self.system_process = subprocess.Popen(
            system_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Small delay to ensure both started
        time.sleep(0.1)
        
        return str(self.current_file)
    
    def stop_recording(self) -> str:
        """Stop the current recording.
        
        Returns:
            Path to the recorded file.
        """
        logger.info(f"Stopping audio recording (mode: {self.mode})")
        if not self.is_recording():
            logger.error("Attempted to stop recording when not recording")
            raise RuntimeError("Not currently recording")
        
        if self.mode == "combined":
            return self._stop_combined_recording()
        else:
            return self._stop_single_recording()
    
    def _stop_single_recording(self) -> str:
        """Stop a single-source recording."""
        # Send SIGINT to cleanly stop recording
        if self.process is not None:
            try:
                logger.debug("Sending SIGINT to recording process")
                self.process.send_signal(signal.SIGINT)
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # If SIGINT didn't work, force terminate
                logger.warning("SIGINT timeout, terminating recording process")
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    # Last resort: kill
                    logger.warning("Terminate timeout, killing recording process")
                    self.process.kill()
                    self.process.wait()
        
        output_file = str(self.current_file)
        logger.info(f"Recording stopped: {output_file}")
        self.process = None
        self.current_file = None
        
        return output_file
    
    def _stop_combined_recording(self) -> str:
        """Stop combined recording and mix the audio sources."""
        # Stop both recording processes
        for proc in [self.mic_process, self.system_process]:
            if proc is not None:
                try:
                    proc.send_signal(signal.SIGINT)
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
        
        # Mix the two audio files using ffmpeg
        if len(self.temp_files) == 2 and all(f.exists() for f in self.temp_files):
            import shutil
            if shutil.which("ffmpeg"):
                # Mix with BOTH audio sources at equal volume (2.0 each) and normalize
                # This ensures both mic and system audio are clearly audible
                mix_cmd = [
                    "ffmpeg",
                    "-i", str(self.temp_files[0]),  # mic
                    "-i", str(self.temp_files[1]),  # system
                    "-filter_complex", 
                    "[0:a]volume=2.0[a0];[1:a]volume=2.0[a1];[a0][a1]amix=inputs=2:duration=longest:normalize=0[out]",
                    "-map", "[out]",
                    "-ar", "48000",
                    "-ac", "2",
                    str(self.current_file),
                    "-y"
                ]
                
                try:
                    result = subprocess.run(mix_cmd, capture_output=True, timeout=30, text=True)
                    # Log any ffmpeg errors for debugging
                    if result.returncode != 0:
                        print(f"FFmpeg mixing error: {result.stderr[-200:]}")
                except subprocess.TimeoutExpired:
                    pass
            
            # Clean up temp files unless in dev mode
            if not self.dev_mode:
                for temp_file in self.temp_files:
                    try:
                        temp_file.unlink()
                    except Exception:
                        pass  # Ignore errors during cleanup
            else:
                print(f"Dev mode: Preserved temp files {self.temp_files}")
        
        output_file = str(self.current_file)
        self.mic_process = None
        self.system_process = None
        self.temp_files = []
        self.current_file = None
        
        return output_file
    
    def is_recording(self) -> bool:
        """Check if currently recording."""
        if self.mode == "combined":
            return (self.mic_process is not None and self.mic_process.poll() is None) or \
                   (self.system_process is not None and self.system_process.poll() is None)
        else:
            return self.process is not None and self.process.poll() is None
    
    def get_recording_path(self) -> Optional[str]:
        """Get the path of the current recording, if any."""
        return str(self.current_file) if self.current_file else None
    
    def get_audio_device_info(self) -> dict[str, str]:
        """Get information about the audio devices being used for recording.
        
        Returns a dictionary with device information:
        - 'mode': Recording mode (mic, system, or combined)
        - 'mic_device': Name of microphone device (if applicable)
        - 'system_device': Name of system audio device (if applicable)
        """
        info = {'mode': self.mode}
        
        try:
            # Get default source (microphone)
            if self.mode in ["mic", "combined"]:
                result = subprocess.run(
                    ["pactl", "get-default-source"],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                if result.returncode == 0:
                    source_name = result.stdout.strip()
                    # Get human-readable description
                    result = subprocess.run(
                        ["pactl", "list", "sources"],
                        capture_output=True,
                        text=True,
                        timeout=2
                    )
                    # Parse pactl output for the device description
                    lines = result.stdout.split('\n')
                    for i, line in enumerate(lines):
                        if source_name in line:
                            # Look for Description: line nearby
                            for j in range(i, min(i + 20, len(lines))):
                                if lines[j].strip().startswith('Description:'):
                                    desc = lines[j].split('Description:', 1)[1].strip()
                                    info['mic_device'] = desc
                                    break
                            break
                    # Fallback to device name if no description found
                    if 'mic_device' not in info:
                        info['mic_device'] = source_name
                else:
                    info['mic_device'] = "System default"
            
            # Get default sink (system audio output)
            if self.mode in ["system", "combined"]:
                result = subprocess.run(
                    ["pactl", "get-default-sink"],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                if result.returncode == 0:
                    sink_name = result.stdout.strip()
                    # Get human-readable description
                    result = subprocess.run(
                        ["pactl", "list", "sinks"],
                        capture_output=True,
                        text=True,
                        timeout=2
                    )
                    # Parse pactl output for the device description
                    lines = result.stdout.split('\n')
                    for i, line in enumerate(lines):
                        if sink_name in line:
                            # Look for Description: line nearby
                            for j in range(i, min(i + 20, len(lines))):
                                if lines[j].strip().startswith('Description:'):
                                    desc = lines[j].split('Description:', 1)[1].strip()
                                    info['system_device'] = f"{desc} (monitor)"
                                    break
                            break
                    # Fallback to device name if no description found
                    if 'system_device' not in info:
                        info['system_device'] = f"{sink_name} (monitor)"
                else:
                    info['system_device'] = "System default (monitor)"
        
        except Exception as e:
            logger.debug(f"Error getting audio device info: {e}")
            # Set fallback values
            if self.mode in ["mic", "combined"]:
                info['mic_device'] = "System default"
            if self.mode in ["system", "combined"]:
                info['system_device'] = "System default (monitor)"
        
        return info
    
    def _get_default_sink_id(self) -> str:
        """Get the default sink ID for recording system audio.
        
        Returns the sink ID (e.g., "1078") which pw-record will use as a monitor source.
        Falls back to hardcoded name if detection fails.
        """
        # Use cached value if available
        if self._default_sink_id:
            return self._default_sink_id
        
        try:
            # Use pactl to get default sink name
            result = subprocess.run(
                ["pactl", "get-default-sink"],
                capture_output=True,
                text=True,
                timeout=2
            )
            
            if result.returncode == 0:
                sink_name = result.stdout.strip()
                
                # Get sink ID from name
                result = subprocess.run(
                    ["pactl", "list", "sinks", "short"],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                
                for line in result.stdout.split('\n'):
                    if sink_name in line:
                        # Line format: "ID NAME DRIVER FORMAT"
                        sink_id = line.split()[0]
                        print(f"Found default sink: {sink_name} (ID: {sink_id})")
                        self._default_sink_id = sink_id
                        return sink_id
        except Exception as e:
            print(f"Error getting default sink: {e}")
        
        # Fallback to empty string = use system default
        print("Warning: Could not detect default sink, using system default")
        return ""


if __name__ == "__main__":
    # Simple test
    import time
    
    recorder = AudioRecorder()
    print("Starting recording...")
    path = recorder.start_recording()
    print(f"Recording to: {path}")
    
    time.sleep(5)
    
    print("Stopping recording...")
    output = recorder.stop_recording()
    print(f"Saved to: {output}")
