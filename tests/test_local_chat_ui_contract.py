from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_webgui_text_command_reaches_common_voiceassistant_process_command_path():
    app = (ROOT / "assets/web/app.js").read_text(encoding="utf-8")
    monitor = (ROOT / "voice_assistant/web_monitor_base.py").read_text(encoding="utf-8")
    agent = (ROOT / "voice_assistant/agent.py").read_text(encoding="utf-8")
    local = (ROOT / "voice_assistant/local_engine.py").read_text(encoding="utf-8")

    assert 'fetch(apiUrl("/api/inject-command")' in app
    assert 'if self.path == "/api/inject-command":' in monitor
    assert "monitor.inject_command(" in monitor
    assert "text = self.pending_injected_command" in agent
    assert "text = self.web_monitor.pop_injected_command()" in agent
    assert "asyncio.create_task(self.process_command(text, speaker_result=speaker_result))" in agent
    assert "class DeterministicLocalVoiceAssistant(agent.VoiceAssistant)" in local
    assert "return await orchestrator.handle(" in local


def test_webgui_manual_speaker_context_is_preserved_for_local_gateway():
    app = (ROOT / "assets/web/app.js").read_text(encoding="utf-8")
    monitor = (ROOT / "voice_assistant/web_monitor_base.py").read_text(encoding="utf-8")
    local = (ROOT / "voice_assistant/local_engine.py").read_text(encoding="utf-8")

    assert "speaker_context_explicit" in app
    assert 'speaker = str(payload.get("speaker") or "unknown")' in monitor
    assert 'command_context["speaker"]' in local
    assert "context=command_context or None" in local


def test_typed_duplicate_commands_are_not_suppressed():
    from voice_assistant.agent import VoiceAssistant

    assistant = object.__new__(VoiceAssistant)
    assistant.last_processed_command_key = None
    assistant.last_processed_command_at = 0.0

    assert assistant._should_skip_duplicate_command("remets anto", suppress=False) is False
    assert assistant._should_skip_duplicate_command("remets anto", suppress=False) is False


def test_audio_duplicate_suppression_is_preserved():
    from voice_assistant.agent import VoiceAssistant

    assistant = object.__new__(VoiceAssistant)
    assistant.last_processed_command_key = None
    assistant.last_processed_command_at = 0.0

    assert assistant._should_skip_duplicate_command("remets anto", suppress=True) is False
    assert assistant._should_skip_duplicate_command("remets anto", suppress=True) is True


def test_duplicate_skip_path_releases_webgui_busy_state():
    agent = (ROOT / "voice_assistant/agent.py").read_text(encoding="utf-8")
    duplicate_block = agent.split('print(f"Duplicate command ignored: {text}")', 1)[1].split("continue", 1)[0]
    assert "self.web_monitor.set_assistant_busy(False)" in duplicate_block
