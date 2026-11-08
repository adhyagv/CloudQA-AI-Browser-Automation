"""Command-line entry point for integrated AI planning and browser execution."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from ai.gemini_provider import GeminiProvider, GeminiProviderError
from ai.provider import PlannerProvider
from browser.browser_manager import BrowserExecutionError, validate_navigation_url
from config.settings import Settings, SettingsError, load_settings
from engine.test_runner import TestRunner
from models.test_result import ResultCategory, TestResult
from reporting.result_writer import ResultWriter


class _SnapshotDemoProvider(PlannerProvider):
    """Offline CLI demo that verifies a requested snapshot button when available."""

    def generate(self, prompt: str, *, response_schema: type) -> str:
        try:
            request_text = prompt.split("Request data (JSON):\n", 1)[1]
            request, _ = json.JSONDecoder().raw_decode(request_text)
            snapshot = json.loads(request["page_snapshot"])
            elements = snapshot.get("elements", [])
            test_case = request.get("test_case", "")
        except (IndexError, KeyError, json.JSONDecodeError, TypeError):
            elements = []
            test_case = ""
        if not elements:
            return json.dumps(
                {
                    "status": "needs_clarification",
                    "summary": "The offline demo could not find a visible element.",
                    "steps": [],
                    "message": "No visible snapshot element is available to verify.",
                }
            )
        element = next(
            (
                item
                for item in elements
                if item.get("role") == "button"
                and item.get("name", "").casefold() in test_case.casefold()
            ),
            elements[0],
        )
        return json.dumps(
            {
                "status": "ok",
                "summary": f"Verify that {element.get('name') or 'the selected element'} is displayed.",
                "steps": [{"action": "expect_visible", "element_id": element["id"]}],
            }
        )


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an AI-planned browser test.")
    parser.add_argument("--url", help="Target URL (HTTP, HTTPS, or local file URL)")
    parser.add_argument("--test-case", help="Plain-English test case")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--headed", action="store_true", help="Show the Chromium window")
    mode.add_argument("--headless", action="store_true", help="Run Chromium headlessly")
    parser.add_argument(
        "--provider",
        choices=("gemini", "fake"),
        default="gemini",
        help="Planner provider (default: gemini)",
    )
    return parser.parse_args(argv)


def _exit_code(status: ResultCategory) -> int:
    if status == ResultCategory.PASS:
        return 0
    if status == ResultCategory.EXECUTION_ERROR:
        return 2
    return 1


def _configuration_error(
    *,
    url: str,
    test_case: str,
    settings: Settings,
    message: str,
) -> int:
    result = TestResult(
        status=ResultCategory.EXECUTION_ERROR,
        message=message,
        website=settings.redact(url),
        test_case=settings.redact(test_case),
        browser_execution="Browser was not launched because configuration is incomplete.",
        execution_time_seconds=0.0,
    )
    files, summary = ResultWriter(settings=settings).write(result)
    print(summary)
    print(f"JSON report: {files.json_path}")
    print(f"HTML report: {files.html_path}")
    print(f"Text summary: {files.summary_path}")
    print(f"Configuration error: {message}", file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        settings = load_settings()
    except SettingsError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    url = args.url or input("Website URL: ").strip()
    test_case = args.test_case or input("Plain-English test case: ").strip()
    if not url or not test_case:
        print("Configuration error: both URL and test case are required.", file=sys.stderr)
        return 2
    try:
        validate_navigation_url(url)
    except BrowserExecutionError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.provider == "gemini":
        if not settings.gemini_api_key:
            return _configuration_error(
                url=url,
                test_case=test_case,
                settings=settings,
                message=(
                    "GEMINI_API_KEY is missing. Set it in the local environment or .env file."
                ),
            )
        if not settings.gemini_model:
            return _configuration_error(
                url=url,
                test_case=test_case,
                settings=settings,
                message=(
                    "GEMINI_MODEL is missing. Set a currently supported model ID in "
                    "the local environment or .env file."
                ),
            )
        provider: PlannerProvider = GeminiProvider(
            settings,
            max_api_calls=settings.max_planner_calls,
        )
    else:
        provider = _SnapshotDemoProvider()

    try:
        result = TestRunner(provider, settings).run(
            url=url,
            test_case=test_case,
            headed=True if args.headed else False if args.headless else None,
        )
        files, summary = ResultWriter(settings=settings).write(result)
    except (OSError, GeminiProviderError, SettingsError) as exc:
        print(f"Execution error: {settings.redact(str(exc))}", file=sys.stderr)
        return 2

    print(summary)
    print(f"JSON report: {files.json_path}")
    print(f"HTML report: {files.html_path}")
    print(f"Text summary: {files.summary_path}")
    return _exit_code(result.status)

if __name__ == "__main__":
    raise SystemExit(main())
