with open("tool1_dashboard/providers.py", "r", encoding="utf-8") as f:
    content = f.read()

# Fix long lines introduced in our refactoring:
# 1. _run_structured_openai signature
search1 = "    def _run_structured_openai(self, args: StructuredRunArgs, prompt_path: Path, schema_path: Path, stdout_path: Path, stderr_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:"
replace1 = """    def _run_structured_openai(
        self,
        args: StructuredRunArgs,
        prompt_path: Path,
        schema_path: Path,
        stdout_path: Path,
        stderr_path: Path,
    ) -> tuple[dict[str, Any], dict[str, Any]]:"""

# 2. _run_structured_claude signature
search2 = "    def _run_structured_claude(self, args: StructuredRunArgs, prompt_path: Path, schema_path: Path, stdout_path: Path, stderr_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:"
replace2 = """    def _run_structured_claude(
        self,
        args: StructuredRunArgs,
        prompt_path: Path,
        schema_path: Path,
        stdout_path: Path,
        stderr_path: Path,
    ) -> tuple[dict[str, Any], dict[str, Any]]:"""

new_content = content.replace(search1, replace1).replace(search2, replace2)

with open("tool1_dashboard/providers.py", "w", encoding="utf-8") as f:
    f.write(new_content)
