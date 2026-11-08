"""Local-fixture browser engine tests using headless Chromium only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from browser.action_executor import ActionExecutor
from browser.browser_manager import BrowserExecutionError, BrowserManager
from browser.locator_resolver import LocatorResolver, ResolutionStatus
from browser.page_inspector import PageInspector, PageSnapshot
from config.settings import Settings
from engine.safety import BLOCKED_UNSAFE_ACTION, screen_action, screen_test_case
from models.actions import (
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
from models.test_result import ResultCategory

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PASSWORD = "local-fixture-password-not-for-results"


def _settings() -> Settings:
    return Settings(
        gemini_model=None,
        gemini_api_key=None,
        allowed_secret_names=("LOGIN_PASSWORD",),
        secret_values={"LOGIN_PASSWORD": PASSWORD},
    )


@pytest.fixture
def browser_manager():
    with BrowserManager(_settings()) as manager:
        yield manager


def _open(manager: BrowserManager, fixture_name: str) -> PageSnapshot:
    manager.navigate((FIXTURES / fixture_name).resolve().as_uri())
    return PageInspector(manager.settings).inspect(manager._require_page())


def _element(snapshot: PageSnapshot, *, name: str, role: str | None = None):
    matches = [
        item
        for item in snapshot.elements
        if item.name == name and (role is None or item.role == role)
    ]
    assert len(matches) == 1, f"Expected one snapshot element named {name!r}, found {len(matches)}"
    return matches[0]


def _run(executor: ActionExecutor, action: object):
    assert executor.current_snapshot is not None
    return executor.execute(action, snapshot=executor.current_snapshot)


def _refreshed(executor: ActionExecutor) -> PageSnapshot:
    assert executor.current_snapshot is not None
    return executor.current_snapshot


def test_snapshot_has_compact_controls_and_never_password_values(
    browser_manager: BrowserManager,
) -> None:
    snapshot = _open(browser_manager, "sample_login.html")
    executor = ActionExecutor(browser_manager, browser_manager.settings)
    executor.current_snapshot = snapshot
    password = _element(snapshot, name="Password", role="textbox")
    password.locator.fill(PASSWORD)

    snapshot = executor.inspect()
    password = _element(snapshot, name="Password", role="textbox")
    encoded = snapshot.compact_text()

    assert snapshot.title == "Sample Login"
    assert snapshot.headings == ("Sign in",)
    assert password.element_id.startswith("e")
    assert password.label == "Password"
    assert password.input_type == "password"
    assert password.masked is True
    assert PASSWORD not in encoded
    assert PASSWORD not in repr(snapshot)
    assert len(encoded) < len(json.dumps(snapshot.to_dict(), ensure_ascii=False))
    assert '"visible"' not in encoded
    assert '"headings"' not in encoded
    assert _element(snapshot, name="Sign in", role="button")


def test_snapshot_includes_visible_noninteractive_text_and_accessible_link(
    browser_manager: BrowserManager,
) -> None:
    snapshot = _open(browser_manager, "navigation_dashboard.html")
    success_text = _element(
        snapshot,
        name="You logged into a secure area!",
        role="text",
    )
    logout = _element(snapshot, name="Logout", role="link")

    assert success_text.text == "You logged into a secure area!"
    assert logout.tag == "a"
    assert "You logged into a secure area!" in snapshot.compact_text()
    assert '"role":"link","name":"Logout"' in snapshot.compact_text()


def test_snapshot_id_resolves_to_its_labeled_control(
    browser_manager: BrowserManager,
) -> None:
    snapshot = _open(browser_manager, "sample_login.html")
    element = _element(snapshot, name="Username", role="textbox")

    resolved = LocatorResolver(browser_manager._require_page()).resolve(
        snapshot,
        element.element_id,
    )

    assert resolved.status == ResolutionStatus.RESOLVED
    assert resolved.locator is not None
    assert resolved.locator.get_attribute("id") == "username"


def test_ambiguous_submit_returns_candidates_and_clicks_nothing(
    browser_manager: BrowserManager,
) -> None:
    snapshot = _open(browser_manager, "ambiguous.html")
    executor = ActionExecutor(browser_manager, browser_manager.settings)
    executor.current_snapshot = snapshot
    first_submit = next(item for item in snapshot.elements if item.name == "Submit")

    result = _run(executor, ClickAction(action="click", element_id=first_submit.element_id))

    assert result.status == ResultCategory.AMBIGUOUS
    assert set(result.steps[0].evidence.split(": ", 1)[1].split(", ")) == {
        item.element_id for item in snapshot.elements if item.name == "Submit"
    }
    assert browser_manager._require_page().get_by_role("status").inner_text() == "No button clicked"


def test_hidden_and_disabled_elements_are_rejected(
    browser_manager: BrowserManager,
) -> None:
    snapshot = _open(browser_manager, "hidden_disabled.html")
    executor = ActionExecutor(browser_manager, browser_manager.settings)
    executor.current_snapshot = snapshot

    assert all(item.stable_id != "hidden-button" for item in snapshot.elements)
    assert all(item.stable_id != "hidden-input" for item in snapshot.elements)
    disabled = _element(snapshot, name="Disabled action", role="button")

    hidden_result = executor.execute(
        ClickAction(action="click", element_id="e999"),
        snapshot=snapshot,
    )
    disabled_result = _run(
        executor,
        ClickAction(action="click", element_id=disabled.element_id),
    )

    assert hidden_result.status == ResultCategory.NEEDS_USER_INPUT
    assert disabled_result.status == ResultCategory.NEEDS_USER_INPUT
    assert "disabled" in disabled_result.message


def test_secret_reference_fills_locally_and_never_enters_results_or_logs(
    browser_manager: BrowserManager,
    caplog: pytest.LogCaptureFixture,
) -> None:
    snapshot = _open(browser_manager, "sample_login.html")
    executor = ActionExecutor(browser_manager, browser_manager.settings)
    executor.current_snapshot = snapshot
    password = _element(snapshot, name="Password", role="textbox")

    result = _run(
        executor,
        FillAction(
            action="fill",
            element_id=password.element_id,
            value="{{secret:LOGIN_PASSWORD}}",
        ),
    )

    assert result.status == ResultCategory.PASS
    assert browser_manager._require_page().locator("#password").input_value() == PASSWORD
    assert PASSWORD not in str(result.model_dump())
    assert PASSWORD not in caplog.text
    assert PASSWORD not in _refreshed(executor).compact_text()


def test_wrong_credentials_show_real_error_and_expect_text_passes(
    browser_manager: BrowserManager,
) -> None:
    snapshot = _open(browser_manager, "sample_login.html")
    executor = ActionExecutor(browser_manager, browser_manager.settings)
    executor.current_snapshot = snapshot

    username = _element(_refreshed(executor), name="Username", role="textbox")
    assert _run(executor, FillAction(action="fill", element_id=username.element_id, value="tester")).status == ResultCategory.PASS
    password = _element(_refreshed(executor), name="Password", role="textbox")
    assert _run(executor, FillAction(action="fill", element_id=password.element_id, value="wrong-password")).status == ResultCategory.PASS
    sign_in = _element(_refreshed(executor), name="Sign in", role="button")
    assert _run(executor, ClickAction(action="click", element_id=sign_in.element_id)).status == ResultCategory.PASS

    error = _element(_refreshed(executor), name="Invalid username or password", role="status")
    result = _run(
        executor,
        ExpectTextAction(
            action="expect_text",
            element_id=error.element_id,
            text="Invalid username or password",
        ),
    )

    assert browser_manager._require_page().get_by_role("status").inner_text() == "Invalid username or password"
    assert result.status == ResultCategory.PASS
    assert "observed text: Invalid username or password" in result.steps[0].evidence


def test_correct_credentials_reach_dashboard_and_assertions_pass(
    browser_manager: BrowserManager,
) -> None:
    snapshot = _open(browser_manager, "sample_login.html")
    executor = ActionExecutor(browser_manager, browser_manager.settings)
    executor.current_snapshot = snapshot

    for field_name, value in (("Username", "tester"), ("Password", "correct-horse")):
        field = _element(_refreshed(executor), name=field_name, role="textbox")
        result = _run(
            executor,
            FillAction(action="fill", element_id=field.element_id, value=value),
        )
        assert result.status == ResultCategory.PASS
    sign_in = _element(_refreshed(executor), name="Sign in", role="button")
    assert _run(executor, ClickAction(action="click", element_id=sign_in.element_id)).status == ResultCategory.PASS

    home = _element(_refreshed(executor), name="Dashboard home", role="link")
    visible = _run(
        executor,
        ExpectVisibleAction(action="expect_visible", element_id=home.element_id),
    )
    url = _run(
        executor,
        ExpectURLAction(action="expect_url", url=browser_manager._require_page().url),
    )

    assert visible.status == ResultCategory.PASS
    assert url.status == ResultCategory.PASS
    assert browser_manager._require_page().get_by_role("heading", name="Dashboard").is_visible()


def test_false_text_assertion_returns_fail_not_pass(
    browser_manager: BrowserManager,
) -> None:
    snapshot = _open(browser_manager, "sample_login.html")
    executor = ActionExecutor(browser_manager, browser_manager.settings)
    executor.current_snapshot = snapshot
    sign_in = _element(snapshot, name="Sign in", role="button")

    result = _run(
        executor,
        ExpectTextAction(
            action="expect_text",
            element_id=sign_in.element_id,
            text="Welcome home",
        ),
    )

    assert result.status == ResultCategory.FAIL
    assert "observed text: Sign in" in result.steps[0].evidence


def test_select_check_uncheck_and_press_actions(
    browser_manager: BrowserManager,
) -> None:
    snapshot = _open(browser_manager, "form_controls.html")
    executor = ActionExecutor(browser_manager, browser_manager.settings)
    executor.current_snapshot = snapshot

    select = _element(_refreshed(executor), name="Favorite color", role="combobox")
    assert _run(
        executor,
        SelectOptionAction(action="select_option", element_id=select.element_id, value="blue"),
    ).status == ResultCategory.PASS
    assert browser_manager._require_page().locator("#color").input_value() == "blue"

    checkbox = _element(_refreshed(executor), name="Subscribe", role="checkbox")
    assert _run(executor, CheckAction(action="check", element_id=checkbox.element_id)).status == ResultCategory.PASS
    assert browser_manager._require_page().locator("#subscribe").is_checked()
    checkbox = _element(_refreshed(executor), name="Subscribe", role="checkbox")
    assert _run(executor, UncheckAction(action="uncheck", element_id=checkbox.element_id)).status == ResultCategory.PASS
    assert not browser_manager._require_page().locator("#subscribe").is_checked()

    radio = _element(_refreshed(executor), name="Phone", role="radio")
    assert _run(executor, CheckAction(action="check", element_id=radio.element_id)).status == ResultCategory.PASS
    assert browser_manager._require_page().locator("#contact-phone").is_checked()

    notes = _element(_refreshed(executor), name="Notes", role="textbox")
    assert _run(executor, FillAction(action="fill", element_id=notes.element_id, value="hello")).status == ResultCategory.PASS
    notes = _element(_refreshed(executor), name="Notes", role="textbox")
    assert _run(executor, PressAction(action="press", element_id=notes.element_id, key="Enter")).status == ResultCategory.PASS
    pressed = _element(_refreshed(executor), name="Enter pressed", role="status")
    assert _run(
        executor,
        ExpectTextAction(
            action="expect_text",
            element_id=pressed.element_id,
            text="Enter pressed",
        ),
    ).status == ResultCategory.PASS


def test_cookie_overlay_is_dismissed_with_normal_click(
    browser_manager: BrowserManager,
) -> None:
    snapshot = _open(browser_manager, "overlay.html")
    executor = ActionExecutor(browser_manager, browser_manager.settings)
    executor.current_snapshot = snapshot
    dismiss = _element(snapshot, name="Accept cookies", role="button")

    result = _run(
        executor,
        ClickAction(action="click", element_id=dismiss.element_id),
    )

    assert result.status == ResultCategory.PASS
    assert browser_manager._require_page().locator("#cookie-banner").count() == 0
    assert browser_manager._require_page().get_by_role("status").inner_text() == "Cookie notice dismissed"


def test_executor_navigate_and_wait_for_actions(browser_manager: BrowserManager) -> None:
    executor = ActionExecutor(browser_manager, browser_manager.settings)
    fixture_url = (FIXTURES / "sample_login.html").resolve().as_uri()

    navigation = executor.execute(NavigateAction(action="navigate", url=fixture_url))
    sign_in = _element(_refreshed(executor), name="Sign in", role="button")
    wait = _run(
        executor,
        WaitForAction(
            action="wait_for",
            element_id=sign_in.element_id,
            state="visible",
        ),
    )

    assert navigation.status == ResultCategory.PASS
    assert wait.status == ResultCategory.PASS
    assert browser_manager._require_page().url == fixture_url


def test_invalid_file_url_is_execution_error() -> None:
    with BrowserManager(_settings()) as manager:
        executor = ActionExecutor(manager, manager.settings)
        result = executor.execute(
            NavigateAction(
                action="navigate",
                url="file://invalid/does-not-exist.html",
            )
        )

    assert result.status == ResultCategory.EXECUTION_ERROR
    assert "EXECUTION_ERROR" in result.message
    assert BrowserExecutionError.code == ResultCategory.EXECUTION_ERROR


def test_verifying_booking_control_is_allowed_but_performing_it_is_blocked(
    browser_manager: BrowserManager,
) -> None:
    snapshot = _open(browser_manager, "form_controls.html")
    executor = ActionExecutor(browser_manager, browser_manager.settings)
    executor.current_snapshot = snapshot
    book_button = _element(snapshot, name="Book now", role="button")
    pay_button = _element(snapshot, name="Pay now", role="button")

    assert not screen_test_case("Verify that the Book now button is displayed").blocked
    assert screen_test_case("Clicking Pay now would submit an order").code == BLOCKED_UNSAFE_ACTION
    verify = _run(
        executor,
        ExpectVisibleAction(action="expect_visible", element_id=book_button.element_id),
    )
    safety = screen_action(
        ClickAction(action="click", element_id=pay_button.element_id),
        pay_button.name,
    )
    result = _run(
        executor,
        ClickAction(action="click", element_id=pay_button.element_id),
    )

    assert verify.status == ResultCategory.PASS
    assert safety.code == BLOCKED_UNSAFE_ACTION
    assert result.status == ResultCategory.BLOCKED_UNSAFE_ACTION
    assert browser_manager._require_page().get_by_role("button", name="Pay now").is_visible()


@pytest.mark.parametrize(
    "label",
    ["Pay now", "Purchase", "Book now", "Confirm order", "Delete", "Cancel booking"],
)
def test_safety_blocks_irreversible_control_clicks(label: str) -> None:
    result = screen_action(ClickAction(action="click", element_id="e1"), label)

    assert result.code == BLOCKED_UNSAFE_ACTION


def test_old_snapshot_is_rejected_after_external_navigation(
    browser_manager: BrowserManager,
) -> None:
    old_snapshot = _open(browser_manager, "sample_login.html")
    executor = ActionExecutor(browser_manager, browser_manager.settings)
    executor.current_snapshot = old_snapshot
    sign_in = _element(old_snapshot, name="Sign in", role="button")
    browser_manager.navigate((FIXTURES / "form_controls.html").resolve().as_uri())

    result = executor.execute(
        ClickAction(action="click", element_id=sign_in.element_id),
        snapshot=old_snapshot,
    )

    assert result.status == ResultCategory.NEEDS_USER_INPUT
    assert "navigated after this snapshot" in result.message
