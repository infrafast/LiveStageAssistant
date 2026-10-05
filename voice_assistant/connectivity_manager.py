"""Engine-independent connectivity monitoring for LiveStageAssistant runtime."""

from __future__ import annotations

from dataclasses import dataclass
import socket
import threading
import time
from typing import Callable


@dataclass(frozen=True)
class ConnectivityEvent:
    online: bool
    previous_online: bool | None
    detected_at: float


class ConnectivityManager:
    """Detect and watch Internet availability independently from voice engines."""

    def __init__(
        self,
        *,
        host: str = "api.openai.com",
        port: int = 443,
        timeout: float = 2.0,
        interval: float = 10.0,
        probe: Callable[[], bool] | None = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self.interval = max(1.0, float(interval))
        self._probe = probe
        self._stop = threading.Event()

    def detect(self) -> bool:
        if self._probe is not None:
            return bool(self._probe())
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout):
                return True
        except OSError:
            return False

    def wait_for_change(self, current_online: bool) -> ConnectivityEvent | None:
        """Block until connectivity changes or stop() is requested."""
        while not self._stop.wait(self.interval):
            online = self.detect()
            if online != current_online:
                return ConnectivityEvent(
                    online=online,
                    previous_online=current_online,
                    detected_at=time.time(),
                )
        return None

    def stop(self) -> None:
        self._stop.set()
