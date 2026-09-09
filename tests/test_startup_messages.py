from __future__ import annotations

import os
import unittest
from unittest import mock

from voice_assistant.startup_messages import startup_connectivity_message, startup_ready_message
from voice_assistant.wake_word import get_configured_wake_words


class StartupMessageTests(unittest.TestCase):
    def test_configured_wake_words_are_read_through_shared_helper(self):
        with mock.patch.dict(os.environ, {"WAKE_WORD": "momo, assistant"}, clear=False):
            self.assertEqual(get_configured_wake_words(), ["momo", "assistant"])

    def test_disabled_wake_word_returns_empty_list(self):
        with mock.patch.dict(os.environ, {"WAKE_WORD": ""}, clear=False):
            self.assertEqual(get_configured_wake_words(), [])

    def test_ready_without_wake_word_has_no_wake_suffix(self):
        self.assertEqual(
            startup_ready_message(
                stt_language="fr",
                tool_count=0,
                has_unknown_native_tools=True,
                wake_words=[],
            ),
            "Assistant vocal prêt à exécuter des commandes.",
        )

    def test_ready_with_wake_word_uses_shared_suffix(self):
        self.assertEqual(
            startup_ready_message(
                stt_language="fr",
                tool_count=0,
                has_unknown_native_tools=True,
                wake_words=["momo"],
            ),
            "Assistant vocal prêt à exécuter des commandes. Wake word actif, prononcez momo pour me réveiller.",
        )

    def test_ready_keeps_tool_count_with_wake_word(self):
        self.assertEqual(
            startup_ready_message(
                stt_language="fr",
                tool_count=16,
                wake_words=["momo"],
            ),
            "Assistant vocal prêt à exécuter des commandes, 16 outils disponibles ! Wake word actif, prononcez momo pour me réveiller.",
        )

    def test_connectivity_message_uses_shared_i18n_contract(self):
        self.assertEqual(
            startup_connectivity_message(stt_language="fr", connectivity="online"),
            "Assistant connecté à internet.",
        )
        self.assertEqual(
            startup_connectivity_message(stt_language="fr", connectivity="offline"),
            "Assistant fonctionne localement.",
        )

    def test_english_startup_text_follows_stt_language(self):
        self.assertEqual(
            startup_ready_message(
                stt_language="en",
                tool_count=0,
                has_unknown_native_tools=True,
                wake_words=["momo"],
            ),
            "Voice assistant ready to run commands. Wake word active, say momo to wake me up.",
        )
        self.assertEqual(
            startup_connectivity_message(stt_language="en", connectivity="online"),
            "Assistant connected to the internet.",
        )


if __name__ == "__main__":
    unittest.main()
