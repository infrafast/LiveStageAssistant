Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoDir

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install it first with: pip install uv"
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
