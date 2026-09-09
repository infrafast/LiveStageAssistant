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


def startup_ready_message(
    *,
    stt_language: str | None,
    tool_count: int,
    failed_servers: Mapping[str, object] | None = None,
    has_unknown_native_tools: bool = False,
) -> str:
    locale = load_locale(stt_language)
    count = max(0, int(tool_count or 0))
    failed_names = sorted(str(name) for name in (failed_servers or {}).keys() if str(name).strip())
    if failed_names:
        if count <= 0:
            return i18n_text(
                locale,
                "startup.ready_no_mcp",
                "Assistant vocal prêt à exécuter des commandes, aucun MCP connecté.",
            )
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
        return template.format(tool_count=count, servers=servers)
    if count <= 0:
        if has_unknown_native_tools:
            return i18n_text(locale, "startup.ready", "Assistant vocal prêt à exécuter des commandes.")
        return i18n_text(
            locale,
            "startup.ready_no_mcp",
            "Assistant vocal prêt à exécuter des commandes, aucun MCP connecté.",
        )
    template = i18n_text(
        locale,
        "startup.ready_tools",
        "Assistant vocal prêt à exécuter des commandes, {tool_count} outils disponibles !",
    )
    return template.format(tool_count=count)
