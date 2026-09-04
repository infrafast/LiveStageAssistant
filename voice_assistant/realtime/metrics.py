"""Realtime usage and cost helpers shared by RV1 benchmarks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RealtimePricing:
    text_input_per_m: float
    cached_text_input_per_m: float
    text_output_per_m: float
    audio_input_per_m: float
    cached_audio_input_per_m: float
    audio_output_per_m: float


OPENAI_REALTIME_PRICING = {
    "gpt-realtime-2.1-mini": RealtimePricing(0.60, 0.06, 2.40, 10.0, 0.30, 20.0),
    "gpt-realtime-2.1": RealtimePricing(4.0, 0.40, 24.0, 32.0, 0.40, 64.0),
}


def realtime_usage_cost_usd(model: str, usage: dict[str, Any]) -> float | None:
    """Calculate OpenAI Realtime cost from response.done usage details."""
    pricing = OPENAI_REALTIME_PRICING.get(model)
    if pricing is None or not usage:
        return None

    input_total = int(usage.get("input_tokens") or 0)
    output_total = int(usage.get("output_tokens") or 0)
    input_details = usage.get("input_token_details") or {}
    output_details = usage.get("output_token_details") or {}
    cached_details = input_details.get("cached_tokens_details") or {}

    input_text = int(input_details.get("text_tokens") or 0)
    input_audio = int(input_details.get("audio_tokens") or 0)
    cached_text = min(input_text, int(cached_details.get("text_tokens") or 0))
    cached_audio = min(input_audio, int(cached_details.get("audio_tokens") or 0))
    uncached_text = max(0, input_text - cached_text)
    uncached_audio = max(0, input_audio - cached_audio)

    # Any non-audio input tokens not itemized by the API are conservatively
    # charged at the text-input rate. RV1 does not send image input.
    unclassified_input = max(0, input_total - input_text - input_audio)

    output_text = int(output_details.get("text_tokens") or 0)
    output_audio = int(output_details.get("audio_tokens") or 0)
    # Reasoning/other output tokens are billed as model text output.
    unclassified_output = max(0, output_total - output_text - output_audio)

    cost = (
        uncached_text * pricing.text_input_per_m
        + cached_text * pricing.cached_text_input_per_m
        + uncached_audio * pricing.audio_input_per_m
        + cached_audio * pricing.cached_audio_input_per_m
        + unclassified_input * pricing.text_input_per_m
        + (output_text + unclassified_output) * pricing.text_output_per_m
        + output_audio * pricing.audio_output_per_m
    ) / 1_000_000.0
    return cost
