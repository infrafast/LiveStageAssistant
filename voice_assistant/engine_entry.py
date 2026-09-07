#!/usr/bin/env python3
"""Engine entry adapter for the common LSA runtime lifecycle.

This module contains no connectivity detection or startup policy. It prevents
legacy per-engine loader/WebMonitor ownership when launched by
`voice_assistant.runtime` and provides the small engine-specific speech adapter
needed to deliver a pending online-connectivity announcement with the selected
engine voice.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import dotenv
from dotenv import dotenv_values

CLASSIC_READY_MARKER = "LSA Classic ready:"


def _suppress_legacy_web_monitor(env_file: str) -> None:
    """Force the legacy Classic child monitor off under the common supervisor.

    `agent.py` still contains backward-compatible standalone WebMonitor wiring.
    Production supervised execution owns one WebMonitor in `runtime.py`; this
    adapter prevents a child engine from binding a second server while the
    remaining configuration handlers are migrated into common services.
    """
    if os.getenv("LSA_COMMON_WEB_MONITOR") != "1":
        return

    original_dotenv_values = dotenv.dotenv_values
    target = os.path.abspath(env_file)

    def supervised_dotenv_values(dotenv_path=None, *args, **kwargs):
        values = original_dotenv_values(dotenv_path, *args, **kwargs)
        try:
            candidate = os.path.abspath(os.fspath(dotenv_path)) if dotenv_path is not None else ""
        except TypeError:
            candidate = ""
        if candidate == target:
            values["WEB_MONITOR_ENABLED"] = "false"
        return values

    dotenv.dotenv_values = supervised_dotenv_values


def run_classic(env_file: str) -> int:
    _suppress_legacy_web_monitor(env_file)
    from voice_assistant import agent

    values = dict(dotenv_values(env_file)) if str(env_file).lower() != "auto" else {}
    online = str(values.get("CONNECTIVITY_MODE") or "online").strip().lower() != "offline"

    if os.getenv("LSA_COMMON_STARTUP_LIFECYCLE") == "1":
        agent.VoiceAssistant.start_startup_loader_sound = lambda self: None
        agent.VoiceAssistant.stop_startup_loader_sound = lambda self: None

        original_announce_ready = agent.VoiceAssistant.announce_startup_ready

        async def announce_ready_with_connectivity(self, loaded_servers):
            print(f"{CLASSIC_READY_MARKER} connectivity={'online' if online else 'offline'}", flush=True)

            if online:
                if os.getenv("LSA_ANNOUNCE_ONLINE_WITH_ENGINE", "1") == "1":
                    message = "Assistant connecté à internet."
                    print(f"LSA connectivity announcement via classic: {message}", flush=True)
                    if getattr(self, "tts_provider", "none") != "none":
                        try:
                            await asyncio.wait_for(
                                asyncio.to_thread(lambda: asyncio.run(self.text_to_speech(message))),
                                timeout=8.0,
                            )
                        except Exception as exc:
                            print(f"Classic connectivity announcement failed: {exc}", flush=True)
                    await asyncio.sleep(0.45)

                await original_announce_ready(self, loaded_servers)
                return

            return

        agent.VoiceAssistant.announce_startup_ready = announce_ready_with_connectivity

    sys.argv = [sys.argv[0], "--env-file", env_file]
    asyncio.run(agent.main())
    return 0


def run_openai_realtime(env_file: str) -> int:
    from voice_assistant.realtime import service

    if os.getenv("LSA_COMMON_STARTUP_LIFECYCLE") == "1":
        service.play_startup_sound = lambda _env_file: None

    sys.argv = [sys.argv[0], "--env-file", env_file]
    return service.main()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, choices=("classic", "local", "openai-realtime"))
    parser.add_argument("--env-file", required=True)
    args = parser.parse_args()

    if args.engine == "openai-realtime":
        return run_openai_realtime(args.env_file)
    return run_classic(args.env_file)


if __name__ == "__main__":
    raise SystemExit(main())
