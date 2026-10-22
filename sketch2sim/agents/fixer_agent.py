"""Fixer (B4.1): runtime-error-driven repair of the generated HYSYS script.

The Fixer may change Steps 1-3 (basis values) and Steps 4-6 (units, streams,
connections). The helper block (Step 3b) and the save step (Step 7) are owned by
the template and are re-inserted by the merge step before every execution, so
edits there are discarded. ``validate_fixed_script`` checks a Fixer answer
against the structure and the unit view before it is accepted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path

from sketch2sim import config, llm
from sketch2sim.agents import configuration_agent, instantiation_agent
from sketch2sim.utils.agent_utils import read_text_file
from sketch2sim.utils.logging_utils import log_text

FIXER_SYSTEM_PROMPT = """
You are a code-fixing agent.

You will receive:
1. The HYSYS scripting instructions for instantiation.
2. The HYSYS scripting instructions for configuration.
3. The unit view JSON.
4. A structured validator issues report.
5. The current Python code.

Your task:
- Fix only the issues explicitly listed in the validator issues report.
- Follow the HYSYS scripting instructions exactly.
- You may change Steps 2-3 (property package, components) and Steps 4-6
  (add_unit / add_stream / connect lines). The file head, Step 1 (HYSYS
  start-up), the helper block (Step 3b) and Step 7 are restored from the
  template before every run, so edits there are lost. Keep every
  "# === Step N: ..." marker line exactly as it is. Never add functions,
  imports, raw COM calls, or assignments to TYPES / PORTS / units / streams.
- Never replace a column connection by skip(): skip() is refused on columns.
  Never change a column unit into another category or vice versa.
- Prefer inserting or editing single lines; do not reorder unrelated code.
- Return only the full corrected Python script.
- No explanations, no comments, no markdown fences, no JSON.
""".strip()

_MARKERS = ("# === Step 2:", "# === Step 3b:", "# === Step 4:", "# === Step 7:")
_UNIT_CATEGORY_RE = re.compile(
    r'add_unit\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"(?:[^"\\]|\\.)*"\s*,\s*TYPES\["([a-z_0-9]+)"\]'
)
_SKIP_RE = re.compile(r'skip\(\s*"((?:[^"\\]|\\.)*)"')
_FORBIDDEN_IN_STEPS_4_6 = (
    re.compile(r"^\s*def\s+\w+\(", re.M),
    re.compile(
        r"^\s*(?:TYPES|PORTS|COLUMN_TEMPLATES|SKIP_REASONS)\s*(?:\[[^\]]*\])?\s*=", re.M
    ),
    re.compile(r"\b(?:units|streams)\s*\[[^\]]*\]\s*(?:\.|=(?!=))", re.M),
    re.compile(r"^\s*(?:import|from)\s+\w+", re.M),
    re.compile(r"\bDispatch(?:Ex)?\(", re.M),
    re.compile(r"\.Operations\.Add\(|\.MaterialStreams\.Add\(", re.M),
)


def _unit_categories(steps_4_6: str) -> dict[str, str]:
    return dict(
        _UNIT_CATEGORY_RE.findall(configuration_agent.strip_comments(steps_4_6))
    )


def validate_fixed_script(
    code_text: str,
    structure_json_str: str | None,
    unit_view_json_str: str | None,
    original_code_text: str | None = None,
) -> list[str]:
    """Problems that make a Fixer answer unusable (empty list = accepted)."""
    problems: list[str] = []
    for marker in _MARKERS:
        count = code_text.count(marker)
        if count != 1:
            problems.append(
                f"marker {marker!r} must appear exactly once (found {count})"
            )
    if problems:
        return problems

    steps_4_6 = code_text[
        code_text.index("# === Step 4:") : code_text.index("# === Step 7:")
    ]
    for pattern in _FORBIDDEN_IN_STEPS_4_6:
        match = pattern.search(configuration_agent.strip_comments(steps_4_6))
        if match:
            problems.append(
                f"Steps 4-6 may only call the helpers; found {match.group(0).strip()!r}"
            )

    if original_code_text and original_code_text.count("# === Step 4:") == 1:
        original = original_code_text[original_code_text.index("# === Step 4:") :]
        before = _unit_categories(original)
        after = _unit_categories(steps_4_6)
        for unit_key, category in after.items():
            was = before.get(unit_key)
            if was and was.startswith("column_") != category.startswith("column_"):
                problems.append(
                    f"unit {unit_key!r} changed from {was!r} to {category!r}: a column "
                    "must stay a column (and a non-column must not become one)"
                )

    try:
        block = instantiation_agent._extract_steps_4_to_5_only(code_text)
        if structure_json_str:
            instantiation_agent._validate_block(block, structure_json_str)
    except ValueError as error:
        problems.append(str(error))

    try:
        step_6 = configuration_agent._extract_step_6_only(steps_4_6)
        if unit_view_json_str:
            configuration_agent._validate_step_6(step_6, unit_view_json_str)
    except ValueError as error:
        problems.append(str(error))

    column_keys = {
        key
        for key, category in _unit_categories(steps_4_6).items()
        if category.startswith("column_")
    }
    for unit_key in _SKIP_RE.findall(configuration_agent.strip_comments(steps_4_6)):
        if unit_key in column_keys:
            problems.append(
                f"skip() is not allowed on column {unit_key!r}: connect its stream "
                "with connect_column_feed / connect_column_product"
            )
    return problems


def _build_fixer_prompt(
    instantiation_instructions_text: str,
    configuration_instructions_text: str,
    unit_view_json_str: str,
    validator_issues: str,
    code_text: str,
) -> str:
    return f"""---------------------------
[INSTANTIATION_INSTRUCTIONS]
<<<
{instantiation_instructions_text}
>>>

[CONFIGURATION_INSTRUCTIONS]
<<<
{configuration_instructions_text}
>>>

[UNIT_VIEW_JSON]
<<<
{unit_view_json_str}
>>>

[VALIDATOR_ISSUES]
<<<
{validator_issues}
>>>

[CODE_PY]
<<<
{code_text}
>>>
"""


def _strip_code_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            inner = parts[1].strip()
            inner_lines = inner.splitlines()
            if inner_lines and inner_lines[0].lower().startswith(("python", "py")):
                inner = "\n".join(inner_lines[1:])
            text = inner.strip()
    return text


def _call_fixer_llm(
    prompt: str,
    model_name: str,
    temperature: float | None = 0.0,
) -> str:
    raw = llm.chat(
        model_name,
        FIXER_SYSTEM_PROMPT,
        prompt,
        temperature=temperature,
        max_output_tokens=config.FIXER_MAX_OUTPUT_TOKENS,
        log_label="fixer",
    )
    return _strip_code_fences(raw)


def fix_code_text(
    *,
    instantiation_instructions_text: str,
    configuration_instructions_text: str,
    unit_view_json_str: str,
    validator_issues: str,
    code_text: str,
    model_name: str | None = None,
    temperature: float | None = 0.0,
) -> str:
    prompt = _build_fixer_prompt(
        instantiation_instructions_text=instantiation_instructions_text,
        configuration_instructions_text=configuration_instructions_text,
        unit_view_json_str=unit_view_json_str,
        validator_issues=validator_issues,
        code_text=code_text,
    )

    return _call_fixer_llm(
        prompt=prompt,
        model_name=model_name or config.FIXER_MODEL,
        temperature=temperature,
    )


def fix_code_file(
    *,
    code_path: str | Path,
    unit_view_json_str: str,
    validator_issues: str,
    model_name: str | None = None,
    temperature: float | None = 0.0,
    log_dir: str | Path | None = None,
    instantiation_instruction_paths: Iterable[str | Path],
    configuration_instruction_paths: Iterable[str | Path],
    structure_json_str: str | None = None,
    max_validation_retries: int = 1,
) -> Path:
    """Repair the script; re-ask once when the answer fails structural validation."""
    instantiation_instructions_text = "\n\n".join(
        read_text_file(str(path)) for path in instantiation_instruction_paths
    )

    configuration_instructions_text = "\n\n".join(
        read_text_file(str(path)) for path in configuration_instruction_paths
    )

    code_path = Path(code_path)
    code_text = code_path.read_text(encoding="utf-8")

    issues = validator_issues
    fixed_code = code_text
    for round_index in range(max_validation_retries + 1):
        fixed_code = fix_code_text(
            instantiation_instructions_text=instantiation_instructions_text,
            configuration_instructions_text=configuration_instructions_text,
            unit_view_json_str=unit_view_json_str,
            validator_issues=issues,
            code_text=code_text,
            model_name=model_name,
            temperature=temperature,
        )
        problems = validate_fixed_script(
            fixed_code, structure_json_str, unit_view_json_str, code_text
        )
        if not problems:
            break
        print(f"[Fixer] answer rejected: {problems}")
        if log_dir is not None:
            log_text(
                str(log_dir),
                f"runtime_fixed_code_rejected_{round_index + 1}.py",
                fixed_code,
            )
        issues = (
            validator_issues
            + "\n\n=== FIXER ANSWER REJECTED (structural validation) ===\n"
            + "\n".join(f"- {problem}" for problem in problems)
        )
    else:
        # Every answer failed validation: keep the previous script rather than
        # handing the merge an unusable file; the next attempt fails the same
        # way and the Fixer is asked again with the same evidence.
        print("[Fixer] every answer was rejected; keeping the previous script")
        return code_path

    output_path = code_path.with_name(code_path.stem + "_fixed" + code_path.suffix)
    output_path.write_text(fixed_code, encoding="utf-8")

    if log_dir is not None:
        log_text(str(log_dir), "runtime_fixed_code.py", fixed_code)

    print(f"[Fixer] Wrote fixed code to: {output_path}")
    return output_path


def structure_json_for_fixer(structure: dict) -> str:
    return json.dumps(structure, indent=2)
