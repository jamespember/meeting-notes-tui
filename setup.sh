#!/bin/bash
# Main setup script for Meeting Notes AI

set -e

echo "======================================"
echo "  Meeting Notes AI - Setup"
echo "======================================"
echo ""

# Check if already configured
CONFIG_FILE="$HOME/.config/meeting-notes/config.yaml"
if [ -f "$CONFIG_FILE" ]; then
    echo "ℹ️  Existing configuration detected at:"
    echo "   $CONFIG_FILE"
    echo ""
    echo "⚠️  Running this setup may overwrite your current settings."
    echo ""
    echo "If you just want to change one setting, you can:"
    echo "  • Press ',' in the app to open settings"
    echo "  • Or manually edit: $CONFIG_FILE"
    echo ""
    read -p "Continue with setup anyway? (y/n): " CONTINUE_SETUP
    
    if [ "$CONTINUE_SETUP" != "y" ] && [ "$CONTINUE_SETUP" != "Y" ]; then
        echo ""
        echo "Setup cancelled. No changes made."
        exit 0
    fi
    echo ""
fi

# Check if running in virtual environment
if [ -z "$VIRTUAL_ENV" ]; then
    echo "Creating virtual environment..."
    python -m venv venv
    echo "Virtual environment created"
    echo ""
    echo "Please activate the virtual environment and run this script again:"
    echo "   source venv/bin/activate"
    echo "   ./setup.sh"
    exit 0
fi

echo "Virtual environment detected: $VIRTUAL_ENV"
echo ""

# Check system dependencies
echo "Checking system dependencies..."

if ! command -v pactl &> /dev/null; then
    echo "ERROR: pactl not found. Please install pulseaudio-utils:"
    echo "   sudo pacman -S pulseaudio-utils"
    exit 1
fi

if ! command -v ffmpeg &> /dev/null; then
    echo "ERROR: ffmpeg not found. Please install it:"
    echo "   sudo pacman -S ffmpeg"
    exit 1
fi

echo "System dependencies OK"
echo ""

# Install Python dependencies
echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "======================================"
echo "  AI Provider Setup"
echo "======================================"
echo ""
echo "Choose your AI summarization provider:"
echo ""
echo "  1) Cloud AI (Recommended)"
echo "     - Fast, high-quality summaries"
echo "     - Choose: OpenAI, Anthropic, or OpenRouter"
echo "     - Requires API key (~$0.01 per meeting)"
echo ""
echo "  2) Local AI (Ollama)"
echo "     - Free, runs on your machine"
echo "     - Requires Ollama installation"
echo "     - Slower, uses system resources"
echo ""
echo "  3) Skip AI setup (transcription only)"
echo "     - No summarization"
echo "     - Can configure later in settings"
echo ""

while true; do
    read -p "Enter choice [1-3]: " choice
    
    case $choice in
        1)
            echo ""
            echo "Running cloud AI setup..."
            echo ""
            echo "You'll choose between OpenAI, Anthropic, or OpenRouter."
            echo "Note: You'll be prompted before any existing settings are changed."
            echo ""
            ./setup_cloud.sh
            break
            ;;
        2)
            echo ""
            echo "Setting up local AI (Ollama)..."
            echo ""
            
            if ! command -v ollama &> /dev/null; then
                echo "Ollama not found. Installing..."
                curl -fsSL https://ollama.com/install.sh | sh
            else
                echo "Ollama already installed"
            fi
            
            echo ""
            echo "Pulling recommended model (llama3.2:3b)..."
            ollama pull llama3.2:3b
            
            echo ""
            echo "Local AI setup complete!"
            echo ""
            echo "Note: You can change the model in settings (press ',' in app)"
            break
            ;;
        3)
            echo ""
            echo "Skipping AI setup"
            echo ""
            echo "You can configure AI later by:"
            echo "  - Pressing ',' in the app"
            echo "  - Or running ./setup_cloud.sh for cloud AI"
            break
            ;;
        *)
            echo "Invalid choice. Please enter 1, 2, or 3."
            ;;
    esac
done

echo ""
echo "======================================"
echo "  Setup Complete!"
echo "======================================"
echo ""
echo "To run the application:"
echo "   python run.py"
echo ""
echo "Keyboard shortcuts:"
echo "   r - Start recording"
echo "   s - Stop recording"
echo "   , - Open settings"
echo "   q - Quit"
echo ""
echo "Note: First transcription will download Whisper base model (~140MB)"
echo ""
echo "For more information, see README.md"
echo ""
