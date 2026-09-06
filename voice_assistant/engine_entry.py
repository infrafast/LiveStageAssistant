#!/usr/bin/env python3
"""Engine entry adapter for the common LSA startup lifecycle.

This module contains no startup policy. It only prevents legacy per-engine
loader ownership when the engine is launched by `voice_assistant.runtime`, then
hands control to the selected engine unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys


def run_classic(env_file: str) -> int:
    from voice_assistant import agent

    if os.getenv("LSA_COMMON_STARTUP_LIFECYCLE") == "1":
        agent.VoiceAssistant.start_startup_loader_sound = lambda self: None
        agent.VoiceAssistant.stop_startup_loader_sound = lambda self: None

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
