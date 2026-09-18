import asyncio
from dataclasses import dataclass
import json

import pytest

from voice_assistant.local_gateway_runtime import (
    ANALYZE_TOOL,
    EXECUTE_TOOL,
    GATEWAY_PROTOCOL,
    DeterministicGatewayOrchestrator,
    local_gateway_mcp_config,
)


@dataclass
class FakeText:
    text: str
    type: str = "text"


class FakeResult:
    def __init__(self, payload):
        self.content = [FakeText(json.dumps(payload))]
        self.isError = False
        self.structuredContent = None


class FakeTool:
    def __init__(self, name, protocol=True):
        self.name = name
        enum = [GATEWAY_PROTOCOL] if protocol else ["other/v1"]
        self.inputSchema = {
            "type": "object",
            "properties": {"protocol": {"type": "string", "enum": enum}},
        }


class FakeSession:
    def __init__(self, *, tools=None, analyze=None, execute=None):
        self.tools = tools or [FakeTool(ANALYZE_TOOL), FakeTool(EXECUTE_TOOL)]
        self.analyze = analyze or (lambda args: {
            "protocol": GATEWAY_PROTOCOL,
            "recognized": False,
            "status": "unrecognized",
            "effect": "none",
        })
        self.execute = execute or (lambda args: {
            "protocol": GATEWAY_PROTOCOL,
            "ok": True,
            "responseText": "ok",
        })
        self.calls = []

    async def list_tools(self):
        return self.tools

    async def call_tool(self, name, args):
        self.calls.append((name, dict(args)))
        if name == ANALYZE_TOOL:
            value = self.analyze(dict(args))
        elif name == EXECUTE_TOOL:
            value = self.execute(dict(args))
        else:
            raise AssertionError(name)
        if asyncio.iscoroutine(value):
            value = await value
        return FakeResult(value)


class FakeClient:
    def __init__(self, sessions):
        self.sessions = dict(sessions)
        self.created = []
        self.closed = False

    async def create_session(self, name):
        self.created.append(name)

    def get_session(self, name):
        return self.sessions[name]

    async def close_all_sessions(self):
        self.closed = True


def config(*entries):
    return {"mcpServers": {name: value for name, value in entries}}


def stdio_entry(*, routing="", permission="open"):
    return {
        "command": "node",
        "args": ["server.js"],
        "env": {"EXISTING": "1"},
        "realtime": {"transport": "stdio", "permissions": {"mode": permission}},
        "assistantOptions": {"routing": routing},
    }


def test_local_config_overlays_gateway_only_on_stdio_without_mutating_source():
    source = config(
        ("mixer", stdio_entry(routing="mix, volume")),
        ("remote", {
            "url": "http://127.0.0.1:9999/mcp",
            "realtime": {"permissions": {"mode": "open"}},
        }),
    )
    runtime = local_gateway_mcp_config(source)

    assert runtime["mcpServers"]["mixer"]["env"]["LSA_LOCAL_COMMAND_GATEWAY"] == "1"
    assert runtime["mcpServers"]["mixer"]["env"]["EXISTING"] == "1"
    assert "LSA_LOCAL_COMMAND_GATEWAY" not in source["mcpServers"]["mixer"]["env"]
    assert runtime["mcpServers"]["remote"]["url"] == "http://127.0.0.1:9999/mcp"
    assert "env" not in runtime["mcpServers"]["remote"]


@pytest.mark.asyncio
async def test_discovery_requires_both_reserved_tools_and_protocol():
    good = FakeSession()
    missing = FakeSession(tools=[FakeTool(ANALYZE_TOOL)])
    incompatible = FakeSession(tools=[
        FakeTool(ANALYZE_TOOL, protocol=False),
        FakeTool(EXECUTE_TOOL, protocol=False),
    ])
    client = FakeClient({"good": good, "missing": missing, "incompatible": incompatible})
    orchestrator = DeterministicGatewayOrchestrator(
        config(
            ("good", stdio_entry()),
            ("missing", stdio_entry()),
            ("incompatible", stdio_entry()),
        ),
        client=client,
    )

    assert await orchestrator.start() == ("good",)
    assert tuple(orchestrator.servers) == ("good",)


@pytest.mark.asyncio
async def test_routing_analyzes_only_matching_server_then_executes_once():
    mixer = FakeSession(
        analyze=lambda args: {
            "protocol": GATEWAY_PROTOCOL,
            "recognized": True,
            "status": "ready",
            "effect": "write",
            "planToken": "mix-plan",
        },
        execute=lambda args: {
            "protocol": GATEWAY_PROTOCOL,
            "ok": True,
            "responseText": "Mixer done",
        },
    )
    qlc = FakeSession()
    client = FakeClient({"mixer": mixer, "qlc": qlc})
    orchestrator = DeterministicGatewayOrchestrator(
        config(
            ("mixer", stdio_entry(routing="mix,volume")),
            ("qlc", stdio_entry(routing="qlc,lumière")),
        ),
        client=client,
    )
    await orchestrator.start()

    assert await orchestrator.handle("monte le volume voix") == "Mixer done"
    assert [name for name, _args in mixer.calls] == [ANALYZE_TOOL, EXECUTE_TOOL]
    assert qlc.calls == []


@pytest.mark.asyncio
async def test_generic_context_is_forwarded_to_analyze_calls():
    mixer = FakeSession(
        analyze=lambda args: {
            "protocol": GATEWAY_PROTOCOL,
            "recognized": True,
            "status": "ready",
            "effect": "read",
            "planToken": "ctx-plan",
        },
        execute=lambda args: {
            "protocol": GATEWAY_PROTOCOL,
            "ok": True,
            "responseText": "ok",
        },
    )
    orchestrator = DeterministicGatewayOrchestrator(
        config(("mixer", stdio_entry())),
        client=FakeClient({"mixer": mixer}),
    )
    await orchestrator.start()
    context = {
        "speaker": {
            "name": "Laurent",
            "confidence": 0.91,
            "backend": "resemblyzer",
        }
    }

    assert await orchestrator.handle("monte mon retour", context=context) == "ok"
    assert mixer.calls[0][0] == ANALYZE_TOOL
    assert mixer.calls[0][1]["context"] == context


@pytest.mark.asyncio
async def test_unrouted_one_claim_executes_and_multiple_claims_never_execute():
    def claim(label):
        return FakeSession(
            analyze=lambda args: {
                "protocol": GATEWAY_PROTOCOL,
                "recognized": True,
                "status": "ready",
                "effect": "read",
                "planToken": f"{label}-plan",
            },
            execute=lambda args: {
                "protocol": GATEWAY_PROTOCOL,
                "ok": True,
                "responseText": label,
            },
        )

    claimant = claim("one")
    silent = FakeSession()
    client = FakeClient({"one": claimant, "two": silent})
    orchestrator = DeterministicGatewayOrchestrator(
        config(("one", stdio_entry()), ("two", stdio_entry())),
        client=client,
    )
    await orchestrator.start()
    assert await orchestrator.handle("commande sans route") == "one"
    assert [name for name, _args in claimant.calls].count(EXECUTE_TOOL) == 1

    first = claim("first")
    second = claim("second")
    client2 = FakeClient({"first": first, "second": second})
    orchestrator2 = DeterministicGatewayOrchestrator(
        config(("first", stdio_entry()), ("second", stdio_entry())),
        client=client2,
    )
    await orchestrator2.start()
    response = await orchestrator2.handle("ambiguous")
    assert "Plusieurs contrôleurs" in response
    assert all(name != EXECUTE_TOOL for name, _args in first.calls)
    assert all(name != EXECUTE_TOOL for name, _args in second.calls)


@pytest.mark.asyncio
async def test_analysis_failure_is_not_reported_as_unrecognized():
    async def failing_analyze(_args):
        raise RuntimeError("Le mixeur est deconnecté: impossible de lire les noms OSC (channel 17)")

    mixer = FakeSession(analyze=failing_analyze)
    orchestrator = DeterministicGatewayOrchestrator(
        config(("mixer", stdio_entry())),
        client=FakeClient({"mixer": mixer}),
    )
    await orchestrator.start()

    response = await orchestrator.handle("mets la guitare de anto sur claude à -5db")
    assert response == "Erreur mixer : impossible de résoudre les noms, le mixeur ne répond pas."


@pytest.mark.asyncio
async def test_generic_analysis_failure_stays_concise():
    async def failing_analyze(_args):
        raise RuntimeError("unexpected internal resolver failure with implementation details")

    mixer = FakeSession(analyze=failing_analyze)
    orchestrator = DeterministicGatewayOrchestrator(
        config(("mixer", stdio_entry())),
        client=FakeClient({"mixer": mixer}),
    )
    await orchestrator.start()

    response = await orchestrator.handle("commande")
    assert response == "Erreur mixer pendant l’analyse de la commande."


@pytest.mark.asyncio
async def test_zero_claim_never_executes():
    one = FakeSession()
    two = FakeSession()
    orchestrator = DeterministicGatewayOrchestrator(
        config(("one", stdio_entry()), ("two", stdio_entry())),
        client=FakeClient({"one": one, "two": two}),
    )
    await orchestrator.start()
    assert await orchestrator.handle("unknown") == "Commande non reconnue."
    assert all(name != EXECUTE_TOOL for session in (one, two) for name, _args in session.calls)


@pytest.mark.asyncio
async def test_continuation_is_pinned_to_requesting_server():
    qlc = FakeSession(
        analyze=lambda args: (
            {
                "protocol": GATEWAY_PROTOCOL,
                "recognized": True,
                "status": "clarification",
                "effect": "none",
                "continuationToken": "cont",
                "responseText": "Quel bouton ?",
            }
            if "continuationToken" not in args
            else {
                "protocol": GATEWAY_PROTOCOL,
                "recognized": True,
                "status": "ready",
                "effect": "write",
                "planToken": "final",
            }
        ),
        execute=lambda args: {
            "protocol": GATEWAY_PROTOCOL,
            "ok": True,
            "responseText": "QLC done",
        },
    )
    mixer = FakeSession()
    orchestrator = DeterministicGatewayOrchestrator(
        config(
            ("qlc", stdio_entry(routing="qlc")),
            ("mixer", stdio_entry(routing="mix")),
        ),
        client=FakeClient({"qlc": qlc, "mixer": mixer}),
    )
    await orchestrator.start()

    assert await orchestrator.handle("qlc bleu") == "Quel bouton ?"
    assert await orchestrator.handle("Blue Speed") == "QLC done"
    assert mixer.calls == []
    assert qlc.calls[1][1]["continuationToken"] == "cont"


@pytest.mark.asyncio
async def test_write_approval_requires_explicit_yes_and_no_cancels():
    def make_session():
        return FakeSession(
            analyze=lambda args: {
                "protocol": GATEWAY_PROTOCOL,
                "recognized": True,
                "status": "ready",
                "effect": "write",
                "planToken": "write-plan",
            },
            execute=lambda args: {
                "protocol": GATEWAY_PROTOCOL,
                "ok": True,
                "responseText": "Write done",
            },
        )

    approved_session = make_session()
    approved = DeterministicGatewayOrchestrator(
        config(("mixer", stdio_entry(permission="approval"))),
        client=FakeClient({"mixer": approved_session}),
    )
    await approved.start()
    prompt = await approved.handle("mute voix")
    assert "Confirmer" in prompt
    assert [name for name, _args in approved_session.calls] == [ANALYZE_TOOL]
    assert await approved.handle("oui") == "Write done"
    assert [name for name, _args in approved_session.calls] == [ANALYZE_TOOL, EXECUTE_TOOL]

    cancelled_session = make_session()
    cancelled = DeterministicGatewayOrchestrator(
        config(("mixer", stdio_entry(permission="approval"))),
        client=FakeClient({"mixer": cancelled_session}),
    )
    await cancelled.start()
    await cancelled.handle("mute voix")
    assert await cancelled.handle("non") == "Commande annulée."
    assert all(name != EXECUTE_TOOL for name, _args in cancelled_session.calls)


@pytest.mark.asyncio
async def test_local_engine_hard_guard_never_builds_llm():
    from voice_assistant.local_engine import DeterministicLocalVoiceAssistant

    assistant = object.__new__(DeterministicLocalVoiceAssistant)
    with pytest.raises(RuntimeError, match="must never construct an LLM"):
        assistant._build_llm()
    assert await assistant.refresh_session_llm_summary() is False
