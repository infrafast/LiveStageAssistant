from pathlib import Path


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one occurrence, got {count}")
    return text.replace(old, new, 1)


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# RV4/RV6: provider-neutral realtime service + bounded provider reconnect.
# ---------------------------------------------------------------------------
p = Path("voice_assistant/realtime/service.py")
s = p.read_text(encoding="utf-8")
s = s.replace('"""Integrated OpenAI Realtime runtime for LiveStageAssistant service mode."""', '"""Integrated provider-neutral Realtime runtime for LiveStageAssistant."""')
s = s.replace("from .openai_realtime import OpenAIRealtimeEngine\n", "from .provider_factory import create_realtime_engine\n")
s = once(
    s,
    'DEFAULT_MODEL = "gpt-realtime-2.1"\nDEFAULT_VOICE = "marin"\n',
    'DEFAULT_MODEL = "gpt-realtime-2.1"\nDEFAULT_VOICE = "marin"\nDEFAULT_GEMINI_MODEL = "gemini-3.1-flash-live-preview"\nDEFAULT_GEMINI_VOICE = "Kore"\nTRANSIENT_PROVIDER_EXIT = 75\n_RECENT_BRIDGE_CALL_IDS: set[str] = set()\n\n',
    "service defaults",
)
s = once(
    s,
    '''async def execute_bridge_call(engine, bridge: RealtimeMCPBridge, event, completed_calls: set[str]) -> None:\n    call_id = str(event.data.get("call_id") or "")\n    name = str(event.data.get("name") or "")\n    arguments = str(event.data.get("arguments") or "{}")\n    if not call_id or call_id in completed_calls:\n        return\n    completed_calls.add(call_id)\n    target = bridge.tool_targets.get(name)\n    started = time.perf_counter()\n    print("Realtime bridge call " + json.dumps({"server": target.server if target else None, "tool": target.tool if target else name, "arguments": arguments}, ensure_ascii=False, separators=(",", ":")), flush=True)\n    try:\n        result = await bridge.execute(name, arguments)\n    except Exception as exc:\n        result = {"is_error": True, "error": str(exc)}\n    print(f"Realtime bridge result: call_id={call_id} duration_ms={(time.perf_counter()-started)*1000:.1f}", flush=True)\n    await engine.submit_tool_result(call_id, result)\n''',
    '''async def execute_bridge_call(engine, bridge: RealtimeMCPBridge, event, completed_calls: set[str]) -> None:\n    call_id = str(event.data.get("call_id") or "")\n    name = str(event.data.get("name") or "")\n    arguments = str(event.data.get("arguments") or "{}")\n    if not call_id or call_id in completed_calls:\n        return\n    if len(completed_calls) >= 2048:\n        completed_calls.clear()\n    completed_calls.add(call_id)\n    target = bridge.tool_targets.get(name)\n    started = time.perf_counter()\n    print("Realtime bridge call " + json.dumps({"server": target.server if target else None, "tool": target.tool if target else name, "arguments": arguments}, ensure_ascii=False, separators=(",", ":")), flush=True)\n    timeout = max(1.0, float(os.getenv("MCP_AGENT_TIMEOUT_SECONDS", "20") or "20"))\n    try:\n        result = await asyncio.wait_for(bridge.execute(name, arguments), timeout=timeout)\n    except asyncio.TimeoutError:\n        result = {"is_error": True, "error": f"MCP call timed out after {timeout:.1f}s; execution state is not replayed automatically"}\n    except Exception as exc:\n        result = {"is_error": True, "error": str(exc)}\n    print(f"Realtime bridge result: call_id={call_id} duration_ms={(time.perf_counter()-started)*1000:.1f}", flush=True)\n    try:\n        await engine.submit_tool_result(call_id, result)\n    except Exception as exc:\n        # The MCP call may already have changed external state. Never replay it\n        # merely because the provider connection disappeared before receiving\n        # the tool result.\n        print(f"Realtime bridge result delivery failed: call_id={call_id} error={exc}", flush=True)\n''',
    "safe bridge execution",
)
s = once(
    s,
    '''    semantic: SemanticAudioController,\n) -> None:\n    current_response_id = ""\n    completed_calls: set[str] = set()\n''',
    '''    semantic: SemanticAudioController,\n    provider_failure: asyncio.Event,\n) -> None:\n    current_response_id = ""\n    completed_calls = _RECENT_BRIDGE_CALL_IDS\n''',
    "event loop signature",
)
s = once(
    s,
    '''        if event.type == "speech_started":\n            print("Realtime speech started", flush=True)\n            if current_response_id:\n                interrupted.add(current_response_id)\n                clear_queue(queue)\n''',
    '''        if event.type == "speech_started":\n            print("Realtime speech started", flush=True)\n            if current_response_id:\n                interrupted.add(current_response_id)\n                clear_queue(queue)\n                try:\n                    await engine.cancel_response()\n                except Exception as exc:\n                    print(f"Realtime cancellation warning: {exc}", flush=True)\n''',
    "barge in cancellation",
)
s = once(
    s,
    '''            metrics = {"pipeline":"realtime","provider":"openai","model":engine.config.model,"turn":turn,"response_id":response_id,"speech_end_to_first_audio_ms":round((first_audio_received[response_id]-speech_end)*1000,1) if speech_end is not None and response_id in first_audio_received else None,"speech_end_to_first_playback_ms":round((first_played[response_id]-speech_end)*1000,1) if speech_end is not None and response_id in first_played else None,"response_start_to_done_ms":round((now-response_started[response_id])*1000,1) if response_id in response_started else None,"usage":event.data.get("usage") or {}}\n            metrics["cost_usd"] = realtime_usage_cost_usd(engine.config.model, metrics["usage"])\n''',
    '''            metrics = {"pipeline":"realtime","provider":engine.config.provider,"model":engine.config.model,"turn":turn,"response_id":response_id,"speech_end_to_first_audio_ms":round((first_audio_received[response_id]-speech_end)*1000,1) if speech_end is not None and response_id in first_audio_received else None,"speech_end_to_first_playback_ms":round((first_played[response_id]-speech_end)*1000,1) if speech_end is not None and response_id in first_played else None,"response_start_to_done_ms":round((now-response_started[response_id])*1000,1) if response_id in response_started else None,"usage":event.data.get("usage") or {}}\n            metrics["cost_usd"] = realtime_usage_cost_usd(engine.config.model, metrics["usage"]) if engine.config.provider == "openai" else None\n''',
    "provider metrics",
)
s = once(
    s,
    '''        elif event.type in {"provider_error", "connection_error"}:\n            print(f"Realtime provider error: {event.data}", flush=True)\n        elif event.type == "connection_closed":\n            print("Realtime connection closed", flush=True)\n            stop_event.set()\n''',
    '''        elif event.type in {"provider_error", "connection_error"}:\n            print(f"Realtime provider error: {event.data}", flush=True)\n            provider_failure.set()\n        elif event.type == "connection_closed":\n            print("Realtime connection closed", flush=True)\n            provider_failure.set()\n            stop_event.set()\n''',
    "provider failure signal",
)
s = once(
    s,
    '''    api_key = read_secret("OPENAI_API_KEY", env_file)\n    if not api_key:\n        raise RuntimeError("OPENAI_API_KEY / OPENAI_API_KEY_FILE is not configured")\n    model = str(os.getenv("OPENAI_REALTIME_MODEL") or DEFAULT_MODEL).strip()\n    voice = str(os.getenv("OPENAI_REALTIME_VOICE") or DEFAULT_VOICE).strip()\n''',
    '''    provider = str(os.getenv("LSA_REALTIME_PROVIDER") or "openai").strip().lower()\n    if provider == "gemini":\n        api_key = read_secret("GEMINI_API_KEY", env_file)\n        if not api_key:\n            raise RuntimeError("GEMINI_API_KEY / GEMINI_API_KEY_FILE is not configured")\n        model = str(os.getenv("GEMINI_LIVE_MODEL") or DEFAULT_GEMINI_MODEL).strip()\n        voice = str(os.getenv("GEMINI_LIVE_VOICE") or DEFAULT_GEMINI_VOICE).strip()\n    elif provider == "openai":\n        api_key = read_secret("OPENAI_API_KEY", env_file)\n        if not api_key:\n            raise RuntimeError("OPENAI_API_KEY / OPENAI_API_KEY_FILE is not configured")\n        model = str(os.getenv("OPENAI_REALTIME_MODEL") or DEFAULT_MODEL).strip()\n        voice = str(os.getenv("OPENAI_REALTIME_VOICE") or DEFAULT_VOICE).strip()\n    else:\n        raise RuntimeError(f"unsupported realtime provider: {provider!r}")\n''',
    "provider config",
)
s = once(
    s,
    '''    for server in inventory.values():\n        transport = server.realtime.transport\n        if transport == "auto":\n''',
    '''    for server in inventory.values():\n        transport = server.realtime.transport\n        if provider == "gemini":\n            if server.realtime.permissions.mode == "approval":\n                raise RuntimeError(f"Gemini bridge approval is not implemented for MCP {server.name!r}; use Open or disable the server")\n            if transport == "native":\n                raise RuntimeError(f"Gemini Live has no provider-native MCP adapter for {server.name!r}; select Auto or STDIO")\n            if transport == "auto":\n                local_ok, local_reason = await probe_local_stdio(raw_config, server)\n                if not local_ok:\n                    raise RuntimeError(f"AUTO MCP {server.name!r} has no healthy local bridge for Gemini: {local_reason}")\n                print(f"Realtime MCP auto selection: {server.name} -> stdio ({local_reason})", flush=True)\n            bridge_names.append(server.name)\n            continue\n        if transport == "auto":\n''',
    "gemini mcp routing",
)
s = once(
    s,
    '''    interrupted: set[str] = set()\n    first_played: dict[str, float] = {}\n''',
    '''    interrupted: set[str] = set()\n    first_played: dict[str, float] = {}\n    provider_failure = asyncio.Event()\n''',
    "provider failure event",
)
s = once(
    s,
    '''        engine = OpenAIRealtimeEngine(RealtimeEngineConfig(provider="openai", model=model, voice=voice, instructions=DEFAULT_BASE_PROMPT, server_vad=True, mcp_servers=tuple(native_servers), function_tools=tuple(function_tools)), api_key=api_key)\n        await engine.start()\n        await wait_until_ready(engine)\n        print(f"LSA Realtime ready: model={model} voice={voice}", flush=True)\n''',
    '''        engine = create_realtime_engine(provider, RealtimeEngineConfig(provider=provider, model=model, voice=voice, instructions=DEFAULT_BASE_PROMPT, server_vad=True, mcp_servers=tuple(native_servers), function_tools=tuple(function_tools)), api_key=api_key)\n        await engine.start()\n        await wait_until_ready(engine)\n        print(f"LSA Realtime ready: provider={provider} model={model} voice={voice}", flush=True)\n''',
    "provider factory",
)
s = once(
    s,
    '''            asyncio.create_task(event_loop(engine, bridge, queue, interrupted, first_played, stop_event, semantic), name="lsa-realtime-events"),\n''',
    '''            asyncio.create_task(event_loop(engine, bridge, queue, interrupted, first_played, stop_event, semantic, provider_failure), name="lsa-realtime-events"),\n''',
    "event task failure arg",
)
s = once(
    s,
    '''        await stop_event.wait()\n        return 0\n''',
    '''        await stop_event.wait()\n        return TRANSIENT_PROVIDER_EXIT if provider_failure.is_set() else 0\n''',
    "transient exit",
)
s = once(
    s,
    '''    try:\n        return asyncio.run(run(args))\n    except KeyboardInterrupt:\n        return 130\n    except Exception as exc:\n        print(f"LSA Realtime failed: {exc}", file=sys.stderr, flush=True)\n        return 1\n''',
    '''    attempts = max(0, int(os.getenv("REALTIME_RECONNECT_ATTEMPTS", "3") or "3"))\n    backoff = max(0.2, float(os.getenv("REALTIME_RECONNECT_BACKOFF_SECONDS", "1.0") or "1.0"))\n    for attempt in range(attempts + 1):\n        try:\n            code = asyncio.run(run(args))\n        except KeyboardInterrupt:\n            return 130\n        except Exception as exc:\n            print(f"LSA Realtime failed: {exc}", file=sys.stderr, flush=True)\n            code = TRANSIENT_PROVIDER_EXIT\n        if code != TRANSIENT_PROVIDER_EXIT:\n            return code\n        if attempt >= attempts:\n            print("LSA Realtime reconnect budget exhausted", file=sys.stderr, flush=True)\n            return 1\n        delay = min(8.0, backoff * (2 ** attempt))\n        print(f"LSA Realtime reconnect: attempt={attempt + 1}/{attempts} delay={delay:.1f}s", flush=True)\n        time.sleep(delay)\n    return 1\n''',
    "reconnect loop",
)
write(str(p), s)

# Gemini call-id -> function-name mapping for tool responses.
p = Path("voice_assistant/realtime/gemini_live.py")
s = p.read_text(encoding="utf-8")
s = once(s, '        self._last_usage: dict[str, Any] = {}\n', '        self._last_usage: dict[str, Any] = {}\n        self._call_names: dict[str, str] = {}\n', "gemini call names init")
s = once(s, '        self._cancelled = False\n        self.state = RealtimeEngineState.STOPPED\n', '        self._cancelled = False\n        self._call_names.clear()\n        self.state = RealtimeEngineState.STOPPED\n', "gemini clear call names")
s = once(s, '''        payload = result if isinstance(result, dict) else {"result": result}\n        await self._send(\n            {\n                "toolResponse": {\n                    "functionResponses": [\n                        {"id": call_id, "name": call_id.split(":", 1)[-1], "response": payload}\n                    ]\n                }\n            }\n        )\n''', '''        payload = result if isinstance(result, dict) else {"result": result}\n        name = self._call_names.pop(call_id, "")\n        if not name:\n            raise ValueError(f"unknown Gemini function call id: {call_id}")\n        await self._send(\n            {\n                "toolResponse": {\n                    "functionResponses": [\n                        {"id": call_id, "name": name, "response": payload}\n                    ]\n                }\n            }\n        )\n''', "gemini tool response name")
s = once(s, '''                if not call_id:\n                    call_id = f"{self._active_turn or self._new_turn()}:{name}"\n                events.append(\n''', '''                if not call_id:\n                    call_id = f"{self._active_turn or self._new_turn()}:{name}"\n                self._call_names[call_id] = name\n                events.append(\n''', "gemini remember function name")
write(str(p), s)

# ---------------------------------------------------------------------------
# RV6 runtime selection + status.
# ---------------------------------------------------------------------------
p = Path("voice_assistant/runtime.py")
s = p.read_text(encoding="utf-8")
s = s.replace('{"classic", "openai-realtime"}', '{"classic", "openai-realtime", "gemini-live"}')
s = once(
    s,
    '''    if engine == "openai-realtime":\n        return (\n            "openai",\n            str(values.get("OPENAI_REALTIME_MODEL") or "gpt-realtime-2.1").strip(),\n            str(values.get("OPENAI_REALTIME_VOICE") or "marin").strip(),\n        )\n''',
    '''    if engine == "openai-realtime":\n        return (\n            "openai",\n            str(values.get("OPENAI_REALTIME_MODEL") or "gpt-realtime-2.1").strip(),\n            str(values.get("OPENAI_REALTIME_VOICE") or "marin").strip(),\n        )\n    if engine == "gemini-live":\n        return (\n            "gemini",\n            str(values.get("GEMINI_LIVE_MODEL") or "gemini-3.1-flash-live-preview").strip(),\n            str(values.get("GEMINI_LIVE_VOICE") or "Kore").strip(),\n        )\n''',
    "runtime identity",
)
s = s.replace('return "LSA Realtime ready:" if engine == "openai-realtime" else CLASSIC_READY_MARKER', 'return "LSA Realtime ready:" if engine in {"openai-realtime", "gemini-live"} else CLASSIC_READY_MARKER')
s = s.replace('if engine != "openai-realtime":', 'if engine not in {"openai-realtime", "gemini-live"}:')
write(str(p), s)

# ---------------------------------------------------------------------------
# RV2F/RV6: common configuration gets READY cue + alternate live provider.
# ---------------------------------------------------------------------------
p = Path("voice_assistant/runtime_web_services.py")
s = p.read_text(encoding="utf-8")
s = once(
    s,
    '''            "selected_thinking_sound_file": str(values.get("THINKING_SOUND_FILE") or "thinking.wav").strip(),\n            "selected_listening_sound_file": str(values.get("LISTENING_SOUND_FILE") or "").strip(),\n''',
    '''            "selected_thinking_sound_file": str(values.get("THINKING_SOUND_FILE") or "thinking.wav").strip(),\n            "selected_ready_sound_file": str(values.get("READY_SOUND_FILE") or "").strip(),\n            "selected_listening_sound_file": str(values.get("LISTENING_SOUND_FILE") or "").strip(),\n''',
    "ready cue options",
)
s = once(
    s,
    '''            "selected_voice_engine": str(values.get("VOICE_ENGINE") or ("local" if connectivity == "offline" else "classic")).strip().lower(),\n            "selected_realtime_model": str(values.get("OPENAI_REALTIME_MODEL") or "gpt-realtime-2.1").strip(),\n            "selected_realtime_voice": str(values.get("OPENAI_REALTIME_VOICE") or "marin").strip(),\n''',
    '''            "selected_voice_engine": str(values.get("VOICE_ENGINE") or ("local" if connectivity == "offline" else "classic")).strip().lower(),\n            "selected_realtime_model": (str(values.get("GEMINI_LIVE_MODEL") or "gemini-3.1-flash-live-preview").strip() if str(values.get("VOICE_ENGINE") or "").strip().lower() == "gemini-live" else str(values.get("OPENAI_REALTIME_MODEL") or "gpt-realtime-2.1").strip()),\n            "selected_realtime_voice": (str(values.get("GEMINI_LIVE_VOICE") or "Kore").strip() if str(values.get("VOICE_ENGINE") or "").strip().lower() == "gemini-live" else str(values.get("OPENAI_REALTIME_VOICE") or "marin").strip()),\n''',
    "alternate realtime selection",
)
s = once(s, '        thinking_sound_file: str,\n        listening_sound_file: str,\n', '        thinking_sound_file: str,\n        ready_sound_file: str,\n        listening_sound_file: str,\n', "ready cue save signature")
s = once(
    s,
    '''        allowed_engines = {"local"} if active_connectivity == "offline" else {"classic", "openai-realtime"}\n''',
    '''        allowed_engines = {"local"} if active_connectivity == "offline" else {"classic", "openai-realtime", "gemini-live"}\n''',
    "allowed engines",
)
s = once(
    s,
    '''        if requested_engine == "openai-realtime":\n            realtime_model = str(realtime_model or "gpt-realtime-2.1").strip()\n            realtime_voice = str(realtime_voice or "marin").strip()\n            if not realtime_model or not realtime_voice:\n                raise ValueError("Realtime model and voice are required")\n''',
    '''        if requested_engine in {"openai-realtime", "gemini-live"}:\n            default_model = "gemini-3.1-flash-live-preview" if requested_engine == "gemini-live" else "gpt-realtime-2.1"\n            default_voice = "Kore" if requested_engine == "gemini-live" else "marin"\n            realtime_model = str(realtime_model or default_model).strip()\n            realtime_voice = str(realtime_voice or default_voice).strip()\n            if not realtime_model or not realtime_voice:\n                raise ValueError("Realtime model and voice are required")\n''',
    "realtime validation",
)
s = once(s, '            "THINKING_SOUND_FILE": str(thinking_sound_file or "").strip(),\n            "LISTENING_SOUND_FILE":', '            "THINKING_SOUND_FILE": str(thinking_sound_file or "").strip(),\n            "READY_SOUND_FILE": str(ready_sound_file or "").strip(),\n            "LISTENING_SOUND_FILE":', "ready cue env")
s = once(
    s,
    '''        if requested_engine == "openai-realtime":\n            updates["OPENAI_REALTIME_MODEL"] = realtime_model\n            updates["OPENAI_REALTIME_VOICE"] = realtime_voice\n''',
    '''        if requested_engine == "openai-realtime":\n            updates["OPENAI_REALTIME_MODEL"] = realtime_model\n            updates["OPENAI_REALTIME_VOICE"] = realtime_voice\n        elif requested_engine == "gemini-live":\n            updates["GEMINI_LIVE_MODEL"] = realtime_model\n            updates["GEMINI_LIVE_VOICE"] = realtime_voice\n''',
    "save alternate realtime",
)
s = once(
    s,
    '''        eleven_present = self._secret_present(values, "ELEVENLABS_API_KEY")\n        return {\n''',
    '''        eleven_present = self._secret_present(values, "ELEVENLABS_API_KEY")\n        gemini_present = self._secret_present(values, "GEMINI_API_KEY")\n        return {\n''',
    "gemini cloud status presence",
)
s = once(
    s,
    '''            "elevenlabs": {\n                "status": "configured" if eleven_present else "missing",\n                "masked_key": "configured" if eleven_present else "",\n                "lines": ["API key configured." if eleven_present else "ELEVENLABS_API_KEY_FILE is not configured."],\n            },\n        }\n''',
    '''            "elevenlabs": {\n                "status": "configured" if eleven_present else "missing",\n                "masked_key": "configured" if eleven_present else "",\n                "lines": ["API key configured." if eleven_present else "ELEVENLABS_API_KEY_FILE is not configured."],\n            },\n            "gemini": {\n                "status": "configured" if gemini_present else "missing",\n                "masked_key": "configured" if gemini_present else "",\n                "lines": ["API key configured." if gemini_present else "GEMINI_API_KEY_FILE is not configured."],\n            },\n        }\n''',
    "gemini cloud status",
)
write(str(p), s)

# Base HTTP form parser: READY cue belongs to the same unified Save.
p = Path("voice_assistant/web_monitor_base.py")
s = p.read_text(encoding="utf-8")
s = once(s, '                    thinking_sound_file = str(payload.get("thinking_sound_file") or "").strip()\n                    listening_sound_file =', '                    thinking_sound_file = str(payload.get("thinking_sound_file") or "").strip()\n                    ready_sound_file = str(payload.get("ready_sound_file") or "").strip()\n                    listening_sound_file =', "parse ready cue")
s = once(s, '                            thinking_sound_file,\n                            listening_sound_file,\n', '                            thinking_sound_file,\n                            ready_sound_file,\n                            listening_sound_file,\n', "pass ready cue")
write(str(p), s)

# ---------------------------------------------------------------------------
# RV7: ephemeral browser auth route, main API key stays server-side.
# ---------------------------------------------------------------------------
p = Path("voice_assistant/web_monitor.py")
s = p.read_text(encoding="utf-8")
s = s.replace('from pathlib import Path\n', 'from pathlib import Path\n\nfrom dotenv import dotenv_values\n')
s = s.replace('    from .runtime_status import read_status_file\n', '    from .runtime_status import read_status_file\n    from .realtime.browser_auth import create_openai_browser_client_secret\n')
s = s.replace('    from runtime_status import read_status_file\n', '    from runtime_status import read_status_file\n    from realtime.browser_auth import create_openai_browser_client_secret\n')
insert = '''\n    def _browser_realtime_secret(self) -> dict[str, Any]:\n        snapshot = super().snapshot()\n        env_file = Path(str(snapshot.get("env_file") or "")).expanduser()\n        if not env_file.is_file():\n            raise RuntimeError("active runtime profile is unavailable")\n        values = dict(dotenv_values(env_file))\n        if str(values.get("VOICE_ENGINE") or "").strip().lower() != "openai-realtime":\n            raise ValueError("browser WebRTC is available only with OpenAI Realtime selected")\n        secret_path = str(values.get("OPENAI_API_KEY_FILE") or "").strip()\n        api_key = str(values.get("OPENAI_API_KEY") or "").strip()\n        if not api_key and secret_path:\n            path = Path(secret_path).expanduser()\n            if not path.is_absolute():\n                path = env_file.parent / path\n            try:\n                api_key = path.read_text(encoding="utf-8").strip()\n            except OSError as exc:\n                raise RuntimeError(f"could not read OPENAI_API_KEY_FILE: {exc}") from exc\n        return create_openai_browser_client_secret(\n            api_key,\n            model=str(values.get("OPENAI_REALTIME_MODEL") or "gpt-realtime-2.1").strip(),\n            voice=str(values.get("OPENAI_REALTIME_VOICE") or "marin").strip(),\n            instructions=str(values.get("ASSISTANT_SYSTEM_PROMPT") or "").strip(),\n        )\n'''
s = once(s, '    def _runtime_status(self) -> dict[str, Any]:\n', insert + '\n    def _runtime_status(self) -> dict[str, Any]:\n', "browser auth method")
s = once(s, '                        routes = {"/api/mcp-realtime-policy", "/api/mcp-server", "/api/mcp-test", "/api/runtime-restart"}\n', '                        routes = {"/api/mcp-realtime-policy", "/api/mcp-server", "/api/mcp-test", "/api/runtime-restart", "/api/realtime-browser-secret"}\n', "browser auth route")
s = once(s, '                        if parsed.path == "/api/runtime-restart":\n                            self._handle_runtime_restart(); return\n', '                        if parsed.path == "/api/runtime-restart":\n                            self._handle_runtime_restart(); return\n                        if parsed.path == "/api/realtime-browser-secret":\n                            self._handle_realtime_browser_secret(); return\n', "browser auth dispatch")
s = once(
    s,
    '                    def _handle_runtime_restart(self) -> None:\n',
    '''                    def _handle_realtime_browser_secret(self) -> None:\n                        payload = self._read_json_body(max_bytes=4 * 1024)\n                        if payload is None: return\n                        try:\n                            result = monitor._browser_realtime_secret()\n                        except ValueError as error:\n                            self._send_json_error(400, {"ok": False, "error": {"message": str(error)}}); return\n                        except Exception as error:\n                            self._send_json_error(503, {"ok": False, "error": {"message": str(error)}}); return\n                        self._send_json({"ok": True, "client_secret": result})\n\n                    def _handle_runtime_restart(self) -> None:\n''',
    "browser secret handler",
)
write(str(p), s)

# ---------------------------------------------------------------------------
# Unified GUI additions: Gemini, READY cue and optional browser WebRTC test.
# ---------------------------------------------------------------------------
p = Path("assets/web/index.html")
s = p.read_text(encoding="utf-8")
# Voice engine select is kept one common selector.
s = s.replace('<option value="openai-realtime">OpenAI Realtime</option>', '<option value="openai-realtime">OpenAI Realtime</option><option value="gemini-live">Gemini Live</option>')
if 'value="gemini-live"' not in s:
    raise SystemExit("index voice engine option insertion failed")
# Add browser WebRTC test next to the common realtime voice control.
needle = '<div class="field hidden" id="realtime-voice-field"><label for="realtime-voice">Realtime voice</label><input id="realtime-voice" type="text" autocomplete="off" spellcheck="false" placeholder="marin"><div class="field-hint">Voice used by the active realtime speech engine.</div></div>'
replacement = needle + '<div class="field hidden" id="realtime-browser-field"><label>Browser WebRTC</label><button class="small-button" id="realtime-browser-toggle" type="button">Start browser realtime</button><div class="field-hint" id="realtime-browser-status">OpenAI Realtime only; uses a short-lived browser credential.</div></div>'
s = once(s, needle, replacement, "browser WebRTC field")
# READY cue beside the existing semantic cue controls.
needle = '<div class="field" id="listening-sound-field">'
ready_html = '<div class="field" id="ready-sound-field"><label for="ready-sound">Ready sound</label><div class="sound-control"><select id="ready-sound"></select><button class="icon-button sound-play" id="ready-sound-play" type="button" title="Play sample" aria-label="Play sample">&#9654;</button></div><div class="field-hint">READY_SOUND_FILE · optional one-shot cue when the engine is ready.</div></div>'
s = once(s, needle, ready_html + needle, "ready cue field")
write(str(p), s)

p = Path("assets/web/app.js")
s = p.read_text(encoding="utf-8")
s = once(s, '    const listeningSoundField = document.querySelector("#listening-sound-field");\n', '    const readySoundField = document.querySelector("#ready-sound-field");\n    const readySound = document.querySelector("#ready-sound");\n    const readySoundPlay = document.querySelector("#ready-sound-play");\n    const listeningSoundField = document.querySelector("#listening-sound-field");\n', "ready cue selectors")
s = once(s, '    const realtimeVoiceField = document.querySelector("#realtime-voice-field");\n', '    const realtimeVoiceField = document.querySelector("#realtime-voice-field");\n    const realtimeBrowserField = document.querySelector("#realtime-browser-field");\n    const realtimeBrowserToggle = document.querySelector("#realtime-browser-toggle");\n    const realtimeBrowserStatus = document.querySelector("#realtime-browser-status");\n', "browser selectors")
s = once(s, '    let currentClassicCloudSpeech = true;\n', '    let currentClassicCloudSpeech = true;\n    let realtimeBrowserPeer = null;\n    let realtimeBrowserStream = null;\n    let realtimeBrowserAudio = null;\n', "browser state")
s = once(s, '        thinking_sound_file: thinkingSound.value || "",\n        listening_sound_file:', '        thinking_sound_file: thinkingSound.value || "",\n        ready_sound_file: readySound.value || "",\n        listening_sound_file:', "ready cue signature")
s = once(s, '      const realtime = !offline && voiceEngine.value === "openai-realtime";\n', '      const realtime = !offline && ["openai-realtime", "gemini-live"].includes(voiceEngine.value);\n      const browserRealtime = !offline && voiceEngine.value === "openai-realtime";\n', "realtime engine family")
s = once(s, '      realtimeVoiceField.classList.toggle("hidden", !realtime);\n', '      realtimeVoiceField.classList.toggle("hidden", !realtime);\n      realtimeBrowserField.classList.toggle("hidden", !browserRealtime);\n      if (!browserRealtime && realtimeBrowserPeer) stopBrowserRealtime();\n', "browser visibility")
# Load READY cue from same sound inventory.
needle = '        listeningSound.replaceChildren();\n'
ready_loader = '''        readySound.replaceChildren();\n        const selectedReadySound = data.selected_ready_sound_file || "";\n        readySound.appendChild(option("No ready sound", "", false, !selectedReadySound));\n        for (const sound of sounds) {\n          readySound.appendChild(option(sound.label || sound.id, sound.id, false, sound.id === selectedReadySound));\n        }\n        if (selectedReadySound && !sounds.some((sound) => sound.id === selectedReadySound)) {\n          readySound.appendChild(option(`${selectedReadySound} (${tr("current", "current")})`, selectedReadySound, false, true));\n        }\n\n'''
s = once(s, needle, ready_loader + needle, "ready cue load")
s = once(s, '        listeningSound.disabled = listeningSound.options.length === 0;\n', '        readySound.disabled = readySound.options.length === 0;\n        listeningSound.disabled = listeningSound.options.length === 0;\n', "ready cue enabled")
# Semantic preview behaviour identical to other backend cue sounds.
s = once(s, '      const listeningBackendUnavailable = !backendAudioCapabilities.output;\n', '      const listeningBackendUnavailable = !backendAudioCapabilities.output;\n      readySound.disabled = listeningBackendUnavailable;\n      readySoundPlay.disabled = !readySound.value || listeningBackendUnavailable;\n      readySoundField.title = listeningBackendUnavailable ? backendUnavailableReason : "READY_SOUND_FILE";\n      readySound.title = readySoundField.title;\n      readySoundPlay.title = readySoundField.title;\n\n', "ready cue preview state")
s = once(s, '    listeningSound.addEventListener("change", () => {\n', '    readySound.addEventListener("change", () => { stopCurrentAudioSample(); syncAudioSampleControls(); });\n    listeningSound.addEventListener("change", () => {\n', "ready cue change")
s = once(s, '    listeningSoundPlay.addEventListener("click", () => toggleAudioSample(listeningSound, listeningSoundPlay, true));\n', '    readySoundPlay.addEventListener("click", () => toggleAudioSample(readySound, readySoundPlay, true));\n    listeningSoundPlay.addEventListener("click", () => toggleAudioSample(listeningSound, listeningSoundPlay, true));\n', "ready cue play")
s = once(s, '      const listeningSoundFile = listeningSound.value;\n', '      const readySoundFile = readySound.value;\n      const listeningSoundFile = listeningSound.value;\n', "ready cue save variable")
s = once(s, '            thinking_sound_file: thinkingSoundFile,\n            listening_sound_file:', '            thinking_sound_file: thinkingSoundFile,\n            ready_sound_file: readySoundFile,\n            listening_sound_file:', "ready cue payload")
# Browser WebRTC functions: one optional diagnostic path, no duplicate config screen.
browser_functions = r'''
    async function stopBrowserRealtime() {
      if (realtimeBrowserPeer) {
        try { realtimeBrowserPeer.close(); } catch (_) {}
      }
      realtimeBrowserPeer = null;
      if (realtimeBrowserStream) {
        for (const track of realtimeBrowserStream.getTracks()) track.stop();
      }
      realtimeBrowserStream = null;
      if (realtimeBrowserAudio) {
        realtimeBrowserAudio.pause();
        realtimeBrowserAudio.srcObject = null;
        realtimeBrowserAudio.remove();
      }
      realtimeBrowserAudio = null;
      if (realtimeBrowserToggle) realtimeBrowserToggle.textContent = "Start browser realtime";
      if (realtimeBrowserStatus) realtimeBrowserStatus.textContent = "Stopped.";
    }

    async function startBrowserRealtime() {
      if (realtimeBrowserPeer) { await stopBrowserRealtime(); return; }
      if (!window.isSecureContext && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
        throw new Error("Browser microphone/WebRTC requires HTTPS or localhost.");
      }
      realtimeBrowserToggle.disabled = true;
      realtimeBrowserStatus.textContent = "Creating short-lived Realtime session…";
      try {
        const secretResponse = await fetch("/api/realtime-browser-secret", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}"
        });
        const secretData = await fetchJsonOrThrow(secretResponse);
        const ephemeral = secretData?.client_secret?.value || "";
        if (!ephemeral) throw new Error("No browser client secret returned.");

        const pc = new RTCPeerConnection();
        realtimeBrowserPeer = pc;
        const audio = document.createElement("audio");
        audio.autoplay = true;
        audio.hidden = true;
        document.body.appendChild(audio);
        realtimeBrowserAudio = audio;
        pc.ontrack = (event) => { audio.srcObject = event.streams[0]; };
        pc.onconnectionstatechange = () => {
          realtimeBrowserStatus.textContent = `WebRTC: ${pc.connectionState}`;
          if (["failed", "closed"].includes(pc.connectionState)) stopBrowserRealtime();
        };
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        realtimeBrowserStream = stream;
        for (const track of stream.getTracks()) pc.addTrack(track, stream);
        pc.createDataChannel("oai-events");
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        const answerResponse = await fetch("https://api.openai.com/v1/realtime/calls", {
          method: "POST",
          headers: {
            "Authorization": `Bearer ${ephemeral}`,
            "Content-Type": "application/sdp"
          },
          body: offer.sdp
        });
        if (!answerResponse.ok) throw new Error(await answerResponse.text());
        await pc.setRemoteDescription({ type: "answer", sdp: await answerResponse.text() });
        realtimeBrowserToggle.textContent = "Stop browser realtime";
        realtimeBrowserStatus.textContent = "WebRTC connected; server API key remains on the Pi.";
      } catch (error) {
        await stopBrowserRealtime();
        realtimeBrowserStatus.textContent = `WebRTC failed: ${error.message || error}`;
        throw error;
      } finally {
        realtimeBrowserToggle.disabled = false;
      }
    }

'''
s = once(s, '    function syncVoiceEngineControls() {\n', browser_functions + '    function syncVoiceEngineControls() {\n', "browser WebRTC functions")
s = once(s, '    realtimeVoice.addEventListener("input", syncConfigActionState);\n', '    realtimeVoice.addEventListener("input", syncConfigActionState);\n    realtimeBrowserToggle.addEventListener("click", () => startBrowserRealtime().catch(() => {}));\n', "browser WebRTC click")
write(str(p), s)

# ---------------------------------------------------------------------------
# Env/documented knobs for RV4/RV6.
# ---------------------------------------------------------------------------
p = Path(".env.example")
s = p.read_text(encoding="utf-8")
append = '''\n# Realtime provider extensions\n# VOICE_ENGINE=gemini-live\nGEMINI_API_KEY_FILE=\nGEMINI_LIVE_MODEL=gemini-3.1-flash-live-preview\nGEMINI_LIVE_VOICE=Kore\nREALTIME_RECONNECT_ATTEMPTS=3\nREALTIME_RECONNECT_BACKOFF_SECONDS=1.0\nWAKE_WORD_POST_TTS_SUPPRESSION_MS=350\nREADY_SOUND_FILE=\n'''
if "GEMINI_LIVE_MODEL=" not in s:
    s += append
write(str(p), s)

# ---------------------------------------------------------------------------
# Tests for provider abstraction, wake/semantic invariants and browser auth.
# ---------------------------------------------------------------------------
Path("tests/test_realtime_provider_factory.py").write_text(r'''import unittest
from unittest.mock import patch

from voice_assistant.realtime.engine import RealtimeEngineConfig
from voice_assistant.realtime.gemini_live import GeminiLiveEngine
from voice_assistant.realtime.openai_realtime import OpenAIRealtimeEngine
from voice_assistant.realtime.provider_factory import create_realtime_engine


class RealtimeProviderFactoryTests(unittest.TestCase):
    def test_openai_provider(self):
        engine = create_realtime_engine("openai", RealtimeEngineConfig(provider="openai", model="m", voice="v"), api_key="key")
        self.assertIsInstance(engine, OpenAIRealtimeEngine)

    def test_gemini_provider(self):
        engine = create_realtime_engine("gemini", RealtimeEngineConfig(provider="gemini", model="m", voice="v"), api_key="key")
        self.assertIsInstance(engine, GeminiLiveEngine)

    def test_gemini_rejects_native_mcp(self):
        from voice_assistant.realtime.engine import RealtimeMCPServer
        config = RealtimeEngineConfig(provider="gemini", model="m", voice="v", mcp_servers=(RealtimeMCPServer(label="x", url="https://example.test/mcp"),))
        with self.assertRaises(ValueError):
            create_realtime_engine("gemini", config, api_key="key")


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")
Path("tests/test_realtime_browser_auth.py").write_text(r'''import io
import json
import unittest
from unittest.mock import patch

from voice_assistant.realtime.browser_auth import create_openai_browser_client_secret


class _Response:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self):
        return json.dumps({"value":"ek_test","expires_at":123,"session":{"model":"gpt-realtime-2.1"}}).encode()


class BrowserAuthTests(unittest.TestCase):
    @patch("voice_assistant.realtime.browser_auth.urlopen", return_value=_Response())
    def test_long_lived_key_is_only_authorization_header(self, mocked):
        result = create_openai_browser_client_secret("sk-server-secret", model="gpt-realtime-2.1", voice="marin", instructions="x")
        self.assertEqual(result["value"], "ek_test")
        request = mocked.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-server-secret")
        self.assertNotIn(b"sk-server-secret", request.data)


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")

# Roadmap: implemented code becomes [~], not [x] until the user's final Pi recipe.
p = Path("docs/ARCHITECTURE_AND_ROADMAP.md")
s = p.read_text(encoding="utf-8")
s = s.replace('- [ ] wake-enabled realtime uses local openWakeWord;', '- [~] wake-enabled realtime uses local openWakeWord; provider-neutral local gate implemented, final Pi recette pending;')
s = s.replace('- [ ] preserve `WAKE_WORD` across engine switching;', '- [~] preserve `WAKE_WORD` across engine switching; unified profile save and common wake gate implemented, final Pi recette pending;')
s = s.replace('- [ ] integrate RV2F semantic feedback contract with wake lifecycle;', '- [~] integrate RV2F semantic feedback contract with wake lifecycle; WAIT_WAKE/WAKE_DETECTED/re-arm wiring implemented;')
s = s.replace('- [ ] preserve Classic post-TTS suppression/re-arm semantics;', '- [~] preserve Classic post-TTS suppression/re-arm semantics; Realtime uses the same post-TTS suppression contract, consolidated comparison pending;')
s = s.replace('- [ ] production-service barge-in retest;', '- [~] production-service barge-in retest; response cancellation is wired, hardware retest pending;')
s = s.replace('- [ ] WebSocket/provider reconnect while Internet remains available;', '- [~] WebSocket/provider reconnect while Internet remains available; bounded exponential reconnect implemented, Pi/provider validation pending;')
s = s.replace('- [ ] cancellation around MCP calls;', '- [~] cancellation around MCP calls; speech cancellation does not cancel/replay already-dispatched MCP tasks;')
s = s.replace('- [ ] duplicate-call prevention across reconnects;', '- [~] duplicate-call prevention across reconnects; bounded call-id memory suppresses duplicate bridge dispatch within the child lifecycle;')
s = s.replace('- [ ] provider/session timeout handling;', '- [~] provider/session timeout handling; startup/tool timeouts and reconnect budget implemented;')
s = s.replace('- [ ] deterministic cleanup;', '- [~] deterministic cleanup; reconnect attempts reuse the existing deterministic session cleanup path;')
s = s.replace('- [ ] alternate provider behind same interface;', '- [~] alternate provider behind same interface; Gemini Live adapter/factory implemented, live validation pending;')
s = s.replace('- [ ] preserve bridge/STDIO regardless of provider-native MCP capability.', '- [~] preserve bridge/STDIO regardless of provider-native MCP capability; Gemini uses the common LSA bridge and rejects unsupported native mode explicitly.')
s = s.replace('- [ ] direct browser realtime transport;', '- [~] direct browser realtime transport; WebRTC diagnostic path implemented, browser validation pending;')
s = s.replace('- [ ] backend-mediated ephemeral authorization;', '- [~] backend-mediated ephemeral authorization; short-lived OpenAI client-secret endpoint implemented;')
s = s.replace('- [ ] secrets stay server-side;', '- [~] secrets stay server-side; browser receives only the short-lived client secret;')
write(str(p), s)
