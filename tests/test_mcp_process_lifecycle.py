from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class McpProcessLifecycleTests(unittest.TestCase):
    def test_agent_closes_mcp_sessions_on_reload_and_shutdown(self):
        source = (ROOT / "voice_assistant" / "agent.py").read_text(encoding="utf-8")
        self.assertNotIn("MCP cleanup deferred for reload.", source)
        self.assertIn("close_all_sessions(), timeout=6.0", source)
        self.assertIn("signal.signal(signal.SIGTERM, request_force_exit)", source)

    def test_systemd_kills_the_entire_service_control_group(self):
        service = (ROOT / "raspi_service_pack_stdio" / "livestageassistant.service").read_text(encoding="utf-8")
        self.assertIn("KillMode=control-group", service)


if __name__ == "__main__":
    unittest.main()
