from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .runtime import ensure_dir, read_text, strip_json_fences, write_json, write_text


class CliExecutionError(RuntimeError):
    def __init__(self, message: str, *, stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class CliRunner:
    def __init__(self) -> None:
        self.codex_bin = os.environ.get("TOOL1_CODEX_BIN") or shutil.which("codex") or "codex"
        self.claude_bin = os.environ.get("TOOL1_CLAUDE_BIN") or shutil.which("claude") or "claude"

    def probe(self) -> dict[str, Any]:
        return {
            "codex": self._probe_codex(),
            "claude": self._probe_claude(),
        }

    def _probe_codex(self) -> dict[str, Any]:
        path = shutil.which(self.codex_bin) if self.codex_bin != "codex" else shutil.which("codex")
        available = path is not None
        version = None
        logged_in = False
        detail = ""
        if available:
            try:
                version_result = subprocess.run(
                    [self.codex_bin, "--version"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=15,
                )
                version = (version_result.stdout or version_result.stderr).strip()
            except Exception as exc:
                detail = str(exc)
            try:
                login_result = subprocess.run(
                    [self.codex_bin, "login", "status"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=15,
                )
                detail = detail or (login_result.stdout or login_result.stderr).strip()
                logged_in = login_result.returncode == 0 and "logged in" in detail.lower()
            except Exception as exc:
                detail = detail or str(exc)
        return {
            "available": available,
            "path": path,
            "version": version,
            "logged_in": logged_in,
            "detail": detail,
        }

    def _probe_claude(self) -> dict[str, Any]:
        path = shutil.which(self.claude_bin) if self.claude_bin != "claude" else shutil.which("claude")
        available = path is not None
        version = None
        logged_in = False
        detail = ""
        auth_payload = None
        if available:
            try:
                version_result = subprocess.run(
                    [self.claude_bin, "--version"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=15,
                )
                version = (version_result.stdout or version_result.stderr).strip()
            except Exception as exc:
                detail = str(exc)
            try:
                auth_result = subprocess.run(
                    [self.claude_bin, "auth", "status"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=15,
                )
                payload_text = (auth_result.stdout or auth_result.stderr).strip()
                detail = detail or payload_text
                auth_payload = json.loads(payload_text)
                logged_in = bool(auth_payload.get("loggedIn"))
            except Exception as exc:
                detail = detail or str(exc)
        return {
            "available": available,
            "path": path,
            "version": version,
            "logged_in": logged_in,
            "detail": detail,
            "auth": auth_payload,
        }

    def run_structured(
        self,
        *,
        provider: str,
        model: str | None,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        workdir: Path,
        artifact_dir: Path,
    ) -> dict[str, Any]:
        ensure_dir(artifact_dir)
        prompt_path = write_text(artifact_dir / "prompt.txt", user_prompt)
        schema_path = write_json(artifact_dir / "schema.json", schema)
        stdout_path = artifact_dir / "stdout.txt"
        stderr_path = artifact_dir / "stderr.txt"
        parsed_path = artifact_dir / "parsed.json"
        command_payload: dict[str, Any]

        if provider == "claude":
            command = [
                self.claude_bin,
                "-p",
                "--model",
                model or "haiku",
                "--system-prompt",
                system_prompt,
                "--output-format",
                "json",
                "--json-schema",
                json.dumps(schema, ensure_ascii=False),
            ]
            returncode, stdout_text, stderr_text = self._run_streaming(
                command, user_prompt, stdout_path, stderr_path, workdir,
            )
            if returncode != 0:
                raise CliExecutionError(
                    self._build_cli_error_message("claude", stdout_text, stderr_text),
                    stdout=stdout_text,
                    stderr=stderr_text,
                )
            parsed = self._parse_structured_response(stdout_text)
            command_payload = {
                "provider": provider,
                "command": [
                    self.claude_bin,
                    "-p",
                    "--model",
                    model or "haiku",
                    "<stdin>",
                    "--system-prompt",
                    "<inline>",
                    "--output-format",
                    "json",
                    "--json-schema",
                    "<inline>",
                ],
                "workdir": str(workdir),
                "prompt_path": str(prompt_path),
                "schema_path": str(schema_path),
                "prompt_transport": "stdin",
                "model": model or "haiku",
            }
        elif provider == "codex":
            last_message_path = artifact_dir / "last_message.txt"
            combined_prompt = (
                "# System Instructions\n"
                f"{system_prompt.strip()}\n\n"
                "# Task\n"
                f"{user_prompt.strip()}\n"
            )
            command = [
                self.codex_bin,
                "exec",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--model",
                model or "gpt-5.4",
                "--json",
                "--output-schema",
                str(schema_path),
                "-o",
                str(last_message_path),
                "-C",
                str(workdir),
                "-",
            ]
            returncode, stdout_text, stderr_text = self._run_streaming(
                command, combined_prompt, stdout_path, stderr_path, workdir,
            )
            if returncode != 0:
                raise CliExecutionError(
                    self._build_cli_error_message("codex", stdout_text, stderr_text),
                    stdout=stdout_text,
                    stderr=stderr_text,
                )
            parsed = self._parse_structured_response(read_text(last_message_path))
            command_payload = {
                "provider": provider,
                "command": command,
                "workdir": str(workdir),
                "prompt_path": str(prompt_path),
                "schema_path": str(schema_path),
                "last_message_path": str(last_message_path),
                "prompt_transport": "stdin",
                "model": model or "gpt-5.4",
            }
        else:
            raise ValueError(f"Unsupported provider '{provider}'.")

        write_json(parsed_path, parsed)
        return {
            "parsed": parsed,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "parsed_path": str(parsed_path),
            "command_payload": command_payload,
        }

    @staticmethod
    def _run_streaming(
        command: list[str],
        stdin_text: str,
        stdout_path: Path,
        stderr_path: Path,
        cwd: Path,
    ) -> tuple[int, str, str]:
        """Run a subprocess while streaming stdout/stderr to files in real-time."""
        with open(stdout_path, "w", encoding="utf-8", errors="replace") as out_f, \
             open(stderr_path, "w", encoding="utf-8", errors="replace") as err_f:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=out_f,
                stderr=err_f,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=cwd,
            )
            if stdin_text:
                try:
                    proc.stdin.write(stdin_text)
                    proc.stdin.close()
                except BrokenPipeError:
                    pass
            proc.wait()
        stdout_text = read_text(stdout_path)
        stderr_text = read_text(stderr_path)
        return proc.returncode, stdout_text, stderr_text

    def _parse_structured_response(self, raw: str) -> dict[str, Any]:
        text = strip_json_fences(raw)
        if not text:
            raise ValueError("The CLI returned an empty response.")
        direct = self._maybe_json(text)
        if isinstance(direct, dict):
            nested = self._unwrap_possible_wrapper(direct)
            if isinstance(nested, dict):
                return nested
        raise ValueError("The CLI did not return valid JSON output.")

    def _build_cli_error_message(self, provider: str, stdout: str, stderr: str) -> str:
        detail = self._extract_cli_error_detail(stdout) or self._extract_cli_error_detail(stderr)
        label = "Claude" if provider == "claude" else "Codex" if provider == "codex" else provider.title()
        if detail:
            if provider == "claude" and "hit your limit" in detail.lower():
                return f"{label} limit reached. {detail}"
            return f"{label} CLI execution failed. {detail}"
        return f"{label} CLI execution failed."

    def _extract_cli_error_detail(self, raw: str) -> str:
        text = strip_json_fences(raw or "")
        if not text:
            return ""
        direct = self._maybe_json(text)
        if isinstance(direct, dict):
            for key in ("result", "error", "message", "text"):
                value = direct.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return lines[-1] if lines else ""

    def _unwrap_possible_wrapper(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if "structured_output" in payload:
            structured = payload["structured_output"]
            if isinstance(structured, dict):
                return structured
            if isinstance(structured, str):
                nested = self._maybe_json(strip_json_fences(structured))
                if isinstance(nested, dict):
                    return nested
        for key in ("result", "content", "message", "text", "output"):
            if key not in payload:
                continue
            candidate = payload[key]
            if isinstance(candidate, str):
                nested = self._maybe_json(strip_json_fences(candidate))
                if isinstance(nested, dict):
                    return nested
            if isinstance(candidate, list):
                for item in candidate:
                    if isinstance(item, dict):
                        if "text" in item and isinstance(item["text"], str):
                            nested = self._maybe_json(strip_json_fences(item["text"]))
                            if isinstance(nested, dict):
                                return nested
                        nested = self._unwrap_possible_wrapper(item)
                        if nested:
                            return nested
        return payload

    @staticmethod
    def _maybe_json(value: str) -> Any:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
