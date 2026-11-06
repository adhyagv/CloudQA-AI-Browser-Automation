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
    secret_values: Mapping[str, str] | None = None
    headed: bool = False
    navigation_timeout_ms: int = 10_000
    action_timeout_ms: int = 5_000
    snapshot_max_elements: int = 60
    snapshot_text_length: int = 120
    stale_retry_attempts: int = 1

    def __repr__(self) -> str:
        key_repr = "'**********'" if self.gemini_api_key else "None"
        secret_names = tuple((self.secret_values or {}).keys())
        return (
            "Settings("
            f"gemini_model={self.gemini_model!r}, "
            f"gemini_api_key={key_repr}, "
            f"max_steps={self.max_steps!r}, "
            f"planner_correction_attempts={self.planner_correction_attempts!r}, "
            f"provider_retries={self.provider_retries!r}, "
            f"timeout_seconds={self.timeout_seconds!r}, "
            f"allowed_secret_names={self.allowed_secret_names!r}, "
            f"secret_names={secret_names!r}, "
            f"headed={self.headed!r}, "
            f"navigation_timeout_ms={self.navigation_timeout_ms!r}, "
            f"action_timeout_ms={self.action_timeout_ms!r}, "
            f"snapshot_max_elements={self.snapshot_max_elements!r}, "
            f"snapshot_text_length={self.snapshot_text_length!r}, "
            f"stale_retry_attempts={self.stale_retry_attempts!r})"
        )

    def get_secret(self, name: str) -> str | None:
        if name not in self.allowed_secret_names:
            return None
        return (self.secret_values or {}).get(name)

    def redact(self, text: str) -> str:
        for value in (self.secret_values or {}).values():
            if value:
                text = text.replace(value, "[REDACTED]")
        if self.gemini_api_key:
            text = text.replace(self.gemini_api_key, "[REDACTED]")
        return text


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
    navigation_timeout_ms = _integer_setting(values, "BROWSER_NAVIGATION_TIMEOUT_MS", 10_000)
    action_timeout_ms = _integer_setting(values, "BROWSER_ACTION_TIMEOUT_MS", 5_000)
    snapshot_max_elements = _integer_setting(values, "SNAPSHOT_MAX_ELEMENTS", 60)
    snapshot_text_length = _integer_setting(values, "SNAPSHOT_TEXT_LENGTH", 120)
    stale_retry_attempts = _integer_setting(values, "BROWSER_STALE_RETRY_ATTEMPTS", 1)
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
    if navigation_timeout_ms <= 0 or action_timeout_ms <= 0:
        raise SettingsError("Browser timeouts must be greater than 0")
    if snapshot_max_elements < 1 or snapshot_text_length < 1:
        raise SettingsError("Snapshot limits must be at least 1")
    if stale_retry_attempts < 0:
        raise SettingsError("BROWSER_STALE_RETRY_ATTEMPTS cannot be negative")
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
    secret_values = {
        name: values[name]
        for name in secret_names
        if values.get(name)
    }

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
        secret_values=secret_values,
        headed=values.get("BROWSER_HEADED", "").strip().lower() in {"1", "true", "yes"},
        navigation_timeout_ms=navigation_timeout_ms,
        action_timeout_ms=action_timeout_ms,
        snapshot_max_elements=snapshot_max_elements,
        snapshot_text_length=snapshot_text_length,
        stale_retry_attempts=stale_retry_attempts,
    )


__all__ = ["Settings", "SettingsError", "load_settings"]
