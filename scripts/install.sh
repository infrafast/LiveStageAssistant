#!/usr/bin/env sh
set -eu

repo_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$repo_dir"

uv_cache_dir="${UV_CACHE_DIR:-/tmp/uv-cache}"
export UV_CACHE_DIR="$uv_cache_dir"

if [ ! -d ".venv" ]; then
    uv venv
fi

machine="$(uname -m 2>/dev/null || printf unknown)"
system="$(uname -s 2>/dev/null || printf unknown)"
speaker_mode="${INSTALL_SPEAKER_RECOGNITION:-auto}"

uv pip install -e .

case "$machine" in
    aarch64|armv7l|armv6l)
        if [ "$speaker_mode" != "1" ] && [ "$speaker_mode" != "true" ]; then
            printf '%s\n' "Speaker recognition dependencies skipped on ARM (${machine})."
            printf '%s\n' "On Raspberry Pi, install CPU Torch first, then run:"
            printf '%s\n' "  INSTALL_SPEAKER_RECOGNITION=1 ./scripts/install.sh"
            exit 0
        fi
        ;;
esac

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
