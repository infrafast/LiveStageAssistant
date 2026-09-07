"""LiveStageAssistant package.

Keep package import lightweight: runtime-owned modules such as the common
WebMonitor must not import the historical Classic agent stack just because the
package is imported. ``VoiceAssistant`` and ``main`` remain available lazily for
legacy callers.
"""

from __future__ import annotations

from typing import Any

__version__ = "0.1.0"
__author__ = "Your Name"
__email__ = "your.email@example.com"

__all__ = ["VoiceAssistant", "main"]


def __getattr__(name: str) -> Any:
    if name in {"VoiceAssistant", "main"}:
        from .agent import VoiceAssistant, main

        return VoiceAssistant if name == "VoiceAssistant" else main
    raise AttributeError(name)
