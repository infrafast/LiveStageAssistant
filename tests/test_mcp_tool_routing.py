import asyncio

from langchain_core.messages import AIMessage, ToolMessage

from voice_assistant import agent
from voice_assistant.ollama_native_mcp import NativeOllamaMcpVoiceAssistant


class _FakeAgent:
    def __init__(self, response: str = "global") -> None:
        self.response = response
        self.calls: list[tuple[str, int]] = []

    async def run(self, agent_input: str, max_steps: int) -> str:
        self.calls.append((agent_input, max_steps))
        return self.response


class _FakeTool:
    def __init__(self, name: str, result=None) -> None:
        self.name = name
        self.result = {"ok": True} if result is None else result
        self.calls: list[dict] = []

    async def ainvoke(self, arguments: dict):
        self.calls.append(dict(arguments))
        return self.result


class _FakeOllama:
    def __init__(self, responses: list[AIMessage]) -> None:
        self.responses = list(responses)
        self.bound_tool_names: list[str] = []
        self.message_batches: list[list] = []

    def bind_tools(self, tools):
        self.bound_tool_names = [tool.name for tool in tools]
        return self

    async def ainvoke(self, messages):
        self.message_batches.append(list(messages))
        return self.responses.pop(0)


def _native_assistant(*, routing_enabled: bool = True) -> NativeOllamaMcpVoiceAssistant:
    assistant = object.__new__(NativeOllamaMcpVoiceAssistant)
    assistant.agent = _FakeAgent()
    assistant.llm_provider = "ollama"
    assistant.model = "llama3.2:3b"
    assistant.ollama_base_url = "http://localhost:11434"
    assistant.system_prompt = "System"
    assistant.mcp_tool_routing_enabled = routing_enabled
    assistant.mcp_tool_routes = [{"server": "mixer", "keywords": ["volume"]}]
    assistant.pending_mcp_confirmation_route = None
    assistant.mcp_agent_max_steps = 5
    assistant.mcp_agent_timeout_seconds = 1.0
    assistant.mcp_all_tools = []
    assistant.mcp_tools_by_server = {}
    assistant.session_context_size = 0
    assistant.session_context_store = None
    assistant.speaker_recognition_requested = False
    return assistant


def test_native_ollama_no_route_keeps_all_tools_available() -> None:
    assistant = _native_assistant()
    mixer_tool = _FakeTool("mixer_tool")
    qlc_tool = _FakeTool("qlc_tool")
    assistant.mcp_all_tools = [mixer_tool, qlc_tool]
    captured: list[list[str]] = []

    async def run_native(agent_input, tools):
        captured.append([tool.name for tool in tools])
        return "ok"

    assistant._run_native_ollama_tool_loop = run_native

    result = asyncio.run(assistant._run_agent_with_optional_tool_routing("qui es tu?"))

    assert result == "ok"
    assert captured == [["mixer_tool", "qlc_tool"]]


def test_native_ollama_route_only_narrows_to_selected_mcp_server() -> None:
    assistant = _native_assistant()
    mixer_tool = _FakeTool("mixer_tool")
    qlc_tool = _FakeTool("qlc_tool")
    assistant.mcp_all_tools = [mixer_tool, qlc_tool]
    assistant.mcp_tools_by_server = {
        "mixer": [mixer_tool],
        "qlcplus": [qlc_tool],
    }
    captured: list[list[str]] = []

    async def run_native(agent_input, tools):
        captured.append([tool.name for tool in tools])
        return "ok"

    assistant._run_native_ollama_tool_loop = run_native

    result = asyncio.run(
        assistant._run_agent_with_optional_tool_routing("baisse le volume")
    )

    assert result == "ok"
    assert captured == [["mixer_tool"]]


def test_native_ollama_executes_real_tool_object_and_returns_final_answer() -> None:
    assistant = _native_assistant()
    tool = _FakeTool("read_level", {"level": -12.0})
    assistant.mcp_all_tools = [tool]
    fake_llm = _FakeOllama(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_level",
                        "args": {"target": "main"},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Le niveau est moins 12 dB."),
        ]
    )
    assistant._build_llm = lambda: fake_llm

    result = asyncio.run(
        assistant._run_native_ollama_tool_loop("quel est le niveau ?", [tool])
    )

    assert result == "Le niveau est moins 12 dB."
    assert fake_llm.bound_tool_names == ["read_level"]
    assert tool.calls == [{"target": "main"}]
    assert any(
        isinstance(message, ToolMessage)
        and message.tool_call_id == "call-1"
        for message in fake_llm.message_batches[-1]
    )


def test_pending_confirmation_reuses_existing_mcp_route() -> None:
    assistant = _native_assistant()
    mixer_tool = _FakeTool("mixer_tool")
    assistant.mcp_all_tools = [mixer_tool]
    assistant.mcp_tools_by_server = {"mixer": [mixer_tool]}
    assistant.pending_mcp_confirmation_route = {
        "server": "mixer",
        "keywords": ["volume"],
    }
    captured: list[list[str]] = []

    async def run_native(agent_input, tools):
        captured.append([tool.name for tool in tools])
        return "confirmé"

    assistant._run_native_ollama_tool_loop = run_native

    result = asyncio.run(assistant._run_agent_with_optional_tool_routing("oui"))

    assert result == "confirmé"
    assert captured == [["mixer_tool"]]


def test_base_voice_assistant_cloud_semantics_are_unchanged() -> None:
    assistant = object.__new__(agent.VoiceAssistant)
    assistant.agent = _FakeAgent()
    assistant.llm_provider = "openai"
    assistant.mcp_tool_routing_enabled = True
    assistant.mcp_tool_routes = [{"server": "mixer", "keywords": ["volume"]}]
    assistant.pending_mcp_confirmation_route = None
    assistant.mcp_agent_max_steps = 20
    assistant.mcp_agent_timeout_seconds = 1.0
    assistant.mcp_all_tools = [object()]
    assistant.mcp_tools_by_server = {}
    assistant.session_context_size = 0
    assistant.session_context_store = None
    assistant.speaker_recognition_requested = False

    result = asyncio.run(
        assistant._run_agent_with_optional_tool_routing("qui es tu?")
    )

    assert result == "global"
    assert len(assistant.agent.calls) == 1
