import io
import json
import struct
import unittest
import wave

from voice_assistant.voice_cost_metrics import OpenAIUsageCollector, TokenSnapshot, cost_log, parse_cost_line, wav_duration_seconds


class DummyResponse:
    llm_output = {
        "token_usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 80,
            "prompt_tokens_details": {"cached_tokens": 400},
        }
    }
    generations = []


class VoiceCostMetricTests(unittest.TestCase):
    def test_token_collector_and_delta(self):
        collector = OpenAIUsageCollector()
        before = collector.snapshot()
        collector.on_llm_end(DummyResponse())
        delta = collector.snapshot() - before
        self.assertEqual(delta, TokenSnapshot(1000, 400, 80))

    def test_wav_duration(self):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * 16000)
        self.assertAlmostEqual(wav_duration_seconds(buffer.getvalue()), 1.0, places=3)

    def test_cost_log_round_trip(self):
        line = cost_log("llm", input_tokens=12, output_tokens=3)
        parsed = parse_cost_line("prefix " + line)
        self.assertEqual(parsed["stage"], "llm")
        self.assertEqual(parsed["input_tokens"], 12)


if __name__ == "__main__":
    unittest.main()
