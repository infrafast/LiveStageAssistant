"""Shared wake-word log formatting."""

from __future__ import annotations

from typing import Iterable


def wake_words_label(wake_words: Iterable[object] | object) -> str:
    if isinstance(wake_words, str):
        items = [wake_words]
    else:
        try:
            items = list(wake_words)  # type: ignore[arg-type]
        except TypeError:
            items = [wake_words]
    label = ", ".join(str(item).strip() for item in items if str(item).strip())
    return label or "configured wake word"


def format_openwakeword_waiting(
    wake_words: Iterable[object] | object,
    *,
    threshold: float,
    model_label: str = "configured model",
    engine: str = "backend",
) -> str:
    return (
        f'openWakeWord {engine}: waiting for "{wake_words_label(wake_words)}" '
        f"({model_label or 'configured model'}, threshold={threshold:.2f})"
    )


def format_openwakeword_detected(
    wake_word: object,
    *,
    label: str,
    score: float,
    threshold: float,
    engine: str = "backend",
    detail: str = "",
) -> str:
    suffix = f" {detail}" if detail else ""
    return (
        f'openWakeWord {engine}: detected "{wake_words_label(wake_word)}" '
        f"({label or 'configured model'}, score={score:.2f}, threshold={threshold:.2f}){suffix}"
    )
