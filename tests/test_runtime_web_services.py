from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from voice_assistant.runtime_web_services import RuntimeWebServices


class DummyMonitor:
    def __init__(self):
        self.handlers = {}
        self.updated = []
        self.password = ""
        self.dialogue = []
        self.context = None

    def set_llm_config_handlers(self, **kwargs): self.handlers.update({"llm_options": kwargs["options_handler"], "llm_save": kwargs["save_handler"]})
    def set_cloud_api_status_handler(self, handler): self.handlers["cloud_status"] = handler
    def set_env_profile_handlers(self, **kwargs): self.handlers.update(kwargs)
    def set_remote_screen_handler(self, handler): self.handlers["remote"] = handler
    def set_mcp_routing_save_handler(self, handler): self.handlers["routing"] = handler
    def set_mcp_server_options_save_handler(self, handler): self.handlers["options"] = handler
    def set_session_context_handlers(self, **kwargs): self.handlers.update(kwargs)
    def set_backend_audio_sample_handler(self, handler): self.handlers["backend_audio_sample"] = handler
    def set_speaker_profile_sample_handler(self, handler): self.handlers["speaker_profile_sample"] = handler
    def set_backend_tts_test_handler(self, handler): self.handlers["backend_tts_test"] = handler
    def set_web_audio_handlers(self, **kwargs): self.handlers.update(kwargs)
    def set_backend_audio_diagnostic_handler(self, handler): self.handlers["backend_audio_diagnostic"] = handler
    def set_backend_speaker_capture_handlers(self, **kwargs): self.handlers.update(kwargs)
    def set_web_password(self, value): self.password = value
    def update(self, **kwargs): self.updated.append(kwargs)
    def replace_dialogue(self, messages): self.dialogue = list(messages)
    def set_context_state(self, snapshot, session_context_size=6000): self.context = (snapshot, session_context_size)


class RuntimeWebServicesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.context_dir = self.root / "contexts"
        self.mcp_file = self.root / "mcp.json"
        self.mcp_file.write_text(
            json.dumps({
                "mcpServers": {
                    "mixer": {"command": "node", "env": {"OLD": "1"}, "assistantOptions": {"routing": "mix"}},
                    "lights": {"command": "node", "assistantOptions": {"routing": "light"}},
                }
            }),
            encoding="utf-8",
        )
        self.online = self.root / ".env.online"
        self.offline = self.root / ".env.offline"
        common = (
            f"MCP_CONFIG={self.mcp_file}\n"
            f"SESSION_CONTEXT_DIR={self.context_dir}\n"
            "SESSION_CONTEXT_SIZE=4000\n"
            "WEB_PASSWORD=secret\n"
            "STT_LANGUAGE=fr\n"
            "BACKEND_AUDIO_INPUT_DEVICE=pipewire:source:test-input\n"
            "BACKEND_AUDIO_OUTPUT_DEVICE=pipewire:sink:test-output\n"
        )
        self.online.write_text(
            "CONNECTIVITY_MODE=online\n"
            "LLM_PROVIDER=openai\n"
            "OPENAI_MODEL=gpt-4.1-mini\n"
            "VOICE_ENGINE=openai-realtime\n"
            + common,
            encoding="utf-8",
        )
        self.offline.write_text(
            "CONNECTIVITY_MODE=offline\n"
            "LLM_PROVIDER=ollama\n"
            "OLLAMA_MODEL=mistral:7b-instruct-q4_K_M\n"
            + common,
            encoding="utf-8",
        )
        self.active = [self.online]
        self.monitor = DummyMonitor()
        self.services = RuntimeWebServices(
            monitor=self.monitor,
            active_profile=lambda: self.active[0],
            automatic_profiles=True,
        )
        self.services.bind()

    def tearDown(self):
        self.tmp.cleanup()

    def test_handlers_are_bound_without_engine_dependency(self):
        self.assertIn("llm_options", self.monitor.handlers)
        self.assertIn("llm_save", self.monitor.handlers)
        self.assertIn("cloud_status", self.monitor.handlers)
        self.assertIn("routing", self.monitor.handlers)
        self.assertIn("options", self.monitor.handlers)
        self.assertIn("list_handler", self.monitor.handlers)
        self.assertIn("new_handler", self.monitor.handlers)

    def test_llm_options_are_available_from_active_profile(self):
        result = self.services.llm_options()
        self.assertEqual(result["provider"], "openai")
        self.assertEqual(result["selected_model"], "gpt-4.1-mini")
        self.assertEqual(result["selected_connectivity_mode"], "online")
        self.assertEqual(result["selected_stt_language"], "fr")
        self.assertEqual(result["selected_backend_audio_input_device"], "pipewire:source:test-input")
        self.assertEqual(result["selected_backend_audio_output_device"], "pipewire:sink:test-output")
        self.assertTrue(result["models"])

        self.active[0] = self.offline
        with mock.patch("voice_assistant.runtime_web_services.urllib.request.urlopen", side_effect=OSError("offline")):
            offline = self.services.llm_options("openai")
        self.assertEqual(offline["provider"], "ollama")
        self.assertEqual(offline["selected_model"], "mistral:7b-instruct-q4_K_M")
        self.assertEqual(offline["selected_connectivity_mode"], "offline")
        self.assertEqual(offline["models"][0]["id"], "mistral:7b-instruct-q4_K_M")
        self.assertIn("configured", offline["models"][0]["label"])

    def test_ollama_live_models_are_listed_from_api(self):
        self.active[0] = self.offline
        payload = json.dumps({
            "models": [
                {"name": "qwen3:8b"},
                {"name": "mistral:7b-instruct-q4_K_M"},
            ]
        }).encode("utf-8")

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return payload

        with mock.patch("voice_assistant.runtime_web_services.urllib.request.urlopen", return_value=Response()):
            result = self.services.llm_options()

        self.assertEqual(
            [item["id"] for item in result["models"]],
            ["mistral:7b-instruct-q4_K_M", "qwen3:8b"],
        )
        self.assertIn("Common runtime options loaded from active profile", result["message"])

    def test_offline_save_uses_existing_model_when_ui_model_is_empty(self):
        self.active[0] = self.offline

        result = self.services.save_llm_config(
            provider="ollama",
            model="",
            cloud_tts_provider="none",
            tts_output="backend",
            stt_input="backend",
            stt_language="fr",
            connectivity_mode="offline",
            wake_word="momo",
            stt_prompt="",
            system_prompt="",
            session_context_size=4000,
            mcp_agent_max_steps=20,
            mcp_tool_routing_enabled=False,
            interrupt_conversation_enabled=False,
            backend_audio_input_device="",
            backend_audio_input_gain=1.0,
            backend_audio_output_device="",
            voice_id="",
            thinking_sound_file="",
            ready_sound_file="",
            listening_sound_file="",
            wake_detected_sound_file="",
            startup_loader_sound_file="",
            command_ack_sound_file="",
            openai_tts_voice="alloy",
            openai_tts_speed=1.0,
            web_tts_volume=1.0,
            backend_tts_volume=1.0,
            backend_audio_output_pan=0.0,
            backend_audio_monitor_mode="off",
            backend_audio_monitor_volume=1.0,
            vad_speech_threshold=0.5,
            vad_negative_threshold=0.35,
            vad_min_speech_ms=120,
            vad_min_silence_ms=650,
            vad_speech_pad_ms=100,
            vad_max_speech_seconds=8.0,
            backend_wake_word_model_paths="data/wake_words/momo.onnx",
            backend_wake_word_model_names="",
            backend_wake_word_threshold=0.65,
            backend_wake_word_pre_roll_ms=1600,
            backend_wake_word_cooldown_ms=1200,
            backend_wake_word_vad_threshold=0.0,
            speaker_recognition_enabled=False,
            speaker_backend="resemblyzer",
            speaker_threshold=0.75,
            speaker_margin=0.10,
            speaker_profiles=[],
            voice_engine="local",
            realtime_model="",
            realtime_voice="",
            cloud_tts_output_gain=1.0,
            local_tts_output_gain=1.0,
        )

        self.assertEqual(result["model"], "mistral:7b-instruct-q4_K_M")
        saved = self.offline.read_text(encoding="utf-8")
        self.assertIn("WAKE_WORD=momo", saved)
        self.assertIn("OFFLINE_MODEL=mistral:7b-instruct-q4_K_M", saved)

    def test_auto_profile_list_is_locked_to_connectivity(self):
        result = self.services.list_env_profiles()
        self.assertTrue(result["auto_mode"])
        self.assertFalse(result["switching_enabled"])
        self.assertEqual(len(result["profiles"]), 2)
        with self.assertRaisesRegex(ValueError, "manual env switching is disabled"):
            self.services.switch_env_profile(str(self.offline))

    def test_mcp_routing_and_options_persist_atomically(self):
        routing = self.services.save_mcp_routing({"mixer": "mix, console", "lights": "light"})
        self.assertTrue(routing["restart_required"])
        options = self.services.save_mcp_server_options({"mixer": {"CHANNELS": 16, "FLAGS": ["a", "b"]}})
        self.assertTrue(options["restart_required"])
        payload = json.loads(self.mcp_file.read_text(encoding="utf-8"))
        self.assertEqual(payload["mcpServers"]["mixer"]["assistantOptions"]["routing"], "mix,console")
        self.assertEqual(payload["mcpServers"]["mixer"]["env"]["CHANNELS"], "16")
        self.assertEqual(payload["mcpServers"]["mixer"]["env"]["FLAGS"], '["a","b"]')

    def test_remote_screen_and_sessions_are_common_services(self):
        saved = self.services.save_remote_screen("vnc://192.0.2.10:5900", True)
        self.assertTrue(saved["saved"])
        self.assertIn("REMOTE_SCREEN_VNC_URL=vnc://192.0.2.10:5900", self.online.read_text(encoding="utf-8"))

        created = self.services.new_session("Runtime session")
        session_id = created["active_id"]
        self.assertTrue(session_id)
        renamed = self.services.rename_session(session_id, "Renamed")
        self.assertEqual(renamed["current"]["title"], "Renamed")
        cleared = self.services.clear_session(session_id)
        self.assertEqual(cleared["messages"], [])


if __name__ == "__main__":
    unittest.main()
