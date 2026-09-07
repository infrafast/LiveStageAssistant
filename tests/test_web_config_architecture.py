from pathlib import Path
import json
import unittest


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "assets" / "web"


class WebConfigArchitectureTests(unittest.TestCase):
    def test_bootstrap_runs_before_app_main(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertLess(app.index("config-bootstrap.js"), app.index("app-main.js"))

    def test_web_boot_continues_when_optional_module_fails(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn('script.addEventListener("error"', app)
        self.assertIn("if (oncomplete) oncomplete(ok)", app)
        self.assertIn("reportBootError", app)

    def test_unified_controller_has_no_dual_wake_selector(self):
        script = (WEB / "config-unified.js").read_text(encoding="utf-8")
        self.assertNotIn("wake-word-select", script)
        self.assertNotIn("legacyWake", script)
        self.assertNotIn("__lsaWakeCanonicalFetchInstalled", script)

    def test_bootstrap_replaces_wake_control_with_single_canonical_select(self):
        script = (WEB / "config-bootstrap.js").read_text(encoding="utf-8")
        self.assertIn('select.id = "wake-word"', script)
        self.assertIn("current.replaceWith(select)", script)
        self.assertNotIn("wake-word-select", script)

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


if __name__ == "__main__":
    unittest.main()
