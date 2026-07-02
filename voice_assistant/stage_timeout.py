"""Bounded daemon workers for blocking runtime stages."""

import queue
import threading
from typing import Any, Callable


class TimedStageRunner:
    """Run at most one blocking worker per named stage with a bounded wait."""

    def __init__(self) -> None:
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def run(self, stage: str, timeout_seconds: float, operation: Callable[[], Any]) -> Any:
        with self._lock:
            previous = self._threads.get(stage)
            if previous and previous.is_alive():
                raise TimeoutError(f"previous {stage} worker is still running")

        results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def _worker() -> None:
            try:
                results.put((True, operation()))
            except BaseException as error:
                results.put((False, error))

        worker = threading.Thread(
            target=_worker,
            name=f"voice-assistant-{stage}",
            daemon=True,
        )
        with self._lock:
            self._threads[stage] = worker
        worker.start()
        worker.join(timeout=max(0.1, float(timeout_seconds)))
        if worker.is_alive():
            raise TimeoutError(f"{stage} exceeded {timeout_seconds:.1f}s")

        with self._lock:
            if self._threads.get(stage) is worker:
                self._threads.pop(stage, None)

        succeeded, value = results.get_nowait()
        if succeeded:
            return value
        raise value

    def active_worker(self, stage: str) -> threading.Thread | None:
        """Return the current stage worker for diagnostics and tests."""
        with self._lock:
            return self._threads.get(stage)
