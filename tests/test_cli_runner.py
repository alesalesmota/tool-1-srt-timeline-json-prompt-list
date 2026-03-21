from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tool1_dashboard.providers import CliRunner


class CliRunnerTests(unittest.TestCase):
    def test_parse_structured_output_wrapper(self) -> None:
        runner = CliRunner()
        payload = {
            "type": "result",
            "result": "Human-readable summary",
            "structured_output": {"scenes": [{"start": 0, "end": 1, "duration": 1, "text": "x", "asset_type": "image"}]},
        }

        parsed = runner._parse_structured_response(json.dumps(payload))

        self.assertEqual(parsed, payload["structured_output"])

    def test_claude_command_uses_system_prompt(self) -> None:
        runner = CliRunner()

        def fake_run(command, input, capture_output, text, cwd, **kwargs):  # type: ignore[no-untyped-def]
            self.assertIn("--system-prompt", command)
            self.assertIn("--json-schema", command)
            self.assertEqual(input, "user")
            self.assertNotIn("user", command)
            return type("Result", (), {"returncode": 0, "stdout": json.dumps({"scenes": []}), "stderr": ""})()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch("tool1_dashboard.providers.subprocess.run", side_effect=fake_run):
                result = runner.run_structured(
                    provider="claude",
                    system_prompt="system",
                    user_prompt="user",
                    schema={"type": "object", "properties": {"scenes": {"type": "array"}}, "required": ["scenes"]},
                    workdir=temp_path,
                    artifact_dir=temp_path / "artifacts",
                )
        self.assertEqual(result["parsed"], {"scenes": []})

    def test_codex_command_writes_last_message(self) -> None:
        runner = CliRunner()

        def fake_run(command, input, capture_output, text, cwd, **kwargs):  # type: ignore[no-untyped-def]
            out_index = command.index("-o") + 1
            Path(command[out_index]).write_text(json.dumps({"prompts": ["a", "b"]}), encoding="utf-8")
            self.assertIn("--output-schema", command)
            self.assertIn("# System Instructions", input)
            return type("Result", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch("tool1_dashboard.providers.subprocess.run", side_effect=fake_run):
                result = runner.run_structured(
                    provider="codex",
                    system_prompt="system",
                    user_prompt="user",
                    schema={"type": "object", "properties": {"prompts": {"type": "array"}}, "required": ["prompts"]},
                    workdir=temp_path,
                    artifact_dir=temp_path / "artifacts",
                )
        self.assertEqual(result["parsed"], {"prompts": ["a", "b"]})


if __name__ == "__main__":
    unittest.main()
