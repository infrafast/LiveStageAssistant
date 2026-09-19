from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_retired_local_llm_stack_is_absent_from_runtime_and_profiles():
    paths = [
        ROOT / "pyproject.toml",
        ROOT / "scripts/install.sh",
        ROOT / "voice_assistant/agent.py",
        ROOT / "voice_assistant/classic_engine.py",
        ROOT / "voice_assistant/runtime.py",
        ROOT / "voice_assistant/runtime_web_services.py",
        ROOT / ".env.example",
        ROOT / ".env.online",
        ROOT / ".env.offline",
        ROOT / ".env.codespace",
        ROOT / ".env.localhttp",
        ROOT / ".env.tailscale",
        ROOT / "container/config/.env.infrafast",
        ROOT / "container/config/.env.localhost",
        ROOT / "container/config/.env.tailscaleHTTP",
        ROOT / "container/config/.env.tailscaleSTDIO",
        ROOT / "raspi_service_pack_stdio/.env.online",
        ROOT / "raspi_service_pack_stdio/.env.offline",
        ROOT / "scripts/install.ps1",
    ]
    banned = (
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
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{token!r} must not remain in {path.relative_to(ROOT)}"


def test_local_cloud_gui_contract_has_no_retired_provider_selector_or_payload():
    html = (ROOT / "assets/web/index.html").read_text(encoding="utf-8")
    js = (ROOT / "assets/web/app.js").read_text(encoding="utf-8")

    assert '<option value="local">Local déterministe</option>' in html
    assert '<option value="cloud">Cloud</option>' in html
    assert 'id="cloud-engine"' in html
    assert 'id="llm-provider"' not in html
    assert "llmProvider" not in js
    assert 'const engine = selectedVoiceEngine();' in js
    assert 'return cloudEngine?.value || "classic";' in js
    assert "provider,\n            model," not in js
