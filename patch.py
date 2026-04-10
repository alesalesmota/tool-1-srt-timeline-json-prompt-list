import re

with open("tool1_dashboard/providers.py", "r", encoding="utf-8") as f:
    content = f.read()

# Original run_structured logic:
search_block = """        if args.provider in ("openai", "codex"):
            default_model = "gpt-5.4-mini" if args.provider == "openai" else "gpt-5.4"
            request_path = args.artifact_dir / "request.json"
            request_payload = {
                "model": args.model or default_model,
                "instructions": args.system_prompt,
                "input": args.user_prompt,
                "store": False,
                "max_output_tokens": 16384,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "structured_output",
                        "strict": True,
                        "schema": args.schema,
                    }
                },
            }
            write_json(request_path, request_payload)
            response_data = self._run_openai_structured(
                api_key=str(args.api_key or "").strip(),
                request_payload=request_payload,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                timeout_seconds=self._structured_timeout_seconds,
            )
            raw_text = self._extract_openai_output_text(response_data)
            parsed = self._parse_structured_response(raw_text or json.dumps(response_data))
            command_payload = {
                "provider": args.provider,
                "endpoint": "https://api.openai.com/v1/responses",
                "workdir": str(args.workdir),
                "prompt_path": str(prompt_path),
                "schema_path": str(schema_path),
                "request_path": str(request_path),
                "model": args.model or default_model,
                "transport": "https",
            }
        elif args.provider == "claude":
            command = [
                self.claude_bin,
                "-p",
                "--model",
                args.model or "haiku",
                "--system-prompt",
                args.system_prompt,
                "--output-format",
                "json",
                "--json-schema",
                json.dumps(args.schema, ensure_ascii=False),
            ]
            streaming_args = StreamingArgs(
                command=command,
                stdin_text=args.user_prompt,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                cwd=args.workdir,
                timeout_seconds=self._structured_timeout_seconds,
            )
            returncode, stdout_text, stderr_text = self._run_streaming(streaming_args)
            if returncode != 0:
                raise CliExecutionError(
                    self._build_cli_error_message("claude", stdout_text, stderr_text),
                    stdout=stdout_text,
                    stderr=stderr_text,
                )
            parsed = self._parse_structured_response(stdout_text)
            command_payload = {
                "provider": args.provider,
                "command": [
                    self.claude_bin,
                    "-p",
                    "--model",
                    args.model or "haiku",
                    "<stdin>",
                    "--system-prompt",
                    "<inline>",
                    "--output-format",
                    "json",
                    "--json-schema",
                    "<inline>",
                ],
                "workdir": str(args.workdir),
                "prompt_path": str(prompt_path),
                "schema_path": str(schema_path),
                "prompt_transport": "stdin",
                "model": args.model or "haiku",
            }"""

replace_block = """        if args.provider in ("openai", "codex"):
            parsed, command_payload = self._run_structured_openai(
                args, prompt_path, schema_path, stdout_path, stderr_path
            )
        elif args.provider == "claude":
            parsed, command_payload = self._run_structured_claude(
                args, prompt_path, schema_path, stdout_path, stderr_path
            )"""

new_content = content.replace(search_block, replace_block)

methods_to_add = """    def _run_structured_openai(self, args: StructuredRunArgs, prompt_path: Path, schema_path: Path, stdout_path: Path, stderr_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        default_model = "gpt-5.4-mini" if args.provider == "openai" else "gpt-5.4"
        request_path = args.artifact_dir / "request.json"
        request_payload = {
            "model": args.model or default_model,
            "instructions": args.system_prompt,
            "input": args.user_prompt,
            "store": False,
            "max_output_tokens": 16384,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "structured_output",
                    "strict": True,
                    "schema": args.schema,
                }
            },
        }
        write_json(request_path, request_payload)
        response_data = self._run_openai_structured(
            api_key=str(args.api_key or "").strip(),
            request_payload=request_payload,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            timeout_seconds=self._structured_timeout_seconds,
        )
        raw_text = self._extract_openai_output_text(response_data)
        parsed = self._parse_structured_response(raw_text or json.dumps(response_data))
        command_payload = {
            "provider": args.provider,
            "endpoint": "https://api.openai.com/v1/responses",
            "workdir": str(args.workdir),
            "prompt_path": str(prompt_path),
            "schema_path": str(schema_path),
            "request_path": str(request_path),
            "model": args.model or default_model,
            "transport": "https",
        }
        return parsed, command_payload

    def _run_structured_claude(self, args: StructuredRunArgs, prompt_path: Path, schema_path: Path, stdout_path: Path, stderr_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        command = [
            self.claude_bin,
            "-p",
            "--model",
            args.model or "haiku",
            "--system-prompt",
            args.system_prompt,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(args.schema, ensure_ascii=False),
        ]
        streaming_args = StreamingArgs(
            command=command,
            stdin_text=args.user_prompt,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            cwd=args.workdir,
            timeout_seconds=self._structured_timeout_seconds,
        )
        returncode, stdout_text, stderr_text = self._run_streaming(streaming_args)
        if returncode != 0:
            raise CliExecutionError(
                self._build_cli_error_message("claude", stdout_text, stderr_text),
                stdout=stdout_text,
                stderr=stderr_text,
            )
        parsed = self._parse_structured_response(stdout_text)
        command_payload = {
            "provider": args.provider,
            "command": [
                self.claude_bin,
                "-p",
                "--model",
                args.model or "haiku",
                "<stdin>",
                "--system-prompt",
                "<inline>",
                "--output-format",
                "json",
                "--json-schema",
                "<inline>",
            ],
            "workdir": str(args.workdir),
            "prompt_path": str(prompt_path),
            "schema_path": str(schema_path),
            "prompt_transport": "stdin",
            "model": args.model or "haiku",
        }
        return parsed, command_payload

    @staticmethod
    def _extract_openai_output_text(payload: dict[str, Any]) -> str:"""

new_content = new_content.replace("    @staticmethod\n    def _extract_openai_output_text(payload: dict[str, Any]) -> str:", methods_to_add)

with open("tool1_dashboard/providers.py", "w", encoding="utf-8") as f:
    f.write(new_content)
