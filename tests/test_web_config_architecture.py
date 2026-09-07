from pathlib import Path
import json
import unittest


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "assets" / "web"


class WebConfigArchitectureTests(unittest.TestCase):
    def test_validated_frontend_boot_sequence_has_no_bootstrap(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("config-bootstrap.js", app)
        self.assertLess(app.index("app-main.js"), app.index("mcp-realtime.js"))
        self.assertLess(app.index("mcp-realtime.js"), app.index("config-unified.js"))

    def test_unified_controller_has_no_dual_wake_selector(self):
        script = (WEB / "config-unified.js").read_text(encoding="utf-8")
        self.assertNotIn("wake-word-select", script)
        self.assertNotIn("legacyWake", script)
        self.assertNotIn("__lsaWakeCanonicalFetchInstalled", script)

    def test_multifield_apply_policy_is_restart_by_default(self):
        policy = json.loads((WEB / "config-apply-policy.json").read_text(encoding="utf-8"))
        self.assertEqual(policy["default_mode"], "engine-restart")
        script = (WEB / "config-unified.js").read_text(encoding="utf-8")
        self.assertIn("function globalMode(entries)", script)
        self.assertIn('entry.mode === "engine-restart"', script)
        self.assertIn("restartPending", script)

    def test_clean_state_disables_global_save(self):
        script = (WEB / "config-unified.js").read_text(encoding="utf-8")
        self.assertIn("saveButton.disabled = true", script)
        self.assertIn('saveButton.textContent = "Save"', script)
        self.assertIn('saveButton.textContent = "Restart"', script)

    def test_engine_and_gain_posts_are_dirty_group_only(self):
        script = (WEB / "config-unified.js").read_text(encoding="utf-8")
        self.assertIn('keys.has("voice-engine")', script)
        self.assertIn('keys.has("voice-output-gains")', script)

    def test_hot_and_restart_changes_can_coexist(self):
        script = (WEB / "config-unified.js").read_text(encoding="utf-8")
        self.assertIn("const dirty = new Map()", script)
        self.assertIn("restartPending = true", script)
        self.assertIn("if (dirty.size > 0)", script)

    def test_restart_wait_uses_backend_loading_contract(self):
        script = (WEB / "config-unified.js").read_text(encoding="utf-8")
        self.assertIn("snapshot.environment_loading?.active", script)
        self.assertIn('runtime.ready && state !== "starting"', script)
        self.assertNotIn("reloadObserved", script)

    def test_frontend_asset_revision_was_bumped_for_reload_fix(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("rv2d-20260907i", app)


if __name__ == "__main__":
    unittest.main()
