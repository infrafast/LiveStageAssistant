#!/usr/bin/env python3
"""Single LiveStageAssistant runtime and engine supervisor.

The runtime owns engine-independent connectivity and startup lifecycle. It
selects the active profile, launches exactly one voice engine, watches Internet
availability, switches between online and offline profiles when connectivity
changes, and keeps loader/process/signal handling common across Classic, Local
and realtime providers.
"""

from __future__ import annotations

import argparse
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
from voice_assistant.startup_lifecycle import StartupLoader

ROOT = Path(__file__).resolve().parents[1]
AUTO_ENV_DIR = Path(os.getenv("ASSISTANT_AUTO_ENV_DIR", "/etc/livestageassistant"))
ONLINE_ENV = AUTO_ENV_DIR / ".env.online"
OFFLINE_ENV = AUTO_ENV_DIR / ".env.offline"
CONNECTIVITY_INTERVAL = float(os.getenv("LSA_CONNECTIVITY_CHECK_INTERVAL", "10") or "10")
STATUS_FILE = Path(os.getenv("LSA_RUNTIME_STATUS_FILE", "/tmp/livestageassistant-runtime-status.json"))


def _load_values(path: Path) -> dict[str, object]:
    return dict(dotenv_values(path))


def normalize_engine(values: Mapping[str, object], *, online: bool) -> str:
    if not online:
        return "local"
    engine = str(values.get("VOICE_ENGINE") or "classic").strip().lower()
    if engine not in {"classic", "openai-realtime"}:
        print(f"Invalid VOICE_ENGINE={engine!r}; falling back to classic.", flush=True)
        return "classic"
    return engine


def fallback_engine_candidates(values: Mapping[str, object], *, online: bool) -> tuple[str, ...]:
    """Return generic online-engine fallback candidates in configured order."""
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
    """Resolve generic provider/model/voice status from the selected profile."""
    if engine == "openai-realtime":
        return (
            "openai",
            str(values.get("OPENAI_REALTIME_MODEL") or "gpt-realtime-2.1").strip(),
            str(values.get("OPENAI_REALTIME_VOICE") or "marin").strip(),
        )
    provider = str(values.get("LLM_PROVIDER") or ("ollama" if engine == "local" else "openai")).strip().lower()
    model_keys = {
        "openai": "OPENAI_MODEL",
        "anthropic": "ANTHROPIC_MODEL",
        "ollama": "OLLAMA_MODEL",
    }
    model = str(values.get(model_keys.get(provider, "MODEL")) or "").strip()
    return provider, model, ""


def engine_command(engine: str, env_file: Path) -> list[str]:
    # The common runtime owns connectivity/profile switching. Engines always
    # receive one explicit profile and therefore never start their legacy auto
    # connectivity watcher.
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
    if engine == "openai-realtime":
        return "LSA Realtime ready:"
    return CLASSIC_READY_MARKER


def speak_local(text: str, values: Mapping[str, object] | None = None) -> None:
    """Speak critical status locally through Piper, with emergency fallback."""
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
    """Consume provider-neutral transport selection logs emitted by Realtime."""
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
        return
    native_prefix = "Realtime MCP auto native unavailable "
    if line.startswith(native_prefix):
        # The following JSON/error line is diagnostic; leave the final health
        # decision to a subsequent successful selection or process failure.
        return


def run_engine_session(
    *,
    engine: str,
    env_file: Path,
    values: Mapping[str, object],
    online: bool,
    connectivity: ConnectivityManager,
    stop_event: threading.Event,
    status_tracker: RuntimeStatusTracker,
) -> tuple[int | None, ConnectivityEvent | None]:
    print(
        f"LSA runtime: engine={engine} connectivity={'online' if online else 'offline'} env={env_file}",
        flush=True,
    )

    if engine != "openai-realtime":
        for item in status_tracker.status.mcp:
            # Classic/local use the existing local MCP client path. This is a
            # transport fact only; no MCP/domain semantics are inferred here.
            status_tracker.set_mcp(item.name, effective_transport="stdio", healthy=None, detail="local MCP path selected")

    loader = StartupLoader(ROOT, values)
    loader.start()

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
                if not ready_seen.is_set() and marker and marker in line:
                    loader.stop()
                    ready_seen.set()
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

    reader = threading.Thread(target=read_output, name="lsa-engine-output", daemon=True)
    watcher = threading.Thread(target=watch_connectivity, name="lsa-connectivity-watch", daemon=True)
    reader.start()
    watcher.start()

    try:
        while not stop_event.wait(0.2):
            if not online and ready_seen.is_set() and not local_ready_announced:
                # Offline READY feedback belongs to the common runtime and uses
                # the same guaranteed-local Piper path as connectivity loss.
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
                status_tracker.set_runtime(ready=False, connectivity="online" if event.online else "offline")
                loader.stop()
                _terminate_process(process)
                output_done.wait(timeout=2.0)
                return None, event
            if process.poll() is not None:
                output_done.wait(timeout=2.0)
                status_tracker.set_runtime(ready=False)
                return int(process.returncode or 0), None
        loader.stop()
        _terminate_process(process)
        output_done.wait(timeout=2.0)
        status_tracker.set_runtime(ready=False)
        return 0, None
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

        while not stop_event.is_set():
            if not env_file.is_file():
                print(f"Active env file not found: {env_file}", file=sys.stderr, flush=True)
                return 2

            values = _load_values(env_file)
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

            code, event = run_engine_session(
                engine=engine,
                env_file=env_file,
                values=values,
                online=online,
                connectivity=connectivity,
                stop_event=stop_event,
                status_tracker=tracker,
            )

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
                        f"LSA runtime engine failure: {engine} exited with code {int(code or 0)}; "
                        f"falling back to {fallback}",
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
                # This must never depend on the cloud engine that just became
                # unavailable. Announce locally before starting the offline path.
                offline_values = _load_values(OFFLINE_ENV) if OFFLINE_ENV.is_file() else {}
                speak_local("Connexion internet perdue. Assistant fonctionne localement.", offline_values)
                env_file = OFFLINE_ENV
            else:
                # The newly selected online engine owns the normal online voice
                # announcement; connectivity detection itself remains here.
                env_file = ONLINE_ENV

            # Give child processes/audio nodes a brief deterministic release
            # window before starting the replacement engine.
            time.sleep(0.35)

        return 0
    finally:
        connectivity.stop()
        for sig, handler in previous_handlers.items():
            try:
                signal.signal(sig, handler)
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
