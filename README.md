<h1 align="center">MCP Live Stage Assistant</h1>

Live Stage Assistant is a voice-enabled AI assistant for live musicians and stage operators. It uses the Model Context Protocol (MCP) to control stage tools such as a digital mixer, QLC+ lighting, or other MCP-compatible services through spoken or typed commands.

## What It Does

- Voice input with OpenAI Whisper or local Whisper.
- Voice output with OpenAI, ElevenLabs, local pyttsx3, browser TTS, backend TTS, or silent text mode.
- Chat-style web monitor with command input, microphone controls, config, logs, sessions, cancellation, and profile management.
- Optional wake word such as `régie` or `console`.
- Optional speaker recognition with up to three WAV samples per profile.
- Online mode with OpenAI/ElevenLabs and offline mode with Ollama/local Whisper/pyttsx3.
- MCP integration for stage-control servers such as XMSeries-MCP and QLCPlus-MCP.

Technical architecture and implementation details live in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Installation

Use the automatic install script for your platform. The generic install applies to normal Linux, macOS, Windows, and Raspberry Pi. Raspberry Pi then has one extra service-pack step when you want the assistant to run as a `systemd` service.

| Target | Use this install path | Main command | Notes |
|---|---|---|---|
| Linux | Generic | `./scripts/install.sh` | The script also tries to install common audio/system packages with `apt-get`. |
| macOS | Generic | `./scripts/install.sh` | The script also tries to install PortAudio with Homebrew. |
| Windows PowerShell | Generic | `.\scripts\install.ps1` | Native Windows path; no shell script needed. |
| Windows WSL / Git Bash | Generic | `./scripts/install.sh` | Same as Linux from the shell environment. |
| Raspberry Pi | Generic + Raspberry service pack | `./scripts/install.sh`, then `raspi_service_pack_stdio/install_livestageassistant_service.sh` | The generic script installs Python dependencies; the service pack installs the system service and Pi profiles. |
| Docker / Synology | Docker | `docker compose up --build -d` | The Docker image installs Python dependencies during build. |

Only these prerequisites are expected before running the generic install:

- Python 3.11 or newer.
- `uv`, installed with `pip install uv` or `pipx install uv`.
- Node.js when you use local stdio MCP servers such as XMSeries-MCP or QLCPlus-MCP.

### 1. Generic Install

```bash
git clone https://github.com/infrafast/LiveStageAssistant.git
cd LiveStageAssistant
./scripts/install.sh
```

On Windows PowerShell:

```powershell
git clone https://github.com/infrafast/LiveStageAssistant.git
cd LiveStageAssistant
.\scripts\install.ps1
```

The install scripts create `.venv` if needed, install the runtime, install the speaker-recognition stack with CPU Torch, and prepare Ollama with `qwen3:8b` for offline mode when possible. On Linux/Raspberry Pi and macOS, `install.sh` also tries to install the common system audio packages when the host package manager is available. CUDA/NVIDIA packages are not required.

Create API key files if you use OpenAI or ElevenLabs:

```bash
printf '%s' 'your-openai-api-key' > OPENAI_API_KEY.txt
printf '%s' 'your-elevenlabs-api-key' > ELEVENLABS_API_KEY.txt
```

Then start with a bundled profile:

```bash
.venv/bin/python voice_assistant/agent.py --env-file .env.online
```

On Windows PowerShell, use `.venv\Scripts\python.exe` instead of `.venv/bin/python`.

For local/offline use, the install script tries to install Ollama and pull `qwen3:8b`. If it reports that Ollama is installed but not running, start Ollama once with `ollama serve`, then rerun the install script or pull the model manually. Start the offline profile with:

```bash
.venv/bin/python voice_assistant/agent.py --env-file .env.offline
```

### 2. Raspberry Pi

First run the generic install script as shown above. Then use the dedicated Raspberry Pi service pack when you want a boot service. It installs a `systemd` service, online/offline profiles under `/etc/livestageassistant`, and a helper command named `livestageassistant`.

```bash
cd /home/pi/LiveStageAssistant
./scripts/install.sh

cd raspi_service_pack_stdio
chmod +x install_livestageassistant_service.sh livestageassistant
./install_livestageassistant_service.sh
livestageassistant auto
```

The Raspberry Pi pack assumes these sibling folders when using local stdio MCP servers:

```text
/home/pi/LiveStageAssistant
/home/pi/XMSeries-MCP
/home/pi/QLCPlus-MCP
```

Build those MCP servers before starting the assistant if you use them locally:

```bash
cd /home/pi/XMSeries-MCP
npm ci
npm run build

cd /home/pi/QLCPlus-MCP
npm ci
npm run build
```

Read the full Raspberry Pi guide before installing on hardware: [raspi_service_pack_stdio/README.md](raspi_service_pack_stdio/README.md).

### 3. Docker / Synology

Docker does not use `scripts/install.sh` or `scripts/install.ps1` directly. The Docker image installs the Python dependencies during `docker compose up --build`.

The Docker setup keeps API keys in mounted text files and publishes the web monitor on port `8765`.

Put secrets in the mounted config folder:

```bash
printf '%s' 'your-openai-api-key' > container/config/OPENAI_API_KEY.txt
printf '%s' 'your-elevenlabs-api-key' > container/config/ELEVENLABS_API_KEY.txt
```

Edit the active Docker env file, usually:

```text
container/config/.env.infrafast
```

Then build and run:

```bash
docker compose up --build -d
docker logs -f live-stage-assistant
```

Open the web monitor:

```text
http://NAS_IP:8765
```

For Synology, Tailscale, mounted MCP servers, bridge networking, and audio passthrough details, use [docs/synology-docker.md](docs/synology-docker.md).

## Running

Common startup commands:

```bash
# Default .env if present
.venv/bin/python voice_assistant/agent.py

# Online/cloud profile
.venv/bin/python voice_assistant/agent.py --env-file .env.online

# Offline/local profile
.venv/bin/python voice_assistant/agent.py --env-file .env.offline

# Raspberry Pi service-pack profile
.venv/bin/python voice_assistant/agent.py --env-file raspi_service_pack_stdio/.env.online

# Automatic online/offline profile switching
.venv/bin/python voice_assistant/agent.py --env-file auto
```

With `--env-file auto`, the assistant uses `.env.online` when internet is reachable and `.env.offline` otherwise. Set `ASSISTANT_AUTO_ENV_DIR` to choose another directory for those two files; the Raspberry Pi service uses `/etc/livestageassistant`.

When the web monitor is enabled, startup prints a URL such as:

```text
Web monitor available at http://127.0.0.1:8765
```

## Basic Configuration

The selected `.env` file is the source of truth for runtime settings. The web UI can edit most common settings and then reload the assistant.

Important files and folders:

- `.env.online`: cloud profile.
- `.env.offline`: local/offline profile.
- `.env.example`: complete template of supported settings.
- `mcp_servers*.json`: MCP server definitions.
- `container/config/*.env*`: Docker/Synology profiles.
- `data/speaker_profiles`: local speaker-recognition samples and embeddings.
- `assets/*.wav`: selectable thinking/startup/acknowledgement sounds.

API keys should be stored in text files and referenced from env variables:

```env
OPENAI_API_KEY_FILE=OPENAI_API_KEY.txt
ELEVENLABS_API_KEY_FILE=ELEVENLABS_API_KEY.txt
```

Useful runtime settings:

```env
CONNECTIVITY_MODE=online
LLM_PROVIDER=openai
OPENAI_MODEL=gpt-4.1-mini
STT_LANGUAGE=fr
STT_INPUT=both
CLOUD_TTS_PROVIDER=openai
TTS_PROVIDER=none
WEB_TTS_PROVIDER=openai
WAKE_WORD="régie,console"
WEB_MONITOR_ENABLED=true
WEB_MONITOR_PORT=8765
MCP_CONFIG=mcp_servers.json
```

For the exhaustive env reference and runtime internals, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Using The Web Monitor

The web monitor is the primary operator UI:

- Type commands in the bottom composer.
- Use the microphone button for browser voice input when enabled.
- Use the stop button to cancel current processing or TTS.
- Use the `+` button to upload a text file into the prompt or send a WAV through the same STT path as browser audio.
- Open Settings to change model, STT/TTS, audio devices, speaker profiles, MCP routing, prompts, and env profiles.
- Use Monitor -> Console Log for technical logs without cluttering the chat bubbles.
- Use the Remote screen panel when VNC/noVNC is configured.

Browser audio device choices are stored in browser `localStorage`; backend audio device choices are saved in the active `.env` file.

## Speaker Recognition

Speaker recognition is optional. Enable it in the web config or with:

```env
SPEAKER_RECOGNITION_ENABLED=true
SPEAKER_BACKEND=resemblyzer
SPEAKER_THRESHOLD=0.75
SPEAKER_MARGIN=0.06
```

Each profile can have up to three WAV samples:

```text
profil1_1.wav
profil1_2.wav
profil1_3.wav
```

A profile becomes usable as soon as one sample embedding exists. Samples can be uploaded as WAV files or captured from the browser/backend microphone in the web UI. A good starting strategy is:

- sample 1: clean reference voice
- sample 2: clean voice with another phrase
- sample 3: live-condition voice from the microphone path used on stage

The assistant does not decide what a speaker means for the mixer or lighting. It passes speaker context to MCP servers; each MCP server owns its own mapping, such as `XMS_SPEAKER_MAP` in XMSeries-MCP.

## MCP Servers

Live Stage Assistant can connect to any MCP server listed in the selected `MCP_CONFIG`. Known compatible stage-control servers include:

- XMSeries-MCP for Behringer/X32-style mixer control: https://github.com/infrafast/XMSeries-MCP
- QLCPlus-MCP for QLC+ lighting/DMX control: https://github.com/infrafast/QLCPlus-MCP

For local stdio servers, put script paths and server-specific env values in the selected `mcp_servers*.json` file. For streamable HTTP servers, put only the HTTP endpoint in the assistant MCP config and configure the stage server itself separately.

Detailed MCP prompt loading, routing, tool limits, and RAG direction are documented in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Troubleshooting

### No Audio Input

- Check browser microphone permission for browser STT.
- Use Settings -> Audio In/Out -> backend input Test for a remote/backend microphone diagnostic.
- Install PortAudio/ALSA packages on Linux/Raspberry Pi.
- If no backend input exists, use the web text composer.

### TTS Not Audible

- Check `TTS Output` in the web config: Browser, Backend, or Silent.
- Verify API keys and provider quota.
- For browser TTS, set `TTS_PROVIDER=none` and `WEB_TTS_PROVIDER=openai` or `elevenlabs`.
- For backend TTS, verify `BACKEND_AUDIO_OUTPUT_DEVICE`, `BACKEND_TTS_VOLUME`, and host audio passthrough.
- In headless Docker, start with Browser or Silent output.

### MCP Server Not Available

- Verify Node.js and `npm run build` for local stdio MCP servers.
- Verify paths in the selected `MCP_CONFIG`.
- For Docker bridge networking, do not point HTTP MCP URLs to `127.0.0.1` unless the service is inside the same container.
- Use the web Config -> MCP Servers panel to inspect route/proxy settings.

### Offline Mode Still Calls Cloud Services

- Start with `.venv/bin/python voice_assistant/agent.py --env-file .env.offline`.
- Confirm `CONNECTIVITY_MODE=offline`, `LLM_PROVIDER=ollama`, `STT_PROVIDER=local-whisper`, `TTS_PROVIDER=pyttsx3`, and `WEB_TTS_PROVIDER=none`.
- Make sure Ollama and local Whisper model caches are available before disconnecting.

More detailed troubleshooting remains in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#troubleshooting-reference).
