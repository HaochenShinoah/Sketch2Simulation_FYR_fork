"""Splicing the Basis agent's Steps 1-3 into the template."""

from __future__ import annotations

import pytest

from sketch2sim.agents.basis_agent import _apply_safety_belt, _extract_code
from tests.conftest import TEMPLATE_PATH

TEMPLATE = TEMPLATE_PATH.read_text(encoding="utf-8")
HELPERS = "# === Step 3b: HYSYS COM helpers"
STEP4 = "# === Step 4: Add Unit Operations (Agent 2) ==="


def _filled_steps_1_to_3() -> str:
    """What the real model returns: the template up to Step 3, values filled."""
    head = TEMPLATE[: TEMPLATE.index(HELPERS)].rstrip()
    head = head.replace('case_name = "FILL_CASE_NAME"', 'case_name = "Demo"')
    head = head.replace(
        'property_package = "FILL_PROPERTY_PACKAGE"', 'property_package = "PengRob"'
    )
    return head


def _assert_well_formed(result: str) -> None:
    assert result.count("# === Step 1:") == 1
    assert result.count(HELPERS) == 1
    assert result.count(STEP4) == 1
    assert result.index("# === Step 1:") < result.index(HELPERS) < result.index(STEP4)
    assert 'case_name = "Demo"' in result
    assert 'property_package = "PengRob"' in result
    assert "FILL_CASE_NAME" not in result
    # the template's own helpers and tail survive untouched
    assert "def add_unit(" in result and "finish_connections()" in result
    assert result.count("flowsheet = case.Flowsheet") == 1


def test_answer_that_stops_after_step_3_is_spliced():
    result = _apply_safety_belt(TEMPLATE, _filled_steps_1_to_3())
    _assert_well_formed(result)


def test_answer_that_echoes_helpers_and_step_4_is_cut_at_the_marker():
    echoed = _filled_steps_1_to_3() + "\n\n" + TEMPLATE[TEMPLATE.index(HELPERS) :]
    result = _apply_safety_belt(TEMPLATE, echoed)
    _assert_well_formed(result)


def test_missing_step_3_tail_is_restored():
    truncated = _filled_steps_1_to_3()
    truncated = truncated[: truncated.index("hybasis.EndBasisChange()")].rstrip()
    assert "flowsheet = case.Flowsheet" not in truncated
    result = _apply_safety_belt(TEMPLATE, truncated)
    _assert_well_formed(result)
    step_block = result[: result.index(HELPERS)]
    assert step_block.rstrip().endswith(
        "hybasis.EndBasisChange()\nflowsheet = case.Flowsheet"
    )


def test_fenced_answer_with_prose_is_accepted():
    raw = "Here is the file:\n\n```python\n" + _filled_steps_1_to_3() + "\n```\nDone."
    result = _apply_safety_belt(TEMPLATE, _extract_code(raw))
    _assert_well_formed(result)


def test_answer_without_step_1_marker_is_rejected():
    with pytest.raises(ValueError, match="no '# === Step 1:' marker"):
        _apply_safety_belt(TEMPLATE, 'case_name = "Demo"\ncomponents = ["Water"]')
