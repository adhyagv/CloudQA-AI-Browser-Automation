"""Offline end-to-end TestRunner tests using local fixtures and scripted providers."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path

from ai.provider import PlannerProvider
from config.settings import Settings
from engine.test_runner import TestRunner
from models.test_result import ResultCategory
from reporting.report_generator import ReportGenerator
from reporting.result_writer import ResultWriter

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PASSWORD = "runner-secret-should-not-be-reported"


def _settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "gemini_model": None,
        "gemini_api_key": None,
        "max_steps": 10,
        "planner_correction_attempts": 2,
        "provider_retries": 3,
        "timeout_seconds": 30.0,
        "allowed_secret_names": ("LOGIN_PASSWORD",),
        "secret_values": {"LOGIN_PASSWORD": PASSWORD},
        "max_planner_calls": 4,
    }
    values.update(changes)
    return Settings(
        gemini_model=values["gemini_model"],  # type: ignore[arg-type]
        gemini_api_key=values["gemini_api_key"],  # type: ignore[arg-type]
        max_steps=values["max_steps"],  # type: ignore[arg-type]
        planner_correction_attempts=values["planner_correction_attempts"],  # type: ignore[arg-type]
        provider_retries=values["provider_retries"],  # type: ignore[arg-type]
        timeout_seconds=values["timeout_seconds"],  # type: ignore[arg-type]
        allowed_secret_names=values["allowed_secret_names"],  # type: ignore[arg-type]
        secret_values=values["secret_values"],  # type: ignore[arg-type]
        max_planner_calls=values["max_planner_calls"],  # type: ignore[arg-type]
    )


def _request(prompt: str) -> dict[str, object]:
    request_text = prompt.split("Request data (JSON):\n", 1)[1]
    request, _ = json.JSONDecoder().raw_decode(request_text)
    request["page_snapshot"] = json.loads(request["page_snapshot"])
    return request


def _ok(*steps: dict[str, object]) -> str:
    return json.dumps(
        {
            "status": "ok",
            "summary": "Verify the requested page state.",
            "steps": list(steps),
        }
    )


class ScriptedProvider(PlannerProvider):
    def __init__(
        self,
        responses: Iterable[str | Callable[[str], str]],
    ) -> None:
        self.responses = iter(responses)
        self.prompts: list[str] = []

    def generate(self, prompt: str, *, response_schema: type) -> str:
        self.prompts.append(prompt)
        response = next(self.responses)
        return response(prompt) if callable(response) else response


def _first_step(action: str, element: dict[str, object]) -> dict[str, object]:
    if action == "expect_visible":
        return {"action": action, "element_id": element["id"]}
    return {"action": action, "element_id": element["id"]}


def _run(
    provider: PlannerProvider,
    tmp_path: Path,
    *,
    fixture: str = "sample_login.html",
    test_case: str = "Verify the sign in button is visible.",
    settings: Settings | None = None,
):
    runner = TestRunner(
        provider,
        settings or _settings(),
        reports_dir=tmp_path / "reports",
    )
    return runner.run(
        url=(FIXTURES / fixture).resolve().as_uri(),
        test_case=test_case,
    )


def test_full_run_pass_requires_real_browser_assertion(tmp_path: Path) -> None:
    def response(prompt: str) -> str:
        request = _request(prompt)
        element = next(
            item
            for item in request["page_snapshot"]["elements"]
            if item["role"] == "button" and item["name"] == "Sign in"
        )
        return _ok(_first_step("expect_visible", element))

    result = _run(ScriptedProvider([response]), tmp_path)

    assert result.status == ResultCategory.PASS
    assert result.ai_calls == 1
    assert result.steps[0].step == 1
    assert result.steps[0].status == ResultCategory.PASS
    assert result.execution_time_seconds > 0


def test_full_run_wrong_expectation_fails_with_evidence(tmp_path: Path) -> None:
    def response(prompt: str) -> str:
        request = _request(prompt)
        button = next(
            item for item in request["page_snapshot"]["elements"] if item["name"] == "Sign in"
        )
        return _ok(
            {
                "action": "expect_text",
                "element_id": button["id"],
                "text": "Impossible expected text",
            }
        )

    result = _run(ScriptedProvider([response]), tmp_path)

    assert result.status == ResultCategory.FAIL
    assert result.steps[0].status == ResultCategory.FAIL
    assert "observed text: Sign in" in result.steps[0].evidence
    assert result.screenshot_path is not None
    assert Path(result.screenshot_path).is_file()


def test_full_run_ambiguous_target_returns_ambiguous(tmp_path: Path) -> None:
    def response(prompt: str) -> str:
        request = _request(prompt)
        submit = next(
            item for item in request["page_snapshot"]["elements"] if item["name"] == "Submit"
        )
        visible = next(
            item for item in request["page_snapshot"]["elements"] if item["role"] == "heading"
        )
        return _ok(
            {"action": "click", "element_id": submit["id"]},
            _first_step("expect_visible", visible),
        )

    result = _run(
        ScriptedProvider([response]),
        tmp_path,
        fixture="ambiguous.html",
        test_case="Click Submit and verify the heading.",
    )

    assert result.status == ResultCategory.AMBIGUOUS
    assert result.ai_calls == 1
    assert result.screenshot_path is not None
    assert Path(result.screenshot_path).is_file()


def test_invalid_credentials_error_is_verified_by_runner(tmp_path: Path) -> None:
    def first_plan(prompt: str) -> str:
        request = _request(prompt)
        elements = request["page_snapshot"]["elements"]
        username = next(item for item in elements if item["label"] == "Username")
        password = next(item for item in elements if item["label"] == "Password")
        button = next(item for item in elements if item["name"] == "Sign in")
        heading = next(item for item in elements if item["role"] == "heading")
        return _ok(
            {"action": "fill", "element_id": username["id"], "value": "tester"},
            {"action": "fill", "element_id": password["id"], "value": "wrong-password"},
            {"action": "click", "element_id": button["id"]},
            _first_step("expect_visible", heading),
        )

    def second_plan(prompt: str) -> str:
        request = _request(prompt)
        status = next(
            item
            for item in request["page_snapshot"]["elements"]
            if item["role"] == "status"
        )
        return _ok(
            {
                "action": "expect_text",
                "element_id": status["id"],
                "text": "Invalid username or password",
            }
        )

    result = _run(
        ScriptedProvider([first_plan, second_plan]),
        tmp_path,
        test_case="Log in with an invalid password and verify the error message.",
    )

    assert result.status == ResultCategory.PASS
    assert result.ai_calls == 2
    assert any("observed text: Invalid username or password" in step.evidence for step in result.steps)


def test_correct_login_reinspects_and_verifies_dashboard(tmp_path: Path) -> None:
    def first_plan(prompt: str) -> str:
        request = _request(prompt)
        elements = request["page_snapshot"]["elements"]
        username = next(item for item in elements if item["label"] == "Username")
        password = next(item for item in elements if item["label"] == "Password")
        button = next(item for item in elements if item["name"] == "Sign in")
        heading = next(item for item in elements if item["role"] == "heading")
        return _ok(
            {"action": "fill", "element_id": username["id"], "value": "tester"},
            {"action": "fill", "element_id": password["id"], "value": "correct-horse"},
            {"action": "click", "element_id": button["id"]},
            _first_step("expect_visible", heading),
        )

    def second_plan(prompt: str) -> str:
        request = _request(prompt)
        heading = next(
            item
            for item in request["page_snapshot"]["elements"]
            if item["role"] == "heading" and item["name"] == "Dashboard"
        )
        return _ok(_first_step("expect_visible", heading))

    provider = ScriptedProvider([first_plan, second_plan])
    result = _run(
        provider,
        tmp_path,
        test_case="Log in with the provided test account and verify the dashboard is displayed.",
    )

    assert result.status == ResultCategory.PASS
    assert result.ai_calls == 2
    assert "Dashboard" in provider.prompts[1]


def _login_then_stale_assertion(prompt: str) -> str:
    request = _request(prompt)
    elements = request["page_snapshot"]["elements"]
    username = next(item for item in elements if item["label"] == "Username")
    password = next(item for item in elements if item["label"] == "Password")
    login_button = next(item for item in elements if item["name"] == "Sign in")
    old_heading = next(item for item in elements if item["name"] == "Sign in")
    return _ok(
        {"action": "fill", "element_id": username["id"], "value": "tester"},
        {"action": "fill", "element_id": password["id"], "value": "correct-horse"},
        {"action": "click", "element_id": login_button["id"]},
        _first_step("expect_visible", old_heading),
    )


def test_login_requires_exact_success_message_and_logout_assertions(
    tmp_path: Path,
) -> None:
    success_message = "Welcome, tester. You have successfully logged in."

    def post_login_plan(prompt: str) -> str:
        request = _request(prompt)
        elements = request["page_snapshot"]["elements"]
        status = next(item for item in elements if item["role"] == "status")
        logout = next(item for item in elements if item["role"] == "button" and item["name"] == "Logout")
        assert success_message in status["text"]
        return _ok(
            {
                "action": "expect_text",
                "element_id": status["id"],
                "text": success_message,
            },
            _first_step("expect_visible", logout),
        )

    test_case = (
        'Log in with the provided test account, verify the success message '
        f'"{success_message}", and verify that the Logout button is visible.'
    )
    provider = ScriptedProvider([_login_then_stale_assertion, post_login_plan])
    result = _run(provider, tmp_path, test_case=test_case)
    files, summary = ResultWriter(
        reports_dir=tmp_path / "reports",
        settings=_settings(),
    ).write(result)

    assert result.status == ResultCategory.PASS
    assert result.ai_calls == 2
    assert "Logout" in provider.prompts[1]
    assert success_message in provider.prompts[1]
    assert [record.status for record in result.steps if record.action == "expect_visible"] == [
        ResultCategory.NOT_EXECUTED,
        ResultCategory.PASS,
    ]
    assert any(
        record.action == "expect_visible" and record.status == ResultCategory.PASS
        and "Logout" in record.evidence
        for record in result.steps
    )
    assert all(
        entry["outcome"] != ResultCategory.NOT_EXECUTED.value
        for entry in result.plan_steps
        if entry.get("action") in {"fill", "click"}
    )
    assert len(result.plan_steps) == len(result.steps)
    assert {
        entry["run_step"] for entry in result.plan_steps
    } == {record.step for record in result.steps}
    assert "NOT_EXECUTED" in summary
    report_html = files.html_path.read_text(encoding="utf-8")
    assert "NOT_EXECUTED" in report_html
    assert "Logout" in report_html


def test_login_cannot_pass_when_requested_success_message_or_logout_is_missing(
    tmp_path: Path,
) -> None:
    def dashboard_only(prompt: str) -> str:
        request = _request(prompt)
        dashboard = next(
            item
            for item in request["page_snapshot"]["elements"]
            if item["role"] == "heading" and item["name"] == "Dashboard"
        )
        return _ok(_first_step("expect_visible", dashboard))

    test_case = (
        'Log in and verify the success message "Welcome, tester. You have successfully '
        'logged in.", and verify that the Logout button is visible.'
    )
    result = _run(
        ScriptedProvider([_login_then_stale_assertion, dashboard_only]),
        tmp_path,
        test_case=test_case,
    )

    assert result.status == ResultCategory.FAIL
    assert "success message" in result.message
    assert "Logout link/button" in result.message
    assert any(
        record.status == ResultCategory.NOT_EXECUTED
        and "success message" in record.message
        for record in result.steps
    )
    assert any(
        record.status == ResultCategory.NOT_EXECUTED
        and "Logout link/button" in record.message
        for record in result.steps
    )


def test_navigation_login_reinspects_and_verifies_text_and_logout_link(
    tmp_path: Path,
) -> None:
    success_message = "You logged into a secure area!"

    def login_plan(prompt: str) -> str:
        request = _request(prompt)
        elements = request["page_snapshot"]["elements"]
        username = next(item for item in elements if item["label"] == "Username")
        password = next(item for item in elements if item["label"] == "Password")
        login = next(
            item
            for item in elements
            if item["role"] == "button" and item["name"] == "Login"
        )
        stale_heading = next(
            item for item in elements if item["role"] == "heading"
        )
        return _ok(
            {"action": "fill", "element_id": username["id"], "value": "tester"},
            {"action": "fill", "element_id": password["id"], "value": "test-password"},
            {"action": "click", "element_id": login["id"]},
            _first_step("expect_visible", stale_heading),
        )

    def post_login_plan(prompt: str) -> str:
        request = _request(prompt)
        elements = request["page_snapshot"]["elements"]
        assert request["page_snapshot"]["url"].endswith("navigation_dashboard.html")
        text = next(
            item
            for item in elements
            if item["role"] == "text" and success_message in item["text"]
        )
        logout = next(
            item
            for item in elements
            if item["role"] == "link" and item["name"] == "Logout"
        )
        return _ok(
            {
                "action": "expect_text",
                "element_id": text["id"],
                "text": success_message,
            },
            _first_step("expect_visible", logout),
        )

    provider = ScriptedProvider([login_plan, post_login_plan])
    test_case = (
        f'Log in and verify the success message "{success_message}" and that the '
        "Logout link is visible."
    )
    runner = TestRunner(
        provider,
        _settings(),
        reports_dir=tmp_path / "reports",
    )
    result = runner.run(
        url=(FIXTURES / "login_navigation.html").resolve().as_uri(),
        test_case=test_case,
    )

    assert result.status == ResultCategory.PASS
    assert result.ai_calls == 2
    assert "navigation_dashboard.html" in provider.prompts[1]
    assert success_message in provider.prompts[1]
    post_login_elements = _request(provider.prompts[1])["page_snapshot"]["elements"]
    assert any(item["role"] == "link" and item["name"] == "Logout" for item in post_login_elements)
    assert any(
        record.action == "expect_text"
        and record.status == ResultCategory.PASS
        and success_message in record.evidence
        for record in result.steps
    )
    assert any(
        record.action == "expect_visible"
        and record.status == ResultCategory.PASS
        and "link: Logout" in record.evidence
        for record in result.steps
    )
    assert len(result.plan_steps) == len(result.steps)
    assert all(
        entry["outcome"] == ResultCategory.NOT_EXECUTED.value
        for entry in result.plan_steps
        if entry["action"] == "expect_visible"
        and entry["run_step"] == 4
    )


def test_missing_secret_returns_name_without_value(tmp_path: Path) -> None:
    missing_value = "must-not-appear-in-result"

    def response(prompt: str) -> str:
        request = _request(prompt)
        password = next(
            item for item in request["page_snapshot"]["elements"] if item["label"] == "Password"
        )
        return _ok(
            {
                "action": "fill",
                "element_id": password["id"],
                "value": "{{secret:CUSTOMER_PASSWORD}}",
            },
            {"action": "expect_visible", "element_id": password["id"]},
        )

    result = _run(
        ScriptedProvider([response]),
        tmp_path,
        settings=_settings(
            allowed_secret_names=("CUSTOMER_PASSWORD",),
            secret_values={},
        ),
    )

    assert result.status == ResultCategory.NEEDS_USER_INPUT
    assert "CUSTOMER_PASSWORD" in result.message
    assert missing_value not in result.model_dump_json()


def test_unsafe_case_returns_blocked(tmp_path: Path) -> None:
    result = _run(
        ScriptedProvider([]),
        tmp_path,
        test_case="Click Pay now to purchase the item.",
    )

    assert result.status == ResultCategory.BLOCKED_UNSAFE_ACTION
    assert result.ai_calls == 0


def test_planner_invalid_json_then_correction_succeeds(tmp_path: Path) -> None:
    def corrected(prompt: str) -> str:
        request = _request(prompt)
        button = next(
            item
            for item in request["page_snapshot"]["elements"]
            if item["role"] == "button" and item["name"] == "Sign in"
        )
        return _ok(_first_step("expect_visible", button))

    provider = ScriptedProvider(["not json", corrected])
    result = _run(provider, tmp_path)

    assert result.status == ResultCategory.PASS
    assert result.ai_calls == 2
    assert "Invalid JSON" in provider.prompts[1]


def test_planner_calls_are_capped_per_run(tmp_path: Path) -> None:
    def changes_page(prompt: str) -> str:
        request = _request(prompt)
        elements = request["page_snapshot"]["elements"]
        button = next(item for item in elements if item["name"] == "Sign in")
        heading = next(item for item in elements if item["role"] == "heading")
        return _ok(
            {"action": "click", "element_id": button["id"]},
            _first_step("expect_visible", heading),
        )

    provider = ScriptedProvider([changes_page, changes_page])
    result = _run(
        provider,
        tmp_path,
        settings=_settings(max_planner_calls=1),
    )

    assert result.status == ResultCategory.EXECUTION_ERROR
    assert result.ai_calls == 1
    assert "planner-call limit" in result.message.lower()


def test_reports_json_html_and_summary_mask_secrets(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            lambda prompt: _ok(
                {
                    "action": "fill",
                    "element_id": next(
                        item["id"]
                        for item in _request(prompt)["page_snapshot"]["elements"]
                        if item["label"] == "Password"
                    ),
                    "value": "{{secret:LOGIN_PASSWORD}}",
                },
                {
                    "action": "expect_text",
                    "element_id": next(
                        item["id"]
                        for item in _request(prompt)["page_snapshot"]["elements"]
                        if item["role"] == "heading"
                    ),
                    "text": "Sign in",
                },
            )
        ]
    )
    result = _run(
        provider,
        tmp_path,
        test_case=f"Verify the heading; private test value: {PASSWORD}",
    )
    writer = ResultWriter(
        reports_dir=tmp_path / "reports",
        settings=_settings(),
    )
    files, summary = writer.write(result)

    assert result.status == ResultCategory.PASS
    assert all(path.is_file() for path in (files.json_path, files.html_path, files.summary_path))
    for path in (files.json_path, files.html_path, files.summary_path):
        assert PASSWORD not in path.read_text(encoding="utf-8")
    assert "OVERALL RESULT: PASS" in summary
    assert "AI calls: 1" in summary
    safe_summary = ReportGenerator(settings=_settings()).summary(result)
    assert "[PASS]" in safe_summary
    assert "[REDACTED]" in safe_summary
    assert "observed text: Sign in" in safe_summary
