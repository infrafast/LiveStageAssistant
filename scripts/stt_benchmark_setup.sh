#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BENCH_ROOT="$ROOT/.stt-benchmark"
WHISPER_DIR="$BENCH_ROOT/whisper.cpp"
SHERPA_VENV="$BENCH_ROOT/sherpa-venv"
TOOLS_VENV="$BENCH_ROOT/build-tools-venv"
MODE="menu"
if [ "$#" -ge 1 ]; then
  MODE="$1"
fi

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing prerequisite: $1"
    exit 2
  }
}

bootstrap_python() {
  if [ -x "$ROOT/.venv/bin/python" ]; then
    echo "$ROOT/.venv/bin/python"
    return
  fi
  command -v python3 >/dev/null 2>&1 || {
    echo "Missing prerequisite: python3" >&2
    exit 2
  }
  command -v python3
}

setup_build_tools() {
  mkdir -p "$BENCH_ROOT"
  BOOTSTRAP_PYTHON="$(bootstrap_python)"

  if [ ! -x "$TOOLS_VENV/bin/python" ]; then
    echo "Creating isolated benchmark build-tools virtualenv..."
    "$BOOTSTRAP_PYTHON" -m venv "$TOOLS_VENV"
  fi

  if [ ! -x "$TOOLS_VENV/bin/cmake" ] || [ ! -x "$TOOLS_VENV/bin/ninja" ]; then
    echo "Installing CMake + Ninja only inside .stt-benchmark..."
    "$TOOLS_VENV/bin/python" -m pip install --upgrade pip
    "$TOOLS_VENV/bin/python" -m pip install --upgrade cmake ninja
  fi
}

setup_whisper() {
  need git
  need bash
  need cc
  need c++

  setup_build_tools
  CMAKE="$TOOLS_VENV/bin/cmake"
  NINJA="$TOOLS_VENV/bin/ninja"

  mkdir -p "$BENCH_ROOT"

  if [ ! -d "$WHISPER_DIR/.git" ]; then
    echo "Cloning whisper.cpp into isolated benchmark directory..."
    git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git "$WHISPER_DIR"
  else
    echo "Keeping existing whisper.cpp checkout:"
    git -C "$WHISPER_DIR" rev-parse --short HEAD
  fi

  echo "Building whisper.cpp for the local CPU..."
  "$CMAKE" -S "$WHISPER_DIR" -B "$WHISPER_DIR/build" -G Ninja \
    -DCMAKE_MAKE_PROGRAM="$NINJA" \
    -DCMAKE_BUILD_TYPE=Release \
    -DGGML_NATIVE=ON \
    -DWHISPER_BUILD_EXAMPLES=ON \
    -DWHISPER_BUILD_SERVER=ON \
    -DWHISPER_BUILD_TESTS=OFF

  "$CMAKE" --build "$WHISPER_DIR/build" --parallel 4 \
    --target whisper-server whisper-cli whisper-quantize

  cd "$WHISPER_DIR"

  if [ ! -f models/ggml-base.bin ]; then
    echo "Downloading multilingual Whisper base..."
    bash models/download-ggml-model.sh base
  fi
  if [ ! -f models/ggml-small.bin ]; then
    echo "Downloading multilingual Whisper small..."
    bash models/download-ggml-model.sh small
  fi

  if [ ! -f models/ggml-base-q5_0.bin ]; then
    echo "Quantizing base -> q5_0..."
    build/bin/whisper-quantize models/ggml-base.bin models/ggml-base-q5_0.bin q5_0
  fi
  if [ ! -f models/ggml-small-q5_0.bin ]; then
    echo "Quantizing small -> q5_0..."
    build/bin/whisper-quantize models/ggml-small.bin models/ggml-small-q5_0.bin q5_0
  fi

  cd "$ROOT"
  echo "whisper.cpp ready."
}

download() {
  url="$1"
  output="$2"
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 -o "$output" "$url"
  elif command -v wget >/dev/null 2>&1; then
    wget -O "$output" "$url"
  else
    echo "curl or wget is required for model download."
    exit 2
  fi
}

setup_sherpa() {
  need tar
  mkdir -p "$BENCH_ROOT/models"
  BOOTSTRAP_PYTHON="$(bootstrap_python)"

  if [ ! -x "$SHERPA_VENV/bin/python" ]; then
    echo "Creating isolated sherpa virtualenv..."
    "$BOOTSTRAP_PYTHON" -m venv "$SHERPA_VENV"
  fi

  echo "Installing sherpa-onnx only inside benchmark virtualenv..."
  "$SHERPA_VENV/bin/python" -m pip install --upgrade pip
  "$SHERPA_VENV/bin/python" -m pip install --upgrade numpy sherpa-onnx

  MODEL_NAME="sherpa-onnx-streaming-zipformer-fr-2023-04-14"
  MODEL_DIR="$BENCH_ROOT/models/$MODEL_NAME"
  ARCHIVE="$BENCH_ROOT/models/$MODEL_NAME.tar.bz2"
  URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$MODEL_NAME.tar.bz2"

  if [ ! -d "$MODEL_DIR" ]; then
    if [ ! -f "$ARCHIVE" ]; then
      echo "Downloading French streaming Zipformer model..."
      download "$URL" "$ARCHIVE"
    fi
    echo "Extracting French streaming Zipformer model..."
    tar -xjf "$ARCHIVE" -C "$BENCH_ROOT/models"
  fi

  # The historical French inference archive contains tokens + ONNX files but
  # not the SentencePiece vocabulary needed by sherpa's contextual hotword
  # encoder. Fetch only the matching 500-piece vocabulary from the original
  # training model; it remains benchmark-only inside .stt-benchmark.
  BPE_VOCAB="$MODEL_DIR/unigram_500.vocab"
  BPE_VOCAB_URL="https://huggingface.co/shaojieli/icefall-asr-commonvoice-fr-pruned-transducer-stateless7-streaming-2023-04-02/resolve/main/data/lang_bpe_500/unigram_500.vocab?download=true"
  if [ ! -f "$BPE_VOCAB" ]; then
    echo "Downloading French SentencePiece vocabulary for Sherpa hotword benchmark..."
    download "$BPE_VOCAB_URL" "$BPE_VOCAB"
  fi

  echo "sherpa-onnx ready."
}

show_menu() {
  cat <<'EOF'

LSA STT benchmark setup
This setup is isolated under .stt-benchmark and never modifies the production .venv.
CMake and Ninja are installed locally in .stt-benchmark/build-tools-venv when needed.

1) Prepare whisper.cpp only
2) Prepare sherpa-onnx French streaming only
3) Prepare both
q) Quit
EOF
  read -r -p "Choice: " choice
  case "$choice" in
    1) setup_whisper ;;
    2) setup_sherpa ;;
    3) setup_whisper; setup_sherpa ;;
    q|Q) exit 0 ;;
    *) echo "Unknown choice"; exit 2 ;;
  esac
}

case "$MODE" in
  whisper|whisper.cpp) setup_whisper ;;
  sherpa|sherpa-onnx) setup_sherpa ;;
  all) setup_whisper; setup_sherpa ;;
  menu) show_menu ;;
  *)
    echo "Usage: bash scripts/stt_benchmark_setup.sh [menu|whisper|sherpa|all]"
    exit 2
    ;;
esac

echo
echo "Benchmark runtime directory: $BENCH_ROOT"
echo "No production service, env profile, MCP config, or .venv was modified."
