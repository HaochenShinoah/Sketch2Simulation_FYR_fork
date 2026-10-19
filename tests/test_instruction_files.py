"""Consistency between the instruction files, the template helpers and the agents."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from sketch2sim.agents.configuration_agent import _build_system_prompt as config_system
from sketch2sim.agents.instantiation_agent import _build_system_prompt as inst_system
from sketch2sim.pipeline import (
    DEFAULT_CONFIGURATION_INSTRUCTIONS,
    DEFAULT_INSTANTIATION_INSTRUCTIONS,
    DEFAULT_TEMPLATE_PATH,
)

TEMPLATE = Path(DEFAULT_TEMPLATE_PATH).read_text(encoding="utf-8")
INST = Path(DEFAULT_INSTANTIATION_INSTRUCTIONS[0]).read_text(encoding="utf-8")
CONF = Path(DEFAULT_CONFIGURATION_INSTRUCTIONS[0]).read_text(encoding="utf-8")


def _dict_keys(source: str, name: str) -> set[str]:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return {k.value for k in node.value.keys}
    raise AssertionError(f"{name} not found in template")


def test_template_markers_and_helpers():
    for marker in [
        "# === Step 1:",
        "# === Step 2:",
        "# === Step 3: Add Components ===",
        "# === Step 3b: HYSYS COM helpers",
        "# === Step 4: Add Unit Operations (Agent 2) ===",
        "# === Step 5: Add Material Streams (Agent 2) ===",
        "# === Step 6: Connect Streams (Agent 3) ===",
        "# === Step 7: Save Simulation ===",
    ]:
        assert TEMPLATE.count(marker) == 1, marker
    for helper in [
        "def add_unit(",
        "def add_stream(",
        "def connect(",
        "def connect_column_feed(",
        "def connect_column_product(",
        "def finish_connections(",
    ]:
        assert helper in TEMPLATE
    # helpers are defined before Step 4 and the summary runs after SaveAs
    assert TEMPLATE.index("def add_unit(") < TEMPLATE.index("# === Step 4:")
    assert TEMPLATE.index("case.SaveAs(") < TEMPLATE.rindex("\nfinish_connections()")


def test_instantiation_instructions_match_template_types():
    types_in_template = _dict_keys(TEMPLATE, "TYPES")
    types_in_instructions = set(re.findall(r'TYPES\["([a-z_0-9]+)"\]', INST)) | set(
        re.findall(r'->\s*"([a-z_0-9]+)"', INST)
    )
    types_in_instructions.discard("see")
    unknown = types_in_instructions - types_in_template
    assert not unknown, (
        f"instruction file uses TYPES keys missing in template: {unknown}"
    )
    # the self-check list enumerates every template key
    checklist = INST[INST.index("every TYPES") :]
    missing = {t for t in types_in_template if t not in checklist}
    assert not missing, missing
    # the file echoes the contract of the agent's system prompt
    assert "# === Step 4: Add Unit Operations (Agent 2) ===" in INST
    assert "# === Step 5: Add Material Streams (Agent 2) ===" in INST
    assert "# === Step 6: Connect Streams (Agent 3) ===" in INST
    assert "Return only a single Python code block" in inst_system()


def test_configuration_instructions_match_template_ports():
    ports_in_template = _dict_keys(TEMPLATE, "PORTS")
    roles_used = set(re.findall(r'"([a-z_]+_(?:in|out))"', CONF))
    unknown = roles_used - ports_in_template
    assert not unknown, (
        f"instruction file uses roles missing in template PORTS: {unknown}"
    )
    assert ports_in_template <= set(re.findall(r"[a-z_]+", CONF)), (
        "every PORTS role is documented"
    )
    for role in ("overhead_vapour", "distillate", "water_draw", "bottoms", "side_draw"):
        assert role in CONF and f'"{role}"' in TEMPLATE
    assert "# === Step 6: Connect Streams (Agent 3) ===" in CONF
    assert "# === Step 7: Save Simulation ===" in CONF
    assert "units_unit_view" in config_system()


def test_worked_examples_are_valid_python():
    inst_example = re.search(r"```python\n(.*?)```", INST, re.S).group(1)
    ast.parse(inst_example)
    start = CONF.index("Step 6 block of the answer:")
    conf_example = CONF[
        CONF.index("# === Step 6: Connect Streams (Agent 3) ===", start) :
    ]
    conf_example = conf_example[: conf_example.index("\n7. ")]
    ast.parse(conf_example)
    column_example = CONF[CONF.index("# --- T-101 (Benzene Column) ---") :]
    column_example = column_example[: column_example.index("\nCrude column")]
    ast.parse(column_example)
