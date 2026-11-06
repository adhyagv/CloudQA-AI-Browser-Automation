"""Write run reports and produce the assignment-style console summary."""

from __future__ import annotations

from pathlib import Path

from config.settings import Settings
from models.test_result import TestResult
from reporting.report_generator import ReportFiles, ReportGenerator


class ResultWriter:
    def __init__(
        self,
        reports_dir: str | Path = "reports",
        settings: Settings | None = None,
    ) -> None:
        self.generator = ReportGenerator(reports_dir, settings)

    def write(self, result: TestResult) -> tuple[ReportFiles, str]:
        files = self.generator.write(result)
        return files, self.generator.summary(result)


__all__ = ["ResultWriter"]
