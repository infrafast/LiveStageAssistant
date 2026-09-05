import unittest
from types import SimpleNamespace

from voice_assistant.realtime.mcp_bridge import RealtimeMCPBridge


class FakeSession:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        return [
            SimpleNamespace(
                name="read_main",
                description="Read main level",
                inputSchema={"type": "object", "properties": {"target": {"type": "string"}}},
            ),
            SimpleNamespace(
                name="set_main",
                description="Set main level",
                inputSchema={"type": "object", "properties": {"db": {"type": "number"}}, "required": ["db"]},
            ),
        ]

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(type="text", text=f"{name}:{arguments}")],
            structuredContent={"ok": True},
        )


class FakeClient:
    def __init__(self):
        self.sessions = {}
        self.closed = False

    async def create_session(self, server_name):
        self.sessions[server_name] = FakeSession()

    def get_session(self, server_name):
        return self.sessions[server_name]

    async def close_all_sessions(self):
        self.closed = True


class RealtimeMCPBridgeTests(unittest.IsolatedAsyncioTestCase):
    def config(self):
        return {"mcpServers": {"mixer": {"command": "node", "args": ["server.js"]}}}

    async def test_discovers_functions_and_maps_back_to_mcp_target(self):
        client = FakeClient()
        bridge = RealtimeMCPBridge(self.config(), server_names=("mixer",), client=client)
        functions = await bridge.start()
        self.assertEqual(len(functions), 2)
        self.assertTrue(all(function.name.startswith("mcp__mixer__") for function in functions))
        targets = bridge.tool_targets
        self.assertEqual({target.tool for target in targets.values()}, {"read_main", "set_main"})
        self.assertEqual(functions[0].parameters["type"], "object")

    async def test_restricted_allow_list_filters_discovery(self):
        bridge = RealtimeMCPBridge(
            self.config(),
            server_names=("mixer",),
            allowed_tools={"mixer": {"read_main"}},
            client=FakeClient(),
        )
        functions = await bridge.start()
        self.assertEqual(len(functions), 1)
        self.assertEqual(next(iter(bridge.tool_targets.values())).tool, "read_main")

    async def test_execute_calls_existing_session_and_serializes_result(self):
        client = FakeClient()
        bridge = RealtimeMCPBridge(self.config(), server_names=("mixer",), client=client)
        await bridge.start()
        exposed_name = next(
            name for name, target in bridge.tool_targets.items() if target.tool == "read_main"
        )
        result = await bridge.execute(exposed_name, '{"target":"main"}')
        self.assertEqual(result["transport"], "stdio/bridge")
        self.assertEqual(result["server"], "mixer")
        self.assertEqual(result["tool"], "read_main")
        self.assertFalse(result["is_error"])
        self.assertEqual(result["structured_content"], {"ok": True})
        self.assertEqual(client.sessions["mixer"].calls, [("read_main", {"target": "main"})])

    async def test_external_client_is_not_closed_by_bridge(self):
        client = FakeClient()
        bridge = RealtimeMCPBridge(self.config(), server_names=("mixer",), client=client)
        await bridge.start()
        await bridge.close()
        self.assertFalse(client.closed)


if __name__ == "__main__":
    unittest.main()
