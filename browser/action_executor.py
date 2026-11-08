"""Execute validated browser actions using snapshot-scoped semantic locators."""

from __future__ import annotations

import re

from playwright.sync_api import Error as PlaywrightError, Locator

from browser.browser_manager import BrowserExecutionError, BrowserManager
from browser.locator_resolver import LocatorResolver, ResolutionStatus
from browser.page_inspector import PageInspector, PageSnapshot
from config.settings import Settings, load_settings
from engine.assertion_engine import AssertionEngine
from engine.safety import screen_action
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
from models.test_result import ResultCategory, StepRecord, TestResult

_SECRET_REFERENCE = re.compile(r"^\{\{secret:([A-Z][A-Z0-9_]*)\}\}$")
_ASSERTIONS: tuple[type[object], ...] = (
    ExpectVisibleAction,
    ExpectTextAction,
    ExpectURLAction,
)


class ActionExecutor:
    """Execute one action at a time and publish a fresh snapshot after mutations."""

    def __init__(
        self,
        manager: BrowserManager,
        settings: Settings | None = None,
    ) -> None:
        self.manager = manager
        self.settings = settings or manager.settings or load_settings()
        self.inspector = PageInspector(self.settings)
        self.current_snapshot: PageSnapshot | None = None

    def inspect(self) -> PageSnapshot:
        self.current_snapshot = self.inspector.inspect(self.manager._require_page())
        return self.current_snapshot

    def execute(
        self,
        action: Action,
        *,
        snapshot: PageSnapshot | None = None,
    ) -> TestResult:
        try:
            if isinstance(action, NavigateAction):
                self.manager.navigate(action.url)
                self.inspect()
                return self._result(action, ResultCategory.PASS, "Navigation completed.")

            active_snapshot = snapshot or self.current_snapshot
            if active_snapshot is None:
                active_snapshot = self.inspect()
            elif self.current_snapshot is not None and active_snapshot.token != self.current_snapshot.token:
                return self._result(
                    action,
                    ResultCategory.NEEDS_USER_INPUT,
                    "This element ID belongs to an outdated snapshot; inspect the page again.",
                )
            else:
                self.current_snapshot = active_snapshot
            if active_snapshot.url != self.manager._require_page().url:
                self.inspect()
                return self._result(
                    action,
                    ResultCategory.NEEDS_USER_INPUT,
                    "The page navigated after this snapshot; inspect it again before acting.",
                )

            resolver = LocatorResolver(self.manager._require_page())
            assertion_engine = AssertionEngine(resolver, self.settings)
            if isinstance(action, _ASSERTIONS):
                return assertion_engine.evaluate(action, active_snapshot)

            needs_enabled = not isinstance(action, WaitForAction)
            resolution = resolver.resolve(
                active_snapshot,
                action.element_id,
                require_enabled=needs_enabled,
            )
            if resolution.status == ResolutionStatus.AMBIGUOUS:
                return self._result(
                    action,
                    ResultCategory.AMBIGUOUS,
                    resolution.message,
                    f"Candidates: {', '.join(resolution.candidates)}",
                )
            if resolution.status != ResolutionStatus.RESOLVED or resolution.locator is None:
                category = (
                    ResultCategory.NEEDS_USER_INPUT
                    if resolution.status in {
                        ResolutionStatus.NOT_FOUND,
                        ResolutionStatus.HIDDEN,
                        ResolutionStatus.DISABLED,
                    }
                    else ResultCategory.EXECUTION_ERROR
                )
                return self._result(action, category, resolution.message)

            element = resolution.element
            target_text = " ".join(
                value for value in (element.name, element.label, element.text) if value
            ) if element else ""
            safety = screen_action(action, target_text)
            if safety.blocked:
                return self._result(
                    action,
                    ResultCategory.BLOCKED_UNSAFE_ACTION,
                    safety.reason,
                )

            if isinstance(action, FillAction) and self._resolve_fill_value(action.value) is None:
                match = _SECRET_REFERENCE.fullmatch(action.value)
                name = match.group(1) if match else "unknown"
                return self._result(
                    action,
                    ResultCategory.NEEDS_USER_INPUT,
                    f"Secret name {name} is not configured in local settings.",
                )

            return self._execute_resolved(action, resolution.locator, active_snapshot)
        except BrowserExecutionError as exc:
            return self._result(
                action,
                ResultCategory.EXECUTION_ERROR,
                self.settings.redact(str(exc)),
            )
        except _MissingSecretReference:
            return self._result(
                action,
                ResultCategory.NEEDS_USER_INPUT,
                "The referenced secret is not configured in local settings.",
            )
        except PlaywrightError as exc:
            return self._result(
                action,
                ResultCategory.EXECUTION_ERROR,
                self.settings.redact(f"EXECUTION_ERROR: {exc}"),
            )

    def _execute_resolved(
        self,
        action: Action,
        locator: Locator,
        snapshot: PageSnapshot,
    ) -> TestResult:
        attempts = self.settings.stale_retry_attempts + 1
        for attempt in range(attempts):
            page = self.manager._require_page()
            main_frame = page.main_frame
            navigated = False

            def on_frame_navigated(frame: object) -> None:
                nonlocal navigated
                if frame == main_frame:
                    navigated = True

            if isinstance(action, ClickAction):
                page.on("framenavigated", on_frame_navigated)
            try:
                message = self._perform(action, locator)
                if isinstance(action, ClickAction) and navigated:
                    self.manager.wait_for_dom_content_loaded()
                self.inspect()
                return self._result(action, ResultCategory.PASS, message)
            except PlaywrightError as exc:
                stale = "not attached" in str(exc).lower() or "detached" in str(exc).lower()
                if stale and attempt + 1 < attempts:
                    refreshed = self.inspect()
                    old_element = snapshot.get(getattr(action, "element_id", ""))
                    element = refreshed.get(getattr(action, "element_id", ""))
                    if (
                        old_element is None
                        or element is None
                        or not self._same_snapshot_target(old_element, element)
                    ):
                        break
                    resolution = LocatorResolver(self.manager._require_page()).resolve(
                        refreshed,
                        element.element_id,
                        require_enabled=True,
                    )
                    if resolution.status != ResolutionStatus.RESOLVED or resolution.locator is None:
                        break
                    locator = resolution.locator
                    snapshot = refreshed
                    continue
                return self._result(
                    action,
                    ResultCategory.EXECUTION_ERROR,
                    self.settings.redact(f"EXECUTION_ERROR: {exc}"),
                )
            finally:
                if isinstance(action, ClickAction):
                    page.remove_listener("framenavigated", on_frame_navigated)

        return self._result(
            action,
            ResultCategory.NEEDS_USER_INPUT,
            "The element changed during the action; inspect the page and retry.",
        )

    def _perform(self, action: Action, locator: Locator) -> str:
        timeout = self.settings.action_timeout_ms
        if isinstance(action, ClickAction):
            locator.click(timeout=timeout)
            return f"Clicked {action.element_id}."
        if isinstance(action, FillAction):
            value = self._resolve_fill_value(action.value)
            if value is None:
                raise _MissingSecretReference
            locator.fill(value, timeout=timeout)
            return f"Filled {action.element_id}; input value omitted."
        if isinstance(action, SelectOptionAction):
            locator.select_option(action.value, timeout=timeout)
            return f"Selected an option in {action.element_id}."
        if isinstance(action, CheckAction):
            locator.check(timeout=timeout)
            return f"Checked {action.element_id}."
        if isinstance(action, UncheckAction):
            locator.uncheck(timeout=timeout)
            return f"Unchecked {action.element_id}."
        if isinstance(action, PressAction):
            locator.press(action.key, timeout=timeout)
            return f"Pressed a key on {action.element_id}."
        if isinstance(action, WaitForAction):
            locator.wait_for(
                state=action.state,
                timeout=action.timeout_ms or timeout,
            )
            return f"Wait condition reached for {action.element_id}."
        raise TypeError(f"Unsupported action type: {type(action).__name__}")

    def _resolve_fill_value(self, value: str) -> str | None:
        match = _SECRET_REFERENCE.fullmatch(value)
        if match is None:
            return value
        return self.settings.get_secret(match.group(1))

    @staticmethod
    def _same_snapshot_target(old: object, new: object) -> bool:
        attributes = ("role", "name", "label", "placeholder", "text", "input_type", "stable_id")
        return all(getattr(old, attribute) == getattr(new, attribute) for attribute in attributes)

    @staticmethod
    def _result(
        action: object,
        status: ResultCategory,
        message: str,
        evidence: str = "",
    ) -> TestResult:
        return TestResult(
            status=status,
            message=message,
            steps=[
                StepRecord(
                    step=getattr(action, "action", "action"),
                    status=status,
                    message=message,
                    evidence=evidence,
                )
            ],
        )

class _MissingSecretReference(Exception):
    pass


__all__ = ["ActionExecutor"]
