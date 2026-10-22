"""Shared fixtures: a deterministic fake of the OpenAI layer.

No network access and no API key are needed to run the test-suite. The fake
LLM answers each agent from fixtures (case study 1, the desalting process) or
by deterministic generation from the prompt contents, which also exercises the
prompt/response contracts (markers, code fences, JSON) of every agent.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pytest

from sketch2sim import llm

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "sketch2sim" / "instructions" / "hysys_template_base.py"


def _json_between(prompt: str, start_marker: str, end_marker: str) -> dict:
    start = prompt.index(start_marker) + len(start_marker)
    end = prompt.index(end_marker, start)
    return json.loads(prompt[start:end].strip())


def fake_basis_output(user_prompt: str) -> str:
    """Fill the template the way the Basis Agent is asked to."""
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    code = template.replace(
        'case_name = "FILL_CASE_NAME"', 'case_name = "Crude_Oil_Desalting"'
    )
    code = code.replace(
        'property_package = "FILL_PROPERTY_PACKAGE"',
        'property_package = "PengRob"',
    )
    step3 = (
        "# === Step 3: Add Components ===\n"
        "components = [\n"
        '    "Crude oil",\n'
        '    "Water",\n'
        '    "Demulsifier agent",\n'
        "]\n\n"
        'if "Crude oil" in components:\n'
        '    components.remove("Crude oil")\n'
        '    components.extend(["n-Heptane", "n-Hexane", "i-Pentane", "n-Pentane", "n-Butane", "Toluene", "Benzene"])\n\n'
        'if "Water" in components:\n'
        '    components.remove("Water")\n'
        '    components.extend(["Water"])\n\n'
        'if "Demulsifier agent" in components:\n'
        '    components.remove("Demulsifier agent")\n'
        '    components.extend(["Nonylphenol", "Tetraethyleneglycol"])\n\n'
        "for comp in components:\n"
        "    fluidpkg.Components.Add(comp)\n"
        "hybasis.EndBasisChange()\n"
        "flowsheet = case.Flowsheet\n\n"
    )
    # The real model is told to stop right before the "# === Step 3b" marker,
    # so the fake answers with Steps 1-3 only (no helper block, no Step 4).
    code = code[: code.index("# === Step 3b")]
    code = re.sub(
        r"(?s)# === Step 3: Add Components ===.*\Z", lambda _: step3, code, count=1
    )
    return "```python\n" + code.rstrip() + "\n```"


_CATEGORY_BY_TAG = [
    ("mixer", "mixer"),  # before valve: normaliser ids like MIXER_TO_MIXING_VALVE
    ("tee", "tee"),
    ("pump", "pump"),
    ("compressor", "compressor"),
    ("valve", "valve"),
    ("fired heater", "fired_heater"),
    ("furnace", "fired_heater"),
    ("heat exchanger", "heater"),
    ("cooler", "cooler"),
    ("heater", "heater"),
    ("column", "column_distillation"),
    ("reactor", "reactor_conversion"),
    ("tank", "tank"),
    ("drum", "tank"),
]


def _category(unit: dict) -> str:
    text = " ".join(
        [unit.get("id", ""), unit.get("name", "")] + list(unit.get("tags", []))
    ).lower()
    for key, category in _CATEGORY_BY_TAG:
        if key in text:
            return category
    return "separator"


def fake_instantiation_output(user_prompt: str) -> str:
    structure = _json_between(
        user_prompt, "STRUCTURED_JSON:\n----------------\n", "\n\nCURRENT PYTHON SCRIPT"
    )
    lines = ["# === Step 4: Add Unit Operations (Agent 2) ===", "units = {}"]
    for unit in structure["units"]:
        lines.append(
            f'add_unit("{unit["id"]}", "{unit["name"]}", TYPES["{_category(unit)}"])'
        )
    lines += ["", "# === Step 5: Add Material Streams (Agent 2) ===", "streams = {}"]
    for group in ("feed_streams", "intermediate_streams", "product_streams"):
        for stream in structure.get(group, []):
            name = stream.get("name") or stream["id"]
            lines.append(f'add_stream("{stream["id"]}", "{name}")')
    return "```python\n" + "\n".join(lines) + "\n```"


_GAS_WORDS = ("gas", "vapour", "vapor", "vent", "off-gas", "purge", "overhead")
_WATER_WORDS = ("water", "aqueous", "brine")


def _is_column(unit: dict) -> bool:
    text = " ".join([unit.get("unit_name", "")] + list(unit.get("tags", []))).lower()
    return "column" in text or "tower" in text or "hysys_overrides" in unit


def fake_configuration_output(user_prompt: str) -> str:
    unit_view = _json_between(
        user_prompt, "STRUCTURED_JSON:\n----------------\n", "\n\nIMPORTANT:"
    )
    lines = ["# === Step 6: Connect Streams (Agent 3) ==="]
    for unit in unit_view["units_unit_view"]:
        key = unit["unit_id"]
        lines.append(f"# --- {key} ({unit['unit_name']}) ---")
        text = " ".join(
            [unit.get("unit_name", "")] + list(unit.get("tags", []))
        ).lower()
        if _is_column(unit):
            for stream in unit["streams_in"]:
                lines.append(f'connect_column_feed("{key}", "{stream["id"]}")')
            for stream in unit["streams_out"]:
                name = stream.get("name", "").lower()
                point = stream.get("connect_point")
                if point == "side" and not stream.get("integrated_via"):
                    role = "side_draw"
                elif any(w in name for w in _GAS_WORDS):
                    role = "overhead_vapour"
                elif point == "bottom" and any(w in name for w in _WATER_WORDS):
                    role = "water_draw"
                elif point == "bottom":
                    role = "bottoms"
                else:
                    role = "distillate"
                lines.append(
                    f'connect_column_product("{key}", "{stream["id"]}", "{role}")'
                )
            continue
        single = any(
            w in text
            for w in ("pump", "valve", "compressor", "heater", "cooler", "exchanger")
        )
        mixer = "mixer" in text
        for stream in unit["streams_in"]:
            role = "single_in" if single else "multi_in"
            lines.append(f'connect("{key}", "{stream["id"]}", "{role}")')
        for stream in unit["streams_out"]:
            if single:
                role = "single_out"
            elif mixer:
                role = "mixer_out"
            else:
                role = (
                    "vapour_out"
                    if stream.get("connect_point") == "overhead"
                    else "liquid_out"
                )
            lines.append(f'connect("{key}", "{stream["id"]}", "{role}")')
    return "\n".join(lines)


class FakeLLM:
    """Stands in for ``sketch2sim.llm.chat``; records every call.

    ``case`` selects which case-study fixture the descriptor/extractor return
    (cs1 by default; cs2-cs4 reuse the cs1 description text).
    """

    def __init__(self, case: str = "cs1"):
        self.calls: list[dict] = []
        self.case = case

    def __call__(self, model, system_prompt, user_prompt, **kwargs):
        label = kwargs.get("log_label")
        self.calls.append(
            {
                "label": label,
                "model": model,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "kwargs": kwargs,
            }
        )
        if label == "descriptor":
            return (FIXTURES / "cs1_description.txt").read_text(encoding="utf-8")
        if label == "description_validator":
            return json.dumps(
                {
                    "status": "ok",
                    "issues": [],
                    "suggested_fix_mode": "minor_edits",
                    "short_feedback_for_upstream_model": "",
                    "cleaned_description": "",
                }
            )
        if label == "extractor":
            return (FIXTURES / f"{self.case}_extraction.json").read_text(
                encoding="utf-8"
            )
        if label == "basis":
            return fake_basis_output(user_prompt)
        if label == "instantiation":
            return fake_instantiation_output(user_prompt)
        if label == "configuration":
            return fake_configuration_output(user_prompt)
        if label == "fixer":
            return user_prompt.split("[CODE_PY]\n<<<\n", 1)[1].rsplit("\n>>>", 1)[0]
        raise AssertionError(f"unexpected LLM call label {label!r}")


def fake_embed_texts(texts, model=None):
    """Deterministic pseudo-embeddings (hash seeded), 32 dimensions."""
    vectors = []
    for text in texts:
        seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        vectors.append(rng.normal(size=32).tolist())
    return vectors


@pytest.fixture
def fake_llm(monkeypatch, tmp_path):
    fake = FakeLLM()
    monkeypatch.setattr(llm, "chat", fake)
    monkeypatch.setattr(llm, "embed_texts", fake_embed_texts)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-offline")
    llm.reset_client()

    # Keep the RAG embedding cache out of the repository during tests.
    from sketch2sim.agents import basis_agent
    from sketch2sim.RAG import rag_components

    monkeypatch.setattr(
        basis_agent,
        "build_component_retriever",
        lambda **kwargs: rag_components.build_component_retriever(
            persist_dir=tmp_path / "rag_cache", **kwargs
        ),
    )
    yield fake
    llm.reset_client()


@pytest.fixture
def repo_cwd(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    return REPO_ROOT


@pytest.fixture
def cs1_extraction():
    return json.loads((FIXTURES / "cs1_extraction.json").read_text(encoding="utf-8"))


def write_dummy_instruction(path: Path, title: str) -> Path:
    path.write_text(
        f"{title}\nUse flowsheet.Operations.Add(name, type).\n", encoding="utf-8"
    )
    return path


@pytest.fixture(autouse=True)
def _pinned_config(monkeypatch):
    """Tests must not depend on the developer's .env or shell environment."""
    from sketch2sim import config

    monkeypatch.setattr(config, "OPENAI_API_MODE", "responses")
    monkeypatch.setattr(config, "OPENAI_STORE", False)
    monkeypatch.setattr(config, "LLM_TEMPERATURE", 0.0)
    monkeypatch.setattr(config, "LLM_SEED", 42)
    monkeypatch.setattr(config, "LLM_REASONING_EFFORT", "medium")
    monkeypatch.setattr(config, "IMAGE_DETAIL", "high")
    monkeypatch.setattr(config, "PYTHON_EXECUTABLE", "")
