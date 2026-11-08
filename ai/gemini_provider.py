"""Google Gen AI provider using structured JSON output."""

from __future__ import annotations

from typing import Any, TypeVar

import httpx2
from google import genai
from google.genai import types
from google.genai._gaos.lib import compat_errors
from pydantic import BaseModel

from ai.provider import PlannerProvider
from config.settings import Settings, load_settings

SchemaT = TypeVar("SchemaT", bound=BaseModel)


def _remove_discriminator(schema: Any) -> Any:
    if isinstance(schema, dict):
        return {
            key: _remove_discriminator(value)
            for key, value in schema.items()
            if key != "discriminator"
        }
    if isinstance(schema, list):
        return [_remove_discriminator(value) for value in schema]
    return schema


class GeminiProviderError(RuntimeError):
    """Raised when Gemini is unavailable or cannot return a response."""

    code = "EXECUTION_ERROR"


class GeminiProvider(PlannerProvider):
    """Use a configured Gemini model; no model ID is assumed by this provider."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        max_api_calls: int | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.max_api_calls = max_api_calls
        self.api_calls = 0

    def generate(self, prompt: str, *, response_schema: type[SchemaT]) -> str:
        if not self.settings.gemini_api_key:
            raise GeminiProviderError(
                "GEMINI_API_KEY is missing; configure it in the environment before using Gemini."
            )
        if not self.settings.gemini_model:
            raise GeminiProviderError(
                "GEMINI_MODEL is missing; configure a currently supported model ID."
            )

        if self.max_api_calls is not None and self.api_calls >= self.max_api_calls:
            raise GeminiProviderError(
                f"Run reached its maximum of {self.max_api_calls} Gemini API calls."
            )

        client = None
        try:
            client = genai.Client(
                api_key=self.settings.gemini_api_key,
                http_options=types.HttpOptions(timeout=self.settings.gemini_timeout_ms),
            )
            self.api_calls += 1
            compatible_schema = _remove_discriminator(
                response_schema.model_json_schema()
            )
            response = client.models.generate_content(
                model=self.settings.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=compatible_schema,
                    thinking_config=types.ThinkingConfig(
                        thinking_level=types.ThinkingLevel.MINIMAL
                    ),
                    max_output_tokens=4096,
                ),
            )
            response_text = response.text
            if not response_text or not response_text.strip():
                raise GeminiProviderError("Gemini returned an empty structured response.")
            return response_text
        except compat_errors.APITimeoutError:
            raise GeminiProviderError(
                "Gemini API request timed out. The transport timeout is configured by "
                "GEMINI_TIMEOUT_MS; check connectivity and try again later."
            ) from None
        except compat_errors.AuthenticationError:
            raise GeminiProviderError(
                "Gemini authentication failed. Verify GEMINI_API_KEY and its API access."
            ) from None
        except compat_errors.RateLimitError:
            raise GeminiProviderError(
                "Gemini API quota or rate limit was exceeded. Check quota and retry later."
            ) from None
        except compat_errors.APIStatusError as exc:
            status_code = getattr(exc, "status_code", None)
            body = getattr(exc, "body", None)
            body_text = str(body).lower()
            invalid_key = (
                "api_key_invalid" in body_text
                or "invalid api key" in body_text
                or "api key not valid" in body_text
            )
            if status_code in {401, 403} or invalid_key:
                message = (
                    "Gemini authentication failed. Verify GEMINI_API_KEY and its API access."
                )
            elif status_code == 429:
                message = (
                    "Gemini API quota or rate limit was exceeded. Check quota and retry later."
                )
            else:
                message = (
                    f"Gemini API request failed with HTTP status {status_code or 'unknown'}."
                )
            raise GeminiProviderError(message) from None
        except compat_errors.APIConnectionError:
            raise GeminiProviderError(
                "Could not connect to the Gemini API. Check the network connection and try again."
            ) from None
        except compat_errors.APIError:
            raise GeminiProviderError(
                "Gemini API request failed. Check the model configuration and service status."
            ) from None
        except httpx2.TimeoutException:
            raise GeminiProviderError(
                "Gemini API request timed out. The transport timeout is configured by "
                "GEMINI_TIMEOUT_MS; check connectivity and try again later."
            ) from None
        except httpx2.HTTPError:
            raise GeminiProviderError(
                "Gemini network request failed. Check the network connection and try again."
            ) from None
        finally:
            if client is not None:
                client.close()


__all__ = ["GeminiProvider", "GeminiProviderError"]
