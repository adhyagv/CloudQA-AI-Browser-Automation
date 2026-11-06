"""Initial conservative keyword screening for planner requests and steps."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from models.plan import TestPlan

BLOCKED_UNSAFE_ACTION = "BLOCKED_UNSAFE_ACTION"

_UNSAFE_OPERATION = r"(?:pay(?:ment)?|purchase|buy|book|reserve|delete|cancel|refund|transfer|withdraw|charge)"
_UNSAFE_INTENT = re.compile(
    rf"^\s*(?:please\s+)?(?:{_UNSAFE_OPERATION})\b"
    r"|"
    rf"\b(?:click(?:ing)?|press(?:ing)?|tap(?:ping)?|select(?:ing)?|submit(?:ting)?|"
    rf"confirm(?:ing)?|proceed(?:ing)?|complete(?:ing)?|execute|perform(?:ing)?|make|place)"
    rf"\b.{{0,80}}\b(?:{_UNSAFE_OPERATION}|order)\b",
    re.IGNORECASE,
)
_UNSAFE_CONTROL = re.compile(
    r"\b(?:pay(?:ment)?(?: now)?|purchase|buy now|book(?: now|ing)?|reserve|"
    r"confirm[- ]order|delete|cancel(?:[- ]booking|[- ]order| booking| order)?|"
    r"refund|transfer money)\b",
    re.IGNORECASE,
)
_SECURITY_BYPASS = re.compile(
    r"\b(?:bypass|circumvent|skip|disable|evade|solve|break)\b"
    r".{0,80}\b(?:captcha|mfa|2fa|two[- ]factor|multi[- ]factor|"
    r"security check|verification challenge)\b|"
    r"\b(?:captcha|mfa|2fa|two[- ]factor|multi[- ]factor|"
    r"security check|verification challenge)\b"
    r".{0,80}\b(?:bypass|circumvent|skip|disable|evade|solve|break)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SafetyResult:
    code: str | None
    reason: str
    approval_required: bool = True

    @property
    def blocked(self) -> bool:
        return self.code == BLOCKED_UNSAFE_ACTION


def screen_text(text: str) -> SafetyResult:
    """Block performing unsafe operations but allow verification of related UI."""
    if _UNSAFE_INTENT.search(text):
        return SafetyResult(
            code=BLOCKED_UNSAFE_ACTION,
            reason="Financial or irreversible actions are not allowed in generated plans.",
        )
    if _SECURITY_BYPASS.search(text):
        return SafetyResult(
            code=BLOCKED_UNSAFE_ACTION,
            reason="Bypassing CAPTCHA, MFA, or a security check is not allowed.",
        )
    return SafetyResult(code=None, reason="No blocked intent detected.")


def screen_action(action: object, target_text: str = "") -> SafetyResult:
    """Block unsafe controls only when the step will activate them."""
    action_name = getattr(action, "action", "")
    if action_name in {"click", "press"} and _UNSAFE_CONTROL.search(target_text):
        return SafetyResult(
            code=BLOCKED_UNSAFE_ACTION,
            reason="Clicking or submitting an irreversible/financial control is blocked.",
        )
    return SafetyResult(code=None, reason="No blocked action detected.")


def screen_test_case(test_case: str) -> SafetyResult:
    """Screen user-authored test intent before sending it to a provider."""
    return screen_text(test_case)


def screen_plan(plan: TestPlan) -> SafetyResult:
    """Screen plan wording; control actions are checked against snapshot labels."""
    for step in plan.steps:
        result = screen_text(
            json.dumps(step.model_dump(mode="json"), ensure_ascii=False)
        )
        if result.blocked:
            return result
    return SafetyResult(
        code=None,
        reason="Plan passed initial screening; approval is still required before execution.",
        approval_required=True,
    )


__all__ = [
    "BLOCKED_UNSAFE_ACTION",
    "SafetyResult",
    "screen_plan",
    "screen_action",
    "screen_test_case",
    "screen_text",
]
