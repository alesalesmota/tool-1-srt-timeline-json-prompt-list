from __future__ import annotations

import os
import subprocess
import threading
from copy import deepcopy
from pathlib import Path
from typing import Callable

from .config import LanguageProfile, resolve_language_profile, resolve_mfa_resources
from .runtime import resolve_mfa_command

MFA_CONDA_EXE = Path.home() / "miniconda3" / "Scripts" / "conda.exe"
MFA_ENV_NAME = os.environ.get("ALIGNMENT_MFA_ENV_NAME", "mfa-aligner")
RESOURCE_STATE_LOCK = threading.Lock()
RESOURCE_STATES: dict[str, dict[str, object]] = {}
RESOURCE_LOCKS: dict[str, threading.Lock] = {}


def _state_for(profile: LanguageProfile) -> dict[str, object]:
    resources = resolve_mfa_resources(profile)
    return {
        "language_code": profile.code,
        "language_label": profile.label,
        "dictionary": resources["dictionary"],
        "acoustic": resources["acoustic"],
        "status": "idle",
        "message": "Not prepared yet.",
        "error": None,
    }


def _get_state(profile: LanguageProfile) -> dict[str, object]:
    with RESOURCE_STATE_LOCK:
        state = RESOURCE_STATES.get(profile.code)
        if state is None:
            state = _state_for(profile)
            RESOURCE_STATES[profile.code] = state
        return deepcopy(state)


def _set_state(profile: LanguageProfile, **updates: object) -> dict[str, object]:
    with RESOURCE_STATE_LOCK:
        state = RESOURCE_STATES.get(profile.code)
        if state is None:
            state = _state_for(profile)
            RESOURCE_STATES[profile.code] = state
        state.update(updates)
        return deepcopy(state)


def _resource_lock(profile: LanguageProfile) -> threading.Lock:
    with RESOURCE_STATE_LOCK:
        lock = RESOURCE_LOCKS.get(profile.code)
        if lock is None:
            lock = threading.Lock()
            RESOURCE_LOCKS[profile.code] = lock
        return lock


def resolve_mfa_invocation(extra_args: list[str]) -> list[str]:
    conda_override = os.environ.get("ALIGNMENT_MFA_CONDA_EXE")
    conda_exe = Path(conda_override) if conda_override else MFA_CONDA_EXE
    if conda_exe.exists():
        return [str(conda_exe), "run", "-n", MFA_ENV_NAME, "mfa", *extra_args]

    command = resolve_mfa_command()
    if command is None:
        raise RuntimeError("Montreal Forced Aligner is not available.")

    command_path = Path(command)
    if command_path.suffix.lower() in {".cmd", ".bat"}:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(command_path), *extra_args]
    return [command, *extra_args]


def run_mfa_command(extra_args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONNOUSERSITE"] = "1"
    command = resolve_mfa_invocation(extra_args)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def mfa_resource_status(language_code: str) -> dict[str, object]:
    profile = resolve_language_profile(language_code)
    state = _get_state(profile)
    state["available"] = resolve_mfa_command() is not None or MFA_CONDA_EXE.exists()
    return state


def _log(logger: Callable[[str], None] | None, message: str) -> None:
    if logger is not None:
        logger(message)


def ensure_mfa_language_resources(
    language_code: str,
    logger: Callable[[str], None] | None = None,
    progress_callback: Callable[[str, int], None] | None = None,
) -> dict[str, object]:
    profile = resolve_language_profile(language_code)
    resources = resolve_mfa_resources(profile)
    if resolve_mfa_command() is None and not MFA_CONDA_EXE.exists():
        raise RuntimeError("MFA is not installed on this machine.")

    lock = _resource_lock(profile)
    with lock:
        current = _get_state(profile)
        if current.get("status") == "ready":
            return current

        _set_state(profile, status="running", message="Preparing MFA language files...", error=None)
        if progress_callback is not None:
            progress_callback("Preparing MFA language files", 0)

        plan = [
            ("dictionary", resources["dictionary"], 30),
            ("acoustic", resources["acoustic"], 70),
        ]
        for model_type, model_name, percent in plan:
            _log(logger, f"Checking MFA {model_type} model '{model_name}'...")
            if progress_callback is not None:
                progress_callback(f"Downloading {model_type} model", percent)
            result = run_mfa_command(["model", "download", model_type, model_name])
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "MFA download failed").strip()
                _set_state(profile, status="failed", message="Language files failed to prepare.", error=detail)
                raise RuntimeError(detail)

        ready = _set_state(profile, status="ready", message="Language files are ready.", error=None)
        if progress_callback is not None:
            progress_callback("Language files ready", 100)
        return ready


def prepare_mfa_language_resources_async(language_code: str) -> dict[str, object]:
    profile = resolve_language_profile(language_code)
    state = _get_state(profile)
    if state.get("status") == "ready":
        return state
    if state.get("status") == "running":
        return state

    _set_state(profile, status="running", message="Preparing MFA language files...", error=None)

    def _worker() -> None:
        try:
            ensure_mfa_language_resources(language_code)
        except Exception as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            _set_state(profile, status="failed", message="Language files failed to prepare.", error=detail)

    threading.Thread(target=_worker, daemon=True).start()
    return _get_state(profile)
