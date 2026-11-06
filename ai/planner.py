"""Request construction, validation, and bounded repair for AI test plans."""

from __future__ import annotations

import json
from json import JSONDecodeError

from pydantic import ValidationError

from ai.prompts import build_correction_prompt, build_request_prompt
from ai.provider import PlannerProvider
from ai.schemas import Plan, parse_plan
from config.settings import Settings, load_settings
from engine.safety import BLOCKED_UNSAFE_ACTION, screen_plan, screen_test_case
from models.plan import TestPlan


class PlannerError(RuntimeError):
    """Raised when a provider response cannot be converted into a valid plan."""

    code = "PLANNER_ERROR"


class PlannerBlockedError(RuntimeError):
    """Raised when a test case or generated plan violates initial safety policy."""

    code = BLOCKED_UNSAFE_ACTION


def _safe_validation_error(error: ValidationError | JSONDecodeError | ValueError) -> str:
    if isinstance(error, ValidationError):
        errors = error.errors(include_input=False, include_context=False)
        return json.dumps(
            [
                {
                    "location": [str(part) for part in item["loc"]],
                    "message": item["msg"],
                    "type": item["type"],
                }
                for item in errors
            ],
            ensure_ascii=False,
        )
    if isinstance(error, JSONDecodeError):
        return (
            f"Invalid JSON at line {error.lineno}, column {error.colno}: "
            f"{error.msg}"
        )
    return str(error)


class Planner:
    """Build and validate plans without resolving secrets or performing browser work."""

    def __init__(
        self,
        provider: PlannerProvider,
        settings: Settings | None = None,
    ) -> None:
        self.provider = provider
        self.settings = settings or load_settings()

    def plan(
        self,
        *,
        test_case: str,
        page_snapshot: str,
        allowed_secret_names: tuple[str, ...] | list[str] | None = None,
    ) -> TestPlan:
        safety = screen_test_case(test_case)
        if safety.blocked:
            raise PlannerBlockedError(f"{safety.code}: {safety.reason}")

        names = tuple(
            self.settings.allowed_secret_names
            if allowed_secret_names is None
            else allowed_secret_names
        )
        prompt_test_case = self._redact_gemini_key(test_case)
        prompt_snapshot = self._redact_gemini_key(page_snapshot)
        base_prompt = build_request_prompt(
            test_case=prompt_test_case,
            page_snapshot=prompt_snapshot,
            allowed_secret_names=names,
            max_steps=self.settings.max_steps,
        )
        prompt = base_prompt

        for correction in range(self.settings.planner_correction_attempts + 1):
            response = self.provider.generate(prompt, response_schema=Plan)
            try:
                plan = parse_plan(
                    response,
                    max_steps=self.settings.max_steps,
                    allowed_secret_names=names,
                )
            except (ValidationError, JSONDecodeError, ValueError) as exc:
                if correction >= self.settings.planner_correction_attempts:
                    raise PlannerError(
                        "Planner response remained invalid after "
                        f"{correction} correction attempt(s)."
                    ) from exc
                prompt = build_correction_prompt(
                    base_prompt,
                    _safe_validation_error(exc),
                )
                continue

            if plan.status == "ok":
                plan_safety = screen_plan(plan)
                if plan_safety.blocked:
                    raise PlannerBlockedError(
                        f"{plan_safety.code}: {plan_safety.reason}"
                    )
            return plan

        raise PlannerError("Planner did not produce a response.")

    def _redact_gemini_key(self, text: str) -> str:
        key = self.settings.gemini_api_key
        if key:
            return text.replace(key, "[REDACTED]")
        return text


__all__ = ["Planner", "PlannerBlockedError", "PlannerError"]
