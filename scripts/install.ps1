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
            $ollamaList = ollama list
            $ollamaListOk = $true
        } catch {
            Write-Host "Ollama is installed but not running. Start it, then pull $OllamaModel for offline mode."
        }
        if ($ollamaListOk) {
            $hasModel = $ollamaList | Select-String -Pattern ("^" + [regex]::Escape($OllamaModel) + "\s")
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

uv pip install -e .

Write-Host "Installing speaker recognition dependencies for Windows."
uv pip install -e ".[speaker]"
uv pip install torch --index-url https://download.pytorch.org/whl/cpu
uv pip install resemblyzer --no-deps

$gpuPackages = uv pip freeze | Select-String -Pattern "^(nvidia|cuda|triton)" -CaseSensitive:$false
if ($gpuPackages) {
    Write-Host "Warning: GPU/CUDA packages are present in the environment:"
    $gpuPackages | ForEach-Object { Write-Host $_.Line }
    Write-Host "They are not required for LiveStageAssistant speaker recognition."
}

Write-Host "LiveStageAssistant install complete."
