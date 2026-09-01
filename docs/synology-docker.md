# Synology DSM Docker Deployment

This setup is intended for Synology DSM 7.x with Docker/Container Manager. For a first deployment, use the web monitor and text command injection with browser or silent TTS, then test microphone/speaker passthrough later.

For Docker/Synology architecture notes and tradeoffs, see [ARCHITECTURE.md](ARCHITECTURE.md#dockersynology-architecture-notes).

## Files

- `Dockerfile`: builds the Python app image with audio, ffmpeg, Node.js, and npm support.
- `docker-compose.yml`: Synology compose file using bridge networking with the web monitor port published.
- `container/config/.env.infrafast`: edit for the NAS/deployment folder.
- `container/config/mcp_servers.infrafast.json`: MCP config used by the default Docker profile.
- `.dockerignore`: keeps local virtualenvs, caches, and API key files out of the image.

## Folder Layout On The NAS

Create a project folder such as:

```text
/volume1/docker/live-stage-assistant/
  docker-compose.yml
  container/
    config/
      .env.infrafast
      OPENAI_API_KEY.txt
      ELEVENLABS_API_KEY.txt
      mcp_servers.infrafast.json
    data/
  assets/
  XMSeries-MCP/
  QLCPlus-MCP/
```

The `container/` folder contains runtime files and persistent container data:

```text
container/
  config/
    .env.infrafast
    OPENAI_API_KEY.txt
    ELEVENLABS_API_KEY.txt
    mcp_servers.infrafast.json
  data/
```

The Docker image includes `assets/web/static/`, including the vendored noVNC client, ONNX Runtime Web files, and the Silero VAD model. Docker Compose mounts `./assets:/app/assets:ro`; make sure that host folder is complete, otherwise a partial mount can hide required files such as `assets/web/static/novnc/vendor/pako/lib/zlib/inflate.js`.

If the monitor is exposed on your LAN/NAS, set `WEB_PASSWORD` in the active env file to require a password before the web page opens. Leave `WEB_PASSWORD=` empty to keep the previous unauthenticated behavior.

Edit `container/config/.env.infrafast`.
Keep `container/config/mcp_servers.infrafast.json` in the same folder.
Put API keys in the two text files. You can leave the ElevenLabs file empty only when neither `CLOUD_TTS_PROVIDER=elevenlabs` nor `WEB_TTS_PROVIDER=elevenlabs` is used.

The repository ships a few additional Docker profile pairs for common network shapes:

- `.env.infrafast` + `mcp_servers.infrafast.json`: default remote HTTP profile.
- `.env.localhost` + `mcp_servers.localhost.json`: LAN/local HTTP MCP endpoints.
- `.env.tailscaleHTTP` + `mcp_servers.tailscaleHTTP.json`: Tailscale HTTP MCP endpoints.
- `.env.tailscaleSTDIO` + `mcp_servers.tailscaleSTDIO.json`: mounted local stdio MCP servers.

Only one `.env*` profile is active at a time. `docker-compose.yml` starts `/config/.env.infrafast` by default; change `ASSISTANT_ENV_FILE` only when you intentionally want another profile.

The compose file mounts `./container/config` to `/config` and `./container/data` to `/data`, and publishes `${WEB_MONITOR_HOST_PORT:-8765}:8765/tcp` so the web monitor is reachable through bridge networking. The Docker image entrypoint starts the assistant with `ASSISTANT_ENV_FILE` when set, defaults to `/config/.env.infrafast`, and otherwise auto-detects the first `/config/.env*` file except `*.example`. Docker Compose `env_file` is intentionally not needed here because the assistant loads the mounted env file itself. Persisted web chat sessions should use a writable mounted path such as `SESSION_CONTEXT_DIR=/data/contexts`.

`WEB_MONITOR_HOST_PORT` is read by Docker Compose before the container starts. It changes only the host/NAS published port. The assistant's internal listening port still comes from `WEB_MONITOR_PORT` in the selected `/config/.env*` file and should remain `8765` unless you also update the compose container-side port or run Compose with a matching interpolation environment.

The web config profile dropdown lists `.env*` files from both the app working directory and the active env file's directory. In Docker, this means mounted profiles such as `/config/.env.infrafast`, `/config/.env.localhost`, `/config/.env.tailscaleHTTP`, and `/config/.env.tailscaleSTDIO` appear when the container is started with `ASSISTANT_ENV_FILE=/config/...`. Manual switching is disabled only when the assistant is started with `--env-file auto`. Use the HTTP Tailscale profile when XMSeries-MCP and QLCPlus-MCP are already running as reachable streamable HTTP services; use the STDIO Tailscale profile when the container should start mounted local MCP server scripts itself.

If you intentionally switch the assistant back to `network_mode: host`, remove the `ports` block because published ports are not used with host networking. In bridge mode, do not point MCP or Ollama URLs at `127.0.0.1` unless that service runs inside the same container; use the NAS LAN IP, Tailscale IP, or another reachable service name/address.

## Compatible Stage MCP Servers

Live Stage Assistant can use any MCP server listed in the selected `MCP_CONFIG`. Two stage-control MCP servers known to be compatible are:

- XMSeries-MCP for Behringer/X32-style mixer control: https://github.com/infrafast/XMSeries-MCP
- QLCPlus-MCP for QLC+ lighting/DMX control: https://github.com/infrafast/QLCPlus-MCP

If you use the mixer server in local stdio mode, clone/install/build XMSeries-MCP before starting the assistant, then mount it as `/xmseries-mcp`.
The compose file already includes the commented volume line:

```yaml
- ./XMSeries-MCP:/xmseries-mcp:ro
```

Enable that line after the folder exists and contains `dist/index.js`.
The corresponding MCP server path is configured in the selected MCP config file, for example `container/config/mcp_servers.tailscaleSTDIO.json`, not in the agent `.env` file:

```json
"args": ["/xmseries-mcp/dist/index.js"]
```

In this stdio setup, the `env` block in the selected `mcp_servers*.json` file is passed to the XMSeries-MCP child process. It is MCP-server configuration, not Live Stage Assistant application configuration.

If XMSeries-MCP runs as a separate HTTP service/container, put `OSC_HOST`, `OSC_PORT`, `OSC_PROTOCOL`, and related mixer settings on that XMSeries-MCP service instead. In that case, the assistant MCP config should only point to the HTTP endpoint:

```json
{
  "mcpServers": {
    "mixer": {
      "type": "streamable-http",
      "url": "http://NAS_IP:8787/mcp",
      "headers": {
        "Authorization": "Bearer change-me"
      }
    }
  }
}
```

QLCPlus-MCP follows the same pattern. For local stdio mode, mount the built QLCPlus-MCP checkout, set the `qlcplus.args` entry to its built entrypoint, and put QLC+ host/OSC settings in that server's `env` block. If it runs as a separate HTTP service/container, configure Live Stage Assistant with only the QLCPlus-MCP HTTP MCP endpoint.

When pointing at a raw LAN or Tailscale IP such as `100.x.y.z:8788`, use `http://` unless that MCP service is actually behind a TLS reverse proxy. A `https://` URL against a plain HTTP Node service usually fails with `SSL: WRONG_VERSION_NUMBER`.

The web monitor Config tab includes a **MCP Servers** collapsible with an **HTTP proxy / Direct** route switch. In proxy mode, HTTP MCP admin pages route through the Live Stage Assistant backend at `/api/mcp-admin/<server>/...`, so admin pages can work from a browser that only reaches the NAS while the NAS reaches the MCP server over Tailscale. In direct mode, the browser opens the MCP server URL itself. Local stdio MCP entries are shown without a frame. Bearer headers from the MCP config are applied by the backend proxy and are not exposed to the browser. Each card can edit `assistantOptions.routing` when **Tool Routing** is enabled; saving rewrites the active `MCP_CONFIG` JSON and reloads the assistant, so the container user must be able to write that JSON file.

## Start

From SSH:

```bash
docker compose up --build -d
docker logs -f live-stage-assistant
```

The Docker image includes CPU Torch, Resemblyzer, and the speaker-recognition dependencies. Speaker profile data is stored in the mounted `/data/speaker_profiles` directory.

Or in Synology Container Manager/Project, create a project from the compose file.

The monitor will be reachable at:

```text
http://NAS_IP:8765
```

## Audio Notes

The compose file maps `/dev/snd` and adds the `audio` group. This is only useful if the NAS exposes compatible audio hardware to Docker.
If audio does not work, the app should fall back to text commands through the web monitor.

Recommended first-run settings:

```env
WEB_MONITOR_HOST=0.0.0.0
WEB_MONITOR_PORT=8765
CONNECTIVITY_MODE=online
CLOUD_TTS_PROVIDER=openai
TTS_PROVIDER=none
WEB_TTS_PROVIDER=openai
STT_PROVIDER=openai-whisper
STT_INPUT=both
LLM_PROVIDER=openai
MCP_AGENT_MAX_STEPS=20
SESSION_CONTEXT_SIZE=6000
SESSION_CONTEXT_DIR=/data/contexts
```

In the web config, keep the connectivity switch on `Online` and use `TTS Output = Browser` for this first-run shape. Saving browser output stores the chosen cloud provider in `WEB_TTS_PROVIDER`. `Backend` is useful only when the container has usable speaker/audio passthrough; `Silent` sets both backend and browser TTS to `none`. `STT Input = Both` keeps browser microphone and backend microphone available when both devices exist.

Browser audio device selection is per browser and saved in browser `localStorage`. The monitor can select a browser microphone for web STT and, when the browser supports `setSinkId()`, a browser output device for web TTS/thinking sound. Input selectors follow the selected `STT Input`; output selectors follow the selected `TTS Output`. The STT/TTS choices keep unavailable browser/backend input/output options visible but disabled, with a tooltip explaining why. Browser input **Test** shows its local live level meter. Backend input **Test** records a guided seven-second sample on the NAS and temporarily pauses normal backend listening. Backend input can use a PyAudio index or, when PipeWire tools are available, a targeted `pipewire:source:<node.name>` source without changing the global default source. Its hardware verdict uses fixed reference VAD values (`0.50`, `120 ms`) with level, noise, and clipping measurements; a separate result says whether the active `.env` VAD settings would accept that same voice. The returned WAV preview lets the browser play exactly what the NAS received and is not saved. The voice **Test** button follows the selected TTS output and current volume/pan sliders: browser output plays in the browser, backend output plays through the selected PyAudio/PipeWire backend device, and silent output does not play. The STT/TTS panel exposes `WEB_TTS_VOLUME` for browser TTS/thinking-sound playback volume and `BACKEND_TTS_VOLUME` for backend TTS/thinking-sound software gain before backend playback. The Audio In/Out backend output pan slider writes `BACKEND_AUDIO_OUTPUT_PAN`, from left `-1.00` to center `0.00` to right `1.00`, and is applied in software before backend playback. The backend monitor switch writes `BACKEND_AUDIO_MONITOR_MODE`: `off` keeps the current behavior, `passthrough` forwards backend microphone chunks to backend output while capture is running, and `rejected` replays only wake-word-rejected phrases; `rejected` is disabled unless `WAKE_WORD` is set. `BACKEND_AUDIO_MONITOR_VOLUME` controls that microphone monitoring path separately from TTS gain. Device labels may remain generic until microphone permission is granted. On LAN/NAS access, browser microphone permission may require HTTPS through a reverse proxy.

Backend thinking sound is used only when TTS Output is `Backend`; with TTS Output set to `Browser`, the browser plays the selected thinking sound through its own audio output. Backend thinking sound continues while cloud/local TTS audio is being generated and stops only when backend TTS playback is about to begin, so response speech does not leave a silent gap after the LLM/MCP answer is ready.
When `COMMAND_ACK_SOUND_ENABLED=true`, the assistant also plays `assets/ring.wav` after the LLM/MCP response is ready and just before TTS generation/playback begins; it uses backend PyAudio for backend TTS and browser audio for browser TTS, and it does not stop the thinking sound.

The web Config -> STT/TTS section also exposes a nested **Voice Activity Detection (VAD)** collapsible for the bundled offline Silero VAD used by both browser and backend microphone STT. It writes the active profile's `VAD_*` speech threshold, end threshold, minimum speech, silence, padding, and maximum utterance settings; hover a slider label to see the exact `.env` key.

Backend microphone capture auto-selects a channel/rate combination that PyAudio can open, then resamples internally to 16 kHz for Silero VAD. This avoids Raspberry Pi or NAS ALSA devices that list as inputs but reject a fixed 16 kHz stream.

Backend wake-word detection is controlled by `WAKE_WORD`. Leave it empty to disable wake-word detection; set it to require local openWakeWord before backend STT. The backend must also have `BACKEND_WAKE_WORD_MODEL_PATHS` or `BACKEND_WAKE_WORD_MODEL_NAMES` configured, otherwise microphone capture pauses and the service log reports the missing wake detector/model instead of falling back to a text gate. On Linux/Raspberry Pi, `scripts/install.sh` installs openWakeWord in ONNX-only mode, downloads its required internal ONNX resources, and validates the install by instantiating an ONNX model. With wake word active, the assistant keeps a short backend audio ring buffer, waits for the local wake detector, then starts STT only after real command speech follows activation so the beginning of the phrase is not cut off. If the detector fires on silence or noise, the assistant returns to listening without calling STT.

If the web config is saved while backend microphone capture is active, the assistant interrupts capture and reloads. During that reload, PortAudio termination is deferred, the local pyttsx3/espeak engine stop is skipped in favor of the shared TTS stop event, and MCP session cleanup is deferred so a stuck transport close cannot block the reload.

`SESSION_CONTEXT_SIZE` controls how much of the active `.context.json` session summary is reinjected for continuity. The assistant stores both a deterministic `summary` transcript and, when an LLM is available at startup or session switch, a compact `llm_summary` generated from it. In the web chat, restored bubbles highlighted in green are the messages that fit in the current context window; moving the **Session Context** range updates that preview immediately. In the session sidebar, hover a session on desktop to preview its `llm_summary`, or tap the small `i` button on touch/mobile when a summary exists. Use `0` to disable context injection.

`MCP_AGENT_MAX_STEPS` controls the internal MCPAgent/LangGraph step budget for one user turn. Keep the default unless a command successfully calls MCP tools and then fails with a recursion-limit error before the final answer; in that case, raise it to `30`.

Once the monitor path is stable, try microphone/speaker passthrough if needed.
