"""Wake word matching helpers for post-STT command gating."""

from __future__ import annotations

import os
import re
from typing import Mapping
import unicodedata


MAX_WAKE_WORD_START_TOKEN = 2


def normalize_for_wake_word(value: str) -> str:
    """Normalize text for tolerant wake word matching."""
    without_accents = "".join(
        char for char in unicodedata.normalize("NFKD", value) if not unicodedata.combining(char)
    )
    return without_accents.casefold()


def parse_wake_words(value: str | None) -> list[str]:
    """Parse one or more wake word variants from an env value."""
    if not value:
        return []

    candidates = re.split(r"[,;|]", value)
    return [candidate.strip() for candidate in candidates if candidate.strip()]


def get_configured_wake_words(values: Mapping[str, object] | None = None) -> list[str]:
    """Return configured wake words through the single configuration boundary.

    ``values`` lets early startup consumers use their selected profile before it
    has been exported to the process environment. Engine code must use this
    helper instead of reading or parsing ``WAKE_WORD`` directly.
    """
    source: Mapping[str, object] = values if values is not None else os.environ
    return parse_wake_words(str(source.get("WAKE_WORD") or "").strip() or None)


def apply_wake_word(text: str, wake_words: list[str]) -> tuple[bool, str | None, str]:
    """Return whether a command should be processed, the matched wake word, and command text."""
    if not wake_words:
        return True, None, text

    if not text or not text.strip():
        return False, None, text

    normalized_text = normalize_for_wake_word(text)
    matches: list[tuple[int, int, str]] = []
    for wake_word in wake_words:
        pattern = _wake_word_pattern(wake_word)
        if not pattern:
            continue

        for match in re.finditer(pattern, normalized_text):
            if _token_index_before(normalized_text, match.start()) <= MAX_WAKE_WORD_START_TOKEN:
                matches.append((match.start(), match.end(), wake_word))

    if not matches:
        return False, None, text

    start, end, wake_word = min(matches, key=lambda item: item[0])
    command = _extract_command_after_match(text, normalized_text, end)
    return True, wake_word, command or text.strip()


def _wake_word_pattern(wake_word: str) -> str:
    normalized = normalize_for_wake_word(wake_word).strip()
    if not normalized:
        return ""

    escaped_tokens = [re.escape(token) for token in re.findall(r"\w+", normalized)]
    if not escaped_tokens:
        return ""
    return r"(?<!\w)" + r"\W+".join(escaped_tokens) + r"(?!\w)"


def _token_index_before(value: str, offset: int) -> int:
    return len(re.findall(r"\w+", value[:offset]))


def _extract_command_after_match(original_text: str, normalized_text: str, normalized_end: int) -> str:
    original_offset = _map_normalized_offset_to_original(original_text, normalized_end)
    command = original_text[original_offset:]
    command = re.sub(r"^[\s,;:!?.-]+", "", command)
    return command.strip()


def _map_normalized_offset_to_original(original_text: str, normalized_offset: int) -> int:
    normalized_count = 0
    for index, char in enumerate(original_text):
        normalized_char = normalize_for_wake_word(char)
        normalized_count += len(normalized_char)
        if normalized_count >= normalized_offset:
            return index + 1
    return len(original_text)
