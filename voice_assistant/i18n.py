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


def localized_error_text(
    locale: str | None,
    *,
    domain: str = "command",
    error: Exception | str | None = None,
    error_code: str | None = None,
) -> str:
    """Return a concise localized user-facing error without leaking technical details."""
    data = load_locale(locale)
    errors = data.get("errors") if isinstance(data.get("errors"), dict) else {}
    raw = str(error or "").strip()
    normalized = raw.casefold()
    code = str(error_code or "").strip().casefold()

    domain_key = {
        "mixer": "mixer_command_failed",
        "mixeur": "mixer_command_failed",
        "qlcplus": "lighting_command_failed",
        "lighting": "lighting_command_failed",
        "command": "command_failed",
    }.get(str(domain or "").strip().casefold(), "command_failed")

    prefix = str(errors.get(domain_key) or errors.get("command_failed") or "La commande a échoué.")

    if "unsupported for oscxr" in normalized:
        if "channel-to-bus mute" in normalized:
            detail = str(
                errors.get("oscxr_channel_bus_mute_unsupported")
                or "le mute d’une voie vers un bus séparé n’est pas pris en charge sur ce mixeur."
            )
        elif "matrix" in normalized:
            detail = str(errors.get("oscxr_matrix_unsupported") or "les matrices ne sont pas disponibles sur ce mixeur.")
        elif "channel sends to aux" in normalized or "channel-to-aux" in normalized:
            detail = str(
                errors.get("oscxr_channel_aux_unsupported")
                or "l’envoi d’une voie vers une sortie AUX dédiée n’est pas disponible sur ce mixeur."
            )
        else:
            detail = str(
                errors.get("oscxr_operation_unsupported")
                or "cette fonction n’est pas disponible avec le protocole OSCXR."
            )
        return f"{prefix} : {detail}"

    if code in {"stale_plan", "expired_token"} or "stale_target" in normalized:
        detail = str(errors.get("stale_command") or "la cible a changé ; relance la commande.")
        return f"{prefix} : {detail}"

    if "timeout" in normalized or "timed out" in normalized or "délai" in normalized:
        detail = str(errors.get("timeout") or "le délai d’attente a été dépassé.")
        return f"{prefix} : {detail}"

    if (
        "disconnected" in normalized
        or "deconnect" in normalized
        or "déconnect" in normalized
        or "connection refused" in normalized
        or "connexion" in normalized and "perdue" in normalized
    ):
        detail = str(errors.get("disconnected") or "le périphérique ne répond pas.")
        return f"{prefix} : {detail}"

    if code in {"invalid_token", "invalid_request"} or "protocol mismatch" in normalized:
        detail = str(errors.get("invalid_request") or "la requête n’est plus valide.")
        return f"{prefix} : {detail}"

    if "not found" in normalized or "introuvable" in normalized:
        detail = str(errors.get("not_found") or "la cible demandée est introuvable.")
        return f"{prefix} : {detail}"

    detail = str(errors.get("generic_detail") or "une erreur technique est survenue.")
    return f"{prefix} : {detail}"


def sanitize_spoken_response(locale: str | None, response: str) -> str:
    """Last-resort guard that prevents obvious technical error details from reaching chat/TTS."""
    text = str(response or "").strip()
    lowered = text.casefold()
    technical_markers = (
        "unsupported for oscxr",
        "traceback (most recent call last)",
        "runtimeerror:",
        "exception:",
        "sorry, i encountered an error:",
        "mcp gateway tool returned an error",
        "connection refused",
        "timed out",
    )
    mixer_prefixes = (
        "la commande mixeur a échoué",
        "the mixer command failed",
    )
    if any(marker in lowered for marker in technical_markers) or any(lowered.startswith(prefix) for prefix in mixer_prefixes):
        domain = "mixer" if ("mixeur" in lowered or "mixer" in lowered or "oscxr" in lowered) else "command"
        return localized_error_text(locale, domain=domain, error=text)
    return text
