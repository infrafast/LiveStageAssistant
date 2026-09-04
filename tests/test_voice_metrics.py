import unittest

from voice_assistant.voice_metrics import VOICE_METRICS_PREFIX, VoiceTurnMetrics, parse_voice_metrics_line


class FakeClock:
    def __init__(self):
        self.value = 10.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class VoiceMetricsTests(unittest.TestCase):
    def test_records_classic_durations(self):
        clock = FakeClock()
        metrics = VoiceTurnMetrics("classic", clock=clock)
        metrics.mark("command_accepted")
        clock.advance(0.4)
        metrics.mark("agent_response_ready")
        clock.advance(0.1)
        metrics.mark("tts_start")
        clock.advance(0.3)
        metrics.mark("tts_end")
        record = metrics.record()
        self.assertEqual(record["pipeline"], "classic")
        self.assertAlmostEqual(record["durations_ms"]["agent_ms"], 400.0)
        self.assertAlmostEqual(record["durations_ms"]["tts_ms"], 300.0)
        self.assertAlmostEqual(record["durations_ms"]["turn_ms"], 800.0)

    def test_log_line_round_trip(self):
        metrics = VoiceTurnMetrics("classic")
        metrics.mark("command_accepted")
        line = metrics.to_log_line()
        self.assertTrue(line.startswith(VOICE_METRICS_PREFIX))
        parsed = parse_voice_metrics_line("prefix " + line)
        self.assertEqual(parsed["pipeline"], "classic")

    def test_invalid_line_is_ignored(self):
        self.assertIsNone(parse_voice_metrics_line("VOICE_METRICS {broken"))


if __name__ == "__main__":
    unittest.main()
