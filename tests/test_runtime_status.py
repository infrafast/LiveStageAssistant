from pathlib import Path

from voice_assistant.runtime_status import MCPRuntimeStatus, RuntimeStatus, read_status_file, status_json, write_status_file


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
