"""Public planner schema and JSON validation helpers."""

from __future__ import annotations

import json
from typing import Any, TypeAlias

from pydantic import ValidationError

from models.actions import (
    Action,
    CheckAction,
    ClickAction,
    ExpectTextAction,
    ExpectURLAction,
    ExpectVisibleAction,
    FillAction,
    NavigateAction,
    PressAction,
    SelectOptionAction,
    UncheckAction,
    WaitForAction,
)
from models.plan import TestPlan

Plan: TypeAlias = TestPlan


def parse_plan(
    response: str,
    *,
    max_steps: int = 10,
    allowed_secret_names: tuple[str, ...] | list[str] = (),
) -> TestPlan:
    """Parse provider JSON and validate it with runtime planner policy."""
    data: Any = json.loads(response)
    if not isinstance(data, dict):
        raise ValueError("planner response must be a JSON object")
    return TestPlan.model_validate(
        data,
        context={
            "max_steps": max_steps,
            "allowed_secret_names": tuple(allowed_secret_names),
        },
    )


__all__ = [
    "Action",
    "CheckAction",
    "ClickAction",
    "ExpectTextAction",
    "ExpectURLAction",
    "ExpectVisibleAction",
    "FillAction",
    "NavigateAction",
    "Plan",
    "PressAction",
    "SelectOptionAction",
    "TestPlan",
    "UncheckAction",
    "WaitForAction",
    "parse_plan",
]
