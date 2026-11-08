"""CLI integration tests that use only fake planning and local fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import Settings
from main import main

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_login.html"


def test_fake_cli_runs_fixture_and_writes_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    exit_code = main(
        [
            "--url",
            FIXTURE.resolve().as_uri(),
            "--test-case",
            "Verify the sign in button is visible.",
            "--provider",
            "fake",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "OVERALL RESULT: PASS" in output
    assert "AI calls: 1" in output
    assert list((tmp_path / "reports").glob("*.json"))
    assert list((tmp_path / "reports").glob("*.html"))
    assert list((tmp_path / "reports").glob("*.txt"))


def test_gemini_cli_explains_missing_key_without_browser_or_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "main.load_settings",
        lambda: Settings(gemini_model=None, gemini_api_key=None),
    )

    exit_code = main(
        [
            "--url",
            FIXTURE.resolve().as_uri(),
            "--test-case",
            "Verify the sign in button.",
        ]
    )

    output = capsys.readouterr().err
    assert exit_code == 2
    assert "GEMINI_API_KEY is missing" in output
    assert list((tmp_path / "reports").glob("*.json"))
    assert list((tmp_path / "reports").glob("*.html"))
