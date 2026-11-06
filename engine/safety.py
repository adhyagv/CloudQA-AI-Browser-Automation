"""Initial conservative keyword screening for planner requests and steps."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from models.plan import TestPlan

BLOCKED_UNSAFE_ACTION = "BLOCKED_UNSAFE_ACTION"

_IRREVERSIBLE_OR_FINANCIAL = re.compile(
    r"\b(?:pay(?:ing|ment)?s?|purchase(?:s|d|ing)?|buy|buys|bought|"
    r"book(?:s|ed|ing)?|reserv(?:e|es|ed|ation|ations|ing)|"
    r"delete(?:s|d|ing)?|deletion|cancel(?:s|led|ling|lation)?|"
    r"refund(?:s|ed|ing)?|transfer(?: funds| money)?|send money|"
    r"withdraw(?:s|al|n|ing)?|charge(?:s|d|ing)?)\b",
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
    """Block irreversible/financial intent and security-check bypass requests."""
    if _IRREVERSIBLE_OR_FINANCIAL.search(text):
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


def screen_test_case(test_case: str) -> SafetyResult:
    """Screen user-authored test intent before sending it to a provider."""
    return screen_text(test_case)


def screen_plan(plan: TestPlan) -> SafetyResult:
    """Screen every planned step before returning an executable plan."""
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
    "screen_test_case",
    "screen_text",
]
