"""Configuration Agent (B3): connect material streams to unit ports (Step 6)."""

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

_STEP6_HEADER_RE = re.compile(
    r"(?im)^\s*#\s*===\s*Step\s*6:\s*Connect\s*Streams\s*\(Agent\s*3\)\s*===\s*$"
)
_STEP7_HEADER_RE = re.compile(
    r"(?im)^\s*#\s*===\s*Step\s*7:\s*Save\s*Simulation\s*===\s*$"
)


class ConfigurationState(BaseModel):
    description: str
    instantiation_path: str
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


def _extract_first_code_block(text: str) -> str:
    if not text:
        return ""

    match = re.search(
        r"```(?:python|py)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE
    )
    return match.group(1).strip() if match else text.strip()


def _extract_step_6_only(code: str) -> str:
    if not code:
        return ""

    code = code.replace("\r\n", "\n")
    step_6_match = _STEP6_HEADER_RE.search(code)
    if not step_6_match:
        return code.strip()

    start = step_6_match.start()
    step_7_match = _STEP7_HEADER_RE.search(code, pos=step_6_match.end())
    end = step_7_match.start() if step_7_match else len(code)

    return code[start:end].strip()


def _extract_code(text: str) -> str:
    return _extract_step_6_only(_extract_first_code_block(text))


def _build_system_prompt() -> str:
    return (
        "You are the Configuration Agent.\n\n"
        "Your only task is to connect streams exactly as defined in the provided units_unit_view JSON.\n\n"
        "Rules:\n"
        "1. For each unit in units_unit_view:\n"
        "   - For every item in streams_out, connect this stream as an outlet of the current unit "
        "(to_unit is only informative; null means it is a product leaving the process).\n"
        "   - For every item in streams_in, connect this stream as an inlet of the current unit "
        "(from_unit is only informative; null means it is a feed entering the process).\n"
        "   - A stream that links two units therefore appears twice: once as outlet of its source, once as inlet of its destination.\n"
        "2. Do not create any connection that is not explicitly listed in the JSON.\n"
        "3. Do not skip any connection that is explicitly listed in the JSON, except where the "
        "instruction block tells you to record it with skip(...) because the unit has no free port.\n"
        "4. Do not create new streams or new units. The one exception is the energy "
        "stream of a reactor: after the streams of every unit created with "
        'TYPES["reactor_..."] add exactly one line connect_energy("<unit_id>") '
        "(instruction block, section 4.4); such lines are not counted as stream "
        "connections.\n\n"
        "Implementation:\n"
        "- Use the existing unit and stream objects already created in the Instantiation script.\n"
        "- Match objects using their IDs.\n"
        "- Only append connection code.\n"
        "- For vessels and reactors use connect_point (and, for side/unknown, the stream name) to "
        "choose the outlet port as described in the instruction block; single-port units ignore "
        "connect_point; for columns follow its column section.\n\n"
        "Output:\n"
        "- Return only the Step 6 block: it starts with the exact line\n"
        "# === Step 6: Connect Streams (Agent 3) ===\n"
        "  and ends right before the line '# === Step 7: Save Simulation ===' (do not write that line).\n"
        "- No explanations. No markdown fences. No extra text. Do not repeat Steps 1-5 or 7.\n"
    ).strip()


def _build_user_prompt(
    description: str, instantiation_code: str, instructions: str
) -> str:
    return (
        "STRUCTURED_JSON:\n"
        "----------------\n"
        f"{description}\n\n"
        "IMPORTANT:\n"
        "- Only create connections that are explicitly listed in STRUCTURED_JSON.units_unit_view "
        "(every item of streams_in and streams_out of every unit).\n"
        "- If units_unit_view is missing or empty, do not connect anything.\n\n"
        "CURRENT PYTHON SCRIPT (AFTER INSTANTIATION):\n"
        "----------------\n"
        "```python\n"
        f"{instantiation_code}\n"
        "```\n\n"
        "INSTRUCTION BLOCK:\n"
        "----------------\n"
        "[INSTRUCTIONS START]\n"
        f"{instructions}\n"
        "[INSTRUCTIONS END]\n"
    ).strip()


_CONNECT_CALL_RE = re.compile(
    r"(?:connect|connect_column_feed|connect_column_product|skip)\(\s*"
    r'"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"'
)
_ROLE_RE = re.compile(
    r'(connect|connect_column_product)\(\s*"(?:[^"\\]|\\.)*"\s*,\s*"(?:[^"\\]|\\.)*"'
    r'\s*,\s*"([^"]*)"'
)
# Roles of the template's PORTS dict (connect) and the column product roles
# (connect_column_product); tests/test_instruction_files.py keeps them in sync.
CONNECT_ROLES = frozenset(
    {
        "single_in",
        "single_out",
        "multi_in",
        "mixer_out",
        "tee_in",
        "tee_out",
        "vapour_out",
        "liquid_out",
        "light_liquid_out",
        "heavy_liquid_out",
        "pfr_out",
        "tube_in",
        "tube_out",
        "shell_in",
        "shell_out",
        "splitter_in",
        "splitter_overhead",
        "splitter_bottom",
        "fired_in",
        "fired_out",
        "flue_gas_out",
        "fuel_in",
    }
)
COLUMN_PRODUCT_ROLES = frozenset(
    {"overhead_vapour", "distillate", "water_draw", "bottoms", "side_draw"}
)


def strip_comments(code: str) -> str:
    """Drop comments so a commented-out call is not mistaken for a connection."""
    lines = []
    for line in code.splitlines():
        in_string = None
        for index, char in enumerate(line):
            if in_string:
                if char == in_string:
                    in_string = None
            elif char in ("'", '"'):
                in_string = char
            elif char == "#":
                line = line[:index]
                break
        lines.append(line)
    return "\n".join(lines)


def _validate_step_6(step_6_code: str, unit_view_text: str) -> None:
    """Every (unit, stream) pair of the unit view must appear exactly once."""
    try:
        view = json.loads(unit_view_text)
    except Exception:
        return
    expected: list[tuple[str, str]] = []
    for unit in view.get("units_unit_view", []) or []:
        unit_id = unit.get("unit_id")
        for stream in (unit.get("streams_in") or []) + (unit.get("streams_out") or []):
            if unit_id and stream.get("id"):
                expected.append((unit_id, stream["id"]))
    if not expected:
        return
    if not _STEP6_HEADER_RE.search(step_6_code):
        raise ValueError("Configuration output does not start with the Step 6 marker.")
    code = strip_comments(step_6_code)
    found = _CONNECT_CALL_RE.findall(code)
    missing = [pair for pair in expected if pair not in found]
    extra = [pair for pair in found if pair not in expected]
    duplicates = [pair for pair in set(found) if found.count(pair) > 1]
    bad_roles = [
        f"{call}(..., {role!r})"
        for call, role in _ROLE_RE.findall(code)
        if role not in (CONNECT_ROLES if call == "connect" else COLUMN_PRODUCT_ROLES)
    ]
    problems = []
    if missing:
        problems.append(f"streams never connected (unit, stream): {missing}")
    if extra:
        problems.append(f"connections not in units_unit_view: {extra}")
    if duplicates:
        problems.append(f"connected more than once: {duplicates}")
    if bad_roles:
        problems.append(f"unknown roles: {bad_roles}")
    if problems:
        raise ValueError("Configuration output is incomplete: " + "; ".join(problems))


def _replace_step_6(instantiation_code: str, step_6_code: str) -> str:
    pattern = (
        r"(?s)#\s*===\s*Step\s*6:\s*Connect\s*Streams\s*\(Agent\s*3\)\s*===.*?"
        r"#\s*===\s*Step\s*7:\s*Save\s*Simulation\s*==="
    )
    if not re.search(pattern, instantiation_code, flags=re.IGNORECASE):
        raise ValueError("Could not find Step 6 block markers in Instantiation code.")

    replacement = step_6_code.rstrip() + "\n\n# === Step 7: Save Simulation ==="
    return re.sub(
        pattern, lambda _: replacement, instantiation_code, count=1, flags=re.IGNORECASE
    )


def generate_configuration_script(
    state: ConfigurationState, model_name: str
) -> dict[str, Any]:
    instantiation_code = _load_text(state.instantiation_path)
    instructions = _load_instructions(state.instruction_paths)
    structured_json_text = _load_json_text(state.description)

    system_prompt = _build_system_prompt()
    user_prompt = _build_user_prompt(
        description=structured_json_text,
        instantiation_code=instantiation_code,
        instructions=instructions,
    )

    raw_output = llm.chat(
        model_name,
        system_prompt,
        user_prompt,
        max_output_tokens=config.CONFIGURATION_MAX_OUTPUT_TOKENS,
        log_label="configuration",
    )

    step_6_code = _extract_code(raw_output)
    try:
        _validate_step_6(step_6_code, structured_json_text)
    except ValueError:
        dump_path = os.path.join(
            os.path.dirname(state.instantiation_path),
            "configuration_failed_validation__step6.txt",
        )
        with open(dump_path, "w", encoding="utf-8") as file:
            file.write(raw_output)
        raise
    full_script = _replace_step_6(instantiation_code, step_6_code)

    return {
        "python_code": full_script,
        "raw_output": raw_output,
        "prompt_debug": system_prompt + "\n\n" + user_prompt,
        "summary": "Configuration script generated and merged into full file.",
        "output_path": None,
    }


def build_configuration_graph(model_name: str):
    graph = StateGraph(ConfigurationState)
    graph.add_node(
        "configuration",
        RunnableLambda(lambda state: generate_configuration_script(state, model_name)),
    )
    graph.set_entry_point("configuration")
    graph.set_finish_point("configuration")
    return graph.compile()
