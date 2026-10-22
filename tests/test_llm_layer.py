import json
from types import SimpleNamespace

import openai
import pytest

from sketch2sim import config, llm


def _bad_request(message: str, param: str | None, code: str | None):
    # A minimal stand-in for the HTTP response the SDK attaches to the error.
    response = SimpleNamespace(status_code=400, headers={}, request=None)
    body = {
        "message": message,
        "type": "invalid_request_error",
        "param": param,
        "code": code,
    }
    return openai.BadRequestError(message, response=response, body=body)


class _Recorder:
    def __init__(self, rejected: dict[str, str]):
        self.rejected = dict(rejected)
        self.calls: list[dict] = []

    def responses_create(self, **kwargs):
        self.calls.append(kwargs)
        for param, code in list(self.rejected.items()):
            top = param.split(".")[0]
            if top in kwargs:
                raise _bad_request(
                    f"Unsupported parameter: '{param}' is not supported with this model.",
                    param,
                    code,
                )
        return SimpleNamespace(
            output_text="ok",
            status="completed",
            incomplete_details=None,
            usage=SimpleNamespace(
                model_dump=lambda exclude_none=True: {"total_tokens": 3}
            ),
        )

    def chat_create(self, **kwargs):
        self.calls.append(kwargs)
        for param, code in list(self.rejected.items()):
            if param in kwargs:
                raise _bad_request(
                    f"Unsupported value: '{param}' does not support 0 with this model.",
                    param,
                    code,
                )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok"), finish_reason="stop"
                )
            ],
            usage=None,
        )


def _fake_client(recorder: _Recorder):
    return SimpleNamespace(
        responses=SimpleNamespace(create=recorder.responses_create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=recorder.chat_create)),
    )


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    llm.reset_client()
    llm.set_call_log(str(tmp_path / "calls.jsonl"))
    yield
    llm.set_call_log(None)
    llm.reset_client()


def test_responses_request_shape_and_temperature_fallback(monkeypatch, tmp_path):
    recorder = _Recorder({"temperature": "unsupported_parameter"})
    monkeypatch.setattr(llm, "get_client", lambda: _fake_client(recorder))
    monkeypatch.setattr(config, "OPENAI_API_MODE", "responses")

    result = llm.complete(
        "gpt-5",
        "SYSTEM",
        "USER",
        images_b64=["AAAA"],
        json_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
            "required": [],
        },
        max_output_tokens=123,
        reasoning_effort="none",  # sampling params are sent only without reasoning
        log_label="unit-test",
    )

    assert result.text == "ok"
    assert result.stripped_params == ["temperature"]
    first, second = recorder.calls
    assert first["instructions"] == "SYSTEM"
    assert first["store"] is False
    assert first["input"][0]["content"][0] == {"type": "input_text", "text": "USER"}
    assert first["input"][0]["content"][1]["type"] == "input_image"
    assert first["input"][0]["content"][1]["image_url"].startswith(
        "data:image/jpeg;base64,AAAA"
    )
    assert first["max_output_tokens"] == 123
    assert first["reasoning"] == {"effort": "none"}
    assert first["text"]["format"]["type"] == "json_schema"
    assert "temperature" in first and "temperature" not in second

    # the rejection is remembered for the model: no retry on the next call
    recorder.calls.clear()
    llm.complete("gpt-5", "SYSTEM", "USER", reasoning_effort="none")
    assert len(recorder.calls) == 1 and "temperature" not in recorder.calls[0]

    # with reasoning in force, temperature is not sent at all (and not "stripped")
    llm.reset_client()
    monkeypatch.setattr(llm, "get_client", lambda: _fake_client(recorder))
    recorder.calls.clear()
    result = llm.complete("gpt-5.6-terra", "SYSTEM", "USER")
    assert len(recorder.calls) == 1 and "temperature" not in recorder.calls[0]
    assert recorder.calls[0]["reasoning"] == {"effort": config.LLM_REASONING_EFFORT}
    assert result.omitted_params == ["temperature"] and result.stripped_params == []

    log_lines = (tmp_path / "calls.jsonl").read_text().splitlines()
    assert json.loads(log_lines[0])["label"] == "unit-test"


def test_json_schema_downgrades_to_json_object(monkeypatch):
    recorder = _Recorder({"text.format": "unsupported_parameter"})
    monkeypatch.setattr(llm, "get_client", lambda: _fake_client(recorder))
    monkeypatch.setattr(config, "OPENAI_API_MODE", "responses")

    # Reject only json_schema, accept json_object.
    original = recorder.responses_create

    def create(**kwargs):
        fmt = (kwargs.get("text") or {}).get("format", {})
        if fmt.get("type") == "json_schema":
            raise _bad_request(
                "Unsupported parameter: 'text.format'",
                "text.format",
                "unsupported_parameter",
            )
        recorder.calls.append(kwargs)
        return original.__self__.responses_create(
            **{k: v for k, v in kwargs.items() if k != "text"}
        )

    monkeypatch.setattr(
        llm,
        "get_client",
        lambda: SimpleNamespace(responses=SimpleNamespace(create=create)),
    )
    result = llm.complete(
        "gpt-5-mini",
        None,
        "USER",
        json_schema={"type": "object", "properties": {}, "required": []},
        temperature=None,
        reasoning_effort=None,
    )
    assert result.text == "ok"
    assert result.stripped_params == ["text:json_schema->json_object"]
    downgraded = [
        c
        for c in recorder.calls
        if (c.get("text") or {}).get("format", {}).get("type") == "json_object"
    ]
    assert downgraded, "the retry must use json_object"
    assert downgraded[-1]["input"][0]["content"][0]["text"].endswith(llm.JSON_MODE_HINT)


def test_chat_mode_request_shape(monkeypatch):
    recorder = _Recorder({"temperature": "unsupported_value"})
    monkeypatch.setattr(llm, "get_client", lambda: _fake_client(recorder))
    monkeypatch.setattr(config, "OPENAI_API_MODE", "chat")

    text = llm.chat("gpt-4.1", "SYSTEM", "USER", json_mode=True, max_output_tokens=50)
    assert text == "ok"
    first = recorder.calls[0]
    assert first["messages"][0] == {"role": "system", "content": "SYSTEM"}
    # neither SYSTEM nor USER mentions json, so the json_object hint is appended
    assert first["messages"][1] == {
        "role": "user",
        "content": f"USER\n\n{llm.JSON_MODE_HINT}",
    }
    assert first["max_completion_tokens"] == 50
    assert first["seed"] == config.LLM_SEED
    assert first["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in first  # gpt-4.1 is not a reasoning model
    assert "temperature" not in recorder.calls[-1]


def test_non_parameter_errors_are_raised(monkeypatch):
    def create(**kwargs):
        raise _bad_request("Invalid image data", "input", "invalid_image")

    monkeypatch.setattr(
        llm,
        "get_client",
        lambda: SimpleNamespace(responses=SimpleNamespace(create=create)),
    )
    monkeypatch.setattr(config, "OPENAI_API_MODE", "responses")
    with pytest.raises(openai.BadRequestError):
        llm.complete("gpt-5", None, "USER")


def test_is_reasoning_model():
    assert llm.is_reasoning_model("gpt-5.4")
    assert llm.is_reasoning_model("gpt-5.6-terra")
    assert llm.is_reasoning_model("gpt-6-astra")
    assert llm.is_reasoning_model("o3-mini")
    assert not llm.is_reasoning_model("gpt-5-chat-latest")
    assert not llm.is_reasoning_model("gpt-4.1-mini")


def test_missing_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    llm.reset_client()
    with pytest.raises(OSError):
        llm.get_client()


def _resp(text="ok", status="completed", reason=None, output=None, error=None):
    details = SimpleNamespace(reason=reason) if reason else None
    return SimpleNamespace(
        output_text=text,
        status=status,
        incomplete_details=details,
        output=output or [],
        error=error,
        usage=None,
    )


def test_truncated_and_failed_responses_raise(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_MODE", "responses")
    monkeypatch.setattr(
        llm,
        "get_client",
        lambda: SimpleNamespace(
            responses=SimpleNamespace(
                create=lambda **kw: _resp("partial", "incomplete", "max_output_tokens")
            )
        ),
    )
    with pytest.raises(llm.LLMError, match="truncated"):
        llm.complete("gpt-5.6-terra", None, "USER", max_output_tokens=10, log_label="x")

    monkeypatch.setattr(
        llm,
        "get_client",
        lambda: SimpleNamespace(
            responses=SimpleNamespace(
                create=lambda **kw: _resp(
                    "",
                    "failed",
                    error=SimpleNamespace(code="server_error", message="boom"),
                )
            )
        ),
    )
    with pytest.raises(llm.LLMError, match="failed response"):
        llm.complete("gpt-5.6-terra", None, "USER")

    refusal_output = [
        SimpleNamespace(content=[SimpleNamespace(type="refusal", refusal="no way")])
    ]
    monkeypatch.setattr(
        llm,
        "get_client",
        lambda: SimpleNamespace(
            responses=SimpleNamespace(
                create=lambda **kw: _resp("", "completed", output=refusal_output)
            )
        ),
    )
    with pytest.raises(llm.LLMError, match="refused"):
        llm.complete("gpt-5.6-terra", None, "USER")


def test_invalid_reasoning_effort_value_raises(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_MODE", "responses")

    def create(**kwargs):
        raise _bad_request(
            "Unsupported value: 'reasoning.effort' does not support 'xhigh' with this model.",
            "reasoning.effort",
            "unsupported_value",
        )

    monkeypatch.setattr(
        llm,
        "get_client",
        lambda: SimpleNamespace(responses=SimpleNamespace(create=create)),
    )
    with pytest.raises(llm.LLMError, match="reasoning effort"):
        llm.complete("gpt-5.6-terra", None, "USER", reasoning_effort="xhigh")


def test_chat_legacy_gateway_falls_back_to_max_tokens(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_MODE", "chat")
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if "max_completion_tokens" in kwargs:
            raise _bad_request(
                "Unsupported parameter: 'max_completion_tokens' is not supported with this model. Use 'max_tokens' instead.",
                "max_completion_tokens",
                "unsupported_parameter",
            )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok", refusal=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )

    monkeypatch.setattr(
        llm,
        "get_client",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    assert llm.chat("legacy-model", None, "USER", max_output_tokens=77) == "ok"
    assert calls[-1]["max_tokens"] == 77 and "max_completion_tokens" not in calls[-1]
    calls.clear()
    llm.chat("legacy-model", None, "USER", max_output_tokens=78)
    assert len(calls) == 1 and calls[0]["max_tokens"] == 78  # remembered for the model


def test_reasoning_model_detection_with_gateway_prefix():
    assert llm.is_reasoning_model("openai/gpt-5.4")
    assert llm.is_reasoning_model("azure-gpt-5.6-sol")
    assert not llm.is_reasoning_model("openai/gpt-4.1-mini")


def test_seed_env_parsing(monkeypatch):
    assert config._env_optional_int("S2S_TEST_SEED", 42) == 42
    monkeypatch.setenv("S2S_TEST_SEED", "0")
    assert config._env_optional_int("S2S_TEST_SEED", 42) == 0
    monkeypatch.setenv("S2S_TEST_SEED", "")
    assert config._env_optional_int("S2S_TEST_SEED", 42) is None
    monkeypatch.setenv("S2S_TEST_SEED", "off")
    assert config._env_optional_int("S2S_TEST_SEED", 42) is None


def test_json_mode_adds_json_word_to_responses_input(monkeypatch):
    """The Responses API checks the input messages only, not `instructions`."""
    recorder = _Recorder({})
    monkeypatch.setattr(llm, "get_client", lambda: _fake_client(recorder))
    monkeypatch.setattr(config, "OPENAI_API_MODE", "responses")

    # the system prompt mentions JSON, the user prompt (a JSON document) does not
    llm.chat(
        "gpt-4.1",
        "Return one JSON object with exact keys.",
        json.dumps({"description": "A pump feeds a separator."}),
        json_mode=True,
    )
    call = recorder.calls[-1]
    assert call["text"] == {"format": {"type": "json_object"}}
    assert call["instructions"] == "Return one JSON object with exact keys."
    assert call["input"][0]["content"][0]["text"].endswith("\n\n" + llm.JSON_MODE_HINT)

    # a user prompt that already says json is left alone
    llm.chat("gpt-4.1", "SYSTEM", "Answer as JSON: is water a liquid?", json_mode=True)
    assert recorder.calls[-1]["input"][0]["content"][0]["text"] == (
        "Answer as JSON: is water a liquid?"
    )

    # json_schema requests are not touched (the API does not require the word)
    llm.chat(
        "gpt-4.1",
        "SYSTEM",
        "USER",
        json_schema={"type": "object", "properties": {}, "required": []},
    )
    assert recorder.calls[-1]["input"][0]["content"][0]["text"] == "USER"
    # plain text requests are not touched either
    llm.chat("gpt-4.1", "SYSTEM", "USER")
    assert recorder.calls[-1]["input"][0]["content"][0]["text"] == "USER"


def test_ensure_json_word_with_images_and_chat_parts():
    responses_kwargs = {
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_image", "image_url": "data:..."},
                    {"type": "input_text", "text": "Describe the sketch."},
                ],
            }
        ]
    }
    assert llm.ensure_json_word(responses_kwargs) is True
    assert responses_kwargs["input"][0]["content"][1]["text"].endswith(
        llm.JSON_MODE_HINT
    )
    assert llm.ensure_json_word(responses_kwargs) is False  # idempotent

    chat_kwargs = {
        "messages": [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": [{"type": "text", "text": "Hi"}]},
        ]
    }
    assert llm.ensure_json_word(chat_kwargs) is True
    assert (
        chat_kwargs["messages"][1]["content"][0]["text"]
        == f"Hi\n\n{llm.JSON_MODE_HINT}"
    )
    assert (
        llm.ensure_json_word({"messages": [{"role": "system", "content": "json"}]})
        is False
    )
