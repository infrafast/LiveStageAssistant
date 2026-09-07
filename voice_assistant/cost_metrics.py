"""Usage/cost helpers for Classic-vs-Realtime voice benchmarks.

Pricing snapshot: 2026-09-07, USD.
Classic LLM/STT costs can be calculated from provider usage/duration directly.
OpenAI TTS currently does not expose per-request token usage through the speech
endpoint used by LSA, so TTS cost is explicitly reported as an estimate based
on measured generated-audio duration.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextPricing:
    input_per_m: float
    cached_input_per_m: float
    output_per_m: float


OPENAI_TEXT_PRICING: dict[str, TextPricing] = {
    "gpt-4.1-mini": TextPricing(0.40, 0.10, 1.60),
    "gpt-4o-mini": TextPricing(0.15, 0.075, 0.60),
}

OPENAI_TRANSCRIPTION_PER_MINUTE_USD: dict[str, float] = {
    "whisper-1": 0.006,
    "gpt-transcribe": 0.0045,
}

# gpt-4o-mini-tts pricing is $0.60/M text input and $12/M audio output.
# The Speech endpoint used by LSA does not currently return token usage, so the
# benchmark estimates audio output at 20 audio tokens/s from measured duration.
OPENAI_TTS_TEXT_INPUT_PER_M = 0.60
OPENAI_TTS_AUDIO_OUTPUT_PER_M = 12.0
OPENAI_TTS_ESTIMATED_AUDIO_TOKENS_PER_SECOND = 20.0


def text_usage_cost_usd(
    model: str,
    *,
    input_tokens: int,
    cached_input_tokens: int = 0,
    output_tokens: int,
) -> float | None:
    pricing = OPENAI_TEXT_PRICING.get(model)
    if pricing is None:
        return None
    input_tokens = max(0, int(input_tokens))
    cached_input_tokens = min(input_tokens, max(0, int(cached_input_tokens)))
    output_tokens = max(0, int(output_tokens))
    uncached = input_tokens - cached_input_tokens
    return (
        uncached * pricing.input_per_m
        + cached_input_tokens * pricing.cached_input_per_m
        + output_tokens * pricing.output_per_m
    ) / 1_000_000.0


def transcription_cost_usd(model: str, audio_seconds: float) -> float | None:
    rate = OPENAI_TRANSCRIPTION_PER_MINUTE_USD.get(model)
    if rate is None:
        return None
    return max(0.0, float(audio_seconds)) / 60.0 * rate


def estimate_openai_tts_cost_usd(
    *,
    text_input_tokens: int,
    generated_audio_seconds: float,
) -> dict[str, float | int | str]:
    """Return an explicitly estimated gpt-4o-mini-tts cost breakdown."""
    text_input_tokens = max(0, int(text_input_tokens))
    generated_audio_seconds = max(0.0, float(generated_audio_seconds))
    estimated_audio_tokens = generated_audio_seconds * OPENAI_TTS_ESTIMATED_AUDIO_TOKENS_PER_SECOND
    text_cost = text_input_tokens * OPENAI_TTS_TEXT_INPUT_PER_M / 1_000_000.0
    audio_cost = estimated_audio_tokens * OPENAI_TTS_AUDIO_OUTPUT_PER_M / 1_000_000.0
    return {
        "method": "estimated_from_generated_audio_duration",
        "text_input_tokens": text_input_tokens,
        "generated_audio_seconds": generated_audio_seconds,
        "estimated_audio_output_tokens": round(estimated_audio_tokens, 3),
        "text_input_cost_usd": text_cost,
        "audio_output_cost_usd": audio_cost,
        "cost_usd": text_cost + audio_cost,
    }
