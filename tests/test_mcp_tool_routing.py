import asyncio

from voice_assistant import agent
from voice_assistant.ollama_native_mcp import (
    NativeOllamaMcpVoiceAssistant,
    _ollama_tool_schema,
    _prompt_for_routed_server,
)


class _FakeAdapter:
    def __init__(self, tools=None) -> None:
        self.tools = list(tools or [])


class _FakeAgent:
    def __init__(self, response: str = "global", *, tools=None) -> None:
        self.response = response
        self.calls: list[tuple[str, int]] = []
        self.adapter = _FakeAdapter(tools)

    async def run(self, agent_input: str, max_steps: int) -> str:
        self.calls.append((agent_input, max_steps))
        return self.response


class _FakeTool:
    def __init__(self, name: str, result=None, *, description: str = "Fake tool") -> None:
        self.name = name
        self.description = description
        self.args_schema = {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
            },
        }
        self.result = {"ok": True} if result is None else result
        self.calls: list[dict] = []

    async def ainvoke(self, arguments: dict):
        self.calls.append(dict(arguments))
        return self.result


def _native_assistant(*, routing_enabled: bool = True) -> NativeOllamaMcpVoiceAssistant:
    assistant = object.__new__(NativeOllamaMcpVoiceAssistant)
    assistant.agent = _FakeAgent()
    assistant.llm_provider = "ollama"
    assistant.model = "llama3.2:3b"
    assistant.ollama_base_url = "http://localhost:11434"
    assistant.system_prompt = "System"
    assistant.mcp_tool_routing_enabled = routing_enabled
    assistant.mcp_tool_routes = [
        {"server": "mixer", "keywords": ["volume"]},
        {"server": "qlcplus", "keywords": ["qlc"]},
    ]
    assistant.pending_mcp_confirmation_route = None
    assistant.mcp_agent_max_steps = 5
    assistant.mcp_agent_timeout_seconds = 1.0
    assistant.mcp_all_tools = []
    assistant.mcp_tools_by_server = {}
    assistant.session_context_size = 0
    assistant.session_context_store = None
    assistant.speaker_recognition_requested = False
    return assistant


def test_native_ollama_no_route_keeps_all_actual_mcp_tools_available() -> None:
    assistant = _native_assistant()
    mixer_tool = _FakeTool("mixer_tool")
    qlc_tool = _FakeTool("qlc_tool")
    prompt_wrapper = _FakeTool("agent_prompt")
    assistant.agent.adapter.tools = [mixer_tool, qlc_tool]
    assistant.mcp_all_tools = [mixer_tool, qlc_tool, prompt_wrapper]
    captured: list[tuple[list[str], str | None]] = []

    async def run_native(agent_input, tools, *, route_server=None):
        captured.append(([tool.name for tool in tools], route_server))
        return "ok"

    assistant._run_native_ollama_tool_loop = run_native

    result = asyncio.run(assistant._run_agent_with_optional_tool_routing("qui es tu?"))

    assert result == "ok"
    assert captured == [(["mixer_tool", "qlc_tool"], None)]


def test_native_ollama_route_only_narrows_to_selected_actual_mcp_tools() -> None:
    assistant = _native_assistant()
    mixer_tool = _FakeTool("mixer_tool")
    qlc_tool = _FakeTool("qlc_tool")
    qlc_prompt_wrapper = _FakeTool("agent_prompt")
    assistant.agent.adapter.tools = [mixer_tool, qlc_tool]
    assistant.mcp_all_tools = [mixer_tool, qlc_tool, qlc_prompt_wrapper]
    assistant.mcp_tools_by_server = {
        "mixer": [mixer_tool],
        "qlcplus": [qlc_tool, qlc_prompt_wrapper],
    }
    captured: list[tuple[list[str], str | None]] = []

    async def run_native(agent_input, tools, *, route_server=None):
        captured.append(([tool.name for tool in tools], route_server))
        return "ok"

    assistant._run_native_ollama_tool_loop = run_native

    result = asyncio.run(
        assistant._run_agent_with_optional_tool_routing("qlc liste tous les contrôles")
    )

    assert result == "ok"
    assert captured == [(["qlc_tool"], "qlcplus")]


def test_routed_prompt_keeps_base_and_only_selected_server_instructions() -> None:
    merged = (
        "BASE RULES\n"
        "\nAdditional instructions loaded from MCP servers:\n\n"
        'Instructions loaded from MCP server "mixer":\n\n'
        "MIXER RULES\n\n"
        'Instructions loaded from MCP server "qlcplus":\n\n'
        "QLC RULES"
    )

    qlc_prompt = _prompt_for_routed_server(merged, "qlcplus")

    assert "BASE RULES" in qlc_prompt
    assert "QLC RULES" in qlc_prompt
    assert "MIXER RULES" not in qlc_prompt


def test_unrouted_prompt_is_not_trimmed() -> None:
    merged = (
        "BASE RULES\n"
        "\nAdditional instructions loaded from MCP servers:\n\n"
        'Instructions loaded from MCP server "mixer":\n\n'
        "MIXER RULES"
    )

    assert _prompt_for_routed_server(merged, None) == merged


def test_ollama_tool_schema_uses_generic_langchain_tool_metadata() -> None:
    tool = _FakeTool("read_level", description="Read current level")

    schema = _ollama_tool_schema(tool)

    assert schema["type"] == "function"
    assert schema["function"]["name"] == "read_level"
    assert schema["function"]["description"] == "Read current level"
    assert schema["function"]["parameters"]["properties"]["target"]["type"] == "string"


def test_native_ollama_tool_loop_uses_direct_api_and_routed_compact_prompt() -> None:
    assistant = _native_assistant()
    assistant.system_prompt = (
        "BASE RULES\n"
        "\nAdditional instructions loaded from MCP servers:\n\n"
        'Instructions loaded from MCP server "mixer":\n\n'
        "MIXER RULES\n\n"
        'Instructions loaded from MCP server "qlcplus":\n\n'
        "QLC RULES"
    )
    tool = _FakeTool("qlc_get_state")
    calls: list[tuple[list[dict], list[dict], float]] = []

    async def fake_chat(messages, tool_schemas, *, timeout_seconds):
        calls.append((list(messages), list(tool_schemas), timeout_seconds))
        return {
            "message": {
                "role": "assistant",
                "content": "QLC est prêt.",
            },
            "total_duration": 5_000_000,
            "prompt_eval_count": 25,
            "eval_count": 4,
        }

    assistant._native_ollama_chat = fake_chat

    result = asyncio.run(
        assistant._run_native_ollama_tool_loop(
            "état qlc",
            [tool],
            route_server="qlcplus",
        )
    )

    assert result == "QLC est prêt."
    first_messages, first_tools, _ = calls[0]
    assert first_messages[0]["role"] == "system"
    assert "BASE RULES" in first_messages[0]["content"]
    assert "QLC RULES" in first_messages[0]["content"]
    assert "MIXER RULES" not in first_messages[0]["content"]
    assert first_tools[0]["function"]["name"] == "qlc_get_state"


def test_native_ollama_executes_real_tool_object_and_returns_final_answer() -> None:
    assistant = _native_assistant()
    tool = _FakeTool("read_level", {"level": -12.0})
    assistant.agent.adapter.tools = [tool]
    assistant.mcp_all_tools = [tool]
    responses = [
        {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "read_level",
                            "arguments": {"target": "main"},
                        },
                    }
                ],
            },
            "prompt_eval_count": 40,
            "eval_count": 8,
        },
        {
            "message": {
                "role": "assistant",
                "content": "Le niveau est moins 12 dB.",
            },
            "prompt_eval_count": 55,
            "eval_count": 9,
        },
    ]
    message_batches: list[list[dict]] = []

    async def fake_chat(messages, tool_schemas, *, timeout_seconds):
        message_batches.append([dict(message) for message in messages])
        return responses.pop(0)

    assistant._native_ollama_chat = fake_chat

    result = asyncio.run(
        assistant._run_native_ollama_tool_loop("quel est le niveau ?", [tool])
    )

    assert result == "Le niveau est moins 12 dB."
    assert tool.calls == [{"target": "main"}]
    assert any(
        message.get("role") == "tool"
        and message.get("tool_name") == "read_level"
        and "-12.0" in message.get("content", "")
        for message in message_batches[-1]
    )


def test_pending_confirmation_reuses_existing_mcp_route() -> None:
    assistant = _native_assistant()
    mixer_tool = _FakeTool("mixer_tool")
    assistant.agent.adapter.tools = [mixer_tool]
    assistant.mcp_all_tools = [mixer_tool]
    assistant.mcp_tools_by_server = {"mixer": [mixer_tool]}
    assistant.pending_mcp_confirmation_route = {
        "server": "mixer",
        "keywords": ["volume"],
    }
    captured: list[tuple[list[str], str | None]] = []

    async def run_native(agent_input, tools, *, route_server=None):
        captured.append(([tool.name for tool in tools], route_server))
        return "confirmé"

    assistant._run_native_ollama_tool_loop = run_native

    result = asyncio.run(assistant._run_agent_with_optional_tool_routing("oui"))

    assert result == "confirmé"
    assert captured == [(["mixer_tool"], "mixer")]


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
