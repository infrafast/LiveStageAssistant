Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoDir

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install it first with: pip install uv"
}

if (-not $env:UV_LINK_MODE) {
    $env:UV_LINK_MODE = "copy"
}

$OllamaModel = if ($env:LSA_OLLAMA_MODEL) { $env:LSA_OLLAMA_MODEL } else { "qwen3:8b" }
if ($env:LSA_SKIP_OLLAMA -eq "1") {
    Write-Host "Skipping Ollama setup because LSA_SKIP_OLLAMA=1."
} else {
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($winget) {
            Write-Host "Installing Ollama with winget for local/offline mode."
            winget install --id Ollama.Ollama -e
        } else {
            Write-Warning "Ollama was not found and winget is unavailable. Install Ollama manually for offline mode."
        }
    }
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        $ollamaListOk = $false
        try {
            ollama list | Out-Null
            $ollamaListOk = $true
        } catch {
            Write-Host "Ollama is installed but not running. Start it, then pull $OllamaModel for offline mode."
        }
        if ($ollamaListOk) {
            $hasModel = $false
            try {
                ollama show $OllamaModel | Out-Null
                $hasModel = $true
            } catch {
                $hasModel = $false
            }
            if ($hasModel) {
                Write-Host "Ollama model $OllamaModel is already available."
            } else {
                Write-Host "Pulling Ollama model $OllamaModel for local/offline mode."
                ollama pull $OllamaModel
            }
        }
    }
}

if (-not (Test-Path ".venv")) {
    uv venv
}

# Editable install includes both Classic and voice_assistant.realtime.
uv pip install -e .
uv pip install "mcp-use>=1.7.0,<2.0.0" "mcp>=1.24.0,<2.0.0"

if ($env:LSA_SKIP_PIPER -eq "1") {
    Write-Host "Skipping Piper setup because LSA_SKIP_PIPER=1."
} else {
    $PiperVoice = if ($env:LSA_PIPER_VOICE) { $env:LSA_PIPER_VOICE } else { "fr_FR-siwis-medium" }
    $PiperDataDir = if ($env:LSA_PIPER_DATA_DIR) { $env:LSA_PIPER_DATA_DIR } else { Join-Path $RepoDir "data\piper" }
    New-Item -ItemType Directory -Force -Path $PiperDataDir | Out-Null
    Write-Host "Installing/verifying Piper local TTS."
    uv pip install "piper-tts>=1.4,<2"
    $PiperModel = Join-Path $PiperDataDir "$PiperVoice.onnx"
    $PiperConfig = "$PiperModel.json"
    if ((Test-Path $PiperModel) -and (Test-Path $PiperConfig)) {
        Write-Host "Piper voice $PiperVoice is already available."
    } else {
        Write-Host "Downloading default French Piper voice $PiperVoice."
        uv run python -m piper.download_voices --data-dir $PiperDataDir $PiperVoice
    }
}

if ($env:LSA_SKIP_WAKEWORD -eq "1") {
    Write-Host "Skipping openWakeWord dependencies because LSA_SKIP_WAKEWORD=1."
} else {
    Write-Host "Installing local wake-word detection dependencies."
    uv pip install -e ".[wakeword]"
}

Write-Host "Installing speaker recognition dependencies for Windows."
uv pip install -e ".[speaker]"
uv pip install torch --index-url https://download.pytorch.org/whl/cpu
uv pip install resemblyzer --no-deps
uv pip uninstall typing | Out-Null

uv run python -c "from importlib import metadata; import voice_assistant.realtime; from piper import PiperVoice; from mcp.shared.context import RequestContext; from mcp_use import MCPAgent, MCPClient; from resemblyzer import VoiceEncoder, preprocess_wav; print('Realtime voice package OK: voice_assistant.realtime'); print('Piper dependency OK: piper-tts ' + metadata.version('piper-tts')); print('MCP dependencies OK: mcp-use ' + metadata.version('mcp-use') + ', mcp ' + metadata.version('mcp')); print('Speaker recognition dependencies OK: resemblyzer ' + metadata.version('resemblyzer'));`ntry:`n    print('Wake-word dependencies OK: openwakeword ' + metadata.version('openwakeword'))`nexcept metadata.PackageNotFoundError:`n    print('Wake-word dependencies skipped: openwakeword is not installed')"

$gpuPackages = uv pip freeze | Select-String -Pattern "^(nvidia|cuda|triton)" -CaseSensitive:$false
if ($gpuPackages) {
    Write-Host "Warning: GPU/CUDA packages are present in the environment:"
    $gpuPackages | ForEach-Object { Write-Host $_.Line }
    Write-Host "They are not required for LiveStageAssistant speaker recognition."
}

Write-Host "LiveStageAssistant install complete."
