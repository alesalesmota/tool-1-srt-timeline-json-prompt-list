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
                "--system-prompt",
                system_prompt,
                "--output-format",
                "json",
                "--json-schema",
                json.dumps(schema, ensure_ascii=False),
            ]
            result = subprocess.run(
                command,
                input=user_prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=workdir,
            )
            write_text(stdout_path, result.stdout or "")
            write_text(stderr_path, result.stderr or "")
            if result.returncode != 0:
                raise CliExecutionError(
                    "Claude CLI execution failed.",
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            parsed = self._parse_structured_response(result.stdout)
            command_payload = {
                "provider": provider,
                "command": [
                    self.claude_bin,
                    "-p",
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
                "--json",
                "--output-schema",
                str(schema_path),
                "-o",
                str(last_message_path),
                "-C",
                str(workdir),
                "-",
            ]
            result = subprocess.run(
                command,
                input=combined_prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=workdir,
            )
            write_text(stdout_path, result.stdout or "")
            write_text(stderr_path, result.stderr or "")
            if result.returncode != 0:
                raise CliExecutionError(
                    "Codex CLI execution failed.",
                    stdout=result.stdout,
                    stderr=result.stderr,
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
