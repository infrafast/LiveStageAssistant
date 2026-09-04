<h1 align="center">MCP Live Stage Assistant</h1>

Live Stage Assistant is a voice-enabled assistant for musicians and stage operators. It can control MCP-compatible stage tools such as digital mixers and QLC+ lighting from spoken or typed commands.

## Main Features

- Voice input with OpenAI Whisper or local Whisper.
- Voice output through OpenAI, ElevenLabs, local pyttsx3, browser TTS, backend TTS, or silent text mode.
- Optional wake word.
- Optional speaker recognition.
- Browser-based chat/configuration interface.
- Online mode with cloud AI services.
- Offline mode with Ollama, local Whisper and local TTS.
- MCP integration for XMSeries-MCP, QLCPlus-MCP and other compatible servers.

## Prerequisites

Install these before running the setup script:

- Python 3.11 or newer.
- `uv` (`pip install uv` or `pipx install uv`).
- Node.js when using local stdio MCP servers such as XMSeries-MCP or QLCPlus-MCP.

## Installation

Clone the repository:

```bash
git clone https://github.com/infrafast/LiveStageAssistant.git
cd LiveStageAssistant
```

### Linux / macOS / Raspberry Pi / WSL / Git Bash

```bash
./scripts/install.sh
```

### Windows PowerShell

```powershell
.\scripts\install.ps1
```

The installer creates the Python environment and installs the required dependencies. It also prepares the optional local/offline components when possible.

## API Keys

If you use OpenAI or ElevenLabs, store the keys in local text files:

```bash
printf '%s' 'your-openai-api-key' > OPENAI_API_KEY.txt
printf '%s' 'your-elevenlabs-api-key' > ELEVENLABS_API_KEY.txt
```

The selected env profile points to these files. Do not commit real API keys.

## Running Live Stage Assistant

Default automatic online/offline mode:

```bash
.venv/bin/python voice_assistant/agent.py
```

Explicit online mode:

```bash
.venv/bin/python voice_assistant/agent.py --env-file .env.online
```

Explicit offline mode:

```bash
.venv/bin/python voice_assistant/agent.py --env-file .env.offline
```

On Windows PowerShell, use `.venv\Scripts\python.exe` instead of `.venv/bin/python`.

When the web monitor is enabled, startup prints an address similar to:

```text
http://127.0.0.1:8765
```

Open that address in a browser to use the chat and configuration interface.

## Raspberry Pi Service

Run the normal installer first, then install the Raspberry Pi service pack:

```bash
cd /home/pi/LiveStageAssistant
./scripts/install.sh
cd raspi_service_pack_stdio
chmod +x install_livestageassistant_service.sh livestageassistant
./install_livestageassistant_service.sh
livestageassistant auto
```

If you use local stdio stage MCP servers, the usual layout is:

```text
/home/pi/LiveStageAssistant
/home/pi/XMSeries-MCP
/home/pi/QLCPlus-MCP
```

Build the MCP servers before starting LSA:

```bash
cd /home/pi/XMSeries-MCP
npm ci
npm run build
cd /home/pi/QLCPlus-MCP
npm ci
npm run build
```

For service-specific Raspberry Pi instructions, see `raspi_service_pack_stdio/README.md`.

## Docker / Synology

Build and start the container:

```bash
docker compose up --build -d
docker logs -f live-stage-assistant
```

Store API key files in the mounted configuration folder and use the appropriate env profile under `container/config/`.

For Synology-specific setup, networking and audio passthrough, see `docs/synology-docker.md`.

## Basic Configuration

The selected `.env` profile is the source of truth for runtime settings. Most common settings can also be edited from the web interface.

Important files:

- `.env.online`: cloud profile.
- `.env.offline`: local/offline profile.
- `.env.example`: complete configuration example.
- `mcp_servers*.json`: MCP server definitions.

Common settings include:

```env
CONNECTIVITY_MODE=online
LLM_PROVIDER=openai
OPENAI_MODEL=gpt-4.1-mini
STT_LANGUAGE=fr
STT_INPUT=both
CLOUD_TTS_PROVIDER=openai
TTS_PROVIDER=none
WEB_TTS_PROVIDER=openai
WAKE_WORD=
WEB_MONITOR_ENABLED=true
WEB_MONITOR_PORT=8765
MCP_CONFIG=mcp_servers.json
```

### Wake Word

The wake word is optional.

Disable it:

```env
WAKE_WORD=
```

Enable it, for example:

```env
WAKE_WORD=regie
```

The same setting is available in the web configuration interface.

### Speaker Recognition

Speaker recognition is optional and can be enabled from the web interface. Up to three WAV samples can be stored for each profile.

### Audio Input / Output

Use the web configuration interface to choose browser or backend microphone/speaker devices. On Raspberry Pi with several sound cards, prefer the named PipeWire devices shown by the interface when available.

## Using The Web Interface

The web monitor is the normal operator interface. It allows you to:

- type commands;
- use the browser microphone when enabled;
- stop/cancel a response;
- choose audio input/output;
- configure STT/TTS and wake word;
- configure speaker profiles;
- inspect MCP servers and routing;
- view runtime status and console logs;
- switch env profiles.

## MCP Servers

Live Stage Assistant can connect to MCP servers listed in the selected `MCP_CONFIG` file.

Common stage integrations include:

- XMSeries-MCP for Behringer/X32/XAir-style mixer control.
- QLCPlus-MCP for QLC+ lighting control.

Build local stdio MCP servers before starting LSA, and make sure their paths or HTTP endpoints are correct in the selected MCP configuration file.

## Troubleshooting

### No microphone input

- Check browser or system microphone permissions.
- Select the correct input device in the web configuration.
- On Linux/Raspberry Pi, confirm the audio packages were installed by the setup script.

### No TTS sound

- Check whether TTS Output is set to Browser, Backend or Silent.
- Verify the selected audio output device.
- Check API keys and provider quota if using cloud TTS.

### MCP server unavailable

- Verify Node.js is installed for local stdio servers.
- Run `npm ci` and `npm run build` in the MCP server directory.
- Verify the configured script path or HTTP endpoint.

### Offline mode still tries cloud services

Run explicitly with:

```bash
.venv/bin/python voice_assistant/agent.py --env-file .env.offline
```

and verify the offline profile uses Ollama, local Whisper and local TTS.

## Development And Maintenance

Technical architecture, design decisions, planned improvements and implementation roadmaps are maintained only in:

`docs/ARCHITECTURE_AND_ROADMAP.md`
