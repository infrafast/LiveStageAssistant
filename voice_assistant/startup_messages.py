"""Shared startup announcement text for all LiveStageAssistant engines."""

from __future__ import annotations

from typing import Mapping

try:
    from .i18n import i18n_text, load_locale
except ImportError:  # pragma: no cover - direct script fallback
    from i18n import i18n_text, load_locale  # type: ignore


def human_join(items: list[str], conjunction: str = "et") -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f" {conjunction} ".join(items)
    return ", ".join(items[:-1]) + f" {conjunction} " + items[-1]


def startup_connectivity_message(*, stt_language: str | None, connectivity: str) -> str:
    """Build the shared spoken connectivity announcement."""
    locale = load_locale(stt_language)
    online = str(connectivity or "").strip().lower() == "online"
    fallback = "Assistant connecté à internet" if online else "Assistant fonctionne localement"
    text = i18n_text(locale, "network.online" if online else "network.offline", fallback).strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def _wake_word_suffix(
    locale: Mapping[str, object],
    wake_words: list[str],
    wake_word_state: str | None = None,
) -> str:
    if not wake_words:
        return ""
    language = str(locale.get("locale") or "fr").strip().lower()
    conjunction = "and" if language == "en" else "et"
    joined = human_join(wake_words, conjunction=conjunction)
    state = str(wake_word_state or "active").strip().lower()
    if state == "unavailable":
        if language == "en":
            return f" Wake word {joined} is configured, but detection is unavailable."
        return f" Wake word {joined} configuré, mais détection indisponible."
    if language == "en":
        return f" Wake word active, say {joined} to wake me up."
    return f" Wake word actif, prononcez {joined} pour me réveiller."


def startup_ready_message(
    *,
    stt_language: str | None,
    tool_count: int,
    failed_servers: Mapping[str, object] | None = None,
    has_unknown_native_tools: bool = False,
    wake_words: list[str] | tuple[str, ...] | None = None,
    deterministic_gateway_count: int | None = None,
    wake_word_state: str | None = None,
) -> str:
    locale = load_locale(stt_language)
    count = max(0, int(tool_count or 0))
    failed_names = sorted(str(name) for name in (failed_servers or {}).keys() if str(name).strip())
    normalized_wake_words = [str(item).strip() for item in (wake_words or []) if str(item).strip()]
    wake_word_suffix = _wake_word_suffix(locale, normalized_wake_words, wake_word_state)

    if deterministic_gateway_count is not None:
        gateway_count = max(0, int(deterministic_gateway_count or 0))
        language = str(locale.get("locale") or "fr").strip().lower()
        if gateway_count <= 0:
            base = (
                "Deterministic local assistant ready, but no MCP command gateway is available."
                if language == "en"
                else "Assistant local déterministe prêt, mais aucune commande MCP n'est disponible."
            )
            return base + wake_word_suffix
        if language == "en":
            noun = "gateway" if gateway_count == 1 else "gateways"
            return f"Deterministic local assistant ready, {gateway_count} MCP {noun} available." + wake_word_suffix
        noun = "passerelle" if gateway_count == 1 else "passerelles"
        suffix = "" if gateway_count == 1 else "s"
        return f"Assistant local déterministe prêt, {gateway_count} {noun} MCP disponible{suffix}." + wake_word_suffix
    if failed_names:
        if count <= 0:
            return i18n_text(
                locale,
                "startup.ready_no_mcp",
                "Assistant vocal prêt à exécuter des commandes, aucun MCP connecté.",
            ) + wake_word_suffix
        servers = human_join(failed_names)
        if len(failed_names) == 1:
            template = i18n_text(
                locale,
                "startup.ready_partial_tools_singular",
                "Assistant vocal prêt à exécuter des commandes, seulement {tool_count} outils disponibles car {servers} est injoignable.",
            )
        else:
            template = i18n_text(
                locale,
                "startup.ready_partial_tools",
                "Assistant vocal prêt à exécuter des commandes, seulement {tool_count} outils disponibles car {servers} sont injoignables.",
            )
        return template.format(tool_count=count, servers=servers) + wake_word_suffix
    if count <= 0:
        if has_unknown_native_tools:
            return i18n_text(locale, "startup.ready", "Assistant vocal prêt à exécuter des commandes.") + wake_word_suffix
        return i18n_text(
            locale,
            "startup.ready_no_mcp",
            "Assistant vocal prêt à exécuter des commandes, aucun MCP connecté.",
        ) + wake_word_suffix
    template = i18n_text(
        locale,
        "startup.ready_tools",
        "Assistant vocal prêt à exécuter des commandes, {tool_count} outils disponibles !",
    )
    return template.format(tool_count=count) + wake_word_suffix

