class AutoVideoError(Exception):
    """Base exception for the application."""


class TimelineValidationError(AutoVideoError):
    """Raised when the project or timeline is not valid."""

    def __init__(self, errors: list[str], warnings: list[str] | None = None):
        self.errors = errors
        self.warnings = warnings or []
        super().__init__("\n".join(errors))


class CommandExecutionError(AutoVideoError):
    """Raised when an external command fails."""

    def __init__(
        self,
        description: str,
        command: list[str],
        stdout: str = "",
        stderr: str = "",
    ):
        self.description = description
        self.command = command
        self.stdout = stdout
        self.stderr = stderr
        message = f"{description} failed: {' '.join(command)}"
        if stderr.strip():
            message = f"{message}\n{stderr.strip()}"
        super().__init__(message)


class RenderCancelledError(AutoVideoError):
    """Raised when the dashboard requests the current render job to stop."""
