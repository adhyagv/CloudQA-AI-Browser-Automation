"""Environment-backed settings with safe representations for local secrets."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

_SECRET_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


class SettingsError(ValueError):
    """Raised when an environment setting is invalid."""


@dataclass(frozen=True, repr=False)
class Settings:
    gemini_model: str | None
    gemini_api_key: str | None
    max_steps: int = 10
    planner_correction_attempts: int = 2
    provider_retries: int = 3
    timeout_seconds: float = 30.0
    allowed_secret_names: tuple[str, ...] = ()

    def __repr__(self) -> str:
        key_repr = "'**********'" if self.gemini_api_key else "None"
        return (
            "Settings("
            f"gemini_model={self.gemini_model!r}, "
            f"gemini_api_key={key_repr}, "
            f"max_steps={self.max_steps!r}, "
            f"planner_correction_attempts={self.planner_correction_attempts!r}, "
            f"provider_retries={self.provider_retries!r}, "
            f"timeout_seconds={self.timeout_seconds!r}, "
            f"allowed_secret_names={self.allowed_secret_names!r})"
        )


def _integer_setting(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer") from exc


def load_settings(
    *,
    env_file: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Load settings from .env/environment without logging secret values."""
    if environ is None:
        load_dotenv(dotenv_path=env_file, override=False)
        values: Mapping[str, str] = os.environ
    else:
        values = environ

    max_steps = _integer_setting(values, "PLANNER_MAX_STEPS", 10)
    corrections = _integer_setting(values, "PLANNER_CORRECTION_ATTEMPTS", 2)
    provider_retries = _integer_setting(values, "GEMINI_RETRIES", 3)
    try:
        timeout = float(values.get("GEMINI_TIMEOUT_SECONDS", "30"))
    except ValueError as exc:
        raise SettingsError("GEMINI_TIMEOUT_SECONDS must be a number") from exc

    if max_steps < 1:
        raise SettingsError("PLANNER_MAX_STEPS must be at least 1")
    if corrections < 0:
        raise SettingsError("PLANNER_CORRECTION_ATTEMPTS cannot be negative")
    if provider_retries < 0:
        raise SettingsError("GEMINI_RETRIES cannot be negative")
    if not math.isfinite(timeout) or timeout <= 0:
        raise SettingsError("GEMINI_TIMEOUT_SECONDS must be greater than 0")

    secret_names = tuple(
        name.strip()
        for name in values.get("ALLOWED_SECRET_NAMES", "").split(",")
        if name.strip()
    )
    invalid_names = [name for name in secret_names if not _SECRET_NAME_PATTERN.fullmatch(name)]
    if invalid_names:
        raise SettingsError("ALLOWED_SECRET_NAMES must contain uppercase names only")

    model = values.get("GEMINI_MODEL", "").strip() or None
    if model == "your_configured_model":
        model = None

    return Settings(
        gemini_model=model,
        gemini_api_key=values.get("GEMINI_API_KEY") or None,
        max_steps=max_steps,
        planner_correction_attempts=corrections,
        provider_retries=provider_retries,
        timeout_seconds=timeout,
        allowed_secret_names=secret_names,
    )


__all__ = ["Settings", "SettingsError", "load_settings"]
