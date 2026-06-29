<h1 align="center"> MCP Live Stage Assistant </h1>

This is a voice-enabled AI personal assistant that leverages the Model Context Protocol (MCP) to integrate multiple tools and services through natural voice interactions.
It is more specifically design for assisting live musician that gives commands to drive a digital mixer, a DMX console or other on stage equipment.

For developpers: https://deepwiki.com/infrafast/LiveStageAssistant

## Features

- 🎤 **Voice Input**: Real-time speech-to-text using OpenAI Whisper API or local Whisper
- 🔊 **Voice Output**: High-quality text-to-speech using OpenAI, ElevenLabs, pyttsx3, or no spoken output
- 🤖 **AI-Powered**: Conversational AI with memory persistence
- 🌐 **Multiple Model Providers**: Works with OpenAI or local Ollama models that support tool calling
- 🛠️ **Multi-Tool Integration**: Seamlessly connects to any MCP servers:
- 🧭 **MCP-provided Startup Instructions**: Optionally loads system instructions from MCP prompts, resources, or one configured fallback tool
- 🖥️ **Local Web Monitor**: Chat-style command UI, persisted sessions, runtime state, active config, console logs, final prompt, request cancellation, and manual command injection
- 💾 **Conversational Memory**: Maintains in-memory MCPAgent context plus persisted web chat session summaries
- 🗣️ **Optional Wake Word**: Gate spoken commands with a global wake word after STT transcription
- 🎯 **Extensible**: Easy to add new MCP servers and capabilities
- 📴 **Offline Mode**: Can run with Ollama, local Whisper, pyttsx3, and local MCP servers after models/packages are installed

## Architecture

```
                    ┌──────────────────────────────────────┐
                    │        Live Stage Assistant          │
                    │        backend Python agent          │
                    └──────────────────────────────────────┘
                                      ▲
                                      │
       ┌──────────────────────────────┴──────────────────────────────┐
       │                                                             │
┌──────┴────────┐                                           ┌────────┴───────┐
│ Backend local │                                           │ Remote web UI  │
│ control       │                                           │ browser client │
├───────────────┤                                           ├────────────────┤
│ • local mic   │                                           │ • text command │
│ • local TTS   │                                           │ • browser mic  │
│ • terminal    │                                           │ • browser TTS  │
└──────┬────────┘                                           └────────┬───────┘
       │                                                             │
       │                           HTTP/web monitor                  │
       └──────────────────────────────┬──────────────────────────────┘
                                      │
                                      ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────────┐
│ Audio input  │ --> │ Silero VAD   │ --> │ Speech-to-   │ --> │  LLM with   │ --> │ Text-to-     │
│ backend/web  │     │ local ONNX   │     │ Text (STT)   │     │  MCPAgent   │     │ Speech (TTS) │
└──────────────┘     └──────────────┘     └──────────────┘     └──────┬──────┘     └──────────────┘
┌──────────────┐                                                      │               Browser,
│ Text command │ -----------------------------------------------------┘               OpenAI,
└──────────────┘                         Whisper API or local     OpenAI or local     ElevenLabs,
                                                                    (Ollama)          or pyttsx3
                                          ┌───────▼───────┐
                                          │ MCP Servers   │
                                          ├───────────────┤
                                          │ • Linear      │
                                          │ • Playwright  │
                                          │ • Filesystem  │
                                          │ • XMSeries-MCP│
                                          │ • QLCPlus-MCP │
                                          └───────────────┘
```

## Runtime Modes

Live Stage Assistant now has three complementary operating paths:

- **Backend embedded audio**: local microphone capture and backend TTS are driven by `STT_PROVIDER`, `TTS_PROVIDER`, `BACKEND_AUDIO_INPUT_DEVICE`, and `BACKEND_AUDIO_OUTPUT_DEVICE`.
- **Web text/chat**: always available when the web monitor is enabled; the browser sends text commands, can cancel active work, and shows state, logs, sessions, config, and final prompt.
- **Web audio**: browser microphone and browser TTS are proxied through the backend when `STT_INPUT` or `WEB_TTS_PROVIDER` needs them, so API keys stay server-side.

The Python backend remains the MCP/LLM control plane in every mode. Browser controls queue commands and cancellation requests; the agent owns wake-word handling, MCP tool calls, runtime reloads, and final responses.

## Installation

### Prerequisites

1. **Python 3.11+**
2. **uv** (Python package manager): `pip install uv` or `pipx install uv`
3. **Node.js** (for MCP servers)
4. **System dependencies**:
   - macOS: `brew install portaudio`
   - Ubuntu/Debian/Raspberry Pi OS: `sudo apt-get install portaudio19-dev alsa-utils ffmpeg espeak espeak-ng libespeak1 libespeak-ng1`
   - Windows: PyAudio wheel includes PortAudio
5. **Ollama** (optional, required for offline LLM mode)


### Install from Source

```bash
# Clone the repository
git clone https://github.com/yourusername/mcp-voice-assistant.git
cd mcp-voice-assistant

# Create a virtual environment with uv
uv venv

# Activate the virtual environment
# On Linux/macOS:
source .venv/bin/activate
# On Windows:
# .venv\Scripts\activate

# Install in development mode, including non-CUDA speaker recognition
./scripts/install.sh

# For a minimal development install only, without the speaker-recognition stack
uv pip install .
```

`scripts/install.sh` installs the Resemblyzer speaker-recognition backend on supported platforms after first installing a non-CUDA PyTorch wheel, so CUDA/NVIDIA packages are not needed. The base Python install also includes `imageio-ffmpeg`, which provides a packaged ffmpeg fallback for local installs where no system `ffmpeg` binary is on `PATH`; Docker still installs the system ffmpeg package. On Raspberry Pi, use the service-pack instructions so system audio packages are installed before the assistant service is started.

### Docker / Synology Quick Start

The Docker setup is designed so API keys stay in text files on the host machine and are mounted into the container.
Do not put the raw OpenAI or ElevenLabs key value directly in `docker-compose.yml`.

Edit `container/config/.env.infrafast` for your NAS/deployment settings.

Put the API keys in files inside the mounted `container/config/` folder:

```bash
printf '%s' 'your-openai-api-key' > container/config/OPENAI_API_KEY.txt
printf '%s' 'your-elevenlabs-api-key' > container/config/ELEVENLABS_API_KEY.txt
```

The Docker env file points to those mounted files from inside the container:

```env
OPENAI_API_KEY_FILE=/config/OPENAI_API_KEY.txt
ELEVENLABS_API_KEY_FILE=/config/ELEVENLABS_API_KEY.txt
```

`docker-compose.yml` mounts `./container/config` to `/config`, so the assistant reads:

```text
host:      ./container/config/OPENAI_API_KEY.txt
container: /config/OPENAI_API_KEY.txt

host:      ./container/config/ELEVENLABS_API_KEY.txt
container: /config/ELEVENLABS_API_KEY.txt
```

The Docker image entrypoint uses `ASSISTANT_ENV_FILE` when set, defaults to `/config/.env.infrafast`, and if that file is missing it auto-detects the first `/config/.env*` file except `*.example`. Docker Compose `env_file` is intentionally not needed here because the assistant loads the mounted env file itself. The bundled compose file uses bridge networking and publishes the web monitor as `${WEB_MONITOR_HOST_PORT:-8765}:8765/tcp`; keep that port mapping when you want to open the monitor from the NAS/LAN. Vendored web files live under `assets/web/static/` and are copied into the image through `assets/`; the compose file mounts `./assets:/app/assets:ro`, so keep the host `assets/` directory complete.

Set `WEB_PASSWORD` in the active env file to require a password before the web monitor opens. Leave it empty or unset to keep the monitor open as before. Authentication is kept in an in-memory browser session cookie and resets when the assistant restarts.

The assistant can run without working audio devices: if microphone capture fails because no input device is available, it falls back to text commands from the web monitor or terminal; if speech playback is unavailable, responses are still printed in the console and monitor. For a first run on Synology or another headless Docker host, `TTS_PROVIDER=none` is only the quietest starting point while you validate the container, MCP, and web monitor. Microphone and speaker passthrough can be tested later.

Build and start:

```bash
docker compose up --build -d
docker logs -f live-stage-assistant
```

The bundled Docker image includes CPU Torch, Resemblyzer, and the speaker-recognition dependencies. Speaker profile WAVs and sample embeddings are stored in the mounted `/data/speaker_profiles` directory.

Open the monitor from your browser:

```text
http://NAS_IP:8765
```

Live Stage Assistant can connect to any MCP server exposed in the selected `MCP_CONFIG`. Two stage-control MCP servers known to be compatible with this agent are:

- XMSeries-MCP for Behringer/X32-style mixer control: https://github.com/infrafast/XMSeries-MCP
- QLCPlus-MCP for QLC+ lighting/DMX control: https://github.com/infrafast/QLCPlus-MCP

If you use the mixer MCP server in local stdio mode, clone/install/build `XMSeries-MCP` on the host and mount it in `docker-compose.yml`:

```yaml
volumes:
  - ./XMSeries-MCP:/xmseries-mcp:ro
```

Then keep this value in `.env`:

```env
MCP_CONFIG=/config/mcp_servers.synology.json
```

The server script path itself belongs in `container/config/mcp_servers.synology.json`, for example:

```json
"args": ["/xmseries-mcp/dist/index.js"]
```

In stdio mode, mixer connection settings such as `OSC_HOST`, `OSC_PORT`, and `OSC_PROTOCOL` belong in the `env` block of `container/config/mcp_servers.synology.json`; that block is passed to the XMSeries-MCP process. If XMSeries-MCP runs as a separate HTTP service/container, put those OSC settings on the XMSeries-MCP service instead and configure Live Stage Assistant with only the MCP HTTP URL. QLCPlus-MCP can be configured the same way: either run it as a local stdio MCP server with its QLC+ host/OSC settings in that server's `env` block, or point the assistant at its streamable HTTP MCP endpoint.

On DSM 7.0, the exact Docker UI depends on the Synology model and installed Docker package. If the Container Manager "Project" interface is not available, use SSH and the `docker compose` command above, or create an equivalent container manually with the same mounts and published port `8765`.

### Offline Preparation

Offline mode works only after the required Python packages, Node packages, Ollama model, and Whisper model are already available locally.

1. Install dependencies while online:
```bash
./scripts/install.sh
```

2. Install and start Ollama, then pull a tool-capable model:
```bash
ollama serve
ollama pull qwen3:8b
```

3. Download/cache the local Whisper model once:
```bash
python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='auto', compute_type='int8')"
```

4. Run the local MCP server packages used by `mcp_servers.offline.json`:
```bash
npx -y @modelcontextprotocol/server-filesystem
npx -y @modelcontextprotocol/server-memory --help
```

The offline MCP config uses `npx --offline`, so it will fail instead of reaching the network if those packages were not cached first. After those steps, the assistant can run without OpenAI or ElevenLabs API keys when using Ollama, local Whisper, pyttsx3, and the offline MCP config.

## Configuration

### Environment Variables

Create a `.env` file in your project root (see `.env.example` for a complete template):

```bash
# Required only when using OpenAI LLM or OpenAI Whisper API.
# Put the real key in this ignored local text file.
OPENAI_API_KEY_FILE=OPENAI_API_KEY.txt

# Optional but recommended for better voice output
ELEVENLABS_API_KEY_FILE=ELEVENLABS_API_KEY.txt

# LLM provider selection
CONNECTIVITY_MODE=online                        # online | offline; web config keeps cloud/local fields coherent
LLM_PROVIDER=openai                             # openai | ollama
OPENAI_MODEL=gpt-4o-mini                        # OpenAI: gpt-4o-mini, gpt-4o, gpt-4.1-mini

# Ollama local settings (when LLM_PROVIDER=ollama)
OLLAMA_BASE_URL=http://localhost:11434
# OPENAI_MODEL=qwen3:8b                         # Example Ollama model tag

# Speech-to-text settings
STT_PROVIDER=openai-whisper                     # openai-whisper | local-whisper
LOCAL_WHISPER_MODEL=base                        # faster-whisper model size or local model path
STT_LANGUAGE=fr                                 # required locale/STT language; options come from assets/i18n/<locale>.json
STT_PROMPT="Commandes courtes en français..."   # Optional context prompt for Whisper

# Text-to-speech settings
CLOUD_TTS_PROVIDER=openai                       # TTS dropdown: none | openai | elevenlabs
TTS_PROVIDER=none                               # Backend output: openai | elevenlabs | pyttsx3 | none

# Voice Settings
ELEVENLABS_VOICE_OPTIONS=kENkNtk0xyzG09WW40xE (Marcel), 1EmYoP3UnnnwhlJKovEy (Anthony)
ELEVENLABS_VOICE_ID=1EmYoP3UnnnwhlJKovEy      # Selected ElevenLabs voice ID

# Optional - Audio Configuration
VAD_SPEECH_THRESHOLD=0.5                         # Silero speech probability required to start speech
VAD_NEGATIVE_THRESHOLD=0.35                      # Silero probability below which accepted speech can end
VAD_MIN_SPEECH_MS=120                            # Minimum speech duration before STT is accepted
VAD_MIN_SILENCE_MS=650                           # Silence duration before ending an accepted utterance
VAD_SPEECH_PAD_MS=100                            # Backend pre-roll kept before detected speech
VAD_MAX_SPEECH_SECONDS=8                         # Hard cap for one detected utterance
INTERRUPT_CONVERSATION_ENABLED=false            # Let new text/STT commands cancel current processing/TTS first
VOICE_CANCEL_DURING_THINKING=false             # Optional spoken stop/annule cancel listener while processing
THINKING_SOUND_FILE=thinking.wav                # WAV loop during accepted STT/LLM/MCP processing; empty disables it
STARTUP_LOADER_SOUND_ENABLED=false             # Loop STARTUP_LOADER_SOUND_FILE on backend output until startup ready
STARTUP_LOADER_SOUND_FILE=loader.wav           # WAV loop for backend startup loading feedback
COMMAND_ACK_SOUND_ENABLED=false                # Play assets/ring.wav when the response is ready and TTS is about to start
BACKEND_AUDIO_INPUT_DEVICE=                     # Optional PyAudio input device index; empty uses default
BACKEND_AUDIO_OUTPUT_DEVICE=                    # Optional PyAudio output device index; empty uses default

# Optional - local web monitor
WEB_MONITOR_ENABLED=true                        # Serve chat UI, runtime state, config, logs, and final prompt
WEB_MONITOR_HOST=127.0.0.1
WEB_MONITOR_PORT=8765
STT_INPUT=both                                 # both | backend | browser | silent
WEB_STT_PROVIDER=openai
WEB_STT_MODEL=whisper-1
WEB_TTS_PROVIDER=openai                         # Browser output: openai | elevenlabs | none
WEB_TTS_MODEL=gpt-4o-mini-tts
WEB_TTS_VOICE=alloy
WEB_TTS_SPEED=1.00                              # Cloud TTS speed for web playback and backend cloud TTS
WEB_TTS_VOLUME=1.00                             # Browser TTS volume, 0.00 to 1.00
BACKEND_TTS_VOLUME=1.00                         # Backend TTS software gain, 0.00 to 2.00
BACKEND_AUDIO_OUTPUT_PAN=0.00                   # Backend output pan: -1.00 left, 0.00 center, 1.00 right
BACKEND_AUDIO_MONITOR_MODE=off                  # Backend mic monitoring: off | rejected | passthrough
BACKEND_AUDIO_MONITOR_VOLUME=1.00               # Backend mic monitoring gain, 0.00 to 2.00

# Optional - Assistant Configuration
WAKE_WORD="régie,console"
ASSISTANT_SYSTEM_PROMPT="You are a helpful voice assistant..."  # Customize personality
MCP_AGENT_MEMORY_ENABLED=true                  # Keep conversational memory; live external state still requires MCP reads
MCP_AGENT_TIMEOUT_SECONDS=45                   # Max seconds for one LLM/MCP response before timeout
MCP_AGENT_MAX_STEPS=20                         # Max MCPAgent steps for one response; each tool call consumes several steps
MCP_TOOL_ROUTING_ENABLED=false                 # Use assistantOptions.routing keywords to expose only one MCP server's tools
SESSION_CONTEXT_SIZE=6000                      # Inject up to this many session-summary chars; 0 disables injection
SESSION_CONTEXT_DIR=.contexts                  # Local directory for .context JSON session files
MCP_CONFIG=mcp_servers.offline.json             # Optional config override

# Optional - MCP-provided Assistant Instructions
MCP_LOAD_SERVER_PROMPT=false                    # true | false, default false
MCP_PROMPT_MERGE_MODE=append                    # append | replace, default append

# Optional - MCP Server Specific
LINEAR_API_KEY=your-linear-api-key              # For Linear integration
```

The assistant is configured from an environment file. The CLI intentionally accepts only `--env-file` plus `--help`, so the selected `.env` file is the single source of truth for runtime settings.

API secrets are read through `OPENAI_API_KEY_FILE` and `ELEVENLABS_API_KEY_FILE`. These variables must contain paths to text files that contain the secret, not the secret value itself. In Docker/Synology deployments, place those files in the mounted config folder on the host, for example `./container/config/OPENAI_API_KEY.txt`, and point the container env file to `/config/OPENAI_API_KEY.txt`.

The assistant treats current external state as time-sensitive. Conversation memory can preserve context and follow-up references, but when the user asks for the current state of anything outside the conversation, the agent is instructed to call the relevant MCP read tool before answering. Set `MCP_AGENT_MEMORY_ENABLED=false` only if you want to disable MCPAgent conversation memory entirely. `MCP_AGENT_TIMEOUT_SECONDS` limits one LLM/MCP turn by wall-clock time; if the agent has not produced a response before that delay, the request is cancelled and the assistant says that the request is taking too long. `MCP_AGENT_MAX_STEPS` controls the MCPAgent/LangGraph step budget for one turn and is exposed in **Config -> IA model -> MCP Steps**. Mixer and lighting commands that resolve a named target, read the current value, convert units, write the new value, verify it, then answer can consume several internal steps, so raise it if a successful tool action ends with a recursion-limit error.

Web chat sessions are persisted as `.context.json` files under `SESSION_CONTEXT_DIR`. The web monitor shows the sessions in the left sidebar, restores the selected session's message bubbles, and loads the most recently active session on startup. The active session keeps the full message list for UI restore, a bounded `summary` transcript, and an optional `llm_summary` generated from that transcript when the assistant starts or when the web UI switches sessions. The LLM summary is durable memory: it keeps preferences, future instructions, corrections, conventions, unresolved tasks, and project decisions, while ignoring transient one-off requests, momentary state checks, executed commands, temporary values, live external state, connected device identity, and routine tool results unless the user explicitly turns them into a durable note. In the web sidebar, hover a session on desktop to preview its `llm_summary`; on touch/mobile, tap the small `i` button when a session has a summary. The session actions menu can rename, delete, clear the visible conversation, or save context; save context forces a fresh `llm_summary` generation for that session. Clearing removes the stored message bubbles and transcript summary but preserves `llm_summary`, so learned continuity can still be injected internally without reappearing as a visible chat bubble. `SESSION_CONTEXT_SIZE` controls how many characters from the injectable summary are added to each LLM/MCP turn, but the injected context is not added to the Final Prompt shown in the Config tab. The web range accepts `0` to `12000`; set it to `0`, or use **Config -> IA model -> Session Context**, if a prior session is steering the assistant too strongly. Restored chat bubbles that fit inside the selected context window are highlighted in green, and the preview updates immediately when the range changes. If `llm_summary` cannot be generated, the assistant falls back to the deterministic `summary` transcript. Even when session context is enabled, live external state still must be read again through MCP tools.

### Wake Word

`WAKE_WORD` is optional. When it is empty, the assistant processes every successful transcription. When it is set, spoken transcriptions are processed only if the wake word appears at the start of the phrase or very close to it.

The thinking sound follows the same gate. Without a configured wake word, it may start while an audio sample is being transcribed. With a configured wake word, transcription stays silent and the thinking sound starts only after the wake word has been detected and the command has been accepted. Ambient speech rejected by the wake-word gate therefore produces no thinking sound. This applies to the backend microphone, browser conversation audio, and WAV files injected through the chat composer. Set `THINKING_SOUND_FILE=` or select **No thinking sound** in the web configuration to disable it entirely.

For example, with `WAKE_WORD="régie,console"`, all of these are accepted and the command text after the wake word is sent to the agent:

```text
Régie, increase volume
Hi Régie, increase volume
Wakeup Régie, increase volume
```

If multiple variants are needed, separate them with a comma, semicolon, or pipe:

```bash
WAKE_WORD="régie,console"
```

The web config exposes the same value in the STT/TTS section. Saving the config writes `WAKE_WORD` to the active env file and reloads the assistant, so the new wake-word gate applies after the runtime reload.

### Web Monitor

When `WEB_MONITOR_ENABLED=true`, the assistant starts a local web monitor and prints its URL at startup, by default:

```text
Web monitor available at http://127.0.0.1:8765
```

The web monitor is intentionally split between a clean chat surface and a technical settings overlay:

- The main page is a ChatGPT-like command window. User commands appear as right-aligned bubbles and assistant/TTS responses appear as left-aligned bubbles.
- The command input stays pinned to the bottom of the page. Press Enter or the up-arrow button to send; Shift+Enter inserts a newline.
- A **Remote screen** collapsible above the chat embeds the monitor's generated `/vnc.html` page in an iframe for visual monitoring. The URL is loaded from `REMOTE_SCREEN_VNC_URL` in the active env file and defaults to `vnc://192.168.0.160:5900?password=ronron`; clicking **Connecter** saves the edited URL back to the active env file. The noVNC viewer's **Lecture seule** checkbox is loaded from `REMOTE_SCREEN_VNC_VIEW_ONLY` and saved back to the active env file with the URL; changing it disconnects/reconnects the iframe so the new mode applies immediately. When the active env profile changes, the web UI disconnects the current noVNC iframe and reconnects with the remote-screen settings from the newly selected profile. The browser-side helper converts `vnc://` values to `/vnc.html?host=...&port=...&password=...&autoconnect=1`. The monitor exposes `/api/vnc-check` to test TCP reachability from the server and `/api/vnc-proxy`, a small WebSocket-to-TCP bridge used by noVNC to reach the VNC target. This panel is independent of the assistant logic. The noVNC browser module is vendored under `assets/web/static/novnc`, so the remote-screen panel can run on a local network without internet access.
- While the LLM/MCP agent is processing, the input is disabled and the response area shows a small thinking animation. The send arrow becomes a square stop button.
- While the thinking animation is visible, the browser also loops the selected `THINKING_SOUND_FILE` from `assets/`, matching the backend thinking-sound behavior.
- Pressing the stop button calls `/api/cancel-command`, cancels the active agent task, clears the busy state, and returns the assistant to listening.
- If `STT_INPUT=both` or `STT_INPUT=browser`, a browser microphone button appears in the composer. The browser records audio, sends it to the backend, the backend calls OpenAI STT, and the transcribed text is injected as a normal command.
- The composer `+` button can load a small text file into the prompt or send a WAV file through the same browser STT path as microphone audio. WAV uploads follow the active wake-word setting, so they can reproduce microphone command handling for tests.
- The left sidebar lists persisted sessions. The `+` button creates a new session, and selecting a session restores its chat bubbles and clears the in-memory MCPAgent history for a clean switch.
- The top-right settings button opens an overlay. The first tab contains **State** and **Console Log** collapsibles. The second tab contains **Config** with a top-level connectivity switch, **MCP Servers** links, routing-word editing, and optional iframe loading for proxied HTTP MCP `/mcp` admin pages, then **STT/TTS**, **IA model**, **User interface**, **Prompt**, and **Env file** collapsibles.
- The web monitor frontend lives under `assets/web/` (`index.html`, `app.css`, `app.js`). The backend still injects the active i18n payload into `index.html`, while static CSS/JS are served from `/assets/web/...`. Docker is compatible because the image copies `assets/` and the compose file mounts `./assets:/app/assets:ro`.

The monitor exposes these HTTP endpoints:

- `GET /api/snapshot`: returns runtime state, logs, dialogue messages, `assistant_busy`, config, prompt, and service status.
- `POST /api/inject-command`: queues a text command for the agent.
- `POST /api/cancel-command`: requests cancellation of the currently processing command.
- `POST /api/web-transcribe`: accepts browser-recorded base64 audio and returns transcribed command text when web audio is enabled.
- `POST /api/web-tts`: returns browser-playable base64 MP3 speech when web audio TTS is enabled.
- `POST /api/backend-tts-test`: plays the config page's voice test phrase through the selected backend audio output. The voice test button follows `TTS Output`: browser output previews through `/api/web-tts`, backend output previews through this endpoint, and silent output does not play.
- `POST /api/mcp-routing`: persists Config -> MCP Servers routing words into the active `MCP_CONFIG` JSON and requests an assistant reload.
- `POST /api/backend-audio-level`: samples the selected backend PyAudio input briefly and returns a level used by the Config audio test meter.
- `GET /api/llm-options` and `POST /api/llm-config`: back the provider/model/voice/thinking-sound/startup-loader controls in the config tab.
- `POST /api/backend-audio-sample`: previews one validated top-level `assets/*.wav` file through the currently selected backend output, volume, and pan.
- `GET /api/session-context`, `POST /api/session-context/new`, `POST /api/session-context/select`, `POST /api/session-context/rename`, `POST /api/session-context/clear`, `POST /api/session-context/save`, and `POST /api/session-context/delete`: list, create, switch, rename, clear visible conversation, force-save context summary, and delete persisted chat sessions.

Dialogue and technical logs are separate. The main chat only displays user commands and assistant responses. Python `stdout`, `stderr`, and existing `logging.StreamHandler` instances are mirrored into **Settings > Monitor > Console Log**, so tool traces such as OSC read/write logs stay available without cluttering the main dialogue.

OSC log lines are no longer filtered out by the web monitor. If the terminal prints an MCP/tool trace such as `[OSC READ]`, `/xinfo`, or `[OSC WRITE]`, the same text should be visible in **Console Log**.

Injected commands are treated as direct text input after wake word handling. This means the text entered in the chat box should be the command itself, without the wake word. After the monitor accepts the command, the input is cleared. The agent logs the command as consumed before processing it.

The monitor remains decoupled from the assistant logic. The web page queues text and cancellation requests; the agent remains responsible for consuming, processing, cancelling, and returning to microphone/text fallback listening. If the assistant is already inside microphone recording when a command is injected, the recording loop stops early and the queued command is consumed immediately after the microphone stream closes.

#### Browser Audio Mode

Browser audio is now derived from explicit input/output choices. `STT_INPUT=both|browser` enables browser microphone STT, and `WEB_TTS_PROVIDER=openai|elevenlabs` enables browser TTS when backend TTS is silent. `STT_INPUT=backend` keeps voice input on the backend microphone only, and `STT_INPUT=silent` leaves text input only. Browser audio is still proxied through the backend so API keys are never sent to the browser:

```env
STT_INPUT=both
WEB_STT_PROVIDER=openai
WEB_STT_MODEL=whisper-1
VAD_SPEECH_THRESHOLD=0.5
VAD_NEGATIVE_THRESHOLD=0.35
VAD_MIN_SPEECH_MS=120
VAD_MIN_SILENCE_MS=650
VAD_SPEECH_PAD_MS=100
VAD_MAX_SPEECH_SECONDS=8
CLOUD_TTS_PROVIDER=openai
WEB_TTS_PROVIDER=openai
WEB_TTS_MODEL=gpt-4o-mini-tts
WEB_TTS_VOICE=alloy
WEB_TTS_SPEED=1.00
WEB_TTS_VOLUME=1.00
BACKEND_TTS_VOLUME=1.00
BACKEND_AUDIO_OUTPUT_PAN=0.00
BACKEND_AUDIO_MONITOR_MODE=off
BACKEND_AUDIO_MONITOR_VOLUME=1.00
```

The browser microphone path requires browser microphone permission, a browser that supports `MediaRecorder`, and cross-origin isolation for the bundled ONNX Runtime Web worker/wasm. The monitor serves the required COOP/COEP headers for its own pages and static assets. Depending on the browser, microphone access may require HTTPS when the monitor is opened from another machine over the LAN. Push-to-talk recording starts when the microphone button is pressed, stops when the square button is pressed again, and stops automatically after Silero VAD detects end-of-speech or reaches `VAD_MAX_SPEECH_SECONDS`.

Browser audio input/output device choices are local to each browser and are saved in `localStorage`, not in the backend `.env` file. Settings -> Config -> Audio In/Out lists browser microphones with `navigator.mediaDevices.enumerateDevices()` and applies the selected input to push-to-talk and conversation mode with `getUserMedia({ deviceId })`. Input selectors follow **STT/TTS -> STT Input**: `Both` shows browser and backend inputs, `Browser` shows browser input, `Backend` shows backend input, and `Silent` hides microphone inputs. Each input selector has a small **Test** button with a vertical level meter: browser input is measured locally with Web Audio, and backend input is sampled through `/api/backend-audio-level`. Output selectors follow **STT/TTS -> TTS Output**: `Silent` hides output selectors, `Browser` shows only browser output, and `Backend` shows only backend output. The STT/TTS segmented controls keep unavailable choices visible but disabled, with a tooltip explaining the missing browser/backend input/output capability or mode constraint. Device names may stay generic until the browser grants microphone permission. Browser output selection uses `HTMLMediaElement.setSinkId()` for web TTS and web thinking sound when the browser supports it; unsupported browsers show output selection as unavailable and use the system/browser default output. AudioContext fallback playback cannot force a selected sink, so the HTML audio path is preferred for web TTS.

The conversation button next to the microphone enables continuous browser listening. In this mode the push-to-talk button is disabled, the browser detects speech/silence locally, sends each detected utterance to the backend, and then restarts listening after the assistant is done. If `WAKE_WORD` is configured, conversation-mode transcriptions must pass the same wake-word gate before being injected. Manual push-to-talk remains direct command input and does not require the wake word.

Backend microphone STT and browser microphone STT now use the same bundled Silero VAD ONNX model offline. Backend microphone input uses `STT_PROVIDER`, `LOCAL_WHISPER_MODEL`, `STT_LANGUAGE`, and `STT_PROMPT` for transcription selection and Whisper biasing; browser microphone input uses `WEB_STT_PROVIDER` and `WEB_STT_MODEL`. `STT_LANGUAGE` is also the web GUI locale and is selected from Settings -> Config -> **User interface**. Available choices are discovered from `assets/i18n/<locale>.json`; the repository ships `fr` and `en`, and changing the language saves the active `.env` file and reloads the assistant. Before either path sends audio to STT, Silero estimates speech probability. `VAD_SPEECH_THRESHOLD` starts speech, `VAD_NEGATIVE_THRESHOLD` allows accepted speech to end, `VAD_MIN_SPEECH_MS` rejects tiny noises, `VAD_MIN_SILENCE_MS` controls end-of-phrase timing, `VAD_SPEECH_PAD_MS` keeps backend pre-roll before detected speech, and `VAD_MAX_SPEECH_SECONDS` caps one utterance. Raise thresholds or minimum speech values to reject breaths/noise; lower them if short spoken commands are missed.

The Settings -> Config -> STT/TTS section exposes these controls in a nested **Voice Activity Detection (VAD)** collapsible. Hover each slider label to see the exact `.env` variable it writes. The same VAD collapsible includes three Silero presets for quick isolated words, breath filtering, and slow soft speech; applying one fills the sliders and still requires Save to persist it.

`INTERRUPT_CONVERSATION_ENABLED=false` keeps the conservative default: text, web STT, and backend STT do not start a new normal command while the assistant is processing. When set to `true`, a new text command or accepted STT command silently cancels current processing or TTS first, then runs the new command. Browser conversation mode also keeps listening during processing and web TTS.

Backend interruption is implemented too: backend microphone input starts a parallel interrupt listener during command processing and backend TTS. If it hears a cancel phrase such as `stop` or `annule`, it only cancels; if it hears a full command that passes the wake-word gate, it cancels the current work and queues that command next. Remaining caveats are operational rather than TODOs in the code: backend barge-in depends on microphone availability and can be affected by the assistant's own speaker output leaking into the mic. For the cleanest backend interruption, use headphones, echo control, or browser TTS instead of open speakers.

Backend audio devices can be selected from Settings -> Config -> Audio In/Out. The dropdown values are PyAudio device indexes saved as `BACKEND_AUDIO_INPUT_DEVICE` and `BACKEND_AUDIO_OUTPUT_DEVICE`; leave either value empty to use the system default. If a saved index is unavailable at startup, the backend falls back to the default device and reports the fallback in Settings -> Monitor -> State as `Backend audio`. If the web config is saved while a previously selected backend device is unavailable, the stale selection is cleared to the default instead of blocking the save. The older separate `Audio input` state tile is replaced by this single backend audio tile. Backend microphone recording uses the selected input device and opens it with a supported channel/rate combination when ALSA rejects the 16 kHz default; audio is resampled internally to 16 kHz for Silero VAD. Backend cloud TTS and backend thinking sound play through the selected PyAudio output device only when TTS Output is `Backend`; when TTS Output is `Browser`, the browser plays web TTS and the thinking sound through its own selected/browser-default output. MP3 cloud TTS is decoded with `ffmpeg` before playback. The backend thinking sound continues through cloud/local TTS generation and stops only at the transition into actual TTS playback, avoiding a silent gap between the answer being ready and speech starting. The **User interface** panel lists every top-level `assets/*.wav` file for both the thinking sound and startup loader. Their Play buttons use the currently selected output and volume; thinking-sound preview supports Browser and Backend, while loader selection and preview are intentionally disabled unless TTS Output is Backend. Selecting **No startup loader sound** saves `STARTUP_LOADER_SOUND_ENABLED=false`; selecting a WAV saves it as `STARTUP_LOADER_SOUND_FILE` and enables the loader. At runtime the loader remains backend-only, loops while the assistant initializes, and stops just before the startup TTS says `Assistant vocal prêt`. `COMMAND_ACK_SOUND_ENABLED=true` plays `assets/ring.wav` after the LLM/MCP response is ready and just before TTS generation/playback begins; it follows the selected speech side, using backend PyAudio for backend TTS and browser audio for browser TTS, and does not stop the thinking sound. Local `pyttsx3` is first rendered to a file and played through the same output path when possible, with direct system TTS as the last fallback. `BACKEND_TTS_VOLUME` applies software gain to backend TTS, backend thinking-sound playback, and backend startup-loader playback before PyAudio writes to the selected output device; `BACKEND_AUDIO_OUTPUT_PAN` applies software pan from left `-1.00` to center `0.00` to right `1.00` on the same backend playback path. `BACKEND_AUDIO_MONITOR_MODE=passthrough` forwards backend microphone chunks to the backend output while capture is running; `BACKEND_AUDIO_MONITOR_MODE=rejected` replays a captured phrase only when it is rejected because the wake word was not detected, and is therefore available only when `WAKE_WORD` is set. `BACKEND_AUDIO_MONITOR_VOLUME` controls that microphone monitoring path separately from TTS gain. These software controls do not call PipeWire/PulseAudio controls.

Backend microphone monitoring is currently **experimental** and has not yet been validated across the supported local, Docker, Raspberry Pi, ALSA, and PipeWire setups. Keep `BACKEND_AUDIO_MONITOR_MODE=off` unless actively testing it. Start with a low `BACKEND_AUDIO_MONITOR_VOLUME` and preferably headphones: `passthrough` may introduce latency, feedback, channel/rate incompatibilities, or device contention depending on the audio interface and host stack. The `rejected` mode also needs real-world validation of its timing and wake-word interaction, so further adjustments are expected after hardware testing.

When the web config requests a runtime reload while backend microphone capture is active, the assistant stops the recording loop and rebuilds itself. On reload only, PortAudio termination is deferred instead of calling `PyAudio.terminate()` immediately, the local pyttsx3/espeak engine stop is skipped in favor of setting the shared TTS stop event, and MCP session cleanup is deferred so a stuck stdio/HTTP close cannot block the reload. This avoids native ALSA/PipeWire crashes, pyttsx3/espeak hangs, and MCP transport cleanup stalls seen when tearing down immediately after an interrupted capture.

`CLOUD_TTS_PROVIDER` is the TTS dropdown shown in the config page. Set it to `none`, `openai`, or `elevenlabs`. The separate `TTS Output` control chooses `Browser`, `Backend`, or `Silent`; it saves that choice by updating `TTS_PROVIDER` and `WEB_TTS_PROVIDER`. Browser output saves `TTS_PROVIDER=none` and `WEB_TTS_PROVIDER=<cloud provider>`. Backend output saves `TTS_PROVIDER=<cloud provider>` and `WEB_TTS_PROVIDER=none`. Silent output saves both as `none`. Selecting `TTS=none` forces silent output. The config voice Test button speaks a fixed sample using the currently selected cloud provider, voice, speed, volume, and selected output: browser output plays in the browser, backend output plays through the selected PyAudio backend device.

Backend TTS has priority over web TTS. If `TTS_PROVIDER` is `openai`, `elevenlabs`, or `pyttsx3`, the monitor still allows browser STT, but web TTS is disabled to avoid double audio. To let the browser play assistant responses, set `TTS_PROVIDER=none` and set `WEB_TTS_PROVIDER` to the same cloud value as `CLOUD_TTS_PROVIDER`.

Browser/web TTS never falls back to pyttsx3. If the selected web cloud provider cannot be used, browser audio falls back to silent text chat and the monitor shows a short red status message, such as exhausted credits, API-key refusal, or rate limit, instead of the raw provider error. Backend/non-web cloud TTS can fall back to pyttsx3 when `TTS_PROVIDER` is `openai` or `elevenlabs`.

Each rebuilt assistant publishes a new runtime instance identifier. The browser uses it to reset its assistant-message ID tracking after a config or environment reload, while treating restored bubbles as already hydrated. This prevents reused numeric message IDs from suppressing the next browser TTS response without replaying old conversation messages.

The config page shows only the voice selector for the selected TTS provider: OpenAI voices when `openai` is selected, ElevenLabs voices when `elevenlabs` is selected, and no voice selector when `none` is selected. It also exposes `WEB_TTS_SPEED`, used as browser playback speed for web TTS and passed to the cloud provider for backend cloud TTS. `WEB_TTS_VOLUME` controls browser TTS and browser thinking-sound playback from 0 to 100%; `BACKEND_TTS_VOLUME` controls backend TTS and backend thinking-sound software gain from 0 to 200%. The Audio In/Out backend output pan slider writes `BACKEND_AUDIO_OUTPUT_PAN`; the backend monitor switch writes `BACKEND_AUDIO_MONITOR_MODE`, and its volume slider writes `BACKEND_AUDIO_MONITOR_VOLUME`. The voice Test button uses the current unsaved pan value for quick checks, while Save persists it. Saving the config triggers the existing runtime reload, so the active web/backend TTS handlers are replaced after the assistant reloads. OpenAI voice choices are built-in values rather than fetched dynamically like ElevenLabs voice IDs.

The **Cloud API** config collapsible queries provider status from the backend without exposing API keys to the browser. It shows each provider key masked with only the final characters visible, so you can confirm which secret is loaded. ElevenLabs uses `/v1/user/subscription` to show used, remaining, and limit characters for the current billing period. OpenAI's public API does not expose a simple remaining-credit balance; the panel says so and, when the active key is authorized for organization usage endpoints, shows the last 7 days of Costs API spend instead.

The config tab also has a top-level connectivity area. The `.env` dropdown shows the active env profile and lists the available `.env*` files from the assistant working directory and from the active env file's directory, including ignored local profiles that exist on disk or in a mounted Docker `/config` folder. Selecting another env profile asks for confirmation when there are unsaved config changes, then requests a runtime reload with the selected env file. While an env reload is in progress, the web UI shows a loading overlay until the backend reports that the new environment is ready. In `--env-file auto` mode, manual env switching is disabled because connectivity detection owns the active `.env.online` / `.env.offline` choice; when auto switches profile, the config tab refreshes its fields from the newly loaded env file.

The `CONNECTIVITY_MODE` switch below the env dropdown keeps the selected profile coherent. This switch is locked whenever the active profile file is named `.env.online` or `.env.offline`, so those canonical profiles cannot be mislabeled from the UI. `online` exposes the cloud LLM/STT/TTS controls and rejects saving an Ollama provider as an online profile. `offline` hides cloud STT/TTS choices, displays `TTS: local pyttsx3`, and saves a coherent local profile: `LLM_PROVIDER=ollama`, `STT_PROVIDER=local-whisper`, `STT_INPUT=backend`, `CLOUD_TTS_PROVIDER=none`, `TTS_PROVIDER=pyttsx3`, and `WEB_TTS_PROVIDER=none`.

#### Voice Cancel During Thinking

`VOICE_CANCEL_DURING_THINKING=false` is the default and preserves the current behavior: no extra microphone listener is started while the assistant is processing a command.

When set to `true`, the assistant starts a short-lived parallel listener only during the thinking phase. If it clearly hears one of the configured built-in cancel words, such as `stop`, `stoppe`, `annule`, `annuler`, `arrête`, `arrete`, or `cancel`, it cancels the active LLM/MCP task and returns to listening. This is experimental because it opens the microphone during processing and may be affected by stage noise, thinking sounds, or TTS/audio bleed.

### Online and Offline Profiles

The repository includes ready-to-use environment profiles:

- `.env.online`: `CONNECTIVITY_MODE=online`, cloud mode with OpenAI for LLM/STT, TTS dropdown set to ElevenLabs, and `mcp_servers.json`
- `.env.offline`: `CONNECTIVITY_MODE=offline`, local mode with Ollama for LLM, local Whisper for STT, pyttsx3 for TTS, and `mcp_servers.offline.json`
- `raspi_service_pack_stdio/.env.online`: `CONNECTIVITY_MODE=online`, cloud LLM/STT/TTS with local stdio `XMSeries-MCP` and `QLCPlus-MCP` sibling folders through `raspi_service_pack_stdio/mcp_servers_raspi.json`
- `raspi_service_pack_stdio/.env.offline`: `CONNECTIVITY_MODE=offline`, local Ollama/STT/TTS with the same Raspberry Pi stdio MCP config
- `auto`: switch to/from online to offline setting depending according to internet connectivity

Start the assistant by passing the profile you want:

```bash
# Online/cloud profile
python voice_assistant/agent.py --env-file .env.online

# Offline/local profile
python voice_assistant/agent.py --env-file .env.offline

# Raspberry Pi local stdio MCP profile
python voice_assistant/agent.py --env-file raspi_service_pack_stdio/.env.online

# auto
python voice_assistant/agent.py --env-file auto
```

For a Raspberry Pi service install, use the bundled stdio service pack:

```bash
cd /home/pi/LiveStageAssistant/raspi_service_pack_stdio
chmod +x install_livestageassistant_service.sh livestageassistant
./install_livestageassistant_service.sh
livestageassistant auto
```

The installer installs `livestageassistant.service`, adds the `livestageassistant` helper command, and runs the agent with `--env-file auto` using the pack's `.env.online` and `.env.offline` profiles. To test that exact systemd profile selection in a terminal, run `livestageassistant run-auto` or set `ASSISTANT_AUTO_ENV_DIR=/etc/livestageassistant` before calling `voice_assistant/agent.py --env-file auto`.

Before using the online profile, create local secret files at the repository root. They are ignored by Git:

```bash
printf '%s' 'your-openai-api-key' > OPENAI_API_KEY.txt
printf '%s' 'your-elevenlabs-api-key' > ELEVENLABS_API_KEY.txt
```

`.env.online` references those files with:

```bash
OPENAI_API_KEY_FILE=OPENAI_API_KEY.txt
ELEVENLABS_API_KEY_FILE=ELEVENLABS_API_KEY.txt
```

The assistant reads API keys only through `OPENAI_API_KEY_FILE` and `ELEVENLABS_API_KEY_FILE`.

Before using the offline profile, make sure Ollama is running and the selected model is available:

```bash
ollama serve
ollama pull qwen3:8b
```

### MCP Server Configuration

The assistant loads MCP server configurations indicated in your environment file (see Online and Offline Profiles and Environment Variables) in the project root. The bundled examples commonly include:

- **playwright**: Web automation and browser control
- **linear**: Task and project management
- **mixer**: control of a Behringer digital mixer  (see https://github.com/infrafast/XMSeries-MCP)

For offline mode, use `mcp_servers.offline.json`:

- **filesystem**: local filesystem access inside the configured root
- **memory**: local MCP memory server
- **mixer**: control of a Behringer digital mixer  (see https://github.com/infrafast/XMSeries-MCP)

Set `MCP_CONFIG=mcp_servers.offline.json` in the selected env file.

Compatible stage-control MCP servers include **XMSeries-MCP** for mixer control and **QLCPlus-MCP** for QLC+ lighting/DMX control. Add QLCPlus-MCP by adding a `qlcplus` server entry to the selected MCP JSON file, either as a local stdio command or as a streamable HTTP endpoint.

Server-specific paths belong in the selected MCP JSON file. For sibling local MCP repos, prefer portable relative paths such as `../XMSeries-MCP/dist/index.js` and `../QLCPlus-MCP/dist/src/index.js`; use absolute paths only for machine-specific private profiles. Put QLC+ connection settings in that server's `env` block. Environment placeholders can still appear inside JSON string values for secrets or shared settings. If a configured command or Node script cannot be found, the assistant prints that the MCP server instance could not be started and continues with the remaining available servers. When multiple MCP servers are configured, startup probes them individually; if one HTTP/stdio server is unreachable but another one works, the assistant starts with the available servers and reports the skipped server in the monitor state instead of disabling MCP entirely.

For HTTP/Streamable MCP servers, the web monitor Config tab shows a **MCP Servers** collapsible with an **HTTP proxy / Direct** route switch. In proxy mode, **Open** and manual **Load frame** route through `/api/mcp-admin/<server>/...`, so compatible servers such as XMSeries-MCP or QLCPlus-MCP can expose their own runtime/admin UI even when only the LiveStageAssistant backend/NAS can reach the MCP server over Tailscale. In direct mode, the browser opens the MCP server URL itself. Local stdio servers are listed as non-embeddable because they do not have a browser endpoint. Bearer headers from the MCP config are applied by the backend proxy and are not exposed to the browser. Each server card also exposes `assistantOptions.routing`; the editor is visible but disabled while **Tool Routing** is off. Saving rewrites the active `MCP_CONFIG` JSON and reloads the assistant. The process user must have write access to that JSON file.

To add more servers, edit `mcp_servers.json` or copy `mcp_servers.example.json` which includes additional servers like:
- filesystem, github, gitlab, google-drive, postgres, sqlite, slack, memory, puppeteer, brave-search, fetch

Environment variables in the config (like `${GITHUB_PERSONAL_ACCESS_TOKEN}`) are automatically substituted from the selected env file.

To override the default configuration programmatically:

```python
config = {
    "mcpServers": {
        "your_server": {
            "command": "npx",
            "args": ["-y", "@your-org/mcp-server"],
            "env": {"YOUR_API_KEY": "${YOUR_API_KEY}"}
        }
    }
}
```

### MCP-provided Startup Instructions

By default, the assistant uses only the local `ASSISTANT_SYSTEM_PROMPT` or the built-in prompt in `voice_assistant/agent.py`.

You can optionally ask the assistant to load additional system instructions from one or more configured MCP servers before `MCPAgent` is created. This is useful when servers want to expose domain-specific behavior, tool usage rules, or operator guidance without hard-coding that content in the voice assistant.

Enable it with:

```bash
MCP_LOAD_SERVER_PROMPT=true
```

Then add an `assistantOptions` block to each MCP server that should contribute startup instructions. The server name is already the key under `mcpServers`, so it does not need to be repeated in the env file.

```json
{
  "mcpServers": {
    "mixer": {
      "command": "node",
      "args": ["path/to/server.js"],
      "assistantOptions": {
        "routing": "mixer,mix,mixage,son,volume,façade,moniteur,retour,bus"
      }
    }
  }
}
```

For each server with an `assistantOptions` block, the assistant tries standard prompt sources in this order unless custom values are configured:

1. `promptName` or default `agent_prompt`: fetch an MCP prompt with `prompts/get`
2. `resourceUri` or default `agent://prompt/system`: read an MCP resource with `resources/read`
3. `tool` or default `get_agent_prompt`: call a fallback MCP tool with empty arguments

It no longer uses legacy server-specific prompt names or URIs by default. If a server still needs an older custom `promptName`, `resourceUri`, or fallback `tool`, put that explicit override in its `assistantOptions`; otherwise expose the standard `agent_prompt`, `agent://prompt/system`, or `get_agent_prompt`. The assistant never calls arbitrary tools while loading startup instructions. If the server is missing, does not support prompts/resources, does not expose the configured or standard fallback tool, or returns an error, the assistant logs a warning and continues with the local prompt.

Single-server env configuration:

```bash
MCP_LOAD_SERVER_PROMPT=true
MCP_PROMPT_MERGE_MODE=append
```

Multi-server MCP configuration:

```json
{
  "mcpServers": {
    "mixer": {
      "command": "node",
      "args": ["path/to/mixer-server.js"],
      "assistantOptions": {
        "routing": "mixer,mix,mixage,volume,bus"
      }
    },
    "lights": {
      "command": "node",
      "args": ["path/to/lights-server.js"],
      "assistantOptions": {
        "routing": "light,scène,dmx"
      }
    },
    "stage": {
      "command": "node",
      "args": ["path/to/stage-server.js"],
      "assistantOptions": {
        "routing": "stage,show"
      }
    }
  }
}
```

Each `assistantOptions` block can define:

1. `routing`: optional comma-separated business keywords used when `MCP_TOOL_ROUTING_ENABLED=true`
2. `promptName`: optional custom MCP prompt name; omit it for standard `agent_prompt`
3. `resourceUri`: optional custom MCP resource URI; omit it for standard `agent://prompt/system`
4. `tool`: optional custom fallback tool name; omit it for standard `get_agent_prompt`

The prompts are loaded in the order of the servers under `mcpServers`. A failing server prompt logs a warning and does not block the others.

When MCP tool routing is enabled from the web config or with `MCP_TOOL_ROUTING_ENABLED=true`, the assistant checks each command against `assistantOptions.routing`. If a keyword matches, that single turn is run with only that MCP server's tools and the console log includes `[MCP CALL: <server> only]`. If the routed turn fails or returns an empty response, the assistant restores the original tool list and retries with the current default behavior when the full tool list is still within the provider limit.

Routing keywords must be unique across all configured MCP servers, and each server can define at most 10 routing words. If two `assistantOptions.routing` lists share the same normalized word, startup stops with `routing word duplicate: <word>` so ambiguous routing cannot silently send a command to the wrong domain. If a server defines too many words, startup stops with `routing words limit exceeded: <server> has <count> words, max 10`. The web Config -> MCP Servers editor applies the same validation before saving. Routing matching is case-insensitive; MCP servers should also treat user-facing names, labels, and free-text targets as case-insensitive unless a tool explicitly documents a case-sensitive identifier.

OpenAI currently accepts at most 128 tools in one request. When several MCP servers are enabled and their combined tool count exceeds that limit, Live Stage Assistant keeps routing enabled as a guard rail: routed turns still use only the matching server, and unrouted turns use the first configured MCP server whose tool list fits under the limit. If no safe fallback server exists, the unrouted turn runs without MCP tools instead of sending an invalid oversized tool array. Put the most common/default server first in `mcpServers`, and give every additional server a precise `assistantOptions.routing` keyword list. For example, a mixer server can route on `mixer, volume, bus`, while a QLC+ lighting server can route on `qlc, lumière, éclairage, scène, dmx, blackout`.

If a routed server asks for confirmation, a short confirmation reply such as `oui`, `ok`, or `yes` reuses the same MCP server route for the next turn. This keeps flows such as `blackout` followed by `oui` on the lighting server instead of falling back to the default mixer route.

At startup, when instructions are loaded, the assistant writes a console log entry listing the MCP prompt sources that were actually merged, for example:

```text
Loaded and merged 2 MCP prompt source(s) with merge mode 'append': mixer via prompt 'agent_prompt'; lights via resource 'agent://prompt/system'
```

With `MCP_PROMPT_MERGE_MODE=append`, the local prompt stays first and the remote instructions are appended under:

```text
Additional instructions loaded from MCP servers:
Instructions loaded from MCP server "mixer":
...

Instructions loaded from MCP server "lights":
...
```

This mode preserves the local voice constraints, including concise TTS-friendly replies, French-by-default answers unless the user clearly speaks English, plain text only, and no emojis, markdown, bullets, or decorative characters.

Even with several MCP prompt sources, `MCP_PROMPT_MERGE_MODE` still has a role: the loaded MCP prompts are always combined together in the configured order, and this setting decides whether that combined block is appended to the local assistant prompt or replaces it.

With `MCP_PROMPT_MERGE_MODE=replace`, only the loaded remote instructions are used. Choose this only if the MCP server prompts already contain all voice and formatting constraints needed by the assistant.


### Running the Assistant

After installation, run the assistant:

```bash
# Default env file: .env
python voice_assistant/agent.py

# Explicit online profile
python voice_assistant/agent.py --env-file .env.online

# Explicit offline profile
python voice_assistant/agent.py --env-file .env.offline

# Explicit Raspberry Pi local stdio MCP profile
python voice_assistant/agent.py --env-file raspi_service_pack_stdio/.env.online

# Auto profile selection
python voice_assistant/agent.py --env-file auto

# Show the only CLI options
python voice_assistant/agent.py --help
```

`OPENAI_API_KEY` is not required when the selected env file uses `LLM_PROVIDER=ollama` and `STT_PROVIDER=local-whisper`.

With `--env-file auto`, the assistant checks internet connectivity at startup. It loads `.env.online` when internet is reachable, otherwise `.env.offline`. By default these files are read from the current working directory; set `ASSISTANT_AUTO_ENV_DIR` to point auto mode at another profile directory, as the Raspberry Pi service pack does with `/etc/livestageassistant`. It then monitors connectivity every 10 seconds and switches the running assistant profile when the connection state changes:

- `Internet est en ligne` is announced when the online profile is selected
- `Connexion internet coupée` is announced when the offline profile is selected

The network status announcement uses the newly selected profile. Backend TTS uses the selected backend provider. Browser TTS profiles are also handled server-side for this status announcement when `TTS_PROVIDER=none` and `WEB_TTS_PROVIDER=openai|elevenlabs`; the web monitor also marks the status message for browser playback when the UI is open. If the selected cloud provider cannot be used, backend announcements may fall back to pyttsx3.

After the announcement, the current voice loop is interrupted if needed, the active assistant instance is cleaned up, and a fresh instance is started from the newly detected env file. This reloads the TTS, STT, LLM, MCP configuration, and MCP-provided assistant prompt from the selected profile. Any command currently being recorded or processed may be cancelled during the switch, which keeps the implementation simple and avoids mixing services from two profiles. Once the new assistant is ready, it announces that the environment was updated and the in-flight request was cancelled using the TTS from the new profile.

At startup, once MCP initialization is complete, the assistant announces a short ready message. MCP server and web monitor details remain available in the console logs and web state.

If you run `python voice_assistant/agent.py` without `--env-file`, the assistant loads `.env` when present. If `.env` does not exist, internal defaults are used: OpenAI with `gpt-4o-mini` for the LLM, OpenAI Whisper for STT, ElevenLabs as the legacy backend TTS default, `CLOUD_TTS_PROVIDER=openai` for the TTS dropdown, `thinking.wav` for the processing sound, and `mcp_servers.json` when no explicit MCP config is provided. In that default mode, `OPENAI_API_KEY` is required because both the LLM and STT providers use OpenAI.

For short stage commands, `STT_PROMPT` can give Whisper mixer-specific context. The bundled default biases French mixer commands such as `mets Claude`, `baisse snare`, `mute Voc-Claude`, and the assistant also fixes the narrow transcription artifact where a leading `mets` command is fused with the following channel name. At runtime, any `assistantOptions.routing` keywords from the active `MCP_CONFIG` are appended to the STT prompt without rewriting the env file. The web config exposes the base value under **Config -> STT/TTS**.

### Changing Model Provider

The voice assistant supports OpenAI and Ollama through LangChain. Any selected model must support tool calling.

```python
# Using OpenAI (default)
assistant = VoiceAssistant(
    openai_api_key="your-key",
    model="gpt-4o-mini",
    llm_provider="openai",
    stt_provider="openai-whisper",
    tts_provider="openai",
)

# Using Ollama, local Whisper, and local pyttsx3
assistant = VoiceAssistant(
    model="qwen3:8b",
    llm_provider="ollama",
    stt_provider="local-whisper",
    local_whisper_model="base",
    tts_provider="pyttsx3",
)
```

**Note**: Only models with tool calling capabilities can be used. Check your model provider's documentation for supported models.
 
 for ollama:
 https://docs.ollama.com/capabilities/tool-calling

#### Using Ollama (Local LLM)

1. Install Ollama and start it:
```bash
ollama serve
```
2. Pull a tool-capable local model:
```bash
ollama pull qwen3:8b
```
3. Run assistant with the offline env profile:
```bash
python voice_assistant/agent.py --env-file .env.offline
```

The offline profile uses:
```bash
CONNECTIVITY_MODE=offline
LLM_PROVIDER=ollama
OPENAI_MODEL=qwen3.5:4b
OLLAMA_BASE_URL=http://localhost:11434
STT_PROVIDER=local-whisper
STT_INPUT=backend
LOCAL_WHISPER_MODEL=base
CLOUD_TTS_PROVIDER=none
TTS_PROVIDER=pyttsx3
WEB_TTS_PROVIDER=none
MCP_CONFIG=mcp_servers.offline.json
MCP_LOAD_SERVER_PROMPT=true
```

When `LLM_PROVIDER=ollama`, the app stays on Ollama. Start `ollama serve` and pull the selected model before running the assistant.

### Changing Voice Settings

Pass different parameters when initializing. For offline mode, use `tts_provider="pyttsx3"`; for silent text-only output, use `tts_provider="none"`. For backend/non-web cloud speech, use `tts_provider="openai"` or `tts_provider="elevenlabs"`; those cloud modes may fall back to local pyttsx3 if local playback or the selected cloud request fails.

```python
assistant = VoiceAssistant(
    openai_api_key="your-key",
    elevenlabs_api_key="your-key",
    tts_provider="elevenlabs",
    elevenlabs_voice_id="different-voice-id",  # Change voice
    vad_speech_threshold=0.45,  # More sensitive
    vad_min_silence_ms=900,     # Wait longer before ending a phrase
    model="gpt-3.5-turbo"  # Faster model
)
```

Nice voice ID examples:

The web monitor voice dropdown is populated from `ELEVENLABS_VOICE_OPTIONS` in the selected `.env` profile, not from this README. Define the voices you want to expose like this:

```env
ELEVENLABS_VOICE_OPTIONS=kENkNtk0xyzG09WW40xE (Marcel), 1EmYoP3UnnnwhlJKovEy (Anthony), FFXYdAYPzn8Tw8KiHZqg (Ingrid), YxrwjAKoUKULGd0g8K9Y (Lucie)
ELEVENLABS_VOICE_ID=1EmYoP3UnnnwhlJKovEy
```

Each entry uses `voice_id (Display name)`. The dropdown shows the display name and saves the selected voice ID to `ELEVENLABS_VOICE_ID`.

The `TTS` dropdown in the web config saves `CLOUD_TTS_PROVIDER`, and the `TTS Output` control decides whether speech comes from the browser, from the backend speaker, or nowhere. When the browser is the active speech output (`TTS_PROVIDER=none` and `WEB_TTS_PROVIDER=openai|elevenlabs`), browser responses use that cloud provider with no pyttsx3 fallback. When backend speech is active (`TTS_PROVIDER=openai|elevenlabs`), backend speech uses that cloud provider and can fall back to pyttsx3. Selecting `none` sets cloud/browser TTS to silent and hides cloud voice controls. The `STT Input` control independently saves `STT_INPUT=both|backend|browser|silent`. In `CONNECTIVITY_MODE=offline`, the web config hides cloud STT/TTS controls and forces local output with `TTS_PROVIDER=pyttsx3` and `STT_INPUT=backend`.

Speaker recognition is optional and runs after VAD/STT has accepted a speech segment. Enable it with `SPEAKER_RECOGNITION_ENABLED=true` or from the web config, then add up to five WAV reference profiles. Each profile has three optional WAV sample slots and becomes usable as soon as one slot has a computed embedding. Profile slots use a fixed filename convention under `SPEAKER_PROFILES_DIR`, which defaults to `data/speaker_profiles` locally and `/data/speaker_profiles` in Docker: profile 1 uses `profil1_1.wav`, `profil1_2.wav`, and `profil1_3.wav`; profile 2 uses `profil2_1.wav`, `profil2_2.wav`, and `profil2_3.wav`; and so on. Older single-file names such as `profil1.wav` are no longer used. The first backend is `SPEAKER_BACKEND=resemblyzer`; `speechbrain` is reserved for a later backend and currently returns `unknown`. Backend microphone PCM is wrapped as WAV automatically, and browser audio is decoded with ffmpeg when the browser sends WebM/Opus instead of WAV. The assistant uses the system `ffmpeg` binary when available and falls back to the packaged `imageio-ffmpeg` binary otherwise.

Resemblyzer is installed by `scripts/install.sh` and by the Docker image after a non-CUDA PyTorch wheel is installed first. This avoids the CUDA/NVIDIA packages that PyPI's default Torch resolution may otherwise select. On Raspberry Pi, use the service-pack flow documented in `raspi_service_pack_stdio/README.md` so the required system audio packages are installed before the assistant service is started.

For best speaker recognition, provide up to three WAV samples per profile. In the web UI, each sample can come from the existing WAV upload button or from the browser microphone capture button next to it. Browser capture records up to 10 seconds, lets you preview/retry, then uploads a valid WAV to the same `profilX_Y.wav` slot after validation; modern browsers require HTTPS or `localhost` for microphone access. You can mix clean/studio samples and live-condition samples. A good starting strategy is sample 1: clean reference voice, sample 2: clean voice with another phrase, sample 3: live-condition voice from the microphone path used on stage.

The web UI shows three small sample indicators per profile; each square turns green after the corresponding WAV has been saved. Every upload computes an embedding immediately next to that WAV (`profil1_1.npy`, `profil1_2.npy`, etc.). Runtime compares the current utterance against all available sample embeddings for a profile and uses the best sample score as the profile score, so one weak sample does not pull the profile score down. Replacing any sample makes only that sample embedding stale, and the next upload/runtime check recomputes it. Use short clean phrases from the person speaking normally. Avoid music, stage noise, reverb, long silences, clipping, heavy compression, and other voices. Mono WAV at 16 kHz or 44.1/48 kHz is fine; Resemblyzer will resample internally.

The assistant never maps a recognized speaker to a mixer bus, channel, light, or scene by itself. It only adds `speaker`, `speaker_confidence`, and `speaker_backend` to the MCP context or injected command payload. MCP servers that understand that context, such as XMSeries-MCP through `osc_get_speaker_context`, decide what the speaker means for their own domain.

When speaker recognition is enabled and available, the web composer shows a browser-local speaker selector next to the microphone controls. `Auto detect` keeps the current behavior, explicit profile choices force browser text and browser STT commands to use that profile, and `Unknown` explicitly sends `speaker: unknown`. This does not affect backend microphone commands.

If one utterance cannot be analyzed cleanly, the assistant keeps speaker recognition enabled and reports `unknown` for that command. It disables speaker recognition for the current session only when the backend itself appears unusable, such as missing Resemblyzer, SciPy, or `platformdirs` dependencies.

The local diagnostic commands `qui suis-je ?`, `détecte ma voix`, `reconnais ma voix`, and similar phrases are handled by the assistant itself before any MCP call. They report the detected speaker profile and confidence for the current utterance, or explain why no profile was recognized.

For MCP servers launched in `stdio`, the web Config -> MCP Servers section can edit each server's `env` object from the active `MCP_CONFIG` JSON. Use this for server-side options that do not belong to `assistantOptions`, such as XMSeries-MCP speaker mapping:

```json
{
  "XMS_SPEAKER_MAP": {
    "laurent": { "bus": "Laurent", "channel": "Talk Laurent" },
    "marie": { "bus": "Marie" }
  }
}
```

Nested objects are saved back as compact JSON strings because MCP stdio servers receive environment variables as strings. `assistantOptions.routing` remains reserved for LiveStageAssistant's tool-routing words.

Main speaker-recognition settings:

```env
SPEAKER_RECOGNITION_ENABLED=false
SPEAKER_BACKEND=resemblyzer
SPEAKER_THRESHOLD=0.75
SPEAKER_MARGIN=0.10
SPEAKER_PROFILE_1_NAME=
SPEAKER_PROFILE_1_ENABLED=false
```

## Development And Maintenance Notes

The recent web monitor work turned the monitor into the primary operator UI while keeping backend audio and terminal fallback intact. When continuing development, keep these boundaries in mind:

- `voice_assistant/web_monitor.py` owns the browser UI, snapshot state, console-log mirroring, browser audio endpoints, noVNC bridge, MCP admin-page proxy, and settings overlay.
- `voice_assistant/agent.py` owns runtime config loading, STT/TTS selection, wake-word handling, MCP initialization, prompt loading, tool routing, cancellation, and assistant reloads.
- Runtime/config/audio/web/MCP/Docker behavior changes should update this README, relevant `docs/*.md`, `.env.example`, and active profile examples when meanings or defaults change.
- User-facing Web GUI text should be localized through `assets/i18n/*.json`. When adding or changing a visible label, button, tooltip, toast, overlay, or browser-side status message, add/update the same key in every locale file under `assets/i18n/`. Keep technical identifiers such as env var names, API keys, model IDs, route paths, and log-only diagnostics untranslated unless they are intentionally presented as prose.
- Active local/container profiles such as `container/config/.env.infrafast` and `container/config/.env.tailscale` may contain deployment-specific values; inspect them before normalizing or overwriting.

Useful lightweight checks after changes:

```bash
.venv/bin/python -m py_compile voice_assistant/web_monitor.py voice_assistant/agent.py
git diff --check
git status --short
```

For web monitor changes, verify the affected endpoint or UI path in a browser when possible. Important paths include `/api/snapshot`, `/api/cancel-command`, `/api/web-transcribe`, `/api/web-tts`, `/api/llm-config`, `/api/mcp-admin/<server>/...`, `/assets/<thinking-sound>`, and `/vnc.html`.

Operational checks worth repeating on real hardware are browser push-to-talk silence stop, web conversation mode, wake-word gating, thinking sound playback, backend audio device selection, MCP startup with one server down, and MCP admin pages through both HTTP proxy and direct mode.

## Troubleshooting

### Common Issues

1. **No Audio Input Detected**
   - Check microphone permissions
   - Lower `VAD_SPEECH_THRESHOLD` or `VAD_MIN_SPEECH_MS` if short speech is missed
   - Verify PyAudio: `python -c "import pyaudio; pyaudio.PyAudio()"`
   - If no default input device is available, the assistant falls back to text commands instead of retrying microphone capture in a tight loop
   - With the web monitor enabled, use the bottom chat input; without it, type commands in the terminal prompt

2. **TTS Not Working**
   - Verify API keys are set correctly
   - Check API quotas
   - Use `TTS_PROVIDER=pyttsx3` in the selected env file for fully local TTS
   - Backend cloud TTS falls back to pyttsx3 if OpenAI or ElevenLabs fails
   - Browser/web TTS does not fall back to pyttsx3; set `TTS_PROVIDER=none` and `WEB_TTS_PROVIDER=openai` or `elevenlabs`
   - On Ubuntu/Debian/Raspberry Pi OS, install the system TTS/audio packages: `sudo apt-get install alsa-utils ffmpeg espeak espeak-ng libespeak1 libespeak-ng1`
   - In headless environments without ALSA/PyAudio output, `ffmpeg`, or `aplay`, spoken output is skipped or falls back without noisy playback errors. Use `TTS_PROVIDER=none` to make silent mode explicit.

3. **MCP Server Connection Issues**
   - Ensure Node.js is installed
   - Check internet connection for first-time npx downloads
   - Use `MCP_CONFIG=mcp_servers.offline.json` in the selected env file for local-only MCP servers
   - Verify API keys for specific servers
   - For mixer control, set the `mixer` script path in the selected MCP JSON file to the real `XMSeries-MCP/dist/index.js` path
   - For QLC+ lighting control, set the `qlcplus` script path or HTTP endpoint in the selected MCP JSON file to your QLCPlus-MCP server
   - If a configured command or script path is missing, the assistant reports that this MCP server instance could not be started and keeps running with the remaining servers
   - If MCP initialization still fails, the assistant keeps the web monitor running in degraded mode so you can open Config, fix the selected env/MCP profile, and save to reload

4. **Thinking Sound Or Audio Output Unavailable**
   - If PyAudio cannot open the selected backend output device, clear `BACKEND_AUDIO_OUTPUT_DEVICE` to use the system default
   - Set `THINKING_SOUND_FILE=` to leave the thinking sound unset
   - Install an audio backend such as `ffmpeg` or `alsa-utils` only if you need local audio playback

5. **Voice Cancel During Thinking Is Unreliable**
   - Keep `VOICE_CANCEL_DURING_THINKING=false` for the most conservative behavior
   - The web stop button does not depend on microphone/STT and is the reliable cancellation path
   - If enabling voice cancel, prefer `STT_PROVIDER=local-whisper` for local testing and reduce stage bleed into the microphone
   - The cancel listener only runs during the thinking phase; it is not a general wake word or always-on command listener

6. **MCP Startup Instructions Not Loaded**
   - Confirm `MCP_LOAD_SERVER_PROMPT=true` in the selected env file
   - Confirm the selected `MCP_CONFIG` file has at least one server with an `assistantOptions` block
   - Confirm each configured MCP server exposes standard `agent_prompt`, `agent://prompt/system`, or `get_agent_prompt`
   - Check the startup warnings for unsupported prompts/resources or a missing fallback tool
   - If the prompt source belongs to a server instance that could not start, such as `mixer`, fix that server's command or script path in the selected MCP JSON file
   - Use `MCP_PROMPT_MERGE_MODE=append` when you want to keep the local voice and TTS constraints

7. **High Latency**
   - Use faster LLM model (e.g., `gpt-3.5-turbo`)
   - Reduce `MCP_AGENT_MAX_STEPS` only if the model spends too many turns planning tool calls
   - Consider using local models

8. **Tool Action Succeeds Then Ends With a Recursion-Limit Error**
   - Increase `MCP_AGENT_MAX_STEPS` in the active env file, for example `MCP_AGENT_MAX_STEPS=30`
   - This usually means the MCP tool sequence completed, but the agent ran out of internal steps before producing the final spoken answer

9. **Offline Mode Still Tries to Connect**
   - Confirm you started with `python voice_assistant/agent.py --env-file .env.offline`
   - Confirm the selected env file includes `CONNECTIVITY_MODE=offline`
   - Confirm the selected env file includes `LLM_PROVIDER=ollama`
   - Confirm the selected env file includes `STT_PROVIDER=local-whisper`
   - Confirm the selected env file includes `STT_INPUT=backend`, `CLOUD_TTS_PROVIDER=none`, `TTS_PROVIDER=pyttsx3`, and `WEB_TTS_PROVIDER=none`
   - Confirm the selected env file includes `MCP_CONFIG=mcp_servers.offline.json`
   - Confirm the selected env file includes `MCP_LOAD_SERVER_PROMPT=true` when you expect MCP startup instructions to be appended to the assistant prompt
   - Ensure the Ollama model, faster-whisper model, and MCP npm packages were cached before disconnecting

9. **Auto Mode Selected The Wrong Profile**
   - Start with `python voice_assistant/agent.py --env-file auto`
   - Auto mode checks a short TCP connection to `api.openai.com:443`
   - If that host is blocked by your network, auto mode may select `.env.offline`
   - If `.env.online` is selected, make sure `OPENAI_API_KEY.txt` and `ELEVENLABS_API_KEY.txt` exist when those services are configured
   - When the connection status changes, auto mode cancels the current recording or request if needed, then restarts the assistant with `.env.online` or `.env.offline`
