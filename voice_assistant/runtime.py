#!/usr/bin/env python3
"""Single LiveStageAssistant service launcher.

Select the active connectivity profile first, then start exactly one voice
engine. This keeps Classic/Local and provider realtime engines isolated so the
Classic STT/TTS/wake-word stack is not imported in Realtime mode.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import sys

from dotenv import dotenv_values

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


def normalize_engine(values: dict, *, online: bool) -> str:
    if not online:
        return "local"
    engine = str(values.get("VOICE_ENGINE") or "classic").strip().lower()
    if engine not in {"classic", "openai-realtime"}:
        print(f"Invalid VOICE_ENGINE={engine!r}; falling back to classic.", flush=True)
        return "classic"
    return engine


def exec_classic(env_file: Path, original_env_arg: str) -> None:
    target = ROOT / "voice_assistant" / "agent.py"
    env_arg = "auto" if str(original_env_arg).strip().lower() == "auto" else str(env_file)
    print(f"LSA runtime: engine={'local' if env_file == OFFLINE_ENV else 'classic'} env={env_file}", flush=True)
    os.execv(sys.executable, [sys.executable, str(target), "--env-file", env_arg])


def exec_openai_realtime(env_file: Path) -> None:
    print(f"LSA runtime: engine=openai-realtime env={env_file}", flush=True)
    os.execv(
        sys.executable,
        [sys.executable, "-m", "voice_assistant.realtime.service", "--env-file", str(env_file)],
    )


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

    if engine == "openai-realtime":
        exec_openai_realtime(env_file)
    exec_classic(env_file, args.env_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
