"""Prompt file discovery and resolution for LiveStageAssistant profiles."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PROMPT_DIR = ROOT / "data" / "prompt"
DEFAULT_ASSISTANT_SYSTEM_PROMPT_PATH = "data/prompt/assistant_system_prompt.md"
DEFAULT_STT_PROMPT_PATH = "data/prompt/stt_prompt.md"

PROMPT_EXTENSIONS = {".md", ".txt"}


def prompt_path_options() -> list[dict[str, str]]:
    """Return prompt files under data/prompt as GUI dropdown options."""
    if not PROMPT_DIR.is_dir():
        return []
    options: list[dict[str, str]] = []
    for path in sorted(PROMPT_DIR.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.suffix.lower() not in PROMPT_EXTENSIONS:
            continue
        rel = path.relative_to(ROOT).as_posix()
        options.append({"id": rel, "label": path.name})
    return options


def resolve_prompt_path(value: str, *, env_file: Path | None = None) -> Path:
    raw = os.path.expandvars(str(value or "").strip())
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    candidates: list[Path] = []
    if env_file is not None:
        candidates.append(env_file.expanduser().resolve().parent / path)
    candidates.append(ROOT / path)
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])


def read_prompt_file(value: str, *, env_file: Path | None = None) -> str:
    path = resolve_prompt_path(value, env_file=env_file)
    return path.read_text(encoding="utf-8").strip()


def prompt_reference_from_values(
    values: Mapping[str, Any],
    name: str,
    *,
    default_path: str,
) -> str:
    return str(values.get(name) or default_path).strip()


def prompt_text_from_values(
    values: Mapping[str, Any],
    name: str,
    *,
    env_file: Path | None = None,
    default_path: str,
    fallback_text: str = "",
) -> str:
    reference = prompt_reference_from_values(values, name, default_path=default_path)
    try:
        return read_prompt_file(reference, env_file=env_file)
    except OSError:
        if fallback_text:
            return fallback_text.strip()
        raise
