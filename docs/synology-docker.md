# Synology DSM Docker Deployment

This setup is intended for Synology DSM 7.x with Docker/Container Manager.

## Difficulty

The application itself is not hard to containerize: it is a Python service with Node.js available for MCP servers.

The harder parts are runtime integrations:

- Microphone and speaker access require `/dev/snd` passthrough and working ALSA support on the NAS.
- The mixer MCP server needs LAN access to the mixer, so `network_mode: host` is the simplest Synology setup.
- Offline mode needs Ollama and local Whisper model caches available to the container.
- The XMSeries-MCP repository must be mounted after it has been installed and built.

For a first Synology deployment, use the web monitor and text command injection with `TTS_PROVIDER=none`. This leaves backend speaker output disabled and allows browser/web TTS to speak when `WEB_AUDIO_ENABLED=true`.

## Files

- `Dockerfile`: builds the Python app image with audio, ffmpeg, Node.js, and npm support.
- `docker-compose.yml`: host-network Synology compose file.
- `container/config/.env.infrafast`: edit for the NAS/deployment folder.
- `container/config/mcp_servers.synology.json`: mixer-only MCP config for a mounted XMSeries-MCP checkout.
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
      mcp_servers.synology.json
    data/
  assets/
  XMSeries-MCP/
```

The `container/` folder contains runtime files and persistent container data:

```text
container/
  config/
    .env.infrafast
    OPENAI_API_KEY.txt
    ELEVENLABS_API_KEY.txt
    mcp_servers.synology.json
  data/
```

Edit `container/config/.env.infrafast`.
Keep `container/config/mcp_servers.synology.json` in the same folder.
Put API keys in the two text files. You can leave the ElevenLabs file empty only when neither `CLOUD_TTS_PROVIDER=elevenlabs` nor `WEB_TTS_PROVIDER=elevenlabs` is used.

The compose file mounts `./container/config` to `/config:ro` and `./container/data` to `/data`. The Docker image entrypoint starts the assistant with `ASSISTANT_ENV_FILE` when set, defaults to `/config/.env.infrafast`, and otherwise auto-detects the first `/config/.env*` file except `*.example`. Docker Compose `env_file` is intentionally not needed here because the assistant loads the mounted env file itself. Persisted web chat sessions should use a writable mounted path such as `SESSION_CONTEXT_DIR=/data/contexts`.

## XMSeries-MCP

If you use the mixer server, clone/install/build XMSeries-MCP before starting the assistant, then mount it as `/xmseries-mcp`.
The compose file already includes the commented volume line:

```yaml
- ./XMSeries-MCP:/xmseries-mcp:ro
```

Enable that line after the folder exists and contains `dist/index.js`.
The corresponding MCP server path is configured in `container/config/mcp_servers.synology.json`, not in the agent `.env` file:

```json
"args": ["/xmseries-mcp/dist/index.js"]
```

In this stdio setup, the `env` block in `container/config/mcp_servers.synology.json` is passed to the XMSeries-MCP child process. It is MCP-server configuration, not Live Stage Assistant application configuration.

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

## Start

From SSH:

```bash
docker compose up --build -d
docker logs -f live-stage-assistant
```

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
WEB_AUDIO_ENABLED=true
WEB_TTS_PROVIDER=openai
STT_PROVIDER=openai-whisper
LLM_PROVIDER=openai
SESSION_CONTEXT_SIZE=6000
SESSION_CONTEXT_DIR=/data/contexts
```

In the web config, keep the connectivity switch on `Online` and use `TTS Output = Browser` for this first-run shape. Saving browser output keeps `WEB_AUDIO_ENABLED=true` and stores the chosen cloud provider in `WEB_TTS_PROVIDER`. `Backend` is useful only when the container has usable speaker/audio passthrough; `Silent` sets both backend and browser TTS to `none`.

`SESSION_CONTEXT_SIZE` controls how much of the active `.context.json` session summary is reinjected for continuity. The assistant stores both a deterministic `summary` transcript and, when an LLM is available at startup or session switch, a compact `llm_summary` generated from it. In the web chat, restored bubbles highlighted in green are the messages that fit in the current context window; moving the **Session Context** range updates that preview immediately. Use `0` to disable context injection.

Once the monitor path is stable, try microphone/speaker passthrough if needed.
