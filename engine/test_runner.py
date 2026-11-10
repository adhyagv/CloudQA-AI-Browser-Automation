"""Integrate planning, live page inspection, safe action execution, and assertions."""

from __future__ import annotations

import json
import re
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
_SUCCESS_MESSAGE_MARKER = re.compile(
    r"\b(?:(?:success|confirmation|welcome)\s+(?:message|text)|"
    r"successful\s+login\s+(?:message|text))\b",
    re.IGNORECASE,
)
_QUOTED_TEXT = re.compile(r"""["'“‘]([^"'”’]+)["'”’]""")
_VISIBLE_CONTROL_MENTION = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_-]*(?:\s+[A-Za-z][A-Za-z0-9_-]*){0,2})\s+"
    r"(?:button|link)\b",
    re.IGNORECASE,
)
_VISIBLE_WORDS = re.compile(
    r"\b(?:visible|displayed|shown|present|appears|exists|verify|check|confirm|ensure)\b",
    re.IGNORECASE,
)
_BUTTON_FILLER_WORDS = {
    "assert",
    "check",
    "confirm",
    "ensure",
    "that",
    "the",
    "verify",
}


class PlannerCallLimitError(RuntimeError):
    """Raised when a run reaches its configured AI-call budget."""


def _requested_assertions(test_case: str) -> tuple[bool, set[str], set[str]]:
    success_requested = bool(_SUCCESS_MESSAGE_MARKER.search(test_case))
    exact_success_texts: set[str] = set()
    for marker in _SUCCESS_MESSAGE_MARKER.finditer(test_case):
        following_text = test_case[marker.end() : marker.end() + 120]
        match = _QUOTED_TEXT.search(following_text)
        if match:
            exact_success_texts.add(match.group(1).strip())
    visible_buttons: set[str] = set()

    # Examine each instruction separately to avoid treating clicked
    # controls as additional visibility requirements.
    clauses = re.split(
        r"\bthen\b|[.!?;\n]",
        test_case,
        flags=re.IGNORECASE,
    )

    for clause in clauses:
        if not _VISIBLE_WORDS.search(clause):
            continue

        for match in _VISIBLE_CONTROL_MENTION.finditer(clause):
            words = [
                word
                for word in match.group(1).split()
                if word.lower() not in _BUTTON_FILLER_WORDS
                and word.lower() not in {"a", "an", "the"}
            ]

            if words:
                visible_buttons.add(" ".join(words).casefold())

    return success_requested, exact_success_texts, visible_buttons

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
        (
            success_message_requested,
            requested_success_texts,
            requested_visible_buttons,
        ) = _requested_assertions(test_case)
        verified_success_texts: set[str] = set()
        verified_success_message = False
        verified_visible_buttons: set[str] = set()

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
                plan_entries = self._safe_plan_steps(plan, settings)
                plan_step_numbers: list[int] = []
                for entry in plan_entries:
                    step_number += 1
                    plan_step_numbers.append(step_number)
                    entry.update(
                        {
                            "run_step": step_number,
                            "outcome": ResultCategory.NOT_EXECUTED.value,
                            "outcome_message": "Planned step has not been executed yet.",
                        }
                    )
                result.plan_steps.extend(plan_entries)
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
                for action_index, action in enumerate(plan.steps):
                    if executed_steps >= settings.max_steps:
                        result.status = ResultCategory.EXECUTION_ERROR
                        result.message = (
                            f"Run reached its maximum of {settings.max_steps} executed steps."
                        )
                        self._mark_unexecuted_steps(
                            result,
                            plan.steps,
                            plan_entries,
                            plan_step_numbers,
                            action_index,
                            "The configured execution-step limit was reached.",
                        )
                        iteration_failed = True
                        break
                    executed_steps += 1
                    before = snapshot
                    action_result = executor.execute(action, snapshot=snapshot)
                    plan_entries[action_index]["outcome"] = action_result.status.value
                    plan_entries[action_index]["outcome_message"] = settings.redact(
                        action_result.message
                    )
                    result.steps.extend(
                        self._renumber_records(
                            action_result.steps,
                            plan_step_numbers[action_index],
                            settings,
                        )
                    )
                    target = (
                        snapshot.get(action.element_id)
                        if isinstance(
                            action,
                            (ExpectVisibleAction, ExpectTextAction),
                        )
                        else None
                    )
                    if action_result.status == ResultCategory.PASS:
                        if isinstance(action, ExpectTextAction):
                            if action.text in requested_success_texts:
                                verified_success_texts.add(action.text)
                            if (
                                success_message_requested
                                and not requested_success_texts
                                and target is not None
                                and target.role in {"status", "alert", "heading"}
                                and action.text in {target.text, target.name}
                            ):
                                verified_success_message = True
                        elif (
                            isinstance(action, ExpectVisibleAction)
                            and target is not None
                            and target.role in {"button", "link"}
                        ):
                            label = target.name.strip().casefold()
                            if label in requested_visible_buttons:
                                verified_visible_buttons.add(label)
                    if action_result.status != ResultCategory.PASS:
                        result.status = action_result.status
                        result.message = settings.redact(action_result.message)
                        self._mark_unexecuted_steps(
                            result,
                            plan.steps,
                            plan_entries,
                            plan_step_numbers,
                            action_index + 1,
                            "Execution stopped after an earlier step did not pass.",
                        )
                        iteration_failed = True
                        break

                    if isinstance(action, _ASSERTIONS):
                        evaluated_assertions += 1
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
                    self._mark_unexecuted_steps(
                        result,
                        plan.steps,
                        plan_entries,
                        plan_step_numbers,
                        action_index + 1,
                        "A fresh page snapshot was requested after the page changed.",
                    )
                    continue
                missing_assertions = self._missing_requested_assertions(
                    success_message_requested=success_message_requested,
                    requested_success_texts=requested_success_texts,
                    requested_visible_buttons=requested_visible_buttons,
                    verified_success_texts=verified_success_texts,
                    verified_success_message=verified_success_message,
                    verified_visible_buttons=verified_visible_buttons,
                )
                if missing_assertions:
                    self._record_missing_assertions(
                        result,
                        missing_assertions,
                        step_number,
                        settings,
                    )
                    step_number += len(missing_assertions)
                    result.status = ResultCategory.FAIL
                    result.message = (
                        "Requested assertion(s) were missing or not verified: "
                        + "; ".join(missing_assertions)
                    )
                    break
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
    def _mark_unexecuted_steps(
        result: TestResult,
        actions: list[Action],
        plan_entries: list[dict[str, Any]],
        step_numbers: list[int],
        start_index: int,
        reason: str,
    ) -> None:
        for index in range(start_index, len(actions)):
            message = f"NOT_EXECUTED: {reason}"
            plan_entries[index]["outcome"] = ResultCategory.NOT_EXECUTED.value
            plan_entries[index]["outcome_message"] = reason
            result.steps.append(
                StepRecord(
                    step=step_numbers[index],
                    status=ResultCategory.NOT_EXECUTED,
                    message=message,
                    action=actions[index].action,
                )
            )

    @staticmethod
    def _missing_requested_assertions(
        *,
        success_message_requested: bool,
        requested_success_texts: set[str],
        requested_visible_buttons: set[str],
        verified_success_texts: set[str],
        verified_success_message: bool,
        verified_visible_buttons: set[str],
    ) -> list[str]:
        missing: list[str] = []
        missing.extend(
            f'expect_text for exact success message "{text}"'
            for text in sorted(requested_success_texts - verified_success_texts)
        )
        if (
            success_message_requested
            and not requested_success_texts
            and not verified_success_message
        ):
            missing.append("expect_text for the requested success message")
        missing.extend(
            f"expect_visible for the {label.title()} link/button"
            for label in sorted(requested_visible_buttons - verified_visible_buttons)
        )
        return missing

    @staticmethod
    def _record_missing_assertions(
        result: TestResult,
        missing_assertions: list[str],
        last_step_number: int,
        settings: Settings,
    ) -> None:
        for offset, description in enumerate(missing_assertions, start=1):
            action = "expect_text" if "expect_text" in description else "expect_visible"
            step_number = last_step_number + offset
            message = settings.redact(
                f"NOT_EXECUTED: Requested assertion was missing or not verified: {description}"
            )
            result.plan_steps.append(
                {
                    "action": action,
                    "requested_assertion": settings.redact(description),
                    "run_step": step_number,
                    "outcome": ResultCategory.NOT_EXECUTED.value,
                    "outcome_message": message,
                }
            )
            result.steps.append(
                StepRecord(
                    step=step_number,
                    status=ResultCategory.NOT_EXECUTED,
                    message=message,
                    action=action,
                )
            )

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
