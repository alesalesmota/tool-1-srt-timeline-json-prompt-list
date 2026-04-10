from __future__ import annotations

import httpx
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tool1_dashboard.providers import CliExecutionError, CliRunner, StructuredRunArgs


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


class FakeHttpxResponse:
    def __init__(self, status_code: int, payload: dict[str, object], text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict[str, object]:
        return self._payload


class FakeHttpxClient:
    def __init__(self, response: FakeHttpxResponse, recorder: list[dict[str, object]]) -> None:
        self.response = response
        self.recorder = recorder

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url: str, *, headers: dict[str, str] | None = None, json: dict[str, object] | None = None):
        self.recorder.append({
            "url": url,
            "headers": headers or {},
            "json": json or {},
        })
        return self.response


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
                    StructuredRunArgs(
                        provider="claude",
                        model="haiku",
                        system_prompt="system",
                        user_prompt="user",
                        schema={"type": "object", "properties": {"scenes": {"type": "array"}}, "required": ["scenes"]},
                        workdir=temp_path,
                        artifact_dir=temp_path / "artifacts",
                    )
                )
        self.assertIn("--system-prompt", captured_command)
        self.assertIn("--json-schema", captured_command)
        self.assertIn("--model", captured_command)
        self.assertIn("haiku", captured_command)
        self.assertNotIn("user", captured_command)
        self.assertEqual(result["parsed"], {"scenes": []})

    def test_codex_provider_uses_openai_api(self) -> None:
        runner = CliRunner()
        recorder: list[dict[str, object]] = []
        response_payload = {
            "output_text": json.dumps({"prompts": ["a", "b"]}),
        }

        def fake_post(url, *, headers, json, **kwargs):
            recorder.append({"url": url, "json": json})
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = response_payload
            return resp

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch("tool1_dashboard.providers.httpx.Client") as mock_client:
                mock_client.return_value.__enter__ = MagicMock(return_value=MagicMock(post=fake_post))
                mock_client.return_value.__exit__ = MagicMock(return_value=False)
                result = runner.run_structured(
                    StructuredRunArgs(
                        provider="codex",
                        model="gpt-5.4",
                        api_key="test-key",
                        system_prompt="system",
                        user_prompt="user",
                        schema={"type": "object", "properties": {"prompts": {"type": "array"}}, "required": ["prompts"]},
                        workdir=temp_path,
                        artifact_dir=temp_path / "artifacts",
                    )
                )
        self.assertEqual(result["parsed"], {"prompts": ["a", "b"]})
        self.assertEqual(recorder[0]["json"]["model"], "gpt-5.4")
        self.assertIn("openai.com", recorder[0]["url"])

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
                        StructuredRunArgs(
                            provider="claude",
                            model="haiku",
                            system_prompt="system",
                            user_prompt="user",
                            schema={"type": "object", "properties": {"scenes": {"type": "array"}}, "required": ["scenes"]},
                            workdir=temp_path,
                            artifact_dir=temp_path / "artifacts",
                        )
                    )
        self.assertIn("Claude limit reached.", str(context.exception))
        self.assertIn("You've hit your limit", str(context.exception))

    def test_codex_timeout_surfaces_real_message(self) -> None:
        runner = CliRunner()
        runner._structured_timeout_seconds = 30

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch("tool1_dashboard.providers.httpx.Client") as mock_client:
                mock_instance = MagicMock()
                mock_instance.post.side_effect = httpx.TimeoutException("timed out")
                mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
                mock_client.return_value.__exit__ = MagicMock(return_value=False)
                with self.assertRaises(CliExecutionError) as context:
                    runner.run_structured(
                        StructuredRunArgs(
                            provider="codex",
                            model="gpt-5.4-mini",
                            api_key="test-key",
                            system_prompt="system",
                            user_prompt="user",
                            schema={"type": "object", "properties": {"prompts": {"type": "array"}}, "required": ["prompts"]},
                            workdir=temp_path,
                            artifact_dir=temp_path / "artifacts",
                        )
                    )
        self.assertIn("timed out", str(context.exception))

    def test_openai_structured_request_uses_responses_api(self) -> None:
        runner = CliRunner()
        recorder: list[dict[str, object]] = []
        response_payload = {
            "output_text": json.dumps({
                "scenes": [
                    {"start": 0, "end": 1, "duration": 1, "text": "Opening scene"}
                ]
            })
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch(
                "tool1_dashboard.providers.httpx.Client",
                side_effect=lambda *args, **kwargs: FakeHttpxClient(FakeHttpxResponse(200, response_payload), recorder),
            ):
                result = runner.run_structured(
                    StructuredRunArgs(
                        provider="openai",
                        model="gpt-5.4-mini",
                        api_key="sk-stage",
                        system_prompt="system",
                        user_prompt="user",
                        schema={"type": "object", "properties": {"scenes": {"type": "array"}}, "required": ["scenes"]},
                        workdir=temp_path,
                        artifact_dir=temp_path / "artifacts",
                    )
                )

        self.assertEqual(result["parsed"]["scenes"][0]["text"], "Opening scene")
        self.assertEqual(recorder[0]["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(recorder[0]["headers"]["Authorization"], "Bearer sk-stage")
        self.assertEqual(recorder[0]["json"]["text"]["format"]["type"], "json_schema")
        self.assertEqual(recorder[0]["json"]["text"]["format"]["name"], "structured_output")

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

        self.assertEqual(first["claude"]["available"], second["claude"]["available"])
        self.assertEqual(len(calls), 4)  # 2 calls per probe (version + auth), cached on 2nd, refreshed on 3rd
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

        self.assertEqual(len(calls), 4)  # 2 calls per probe (version + auth) x 2 probes

    def test_deep_extract_error_message_depth_limit(self) -> None:
        runner = CliRunner()

        # Test just within depth limit (depth=0, 1, 2, 3 -> 4 levels, but we start _depth=0 at top level)
        # top level: _depth=0
        # "error" 1: _depth=1
        # "error" 2: _depth=2
        # "error" 3: _depth=3
        payload_ok = {
            "error": {
                "error": {
                    "error": {
                        "message": "Just deep enough"
                    }
                }
            }
        }
        self.assertEqual(runner._deep_extract_error_message(payload_ok), "Just deep enough")

        # Test exceeding depth limit
        # top level: _depth=0
        # "error" 1: _depth=1
        # "error" 2: _depth=2
        # "error" 3: _depth=3
        # "error" 4: _depth=4 (> 3) -> returns ""
        payload_too_deep = {
            "error": {
                "error": {
                    "error": {
                        "error": {
                            "message": "Too deep"
                        }
                    }
                }
            }
        }
        self.assertEqual(runner._deep_extract_error_message(payload_too_deep), "")


if __name__ == "__main__":
    unittest.main()
