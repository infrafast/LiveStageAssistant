from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class McpProcessLifecycleTests(unittest.TestCase):
    def test_common_runtime_owns_shutdown_and_agent_closes_mcp_sessions(self):
        agent_source = (ROOT / "voice_assistant" / "agent.py").read_text(encoding="utf-8")
        runtime_source = (ROOT / "voice_assistant" / "runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("MCP cleanup deferred for reload.", agent_source)
        self.assertIn("close_all_sessions(), timeout=6.0", agent_source)
        self.assertIn("signal.signal(sig, request_stop)", runtime_source)
        self.assertIn("from voice_assistant.runtime import main as runtime_main", agent_source)

    def test_systemd_kills_the_entire_service_control_group(self):
        service = (ROOT / "raspi_service_pack_stdio" / "livestageassistant.service").read_text(encoding="utf-8")
        self.assertIn("KillMode=control-group", service)


if __name__ == "__main__":
    unittest.main()
