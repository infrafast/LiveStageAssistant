import os
from pathlib import Path
import tempfile
import threading
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
        self.reload_in_progress = threading.Event()
        self.monitor = WebMonitor()
        self.monitor.set_runtime_reload_state_provider(self.reload_in_progress.is_set)
        self.monitor.set_runtime_restart_handler(self._request_reload)

    def tearDown(self):
        self.env_patch.stop()
        self.tempdir.cleanup()

    def _request_reload(self):
        self.reload_in_progress.set()

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

    def test_restart_modal_follows_parent_reload_state_only(self):
        self.write_status(ready=True, semantic_state="wait_wake")
        self.monitor._request_runtime_restart()
        self.assertTrue(self.monitor.snapshot()["environment_loading"]["active"])

        self.reload_in_progress.clear()
        self.assertFalse(self.monitor.snapshot()["environment_loading"]["active"])

    def test_starting_state_does_not_open_configuration_modal(self):
        self.write_status(ready=False, semantic_state="starting")
        self.assertFalse(self.monitor.snapshot()["environment_loading"]["active"])

    def test_runtime_status_does_not_expire_while_engine_is_stable(self):
        self.write_status(ready=True, semantic_state="wait_wake")
        os.utime(self.status_file, (1, 1))

        snapshot = self.monitor.snapshot()

        self.assertTrue(snapshot["runtime_status"]["ready"])
        self.assertEqual(snapshot["services"]["Voice engine"]["status"], "ready")
        self.assertNotIn("runtime_status_stale", snapshot)


if __name__ == "__main__":
    unittest.main()
