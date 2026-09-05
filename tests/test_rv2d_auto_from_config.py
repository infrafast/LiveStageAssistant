import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.rv2d_auto_from_config import build_auto_args


class RV2DAutoFromConfigTests(unittest.TestCase):
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

    def test_maps_canonical_auto_open_policy_to_existing_runner_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".env.online"
            env_file.write_text("", encoding="utf-8")
            config_path = root / "mcp.json"
            config_path.write_text(json.dumps({
                "mcpServers": {
                    "mixer": {
                        "command": "node",
                        "args": ["server.js"],
                        "native": {"url": "https://example.test/mcp"},
                        "realtime": {"transport": "auto", "permissions": {"mode": "open"}},
                    }
                }
            }), encoding="utf-8")
            with patch.dict(os.environ, {}, clear=False):
                args = build_auto_args(self._cli(env_file, config_path))
            self.assertEqual(args.mcp_url, "https://example.test/mcp")
            self.assertEqual(args.permission_mode, "open")
            self.assertEqual(args.allow_tool, [])
            self.assertEqual(args.mcp_config, str(config_path))

    def test_maps_restricted_allow_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".env.online"
            env_file.write_text("", encoding="utf-8")
            config_path = root / "mcp.json"
            config_path.write_text(json.dumps({
                "mcpServers": {
                    "mixer": {
                        "native": {"url": "https://example.test/mcp", "headers": {"X-Test": "ok"}},
                        "realtime": {
                            "transport": "auto",
                            "permissions": {"mode": "restricted", "allowedTools": ["alpha", "beta"]},
                        },
                    }
                }
            }), encoding="utf-8")
            args = build_auto_args(self._cli(env_file, config_path))
            self.assertEqual(args.permission_mode, "restricted")
            self.assertEqual(args.allow_tool, ["alpha", "beta"])
            self.assertEqual(args.mcp_header, ["X-Test=ok"])

    def test_rejects_non_auto_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".env.online"
            env_file.write_text("", encoding="utf-8")
            config_path = root / "mcp.json"
            config_path.write_text(json.dumps({
                "mcpServers": {
                    "qlcplus": {
                        "realtime": {"transport": "stdio", "permissions": {"mode": "open"}}
                    }
                }
            }), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "not 'auto'"):
                build_auto_args(self._cli(env_file, config_path, server="qlcplus"))


if __name__ == "__main__":
    unittest.main()
