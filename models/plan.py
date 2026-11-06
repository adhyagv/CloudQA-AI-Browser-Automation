"""Validated test-plan model returned by planner providers."""

from __future__ import annotations

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from models.actions import ASSERTION_ACTIONS, Action, validate_untrusted_text


class TestPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["ok", "needs_clarification", "unsafe"]
    summary: str = Field(min_length=1)
    steps: list[Action]
    message: str | None = None

    @field_validator("summary", "message")
    @classmethod
    def validate_plan_text(cls, value: str | None) -> str | None:
        if value is not None:
            return validate_untrusted_text(value)
        return value

    @model_validator(mode="after")
    def validate_plan_policy(self, info: ValidationInfo) -> TestPlan:
        context = info.context or {}
        max_steps = context.get("max_steps", 10)
        if len(self.steps) > max_steps:
            raise ValueError(f"plan exceeds configured maximum of {max_steps} steps")

        if self.status == "ok":
            if not self.steps or self.steps[-1].action not in ASSERTION_ACTIONS:
                raise ValueError("an ok plan must end with an expect_* assertion")
            if self.message is not None:
                raise ValueError("message is only allowed for non-ok plans")
        else:
            if not self.message or not self.message.strip():
                raise ValueError("message is required for non-ok plans")
            if self.steps:
                raise ValueError("non-ok plans must not contain executable steps")

        return self


__all__ = ["TestPlan"]
