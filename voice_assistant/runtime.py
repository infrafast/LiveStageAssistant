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
import contextlib
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
from voice_assistant.startup_lifecycle import StartupLoader

ROOT = Path(__file__).resolve().parents[1]
AUTO_ENV_DIR = Path(os.getenv("ASSISTANT_AUTO_ENV_DIR", "/etc/livestageassistant"))
ONLINE_ENV = AUTO_ENV_DIR / ".env.online"
OFFLINE_ENV = AUTO_ENV_DIR / ".env.offline"
CONNECTIVITY_INTERVAL = float(os.getenv("LSA_CONNECTIVITY_CHECK_INTERVAL", "10") or "10")


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


@contextlib.contextmanager
def _quiet_native_stderr():
    saved = None
    null_fd = None
    try:
        saved = os.dup(2)
        null_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null_fd, 2)
        yield
    finally:
        if saved is not None:
            os.dup2(saved, 2)
            os.close(saved)
        if null_fd is not None:
            os.close(null_fd)


def speak_local(text: str) -> None:
    """Speak a connectivity transition without depending on a cloud engine."""
    message = str(text or "").strip()
    if not message:
        return
    print(f"LSA local announcement: {message}", flush=True)
    try:
        with _quiet_native_stderr():
            import pyttsx3

            tts = pyttsx3.init()
            tts.say(message)
            tts.runAndWait()
            tts.stop()
    except Exception as exc:
        print(f"LSA local announcement failed: {exc}", flush=True)


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


def run_engine_session(
    *,
    engine: str,
    env_file: Path,
    values: Mapping[str, object],
    online: bool,
    connectivity: ConnectivityManager,
    stop_event: threading.Event,
) -> tuple[int | None, ConnectivityEvent | None]:
    print(
        f"LSA runtime: engine={engine} connectivity={'online' if online else 'offline'} env={env_file}",
        flush=True,
    )

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

    def read_output() -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                print(line, end="", flush=True)
                if not ready_seen.is_set() and marker and marker in line:
                    loader.stop()
                    ready_seen.set()
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
                loader.stop()
                _terminate_process(process)
                output_done.wait(timeout=2.0)
                return None, event
            if process.poll() is not None:
                output_done.wait(timeout=2.0)
                return int(process.returncode or 0), None
        loader.stop()
        _terminate_process(process)
        output_done.wait(timeout=2.0)
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

        if automatic:
            online = connectivity.detect()
            env_file = ONLINE_ENV if online else OFFLINE_ENV
            print(f"LSA initial connectivity: {'online' if online else 'offline'}", flush=True)
            if not online:
                speak_local("Assistant fonctionne localement.")
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
            engine = normalize_engine(values, online=online)
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
            )

            if event is None:
                return int(code or 0)

            if not automatic:
                print("LSA connectivity changed but fixed --env-file mode prevents profile switching.", flush=True)
                return int(code or 0)

            online = event.online
            if not online:
                # This must never depend on the cloud engine that just became
                # unavailable. Announce locally before starting the offline path.
                speak_local("Connexion internet perdue. Assistant fonctionne localement.")
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
