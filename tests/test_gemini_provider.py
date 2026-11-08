"""Offline tests for Gemini transport configuration and safe error handling."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx2
import pytest
from google.genai import types
from google.genai._gaos.lib import compat_errors

import ai.gemini_provider as gemini_provider_module
from ai.gemini_provider import GeminiProvider, GeminiProviderError
from ai.schemas import Plan
from config.settings import Settings, load_settings
from ai.planner import Planner, PlannerError
from engine.test_runner import TestRunner
from models.test_result import ResultCategory
from reporting.result_writer import ResultWriter

WORKSPACE = Path(__file__).resolve().parents[1]
API_KEY = "api-key-must-never-be-reported"
SECRET = "account-password-must-never-be-reported"


class MockClient:
    def __init__(
        self,
        response_text: str | None = '{"status":"ok"}',
        error: BaseException | None = None,
    ) -> None:
        self.models = SimpleNamespace(generate_content=Mock())
        if error is not None:
            self.models.generate_content.side_effect = error
        else:
            self.models.generate_content.return_value = SimpleNamespace(text=response_text)
        self.close = Mock()


def _settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "gemini_model": "configured-test-model",
        "gemini_api_key": API_KEY,
        "timeout_seconds": 30.0,
        "gemini_timeout_ms": 91_234,
        "allowed_secret_names": ("LOGIN_PASSWORD",),
        "secret_values": {"LOGIN_PASSWORD": SECRET},
    }
    values.update(changes)
    return Settings(**values)  # type: ignore[arg-type]


def _error(kind: str) -> BaseException:
    request_url = (
        "https://example.invalid/?api_key=api-key-must-never-be-reported"
    )
    request = httpx2.Request("GET", request_url)
    if kind == "timeout":
        return compat_errors.APITimeoutError(request=request)
    if kind == "httpx_timeout":
        return httpx2.ReadTimeout(API_KEY, request=request)
    if kind == "connection":
        return compat_errors.APIConnectionError(request=request)
    if kind == "network":
        return httpx2.ConnectError(API_KEY, request=request)
    if kind == "authentication":
        return compat_errors.AuthenticationError(
            message=API_KEY,
            response=httpx2.Response(401, request=request),
            body={"message": API_KEY},
        )
    if kind == "invalid_key":
        return compat_errors.APIStatusError(
            message=API_KEY,
            response=httpx2.Response(400, request=request),
            body={"error": {"status": "API_KEY_INVALID", "message": API_KEY}},
        )
    if kind == "quota":
        return compat_errors.RateLimitError(
            message=API_KEY,
            response=httpx2.Response(429, request=request),
            body={"message": API_KEY},
        )
    raise AssertionError(f"Unknown mocked error: {kind}")


def test_client_receives_configured_http_transport_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MockClient()
    captured: dict[str, object] = {}

    def create_client(**kwargs: object) -> MockClient:
        captured.update(kwargs)
        return client

    monkeypatch.setattr(gemini_provider_module.genai, "Client", create_client)
    provider = GeminiProvider(_settings())
    prompt = '{"test":"prompt"}'
    response_text = provider.generate(prompt, response_schema=Plan)

    http_options = captured["http_options"]
    assert isinstance(http_options, types.HttpOptions)
    assert http_options.timeout == 91_234
    assert captured["api_key"] == API_KEY
    assert response_text == '{"status":"ok"}'
    assert client.models.generate_content.call_count == 1
    call = client.models.generate_content.call_args
    assert call.kwargs["model"] == "configured-test-model"
    assert call.kwargs["contents"] == prompt
    config = call.kwargs["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert config.response_mime_type == "application/json"
    schema = config.response_json_schema
    assert isinstance(schema, dict)
    _assert_no_discriminator(schema)
    steps_schema = schema["properties"]["steps"]["items"]
    assert len(steps_schema["oneOf"]) == 11
    assert "$defs" in schema
    action_values = {
        definition["properties"]["action"]["const"]
        for definition in schema["$defs"].values()
        if "action" in definition.get("properties", {})
    }
    assert action_values == {
        "navigate",
        "click",
        "fill",
        "select_option",
        "check",
        "uncheck",
        "press",
        "wait_for",
        "expect_visible",
        "expect_text",
        "expect_url",
    }
    assert config.response_schema is None
    assert config.thinking_config.thinking_level == types.ThinkingLevel.MINIMAL
    assert config.max_output_tokens >= 4096
    client.close.assert_called_once_with()


def _assert_no_discriminator(value: object) -> None:
    if isinstance(value, dict):
        assert "discriminator" not in value
        for nested in value.values():
            _assert_no_discriminator(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_discriminator(nested)


@pytest.mark.parametrize("response_text", [None, "", "  \n"])
def test_empty_gemini_response_is_a_safe_provider_error(
    monkeypatch: pytest.MonkeyPatch,
    response_text: str | None,
) -> None:
    client = MockClient(response_text=response_text)
    monkeypatch.setattr(gemini_provider_module.genai, "Client", lambda **_: client)

    with pytest.raises(GeminiProviderError, match="empty structured response"):
        GeminiProvider(_settings()).generate("prompt", response_schema=Plan)

    assert client.models.generate_content.call_count == 1
    client.close.assert_called_once_with()


def test_provider_enforces_configured_api_call_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MockClient()
    client_factory = Mock(return_value=client)
    monkeypatch.setattr(gemini_provider_module.genai, "Client", client_factory)
    provider = GeminiProvider(_settings(), max_api_calls=1)

    provider.generate("first", response_schema=Plan)
    with pytest.raises(GeminiProviderError, match="maximum of 1"):
        provider.generate("second", response_schema=Plan)

    assert client_factory.call_count == 1
    assert client.models.generate_content.call_count == 1
    assert provider.api_calls == 1


@pytest.mark.parametrize(
    ("kind", "expected_message"),
    [
        ("timeout", "timed out"),
        ("httpx_timeout", "timed out"),
        ("connection", "Could not connect"),
        ("network", "network request failed"),
        ("authentication", "authentication failed"),
        ("invalid_key", "authentication failed"),
        ("quota", "quota or rate limit"),
    ],
)
def test_gemini_errors_are_safe_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    expected_message: str,
) -> None:
    client = MockClient(error=_error(kind))
    monkeypatch.setattr(gemini_provider_module.genai, "Client", lambda **_: client)
    provider = GeminiProvider(_settings())

    with pytest.raises(GeminiProviderError, match=expected_message) as error:
        provider.generate("prompt", response_schema=Plan)

    assert API_KEY not in str(error.value)
    assert SECRET not in str(error.value)
    assert error.value.__cause__ is None
    assert client.models.generate_content.call_count == 1
    assert provider.api_calls == 1
    client.close.assert_called_once_with()


def test_timeout_becomes_safe_execution_error_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = MockClient(error=_error("timeout"))
    monkeypatch.setattr(gemini_provider_module.genai, "Client", lambda **_: client)
    settings = _settings()
    provider = GeminiProvider(settings, max_api_calls=4)

    result = TestRunner(provider, settings).run(
        url=(WORKSPACE / "fixtures" / "form_controls.html").as_uri(),
        test_case="Verify the form controls are displayed.",
    )
    files, _ = ResultWriter(tmp_path, settings).write(result)
    report_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (files.json_path, files.html_path, files.summary_path)
    )

    assert result.status == ResultCategory.EXECUTION_ERROR
    assert "timed out" in result.message
    assert API_KEY not in report_text
    assert SECRET not in report_text
    assert "api-key-must-never-be-reported" not in report_text


def test_malformed_gemini_json_is_corrected_by_planner_without_leakage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_plan = (
        '{"status":"ok","summary":"Verify the heading.",'
        '"steps":[{"action":"expect_visible","element_id":"e1"}]}'
    )
    clients = [MockClient(response_text="invalid json"), MockClient(response_text=valid_plan)]
    client_iter = iter(clients)

    def create_client(**_: object) -> MockClient:
        return next(client_iter)

    monkeypatch.setattr(gemini_provider_module.genai, "Client", create_client)
    settings = _settings(planner_correction_attempts=1)
    provider = GeminiProvider(settings, max_api_calls=2)
    plan = Planner(provider, settings).plan(
        test_case="Verify the heading.",
        page_snapshot='{"elements":[{"id":"e1","role":"heading","name":"Welcome"}]}',
    )

    assert plan.status == "ok"
    assert provider.api_calls == 2
    assert [client.models.generate_content.call_count for client in clients] == [1, 1]
    prompts = [
        client.models.generate_content.call_args.kwargs["contents"]
        for client in clients
    ]
    assert API_KEY not in "\n".join(prompts)
    assert SECRET not in "\n".join(prompts)


@pytest.mark.parametrize(
    "response_text",
    [
        "invalid JSON",
        '{"status":"ok","summary":"Unknown action.",'
        '"steps":[{"action":"run_script","element_id":"e1"}]}',
    ],
)
def test_invalid_json_and_unknown_action_are_rejected_by_planner(
    monkeypatch: pytest.MonkeyPatch,
    response_text: str,
) -> None:
    client = MockClient(response_text=response_text)
    monkeypatch.setattr(gemini_provider_module.genai, "Client", lambda **_: client)
    settings = _settings(planner_correction_attempts=0)
    planner = Planner(GeminiProvider(settings), settings)

    with pytest.raises(PlannerError, match="remained invalid"):
        planner.plan(
            test_case="Verify the heading.",
            page_snapshot='{"elements":[{"id":"e1","role":"heading","name":"Welcome"}]}',
        )

    assert client.models.generate_content.call_count == 1



def test_gemini_timeout_setting_defaults_and_validates() -> None:
    assert load_settings(environ={}).gemini_timeout_ms == 180_000
    settings = load_settings(environ={"GEMINI_TIMEOUT_MS": "91_234"})
    assert settings.gemini_timeout_ms == 91_234

    with pytest.raises(ValueError, match="GEMINI_TIMEOUT_MS"):
        load_settings(environ={"GEMINI_TIMEOUT_MS": "0"})
