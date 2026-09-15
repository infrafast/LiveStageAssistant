import asyncio

from voice_assistant.agent import VoiceAssistant


class _FakeAgent:
    def __init__(self, response: str = "global") -> None:
        self.response = response
        self.calls: list[tuple[str, int]] = []

    async def run(self, agent_input: str, max_steps: int) -> str:
        self.calls.append((agent_input, max_steps))
        return self.response


def _routing_assistant(*, provider: str, routing_enabled: bool = True) -> VoiceAssistant:
    assistant = object.__new__(VoiceAssistant)
    assistant.agent = _FakeAgent()
    assistant.llm_provider = provider
    assistant.mcp_tool_routing_enabled = routing_enabled
    assistant.mcp_tool_routes = [{"server": "mixer", "keywords": ["mix"]}]
    assistant.pending_mcp_confirmation_route = None
    assistant.mcp_agent_max_steps = 20
    assistant.mcp_agent_timeout_seconds = 1.0
    assistant.mcp_all_tools = [object()]
    assistant.mcp_tools_by_server = {}
    assistant.session_context_size = 0
    assistant.session_context_store = None
    assistant.speaker_recognition_requested = False
    return assistant


def test_ollama_unrouted_turn_uses_zero_tools_when_routing_enabled() -> None:
    assistant = _routing_assistant(provider="ollama")
    routed_calls: list[list[object]] = []

    async def run_with_tools(agent_input, tools, route_for_confirmation=None):
        routed_calls.append(list(tools))
        return "local"

    assistant._run_agent_with_tools = run_with_tools

    result = asyncio.run(assistant._run_agent_with_optional_tool_routing("qui es tu?"))

    assert result == "local"
    assert routed_calls == [[]]
    assert assistant.agent.calls == []


def test_openai_unrouted_turn_keeps_global_tools_behavior() -> None:
    assistant = _routing_assistant(provider="openai")

    async def fail_if_subset_is_used(*args, **kwargs):
        raise AssertionError("OpenAI unrouted behavior must remain unchanged")

    assistant._run_agent_with_tools = fail_if_subset_is_used

    result = asyncio.run(assistant._run_agent_with_optional_tool_routing("qui es tu?"))

    assert result == "global"
    assert len(assistant.agent.calls) == 1


def test_ollama_unrouted_turn_keeps_global_tools_when_routing_disabled() -> None:
    assistant = _routing_assistant(provider="ollama", routing_enabled=False)

    async def fail_if_subset_is_used(*args, **kwargs):
        raise AssertionError("Disabled routing must preserve the legacy global-agent path")

    assistant._run_agent_with_tools = fail_if_subset_is_used

    result = asyncio.run(assistant._run_agent_with_optional_tool_routing("qui es tu?"))

    assert result == "global"
    assert len(assistant.agent.calls) == 1
