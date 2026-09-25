from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from voice_assistant.realtime.service import (
    RealtimeTurnPhase,
    RealtimeTurnTracker,
    _turn_phase_timeout_seconds,
    is_benign_provider_error,
    recover_turn_timeout,
)
from voice_assistant.semantic_audio import (
    SemanticAudioConfig,
    SemanticAudioController,
    SemanticAudioState,
)


class _FakeEngine:
    def __init__(self) -> None:
        self.cancel_count = 0
        self.discard_count = 0

    async def cancel_response(self) -> None:
        self.cancel_count += 1

    async def discard_input_audio(self) -> None:
        self.discard_count += 1


class RealtimeTurnRecoveryTests(unittest.TestCase):
    def test_active_response_conflict_is_benign_lifecycle_error(self) -> None:
        self.assertTrue(
            is_benign_provider_error(
                {
                    "error": {
                        "code": "conversation_already_has_active_response",
                        "message": "Conversation already has an active response in progress.",
                    }
                }
            )
        )

    def test_tracker_uses_explicit_turn_phases(self) -> None:
        tracker = RealtimeTurnTracker()

        self.assertEqual(tracker.phase, RealtimeTurnPhase.IDLE)
        tracker.speech_started()
        self.assertEqual(tracker.phase, RealtimeTurnPhase.CAPTURING)
        self.assertFalse(tracker.awaiting_response)

        tracker.speech_stopped()
        self.assertEqual(tracker.phase, RealtimeTurnPhase.WAIT_RESPONSE)
        self.assertTrue(tracker.awaiting_response)

        tracker.response_started_event("resp-1")
        self.assertEqual(tracker.phase, RealtimeTurnPhase.RESPONDING)

        tracker.tool_started()
        self.assertEqual(tracker.phase, RealtimeTurnPhase.TOOL_RUNNING)

        tracker.tool_finished(expect_followup=True)
        self.assertEqual(tracker.phase, RealtimeTurnPhase.WAIT_FOLLOWUP)

        tracker.reset_after_cancel_or_failure()
        self.assertEqual(tracker.phase, RealtimeTurnPhase.IDLE)
        self.assertFalse(tracker.has_pending_work())

    def test_phase_specific_timeouts_do_not_use_one_global_deadline(self) -> None:
        tracker = RealtimeTurnTracker()
        with patch.dict(
            os.environ,
            {
                "REALTIME_CAPTURE_TIMEOUT_SECONDS": "11",
                "REALTIME_WAIT_RESPONSE_TIMEOUT_SECONDS": "7",
                "REALTIME_RESPONSE_TIMEOUT_SECONDS": "31",
                "REALTIME_FOLLOWUP_TIMEOUT_SECONDS": "9",
            },
            clear=False,
        ):
            tracker.speech_started()
            self.assertEqual(_turn_phase_timeout_seconds(tracker), 11)
            tracker.speech_stopped()
            self.assertEqual(_turn_phase_timeout_seconds(tracker), 7)
            tracker.response_started_event("resp-1")
            self.assertEqual(_turn_phase_timeout_seconds(tracker), 31)
            tracker.tool_finished(expect_followup=True)
            self.assertEqual(_turn_phase_timeout_seconds(tracker), 9)

    def test_turn_timeout_recovers_without_session_restart_signal(self) -> None:
        async def scenario() -> None:
            engine = _FakeEngine()
            tracker = RealtimeTurnTracker()
            tracker.response_started_event("resp-1")
            interrupted: set[str] = set()
            queue: asyncio.Queue = asyncio.Queue()
            await queue.put(("resp-1", b"stale"))
            busy_values: list[bool] = []

            semantic = SemanticAudioController(
                SemanticAudioConfig(),
                play_once=lambda _cue: None,
                start_loop=lambda _cue: None,
                stop_loop=lambda: None,
            )

            class _Callbacks:
                semantic_transition = None
                append_dialogue = None
                pop_cancel_requested = None
                pop_injected_command = None
                refresh_engine_context = None
                should_ignore_provider_speech_started = None
                defer_provider_response_until_user_transcript = False
                is_wake_only_transcript = None
                set_busy = busy_values.append

            await recover_turn_timeout(
                engine=engine,
                turn_tracker=tracker,
                interrupted=interrupted,
                queue=queue,
                semantic=semantic,
                callbacks=_Callbacks(),
                timeout_seconds=5.0,
            )

            self.assertEqual(engine.cancel_count, 1)
            self.assertEqual(engine.discard_count, 0)
            self.assertIn("resp-1", interrupted)
            self.assertTrue(queue.empty())
            self.assertEqual(tracker.phase, RealtimeTurnPhase.IDLE)
            self.assertFalse(tracker.has_pending_work())
            self.assertEqual(semantic.state, SemanticAudioState.LISTENING)
            self.assertEqual(busy_values, [False])

        asyncio.run(scenario())

    def test_wait_response_timeout_clears_uncommitted_input(self) -> None:
        async def scenario() -> None:
            engine = _FakeEngine()
            tracker = RealtimeTurnTracker()
            tracker.speech_started()
            tracker.speech_stopped()
            semantic = SemanticAudioController(
                SemanticAudioConfig(),
                play_once=lambda _cue: None,
                start_loop=lambda _cue: None,
                stop_loop=lambda: None,
            )

            await recover_turn_timeout(
                engine=engine,
                turn_tracker=tracker,
                interrupted=set(),
                queue=asyncio.Queue(),
                semantic=semantic,
                callbacks=None,
                timeout_seconds=5.0,
            )

            self.assertEqual(engine.cancel_count, 1)
            self.assertEqual(engine.discard_count, 1)
            self.assertEqual(tracker.phase, RealtimeTurnPhase.IDLE)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
