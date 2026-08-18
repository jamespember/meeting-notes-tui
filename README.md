# Meeting Notes AI

A local, privacy-focused AI meeting notetaker for Linux with a keyboard-driven TUI. Record meetings, transcribe with Whisper, and generate summaries with your choice of local or cloud LLM.

## Features

- **Keyboard-driven TUI** - Lazygit-inspired interface, no mouse required
- **Audio recording** - Mic + system audio (PipeWire/PulseAudio)
- **Local transcription** - OpenAI Whisper (CPU-based, privacy-first)
- **AI summaries** - Cloud AI (OpenAI, Anthropic, OpenRouter) or local (Ollama)
- **User notes** - Write your own notes during recording to provide context to AI
- **Markdown notes** - Full transcripts with timestamps
- **Note management** - Edit titles, manage tags, search, delete
- **Settings UI** - Configure AI providers, API keys, models, paths
- **Desktop integrations** - Editor, file manager, clipboard, notifications, and Omarchy Quattro
- **Theme-aware TUI** - Uses the active Omarchy palette with responsive compact layouts
- **Recovery-first recording** - Preserves separate mic/system audio if final mixing fails

## Quick Start

### Automated Setup (Recommended)

The easiest way to get started:

```bash
# Clone the repository
git clone https://github.com/jamespember/meeting-notes.git
cd meeting-notes

# Run the setup script
./setup.sh
```

The setup script will:
1. Check system dependencies (PipeWire, Pulse compatibility, ffmpeg)
2. Create a Python virtual environment
3. Install the `meeting-notes` and `meeting-notes-status` commands
4. Configure Omarchy Quattro automatically when detected
5. Let you choose between Cloud AI, Local AI (Ollama), or no AI

On Omarchy Quattro, setup adds:

- `SUPER + M` to launch or focus Meeting Notes
- Meeting Notes to the native Apps menu
- A clickable recording/processing indicator in the Quickshell bar
- Clickable desktop notifications for important recording events

Existing Hyprland and Omarchy shell files are backed up before setup changes them.

### Manual Setup

If you prefer to set up manually:

#### 1. Install System Dependencies

```bash
# Omarchy Quattro
omarchy pkg add python ffmpeg pipewire libpulse wl-clipboard

# Arch Linux
sudo pacman -S python ffmpeg pipewire pipewire-pulse libpulse wl-clipboard

# Ubuntu / Debian
sudo apt install python3 python3-pip python3-venv ffmpeg pipewire pulseaudio-utils wl-clipboard

# Your system should already have PipeWire/PulseAudio
```

#### 2. Set Up Python Environment

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install the application and all cloud providers
pip install -e ".[all]"
```

**Note:** The first time you run transcription, Whisper will download the `base` model (~140MB).

#### 3. Set Up AI Summarization

**Option A: Cloud AI (Recommended for speed and quality)**

Run the cloud setup script:
```bash
./setup_cloud.sh
```

Or configure manually:
- Press `,` in the app → configure API key
- Supports OpenAI, Anthropic, OpenRouter
- Keys stored in `~/.config/meeting-notes/config.yaml`

**Option B: Local AI (Free, private, but slower)**

Install Ollama:
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2:3b
```

**Option C: No AI (transcription only)**
- Set `ai_provider: none` in settings
- You'll get transcripts without AI summaries

### Run the Application

```bash
# Installed by setup.sh (works immediately even before your next login)
~/.local/bin/meeting-notes

# Or with development mode (preserves temp audio files):
python run.py --dev
```

## Usage

### TUI Interface

```
┌─────────────────────────┐ ┌──────────────────────────────────┐
│ Meeting Notes           │ │ Note Preview                     │
│                         │ │                                  │
│ 2026-01-15 14:04        │ │ # Website Redesign Discussion    │
│ Website Redesign...     │ │                                  │
│ (419 words)             │ │ **Date:** January 15, 2026       │
│                         │ │                                  │
│ 2026-01-14 10:30        │ │ ## AI Summary                    │
│ Sprint Planning...      │ │                                  │
│ (523 words)             │ │ The meeting discussed...         │
│                         │ │                                  │
└─────────────────────────┘ └──────────────────────────────────┘
 r Record  o Open  e Edit  t Transcript  T Tags  d Delete  , Settings
```

### Recording View

```
┌────────────────────────────────────────────────────────────────────────────┐
│                                                                            │
│  ┌──────────────────────────────┐  ┌─────────────────────────────────┐   │
│  │                              │  │ Meeting Title (optional):       │   │
│  │      🔴  RECORDING           │  │ ┌─────────────────────────────┐ │   │
│  │                              │  │ │ Weekly Team Standup_        │ │   │
│  │                              │  │ └─────────────────────────────┘ │   │
│  │         05:42                │  │                                 │   │
│  │                              │  │ Your Notes:                     │   │
│  │                              │  │ ┌─────────────────────────────┐ │   │
│  │  ┌────────────────────────┐  │  │ │ Discussing Q1 planning      │ │   │
│  │  │ 🎤🔊 Microphone +      │  │  │ │ Need to follow up with      │ │   │
│  │  │     System Audio       │  │  │ │ Sarah about budget_         │ │   │
│  │  │                        │  │  │ │                             │ │   │
│  │  │ Mic: Scarlett Solo     │  │  │ │                             │ │   │
│  │  │ (3rd Gen.) Input 1     │  │  │ │                             │ │   │
│  │  │                        │  │  │ │                             │ │   │
│  │  │ System: Scarlett Solo  │  │  │ │                             │ │   │
│  │  │ (3rd Gen.) Headphones  │  │  │ │                             │ │   │
│  │  │ / Line 1-2 (monitor)   │  │  │ │                             │ │   │
│  │  └────────────────────────┘  │  │ └─────────────────────────────┘ │   │
│  │                              │  │                                 │   │
│  └──────────────────────────────┘  └─────────────────────────────────┘   │
│                                                                            │
│  Press 's' to stop and process recording                                  │
│  Press 'x' to cancel and discard recording                                │
│  Press 'Esc' to unfocus title input                                       │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
 s Stop  x Cancel  q Quit
```

**User Notes:** While recording, you can write your own notes in the text area. These notes:
- Provide additional context to the AI when generating summaries
- Are saved in a dedicated "User Notes" section in the final markdown file
- Support markdown formatting
- Are completely optional

### Keyboard Shortcuts

**Main View:**
- `r` - Start recording
- `o` - Open in editor
- `e` - Edit title
- `t` - View transcript
- `T` - Manage tags
- `d` - Delete
- `c` - Copy content
- `p` - Copy path
- `f` - Show in file manager
- `,` - Settings
- `A` - Audio Test (verify mic + system audio are working)
- `q` - Quit
- `↑↓` or `j/k` - Navigate list
- `/` - Focus search
- `1` / `2` - Jump between Meetings and Note panes
- `Esc` - Clear search and return to the meeting list

**Recording:**
- `s` - Stop and process
- `x` - Cancel (requires confirmation)

Recordings are never deleted automatically. If mixing or processing fails,
the app retains available audio in the recordings directory for recovery.

### Settings

Press `,` to configure:
- AI provider (OpenAI, Anthropic, OpenRouter, Ollama, none)
- API keys
- Whisper model (tiny/base/small/medium/large)
- Recording mode (mic/system/combined)
- Directories and editor

## Output Format

Notes are saved as markdown files in `notes/`:

```markdown
---
title: "Website Redesign Discussion"
date: 2026-01-15
time: "14:04"
duration_seconds: 179
word_count: 419
tags: [meeting, auto-generated, ai-summary]
---

# Website Redesign Discussion

**Date:** January 15, 2026 at 2:04 PM  
**Duration:** 2 minutes, 59 seconds  
**Words:** 419

## User Notes

Discussing Q1 planning
Need to follow up with Sarah about budget

## AI Summary

The meeting discussed the updates and changes to be made on the content 
side of a website, focusing on layout, design, and functionality. The 
conversation centered around visualizing the proposed changes and finalizing 
the details for implementation. Key stakeholders were engaged in the discussion.

### Key Points

- Review website layout and design changes
- Update badge display (G2, SOC2, ISO)
- Modify three-column layout for different engines
- Replace SN Genome with developer code section

### Action Items

- Review and finalize the updated website design and layout
- Create assets for implementation

### Decisions Made

- Retain white section for platform features and SEO
- Remove certain sections from homepage
- Keep customer testimonials with updated copy

### Participants

Charlie, [other participants]

## Full Transcript

**[00:00]** but the actual changes or the full updates are on the 
content side of things.

**[00:06]** If I actually share with you just to help you kind of 
visualize that...

**[00:12]** with what we look like, I'll share my screen right now...
```

## Omarchy Quattro Integration

Omarchy 4 replaced Waybar and legacy Hyprland `.conf` overrides with its
Quickshell desktop and Lua configuration. `./setup.sh` detects Quattro and
runs `integrations/omarchy/install.sh` automatically. The installer is
idempotent and can also be rerun directly after changing your bar layout.

The integration uses only supported Quattro surfaces:

- `o.bind` in `~/.config/hypr/bindings.lua`
- a standard `.desktop` entry discovered by the native Apps menu
- a Quickshell `type: "command"` bar module in `~/.config/omarchy/shell.json`
- `omarchy-notification-send` for clickable notification history
- `omarchy-launch-or-focus-tui` so repeated launches focus the existing window

The bar shows:

- `󰗠` when the app is ready or not running
- `󰦕 05:42` while recording
- `󰄬` while transcribing and generating the note

Status is atomic JSON stored privately under `$XDG_RUNTIME_DIR`; it is not
kept in the repository and is never evaluated as shell code. Click the bar
indicator or a Meeting Notes notification to launch or focus the TUI.

To remove the bar entry, delete the object with `"id": "meeting-notes"` from
`~/.config/omarchy/shell.json`, then run:

```bash
omarchy restart shell
```

For non-Omarchy Hyprland installations, launch `meeting-notes` from your
preferred terminal and define compositor bindings using that installation's
current configuration format.

### Notifications

Desktop notifications are sent only for important lifecycle events: recording
start/cancel, processing, completion, failures, silent audio, and meeting-app
audio routed to the wrong output. Notification text deliberately excludes
meeting titles, transcripts, device names, paths, API errors, and credentials
because Quattro retains recent notifications in its history.

## Audio Configuration

Press `,` → **Audio** to configure all of this from the TUI.

**Recording modes:**
- `combined` - Mic + System (default, best for meetings)
- `mic` - Microphone only
- `system` - System audio only (records the default sink's monitor)

**Mic / System device pickers:**
The Audio settings page lists every PipeWire/PulseAudio source and sink
detected via `pactl`. Pick a specific one (e.g. *Scarlett Solo*) or leave
both on **System default** to follow whatever your OS is currently using.
Devices are stored by name (not numeric index), so they survive reboots.

**Whisper compute device:**
- `cpu` (default) — safe everywhere; matches the privacy-first claim above.
- `cuda` — uses your GPU; requires a working `torch` + CUDA install. If
  loading fails (e.g. *"no kernel image is available for execution on the
  device"*), the app automatically falls back to CPU and logs a warning.
- `auto` — let `torch` pick.

**Live mic level meter:**
While recording, the recording view shows a real-time peak meter for the
mic so you can confirm audio is actually arriving (green → yellow → red).
This runs as a separate `parec`/`pw-record` reader and doesn't interfere
with the main capture.

**Audio Test mode** (press `A` from the main view, or **Run Audio Test**
from settings → Audio):

- Shows live peak meters for both the mic and the system sink monitor
  simultaneously, so you can verify each side is wired up correctly.
- Records a 5-second clip with your *current* device choices (including
  unsaved changes in the settings screen) and then issues a verdict:
  - **PASS** — clip is the right length, levels are healthy, not clipping.
  - **WARN** — captured audio but it's too quiet, clipping, or mostly silent.
  - **FAIL** — file is missing/empty/silent; pipeline is broken.
- Lets you play the clip back through `pw-play`/`paplay`/`aplay`/`ffplay`
  so you can listen to what Whisper would actually see.
- Cleans up after itself: test recordings live in `/tmp` and are deleted
  when you close the screen.

### Does this work for Google Meet, Zoom, Teams, etc?

Yes. The system-audio capture path is app-agnostic — it records whatever's
playing through the configured sink. As long as your meeting app (browser
Meet, native Zoom, Teams, Slack huddle, Discord, etc.) plays through the
same sink the recorder is pointed at, the other participants' voices end
up in the transcript.

Three traps to watch for:

1. **Native apps remember per-app sinks.** Zoom, Teams etc. often
   remember the last "Speaker" you picked in their own settings. If you
   ever picked something other than "Same as System" / "System Default",
   Zoom may play out of laptop speakers while the recorder captures the
   Scarlett (or whatever).
2. **PipeWire `stream-restore` remembers per-app sinks.** Even browser
   tabs can stick to a sink they used last week.
3. **Echo-cancel virtual sources.** Some setups create an
   `echo-cancel-source` device. This only affects what your meeting
   partners hear from your mic; it doesn't affect what we capture for
   the transcript.

The Audio Test screen (press `A` from the main view) now actively
diagnoses all three. When a meeting app is running, it shows:

- `✓ Zoom is routed to the captured sink — its audio will be in your notes.`
- `⚠ Zoom is playing on 'alsa_output.builtin', but we'll capture 'alsa_output.scarlett'. Other participants' audio will be MISSING from your meeting notes.`

And during a real recording, if a meeting app opens a new stream on a
sink we're not capturing, you'll get both an in-app warning and a Quattro
desktop notification immediately.

### Why does the recorder use different tools for mic vs system audio?

`pw-record` (the PipeWire-native capture tool) handles microphone sources
correctly, but on at least one common setup — Focusrite Scarlett Solo
under PipeWire 1.6.4 — it captures sink monitors at roughly **40 dB lower
amplitude** than expected. `parec` (the PulseAudio compatibility tool)
reads the same monitor source at full level on the same hardware in the
same instant.

So the recorder uses:

- `parec` for any target ending in `.monitor` (system audio capture)
- `pw-record` for microphone sources

Both fall back to the other if the preferred tool is missing. This split
keeps mic capture on the simpler PipeWire-native path while routing
monitor capture through the compatibility shim that gets the volume
right.

If you discover your setup is the opposite (parec is quiet on monitors,
pw-record is loud), please open an issue with output from
`pactl list sinks | grep -A2 -E 'Name|Volume'` so we can revisit.

## Roadmap

### Planned Features

- Advanced filtering UI (by date, tags, keywords)
- Export to PDF/DOCX formats
- Google Calendar integration (OAuth, auto-fetch meetings)
- Real-time transcription during recording

## License

MIT License - See LICENSE file for details

## Contributing

This is a personal project but suggestions and contributions are welcome!

1. Open an issue for bugs or feature requests
2. Check the roadmap above for planned features
3. Submit PRs with clear descriptions

### Running tests

```bash
# Lightweight tests (matches CI — no whisper/torch needed)
pip install pytest pytest-asyncio ruff textual openai anthropic openrouter pyyaml
pytest tests/test_config.py tests/test_paths_and_fallbacks.py \
       tests/test_summarizers.py tests/test_textual_smoke.py
ruff check meeting_notes/ tests/

# Full suite (also runs Textual headless smoke tests; needs the full env)
pip install -e ".[all,dev]"
pytest
```

CI (`.github/workflows/ci.yml`) runs the lightweight subset on every PR.
