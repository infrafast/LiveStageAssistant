from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from voice_assistant import classic_engine


class DummyAssistant:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.llm_provider = kwargs["llm_provider"]
        self.model = kwargs["model"]
        self.tts_provider = kwargs["tts_provider"]


class ClassicEngineRuntimeEntryTests(unittest.TestCase):
    def _build(self, text: str):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".env"
            env_file.write_text(text, encoding="utf-8")
            with mock.patch.object(classic_engine.agent, "VoiceAssistant", DummyAssistant):
                assistant = classic_engine.build_assistant(env_file)
            return assistant

    def test_online_classic_is_web_free_and_uses_cloud_gain(self):
        assistant = self._build(
            "CONNECTIVITY_MODE=online\n"
            "LLM_PROVIDER=openai\n"
            "OPENAI_MODEL=gpt-4.1-mini\n"
            "TTS_PROVIDER=openai\n"
            "CLOUD_TTS_PROVIDER=openai\n"
            "CLOUD_TTS_OUTPUT_GAIN=0.42\n"
            "LOCAL_TTS_OUTPUT_GAIN=1.35\n"
            "STT_LANGUAGE=fr\n"
        )
        self.assertIsNone(assistant.kwargs["web_monitor"])
        self.assertEqual(assistant.kwargs["llm_provider"], "openai")
        self.assertEqual(assistant.kwargs["backend_tts_volume"], 0.42)

    def test_offline_local_is_web_free_and_uses_local_gain(self):
        assistant = self._build(
            "CONNECTIVITY_MODE=offline\n"
            "LLM_PROVIDER=ollama\n"
            "OLLAMA_MODEL=mistral:7b-instruct-q4_K_M\n"
            "TTS_PROVIDER=piper\n"
            "CLOUD_TTS_OUTPUT_GAIN=0.25\n"
            "LOCAL_TTS_OUTPUT_GAIN=1.25\n"
            "STT_LANGUAGE=fr\n"
        )
        self.assertIsNone(assistant.kwargs["web_monitor"])
        self.assertEqual(assistant.kwargs["llm_provider"], "ollama")
        self.assertEqual(assistant.kwargs["tts_provider"], "piper")
        self.assertEqual(assistant.kwargs["backend_tts_volume"], 1.25)

    def test_speaker_profile_slug_is_local_and_stable(self):
        profiles = classic_engine._speaker_profiles(
            {
                "SPEAKER_PROFILES_MAX": "1",
                "SPEAKER_PROFILE_1_NAME": "Claude Retour",
                "SPEAKER_PROFILE_1_ENABLED": "true",
            }
        )
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].slug, "claude_retour")
        self.assertEqual([path.name for path in profiles[0].wav_paths], ["profil1_1.wav", "profil1_2.wav", "profil1_3.wav"])


if __name__ == "__main__":
    unittest.main()
