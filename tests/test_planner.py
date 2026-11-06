"""Offline tests for schema validation, planner retries, and safety screening."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from ai.fake_provider import FakeProvider
from ai.planner import Planner, PlannerBlockedError, PlannerError
from ai.schemas import Plan, parse_plan
from config.settings import Settings, load_settings
from engine.safety import BLOCKED_UNSAFE_ACTION, screen_test_case


def _response(*, steps: list[dict[str, object]] | None = None) -> str:
    return json.dumps(
        {
            "status": "ok",
            "summary": "Verify the welcome heading.",
            "steps": steps
            if steps is not None
            else [{"action": "expect_visible", "element_id": "e12"}],
        }
    )


def _settings(
    *,
    gemini_model: str | None = None,
    gemini_api_key: str | None = None,
    max_steps: int = 10,
    planner_correction_attempts: int = 2,
    provider_retries: int = 3,
    timeout_seconds: float = 30.0,
    allowed_secret_names: tuple[str, ...] = (),
) -> Settings:
    return Settings(
        gemini_model=gemini_model,
        gemini_api_key=gemini_api_key,
        max_steps=max_steps,
        planner_correction_attempts=planner_correction_attempts,
        provider_retries=provider_retries,
        timeout_seconds=timeout_seconds,
        allowed_secret_names=allowed_secret_names,
    )


def test_valid_plan_is_accepted() -> None:
    plan = parse_plan(_response())

    assert plan.status == "ok"
    assert plan.steps[-1].action == "expect_visible"


@pytest.mark.parametrize(
    "steps",
    [
        [{"action": "screenshot", "element_id": "e12"}],
        [{"action": "expect_visible", "element_id": "e12", "selector": "#login"}],
        [
            {"action": "fill", "element_id": "e12", "value": "document.querySelector('#x')"},
            {"action": "expect_visible", "element_id": "e12"},
        ],
        [
            {"action": "fill", "element_id": "#login", "value": "test@example.com"},
            {"action": "expect_visible", "element_id": "e12"},
        ],
        [
            {"action": "fill", "element_id": "e12", "value": "console.log('unsafe')"},
            {"action": "expect_visible", "element_id": "e12"},
        ],
        [
            {
                "action": "select_option",
                "element_id": "e12",
                "value": "{{secret:LOGIN_PASSWORD}}",
            },
            {"action": "expect_visible", "element_id": "e12"},
        ],
    ],
)
def test_invalid_action_or_selector_data_is_rejected(
    steps: list[dict[str, object]],
) -> None:
    with pytest.raises(ValidationError):
        parse_plan(_response(steps=steps))


def test_unknown_secret_name_is_rejected() -> None:
    response = _response(
        steps=[
            {"action": "fill", "element_id": "e12", "value": "{{secret:ADMIN_PASSWORD}}"},
            {"action": "expect_visible", "element_id": "e12"},
        ]
    )

    with pytest.raises(ValidationError, match="secret name"):
        parse_plan(response, allowed_secret_names=("LOGIN_PASSWORD",))


def test_plan_without_final_assertion_is_rejected() -> None:
    response = _response(
        steps=[
            {"action": "click", "element_id": "e12"},
            {"action": "fill", "element_id": "e13", "value": "hello"},
        ]
    )

    with pytest.raises(ValidationError, match="expect_"):
        parse_plan(response)


def test_configured_maximum_step_count_is_enforced() -> None:
    response = _response(
        steps=[
            {"action": "click", "element_id": "e12"},
            {"action": "expect_visible", "element_id": "e13"},
        ]
    )

    with pytest.raises(ValidationError, match="maximum of 1"):
        parse_plan(response, max_steps=1)


def test_invalid_json_then_corrected_json_succeeds() -> None:
    provider = FakeProvider(["not json", _response()])
    planner = Planner(provider, _settings(planner_correction_attempts=1))

    plan = planner.plan(test_case="Check the welcome heading.", page_snapshot="e12: Welcome")

    assert plan.status == "ok"
    assert len(provider.prompts) == 2
    assert "Invalid JSON" in provider.prompts[1]


def test_retry_limit_returns_planner_error() -> None:
    provider = FakeProvider(["not json", "still not json"])
    planner = Planner(provider, _settings(planner_correction_attempts=1))

    with pytest.raises(PlannerError, match="remained invalid") as error:
        planner.plan(test_case="Check the heading.", page_snapshot="e12: heading")

    assert error.value.code == "PLANNER_ERROR"
    assert len(provider.prompts) == 2


def test_unsafe_test_case_is_blocked_before_provider_call() -> None:
    provider = FakeProvider([])
    planner = Planner(provider, _settings())

    with pytest.raises(PlannerBlockedError) as error:
        planner.plan(test_case="Purchase the item on the page.", page_snapshot="e12: Buy")

    assert error.value.code == BLOCKED_UNSAFE_ACTION
    assert provider.prompts == []
    assert screen_test_case("Bypass the MFA security check.").code == BLOCKED_UNSAFE_ACTION


def test_verifying_unsafe_control_label_is_allowed() -> None:
    provider = FakeProvider(
        [
            json.dumps(
                {
                    "status": "ok",
                    "summary": "Check the page.",
                    "steps": [
                        {"action": "expect_text", "element_id": "e12", "text": "Cancel order"},
                    ],
                }
            )
        ]
    )
    planner = Planner(provider, _settings())

    plan = planner.plan(test_case="Check the order status.", page_snapshot="e12: Cancel order")

    assert plan.status == "ok"


def test_secret_value_never_enters_provider_prompt() -> None:
    secret_value = "local-secret-value-not-for-prompts"
    provider = FakeProvider(
        [
            _response(
                steps=[
                    {"action": "fill", "element_id": "e12", "value": "{{secret:LOGIN_PASSWORD}}"},
                    {"action": "expect_visible", "element_id": "e13"},
                ]
            )
        ]
    )
    planner = Planner(
        provider,
        _settings(
            gemini_api_key=secret_value,
            allowed_secret_names=("LOGIN_PASSWORD",),
        ),
    )

    planner.plan(
        test_case=f"Enter the saved login password. Do not expose {secret_value}.",
        page_snapshot="e12: Password field; e13: Continue button",
    )

    assert secret_value not in "\n".join(provider.prompts)
    assert "{{secret:LOGIN_PASSWORD}}" not in provider.prompts[0]
    assert "LOGIN_PASSWORD" in provider.prompts[0]


def test_settings_repr_masks_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    api_key = "private-key-that-must-not-be-printed"
    monkeypatch.setenv("GEMINI_API_KEY", api_key)
    monkeypatch.setenv("GEMINI_MODEL", "configured-model-id")

    settings = load_settings()

    assert api_key not in repr(settings)
    assert "**********" in repr(settings)
    assert settings.gemini_model == "configured-model-id"


def test_fake_provider_uses_plan_schema() -> None:
    provider = FakeProvider([_response()])
    planner = Planner(provider, _settings())

    planner.plan(test_case="Check the heading.", page_snapshot="e12: heading")

    assert provider.response_schemas == [Plan]
