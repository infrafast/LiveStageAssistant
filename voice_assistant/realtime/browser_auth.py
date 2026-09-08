"""Server-side helpers for browser Realtime authentication.

The long-lived OpenAI API key never leaves the backend. The browser receives
only a short-lived Realtime client secret created for the requested session.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OPENAI_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"
DEFAULT_INPUT_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"


def create_openai_browser_client_secret(
    api_key: str,
    *,
    model: str,
    voice: str,
    instructions: str,
    ttl_seconds: int = 60,
    timeout: float = 10.0,
) -> dict[str, Any]:
    key = str(api_key or "").strip()
    if not key:
        raise ValueError("OpenAI API key is not configured")
    ttl = max(30, min(600, int(ttl_seconds)))
    payload = {
        "expires_after": {"anchor": "created_at", "seconds": ttl},
        "session": {
            "type": "realtime",
            "model": str(model or "gpt-realtime-2.1").strip(),
            "output_modalities": ["audio"],
            "instructions": str(instructions or "").strip(),
            "audio": {
                "input": {
                    "turn_detection": {
                        "type": "server_vad",
                        "create_response": True,
                        "interrupt_response": True,
                    },
                    "transcription": {"model": DEFAULT_INPUT_TRANSCRIPTION_MODEL},
                },
                "output": {"voice": str(voice or "marin").strip()},
            },
        },
    }
    request = Request(
        OPENAI_CLIENT_SECRETS_URL,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI Realtime client secret request failed: HTTP {exc.code}: {detail[:300]}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"OpenAI Realtime client secret request failed: {exc}") from exc
    if not isinstance(body, dict) or not str(body.get("value") or "").startswith("ek_"):
        raise RuntimeError("OpenAI Realtime client secret response did not contain an ephemeral token")
    return {
        "value": str(body["value"]),
        "expires_at": int(body.get("expires_at") or 0),
        "model": str((body.get("session") or {}).get("model") or payload["session"]["model"]),
    }
