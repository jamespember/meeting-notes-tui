#!/bin/bash
# Setup script for local AI (Ollama)

set -e

echo "🎙️  Meeting Notes AI - Setup"
echo "=============================="
echo ""

# Check if running in virtual environment
if [ -z "$VIRTUAL_ENV" ]; then
    echo "📦 Creating virtual environment..."
    python -m venv venv
    echo "✅ Virtual environment created"
    echo ""
    echo "⚠️  Please activate the virtual environment and run this script again:"
    echo "   source venv/bin/activate"
    echo "   ./setup.sh"
    exit 0
fi

echo "✅ Virtual environment detected: $VIRTUAL_ENV"
echo ""

# Check system dependencies
echo "🔍 Checking system dependencies..."

if ! command -v pactl &> /dev/null; then
    echo "❌ pactl not found. Please install pulseaudio-utils:"
    echo "   sudo pacman -S pulseaudio-utils"
    exit 1
fi

if ! command -v ffmpeg &> /dev/null; then
    echo "❌ ffmpeg not found. Please install it:"
    echo "   sudo pacman -S ffmpeg"
    exit 1
fi

echo "✅ System dependencies OK"
echo ""

# Install Python dependencies
echo "📥 Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "✅ Setup complete!"
echo ""
echo "🚀 To run the application:"
echo "   python run.py"
echo ""
echo "📚 Read README.md for usage instructions"
echo ""
echo "⚠️  Note: First transcription will download Whisper base model (~140MB)"
