from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BANNED = (
    "langchain_ollama",
    "ChatOllama",
    "LocalOllamaManager",
    "NativeOllama",
    "OLLAMA_",
    "OFFLINE_MODEL",
    "LLM_PROVIDER=",
    "qwen3:",
    "ollama serve",
    "ollama pull",
)
TEXT_SUFFIXES = {".py", ".toml", ".sh", ".ps1", ".json", ".js", ".html", ".css"}


def runtime_text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if any(part in {".git", ".venv", "node_modules", "__pycache__"} for part in relative.parts):
            continue
        if str(relative).startswith("assets/web/static/"):
            continue
        if str(relative).startswith("docs/"):
            continue  # Historical architecture decision record may mention the retired experiment.
        if str(relative).startswith("tests/"):
            continue  # Regression tests may intentionally assert that retired keys are absent.
        if relative.name in {"README.md", "AGENT.md"}:
            continue
        if path.suffix in TEXT_SUFFIXES or relative.name.startswith(".env"):
            yield path


def test_retired_local_llm_stack_is_absent_from_runtime_and_profiles():
    violations = []
    for path in runtime_text_files():
        active_text = path.read_text(encoding="utf-8")
        for token in BANNED:
            if token in active_text:
                violations.append(f"{path.relative_to(ROOT)}: {token}")
    assert violations == [], "Retired local-LLM artifacts remain:\n" + "\n".join(violations)


def test_local_cloud_gui_contract_has_no_retired_provider_selector_or_payload():
    html = (ROOT / "assets/web/index.html").read_text(encoding="utf-8")
    js = (ROOT / "assets/web/app.js").read_text(encoding="utf-8")

    assert '<option value="local" data-i18n="local_deterministic">Local déterministe</option>' in html
    assert '<option value="cloud" data-i18n="cloud">Cloud</option>' in html
    assert 'id="cloud-engine"' in html
    assert 'id="llm-provider"' not in html
    assert 'id="session-context-size-field"' in html
    assert 'id="mcp-agent-max-steps-field"' in html
    assert 'id="mcp-tool-routing-field"' in html
    assert 'id="cloud-prompt-section"' in html
    assert "llmProvider" not in js
    assert 'const engine = selectedVoiceEngine();' in js
    assert 'return cloudEngine?.value || "classic";' in js
    assert 'selectedVoiceEngine() === "openai-realtime"' in js
    assert 'for (const element of cloudAgentControls) element.classList.toggle("hidden", local);' in js
    assert "provider,\n            model," not in js
