#!/usr/bin/env sh
set -eu

repo_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$repo_dir"

if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' "Error: uv is required. Install it first with: pip install uv" >&2
    exit 1
fi

uv_cache_dir="${UV_CACHE_DIR:-/tmp/uv-cache}"
export UV_CACHE_DIR="$uv_cache_dir"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

install_system_packages() {
    if [ "${LSA_SKIP_SYSTEM_PACKAGES:-}" = "1" ]; then
        printf '%s\n' "Skipping system package installation because LSA_SKIP_SYSTEM_PACKAGES=1."
        return
    fi

    case "$system" in
        Linux)
            if command -v apt-get >/dev/null 2>&1; then
                printf '%s\n' "Installing Linux audio/system packages with apt-get."
                if [ "$(id -u 2>/dev/null || printf 1)" = "0" ]; then
                    apt-get update
                    apt-get install -y curl portaudio19-dev alsa-utils ffmpeg pipewire-bin espeak espeak-ng libespeak1 libespeak-ng1
                elif command -v sudo >/dev/null 2>&1; then
                    sudo apt-get update
                    sudo apt-get install -y curl portaudio19-dev alsa-utils ffmpeg pipewire-bin espeak espeak-ng libespeak1 libespeak-ng1
                else
                    printf '%s\n' "Warning: sudo is not available; install audio packages manually if backend audio is needed." >&2
                fi
            else
                printf '%s\n' "Warning: apt-get not found; install PortAudio, ffmpeg, ALSA, PipeWire tools, and espeak packages manually if backend audio is needed." >&2
            fi
            ;;
        Darwin)
            if command -v brew >/dev/null 2>&1; then
                if ! brew list portaudio >/dev/null 2>&1; then
                    printf '%s\n' "Installing macOS PortAudio package with Homebrew."
                    brew install portaudio
                fi
            else
                printf '%s\n' "Warning: Homebrew not found; install PortAudio manually if backend audio is needed." >&2
            fi
            ;;
    esac
}

install_ollama() {
    if [ "${LSA_SKIP_OLLAMA:-}" = "1" ]; then
        printf '%s\n' "Skipping Ollama setup because LSA_SKIP_OLLAMA=1."
        return
    fi

    ollama_model="${LSA_OLLAMA_MODEL:-qwen3:8b}"
    case "$system" in
        Linux)
            if ! command -v ollama >/dev/null 2>&1; then
                if command -v curl >/dev/null 2>&1; then
                    printf '%s\n' "Installing Ollama for local/offline mode."
                    curl -fsSL https://ollama.com/install.sh | sh
                else
                    printf '%s\n' "Warning: curl is not available; install Ollama manually for offline mode." >&2
                    return
                fi
            fi
            ;;
        Darwin)
            if ! command -v ollama >/dev/null 2>&1; then
                if command -v brew >/dev/null 2>&1; then
                    printf '%s\n' "Installing Ollama with Homebrew for local/offline mode."
                    brew install ollama
                else
                    printf '%s\n' "Warning: Homebrew not found; install Ollama manually for offline mode." >&2
                    return
                fi
            fi
            ;;
        *)
            if ! command -v ollama >/dev/null 2>&1; then
                printf '%s\n' "Warning: Ollama was not found; install it manually for offline mode." >&2
                return
            fi
            ;;
    esac

    if ! command -v ollama >/dev/null 2>&1; then
        printf '%s\n' "Warning: Ollama is still unavailable; skipping model pull." >&2
        return
    fi

    if ! ollama list >/dev/null 2>&1; then
        printf '%s\n' "Ollama is installed but not running. Start it with 'ollama serve', then pull ${ollama_model} for offline mode."
        return
    fi

    if ollama show "$ollama_model" >/dev/null 2>&1; then
        printf '%s\n' "Ollama model ${ollama_model} is already available."
    else
        printf '%s\n' "Pulling Ollama model ${ollama_model} for local/offline mode."
        ollama pull "$ollama_model"
    fi
}

create_venv() {
    if [ -n "${LSA_PYTHON:-}" ]; then
        uv venv --python "$LSA_PYTHON"
        return
    fi
    case "$system" in
        Linux)
            if command -v python3.11 >/dev/null 2>&1; then
                uv venv --python python3.11
                return
            fi
            ;;
    esac
    uv venv
}

install_wakeword_dependencies() {
    if [ "${LSA_SKIP_WAKEWORD:-}" = "1" ]; then
        printf '%s\n' "Skipping openWakeWord dependencies because LSA_SKIP_WAKEWORD=1."
        return
    fi

    printf '%s\n' "Installing local wake-word detection dependencies."
    if [ "$system" = "Linux" ]; then
        printf '%s\n' "Installing openWakeWord in ONNX-only mode to avoid Raspberry Pi tflite-runtime wheel issues."
        uv pip install "onnxruntime>=1.16,<2" "tqdm>=4,<5" "requests>=2,<3" "scikit-learn>=1,<2"
        uv pip install "openwakeword>=0.6,<1" --no-deps
    else
        uv pip install -e ".[wakeword]"
    fi
}

machine="$(uname -m 2>/dev/null || printf unknown)"
system="$(uname -s 2>/dev/null || printf unknown)"

install_system_packages
install_ollama

if [ ! -d ".venv" ]; then
    create_venv
fi

uv pip install -e .
uv pip install "mcp-use>=1.7.0,<2.0.0" "mcp>=1.24.0,<2.0.0"
install_wakeword_dependencies

printf '%s\n' "Installing speaker recognition dependencies for ${system}/${machine}."
uv pip install -e ".[speaker]"
case "$system" in
    Darwin)
        uv pip install torch
        ;;
    *)
        uv pip install torch --index-url https://download.pytorch.org/whl/cpu
        ;;
esac
uv pip install resemblyzer --no-deps
# Resemblyzer declares the legacy backport package "typing", which is not
# compatible with modern Python and is not needed at runtime.
uv pip uninstall typing >/dev/null 2>&1 || true

uv run python - <<'PY'
import os
from importlib import metadata

from mcp.shared.context import RequestContext
from mcp_use import MCPAgent, MCPClient
from resemblyzer import VoiceEncoder, preprocess_wav

print(
    "MCP dependencies OK: "
    f"mcp-use {metadata.version('mcp-use')}, "
    f"mcp {metadata.version('mcp')}"
)
print(f"Speaker recognition dependencies OK: resemblyzer {metadata.version('resemblyzer')}")
try:
    print(f"Wake-word dependencies OK: openwakeword {metadata.version('openwakeword')}")
except metadata.PackageNotFoundError:
    if os.environ.get("LSA_SKIP_WAKEWORD") == "1":
        print("Wake-word dependencies skipped: openwakeword is not installed")
    else:
        raise SystemExit("Wake-word dependency check failed: openwakeword is not installed")
PY

if uv pip freeze | grep -Ei '^(nvidia|cuda|triton)' >/dev/null; then
    printf '%s\n' "Warning: GPU/CUDA packages are present in the environment:"
    uv pip freeze | grep -Ei '^(nvidia|cuda|triton)'
    printf '%s\n' "They are not required for LiveStageAssistant speaker recognition."
fi

printf '%s\n' "LiveStageAssistant install complete."
