"""Evaluate planner assertions against a live Playwright page."""

from __future__ import annotations

from models.actions import ExpectTextAction, ExpectURLAction, ExpectVisibleAction
from models.test_result import ResultCategory, StepRecord, TestResult
from browser.locator_resolver import LocatorResolver, ResolutionStatus
from browser.page_inspector import PageSnapshot
from config.settings import Settings, load_settings


class AssertionEngine:
    def __init__(self, resolver: LocatorResolver, settings: Settings | None = None) -> None:
        self.resolver = resolver
        self.settings = settings or load_settings()

    def evaluate(
        self,
        action: ExpectVisibleAction | ExpectTextAction | ExpectURLAction,
        snapshot: PageSnapshot,
    ) -> TestResult:
        if isinstance(action, ExpectURLAction):
            actual_url = self.settings.redact(self.resolver.page.url)
            expected_url = self.settings.redact(action.url)
            passed = actual_url == expected_url
            evidence = f"Expected URL: {expected_url}; actual URL: {actual_url}"
            return self._result(
                action,
                ResultCategory.PASS if passed else ResultCategory.FAIL,
                "URL assertion passed." if passed else "URL assertion failed.",
                evidence,
            )

        resolution = self.resolver.resolve(
            snapshot,
            action.element_id,
            require_enabled=False,
        )
        if resolution.status == ResolutionStatus.AMBIGUOUS:
            return self._result(
                action,
                ResultCategory.AMBIGUOUS,
                resolution.message,
                f"Candidates: {', '.join(resolution.candidates)}",
            )
        if resolution.status != ResolutionStatus.RESOLVED or resolution.locator is None:
            return self._result(
                action,
                ResultCategory.FAIL,
                f"Assertion target is unavailable: {resolution.message}",
                resolution.message,
            )

        if isinstance(action, ExpectVisibleAction):
            visible = resolution.locator.is_visible()
            return self._result(
                action,
                ResultCategory.PASS if visible else ResultCategory.FAIL,
                "Visibility assertion passed." if visible else "Visibility assertion failed.",
                f"Observed visible={visible} for {action.element_id}.",
            )

        actual_text = self.settings.redact(resolution.locator.inner_text().strip())
        passed = action.text in actual_text
        evidence = self.settings.redact(
            f"Expected text: {action.text}; observed text: {actual_text}"
        )
        return self._result(
            action,
            ResultCategory.PASS if passed else ResultCategory.FAIL,
            "Text assertion passed." if passed else "Text assertion failed.",
            evidence,
        )

    @staticmethod
    def _result(
        action: object,
        status: ResultCategory,
        message: str,
        evidence: str,
    ) -> TestResult:
        return TestResult(
            status=status,
            message=message,
            steps=[
                StepRecord(
                    step=getattr(action, "action", "assertion"),
                    status=status,
                    message=message,
                    evidence=evidence,
                )
            ],
        )


__all__ = ["AssertionEngine"]
