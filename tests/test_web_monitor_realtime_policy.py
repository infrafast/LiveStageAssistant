from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib import request as urllib_request

from voice_assistant.runtime_web_services import RuntimeWebServices
from voice_assistant.web_monitor import WebMonitor, _runtime_service_tiles


class WebMonitorRealtimePolicyRouteTests(unittest.TestCase):
    def _save_runtime_config(
        self,
        services: RuntimeWebServices,
        *,
        voice_engine: str | None = None,
        cloud_gain: float = 1.0,
        local_gain: float = 1.0,
        connectivity: str = "online",
    ) -> dict:
        provider = "ollama" if connectivity == "offline" else "openai"
        model = "qwen3:8b" if connectivity == "offline" else "gpt-4.1-mini"
        selected_voice_engine = voice_engine or ("local" if connectivity == "offline" else "classic")
        return services.save_llm_config(
            provider=provider,
            model=model,
            cloud_tts_provider="none",
            tts_output="silent",
            stt_input="backend",
            stt_language="fr",
            connectivity_mode=connectivity,
            wake_word="",
            stt_prompt="",
            system_prompt="",
            session_context_size=4000,
            mcp_agent_max_steps=20,
            mcp_tool_routing_enabled=True,
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
            vad_min_silence_ms=900,
            vad_speech_pad_ms=100,
            vad_max_speech_seconds=12,
            backend_wake_word_model_paths="",
            backend_wake_word_model_names="",
            backend_wake_word_threshold=0.7,
            backend_wake_word_pre_roll_ms=1600,
            backend_wake_word_cooldown_ms=1200,
            backend_wake_word_vad_threshold=0.0,
            speaker_recognition_enabled=False,
            speaker_backend="resemblyzer",
            speaker_threshold=0.75,
            speaker_margin=0.06,
            speaker_profiles=[],
            voice_engine=selected_voice_engine,
            realtime_model="gpt-realtime-2.1",
            realtime_voice="marin",
            cloud_tts_output_gain=cloud_gain,
            local_tts_output_gain=local_gain,
        )

    def test_runtime_service_tiles_are_provider_and_mcp_neutral(self) -> None:
        tiles = _runtime_service_tiles({
            "engine": "openai-realtime", "provider": "openai", "model": "gpt-realtime-2.1", "voice": "marin", "ready": True,
            "mcp": [{"name": "alpha", "configured_transport": "auto", "effective_transport": "stdio", "permission": "open", "healthy": True, "detail": "selected and healthy"}],
        })
        self.assertEqual(tiles["Voice engine"]["status"], "ready")
        self.assertIn("transport=stdio", tiles["MCP · alpha"]["detail"])
        self.assertIn("configured=auto", tiles["MCP · alpha"]["detail"])

    def test_native_transport_is_presented_as_https(self) -> None:
        tiles = _runtime_service_tiles({"engine": "openai-realtime", "ready": True, "mcp": [{"name": "alpha", "configured_transport": "native", "effective_transport": "native", "permission": "open", "healthy": True}]})
        self.assertIn("transport=https", tiles["MCP · alpha"]["detail"])
        self.assertNotIn("transport=native", tiles["MCP · alpha"]["detail"])

    def test_snapshot_includes_runtime_status_tiles_when_status_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status_path = Path(temp_dir) / "runtime-status.json"
            status_path.write_text(json.dumps({"engine": "classic", "provider": "openai", "model": "gpt-4.1-mini", "ready": True, "mcp": []}), encoding="utf-8")
            with patch.dict(os.environ, {"LSA_RUNTIME_STATUS_FILE": str(status_path)}):
                snapshot = WebMonitor().snapshot()
            self.assertEqual(snapshot["runtime_status"]["engine"], "classic")
            self.assertEqual(snapshot["services"]["Voice engine"]["status"], "ready")

    def test_runtime_http_omits_cross_origin_isolation_headers(self) -> None:
        monitor = WebMonitor(); host, port = monitor.start("127.0.0.1", 0)
        try:
            with urllib_request.urlopen(f"http://{host}:{port}/", timeout=2) as response:
                headers = response.headers; response.read()
        finally:
            monitor.stop()
        self.assertIsNone(headers.get("Cross-Origin-Opener-Policy"))
        self.assertIsNone(headers.get("Cross-Origin-Embedder-Policy"))

    def test_voice_engine_persists_to_canonical_profile_and_requires_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env.online"
            env_path.write_text("CONNECTIVITY_MODE=online\nVOICE_ENGINE=classic\n", encoding="utf-8")
            monitor = WebMonitor()
            services = RuntimeWebServices(
                monitor=monitor,
                active_profile=lambda: env_path,
                automatic_profiles=True,
            )
            with patch.dict(os.environ, {"ASSISTANT_AUTO_ENV_DIR": temp_dir}):
                result = self._save_runtime_config(services, voice_engine="openai-realtime")
            saved = env_path.read_text(encoding="utf-8")
            self.assertIn("VOICE_ENGINE=openai-realtime", saved)
            self.assertEqual(result["profile"], str(env_path))
            self.assertTrue(result["restart_required"])

    def test_voice_output_gains_are_persisted_to_online_and_offline_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            online = Path(temp_dir) / ".env.online"; offline = Path(temp_dir) / ".env.offline"
            online.write_text("CONNECTIVITY_MODE=online\n", encoding="utf-8"); offline.write_text("CONNECTIVITY_MODE=offline\n", encoding="utf-8")
            active = [online]
            monitor = WebMonitor()
            services = RuntimeWebServices(
                monitor=monitor,
                active_profile=lambda: active[0],
                automatic_profiles=True,
            )
            with patch.dict(os.environ, {"ASSISTANT_AUTO_ENV_DIR": temp_dir}):
                result = self._save_runtime_config(services, cloud_gain=1.35, local_gain=0.80, connectivity="online")
                active[0] = offline
                self._save_runtime_config(services, cloud_gain=1.35, local_gain=0.80, connectivity="offline")
            self.assertTrue(result["restart_required"])
            self.assertIn("CLOUD_TTS_OUTPUT_GAIN=1.35", online.read_text(encoding="utf-8"))
            self.assertIn("LOCAL_TTS_OUTPUT_GAIN=0.80", offline.read_text(encoding="utf-8"))

    def test_mcp_crud_route_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "mcp.json"
            config_path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
            monitor = WebMonitor(); monitor.update(env_file=Path(temp_dir) / ".env.online", env_values={"MCP_CONFIG": str(config_path)}, mcp_config={"mcpServers": {}})
            host, port = monitor.start("127.0.0.1", 0)
            try:
                create = json.dumps({"action": "create", "server": {"name": "alpha", "command": "node", "args": '["server.js"]', "realtime_transport": "stdio", "permission_mode": "open"}}).encode()
                with urllib_request.urlopen(urllib_request.Request(f"http://{host}:{port}/api/mcp-server", data=create, headers={"Content-Type": "application/json"}, method="POST"), timeout=2) as response:
                    result = json.loads(response.read().decode())
                self.assertTrue(result["ok"]); self.assertTrue(result["restart_required"])
                self.assertIn("alpha", json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"])
                delete = json.dumps({"action": "delete", "server": "alpha"}).encode()
                with urllib_request.urlopen(urllib_request.Request(f"http://{host}:{port}/api/mcp-server", data=delete, headers={"Content-Type": "application/json"}, method="POST"), timeout=2) as response:
                    deleted = json.loads(response.read().decode())
                self.assertEqual(deleted["deleted"], "alpha")
            finally:
                monitor.stop()

    def test_policy_route_uses_https_vocabulary_and_preserves_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "mcp.json"
            config_path.write_text(json.dumps({"mcpServers": {"alpha": {"command": "node", "native": {"url": "https://old.example/mcp", "headers": {"Authorization": "Bearer keep-secret"}}, "realtime": {"transport": "stdio", "permissions": {"mode": "open"}}}}}), encoding="utf-8")
            initial = json.loads(config_path.read_text(encoding="utf-8")); monitor = WebMonitor(); monitor.update(env_values={"MCP_CONFIG": str(config_path)}, mcp_config=initial)
            host, port = monitor.start("127.0.0.1", 0)
            try:
                payload = json.dumps({"server": "alpha", "policy": {"realtime_transport": "https", "permission_mode": "approval", "https_url": "https://new.example/mcp"}}).encode()
                with urllib_request.urlopen(urllib_request.Request(f"http://{host}:{port}/api/mcp-realtime-policy", data=payload, headers={"Content-Type": "application/json"}, method="POST"), timeout=2) as response:
                    result = json.loads(response.read().decode())
            finally:
                monitor.stop()
            self.assertEqual(result["policy"]["realtime_transport"], "https")
            saved = json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["alpha"]
            self.assertEqual(saved["realtime"]["transport"], "native")
            self.assertEqual(saved["native"]["headers"]["Authorization"], "Bearer keep-secret")


if __name__ == "__main__":
    unittest.main()
