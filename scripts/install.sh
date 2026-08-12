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
                    apt-get install -y portaudio19-dev alsa-utils ffmpeg espeak espeak-ng libespeak1 libespeak-ng1
                elif command -v sudo >/dev/null 2>&1; then
                    sudo apt-get update
                    sudo apt-get install -y portaudio19-dev alsa-utils ffmpeg espeak espeak-ng libespeak1 libespeak-ng1
                else
                    printf '%s\n' "Warning: sudo is not available; install audio packages manually if backend audio is needed." >&2
                fi
            else
                printf '%s\n' "Warning: apt-get not found; install PortAudio, ffmpeg, ALSA, and espeak packages manually if backend audio is needed." >&2
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

machine="$(uname -m 2>/dev/null || printf unknown)"
system="$(uname -s 2>/dev/null || printf unknown)"

install_system_packages

if [ ! -d ".venv" ]; then
    uv venv
fi

uv pip install -e .

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

if uv pip freeze | grep -Ei '^(nvidia|cuda|triton)' >/dev/null; then
    printf '%s\n' "Warning: GPU/CUDA packages are present in the environment:"
    uv pip freeze | grep -Ei '^(nvidia|cuda|triton)'
    printf '%s\n' "They are not required for LiveStageAssistant speaker recognition."
fi

printf '%s\n' "LiveStageAssistant install complete."
