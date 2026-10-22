"""Step 1 of the generated script is template-owned whatever the Basis model wrote."""

from __future__ import annotations

import re

from sketch2sim.agents.basis_agent import _apply_safety_belt
from tests.test_basis_agent import TEMPLATE, _filled_steps_1_to_3


def test_step_1_is_template_owned_whatever_the_model_wrote():
    answer = _filled_steps_1_to_3()
    # the model rewrote Step 1 (old Dispatch call, no retry) and used single quotes
    answer = re.sub(
        r"(?s)# === Step 1:.*?(?=# === Step 2:)",
        "# === Step 1: Launch HYSYS and create a new case (Agent 1) ===\n"
        "case_name = 'Quoted Name'\nhysys = Dispatch('HYSYS.Application')\n"
        "case = hysys.SimulationCases.Add(case_name)\n\n",
        answer,
    )
    result = _apply_safety_belt(TEMPLATE, answer)
    assert 'case_name = "Quoted Name"' in result
    assert "DispatchEx(" in result and "Dispatch('HYSYS.Application')" not in result
    assert result.count("# === Step 1:") == 1
    # a reworded Step 2 header is still found
    reworded = answer.replace(
        "# === Step 2: Setup Property Package (Agent 1) ===", "# === Step 2 ==="
    )
    result = _apply_safety_belt(TEMPLATE, reworded)
    assert 'property_package = "PengRob"' in result
