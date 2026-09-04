import unittest

from voice_assistant.realtime.metrics import realtime_usage_cost_usd


class RealtimeMetricsTests(unittest.TestCase):
    def test_mini_usage_cost_from_native_response_done_shape(self):
        usage = {
            "total_tokens": 253,
            "input_tokens": 132,
            "output_tokens": 121,
            "input_token_details": {
                "text_tokens": 119,
                "audio_tokens": 13,
                "cached_tokens": 64,
                "cached_tokens_details": {"text_tokens": 64, "audio_tokens": 0},
            },
            "output_token_details": {"text_tokens": 30, "audio_tokens": 91},
        }
        cost = realtime_usage_cost_usd("gpt-realtime-2.1-mini", usage)
        self.assertIsNotNone(cost)
        self.assertAlmostEqual(cost, 0.00205884, places=8)

    def test_unknown_model_is_not_guessed(self):
        self.assertIsNone(realtime_usage_cost_usd("future-model", {"input_tokens": 10}))


if __name__ == "__main__":
    unittest.main()
