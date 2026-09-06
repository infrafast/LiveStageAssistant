#!/usr/bin/env python3
"""Single LiveStageAssistant service launcher.

The launcher owns the engine-independent startup lifecycle. It selects the
active connectivity profile, starts the configured loader audio, launches one
voice engine, stops the loader when that engine reports READY, and keeps engine
process/signal handling common for Classic, Local and realtime providers.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
from typing import Mapping

from dotenv import dotenv_values

from voice_assistant.startup_lifecycle import StartupLoader, classic_ready_marker

ROOT = Path(__file__).resolve().parents[1]
AUTO_ENV_DIR = Path(os.getenv("ASSISTANT_AUTO_ENV_DIR", "/etc/livestageassistant"))
ONLINE_ENV = AUTO_ENV_DIR / ".env.online"
OFFLINE_ENV = AUTO_ENV_DIR / ".env.offline"


def internet_available() -> bool:
    try:
        with socket.create_connection(("api.openai.com", 443), timeout=2.0):
            return True
    except OSError:
        return False


def resolve_env_file(value: str) -> tuple[Path, bool]:
    raw = str(value or "auto").strip()
    if raw.lower() == "auto":
        online = internet_available()
        return (ONLINE_ENV if online else OFFLINE_ENV), online
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    values = dotenv_values(path)
    online = str(values.get("CONNECTIVITY_MODE") or "online").strip().lower() != "offline"
    return path, online


def normalize_engine(values: Mapping[str, object], *, online: bool) -> str:
    if not online:
        return "local"
    engine = str(values.get("VOICE_ENGINE") or "classic").strip().lower()
    if engine not in {"classic", "openai-realtime"}:
        print(f"Invalid VOICE_ENGINE={engine!r}; falling back to classic.", flush=True)
        return "classic"
    return engine


def engine_command(engine: str, env_file: Path, original_env_arg: str) -> list[str]:
    if engine == "openai-realtime":
        return [sys.executable, "-m", "voice_assistant.realtime.service", "--env-file", str(env_file)]
    target = ROOT / "voice_assistant" / "agent.py"
    env_arg = "auto" if str(original_env_arg).strip().lower() == "auto" else str(env_file)
    return [sys.executable, str(target), "--env-file", env_arg]


def ready_marker(engine: str, values: Mapping[str, object]) -> str:
    if engine == "openai-realtime":
        return "LSA Realtime ready:"
    return classic_ready_marker(ROOT, values)


def run_engine(engine: str, env_file: Path, original_env_arg: str, values: Mapping[str, object]) -> int:
    print(f"LSA runtime: engine={engine} env={env_file}", flush=True)

    loader = StartupLoader(ROOT, values)
    loader.start()

    child_env = os.environ.copy()
    # Startup feedback belongs to the common runtime. Individual engines must
    # not start a second loader while running under this launcher.
    child_env["STARTUP_LOADER_SOUND_ENABLED"] = "false"
    child_env["LSA_COMMON_STARTUP_LIFECYCLE"] = "1"

    command = engine_command(engine, env_file, original_env_arg)
    marker = ready_marker(engine, values)
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    stopped_for_ready = False

    def forward_signal(signum, _frame) -> None:
        if process.poll() is None:
            try:
                process.send_signal(signum)
            except Exception:
                pass

    previous_handlers = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, forward_signal)
        except Exception:
            pass

    try:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            if not stopped_for_ready and marker and marker in line:
                loader.stop()
                stopped_for_ready = True
        return process.wait()
    finally:
        if not stopped_for_ready:
            loader.stop()
        for sig, handler in previous_handlers.items():
            try:
                signal.signal(sig, handler)
            except Exception:
                pass
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=3.0)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default="auto")
    args = parser.parse_args()

    env_file, online = resolve_env_file(args.env_file)
    if not env_file.is_file():
        raise SystemExit(f"Active env file not found: {env_file}")
    values = dict(dotenv_values(env_file))
    engine = normalize_engine(values, online=online)
    print(
        f"LSA runtime selection: connectivity={'online' if online else 'offline'} "
        f"voice_engine={engine} profile={env_file}",
        flush=True,
    )
    return run_engine(engine, env_file, args.env_file, values)


if __name__ == "__main__":
    raise SystemExit(main())
