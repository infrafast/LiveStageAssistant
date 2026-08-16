FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/data/huggingface \
    XDG_CACHE_HOME=/data/cache \
    NPM_CONFIG_CACHE=/data/npm-cache \
    SPEAKER_PROFILES_DIR=/data/speaker_profiles

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        alsa-utils \
        espeak-ng \
        ffmpeg \
        libasound2 \
        libespeak-ng1 \
        libportaudio2 \
        libportaudiocpp0 \
        nodejs \
        npm \
        pipewire-bin \
        portaudio19-dev \
        build-essential \
    && ffmpeg -version >/dev/null \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY voice_assistant ./voice_assistant
COPY assets ./assets
COPY mcp_servers*.json ./
COPY docker-entrypoint.sh /usr/local/bin/live-stage-assistant-entrypoint

RUN test -f assets/web/static/novnc/core/rfb.js \
    && test -f assets/web/static/novnc/vendor/pako/lib/zlib/inflate.js \
    && test -f assets/web/static/vendor/silero-vad/silero_vad_v6.onnx

RUN python -m pip install --upgrade pip \
    && python -m pip install .

RUN python -m pip install --no-cache-dir ".[speaker]" \
    && python -m pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install --no-cache-dir resemblyzer --no-deps \
    && python -c "from importlib import metadata; from mcp.shared.context import RequestContext; from mcp_use import MCPAgent, MCPClient; from resemblyzer import VoiceEncoder, preprocess_wav; print('MCP dependencies OK: mcp-use ' + metadata.version('mcp-use') + ', mcp ' + metadata.version('mcp')); print('Speaker recognition dependencies OK: resemblyzer ' + metadata.version('resemblyzer'))"

RUN chmod +x /usr/local/bin/live-stage-assistant-entrypoint \
    && mkdir -p /data/huggingface /data/cache /data/npm-cache /data/notes /data/speaker_profiles

VOLUME ["/config", "/data"]

EXPOSE 8765

ENTRYPOINT ["live-stage-assistant-entrypoint"]
