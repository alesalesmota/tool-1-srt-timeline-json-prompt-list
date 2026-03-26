from __future__ import annotations

import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from tool1_dashboard.providers import CliExecutionError, CliRunner


def _make_fake_popen(returncode: int, stdout_text: str, stderr_text: str, *, side_effect=None):
    """Return a factory that creates mock Popen instances writing to the real file handles."""
    def fake_popen(command, *, stdin, stdout, stderr, text, encoding, errors, cwd, **kwargs):
        if side_effect:
            side_effect(command, stdin_text="")
        if stdout and hasattr(stdout, "write"):
            stdout.write(stdout_text)
        if stderr and hasattr(stderr, "write"):
            stderr.write(stderr_text)
        proc = MagicMock()
        proc.returncode = returncode
        proc.stdin = MagicMock()
        proc.stdin.write = MagicMock()
        proc.stdin.close = MagicMock()
        proc.wait = MagicMock()
        return proc
    return fake_popen


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
        captured_command = []

        def on_call(command, stdin_text):
            captured_command.extend(command)

        fake = _make_fake_popen(0, json.dumps({"scenes": []}), "", side_effect=on_call)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch("tool1_dashboard.providers.subprocess.Popen", side_effect=fake):
                result = runner.run_structured(
                    provider="claude",
                    model="haiku",
                    system_prompt="system",
                    user_prompt="user",
                    schema={"type": "object", "properties": {"scenes": {"type": "array"}}, "required": ["scenes"]},
                    workdir=temp_path,
                    artifact_dir=temp_path / "artifacts",
                )
        self.assertIn("--system-prompt", captured_command)
        self.assertIn("--json-schema", captured_command)
        self.assertIn("--model", captured_command)
        self.assertIn("haiku", captured_command)
        self.assertNotIn("user", captured_command)
        self.assertEqual(result["parsed"], {"scenes": []})

    def test_codex_command_writes_last_message(self) -> None:
        runner = CliRunner()

        def fake(command, *, stdin, stdout, stderr, text, encoding, errors, cwd, **kwargs):
            out_index = command.index("-o") + 1
            Path(command[out_index]).write_text(json.dumps({"prompts": ["a", "b"]}), encoding="utf-8")
            if stdout and hasattr(stdout, "write"):
                stdout.write("{}")
            if stderr and hasattr(stderr, "write"):
                stderr.write("")
            proc = MagicMock()
            proc.returncode = 0
            proc.stdin = MagicMock()
            proc.wait = MagicMock()
            return proc

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch("tool1_dashboard.providers.subprocess.Popen", side_effect=fake):
                result = runner.run_structured(
                    provider="codex",
                    model="gpt-5.4",
                    system_prompt="system",
                    user_prompt="user",
                    schema={"type": "object", "properties": {"prompts": {"type": "array"}}, "required": ["prompts"]},
                    workdir=temp_path,
                    artifact_dir=temp_path / "artifacts",
                )
        self.assertEqual(result["parsed"], {"prompts": ["a", "b"]})

    def test_claude_limit_error_surfaces_real_message(self) -> None:
        runner = CliRunner()
        payload = {
            "type": "result",
            "is_error": True,
            "result": "You've hit your limit \u00b7 resets 6pm (America/Sao_Paulo)",
        }
        fake = _make_fake_popen(1, json.dumps(payload), "")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch("tool1_dashboard.providers.subprocess.Popen", side_effect=fake):
                with self.assertRaises(CliExecutionError) as context:
                    runner.run_structured(
                        provider="claude",
                        model="haiku",
                        system_prompt="system",
                        user_prompt="user",
                        schema={"type": "object", "properties": {"scenes": {"type": "array"}}, "required": ["scenes"]},
                        workdir=temp_path,
                        artifact_dir=temp_path / "artifacts",
                    )
        self.assertIn("Claude limit reached.", str(context.exception))
        self.assertIn("You've hit your limit", str(context.exception))

    def test_probe_uses_short_lived_cache(self) -> None:
        runner = CliRunner()
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            calls.append(command)
            result = MagicMock()
            result.returncode = 0
            result.stdout = json.dumps({"loggedIn": True}) if command[:2] == [runner.claude_bin, "auth"] else "logged in"
            result.stderr = ""
            return result

        with patch("tool1_dashboard.providers.shutil.which", side_effect=lambda command: f"/fake/{command}"):
            with patch("tool1_dashboard.providers.subprocess.run", side_effect=fake_run):
                with patch("tool1_dashboard.providers.time.monotonic", side_effect=[100.0, 100.5, 111.0]):
                    first = runner.probe()
                    second = runner.probe()
                    third = runner.probe()

        self.assertEqual(first["codex"]["available"], second["codex"]["available"])
        self.assertEqual(first["claude"]["available"], second["claude"]["available"])
        self.assertEqual(len(calls), 8)
        self.assertNotEqual(id(first), id(second))
        self.assertNotEqual(id(first["claude"]), id(second["claude"]))
        self.assertEqual(third["claude"]["logged_in"], first["claude"]["logged_in"])

    def test_probe_force_bypasses_cache(self) -> None:
        runner = CliRunner()
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            calls.append(command)
            result = MagicMock()
            result.returncode = 0
            result.stdout = json.dumps({"loggedIn": True}) if command[:2] == [runner.claude_bin, "auth"] else "logged in"
            result.stderr = ""
            return result

        with patch("tool1_dashboard.providers.shutil.which", side_effect=lambda command: f"/fake/{command}"):
            with patch("tool1_dashboard.providers.subprocess.run", side_effect=fake_run):
                with patch("tool1_dashboard.providers.time.monotonic", return_value=100.0):
                    runner.probe()
                    runner.probe(force=True)

        self.assertEqual(len(calls), 8)


if __name__ == "__main__":
    unittest.main()
