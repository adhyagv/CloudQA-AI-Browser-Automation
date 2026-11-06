"""Google Gen AI provider using structured JSON output."""

from __future__ import annotations

import time
from typing import TypeVar

import httpx2
from google import genai
from google.genai import errors
from pydantic import BaseModel

from config.settings import Settings, load_settings
from ai.provider import PlannerProvider

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class GeminiProviderError(RuntimeError):
    """Raised when Gemini is unavailable or cannot return a response."""


class GeminiProvider(PlannerProvider):
    """Use a configured Gemini model; no model ID is assumed by this provider."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()

    def generate(self, prompt: str, *, response_schema: type[SchemaT]) -> str:
        if not self.settings.gemini_api_key:
            raise GeminiProviderError(
                "GEMINI_API_KEY is missing; configure it in the environment before using Gemini."
            )
        if not self.settings.gemini_model:
            raise GeminiProviderError(
                "GEMINI_MODEL is missing; configure a currently supported model ID."
            )

        client = genai.Client(api_key=self.settings.gemini_api_key)
        try:
            for attempt in range(self.settings.provider_retries + 1):
                try:
                    interaction = client.interactions.create(
                        model=self.settings.gemini_model,
                        input=prompt,
                        response_format={
                            "type": "text",
                            "mime_type": "application/json",
                            "schema": response_schema.model_json_schema(),
                        },
                        timeout=self.settings.timeout_seconds,
                    )
                    response_text = interaction.output_text
                    if not response_text:
                        raise GeminiProviderError(
                            "Gemini returned an empty structured response."
                        )
                    return response_text
                except errors.APIError as exc:
                    if exc.code == 429 and attempt < self.settings.provider_retries:
                        time.sleep(min(2**attempt, 8))
                        continue
                    raise GeminiProviderError(
                        f"Gemini API request failed with status {exc.code}."
                    ) from exc
                except httpx2.HTTPError as exc:
                    raise GeminiProviderError(
                        f"Gemini API transport failed ({type(exc).__name__})."
                    ) from exc
        finally:
            client.close()

        raise GeminiProviderError("Gemini request ended without a response.")


__all__ = ["GeminiProvider", "GeminiProviderError"]
