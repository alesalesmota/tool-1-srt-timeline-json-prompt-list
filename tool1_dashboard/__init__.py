try:
    from .app import app
except ModuleNotFoundError as exc:
    if exc.name not in {"fastapi", "uvicorn", "pydantic"}:
        raise
    app = None

__all__ = ["app"]
