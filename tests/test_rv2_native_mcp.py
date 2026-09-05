import importlib.util
from pathlib import Path
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "rv2_native_mcp.py"
SPEC = importlib.util.spec_from_file_location("rv2_native_mcp", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"could not load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
permission_policy = MODULE.permission_policy


class RV2NativeMCPPermissionTests(unittest.TestCase):
    def args(self, **overrides):
        values = {
            "discover_only": False,
            "permission_mode": "open",
            "allow_tool": [],
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_open_is_permissive_default(self):
        allowed_tools, approval = permission_policy(self.args())
        self.assertEqual(allowed_tools, ())
        self.assertEqual(approval, "never")

    def test_approval_mode_exposes_all_tools_but_requires_approval(self):
        allowed_tools, approval = permission_policy(self.args(permission_mode="approval"))
        self.assertEqual(allowed_tools, ())
        self.assertEqual(approval, "always")

    def test_restricted_mode_uses_explicit_allow_list(self):
        allowed_tools, approval = permission_policy(
            self.args(permission_mode="restricted", allow_tool=["read_main", "read_channel", "read_main"])
        )
        self.assertEqual(allowed_tools, ("read_main", "read_channel"))
        self.assertEqual(approval, "never")

    def test_restricted_mode_requires_at_least_one_tool(self):
        with self.assertRaises(RuntimeError):
            permission_policy(self.args(permission_mode="restricted"))

    def test_discovery_mode_overrides_live_permissions(self):
        allowed_tools, approval = permission_policy(
            self.args(discover_only=True, permission_mode="open", allow_tool=["write_tool"])
        )
        self.assertEqual(allowed_tools, ())
        self.assertEqual(approval, "always")


if __name__ == "__main__":
    unittest.main()
