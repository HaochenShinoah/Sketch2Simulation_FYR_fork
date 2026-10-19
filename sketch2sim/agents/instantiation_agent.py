"""Instantiation Agent (B2): unit operations and material streams (Steps 4-5)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableLambda
from langgraph.graph import StateGraph
from pydantic import BaseModel

from sketch2sim import config, llm

_STEP4_MARKER = "# === Step 4: Add Unit Operations (Agent 2) ==="
_STEP6_MARKER = "# === Step 6: Connect Streams (Agent 3) ==="


class InstantiationState(BaseModel):
    description: str
    basis_path: str
    instruction_paths: list[str]
    python_code: str | None = None
    summary: str | None = None
    output_path: str | None = None
    prompt_debug: str | None = None
    raw_output: str | None = None


def _load_text(path: str) -> str:
    with open(path, encoding="utf-8") as file:
        return file.read()


def _load_json_text(maybe_path_or_json: str) -> str:
    text = (maybe_path_or_json or "").strip()
    if text and os.path.exists(text):
        return Path(text).read_text(encoding="utf-8", errors="ignore")
    return text


def _load_instructions(paths: list[str]) -> str:
    if not paths:
        return ""

    parts: list[str] = []
    for path in paths:
        if path and os.path.exists(path):
            parts.append(_load_text(path).strip())
        elif path:
            raise FileNotFoundError(f"Instruction file not found: {path}")

    return "\n\n".join(parts)


def _extract_code(text: str) -> str:
    blocks = re.findall(
        r"```(?:python|py)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE
    )
    return (
        "\n\n".join(block.strip() for block in blocks if block.strip())
        if blocks
        else text.strip()
    )


def _extract_steps_4_to_5_only(text: str) -> str:
    pattern = (
        r"(?s)"
        r"(# === Step 4: Add Unit Operations \(Agent 2\) ===.*?)"
        r"(?=# === Step 6: Connect Streams \(Agent 3\) ===|\Z)"
    )
    match = re.search(pattern, text)
    if not match:
        raise ValueError("Could not extract Step 4–5 block from Instantiation output.")

    block = match.group(1).strip()

    for bad_marker in (
        "# === Step 1:",
        "# === Step 2:",
        "# === Step 3:",
        "# === Step 6:",
        "# === Step 7:",
    ):
        if bad_marker in block:
            raise ValueError(
                f"Extracted Step 4–5 block still contains out-of-scope marker: {bad_marker}"
            )

    return block


def _structure_ids(structured_json_text: str) -> tuple[list[str], list[str]]:
    try:
        data = json.loads(structured_json_text)
    except Exception:
        return [], []
    units = [
        u.get("id")
        for u in data.get("units", [])
        if isinstance(u, dict) and u.get("id")
    ]
    streams = [
        s.get("id")
        for group in ("feed_streams", "intermediate_streams", "product_streams")
        for s in data.get(group, [])
        if isinstance(s, dict) and s.get("id")
    ]
    return units, streams


def _validate_block(block: str, structured_json_text: str) -> None:
    """Every unit and stream of the structure must be created exactly once."""
    unit_ids, stream_ids = _structure_ids(structured_json_text)
    if not unit_ids and not stream_ids:
        return
    added_units = re.findall(r'add_unit\(\s*"((?:[^"\\]|\\.)*)"', block)
    added_streams = re.findall(r'add_stream\(\s*"((?:[^"\\]|\\.)*)"', block)
    problems = []
    missing_units = [u for u in unit_ids if u not in added_units]
    extra_units = [u for u in added_units if u not in unit_ids]
    missing_streams = [s for s in stream_ids if s not in added_streams]
    extra_streams = [s for s in added_streams if s not in stream_ids]
    if missing_units:
        problems.append(f"units without add_unit: {missing_units}")
    if extra_units:
        problems.append(f"add_unit keys not in STRUCTURED_JSON: {extra_units}")
    if len(added_units) != len(set(added_units)):
        problems.append("duplicate add_unit keys")
    if missing_streams:
        problems.append(f"streams without add_stream: {missing_streams}")
    if extra_streams:
        problems.append(f"add_stream keys not in STRUCTURED_JSON: {extra_streams}")
    if len(added_streams) != len(set(added_streams)):
        problems.append("duplicate add_stream keys")
    if problems:
        raise ValueError("Instantiation output is incomplete: " + "; ".join(problems))


def _build_system_prompt() -> str:
    return (
        "You are the Instantiation Agent. Fill only Steps 4–5 of the provided HYSYS Python script.\n\n"
        "Rules:\n"
        "1. Do not change anything outside Steps 4–5.\n"
        "2. Use only units listed in STRUCTURED_JSON.units.\n"
        "3. Streams must use the exact names from feed_streams, intermediate_streams, and product_streams; "
        "if a name is empty or duplicated, use the stream id as the name.\n"
        "4. Use only valid HYSYS COM types listed in the instruction block.\n"
        "5. Do not connect streams to units in this step.\n"
        "6. Return only a single Python code block that starts with:\n"
        f"{_STEP4_MARKER}\n"
        "and ends right before:\n"
        f"{_STEP6_MARKER}\n"
        "7. No explanations.\n"
    )


def _build_user_prompt(description: str, basis_code: str, instructions: str) -> str:
    return (
        "STRUCTURED_JSON:\n"
        "----------------\n"
        f"{description}\n\n"
        "CURRENT PYTHON SCRIPT (BASIS OUTPUT):\n"
        "----------------\n"
        "```python\n"
        f"{basis_code}\n"
        "```\n\n"
        "INSTRUCTION BLOCK:\n"
        "----------------\n"
        "[INSTRUCTIONS START]\n"
        f"{instructions}\n"
        "[INSTRUCTIONS END]\n"
    ).strip()


def _replace_steps_4_to_5(basis_code: str, step_4_to_5_code: str) -> str:
    pattern = r"(?s)# === Step 4:.*?# === Step 6: Connect Streams \(Agent 3\) ==="
    if not re.search(pattern, basis_code):
        raise ValueError("Could not find Step 4–5 block markers in Basis code.")

    replacement = (
        step_4_to_5_code.rstrip() + "\n\n# === Step 6: Connect Streams (Agent 3) ==="
    )
    return re.sub(pattern, lambda _: replacement, basis_code, count=1)


def generate_instantiation_script(
    state: InstantiationState, model_name: str
) -> dict[str, Any]:
    basis_code = _load_text(state.basis_path)
    instructions = _load_instructions(state.instruction_paths)
    structured_json_text = _load_json_text(state.description)

    system_prompt = _build_system_prompt()
    user_prompt = _build_user_prompt(
        description=structured_json_text,
        basis_code=basis_code,
        instructions=instructions,
    )

    raw_output = llm.chat(
        model_name,
        system_prompt,
        user_prompt,
        max_output_tokens=config.INSTANTIATION_MAX_OUTPUT_TOKENS,
        log_label="instantiation",
    )
    code_text = _extract_code(raw_output)

    try:
        step_4_to_5_code = _extract_steps_4_to_5_only(code_text)
        _validate_block(step_4_to_5_code, structured_json_text)
    except ValueError:
        dump_path = os.path.join(
            os.path.dirname(state.basis_path),
            "instantiation_failed_extract__code_text.txt",
        )
        with open(dump_path, "w", encoding="utf-8") as file:
            file.write(code_text)
        raise

    full_script = _replace_steps_4_to_5(basis_code, step_4_to_5_code)

    return {
        "python_code": full_script,
        "raw_output": raw_output,
        "prompt_debug": system_prompt + "\n\n" + user_prompt,
        "summary": "Instantiation script generated and merged into full file.",
        "output_path": None,
    }


def build_instantiation_graph(model_name: str):
    graph = StateGraph(InstantiationState)
    graph.add_node(
        "instantiation",
        RunnableLambda(lambda state: generate_instantiation_script(state, model_name)),
    )
    graph.set_entry_point("instantiation")
    graph.set_finish_point("instantiation")
    return graph.compile()
