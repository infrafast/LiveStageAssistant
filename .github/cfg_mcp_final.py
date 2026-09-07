from pathlib import Path


def once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected 1 occurrence, got {n}")
    return text.replace(old, new, 1)

# mcp_config_web.py
p = Path('voice_assistant/mcp_config_web.py')
s = p.read_text(encoding='utf-8')
s = s.replace('import re\nimport tempfile\n', 'import re\nimport subprocess\nimport tempfile\nimport time\n')
s = s.replace('from urllib.parse import urlparse\n', 'from urllib.error import HTTPError, URLError\nfrom urllib.parse import urlparse\nfrom urllib.request import Request, urlopen\n')
s = once(s, '    return {\n        "name": server.name,\n', '    return {\n        "name": server.name,\n        "enabled": server.raw_entry.get("enabled", True) is not False,\n', 'web payload enabled')
s = once(s, '    entry = dict(servers.get(current_name) or {})\n    command = str(payload.get("command") or "").strip()\n', '    entry = dict(servers.get(current_name) or {})\n    entry["enabled"] = bool(payload.get("enabled", entry.get("enabled", True)))\n    command = str(payload.get("command") or "").strip()\n', 'save enabled')
if 'def test_web_mcp_server(' not in s:
    s += r'''

def _probe_http(url: str, headers: Mapping[str, Any] | None, timeout: float) -> tuple[bool, str]:
    if not url:
        return False, "no URL configured"
    request = Request(url, method="GET", headers={str(k): str(v) for k, v in (headers or {}).items()})
    try:
        with urlopen(request, timeout=timeout) as response:
            return True, f"HTTP {getattr(response, 'status', 200)}"
    except HTTPError as exc:
        return True, f"HTTP {exc.code} (endpoint reachable)"
    except (URLError, TimeoutError, OSError) as exc:
        return False, str(exc)


def _probe_stdio(entry: Mapping[str, Any], timeout: float) -> tuple[bool, str]:
    command = str(entry.get("command") or "").strip()
    if not command:
        local_url = str(entry.get("url") or "").strip()
        if local_url:
            headers = entry.get("headers") if isinstance(entry.get("headers"), Mapping) else None
            return _probe_http(local_url, headers, timeout)
        return False, "no local STDIO/private HTTP route configured"
    args = entry.get("args") if isinstance(entry.get("args"), list) else []
    env = os.environ.copy()
    raw_env = entry.get("env") if isinstance(entry.get("env"), Mapping) else {}
    env.update({str(k): str(v) for k, v in raw_env.items()})
    try:
        process = subprocess.Popen([command, *[str(item) for item in args]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    except OSError as exc:
        return False, f"could not start command: {exc}"
    deadline = time.monotonic() + min(max(timeout, 0.2), 2.0)
    try:
        while time.monotonic() < deadline:
            code = process.poll()
            if code is not None:
                return (code == 0), f"process exited with code {code}"
            time.sleep(0.05)
        return True, "process started and remained alive"
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.5)


def test_web_mcp_server(path: str | Path, server_name: str, *, timeout: float = 2.0) -> dict[str, Any]:
    name = _server_name(server_name)
    config_path, root = _read_raw(path)
    raw = root["mcpServers"].get(name)
    if not isinstance(raw, Mapping):
        raise ValueError(f"MCP server {name!r} does not exist")
    server = normalize_mcp_server(name, raw)
    enabled = raw.get("enabled", True) is not False
    transport = server.realtime.transport
    tested = ""
    healthy = False
    detail = ""
    if transport == "native":
        tested = "https"
        healthy, detail = _probe_http(server.native.url, server.native.headers, timeout)
    elif transport == "stdio":
        tested = "stdio"
        healthy, detail = _probe_stdio(server.local_entry, timeout)
    else:
        if server.local_entry.get("command") or server.local_entry.get("url"):
            tested = "stdio"
            healthy, detail = _probe_stdio(server.local_entry, timeout)
        if not healthy and server.native.url:
            tested = "https"
            healthy, detail = _probe_http(server.native.url, server.native.headers, timeout)
    if not tested:
        detail = "no testable route configured"
    return {
        "ok": True,
        "server": name,
        "enabled": enabled,
        "configured_transport": _web_transport(transport),
        "tested_transport": tested,
        "healthy": healthy,
        "detail": detail,
        "auth_configured": bool(server.native.headers),
        "config_path": str(config_path),
    }
'''
p.write_text(s, encoding='utf-8')

# endpoint helpers
p = Path('voice_assistant/mcp_realtime_web_endpoint.py')
s = p.read_text(encoding='utf-8')
s = once(s, 'from .mcp_config_web import delete_web_mcp_server, save_web_mcp_server, update_web_mcp_policy', 'from .mcp_config_web import delete_web_mcp_server, load_web_mcp_policies, save_web_mcp_server, test_web_mcp_server, update_web_mcp_policy', 'endpoint imports package')
s = once(s, 'from mcp_config_web import delete_web_mcp_server, save_web_mcp_server, update_web_mcp_policy', 'from mcp_config_web import delete_web_mcp_server, load_web_mcp_policies, save_web_mcp_server, test_web_mcp_server, update_web_mcp_policy', 'endpoint imports fallback')
if 'def mcp_registry_from_snapshot' not in s:
    s += '\n\ndef mcp_registry_from_snapshot(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:\n    return load_web_mcp_policies(mcp_config_path_from_snapshot(snapshot))\n\n\ndef test_mcp_server_from_snapshot(snapshot: Mapping[str, Any], server_name: str) -> dict[str, Any]:\n    return test_web_mcp_server(mcp_config_path_from_snapshot(snapshot), server_name)\n'
p.write_text(s, encoding='utf-8')

# runtime status
p = Path('voice_assistant/runtime_status.py')
s = p.read_text(encoding='utf-8')
s = once(s, '    permission: str\n    healthy: bool | None = None\n', '    permission: str\n    enabled: bool = True\n    healthy: bool | None = None\n', 'status enabled field')
s = once(s, '        realtime = entry.get("realtime") if isinstance(entry.get("realtime"), dict) else {}\n        configured = str(realtime.get("transport") or "stdio").strip().lower()\n', '        enabled = entry.get("enabled", True) is not False\n        realtime = entry.get("realtime") if isinstance(entry.get("realtime"), dict) else {}\n        configured = str(realtime.get("transport") or "stdio").strip().lower()\n', 'status enabled read')
s = once(s, '            configured_transport=configured,\n            effective_transport=effective,\n            permission=permission,\n            healthy=None,\n            detail="configured",\n', '            configured_transport=configured,\n            effective_transport=effective if enabled else "",\n            permission=permission,\n            enabled=enabled,\n            healthy=None,\n            detail="configured" if enabled else "disabled",\n', 'status enabled construct')
p.write_text(s, encoding='utf-8')

# classic engine
p = Path('voice_assistant/classic_engine.py')
s = p.read_text(encoding='utf-8')
s = once(s, '    if not isinstance(payload, dict):\n        raise RuntimeError(f"MCP_CONFIG \'{path}\' must contain a JSON object")\n    return payload\n', '    if not isinstance(payload, dict):\n        raise RuntimeError(f"MCP_CONFIG \'{path}\' must contain a JSON object")\n    servers = payload.get("mcpServers")\n    if isinstance(servers, dict):\n        payload = dict(payload)\n        payload["mcpServers"] = {name: entry for name, entry in servers.items() if not isinstance(entry, dict) or entry.get("enabled", True) is not False}\n    return payload\n', 'classic enabled filter')
p.write_text(s, encoding='utf-8')

# realtime engine
p = Path('voice_assistant/realtime/service.py')
s = p.read_text(encoding='utf-8')
s = once(s, '    raw_config = json.loads(config_path.read_text(encoding="utf-8"))\n    inventory = load_mcp_inventory(config_path)\n', '    raw_config = json.loads(config_path.read_text(encoding="utf-8"))\n    raw_servers = raw_config.get("mcpServers") if isinstance(raw_config, dict) else None\n    if isinstance(raw_servers, dict):\n        raw_config = dict(raw_config)\n        raw_config["mcpServers"] = {name: entry for name, entry in raw_servers.items() if not isinstance(entry, dict) or entry.get("enabled", True) is not False}\n    inventory = {name: server for name, server in load_mcp_inventory(config_path).items() if server.raw_entry.get("enabled", True) is not False}\n', 'realtime enabled filter')
p.write_text(s, encoding='utf-8')

# web monitor
p = Path('voice_assistant/web_monitor.py')
s = p.read_text(encoding='utf-8')
s = once(s, '        delete_mcp_server_from_snapshot,\n        save_mcp_realtime_policy_from_snapshot,\n        save_mcp_server_from_snapshot,\n', '        delete_mcp_server_from_snapshot,\n        mcp_registry_from_snapshot,\n        save_mcp_realtime_policy_from_snapshot,\n        save_mcp_server_from_snapshot,\n        test_mcp_server_from_snapshot,\n', 'web imports package')
s = once(s, 'from mcp_realtime_web_endpoint import delete_mcp_server_from_snapshot, save_mcp_realtime_policy_from_snapshot, save_mcp_server_from_snapshot', 'from mcp_realtime_web_endpoint import delete_mcp_server_from_snapshot, mcp_registry_from_snapshot, save_mcp_realtime_policy_from_snapshot, save_mcp_server_from_snapshot, test_mcp_server_from_snapshot', 'web imports fallback')
s = once(s, '        configured = str(entry.get("configured_transport") or "").strip()\n        effective = str(entry.get("effective_transport") or "").strip()\n', '        configured = str(entry.get("configured_transport") or "").strip()\n        enabled = entry.get("enabled", True) is not False\n        effective = str(entry.get("effective_transport") or "").strip()\n', 'tile enabled read')
s = once(s, '        state = "online" if healthy is True else "offline" if healthy is False else "unknown"\n        parts = [f"transport={_display_transport(transport)}"]\n', '        state = "disabled" if not enabled else "online" if healthy is True else "offline" if healthy is False else "unknown"\n        parts = ["disabled"] if not enabled else [f"transport={_display_transport(transport)}"]\n', 'tile disabled state')
s = once(s, '        snapshot["environment_loading"] = {"active": loading, "title": "Application de la configuration" if loading else ""}\n        if not loading:\n            self.set_environment_loading(False)\n', '        snapshot["environment_loading"] = {"active": loading, "title": "Application de la configuration" if loading else ""}\n        if not loading:\n            self.set_environment_loading(False)\n        try:\n            snapshot["mcp_registry"] = mcp_registry_from_snapshot(snapshot)\n        except Exception:\n            snapshot["mcp_registry"] = []\n', 'safe registry snapshot')
if 'def _test_mcp_server(self' not in s:
    s = once(s, '    def _delete_mcp_server(self, server_name: str) -> dict[str, Any]:\n        deleted, refreshed_config = delete_mcp_server_from_snapshot(self.snapshot(), server_name)\n        self._refresh_mcp_snapshot(refreshed_config)\n        return {"ok": True, "deleted": deleted, "restart_required": True}\n\n', '    def _delete_mcp_server(self, server_name: str) -> dict[str, Any]:\n        deleted, refreshed_config = delete_mcp_server_from_snapshot(self.snapshot(), server_name)\n        self._refresh_mcp_snapshot(refreshed_config)\n        return {"ok": True, "deleted": deleted, "restart_required": True}\n\n    def _test_mcp_server(self, server_name: str) -> dict[str, Any]:\n        return test_mcp_server_from_snapshot(self.snapshot(), server_name)\n\n', 'test method')
s = once(s, 'routes = {"/api/mcp-realtime-policy", "/api/mcp-server", "/api/runtime-restart"}', 'routes = {"/api/mcp-realtime-policy", "/api/mcp-server", "/api/mcp-test", "/api/runtime-restart"}', 'test route')
s = once(s, '                        if parsed.path == "/api/mcp-server":\n                            self._handle_mcp_server(); return\n                        self._handle_mcp_realtime_policy_save()\n', '                        if parsed.path == "/api/mcp-server":\n                            self._handle_mcp_server(); return\n                        if parsed.path == "/api/mcp-test":\n                            self._handle_mcp_test(); return\n                        self._handle_mcp_realtime_policy_save()\n', 'test dispatch')
if 'def _handle_mcp_test(self)' not in s:
    s = once(s, '                    def _handle_mcp_server(self) -> None:\n', '                    def _handle_mcp_test(self) -> None:\n                        payload = self._read_json_body(max_bytes=8 * 1024)\n                        if payload is None: return\n                        name = str(payload.get("server") or "").strip()\n                        if not name:\n                            self._send_json_error(400, {"ok": False, "error": {"message": "server is required"}}); return\n                        try:\n                            result = monitor._test_mcp_server(name)\n                        except ValueError as error:\n                            self._send_json_error(400, {"ok": False, "error": {"message": str(error)}}); return\n                        except Exception as error:\n                            self._send_json_error(500, {"ok": False, "error": {"message": f"MCP test failed: {error}"}}); return\n                        self._send_json(result)\n\n                    def _handle_mcp_server(self) -> None:\n', 'test handler')
p.write_text(s, encoding='utf-8')

# app.js
p = Path('assets/web/app.js')
s = p.read_text(encoding='utf-8')
old = '''    function mcpPolicyMap(snapshot) {\n      const servers = findMcpConfigServers(snapshot) || {};\n      const result = {};\n      for (const [name, raw] of Object.entries(servers)) {\n        if (!raw || typeof raw !== "object") continue;\n        const native = raw.native && typeof raw.native === "object" ? raw.native : {};\n        const realtime = raw.realtime && typeof raw.realtime === "object" ? raw.realtime : {};\n        const permissions = realtime.permissions && typeof realtime.permissions === "object" ? realtime.permissions : {};\n        result[name] = {\n          transport: String(realtime.transport || "auto").toLowerCase(),\n          permission: String(permissions.mode || realtime.permission || "open").toLowerCase() === "approval" ? "approval" : "open",\n          httpsUrl: String(native.url || ""),\n          command: String(raw.command || ""),\n          args: Array.isArray(raw.args) ? raw.args.map(String) : [],\n          localUrl: String(raw.url || "")\n        };\n      }\n      return result;\n    }\n'''
new = '''    function mcpPolicyMap(snapshot) {\n      const result = {};\n      for (const item of snapshot?.mcp_registry || []) {\n        const name = String(item?.name || "").trim();\n        if (!name) continue;\n        result[name] = {\n          enabled: item.enabled !== false,\n          transport: String(item.transport || item.realtime_transport || "auto").toLowerCase().replace("https", "native"),\n          permission: String(item.permission_mode || "open").toLowerCase() === "approval" ? "approval" : "open",\n          httpsUrl: String(item.https_url || ""),\n          command: String(item.command || ""),\n          args: Array.isArray(item.args) ? item.args.map(String) : [],\n          localUrl: String(item.local_url || ""),\n          authConfigured: Boolean(item.auth_configured)\n        };\n      }\n      return result;\n    }\n'''
s = once(s, old, new, 'safe registry UI')
s = once(s, '      const controls = makeMcpPolicyControls({ transport: "auto", permission: "open", httpsUrl: "" });\n      const create = document.createElement("button");', '      const enabled = document.createElement("input"); enabled.type = "checkbox"; enabled.checked = true;\n      const controls = makeMcpPolicyControls({ transport: "auto", permission: "open", httpsUrl: "" });\n      const create = document.createElement("button");', 'create enabled control')
s = once(s, '      form.append(makeCfgField("Name", name), makeCfgField("STDIO command", command), makeCfgField("STDIO args (JSON)", args), makeCfgField("Local MCP URL", localUrl), makeCfgField("Transport", controls.transport), controls.httpsField, makeCfgField("Permission", controls.permission), create, cancel, message);', '      form.append(makeCfgField("Name", name), makeCfgField("Enabled", enabled), makeCfgField("STDIO command", command), makeCfgField("STDIO args (JSON)", args), makeCfgField("Local MCP URL", localUrl), makeCfgField("Transport", controls.transport), controls.httpsField, makeCfgField("Permission", controls.permission), create, cancel, message);', 'create enabled field')
s = once(s, '            name: name.value.trim(), command: command.value.trim(), args: args.value.trim() || "[]", local_url: localUrl.value.trim(),\n            realtime_transport:', '            name: name.value.trim(), enabled: enabled.checked, command: command.value.trim(), args: args.value.trim() || "[]", local_url: localUrl.value.trim(),\n            realtime_transport:', 'create enabled payload')
s = once(s, '          status.textContent = effective ? `Configured ${configured} · Effective ${effective}${runtime.healthy === true ? " · healthy" : runtime.healthy === false ? " · unavailable" : ""}` : `Configured ${configured}`;\n          section.append(status);\n        }\n        const command = document.createElement("input");', '          status.textContent = policy.enabled === false ? `Disabled · Configured ${configured}` : effective ? `Configured ${configured} · Effective ${effective}${runtime.healthy === true ? " · healthy" : runtime.healthy === false ? " · unavailable" : ""}` : `Configured ${configured}`;\n          section.append(status);\n        }\n        const enabled = document.createElement("input"); enabled.type = "checkbox"; enabled.checked = policy.enabled !== false;\n        const command = document.createElement("input");', 'editor enabled')
s = once(s, '        const save = document.createElement("button"); save.type = "button"; save.className = "small-button"; save.textContent = "Save MCP";\n        const del = document.createElement("button");', '        const auth = document.createElement("div"); auth.className = "detail"; auth.textContent = `HTTPS auth: ${policy.authConfigured ? "Configured" : "Missing / not required"}`;\n        const test = document.createElement("button"); test.type = "button"; test.className = "small-button"; test.textContent = "Test";\n        const save = document.createElement("button"); save.type = "button"; save.className = "small-button"; save.textContent = "Save MCP";\n        const del = document.createElement("button");', 'test auth controls')
s = once(s, '              name, command: command.value.trim(), args: args.value.trim() || "[]", local_url: localUrl.value.trim(),\n              realtime_transport:', '              name, enabled: enabled.checked, command: command.value.trim(), args: args.value.trim() || "[]", local_url: localUrl.value.trim(),\n              realtime_transport:', 'update enabled payload')
s = once(s, '        del.addEventListener("click", async () => {\n', '        test.addEventListener("click", async () => {\n          test.disabled = true; message.textContent = "Testing…";\n          try {\n            const response = await fetch("/api/mcp-test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ server: name }) });\n            const data = await fetchJsonOrThrow(response);\n            message.textContent = `${data.healthy ? "Healthy" : "Unavailable"} · ${displayMcpTransport(data.tested_transport || data.configured_transport)} · ${data.detail || ""}`;\n          } catch (error) { message.textContent = `Test failed: ${error.message || error}`; }\n          finally { test.disabled = false; }\n        });\n        del.addEventListener("click", async () => {\n', 'test listener')
s = once(s, '        section.append(makeCfgField("STDIO command", command), makeCfgField("STDIO args (JSON)", args), makeCfgField("Local MCP URL", localUrl), makeCfgField("Transport", controls.transport), controls.httpsField, makeCfgField("Permission", controls.permission), save, del, message);', '        section.append(makeCfgField("Enabled", enabled), makeCfgField("STDIO command", command), makeCfgField("STDIO args (JSON)", args), makeCfgField("Local MCP URL", localUrl), makeCfgField("Transport", controls.transport), controls.httpsField, makeCfgField("Permission", controls.permission), auth, test, save, del, message);', 'editor controls append')
p.write_text(s, encoding='utf-8')
