"""Guards against gaming the validators: comments, roles, category and helper edits."""

from __future__ import annotations

import pytest

from sketch2sim.agents import configuration_agent as ca
from tests.test_agent_validation import _COLUMN_STRUCTURE, _SCRIPT, UNIT_VIEW


def test_step_6_validation_ignores_comments_and_checks_roles():
    commented = (
        "# === Step 6: Connect Streams (Agent 3) ===\n# --- P-1 (Pump) ---\n"
        'connect("P-1", "S1", "single_in")\n# connect("P-1", "S2", "single_out")\n'
    )
    with pytest.raises(ValueError, match="never connected"):
        ca._validate_step_6(commented, UNIT_VIEW)
    bad_role = (
        "# === Step 6: Connect Streams (Agent 3) ===\n# --- P-1 (Pump) ---\n"
        'connect("P-1", "S1", "single_in")\nconnect("P-1", "S2", "vapor_out")\n'
    )
    with pytest.raises(ValueError, match="unknown roles"):
        ca._validate_step_6(bad_role, UNIT_VIEW)
    with_energy = (
        "# === Step 6: Connect Streams (Agent 3) ===\n# --- P-1 (Pump) ---\n"
        'connect("P-1", "S1", "single_in")\nconnect("P-1", "S2", "single_out")\n'
        'connect_energy("P-1")\n'
    )
    ca._validate_step_6(with_energy, UNIT_VIEW)  # energy lines are not pairs


def test_fixed_script_validation_rejects_category_and_helper_tampering():
    from sketch2sim.agents.fixer_agent import validate_fixed_script

    original = _SCRIPT.format(
        step6='connect("P-1", "S1", "single_in")\nconnect("P-1", "S2", "single_out")\n'
    )
    demoted = original.replace(
        'add_unit("T-1", "Column", TYPES["column_distillation"])',
        'add_unit("T-1", "Column", TYPES["separator"])',
    )
    problems = validate_fixed_script(demoted, _COLUMN_STRUCTURE, UNIT_VIEW, original)
    assert any("must stay a column" in p for p in problems)
    tampered = original.replace(
        'connect("P-1", "S2", "single_out")\n',
        'TYPES["pump"] = ["heaterop"]\nconnect("P-1", "S2", "single_out")\n',
    )
    problems = validate_fixed_script(tampered, _COLUMN_STRUCTURE, UNIT_VIEW, original)
    assert any("may only call the helpers" in p for p in problems)
    poked = original.replace(
        'connect("P-1", "S2", "single_out")\n',
        'units["P-1"].ProductStream = streams["S2"]\n'
        'connect("P-1", "S2", "single_out")\n',
    )
    problems = validate_fixed_script(poked, _COLUMN_STRUCTURE, UNIT_VIEW, original)
    assert any("may only call the helpers" in p for p in problems)
    # a commented-out skip on a column is not a skip
    commented = original.replace(
        'connect("P-1", "S2", "single_out")\n',
        'connect("P-1", "S2", "single_out")\n'
        '# skip("T-1", "S9", "no free vessel port")\n',
    )
    assert validate_fixed_script(commented, _COLUMN_STRUCTURE, UNIT_VIEW) == []


def test_fixer_keeps_previous_script_when_every_answer_is_rejected(
    tmp_path, monkeypatch
):
    from sketch2sim import llm
    from sketch2sim.agents import fixer_agent

    script = tmp_path / "conf.py"
    script.write_text(
        "# === Step 4: Add Unit Operations (Agent 2) ===\n", encoding="utf-8"
    )
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "garbage without markers")
    instructions = tmp_path / "i.txt"
    instructions.write_text("x", encoding="utf-8")
    result = fixer_agent.fix_code_file(
        code_path=script,
        unit_view_json_str=UNIT_VIEW,
        validator_issues="boom",
        instantiation_instruction_paths=[instructions],
        configuration_instruction_paths=[instructions],
        log_dir=tmp_path,
    )
    assert result == script
    assert not (tmp_path / "conf_fixed.py").exists()
    assert (tmp_path / "runtime_fixed_code_rejected_1.py").is_file()
    assert (tmp_path / "runtime_fixed_code_rejected_2.py").is_file()
