"""Central configuration for the Sketch2Simulation pipeline.

Every language-model and embedding call in the pipeline goes through the OpenAI
API (see ``sketch2sim/llm.py``). Model names and decoding parameters are read
from environment variables so that a replication run can be re-pointed at a
different model, endpoint or budget without touching code.

Environment variables
---------------------
Variables may also be placed in a ``.env`` file (``KEY=value`` lines) in the
working directory or in the repository root; both are read when this module
is imported, without overriding variables that are already set.

Required
    OPENAI_API_KEY              API key used by every agent.

Optional (connection)
    OPENAI_BASE_URL             Alternative OpenAI-compatible endpoint.
    OPENAI_ORG_ID / OPENAI_PROJECT_ID
    S2S_OPENAI_API_MODE         "responses" (default) or "chat"
                                (Chat Completions, for compatible gateways).
    S2S_OPENAI_STORE            "1" lets the Responses API store responses
                                server-side (default "0": stateless).
    S2S_LLM_TIMEOUT             Per-request timeout in seconds (default 3600:
                                a call at reasoning effort "max" can run long).
    S2S_LLM_MAX_RETRIES         SDK retries on transient errors (default 3).

Optional (models; one per agent)
    S2S_DESCRIPTOR_MODEL, S2S_DESCRIPTION_VALIDATOR_MODEL, S2S_EXTRACTOR_MODEL,
    S2S_BASIS_MODEL, S2S_INSTANTIATION_MODEL, S2S_CONFIGURATION_MODEL,
    S2S_FIXER_MODEL, S2S_EMBEDDING_MODEL

Optional (decoding)
    S2S_LLM_TEMPERATURE         Default 0.0. Not sent when a reasoning effort
                                other than "none" is in force (reasoning models
                                reject it); any other rejection is detected and
                                the parameter dropped for that model.
    S2S_LLM_SEED                Default 42 (Chat Completions only); "" or
                                "off" disables it.
    S2S_LLM_REASONING_EFFORT    Effort sent to reasoning-capable models
                                (default "max", the highest level the API
                                offers; a model that rejects a level is
                                retried one level lower, see llm.py; set ""
                                to send nothing).
    S2S_*_MAX_OUTPUT_TOKENS     Per-agent output caps (see below). A cap above
                                the maximum of the model is clamped to the
                                maximum the API reports (llm.py) and logged.

Optional (misc)
    DESCRIPTOR_MAX_IMAGE_WIDTH  Sketch is downscaled to this width (2048).
    S2S_IMAGE_DETAIL            Image detail sent to the vision model (high).
    S2S_EXECUTOR_TIMEOUT        HYSYS script timeout in seconds (600).
    S2S_PYTHON_EXECUTABLE       Interpreter used to run the generated script
                                (default: the current interpreter).

Read by the generated script (set by the executor or by you)
    S2S_HSC_DIR                 Folder where the .hsc and the topology report
                                are written (the executor sets the output dir).
    S2S_HSC_SUFFIX              Suffix of the case file name; the executor
                                sets "_attemptN" so every attempt keeps its case.
    S2S_HYSYS_KEEP_OPEN         "0" (default): every script quits the HYSYS
                                instance it started (open the saved .hsc to
                                look at the flowsheet). "1" leaves the
                                instance of a successful script open; close it
                                before the next run, or the next script
                                attaches to it instead of starting its own.
    S2S_ATTEMPT                 Attempt number, set by the executor for the
                                "[hysys] attempt N" log line.
    S2S_STRICT_CONNECTIONS      "1" (default): a connection that could not be
                                made exits with code 2; "0" only reports it.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path = ".env", override: bool = False) -> dict[str, str]:
    """Load ``KEY=value`` lines from a .env file into ``os.environ``.

    Existing variables win unless ``override`` is True. Returns the values
    that were loaded. Called once at import time for the ``.env`` file in the
    current working directory, so a notebook or CLI started from the repository
    root picks up ``OPENAI_API_KEY`` and ``S2S_*`` overrides automatically.
    """
    loaded: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.is_file():
        return loaded
    raw = env_path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")  # PowerShell 5.1 `echo > .env` writes UTF-16
    else:
        text = raw.decode("utf-8-sig", errors="replace")  # BOM from Notepad is fine
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] in {'"', "'"}:
            # Quoted value: everything up to the closing quote; a trailing
            # comment after the closing quote is ignored.
            closing = value.find(value[0], 1)
            value = value[1:closing] if closing != -1 else value[1:]
        else:
            value = value.split(" #", 1)[0].split("\t#", 1)[0].strip()  # inline comment
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


# The working directory first, then the repository root (where the README
# tells users to put the file), without overriding variables already set.
load_dotenv()
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None else value.strip()


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return float(value)


def _env_optional_int(name: str, default: int | None) -> int | None:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    if not value or value.lower() in {"none", "null", "off"}:
        return None
    return int(value)


def _env_optional_float(name: str, default: float | None) -> float | None:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    if not value or value.lower() in {"none", "null", "off"}:
        return None
    return float(value)


# ---------------------------------------------------------------------------
# Models (OpenAI platform defaults; override per agent through the environment)
# ---------------------------------------------------------------------------
# Every agent runs on GPT-6 Astra ("gpt-6-astra", the id listed by openai SDK
# 3.35 in October 2026); the four roles below are kept so that a cheaper or
# different model can still be assigned per role through the environment.
# Interpretation layer (sketch + text; needs image input).
DEFAULT_VISION_MODEL = "gpt-6-astra"
# Code synthesis for the simpler agents (basis, instantiation).
DEFAULT_CODE_MODEL = "gpt-6-astra"
# Harder code reasoning (configuration, fixer).
DEFAULT_REASONING_CODE_MODEL = "gpt-6-astra"
# Auxiliary description validator (text only).
DEFAULT_LIGHT_MODEL = "gpt-6-astra"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"

DESCRIPTOR_MODEL = _env_str("S2S_DESCRIPTOR_MODEL", DEFAULT_VISION_MODEL)
DESCRIPTION_VALIDATOR_MODEL = _env_str(
    "S2S_DESCRIPTION_VALIDATOR_MODEL", DEFAULT_LIGHT_MODEL
)
EXTRACTOR_MODEL = _env_str("S2S_EXTRACTOR_MODEL", DEFAULT_VISION_MODEL)
BASIS_MODEL = _env_str("S2S_BASIS_MODEL", DEFAULT_CODE_MODEL)
INSTANTIATION_MODEL = _env_str("S2S_INSTANTIATION_MODEL", DEFAULT_CODE_MODEL)
CONFIGURATION_MODEL = _env_str("S2S_CONFIGURATION_MODEL", DEFAULT_REASONING_CODE_MODEL)
FIXER_MODEL = _env_str("S2S_FIXER_MODEL", CONFIGURATION_MODEL)
EMBEDDING_MODEL = _env_str("S2S_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
OPENAI_API_MODE = _env_str("S2S_OPENAI_API_MODE", "responses").lower()
# Responses API: keep nothing on OpenAI's servers (the API stores responses for
# 30 days by default). Set S2S_OPENAI_STORE=1 to opt in.
OPENAI_STORE = _env_str("S2S_OPENAI_STORE", "0").lower() in {"1", "true", "yes"}
# A single call at reasoning effort "max" can think for a long time before it
# answers, so the per-request timeout is an hour.
LLM_TIMEOUT = _env_float("S2S_LLM_TIMEOUT", 3600.0)
LLM_MAX_RETRIES = _env_int("S2S_LLM_MAX_RETRIES", 3)

# ---------------------------------------------------------------------------
# Decoding (deterministic settings of the paper: temperature 0, seed 42)
# ---------------------------------------------------------------------------
LLM_TEMPERATURE: float | None = _env_optional_float("S2S_LLM_TEMPERATURE", 0.0)
LLM_SEED: int | None = _env_optional_int("S2S_LLM_SEED", 42)
# "max" is the top of the API's ladder (none, minimal, low, medium, high,
# xhigh, max); llm.py steps down one level when a model rejects a level.
LLM_REASONING_EFFORT = _env_str("S2S_LLM_REASONING_EFFORT", "max") or None

# Output caps. The original pipeline used num_predict=5000 for the code
# agents. With a reasoning model at effort "max" the cap also covers the
# reasoning tokens, which can run to tens of thousands per call (the
# descriptor hit a cap of 8000 on cs1), and the Fixer echoes the whole script,
# so the caps are sized for the visible answer plus a long think. They are
# upper bounds, not budgets: a short answer costs no more. A call that hits
# its cap raises instead of returning a truncated answer; a cap above the
# maximum of the model is clamped by llm.py to the maximum the API reports.
DESCRIPTOR_MAX_OUTPUT_TOKENS = _env_int("S2S_DESCRIPTOR_MAX_OUTPUT_TOKENS", 32000)
DESCRIPTION_VALIDATOR_MAX_OUTPUT_TOKENS = _env_int(
    "S2S_DESCRIPTION_VALIDATOR_MAX_OUTPUT_TOKENS", 16000
)
EXTRACTOR_MAX_OUTPUT_TOKENS = _env_int("S2S_EXTRACTOR_MAX_OUTPUT_TOKENS", 48000)
BASIS_MAX_OUTPUT_TOKENS = _env_int("S2S_BASIS_MAX_OUTPUT_TOKENS", 32000)
INSTANTIATION_MAX_OUTPUT_TOKENS = _env_int("S2S_INSTANTIATION_MAX_OUTPUT_TOKENS", 48000)
CONFIGURATION_MAX_OUTPUT_TOKENS = _env_int("S2S_CONFIGURATION_MAX_OUTPUT_TOKENS", 64000)
FIXER_MAX_OUTPUT_TOKENS = _env_int("S2S_FIXER_MAX_OUTPUT_TOKENS", 96000)

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
DESCRIPTOR_MAX_IMAGE_WIDTH = _env_int("DESCRIPTOR_MAX_IMAGE_WIDTH", 2048)
IMAGE_DETAIL = _env_str("S2S_IMAGE_DETAIL", "high")
EXECUTOR_TIMEOUT = _env_int("S2S_EXECUTOR_TIMEOUT", 600)
PYTHON_EXECUTABLE = _env_str("S2S_PYTHON_EXECUTABLE", "")


def model_table() -> dict[str, str]:
    """Models in use, keyed by agent name (for logging and the README)."""
    return {
        "descriptor": DESCRIPTOR_MODEL,
        "description_validator": DESCRIPTION_VALIDATOR_MODEL,
        "extractor": EXTRACTOR_MODEL,
        "basis": BASIS_MODEL,
        "instantiation": INSTANTIATION_MODEL,
        "configuration": CONFIGURATION_MODEL,
        "fixer": FIXER_MODEL,
        "embeddings": EMBEDDING_MODEL,
    }
