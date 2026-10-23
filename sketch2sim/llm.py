"""Single entry point for every language-model call in the pipeline.

All agents (descriptor, validator, extractor, basis, instantiation,
configuration, fixer) and the RAG embeddings go through this module, which
wraps the official ``openai`` Python SDK. Two transports are supported:

* ``responses`` (default): the OpenAI Responses API, which supports every
  current model, image input, Structured Outputs and reasoning controls.
* ``chat``: the Chat Completions API, useful for OpenAI-compatible gateways.

The transport is selected with ``S2S_OPENAI_API_MODE`` (see ``config.py``).

Decoding parameters are not supported uniformly across models (for example
reasoning models reject ``temperature``). Instead of hard-coding a matrix per
model, :func:`complete` sends the configured parameters and, when the API
answers with an *unsupported parameter / unsupported value* error, drops the
offending parameter, remembers that for the model and retries. The parameters
that were dropped are recorded in the call log so a run stays auditable.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sketch2sim import config

_UNSET: Any = object()

_client = None
_client_lock = threading.Lock()
_call_log_path: str | None = None
_unsupported_by_model: dict[str, set[str]] = {}
# Reasoning effort levels from highest to lowest. When a model rejects a level
# the request is retried one level lower, and the accepted level is remembered
# per model so later calls start there.
_EFFORT_LADDER = ["max", "xhigh", "high", "medium", "low", "minimal"]
_effort_ceiling_by_model: dict[str, str] = {}
# Output caps above the maximum of a model are clamped to the maximum the API
# names in its error ("Expected a value <= 128000", "supports at most 65536
# completion tokens"); the maximum is remembered per model.
_OUTPUT_CAP_PARAMS = ("max_output_tokens", "max_completion_tokens", "max_tokens")
_CAP_CEILING_RE = re.compile(
    r"(?:<=|at most|maximum(?: value)?(?: is| of)?|less than or equal to|"
    r"cannot exceed|must not exceed|limit(?: is| of)?|up to)\s*:?\s*(\d{3,})",
    re.IGNORECASE,
)
_output_cap_ceiling_by_model: dict[str, int] = {}

# Parameters that may be dropped when a model rejects them. Anything else that
# is rejected is a real error and is raised.
_STRIPPABLE_PARAMS = {
    "temperature",
    "top_p",
    "seed",
    "reasoning_effort",
    "reasoning",
    "verbosity",
    "max_completion_tokens",
    "max_output_tokens",
    "max_tokens",
    "text",
    "response_format",
    "store",
}
_UNSUPPORTED_CODES = {"unsupported_parameter", "unsupported_value"}
_UNSUPPORTED_MESSAGE_RE = re.compile(
    r"unsupported (?:parameter|value)s?|not supported (?:with|for|by) this model|"
    r"does not support|is not supported",
    re.IGNORECASE,
)
_PARAM_IN_MESSAGE_RE = re.compile(r"'([A-Za-z_][A-Za-z0-9_.\[\]]*)'")


class LLMError(RuntimeError):
    """Raised when a model call cannot be completed."""


def require_api_key() -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise OSError(
            "OPENAI_API_KEY is not set. Export it (or put it in a .env file) "
            "before running the pipeline."
        )
    return api_key


def get_client():
    """Return the process-wide OpenAI client (created lazily)."""
    global _client
    with _client_lock:
        if _client is None:
            from openai import OpenAI

            kwargs: dict[str, Any] = {
                "api_key": require_api_key(),
                "timeout": config.LLM_TIMEOUT,
                "max_retries": config.LLM_MAX_RETRIES,
            }
            for env_name, kwarg in (
                ("OPENAI_BASE_URL", "base_url"),
                ("OPENAI_ORG_ID", "organization"),
                ("OPENAI_PROJECT_ID", "project"),
            ):
                value = os.getenv(env_name, "").strip()
                if value:
                    kwargs[kwarg] = value
            _client = OpenAI(**kwargs)
        return _client


def reset_client() -> None:
    """Forget the cached client (used by tests and after key changes)."""
    global _client
    with _client_lock:
        _client = None
    _unsupported_by_model.clear()
    _effort_ceiling_by_model.clear()
    _output_cap_ceiling_by_model.clear()


def set_call_log(path: str | None) -> None:
    """Append one JSON line per model call to ``path`` (``None`` disables)."""
    global _call_log_path
    _call_log_path = path


def is_reasoning_model(model: str) -> bool:
    """Heuristic: models that accept ``reasoning`` controls.

    Gateway-style ids ("openai/gpt-5.4", "azure-gpt-5.4") are reduced to the
    bare model name first.
    """
    name = (model or "").lower().rsplit("/", 1)[-1]
    for prefix in ("azure-", "openai-", "oai-"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
    if "chat" in name:  # gpt-5-chat-latest and friends are non-reasoning
        return False
    return name.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))


@dataclass
class LLMResult:
    text: str
    model: str
    api: str
    usage: dict[str, Any] = field(default_factory=dict)
    stripped_params: list[str] = field(default_factory=list)
    omitted_params: list[str] = field(default_factory=list)
    finish: str | None = None
    duration_s: float = 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def chat(
    model: str,
    system_prompt: str | None,
    user_prompt: str,
    *,
    images_b64: list[str] | tuple[str, ...] = (),
    image_mime: str = "image/jpeg",
    json_mode: bool = False,
    json_schema: dict[str, Any] | None = None,
    schema_name: str = "structured_output",
    max_output_tokens: int | None = None,
    temperature: float | None = _UNSET,
    seed: int | None = _UNSET,
    reasoning_effort: str | None = _UNSET,
    log_label: str | None = None,
) -> str:
    """Run one system+user exchange and return the model text."""
    return complete(
        model,
        system_prompt,
        user_prompt,
        images_b64=images_b64,
        image_mime=image_mime,
        json_mode=json_mode,
        json_schema=json_schema,
        schema_name=schema_name,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        seed=seed,
        reasoning_effort=reasoning_effort,
        log_label=log_label,
    ).text


def complete(
    model: str,
    system_prompt: str | None,
    user_prompt: str,
    *,
    images_b64: list[str] | tuple[str, ...] = (),
    image_mime: str = "image/jpeg",
    json_mode: bool = False,
    json_schema: dict[str, Any] | None = None,
    schema_name: str = "structured_output",
    max_output_tokens: int | None = None,
    temperature: float | None = _UNSET,
    seed: int | None = _UNSET,
    reasoning_effort: str | None = _UNSET,
    log_label: str | None = None,
) -> LLMResult:
    """Run one exchange and return text plus usage metadata."""
    if not model:
        raise ValueError("model must be a non-empty model identifier")

    if temperature is _UNSET:
        temperature = config.LLM_TEMPERATURE
    if seed is _UNSET:
        seed = config.LLM_SEED
    if reasoning_effort is _UNSET:
        reasoning_effort = (
            config.LLM_REASONING_EFFORT if is_reasoning_model(model) else None
        )

    # Reasoning models accept sampling parameters only without reasoning
    # ("none"); do not even send them in that case.
    omitted: list[str] = []
    if reasoning_effort and reasoning_effort != "none" and is_reasoning_model(model):
        if temperature is not None:
            omitted.append("temperature")
            temperature = None

    api = (config.OPENAI_API_MODE or "responses").lower()
    client = get_client()
    started = time.monotonic()

    if api == "chat":
        kwargs = _build_chat_kwargs(
            model,
            system_prompt,
            user_prompt,
            images_b64,
            image_mime,
            json_mode,
            json_schema,
            schema_name,
            max_output_tokens,
            temperature,
            seed,
            reasoning_effort,
        )
        raw, stripped = _invoke_with_param_fallback(
            client.chat.completions.create, kwargs, model
        )
        choice = raw.choices[0]
        text = _content_to_text(choice.message.content)
        finish = choice.finish_reason
        usage = _usage_dict(raw)
        refusal = getattr(choice.message, "refusal", None)
        if refusal and not text:
            raise LLMError(f"{log_label or model}: the model refused: {refusal}")
    elif api == "responses":
        kwargs = _build_responses_kwargs(
            model,
            system_prompt,
            user_prompt,
            images_b64,
            image_mime,
            json_mode,
            json_schema,
            schema_name,
            max_output_tokens,
            temperature,
            reasoning_effort,
        )
        raw, stripped = _invoke_with_param_fallback(
            client.responses.create, kwargs, model
        )
        text = raw.output_text or ""
        finish = getattr(raw, "status", None)
        details = getattr(raw, "incomplete_details", None)
        if details is not None and getattr(details, "reason", None):
            finish = f"{finish}:{details.reason}"
        usage = _usage_dict(raw)
        if finish == "failed":
            error = getattr(raw, "error", None)
            raise LLMError(
                f"{log_label or model}: the Responses API reported a failed response: "
                f"{getattr(error, 'code', None)} {getattr(error, 'message', error)}"
            )
        refusal = _responses_refusal(raw)
        if refusal and not text:
            raise LLMError(f"{log_label or model}: the model refused: {refusal}")
    else:
        raise ValueError(
            f"Unknown S2S_OPENAI_API_MODE={api!r}; expected 'responses' or 'chat'."
        )

    result = LLMResult(
        text=(text or "").strip(),
        model=model,
        api=api,
        usage=usage,
        stripped_params=stripped,
        omitted_params=omitted,
        finish=finish,
        duration_s=round(time.monotonic() - started, 3),
    )
    _log_call(log_label, result)
    if result.finish and ("incomplete" in result.finish or result.finish == "length"):
        raise LLMError(
            f"{log_label or model}: output truncated ({result.finish}; "
            f"max_output_tokens={max_output_tokens}). Raise the S2S_*_MAX_OUTPUT_TOKENS "
            "setting of this agent or lower the reasoning effort."
        )
    return result


def embed_texts(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Embed ``texts`` with the OpenAI embeddings endpoint (batched)."""
    model = model or config.EMBEDDING_MODEL
    client = get_client()
    vectors: list[list[float]] = []
    batch_size = 256
    for start in range(0, len(texts), batch_size):
        batch = [
            text if text.strip() else " " for text in texts[start : start + batch_size]
        ]
        response = client.embeddings.create(model=model, input=batch)
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors.extend(list(item.embedding) for item in ordered)
    return vectors


# ---------------------------------------------------------------------------
# Request building
# ---------------------------------------------------------------------------
def _data_url(image_b64: str, mime: str) -> str:
    return f"data:{mime};base64,{image_b64}"


def _build_responses_kwargs(
    model,
    system_prompt,
    user_prompt,
    images_b64,
    image_mime,
    json_mode,
    json_schema,
    schema_name,
    max_output_tokens,
    temperature,
    reasoning_effort,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": user_prompt}]
    for image_b64 in images_b64:
        content.append(
            {
                "type": "input_image",
                "image_url": _data_url(image_b64, image_mime),
                "detail": config.IMAGE_DETAIL,
            }
        )
    kwargs: dict[str, Any] = {
        "model": model,
        "input": [{"role": "user", "content": content}],
    }
    if system_prompt:
        kwargs["instructions"] = system_prompt
    if not config.OPENAI_STORE:
        kwargs["store"] = False
    if max_output_tokens:
        kwargs["max_output_tokens"] = int(max_output_tokens)
    if temperature is not None:
        kwargs["temperature"] = float(temperature)
    if reasoning_effort:
        kwargs["reasoning"] = {"effort": reasoning_effort}
    if json_schema is not None:
        kwargs["text"] = {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "schema": json_schema,
                "strict": True,
            }
        }
    elif json_mode:
        kwargs["text"] = {"format": {"type": "json_object"}}
        ensure_json_word(kwargs)
    return kwargs


def _build_chat_kwargs(
    model,
    system_prompt,
    user_prompt,
    images_b64,
    image_mime,
    json_mode,
    json_schema,
    schema_name,
    max_output_tokens,
    temperature,
    seed,
    reasoning_effort,
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if images_b64:
        user_content: Any = [{"type": "text", "text": user_prompt}]
        for image_b64 in images_b64:
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": _data_url(image_b64, image_mime),
                        "detail": config.IMAGE_DETAIL,
                    },
                }
            )
    else:
        user_content = user_prompt
    messages.append({"role": "user", "content": user_content})

    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    if max_output_tokens:
        kwargs["max_completion_tokens"] = int(max_output_tokens)
    if temperature is not None:
        kwargs["temperature"] = float(temperature)
    if seed is not None:
        kwargs["seed"] = int(seed)
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    if json_schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "schema": json_schema,
                "strict": True,
            },
        }
    elif json_mode:
        kwargs["response_format"] = {"type": "json_object"}
        ensure_json_word(kwargs)
    return kwargs


# ---------------------------------------------------------------------------
# JSON mode: the API requires the word "json" in the prompt
# ---------------------------------------------------------------------------
# OpenAI rejects a json_object request unless the word "json" occurs in the
# messages. In the Responses API only the *input* messages are checked, not
# `instructions`, so a system prompt full of "JSON" does not count. The hint is
# appended to the user text only when the word is missing there.
_JSON_WORD_RE = re.compile(r"json", re.IGNORECASE)
JSON_MODE_HINT = "Respond with a single JSON object."


def _part_texts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and "text" in part
        ]
    return []


def _append_hint(message: dict[str, Any], text_type: str) -> None:
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == text_type:
                part["text"] = (
                    f"{str(part.get('text', '')).rstrip()}\n\n{JSON_MODE_HINT}"
                )
                return
        content.insert(0, {"type": text_type, "text": JSON_MODE_HINT})
        return
    message["content"] = f"{str(content or '').rstrip()}\n\n{JSON_MODE_HINT}".strip()


def ensure_json_word(kwargs: dict[str, Any]) -> bool:
    """Make a json_object request acceptable; returns True if the hint was added."""
    if "input" in kwargs:  # Responses API: only the input messages are checked
        messages, text_type = kwargs["input"], "input_text"
    elif "messages" in kwargs:  # Chat Completions: every message counts
        messages, text_type = kwargs["messages"], "text"
    else:
        return False
    if not isinstance(messages, list):
        return False
    for message in messages:
        if isinstance(message, dict) and any(
            _JSON_WORD_RE.search(text) for text in _part_texts(message.get("content"))
        ):
            return False
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            _append_hint(message, text_type)
            return True
    return False


# ---------------------------------------------------------------------------
# Unsupported-parameter fallback
# ---------------------------------------------------------------------------
def _offending_param(error: Exception, kwargs: dict[str, Any]) -> str | None:
    """Return the top-level kwarg a BadRequestError complains about, if any."""
    param = getattr(error, "param", None)
    code = getattr(error, "code", None)
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        error_obj = body.get("error", body)
        if isinstance(error_obj, dict):
            param = param or error_obj.get("param")
            code = code or error_obj.get("code")
    message = str(error)

    looks_unsupported = (code in _UNSUPPORTED_CODES) or bool(
        _UNSUPPORTED_MESSAGE_RE.search(message)
    )
    if not looks_unsupported:
        return None

    candidates: list[str] = []
    if isinstance(param, str) and param:
        candidates.append(param)
    candidates.extend(_PARAM_IN_MESSAGE_RE.findall(message))

    for candidate in candidates:
        top = re.split(r"[.\[]", candidate, maxsplit=1)[0]
        if top in kwargs and top in _STRIPPABLE_PARAMS:
            return top
    return None


def _downgrade_output_format(kwargs: dict[str, Any], param: str) -> bool:
    """json_schema -> json_object -> nothing. Returns True if a downgrade happened."""
    if param == "text":
        fmt = (kwargs.get("text") or {}).get("format") or {}
        if fmt.get("type") == "json_schema":
            kwargs["text"] = {"format": {"type": "json_object"}}
            ensure_json_word(kwargs)
            return True
        kwargs.pop("text", None)
        return False
    if param == "response_format":
        fmt = kwargs.get("response_format") or {}
        if fmt.get("type") == "json_schema":
            kwargs["response_format"] = {"type": "json_object"}
            ensure_json_word(kwargs)
            return True
        kwargs.pop("response_format", None)
        return False
    return False


def _error_code(error: Exception) -> str | None:
    code = getattr(error, "code", None)
    body = getattr(error, "body", None)
    if not code and isinstance(body, dict):
        error_obj = body.get("error", body)
        if isinstance(error_obj, dict):
            code = error_obj.get("code")
    return code


def _effort_value(kwargs: dict[str, Any], param: str) -> str | None:
    if param == "reasoning":
        reasoning = kwargs.get("reasoning")
        return reasoning.get("effort") if isinstance(reasoning, dict) else None
    return kwargs.get("reasoning_effort")


def _set_effort_value(kwargs: dict[str, Any], param: str, value: str) -> None:
    if param == "reasoning":
        reasoning = kwargs.get("reasoning")
        base = reasoning if isinstance(reasoning, dict) else {}
        kwargs["reasoning"] = {**base, "effort": value}
    else:
        kwargs["reasoning_effort"] = value


def _lower_effort(value: str | None) -> str | None:
    """The next lower level of the ladder, or None when there is none."""
    if value not in _EFFORT_LADDER:
        return None
    index = _EFFORT_LADDER.index(value) + 1
    return _EFFORT_LADDER[index] if index < len(_EFFORT_LADDER) else None


def _apply_effort_ceiling(kwargs: dict[str, Any], model: str, stripped: list[str]):
    """Start at the highest effort this model is known to accept."""
    ceiling = _effort_ceiling_by_model.get(model)
    if ceiling is None:
        return
    for param in ("reasoning", "reasoning_effort"):
        if param not in kwargs:
            continue
        current = _effort_value(kwargs, param)
        if current in _EFFORT_LADDER and _EFFORT_LADDER.index(
            current
        ) < _EFFORT_LADDER.index(ceiling):
            _set_effort_value(kwargs, param, ceiling)
            stripped.append(f"{param}:{current}->{ceiling}")


def _cap_param_in_error(error: Exception, kwargs: dict[str, Any]) -> str | None:
    """The output-cap kwarg an error is about (the API may name ``max_tokens``
    for a ``max_completion_tokens`` request), or None."""
    param = getattr(error, "param", None)
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        error_obj = body.get("error", body)
        if isinstance(error_obj, dict):
            param = param or error_obj.get("param")
    message = str(error)
    if not (
        param in _OUTPUT_CAP_PARAMS or any(n in message for n in _OUTPUT_CAP_PARAMS)
    ):
        return None
    return next((name for name in _OUTPUT_CAP_PARAMS if name in kwargs), None)


def _clamp_output_cap(
    error: Exception, kwargs: dict[str, Any], model: str, stripped: list[str]
) -> bool:
    """A cap above the maximum of the model: clamp it to the maximum the API
    names, remember it for the model and log the change. Returns True when
    the request should be retried."""
    param = _cap_param_in_error(error, kwargs)
    if param is None:
        return False
    match = _CAP_CEILING_RE.search(str(error))
    current = kwargs.get(param)
    if match is None or not isinstance(current, int):
        return False
    ceiling = int(match.group(1))
    if ceiling <= 0 or ceiling >= current:
        return False
    kwargs[param] = ceiling
    _output_cap_ceiling_by_model[model] = ceiling
    stripped.append(f"{param}:{current}->{ceiling}")
    print(
        f"[llm] {model}: {param}={current} is above the maximum of the model, "
        f"retrying with {ceiling}"
    )
    return True


def _apply_output_cap_ceiling(kwargs: dict[str, Any], model: str, stripped: list[str]):
    """Start under the output cap this model is known to accept."""
    ceiling = _output_cap_ceiling_by_model.get(model)
    if ceiling is None:
        return
    for param in _OUTPUT_CAP_PARAMS:
        current = kwargs.get(param)
        if isinstance(current, int) and current > ceiling:
            kwargs[param] = ceiling
            stripped.append(f"{param}:{current}->{ceiling}")


def _invoke_with_param_fallback(call, kwargs: dict[str, Any], model: str):
    from openai import BadRequestError

    known_unsupported = _unsupported_by_model.setdefault(model, set())
    stripped: list[str] = []
    for param in list(kwargs):
        if param in known_unsupported:
            value = kwargs.pop(param)
            if param == "max_completion_tokens" and "max_tokens" not in kwargs:
                kwargs["max_tokens"] = value  # legacy gateways
            stripped.append(param)
    _apply_effort_ceiling(kwargs, model, stripped)
    _apply_output_cap_ceiling(kwargs, model, stripped)

    for _ in range(len(_STRIPPABLE_PARAMS) + len(_EFFORT_LADDER) + 3):
        try:
            return call(**kwargs), stripped
        except BadRequestError as error:
            param = _offending_param(error, kwargs)
            if param is None:
                if _clamp_output_cap(error, kwargs, model, stripped):
                    continue
                raise
            if param in {"text", "response_format"}:
                if _downgrade_output_format(kwargs, param):
                    stripped.append(f"{param}:json_schema->json_object")
                    continue
                stripped.append(param)
                continue
            if (
                param in {"reasoning", "reasoning_effort"}
                and _error_code(error) != "unsupported_parameter"
            ):
                # The model supports reasoning controls but not this effort
                # level: step down one level, remember the ceiling for this
                # model and record the change in the call log. Only when the
                # ladder is exhausted is the parameter dropped like any other.
                current = _effort_value(kwargs, param)
                lower = _lower_effort(current)
                if lower is not None:
                    _set_effort_value(kwargs, param, lower)
                    _effort_ceiling_by_model[model] = lower
                    stripped.append(f"{param}:{current}->{lower}")
                    print(
                        f"[llm] {model}: reasoning effort {current!r} rejected, "
                        f"retrying with {lower!r}"
                    )
                    continue
            value = kwargs.pop(param, None)
            known_unsupported.add(param)
            stripped.append(param)
            if param == "max_completion_tokens" and "max_tokens" not in kwargs:
                kwargs["max_tokens"] = value  # keep the output cap on legacy gateways
            print(
                f"[llm] model {model!r} rejected {param!r}; retrying without it "
                f"({str(error).splitlines()[0][:160]})"
            )
    raise LLMError(f"Could not complete request for model {model!r}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text", "")))
            else:
                parts.append(str(getattr(part, "text", "") or ""))
        return "".join(parts)
    return str(content)


def _responses_refusal(raw: Any) -> str | None:
    for item in getattr(raw, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "refusal":
                return getattr(part, "refusal", None) or "refusal"
    return None


def _usage_dict(raw: Any) -> dict[str, Any]:
    usage = getattr(raw, "usage", None)
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        try:
            return usage.model_dump(exclude_none=True)
        except Exception:
            pass
    return dict(getattr(usage, "__dict__", {}))


def _log_call(label: str | None, result: LLMResult) -> None:
    if not _call_log_path:
        return
    try:
        os.makedirs(os.path.dirname(_call_log_path) or ".", exist_ok=True)
        record = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "label": label,
            "model": result.model,
            "api": result.api,
            "duration_s": result.duration_s,
            "finish": result.finish,
            "stripped_params": result.stripped_params,
            "omitted_params": result.omitted_params,
            "usage": result.usage,
        }
        with open(_call_log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except Exception as error:  # logging must never break a run
        print(f"[llm] could not write call log: {error}")
