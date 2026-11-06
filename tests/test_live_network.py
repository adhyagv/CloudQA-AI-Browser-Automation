"""Explicitly opt-in smoke test for the live Gemini provider."""

from __future__ import annotations

import os

import pytest

from ai.gemini_provider import GeminiProvider
from ai.prompts import SYSTEM_PROMPT
from ai.schemas import Plan
from config.settings import load_settings


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_TESTS") != "1",
    reason="set RUN_LIVE_TESTS=1 to enable live Gemini API tests",
)
def test_live_gemini_structured_output() -> None:
    settings = load_settings()
    prompt = (
        f"{SYSTEM_PROMPT}\n\nReturn needs_clarification for a test case with no page."
    )
    response = GeminiProvider(settings).generate(prompt, response_schema=Plan)
    assert '"status"' in response
