#!/usr/bin/env python3
"""Single LiveStageAssistant runtime and engine supervisor.

The runtime owns connectivity, startup lifecycle, runtime status and the single
production WebMonitor. Voice engines are child processes: they never bind HTTP
or own GUI configuration.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
from typing import Mapping

from dotenv import dotenv_values

from voice_assistant.connectivity_manager import ConnectivityEvent, ConnectivityManager
from voice_assistant.engine_entry import CLASSIC_READY_MARKER
from voice_assistant.local_tts import speak_local_status
from voice_assistant.runtime_status import RuntimeStatus, RuntimeStatusTracker, configured_mcp_statuses
from voice_assistant.runtime_web_services import RuntimeWebServices
from voice_assistant.startup_lifecycle import StartupLoader
from voice_assistant.web_monitor import WebMonitor

ROOT = Path(__file__).resolve().parents[1]
AUTO_ENV_DIR = Path(os.getenv("ASSISTANT_AUTO_ENV_DIR", "/etc/livestageassistant"))
ONLINE_ENV = AUTO_ENV_DIR / ".env.online"
OFFLINE_ENV = AUTO_ENV_DIR / ".env.offline"
CONNECTIVITY_INTERVAL = float(os.getenv("LSA_CONNECTIVITY_CHECK_INTERVAL", "10") or "10")
STATUS_FILE = Path(os.getenv("LSA_RUNTIME_STATUS_FILE", "/tmp/livestageassistant-runtime-status.json"))
SEMANTIC_STATE_PREFIX = "LSA semantic state: "
VALID_SEMANTIC_STATES = {
    "starting",
    "ready",
    "wait_wake",
    "wake_detected",
    "listening",
    "processing",
    "result_ready",
    "speaking",
    "idle",
}


def _load_values(path: Path) -> dict[str, object]:
    return dict(dotenv_values(path))


def _env_bool(values: Mapping[str, object], key: str, default: bool = False) -> bool:
    raw = values.get(key)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _mcp_config_for_monitor(values: Mapping[str, object], profile: Path) -> dict[str, object]:
    raw = str(values.get("MCP_CONFIG") or "mcp_servers.json").strip()
    path = Path(os.path.expandvars(raw)).expanduser()
    if not path.is_absolute():
        candidates = (profile.parent / path, ROOT / path)
        path = next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def normalize_engine(values: Mapping[str, object], *, online: bool) -> str:
    if not online:
        return "local"
    engine = str(values.get("VOICE_ENGINE") or "classic").strip().lower()
    if engine not in {"classic", "openai-realtime"}:
        print(f"Invalid VOICE_ENGINE={engine!r}; falling back to classic.", flush=True)
        return "classic"
    return engine


def fallback_engine_candidates(values: Mapping[str, object], *, online: bool) -> tuple[str, ...]:
    if not online:
        return ()
    raw = str(values.get("VOICE_ENGINE_FALLBACK") or "classic").strip().lower()
    if raw in {"", "none", "off", "disabled", "false", "0"}:
        return ()
    result: list[str] = []
    for item in raw.split(","):
        name = item.strip()
        if name not in {"classic", "openai-realtime"}:
            print(f"Ignoring invalid VOICE_ENGINE_FALLBACK entry {name!r}.", flush=True)
            continue
        if name not in result:
            result.append(name)
    return tuple(result)


def select_fallback_engine(
    values: Mapping[str, object],
    *,
    online: bool,
    failed_engine: str,
    failed_engines: set[str],
) -> str:
    for candidate in fallback_engine_candidates(values, online=online):
        if candidate == failed_engine or candidate in failed_engines:
            continue
        return candidate
    return ""


def engine_identity(engine: str, values: Mapping[str, object]) -> tuple[str, str, str]:
    if engine == "openai-realtime":
        return (
            "openai",
            str(values.get("OPENAI_REALTIME_MODEL") or "gpt-realtime-2.1").strip(),
            str(values.get("OPENAI_REALTIME_VOICE") or "marin").strip(),
        )
    provider = str(values.get("LLM_PROVIDER") or ("ollama" if engine == "local" else "openai")).strip().lower()
    model_keys = {"openai": "OPENAI_MODEL", "anthropic": "ANTHROPIC_MODEL", "ollama": "OLLAMA_MODEL"}
    model = str(values.get(model_keys.get(provider, "MODEL")) or "").strip()
    return provider, model, ""


def engine_command(engine: str, env_file: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "voice_assistant.engine_entry",
        "--engine",
        engine,
        "--env-file",
        str(env_file),
    ]


def ready_marker(engine: str) -> str:
    return "LSA Realtime ready:" if engine == "openai-realtime" else CLASSIC_READY_MARKER


def speak_local(text: str, values: Mapping[str, object] | None = None) -> None:
    message = str(text or "").strip()
    if not message:
        return
    print(f"LSA local announcement: {message}", flush=True)
    if not speak_local_status(message, values):
        print("LSA local announcement failed: no usable local TTS backend", flush=True)


def _terminate_process(process: subprocess.Popen, *, timeout: float = 6.0) -> None:
    if process.poll() is not None:
        return
    try:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=timeout)
        return
    except Exception:
        pass
    try:
        process.kill()
        process.wait(timeout=2.0)
    except Exception:
        pass


def _update_mcp_status_from_line(tracker: RuntimeStatusTracker, line: str) -> None:
    prefix = "Realtime MCP auto selection: "
    if line.startswith(prefix) and " -> " in line:
        server, result = line[len(prefix):].strip().split(" -> ", 1)
        transport = result.split(" ", 1)[0].strip().lower()
        if transport in {"stdio", "native"}:
            tracker.set_mcp(server, effective_transport=transport, healthy=True, detail="selected and healthy")
        return
    local_prefix = "Realtime MCP auto local unavailable: "
    if line.startswith(local_prefix):
        server = line[len(local_prefix):].split(" ", 1)[0].strip()
        tracker.set_mcp(server, detail="local route unavailable; evaluating alternate transport")


def _update_semantic_status_from_line(tracker: RuntimeStatusTracker, line: str) -> None:
    if not line.startswith(SEMANTIC_STATE_PREFIX):
        return
    state = line[len(SEMANTIC_STATE_PREFIX):].strip().lower()
    if state in VALID_SEMANTIC_STATES:
        tracker.set_runtime(semantic_state=state)


def _monitor_host_port(values: Mapping[str, object]) -> tuple[str, int]:
    host = str(values.get("WEB_MONITOR_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(str(values.get("WEB_MONITOR_PORT") or "8765").strip())
    except ValueError:
        raise ValueError(f"invalid WEB_MONITOR_PORT={values.get('WEB_MONITOR_PORT')!r}") from None
    if port < 1 or port > 65535:
        raise ValueError("WEB_MONITOR_PORT must be between 1 and 65535")
    return host, port


def refresh_common_web_monitor(
    monitor: WebMonitor,
    *,
    values: Mapping[str, object],
    env_file: Path,
    online: bool,
) -> None:
    monitor.set_web_password(str(values.get("WEB_PASSWORD") or "").strip())
    monitor.update(
        mode="auto-runtime",
        env_file=env_file,
        internet=online,
        env_values=dict(values),
        mcp_config=_mcp_config_for_monitor(values, env_file),
    )


def start_common_web_monitor(
    *,
    values: Mapping[str, object],
    env_file: Path,
    online: bool,
    active_profile_ref: list[Path],
    automatic_profiles: bool,
) -> tuple[WebMonitor | None, RuntimeWebServices | None]:
    if not _env_bool(values, "WEB_MONITOR_ENABLED", True):
        print("Web monitor disabled by runtime profile", flush=True)
        return None, None
    try:
        host, port = _monitor_host_port(values)
    except ValueError as error:
        print(f"Web monitor disabled: {error}", flush=True)
        return None, None

    monitor = WebMonitor(web_password=str(values.get("WEB_PASSWORD") or "").strip())
    refresh_common_web_monitor(monitor, values=values, env_file=env_file, online=online)
    services = RuntimeWebServices(
        monitor=monitor,
        active_profile=lambda: active_profile_ref[0],
        automatic_profiles=automatic_profiles,
    )
    services.bind()
    monitor.install_console_capture()
    try:
        actual_host, actual_port = monitor.start(host, port)
    except OSError as error:
        monitor.restore_console_capture()
        print(f"Web monitor unavailable on {host}:{port}: {error}", flush=True)
        return None, None
    print(f"LSA common WebMonitor: http://{actual_host}:{actual_port}", flush=True)
    return monitor, services


def run_engine_session(
    *,
    engine: str,
    env_file: Path,
    values: Mapping[str, object],
    online: bool,
    connectivity: ConnectivityManager,
    stop_event: threading.Event,
    reload_event: threading.Event,
    status_tracker: RuntimeStatusTracker,
) -> tuple[int | None, ConnectivityEvent | None, bool]:
    print(f"LSA runtime: engine={engine} connectivity={'online' if online else 'offline'} env={env_file}", flush=True)

    if engine != "openai-realtime":
        for item in status_tracker.status.mcp:
            status_tracker.set_mcp(item.name, effective_transport="stdio", healthy=None, detail="local MCP path selected")

    loader = StartupLoader(ROOT, values)
    loader.start()
    status_tracker.set_runtime(ready=False, semantic_state="starting")

    child_env = os.environ.copy()
    child_env["LSA_COMMON_STARTUP_LIFECYCLE"] = "1"
    child_env["PYTHONUNBUFFERED"] = "1"

    process = subprocess.Popen(
        engine_command(engine, env_file),
        cwd=str(ROOT),
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    marker = ready_marker(engine)
    ready_seen = threading.Event()
    output_done = threading.Event()
    connectivity_events: queue.Queue[ConnectivityEvent] = queue.Queue(maxsize=1)
    local_ready_announced = False

    def read_output() -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                print(line, end="", flush=True)
                stripped = line.strip()
                _update_mcp_status_from_line(status_tracker, stripped)
                _update_semantic_status_from_line(status_tracker, stripped)
                if not ready_seen.is_set() and marker and marker in line:
                    loader.stop()
                    ready_seen.set()
                    if not status_tracker.status.semantic_state or status_tracker.status.semantic_state == "starting":
                        status_tracker.set_runtime(ready=True, semantic_state="ready")
                    else:
                        status_tracker.set_runtime(ready=True)
        finally:
            output_done.set()

    def watch_connectivity() -> None:
        event = connectivity.wait_for_change(online)
        if event is not None and not stop_event.is_set():
            try:
                connectivity_events.put_nowait(event)
            except queue.Full:
                pass

    threading.Thread(target=read_output, name="lsa-engine-output", daemon=True).start()
    threading.Thread(target=watch_connectivity, name="lsa-connectivity-watch", daemon=True).start()

    try:
        while not stop_event.wait(0.2):
            if reload_event.is_set():
                print("LSA runtime reload requested from common WebMonitor", flush=True)
                status_tracker.set_runtime(ready=False, semantic_state="starting")
                loader.stop()
                _terminate_process(process)
                output_done.wait(timeout=2.0)
                return None, None, True

            if not online and ready_seen.is_set() and not local_ready_announced:
                speak_local("Assistant vocal prêt à exécuter des commandes.", values)
                local_ready_announced = True

            try:
                event = connectivity_events.get_nowait()
            except queue.Empty:
                event = None
            if event is not None:
                print(
                    f"LSA connectivity event: {'online' if event.online else 'offline'} "
                    f"(previous={'online' if event.previous_online else 'offline'})",
                    flush=True,
                )
                status_tracker.set_runtime(
                    ready=False,
                    semantic_state="starting",
                    connectivity="online" if event.online else "offline",
                )
                loader.stop()
                _terminate_process(process)
                output_done.wait(timeout=2.0)
                return None, event, False
            if process.poll() is not None:
                output_done.wait(timeout=2.0)
                status_tracker.set_runtime(ready=False, semantic_state="")
                return int(process.returncode or 0), None, False
        loader.stop()
        _terminate_process(process)
        output_done.wait(timeout=2.0)
        status_tracker.set_runtime(ready=False, semantic_state="")
        return 0, None, False
    finally:
        if not ready_seen.is_set():
            loader.stop()
        if process.poll() is None:
            _terminate_process(process)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default="auto")
    args = parser.parse_args()

    stop_event = threading.Event()
    reload_event = threading.Event()
    connectivity = ConnectivityManager(interval=CONNECTIVITY_INTERVAL)

    def request_stop(_signum, _frame) -> None:
        stop_event.set()
        connectivity.stop()

    previous_handlers = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, request_stop)
        except Exception:
            pass

    monitor: WebMonitor | None = None
    active_profile_ref: list[Path] = []

    try:
        raw_env_arg = str(args.env_file or "auto").strip()
        automatic = raw_env_arg.lower() == "auto"
        engine_override = ""
        failed_engines: set[str] = set()

        if automatic:
            online = connectivity.detect()
            env_file = ONLINE_ENV if online else OFFLINE_ENV
            print(f"LSA initial connectivity: {'online' if online else 'offline'}", flush=True)
            if not online:
                offline_values = _load_values(OFFLINE_ENV) if OFFLINE_ENV.is_file() else {}
                speak_local("Assistant fonctionne localement.", offline_values)
        else:
            env_file = Path(raw_env_arg).expanduser()
            if not env_file.is_absolute():
                env_file = (ROOT / env_file).resolve()
            values = _load_values(env_file)
            online = str(values.get("CONNECTIVITY_MODE") or "online").strip().lower() != "offline"

        active_profile_ref[:] = [env_file]
        initial_values = _load_values(env_file) if env_file.is_file() else {}
        monitor, _services = start_common_web_monitor(
            values=initial_values,
            env_file=env_file,
            online=online,
            active_profile_ref=active_profile_ref,
            automatic_profiles=automatic,
        )
        if monitor is not None:
            monitor.set_runtime_restart_handler(reload_event.set)

        while not stop_event.is_set():
            if not env_file.is_file():
                print(f"Active env file not found: {env_file}", file=sys.stderr, flush=True)
                return 2

            active_profile_ref[0] = env_file
            values = _load_values(env_file)
            if monitor is not None:
                refresh_common_web_monitor(monitor, values=values, env_file=env_file, online=online)

            engine = engine_override or normalize_engine(values, online=online)
            provider, model, voice = engine_identity(engine, values)
            tracker = RuntimeStatusTracker(
                STATUS_FILE,
                RuntimeStatus(
                    connectivity="online" if online else "offline",
                    engine=engine,
                    provider=provider,
                    model=model,
                    voice=voice,
                    ready=False,
                    semantic_state="starting",
                    profile=str(env_file),
                    mcp=configured_mcp_statuses(values, profile=env_file, root=ROOT),
                ),
            )
            print(f"LSA runtime status: {STATUS_FILE}", flush=True)
            print(
                f"LSA runtime selection: connectivity={'online' if online else 'offline'} "
                f"voice_engine={engine} profile={env_file}",
                flush=True,
            )

            code, event, reload_requested = run_engine_session(
                engine=engine,
                env_file=env_file,
                values=values,
                online=online,
                connectivity=connectivity,
                stop_event=stop_event,
                reload_event=reload_event,
                status_tracker=tracker,
            )

            if reload_requested:
                reload_event.clear()
                engine_override = ""
                failed_engines.clear()
                time.sleep(0.20)
                continue

            if event is None:
                if stop_event.is_set():
                    return int(code or 0)
                failed_engines.add(engine)
                fallback = select_fallback_engine(
                    values,
                    online=online,
                    failed_engine=engine,
                    failed_engines=failed_engines,
                )
                if fallback:
                    print(
                        f"LSA runtime engine failure: {engine} exited with code {int(code or 0)}; falling back to {fallback}",
                        flush=True,
                    )
                    engine_override = fallback
                    time.sleep(0.35)
                    continue
                return int(code or 0)

            if not automatic:
                print("LSA connectivity changed but fixed --env-file mode prevents profile switching.", flush=True)
                return int(code or 0)

            online = event.online
            engine_override = ""
            failed_engines.clear()
            if not online:
                offline_values = _load_values(OFFLINE_ENV) if OFFLINE_ENV.is_file() else {}
                speak_local("Connexion internet perdue. Assistant fonctionne localement.", offline_values)
                env_file = OFFLINE_ENV
            else:
                env_file = ONLINE_ENV
            active_profile_ref[0] = env_file
            time.sleep(0.35)

        return 0
    finally:
        connectivity.stop()
        if monitor is not None:
            monitor.set_runtime_restart_handler(None)
            monitor.stop()
            monitor.restore_console_capture()
        for sig, handler in previous_handlers.items():
            try:
                signal.signal(sig, handler)
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
