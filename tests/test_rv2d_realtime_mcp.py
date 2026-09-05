import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.rv2d_realtime_mcp import build_runner_args


class RV2DRealtimeMCPTests(unittest.TestCase):
    def _cli(self, env_file: Path, config_path: Path, server: str = "mixer"):
        return argparse.Namespace(
            env_file=str(env_file),
            mcp_config=str(config_path),
            mcp_server=server,
            mcp_label="",
            model="gpt-realtime-2.1",
            voice="marin",
            duration=30.0,
            input_device=None,
            output_device=None,
        )

    def _write(self, root: Path, entry: dict, server: str = "mixer") -> tuple[Path, Path]:
        env_file = root / ".env.online"
        env_file.write_text("", encoding="utf-8")
        config_path = root / "mcp.json"
        config_path.write_text(json.dumps({"mcpServers": {server: entry}}), encoding="utf-8")
        return env_file, config_path

    def test_auto_maps_native_url_and_open_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file, config_path = self._write(root, {
                "command": "node",
                "args": ["server.js"],
                "native": {"url": "https://example.test/mcp"},
                "realtime": {"transport": "auto", "permissions": {"mode": "open"}},
            })
            transport, args = build_runner_args(self._cli(env_file, config_path))
            self.assertEqual(transport, "auto")
            self.assertEqual(args.mcp_url, "https://example.test/mcp")
            self.assertEqual(args.permission_mode, "open")

    def test_native_maps_restricted_permission_and_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file, config_path = self._write(root, {
                "native": {"url": "https://example.test/mcp", "headers": {"X-Test": "ok"}},
                "realtime": {
                    "transport": "native",
                    "permissions": {"mode": "restricted", "allowedTools": ["alpha"]},
                },
            })
            transport, args = build_runner_args(self._cli(env_file, config_path))
            self.assertEqual(transport, "native")
            self.assertEqual(args.permission_mode, "restricted")
            self.assertEqual(args.allow_tool, ["alpha"])
            self.assertEqual(args.mcp_header, ["X-Test=ok"])

    def test_stdio_open_keeps_native_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file, config_path = self._write(root, {
                "command": "node",
                "args": ["server.js"],
                "realtime": {"transport": "stdio", "permissions": {"mode": "open"}},
            }, server="qlcplus")
            transport, args = build_runner_args(self._cli(env_file, config_path, server="qlcplus"))
            self.assertEqual(transport, "stdio")
            self.assertEqual(args.allow_tool, [])
            self.assertFalse(hasattr(args, "mcp_url"))

    def test_stdio_restricted_maps_allow_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file, config_path = self._write(root, {
                "command": "node",
                "args": ["server.js"],
                "realtime": {
                    "transport": "stdio",
                    "permissions": {"mode": "restricted", "allowedTools": ["one", "two"]},
                },
            })
            transport, args = build_runner_args(self._cli(env_file, config_path))
            self.assertEqual(transport, "stdio")
            self.assertEqual(args.allow_tool, ["one", "two"])

    def test_native_requires_https_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file, config_path = self._write(root, {
                "realtime": {"transport": "native", "permissions": {"mode": "open"}},
            })
            with self.assertRaisesRegex(RuntimeError, "no native HTTPS URL"):
                build_runner_args(self._cli(env_file, config_path))

    def test_stdio_approval_is_rejected_until_runtime_support_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file, config_path = self._write(root, {
                "command": "node",
                "args": ["server.js"],
                "realtime": {"transport": "stdio", "permissions": {"mode": "approval"}},
            })
            with self.assertRaisesRegex(RuntimeError, "approval mode is not yet implemented"):
                build_runner_args(self._cli(env_file, config_path))


if __name__ == "__main__":
    unittest.main()
