"""Execution result categories and per-step outcomes."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

__test__ = False


class ResultCategory(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    AMBIGUOUS = "AMBIGUOUS"
    NEEDS_USER_INPUT = "NEEDS_USER_INPUT"
    BLOCKED_UNSAFE_ACTION = "BLOCKED_UNSAFE_ACTION"
    EXECUTION_ERROR = "EXECUTION_ERROR"


class StepRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    step: int | str
    status: ResultCategory
    message: str
    evidence: str = ""


class TestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: ResultCategory
    message: str
    steps: list[StepRecord] = Field(default_factory=list)


__all__ = ["ResultCategory", "StepRecord", "TestResult"]
