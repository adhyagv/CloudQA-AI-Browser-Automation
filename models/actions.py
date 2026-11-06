"""Strict, snapshot-ID-based actions accepted by the AI planner."""

from __future__ import annotations

import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


_ELEMENT_ID_PATTERN = re.compile(r"^e\d+$")
_SECRET_REFERENCE_PATTERN = re.compile(r"^\{\{secret:([A-Z][A-Z0-9_]*)\}\}$")
_SELECTOR_OR_SCRIPT_PATTERNS = (
    re.compile(r"(?is)<\s*script\b"),
    re.compile(r"(?i)\b(?:javascript|data):"),
    re.compile(r"(?i)\b(?:document|window)\s*(?:\.|\[)"),
    re.compile(r"(?i)\b(?:eval|querySelector(?:All)?)\s*\("),
    re.compile(r"(?i)\b(?:alert|confirm|fetch|prompt|setInterval|setTimeout)\s*\("),
    re.compile(r"(?i)\bconsole\.\w+\s*\("),
    re.compile(r"(?m)^\s*(?:xpath\s*=|css\s*=|//|>>>|#[\w-]+|\.[\w-]+|\[[^\]]+\])"),
    re.compile(r"(?i)^[a-z][\w-]*(?:[#.][\w-]+|\[[^\]]+\])"),
    re.compile(r"=>|```|^\s*(?:async\s+)?function\s*\(|^\s*(?:const|let|var)\s+\w+\s*=|;"),
)


def validate_untrusted_text(
    value: str,
    *,
    allow_secret_reference: bool = False,
) -> str:
    if any(pattern.search(value) for pattern in _SELECTOR_OR_SCRIPT_PATTERNS):
        raise ValueError("selector or script-like text is not allowed")
    if not allow_secret_reference and "{{secret:" in value:
        raise ValueError("secret references are only allowed in fill values")
    return value


class StrictAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NavigateAction(StrictAction):
    action: Literal["navigate"]
    url: str

    @field_validator("url")
    @classmethod
    def validate_url_text(cls, value: str) -> str:
        return validate_untrusted_text(value)


class ClickAction(StrictAction):
    action: Literal["click"]
    element_id: str

    @field_validator("element_id")
    @classmethod
    def validate_element_id(cls, value: str) -> str:
        if not _ELEMENT_ID_PATTERN.fullmatch(value):
            raise ValueError("element_id must be a snapshot element ID such as 'e12'")
        return value


class FillAction(StrictAction):
    action: Literal["fill"]
    element_id: str
    value: str

    @field_validator("element_id")
    @classmethod
    def validate_element_id(cls, value: str) -> str:
        if not _ELEMENT_ID_PATTERN.fullmatch(value):
            raise ValueError("element_id must be a snapshot element ID such as 'e12'")
        return value

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str, info: ValidationInfo) -> str:
        validate_untrusted_text(value, allow_secret_reference=True)
        if value.startswith("{{secret:"):
            match = _SECRET_REFERENCE_PATTERN.fullmatch(value)
            if match is None:
                raise ValueError("secret references must use {{secret:NAME}} syntax")
            allowed_names = (info.context or {}).get("allowed_secret_names")
            if allowed_names is not None and match.group(1) not in set(allowed_names):
                raise ValueError("secret name is not in the allowed secret names list")
        return value


class SelectOptionAction(StrictAction):
    action: Literal["select_option"]
    element_id: str
    value: str

    @field_validator("element_id")
    @classmethod
    def validate_element_id(cls, value: str) -> str:
        if not _ELEMENT_ID_PATTERN.fullmatch(value):
            raise ValueError("element_id must be a snapshot element ID such as 'e12'")
        return value

    @field_validator("value")
    @classmethod
    def validate_option_value(cls, value: str) -> str:
        return validate_untrusted_text(value)


class CheckAction(StrictAction):
    action: Literal["check"]
    element_id: str

    @field_validator("element_id")
    @classmethod
    def validate_element_id(cls, value: str) -> str:
        if not _ELEMENT_ID_PATTERN.fullmatch(value):
            raise ValueError("element_id must be a snapshot element ID such as 'e12'")
        return value


class UncheckAction(StrictAction):
    action: Literal["uncheck"]
    element_id: str

    @field_validator("element_id")
    @classmethod
    def validate_element_id(cls, value: str) -> str:
        if not _ELEMENT_ID_PATTERN.fullmatch(value):
            raise ValueError("element_id must be a snapshot element ID such as 'e12'")
        return value


class PressAction(StrictAction):
    action: Literal["press"]
    element_id: str
    key: str

    @field_validator("element_id")
    @classmethod
    def validate_element_id(cls, value: str) -> str:
        if not _ELEMENT_ID_PATTERN.fullmatch(value):
            raise ValueError("element_id must be a snapshot element ID such as 'e12'")
        return value

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        return validate_untrusted_text(value)


class WaitForAction(StrictAction):
    action: Literal["wait_for"]
    element_id: str
    state: Literal["visible", "hidden", "attached", "detached"] = "visible"
    timeout_ms: int | None = Field(default=None, gt=0)

    @field_validator("element_id")
    @classmethod
    def validate_element_id(cls, value: str) -> str:
        if not _ELEMENT_ID_PATTERN.fullmatch(value):
            raise ValueError("element_id must be a snapshot element ID such as 'e12'")
        return value


class ExpectVisibleAction(StrictAction):
    action: Literal["expect_visible"]
    element_id: str

    @field_validator("element_id")
    @classmethod
    def validate_element_id(cls, value: str) -> str:
        if not _ELEMENT_ID_PATTERN.fullmatch(value):
            raise ValueError("element_id must be a snapshot element ID such as 'e12'")
        return value


class ExpectTextAction(StrictAction):
    action: Literal["expect_text"]
    element_id: str
    text: str

    @field_validator("element_id")
    @classmethod
    def validate_element_id(cls, value: str) -> str:
        if not _ELEMENT_ID_PATTERN.fullmatch(value):
            raise ValueError("element_id must be a snapshot element ID such as 'e12'")
        return value

    @field_validator("text")
    @classmethod
    def validate_expected_text(cls, value: str) -> str:
        return validate_untrusted_text(value)


class ExpectURLAction(StrictAction):
    action: Literal["expect_url"]
    url: str

    @field_validator("url")
    @classmethod
    def validate_url_text(cls, value: str) -> str:
        return validate_untrusted_text(value)


Action = Annotated[
    Union[
        NavigateAction,
        ClickAction,
        FillAction,
        SelectOptionAction,
        CheckAction,
        UncheckAction,
        PressAction,
        WaitForAction,
        ExpectVisibleAction,
        ExpectTextAction,
        ExpectURLAction,
    ],
    Field(discriminator="action"),
]

ASSERTION_ACTIONS = frozenset({"expect_visible", "expect_text", "expect_url"})

__all__ = [
    "ASSERTION_ACTIONS",
    "Action",
    "CheckAction",
    "ClickAction",
    "ExpectTextAction",
    "ExpectURLAction",
    "ExpectVisibleAction",
    "FillAction",
    "NavigateAction",
    "PressAction",
    "SelectOptionAction",
    "UncheckAction",
    "WaitForAction",
    "validate_untrusted_text",
]
