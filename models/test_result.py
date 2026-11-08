"""Execution result categories and per-step outcomes."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__test__ = False


class ResultCategory(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    AMBIGUOUS = "AMBIGUOUS"
    NEEDS_USER_INPUT = "NEEDS_USER_INPUT"
    BLOCKED_UNSAFE_ACTION = "BLOCKED_UNSAFE_ACTION"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    NOT_EXECUTED = "NOT_EXECUTED"


class StepRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    step: int | str
    status: ResultCategory
    message: str
    evidence: str = ""
    action: str | None = None


class TestResult(BaseModel):
    __test__ = False
    model_config = ConfigDict(extra="forbid", strict=True)

    status: ResultCategory
    message: str
    steps: list[StepRecord] = Field(default_factory=list)
    website: str = ""
    test_case: str = ""
    ai_interpretation: str = ""
    browser_execution: str = ""
    plan_steps: list[dict[str, Any]] = Field(default_factory=list)
    ai_calls: int = 0
    execution_time_seconds: float = 0.0
    screenshot_path: str | None = None


__all__ = ["ResultCategory", "StepRecord", "TestResult"]
