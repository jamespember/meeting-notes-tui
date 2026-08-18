"""Utilities for managing Ollama models."""

import subprocess
import re
from typing import List, Dict, Optional, Callable, Any
from dataclasses import dataclass


@dataclass
class OllamaModel:
    """Represents an Ollama model."""
    name: str
    size: Optional[str] = None  # e.g. "2.0 GB"
    modified: Optional[str] = None  # e.g. "2 days ago"
    
    def __str__(self) -> str:
        if self.size:
            return f"{self.name} ({self.size})"
        return self.name


# Hardcoded recommended models with approximate sizes
RECOMMENDED_MODELS = [
    {
        "name": "llama3.2:1b",
        "description": "Fast, lightweight",
        "size": "1.3 GB",
        "recommended": False
    },
    {
        "name": "llama3.2:3b", 
        "description": "Recommended balance",
        "size": "2.0 GB",
        "recommended": True
    },
    {
        "name": "llama3.1:8b",
        "description": "Better quality, slower",
        "size": "4.7 GB",
        "recommended": False
    },
    {
        "name": "qwen2.5:7b",
        "description": "Alternative option",
        "size": "4.7 GB",
        "recommended": False
    }
]


def check_ollama_installed() -> bool:
    """Check if Ollama is installed and available."""
    try:
        result = subprocess.run(
            ['ollama', '--version'],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def get_installed_models() -> List[OllamaModel]:
    """
    Get list of installed Ollama models.
    
    Returns:
        List of OllamaModel objects
    
    Raises:
        RuntimeError: If ollama is not installed or command fails
    """
    try:
        result = subprocess.run(
            ['ollama', 'list'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"ollama list failed: {result.stderr}")
        
        models = []
        lines = result.stdout.strip().split('\n')
        
        # Skip header line (NAME    ID    SIZE    MODIFIED)
        for line in lines[1:]:
            if not line.strip():
                continue
            
            # Parse line: "llama3.2:3b    abc123    2.0 GB    2 days ago"
            parts = line.split()
            if len(parts) >= 1:
                name = parts[0]
                size = parts[2] + " " + parts[3] if len(parts) >= 4 else None
                modified = " ".join(parts[4:]) if len(parts) > 4 else None
                models.append(OllamaModel(name=name, size=size, modified=modified))
        
        return models
    
    except FileNotFoundError:
        raise RuntimeError("Ollama is not installed. Visit https://ollama.com to install.")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Ollama command timed out")
    except Exception as e:
        raise RuntimeError(f"Failed to get installed models: {e}")


def install_model(model_name: str, progress_callback: Optional[Callable[[str, int], None]] = None) -> bool:
    """
    Install an Ollama model.
    
    Args:
        model_name: Name of the model to install (e.g. "llama3.2:3b")
        progress_callback: Optional callback(status_message, percentage) for progress updates
        
    Returns:
        True if successful, False otherwise
    
    Raises:
        RuntimeError: If installation fails
    """
    process = None
    try:
        # Run ollama pull with streaming output
        process = subprocess.Popen(
            ['ollama', 'pull', model_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        
        current_percentage = 0
        
        # Read output line by line
        if process.stdout:
            for line in process.stdout:
                line = line.strip()
                
                if not line:
                    continue
                
                # Parse progress from output like:
                # "pulling manifest"
                # "pulling 6a0746a1ec1a... 45% ▕████▏"
                # "verifying sha256 digest"
                # "success"
                
                if progress_callback:
                    # Try to extract percentage
                    percentage_match = re.search(r'(\d+)%', line)
                    if percentage_match:
                        current_percentage = int(percentage_match.group(1))
                        progress_callback(line, current_percentage)
                    elif 'pulling manifest' in line.lower():
                        progress_callback("Downloading manifest...", 0)
                    elif 'pulling' in line.lower():
                        progress_callback("Downloading model...", current_percentage)
                    elif 'verifying' in line.lower():
                        progress_callback("Verifying download...", 95)
                    elif 'success' in line.lower():
                        progress_callback("Installation complete!", 100)
                    else:
                        progress_callback(line, current_percentage)
        
        # Wait for process to complete
        returncode = process.wait(timeout=600)  # 10 minute timeout
        
        if returncode != 0:
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(f"Model installation failed: {stderr}")
        
        return True
    
    except subprocess.TimeoutExpired:
        if process:
            process.kill()
        raise RuntimeError("Model installation timed out after 10 minutes")
    except FileNotFoundError:
        raise RuntimeError("Ollama is not installed")
    except Exception as e:
        raise RuntimeError(f"Failed to install model: {e}")


def get_recommended_models() -> List[Dict[str, Any]]:
    """Get list of recommended models with metadata."""
    return RECOMMENDED_MODELS


if __name__ == "__main__":
    # Test Ollama utilities
    print("Testing Ollama utilities...\n")
    
    if check_ollama_installed():
        print("✓ Ollama is installed")
        
        try:
            models = get_installed_models()
            print(f"\nInstalled models ({len(models)}):")
            for model in models:
                print(f"  - {model}")
        except Exception as e:
            print(f"✗ Error getting models: {e}")
    else:
        print("✗ Ollama is not installed")
    
    print(f"\nRecommended models:")
    for model in get_recommended_models():
        star = "⭐" if model["recommended"] else "  "
        print(f"  {star} {model['name']} - {model['description']} ({model['size']})")
