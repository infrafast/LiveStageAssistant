from __future__ import annotations

import unittest

from voice_assistant.semantic_audio import (
    SemanticAudioConfig,
    SemanticAudioController,
    SemanticAudioState,
    VoiceOutputGains,
)


class SemanticAudioTests(unittest.TestCase):
    def test_semantic_cues_are_engine_neutral(self) -> None:
        config = SemanticAudioConfig.from_env(
            {
                "STARTUP_LOADER_SOUND_ENABLED": "true",
                "STARTUP_LOADER_SOUND_FILE": "loader.wav",
                "READY_SOUND_FILE": "ready.wav",
                "LISTENING_SOUND_FILE": "listen.wav",
                "WAKE_DETECTED_SOUND_FILE": "wake.wav",
                "THINKING_SOUND_FILE": "thinking.wav",
                "COMMAND_ACK_SOUND_FILE": "done.wav",
            }
        )
        self.assertEqual(config.cue_for(SemanticAudioState.STARTING), "loader.wav")
        self.assertEqual(config.cue_for(SemanticAudioState.READY), "ready.wav")
        self.assertEqual(config.cue_for(SemanticAudioState.LISTENING), "listen.wav")
        self.assertEqual(config.cue_for(SemanticAudioState.WAKE_DETECTED), "wake.wav")
        self.assertEqual(config.cue_for(SemanticAudioState.PROCESSING), "thinking.wav")
        self.assertEqual(config.cue_for(SemanticAudioState.RESULT_READY), "done.wav")
        self.assertEqual(config.cue_for(SemanticAudioState.WAIT_WAKE), "listen.wav")
        self.assertEqual(config.cue_for(SemanticAudioState.SPEAKING), "")

    def test_controller_stops_processing_before_result_ready_cue(self) -> None:
        events: list[tuple[str, str]] = []
        controller = SemanticAudioController(
            SemanticAudioConfig(thinking="thinking.wav", result_ready="done.wav", listening="listen.wav"),
            play_once=lambda cue: events.append(("once", cue)),
            start_loop=lambda cue: events.append(("start", cue)),
            stop_loop=lambda: events.append(("stop", "")),
        )
        controller.transition(SemanticAudioState.LISTENING)
        controller.transition(SemanticAudioState.PROCESSING)
        controller.transition(SemanticAudioState.RESULT_READY)
        self.assertEqual(
            events,
            [
                ("once", "listen.wav"),
                ("start", "thinking.wav"),
                ("stop", ""),
                ("once", "done.wav"),
            ],
        )

    def test_wait_wake_plays_listening_cue_once_and_repeated_state_is_noop(self) -> None:
        events: list[str] = []
        states: list[SemanticAudioState] = []
        controller = SemanticAudioController(
            SemanticAudioConfig(listening="listen.wav", wake_detected="wake.wav"),
            play_once=lambda cue: events.append(cue),
            start_loop=lambda cue: events.append(cue),
            stop_loop=lambda: events.append("stop"),
            on_state=states.append,
        )
        self.assertTrue(controller.transition(SemanticAudioState.WAIT_WAKE))
        self.assertFalse(controller.transition(SemanticAudioState.WAIT_WAKE))
        self.assertEqual(events, ["listen.wav"])
        controller.transition(SemanticAudioState.WAKE_DETECTED)
        self.assertEqual(events, ["listen.wav", "wake.wav"])
        self.assertEqual(states, [SemanticAudioState.WAIT_WAKE, SemanticAudioState.WAKE_DETECTED])

    def test_cloud_and_local_gains_are_independent(self) -> None:
        gains = VoiceOutputGains.from_env(
            {
                "CLOUD_TTS_OUTPUT_GAIN": "1.35",
                "LOCAL_TTS_OUTPUT_GAIN": "0.80",
                "BACKEND_TTS_VOLUME": "0.25",
            }
        )
        self.assertEqual(gains.cloud, 1.35)
        self.assertEqual(gains.local, 0.80)

    def test_legacy_backend_volume_migrates_both_until_split_keys_exist(self) -> None:
        gains = VoiceOutputGains.from_env({"BACKEND_TTS_VOLUME": "1.20"})
        self.assertEqual(gains.cloud, 1.20)
        self.assertEqual(gains.local, 1.20)

    def test_one_new_gain_can_split_from_legacy_without_affecting_the_other(self) -> None:
        gains = VoiceOutputGains.from_env(
            {"BACKEND_TTS_VOLUME": "1.20", "LOCAL_TTS_OUTPUT_GAIN": "0.85"}
        )
        self.assertEqual(gains.cloud, 1.20)
        self.assertEqual(gains.local, 0.85)

    def test_gains_are_bounded(self) -> None:
        gains = VoiceOutputGains.from_env(
            {"CLOUD_TTS_OUTPUT_GAIN": "9", "LOCAL_TTS_OUTPUT_GAIN": "-2"}
        )
        self.assertEqual(gains.cloud, 2.0)
        self.assertEqual(gains.local, 0.0)


if __name__ == "__main__":
    unittest.main()
