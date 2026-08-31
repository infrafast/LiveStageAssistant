import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STDIO_CONFIGS = (
    "mcp_servers.json",
    "mcp_servers_localhttp.json",
    "raspi_service_pack_stdio/mcp_servers_raspi.json",
    "container/config/mcp_servers.tailscaleSTDIO.json",
)
OBSOLETE_QLCPLUS_ENV = {
    "DOTENV_CONFIG_QUIET",
    "MCP_PROMPT_FILE",
    "QLC_ALLOW_RAW_OSC",
    "QLC_HOST",
    "QLC_NATIVE_ENABLED",
    "QLC_OSC_INPUT_PORT",
    "QLC_OSC_OUTPUT_PORT",
    "QLC_UNIVERSE",
    "QLC_WIDGETS_FILE",
}
REQUIRED_NATIVE_ENV = {
    "MCP_TRANSPORT",
    "QLC_NATIVE_HOST",
    "QLC_NATIVE_PORT",
    "QLC_NATIVE_CLIENT_NAME",
    "QLC_DRY_RUN",
    "LOG_LEVEL",
    "NODE_ENV",
}


def test_qlcplus_stdio_profiles_use_native_only_environment():
    for relative_path in STDIO_CONFIGS:
        config = json.loads((ROOT / relative_path).read_text(encoding="utf-8"))
        qlcplus = config["mcpServers"]["qlcplus"]
        env = qlcplus["env"]

        assert qlcplus["command"] == "node", relative_path
        assert env["MCP_TRANSPORT"] == "stdio", relative_path
        assert REQUIRED_NATIVE_ENV <= set(env), relative_path
        assert not (OBSOLETE_QLCPLUS_ENV & set(env)), relative_path
        assert env["QLC_NATIVE_PORT"] == "9998", relative_path
