import unittest

import numpy as np

from voice_assistant.realtime.audio import Pcm16MonoResampler, downmix_pcm16, expand_pcm16_channels


class RealtimeAudioTests(unittest.TestCase):
    def test_downmix_and_expand(self):
        stereo = np.array([[1000, 3000], [-1000, -3000]], dtype=np.int16).tobytes()
        mono = np.frombuffer(downmix_pcm16(stereo, 2), dtype=np.int16)
        self.assertEqual(mono.tolist(), [2000, -2000])
        expanded = np.frombuffer(expand_pcm16_channels(mono.tobytes(), 2), dtype=np.int16).reshape(-1, 2)
        self.assertEqual(expanded.tolist(), [[2000, 2000], [-2000, -2000]])

    def test_streaming_resampler_preserves_expected_duration(self):
        source_rate = 48000
        target_rate = 24000
        samples = (np.sin(np.arange(source_rate) * 2 * np.pi * 440 / source_rate) * 12000).astype(np.int16)
        resampler = Pcm16MonoResampler(source_rate, target_rate)
        chunks = []
        for start in range(0, len(samples), 960):
            chunks.append(resampler.process(samples[start : start + 960].tobytes()))
        output = np.frombuffer(b"".join(chunks), dtype=np.int16)
        self.assertGreaterEqual(len(output), target_rate - 10)
        self.assertLessEqual(len(output), target_rate + 10)


if __name__ == "__main__":
    unittest.main()
