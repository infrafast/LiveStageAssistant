import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from urllib.request import Request, urlopen
from unittest.mock import patch

from voice_assistant.runtime_status import RuntimeStatus, write_status_file
from voice_assistant.web_monitor import WebMonitor


class WebMonitorHttpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.status_file = Path(self.tempdir.name) / "runtime-status.json"
        self.env_patch = patch.dict(os.environ, {"LSA_RUNTIME_STATUS_FILE": str(self.status_file)})
        self.env_patch.start()
        write_status_file(
            self.status_file,
            RuntimeStatus(
                connectivity="online",
                engine="openai-realtime",
                provider="openai",
                model="gpt-realtime-2.1",
                voice="marin",
                ready=True,
                semantic_state="wait_wake",
                profile="/tmp/.env.online",
            ),
        )
        self.monitor = WebMonitor()
        self.monitor.update(env_values={"CONNECTIVITY_MODE": "online", "VOICE_ENGINE": "openai-realtime"})
        self.monitor.set_runtime_restart_handler(lambda: None)
        host, port = self.monitor.start("127.0.0.1", 0)
        self.base = f"http://{host}:{port}"

    def tearDown(self):
        self.monitor.stop()
        self.env_patch.stop()
        self.tempdir.cleanup()

    def get(self, path):
        with urlopen(self.base + path, timeout=3) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()

    def post_json(self, path, payload):
        body = json.dumps(payload).encode("utf-8")
        request = Request(self.base + path, data=body, method="POST", headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_root_assets_and_snapshot_are_served(self):
        for path in ("/", "/assets/web/app.js", "/assets/web/app-main.js", "/assets/web/config-unified.js"):
            status, _ctype, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertGreater(len(body), 20, path)
        status, ctype, body = self.get("/api/snapshot")
        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        snapshot = json.loads(body.decode("utf-8"))
        self.assertTrue(snapshot["runtime_status"]["ready"])
        self.assertEqual(snapshot["runtime_status"]["semantic_state"], "wait_wake")
        self.assertFalse(snapshot["environment_loading"]["active"])

    def test_http_restart_loading_cycle_closes_on_new_ready_revision(self):
        status, payload = self.post_json("/api/runtime-restart", {})
        self.assertEqual(status, 200)
        self.assertTrue(payload["restart_requested"])

        _, _, body = self.get("/api/snapshot")
        pending = json.loads(body.decode("utf-8"))
        self.assertTrue(pending["environment_loading"]["active"])

        write_status_file(
            self.status_file,
            RuntimeStatus(
                connectivity="online",
                engine="openai-realtime",
                provider="openai",
                model="gpt-realtime-2.1",
                voice="marin",
                ready=True,
                semantic_state="wait_wake",
                profile="/tmp/.env.online",
            ),
        )
        time.sleep(0.01)
        _, _, body = self.get("/api/snapshot")
        ready = json.loads(body.decode("utf-8"))
        self.assertTrue(ready["runtime_status"]["ready"])
        self.assertEqual(ready["runtime_status"]["semantic_state"], "wait_wake")
        self.assertFalse(ready["environment_loading"]["active"])


if __name__ == "__main__":
    unittest.main()
