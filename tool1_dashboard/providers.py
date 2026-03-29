from __future__ import annotations

from copy import deepcopy
import httpx
import json
import os
import shutil
import subprocess
import threading
import time
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
        self._probe_ttl_seconds = float(os.environ.get("TOOL1_PROVIDER_PROBE_TTL_SECONDS", "10"))
        self._structured_timeout_seconds = float(os.environ.get("TOOL1_PROVIDER_EXEC_TIMEOUT_SECONDS", "600"))
        self._probe_cache: dict[str, Any] | None = None
        self._probe_cached_at = 0.0
        self._probe_lock = threading.Lock()

    def probe(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        with self._probe_lock:
            if (
                not force
                and self._probe_cache is not None
                and (now - self._probe_cached_at) < self._probe_ttl_seconds
            ):
                return deepcopy(self._probe_cache)

        payload = {
            "codex": self._probe_codex(),
            "claude": self._probe_claude(),
        }
        with self._probe_lock:
            self._probe_cache = deepcopy(payload)
            self._probe_cached_at = now
        return payload

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
        api_key: str | None = None,
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

        if provider == "openai":
            request_path = artifact_dir / "request.json"
            request_payload = {
                "model": model or "gpt-5.4-mini",
                "instructions": system_prompt,
                "input": user_prompt,
                "store": False,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "structured_output",
                        "strict": True,
                        "schema": schema,
                    }
                },
            }
            write_json(request_path, request_payload)
            response_data = self._run_openai_structured(
                api_key=str(api_key or "").strip(),
                request_payload=request_payload,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                timeout_seconds=self._structured_timeout_seconds,
            )
            raw_text = self._extract_openai_output_text(response_data)
            parsed = self._parse_structured_response(raw_text or json.dumps(response_data))
            command_payload = {
                "provider": provider,
                "endpoint": "https://api.openai.com/v1/responses",
                "workdir": str(workdir),
                "prompt_path": str(prompt_path),
                "schema_path": str(schema_path),
                "request_path": str(request_path),
                "model": model or "gpt-5.4-mini",
                "transport": "https",
            }
        elif provider == "claude":
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
                command,
                user_prompt,
                stdout_path,
                stderr_path,
                workdir,
                timeout_seconds=self._structured_timeout_seconds,
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
                command,
                combined_prompt,
                stdout_path,
                stderr_path,
                workdir,
                timeout_seconds=self._structured_timeout_seconds,
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
    def _extract_openai_output_text(payload: dict[str, Any]) -> str:
        direct = str(payload.get("output_text") or "").strip()
        if direct:
            return direct
        output = payload.get("output")
        if not isinstance(output, list):
            return ""
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for entry in content:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("type") or "").strip() == "output_text":
                    text = str(entry.get("text") or "").strip()
                    if text:
                        parts.append(text)
        return "\n".join(parts).strip()

    def _run_openai_structured(
        self,
        *,
        api_key: str,
        request_payload: dict[str, Any],
        stdout_path: Path,
        stderr_path: Path,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        if not api_key:
            write_text(stderr_path, "OpenAI API key is required for this provider.")
            raise CliExecutionError(
                "OpenAI API key is required for this provider.",
                stdout="",
                stderr="OpenAI API key is required for this provider.",
            )
        timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 20.0))
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    "https://api.openai.com/v1/responses",
                    headers=headers,
                    json=request_payload,
                )
        except httpx.TimeoutException as exc:
            message = f"OpenAI API request timed out after {int(timeout_seconds)} seconds."
            write_text(stderr_path, message)
            raise CliExecutionError(message, stdout="", stderr=message) from exc
        except httpx.HTTPError as exc:
            message = f"OpenAI API request failed. {exc}"
            write_text(stderr_path, message)
            raise CliExecutionError(message, stdout="", stderr=message) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            body = response.text
            write_text(stdout_path, body)
            message = "OpenAI API returned invalid JSON."
            write_text(stderr_path, message)
            raise CliExecutionError(message, stdout=body, stderr=message) from exc

        write_json(stdout_path, payload)
        write_text(stderr_path, "")
        if response.status_code >= 400:
            detail = str(payload.get("error", {}).get("message") or "").strip() or response.text[:240].strip()
            if response.status_code == 401:
                message = f"OpenAI API key rejected. {detail}".strip()
            else:
                message = f"OpenAI API execution failed. {detail}".strip()
            write_text(stderr_path, message)
            raise CliExecutionError(message, stdout=json.dumps(payload), stderr=message)
        return payload

    @staticmethod
    def _run_streaming(
        command: list[str],
        stdin_text: str,
        stdout_path: Path,
        stderr_path: Path,
        cwd: Path,
        *,
        timeout_seconds: float | None = None,
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
            timed_out = False
            try:
                proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                timeout_message = f"CLI execution timed out after {int(timeout_seconds or 0)} seconds."
                try:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            timeout=10,
                        )
                    else:
                        proc.kill()
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                err_f.write(("\n" if err_f.tell() else "") + timeout_message + "\n")
                err_f.flush()
        stdout_text = read_text(stdout_path)
        stderr_text = read_text(stderr_path)
        returncode = proc.returncode
        if timed_out and returncode is None:
            returncode = -1
        return returncode, stdout_text, stderr_text

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
            if "timed out after" in detail.lower():
                return f"{label} CLI execution timed out. {detail}"
            return f"{label} CLI execution failed. {detail}"
        return f"{label} CLI execution failed."

    def _extract_cli_error_detail(self, raw: str, *, max_length: int = 300) -> str:
        text = strip_json_fences(raw or "")
        if not text:
            return ""
        direct = self._maybe_json(text)
        if isinstance(direct, dict):
            detail = self._deep_extract_error_message(direct)
            if detail:
                if len(detail) > max_length:
                    return detail[:max_length] + "…"
                return detail
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        detail = lines[-1] if lines else ""
        if len(detail) > max_length:
            return detail[:max_length] + "…"
        return detail

    def _deep_extract_error_message(self, obj: dict[str, Any], *, _depth: int = 0) -> str:
        """Recursively extract a human-readable message from nested API error JSON."""
        if _depth > 3:
            return ""
        for key in ("message", "result", "error", "text"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                # The value itself might be a JSON string wrapping another error
                nested = self._maybe_json(value.strip())
                if isinstance(nested, dict):
                    deeper = self._deep_extract_error_message(nested, _depth=_depth + 1)
                    if deeper:
                        return deeper
                return value.strip()
            if isinstance(value, dict):
                deeper = self._deep_extract_error_message(value, _depth=_depth + 1)
                if deeper:
                    return deeper
        return ""

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
