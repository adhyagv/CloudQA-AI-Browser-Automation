"""Generate safe JSON and readable HTML output for one test run."""

from __future__ import annotations

import html
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.settings import Settings, load_settings
from models.test_result import TestResult


@dataclass(frozen=True)
class ReportFiles:
    json_path: Path
    html_path: Path
    summary_path: Path


class ReportGenerator:
    def __init__(
        self,
        reports_dir: str | Path = "reports",
        settings: Settings | None = None,
    ) -> None:
        self.reports_dir = Path(reports_dir)
        self.settings = settings or load_settings()

    def write(self, result: TestResult) -> ReportFiles:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        json_path = self.reports_dir / f"{run_id}.json"
        html_path = self.reports_dir / f"{run_id}.html"
        summary_path = self.reports_dir / f"{run_id}.txt"
        safe_result = self._sanitize(result)

        json_path.write_text(
            json.dumps(safe_result.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        html_path.write_text(self._html(safe_result), encoding="utf-8")
        summary_path.write_text(self.summary(safe_result), encoding="utf-8")
        return ReportFiles(json_path, html_path, summary_path)

    def summary(self, result: TestResult) -> str:
        result = self._sanitize(result)
        lines = [
            f"Website: {result.website}",
            f"Test case: {result.test_case}",
            f"AI interpretation: {result.ai_interpretation or 'Not available'}",
            "Browser execution:",
        ]
        if result.steps:
            for record in result.steps:
                lines.append(f"- Step {record.step}: {record.status} - {record.message}")
        else:
            lines.append(f"- {result.message}")

        assertions = [
            record
            for record in result.steps
            if self._is_assertion(record.action or record.step)
        ]
        if assertions:
            lines.append("Assertions:")
            for assertion in assertions:
                lines.append(
                    f"[{assertion.status}] {assertion.message} | Evidence: {assertion.evidence}"
                )
        lines.extend(
            [
                f"AI calls: {result.ai_calls}",
                f"OVERALL RESULT: {result.status}",
                f"Execution time: {result.execution_time_seconds:.3f} seconds",
            ]
        )
        if result.screenshot_path:
            lines.append(f"Screenshot: {result.screenshot_path}")
        return "\n".join(lines)

    def _html(self, result: TestResult) -> str:
        summary = html.escape(self.summary(result))
        plan_rows = "\n".join(
            f"<li><code>{html.escape(json.dumps(step, ensure_ascii=False))}</code></li>"
            for step in result.plan_steps
        )
        step_rows = "\n".join(
            "<tr>"
            f"<td>{html.escape(str(record.step))}</td>"
            f"<td>{html.escape(str(record.status))}</td>"
            f"<td>{html.escape(record.message)}</td>"
            f"<td>{html.escape(record.evidence)}</td>"
            "</tr>"
            for record in result.steps
        )
        screenshot = ""
        if result.screenshot_path:
            screenshot_name = html.escape(Path(result.screenshot_path).name)
            screenshot = f'<p><a href="{screenshot_name}">Failure screenshot</a></p>'
        return (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>Test report: {html.escape(str(result.status))}</title>"
            "<style>body{font:16px system-ui;max-width:1000px;margin:2rem auto;padding:0 1rem}"
            "pre{white-space:pre-wrap;background:#f4f4f4;padding:1rem}"
            "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:.5rem;"
            "text-align:left;vertical-align:top}</style></head><body>"
            f"<h1>Test report: {html.escape(str(result.status))}</h1>"
            f"<pre>{summary}</pre>{screenshot}<h2>Planned steps</h2><ol>{plan_rows}</ol>"
            "<h2>Step outcomes</h2><table><thead><tr><th>Step</th><th>Status</th>"
            "<th>Message</th><th>Evidence</th></tr></thead>"
            f"<tbody>{step_rows}</tbody></table></body></html>"
        )

    def _sanitize(self, result: TestResult) -> TestResult:
        payload: dict[str, Any] = result.model_dump(mode="python")
        payload["website"] = self.settings.redact(payload["website"])
        payload["test_case"] = self.settings.redact(payload["test_case"])
        payload["ai_interpretation"] = self.settings.redact(payload["ai_interpretation"])
        payload["browser_execution"] = self.settings.redact(payload["browser_execution"])
        payload["message"] = self.settings.redact(payload["message"])
        payload["screenshot_path"] = (
            self.settings.redact(payload["screenshot_path"])
            if payload["screenshot_path"]
            else None
        )
        payload["steps"] = [
            {
                **record,
                "message": self.settings.redact(record["message"]),
                "evidence": self.settings.redact(record["evidence"]),
            }
            for record in payload["steps"]
        ]
        payload["plan_steps"] = [
            self._sanitize_value(step) for step in payload["plan_steps"]
        ]
        return TestResult.model_validate(payload)

    def _sanitize_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.settings.redact(value)
        if isinstance(value, list):
            return [self._sanitize_value(item) for item in value]
        if isinstance(value, dict):
            return {key: self._sanitize_value(item) for key, item in value.items()}
        return value

    @staticmethod
    def _is_assertion(step: int | str) -> bool:
        return str(step) in {"expect_visible", "expect_text", "expect_url"}


__all__ = ["ReportFiles", "ReportGenerator"]
