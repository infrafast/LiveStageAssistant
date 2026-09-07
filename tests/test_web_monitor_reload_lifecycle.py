import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from voice_assistant.runtime_status import RuntimeStatus, write_status_file
from voice_assistant.web_monitor import WebMonitor


class WebMonitorReloadLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.status_file = Path(self.tempdir.name) / "runtime-status.json"
        self.env_patch = patch.dict(os.environ, {"LSA_RUNTIME_STATUS_FILE": str(self.status_file)})
        self.env_patch.start()
        self.monitor = WebMonitor()
        self.monitor.set_runtime_restart_handler(lambda: None)

    def tearDown(self):
        self.env_patch.stop()
        self.tempdir.cleanup()

    def write_status(self, *, ready: bool, semantic_state: str):
        write_status_file(
            self.status_file,
            RuntimeStatus(
                connectivity="online",
                engine="openai-realtime",
                provider="openai",
                model="gpt-realtime-2.1",
                voice="marin",
                ready=ready,
                semantic_state=semantic_state,
                profile="/tmp/.env.online",
            ),
        )

    def test_restart_does_not_complete_on_pre_request_ready_snapshot(self):
        self.write_status(ready=True, semantic_state="wait_wake")
        self.monitor._request_runtime_restart()

        snapshot = self.monitor.snapshot()

        self.assertTrue(snapshot["environment_loading"]["active"])

    def test_fast_restart_can_complete_without_poll_observing_starting(self):
        self.write_status(ready=True, semantic_state="wait_wake")
        self.monitor._request_runtime_restart()

        # Simulate a fast child recycle where no browser poll sees STARTING.
        # RuntimeStatusTracker writes atomically, so this produces a new revision.
        self.write_status(ready=True, semantic_state="wait_wake")
        snapshot = self.monitor.snapshot()

        self.assertFalse(snapshot["environment_loading"]["active"])

    def test_normal_starting_then_ready_sequence_remains_loading_until_ready(self):
        self.write_status(ready=True, semantic_state="wait_wake")
        self.monitor._request_runtime_restart()

        self.write_status(ready=False, semantic_state="starting")
        starting_snapshot = self.monitor.snapshot()
        self.assertTrue(starting_snapshot["environment_loading"]["active"])

        self.write_status(ready=True, semantic_state="ready")
        ready_snapshot = self.monitor.snapshot()
        self.assertFalse(ready_snapshot["environment_loading"]["active"])


if __name__ == "__main__":
    unittest.main()
