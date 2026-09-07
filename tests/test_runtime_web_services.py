from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from voice_assistant.runtime_web_services import RuntimeWebServices


class DummyMonitor:
    def __init__(self):
        self.handlers = {}
        self.updated = []
        self.password = ""
        self.dialogue = []
        self.context = None

    def set_env_profile_handlers(self, **kwargs): self.handlers.update(kwargs)
    def set_remote_screen_handler(self, handler): self.handlers["remote"] = handler
    def set_mcp_routing_save_handler(self, handler): self.handlers["routing"] = handler
    def set_mcp_server_options_save_handler(self, handler): self.handlers["options"] = handler
    def set_session_context_handlers(self, **kwargs): self.handlers.update(kwargs)
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
        common = f"MCP_CONFIG={self.mcp_file}\nSESSION_CONTEXT_DIR={self.context_dir}\nSESSION_CONTEXT_SIZE=4000\nWEB_PASSWORD=secret\n"
        self.online.write_text("CONNECTIVITY_MODE=online\n" + common, encoding="utf-8")
        self.offline.write_text("CONNECTIVITY_MODE=offline\n" + common, encoding="utf-8")
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
        self.assertIn("routing", self.monitor.handlers)
        self.assertIn("options", self.monitor.handlers)
        self.assertIn("list_handler", self.monitor.handlers)
        self.assertIn("new_handler", self.monitor.handlers)

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
