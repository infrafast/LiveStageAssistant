import unittest
from types import SimpleNamespace

from voice_assistant.realtime.mcp_bridge import RealtimeMCPBridge, load_mcp_prompt_from_session


class FakeSession:
    def __init__(self):
        self.calls = []

    async def list_prompts(self):
        return []

    async def list_tools(self):
        return [
            SimpleNamespace(
                name="read_resource",
                description="Read a resource",
                inputSchema={"type": "object", "properties": {"name": {"type": "string"}}},
            ),
            SimpleNamespace(
                name="set_resource",
                description="Set a resource",
                inputSchema={"type": "object", "properties": {"value": {"type": "number"}}, "required": ["value"]},
            ),
        ]

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(type="text", text=f"{name}:{arguments}")],
            structuredContent={"ok": True},
        )


class LegacyToolPromptSession(FakeSession):
    async def list_tools(self):
        return [
            SimpleNamespace(
                name="get_agent_prompt",
                description="Return server instructions",
                inputSchema={"type": "object", "properties": {}},
            ),
            SimpleNamespace(
                name="read_resource",
                description="Read a resource",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "get_agent_prompt":
            return SimpleNamespace(
                isError=False,
                content=[SimpleNamespace(type="text", text="Use the server-provided resource workflow exactly.")],
                structuredContent=None,
            )
        return await super().call_tool(name, arguments)


class PromptCapabilitySession(FakeSession):
    async def list_prompts(self):
        return [SimpleNamespace(name="agent_prompt")]

    async def get_prompt(self, name):
        self.calls.append(("get_prompt", name))
        return SimpleNamespace(
            messages=[
                SimpleNamespace(content=SimpleNamespace(type="text", text="Authoritative MCP instructions from prompts/get."))
            ]
        )


class FakeClient:
    def __init__(self, session_factory=FakeSession):
        self.sessions = {}
        self.closed = False
        self.session_factory = session_factory

    async def create_session(self, server_name):
        self.sessions[server_name] = self.session_factory()

    def get_session(self, server_name):
        return self.sessions[server_name]

    async def close_all_sessions(self):
        self.closed = True


class RealtimeMCPBridgeTests(unittest.IsolatedAsyncioTestCase):
    def config(self):
        return {"mcpServers": {"fixture": {"command": "node", "args": ["server.js"]}}}

    async def test_discovers_functions_and_maps_back_to_mcp_target(self):
        client = FakeClient()
        bridge = RealtimeMCPBridge(self.config(), server_names=("fixture",), client=client)
        functions = await bridge.start()
        self.assertEqual(len(functions), 2)
        self.assertTrue(all(function.name.startswith("mcp__fixture__") for function in functions))
        targets = bridge.tool_targets
        self.assertEqual({target.tool for target in targets.values()}, {"read_resource", "set_resource"})
        self.assertEqual(functions[0].parameters["type"], "object")

    async def test_restricted_allow_list_filters_discovery(self):
        bridge = RealtimeMCPBridge(
            self.config(),
            server_names=("fixture",),
            allowed_tools={"fixture": {"read_resource"}},
            client=FakeClient(),
        )
        functions = await bridge.start()
        self.assertEqual(len(functions), 1)
        self.assertEqual(next(iter(bridge.tool_targets.values())).tool, "read_resource")

    async def test_execute_calls_existing_session_and_serializes_result(self):
        client = FakeClient()
        bridge = RealtimeMCPBridge(self.config(), server_names=("fixture",), client=client)
        await bridge.start()
        exposed_name = next(
            name for name, target in bridge.tool_targets.items() if target.tool == "read_resource"
        )
        result = await bridge.execute(exposed_name, '{"name":"current"}')
        self.assertEqual(result["transport"], "stdio/bridge")
        self.assertEqual(result["server"], "fixture")
        self.assertEqual(result["tool"], "read_resource")
        self.assertFalse(result["is_error"])
        self.assertEqual(result["structured_content"], {"ok": True})
        self.assertEqual(client.sessions["fixture"].calls, [("read_resource", {"name": "current"})])

    async def test_prefers_standard_mcp_prompt_capability(self):
        session = PromptCapabilitySession()
        prompt = await load_mcp_prompt_from_session(session)
        self.assertEqual(prompt, "Authoritative MCP instructions from prompts/get.")
        self.assertIn(("get_prompt", "agent_prompt"), session.calls)

    async def test_legacy_prompt_tool_remains_compatible(self):
        client = FakeClient(LegacyToolPromptSession)
        bridge = RealtimeMCPBridge(self.config(), server_names=("fixture",), client=client)
        functions = await bridge.start()
        prompt = await bridge.load_prompt_text("fixture")
        self.assertEqual(prompt, "Use the server-provided resource workflow exactly.")
        self.assertTrue(all(function.context_instructions == prompt for function in functions))
        self.assertIn(("get_agent_prompt", {}), client.sessions["fixture"].calls)

    async def test_external_client_is_not_closed_by_bridge(self):
        client = FakeClient()
        bridge = RealtimeMCPBridge(self.config(), server_names=("fixture",), client=client)
        await bridge.start()
        await bridge.close()
        self.assertFalse(client.closed)


if __name__ == "__main__":
    unittest.main()
