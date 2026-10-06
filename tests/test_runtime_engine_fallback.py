import unittest

from voice_assistant import runtime


class RuntimeEngineFallbackTests(unittest.TestCase):
    def test_online_engine_fallback_is_disabled_by_default(self):
        self.assertEqual(runtime.fallback_engine_candidates({}, online=True), ())

    def test_online_engine_fallback_requires_explicit_configuration(self):
        values = {"VOICE_ENGINE_FALLBACK": "classic,local"}
        self.assertEqual(
            runtime.fallback_engine_candidates(values, online=True),
            ("classic", "local"),
        )

    def test_offline_never_uses_online_engine_fallback(self):
        values = {"VOICE_ENGINE_FALLBACK": "classic"}
        self.assertEqual(runtime.fallback_engine_candidates(values, online=False), ())


if __name__ == "__main__":
    unittest.main()
