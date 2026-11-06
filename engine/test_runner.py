"""Integrate planning, live page inspection, safe action execution, and assertions."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from ai.planner import Planner, PlannerBlockedError, PlannerError
from ai.gemini_provider import GeminiProviderError
from ai.provider import PlannerProvider
from browser.action_executor import ActionExecutor
from browser.browser_manager import BrowserExecutionError, BrowserManager, validate_navigation_url
from browser.page_inspector import PageSnapshot
from config.settings import Settings, load_settings
from models.actions import Action, ExpectTextAction, ExpectURLAction, ExpectVisibleAction
from models.plan import TestPlan
from models.test_result import ResultCategory, StepRecord, TestResult

_ASSERTIONS = (ExpectVisibleAction, ExpectTextAction, ExpectURLAction)
_SCREENSHOT_CATEGORIES = {
    ResultCategory.FAIL,
    ResultCategory.AMBIGUOUS,
    ResultCategory.EXECUTION_ERROR,
}


class PlannerCallLimitError(RuntimeError):
    """Raised when a run reaches its configured AI-call budget."""


class _LimitedProvider(PlannerProvider):
    def __init__(self, provider: PlannerProvider, limit: int) -> None:
        self.provider = provider
        self.limit = limit
        self.calls = 0

    @property
    def api_calls(self) -> int:
        actual_calls = getattr(self.provider, "api_calls", None)
        return actual_calls if isinstance(actual_calls, int) else self.calls

    def generate(self, prompt: str, *, response_schema: type) -> str:
        if self.calls >= self.limit:
            raise PlannerCallLimitError(
                f"Run reached its maximum of {self.limit} planner calls."
            )
        self.calls += 1
        return self.provider.generate(prompt, response_schema=response_schema)


class TestRunner:
    """Run a bounded test against a URL and produce one structured result."""

    __test__ = False

    def __init__(
        self,
        provider: PlannerProvider,
        settings: Settings | None = None,
        *,
        reports_dir: str | Path = "reports",
    ) -> None:
        self.settings = settings or load_settings()
        self.provider = _LimitedProvider(provider, self.settings.max_planner_calls)
        self.planner = Planner(self.provider, self.settings)
        self.reports_dir = Path(reports_dir)

    def run(self, *, url: str, test_case: str, headed: bool | None = None) -> TestResult:
        started = time.monotonic()
        run_id = uuid.uuid4().hex
        result = TestResult(
            status=ResultCategory.EXECUTION_ERROR,
            message="Run did not complete.",
            website=self.settings.redact(url),
            test_case=self.settings.redact(test_case),
        )
        settings = self.settings
        if headed is not None and headed != settings.headed:
            settings = self._with_headed(settings, headed)
        manager: BrowserManager | None = None

        try:
            validated_url = validate_navigation_url(url)
            manager = BrowserManager(settings).start()
            manager.navigate(validated_url)
            executor = ActionExecutor(manager, settings)
            snapshot = executor.inspect()
            result.website = settings.redact(snapshot.url)
            current_test_case = test_case
            completed_actions: list[str] = []
            evaluated_assertions = 0
            all_assertions_passed = True
            final_plan: TestPlan | None = None
            step_number = 0
            executed_steps = 0

            while self.provider.calls < self.provider.limit:
                try:
                    plan = self.planner.plan(
                        test_case=current_test_case,
                        page_snapshot=snapshot.compact_text(),
                        allowed_secret_names=settings.allowed_secret_names,
                    )
                except PlannerCallLimitError as exc:
                    result.status = ResultCategory.EXECUTION_ERROR
                    result.message = settings.redact(str(exc))
                    break
                except PlannerBlockedError as exc:
                    result.status = ResultCategory.BLOCKED_UNSAFE_ACTION
                    result.message = settings.redact(str(exc))
                    break
                except PlannerError as exc:
                    result.status = ResultCategory.EXECUTION_ERROR
                    result.message = settings.redact(f"Planner error: {exc}")
                    break

                final_plan = plan
                result.ai_interpretation = settings.redact(plan.summary)
                result.plan_steps.extend(self._safe_plan_steps(plan, settings))
                if plan.status == "needs_clarification":
                    result.status = ResultCategory.NEEDS_USER_INPUT
                    result.message = settings.redact(plan.message or "The test needs clarification.")
                    break
                if plan.status == "unsafe":
                    result.status = ResultCategory.BLOCKED_UNSAFE_ACTION
                    result.message = settings.redact(plan.message or "The test was marked unsafe.")
                    break

                replan = False
                iteration_failed = False
                for action in plan.steps:
                    if executed_steps >= settings.max_steps:
                        result.status = ResultCategory.EXECUTION_ERROR
                        result.message = (
                            f"Run reached its maximum of {settings.max_steps} executed steps."
                        )
                        iteration_failed = True
                        break
                    step_number += 1
                    executed_steps += 1
                    before = snapshot
                    action_result = executor.execute(action, snapshot=snapshot)
                    result.steps.extend(
                        self._renumber_records(
                            action_result.steps,
                            step_number,
                            settings,
                        )
                    )
                    if action_result.status != ResultCategory.PASS:
                        result.status = action_result.status
                        result.message = settings.redact(action_result.message)
                        iteration_failed = True
                        break

                    if isinstance(action, _ASSERTIONS):
                        evaluated_assertions += 1
                        if action_result.status != ResultCategory.PASS:
                            all_assertions_passed = False
                        if not all_assertions_passed:
                            result.status = ResultCategory.FAIL
                            result.message = "One or more browser assertions failed."
                            break
                        continue

                    snapshot = executor.current_snapshot or executor.inspect()
                    if self._snapshot_changed(before, snapshot):
                        completed_actions.append(self._action_description(action))
                        current_test_case = (
                            f"{test_case}\n\n"
                            "Already completed successfully: "
                            + "; ".join(completed_actions)
                            + ". Continue from the current page; do not repeat completed actions."
                        )
                        replan = True
                        break

                if iteration_failed:
                    break
                if result.status in {
                    ResultCategory.AMBIGUOUS,
                    ResultCategory.NEEDS_USER_INPUT,
                    ResultCategory.BLOCKED_UNSAFE_ACTION,
                    ResultCategory.FAIL,
                }:
                    break
                if replan:
                    continue
                if evaluated_assertions:
                    result.status = (
                        ResultCategory.PASS
                        if all_assertions_passed
                        else ResultCategory.FAIL
                    )
                    result.message = (
                        "All evaluated browser assertions passed."
                        if all_assertions_passed
                        else "One or more browser assertions failed."
                    )
                    break
                result.status = ResultCategory.FAIL
                result.message = "No assertion was evaluated against the browser."
                break
            else:
                result.status = ResultCategory.EXECUTION_ERROR
                result.message = "Run reached its configured planner-call limit."

            if final_plan is not None and not result.ai_interpretation:
                result.ai_interpretation = settings.redact(final_plan.summary)

        except BrowserExecutionError as exc:
            result.status = ResultCategory.EXECUTION_ERROR
            result.message = settings.redact(str(exc))
        except GeminiProviderError as exc:
            result.status = ResultCategory.EXECUTION_ERROR
            result.message = settings.redact(f"Configuration/API error: {exc}")
        except (OSError, ValueError) as exc:
            result.status = ResultCategory.EXECUTION_ERROR
            result.message = settings.redact(f"EXECUTION_ERROR: {exc}")
        finally:
            if manager is not None:
                if result.status in _SCREENSHOT_CATEGORIES:
                    result.screenshot_path = self._capture_screenshot(
                        manager,
                        run_id,
                        settings,
                    )
                manager.stop()

        result.ai_calls = self.provider.api_calls
        result.execution_time_seconds = time.monotonic() - started
        result.browser_execution = (
            f"{len(result.steps)} browser step(s) recorded."
            if result.steps
            else result.message
        )
        result.test_case = settings.redact(result.test_case)
        result.website = settings.redact(result.website)
        result.ai_interpretation = settings.redact(result.ai_interpretation)
        result.message = settings.redact(result.message)
        result.steps = [
            record.model_copy(
                update={
                    "message": settings.redact(record.message),
                    "evidence": settings.redact(record.evidence),
                }
            )
            for record in result.steps
        ]
        return result

    @staticmethod
    def _with_headed(settings: Settings, headed: bool) -> Settings:
        return replace(settings, headed=headed)

    @staticmethod
    def _snapshot_changed(before: PageSnapshot, after: PageSnapshot) -> bool:
        return before.to_dict() != after.to_dict()

    @staticmethod
    def _action_description(action: Action) -> str:
        return getattr(action, "action", "action")

    @staticmethod
    def _safe_plan_steps(plan: TestPlan, settings: Settings) -> list[dict[str, Any]]:
        return [
            json.loads(
                settings.redact(
                    json.dumps(step.model_dump(mode="json"), ensure_ascii=False)
                )
            )
            for step in plan.steps
        ]

    @staticmethod
    def _renumber_records(
        records: list[StepRecord],
        step_number: int,
        settings: Settings,
    ) -> list[StepRecord]:
        return [
            record.model_copy(
                update={
                    "step": step_number,
                    "action": str(record.step),
                    "message": settings.redact(record.message),
                    "evidence": settings.redact(record.evidence),
                }
            )
            for record in records
        ]

    def _capture_screenshot(
        self,
        manager: BrowserManager,
        run_id: str,
        settings: Settings,
    ) -> str | None:
        if manager.page is None:
            return None
        try:
            self.reports_dir.mkdir(parents=True, exist_ok=True)
            path = self.reports_dir / f"{run_id}.png"
            manager.page.screenshot(path=str(path), full_page=True)
            return str(path)
        except Exception:
            return None


__all__ = ["PlannerCallLimitError", "TestRunner"]
