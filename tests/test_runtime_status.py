import json
from pathlib import Path

from voice_assistant.runtime_status import (
    MCPRuntimeStatus,
    RuntimeStatus,
    RuntimeStatusTracker,
    configured_mcp_statuses,
    read_status_file,
    status_json,
    write_status_file,
)


def test_runtime_status_serializes_generic_mcp_fields(tmp_path: Path):
    status = RuntimeStatus(
        connectivity="online",
        engine="openai-realtime",
        provider="openai",
        model="model-x",
        voice="voice-y",
        ready=True,
        profile="/tmp/profile.env",
        mcp=(
            MCPRuntimeStatus(
                name="server-a",
                configured_transport="auto",
                effective_transport="stdio",
                permission="open",
                healthy=True,
                detail="local discovery succeeded",
            ),
        ),
    )
    payload = status_json(status)
    assert '"server-a"' in payload
    assert '"configured_transport":"auto"' in payload
    assert '"effective_transport":"stdio"' in payload

    path = tmp_path / "runtime-status.json"
    write_status_file(path, status)
    loaded = read_status_file(path)
    assert loaded["connectivity"] == "online"
    assert loaded["engine"] == "openai-realtime"
    assert loaded["mcp"][0]["permission"] == "open"
    assert loaded["mcp"][0]["healthy"] is True


def test_configured_mcp_statuses_are_domain_neutral(tmp_path: Path):
    profile = tmp_path / ".env.online"
    profile.write_text("MCP_CONFIG=servers.json\n", encoding="utf-8")
    (tmp_path / "servers.json").write_text(json.dumps({
        "mcpServers": {
            "alpha": {"command": "example", "realtime": {"transport": "auto", "permissions": {"mode": "open"}}},
            "beta": {"url": "http://127.0.0.1:1234/mcp", "realtime": {"transport": "stdio", "permissions": {"mode": "approval"}}},
        }
    }), encoding="utf-8")
    statuses = configured_mcp_statuses({"MCP_CONFIG": "servers.json"}, profile=profile, root=tmp_path)
    assert [item.name for item in statuses] == ["alpha", "beta"]
    assert statuses[0].configured_transport == "auto"
    assert statuses[0].effective_transport == ""
    assert statuses[1].permission == "approval"


def test_runtime_status_tracker_updates_only_named_server(tmp_path: Path):
    path = tmp_path / "runtime-status.json"
    tracker = RuntimeStatusTracker(
        path,
        RuntimeStatus(
            connectivity="online",
            engine="provider-realtime",
            mcp=(
                MCPRuntimeStatus("alpha", "auto", "", "open"),
                MCPRuntimeStatus("beta", "stdio", "", "open"),
            ),
        ),
    )
    tracker.set_mcp("alpha", effective_transport="stdio", healthy=True, detail="selected")
    loaded = read_status_file(path)
    assert loaded["mcp"][0]["effective_transport"] == "stdio"
    assert loaded["mcp"][0]["healthy"] is True
    assert loaded["mcp"][1]["effective_transport"] == ""
    tracker.set_runtime(ready=True, model="model-z")
    loaded = read_status_file(path)
    assert loaded["ready"] is True
    assert loaded["model"] == "model-z"
