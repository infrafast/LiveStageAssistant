"""Small JSON-backed i18n helpers for LiveStageAssistant UI strings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


I18N_DIR = Path("assets/i18n")
DEFAULT_LOCALE = "fr"


def available_locales(i18n_dir: Path = I18N_DIR) -> list[dict[str, str]]:
    """Return locale metadata discovered from assets/i18n/<locale>.json."""
    locales: list[dict[str, str]] = []
    if not i18n_dir.is_dir():
        return [{"id": DEFAULT_LOCALE, "label": "Français"}]

    for path in sorted(i18n_dir.glob("*.json")):
        locale = path.stem.strip()
        if not locale:
            continue
        label = locale
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            label = str(data.get("language_name") or data.get("locale_name") or locale)
        except (OSError, json.JSONDecodeError):
            pass
        locales.append({"id": locale, "label": label})
    return locales or [{"id": DEFAULT_LOCALE, "label": "Français"}]


def normalize_locale(locale: str | None, i18n_dir: Path = I18N_DIR) -> str:
    """Return a supported locale, falling back to French when unset/invalid."""
    requested = (locale or "").strip().lower()
    known = {item["id"] for item in available_locales(i18n_dir)}
    if requested in known:
        return requested
    if DEFAULT_LOCALE in known:
        return DEFAULT_LOCALE
    return sorted(known)[0] if known else DEFAULT_LOCALE


def load_locale(locale: str | None, i18n_dir: Path = I18N_DIR) -> dict[str, Any]:
    """Load a locale dictionary with French fallback values."""
    selected = normalize_locale(locale, i18n_dir)
    fallback: dict[str, Any] = {}
    fallback_path = i18n_dir / f"{DEFAULT_LOCALE}.json"
    if fallback_path.is_file():
        try:
            fallback = json.loads(fallback_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            fallback = {}

    if selected == DEFAULT_LOCALE:
        data = fallback
    else:
        path = i18n_dir / f"{selected}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            data = {}
        data = deep_merge(fallback, data)

    data["locale"] = selected
    data.setdefault("language_name", selected)
    return data


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def i18n_text(locale_data: dict[str, Any], dotted_key: str, fallback: str) -> str:
    current: Any = locale_data
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return fallback
        current = current[part]
    return str(current) if current is not None else fallback
