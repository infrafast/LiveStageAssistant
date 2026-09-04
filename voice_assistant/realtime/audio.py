"""Small audio helpers for the isolated RV1 realtime runner."""

from __future__ import annotations

import numpy as np


class Pcm16MonoResampler:
    """Streaming linear PCM16 mono resampler with chunk-boundary continuity."""

    def __init__(self, source_rate: int, target_rate: int) -> None:
        if source_rate <= 0 or target_rate <= 0:
            raise ValueError("sample rates must be positive")
        self.source_rate = int(source_rate)
        self.target_rate = int(target_rate)
        self._buffer = np.empty(0, dtype=np.float32)
        self._position = 0.0

    def process(self, pcm: bytes) -> bytes:
        if not pcm:
            return b""
        if self.source_rate == self.target_rate:
            return pcm
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        if samples.size == 0:
            return b""
        if self._buffer.size:
            samples = np.concatenate((self._buffer, samples))

        step = self.source_rate / self.target_rate
        if samples.size < 2 or self._position > samples.size - 1:
            self._buffer = samples
            return b""

        positions = np.arange(self._position, samples.size - 1, step, dtype=np.float64)
        if positions.size == 0:
            self._buffer = samples
            return b""
        output = np.interp(positions, np.arange(samples.size), samples)
        self._position = float(positions[-1] + step)

        drop = max(0, int(self._position) - 1)
        if drop:
            self._buffer = samples[drop:]
            self._position -= drop
        else:
            self._buffer = samples

        return np.clip(np.rint(output), -32768, 32767).astype(np.int16).tobytes()


def downmix_pcm16(pcm: bytes, channels: int) -> bytes:
    if channels <= 0:
        raise ValueError("channels must be positive")
    if channels == 1 or not pcm:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16)
    usable = samples[: samples.size - (samples.size % channels)]
    if usable.size == 0:
        return b""
    frames = usable.reshape(-1, channels).astype(np.int32)
    mono = np.rint(frames.mean(axis=1)).astype(np.int16)
    return mono.tobytes()


def expand_pcm16_channels(pcm: bytes, channels: int) -> bytes:
    if channels <= 0:
        raise ValueError("channels must be positive")
    if channels == 1 or not pcm:
        return pcm
    mono = np.frombuffer(pcm, dtype=np.int16)
    return np.repeat(mono[:, None], channels, axis=1).astype(np.int16).tobytes()
