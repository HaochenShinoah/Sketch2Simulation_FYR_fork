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


def _dict(source: str, name: str) -> dict:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in template")


def _tuple(source: str, name: str) -> tuple:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
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
        "def connect_energy(",
        "def skip(",
        "def finish_connections(",
        "def save_case(",
        "def _shutdown(",
    ]:
        assert helper in TEMPLATE
    # helpers are defined before Step 4; the summary runs before SaveAs, which is
    # followed by the exit code
    assert TEMPLATE.index("def add_unit(") < TEMPLATE.index("# === Step 4:")
    step7 = TEMPLATE[TEMPLATE.index("# === Step 7:") :]
    assert step7.index("finish_connections(") < step7.index("save_case(")
    assert "sys.exit(exit_code)" in step7
    assert "DispatchEx(" in TEMPLATE and "atexit.register(_shutdown)" in TEMPLATE


def test_template_types_and_ports_are_single_verified_members():
    types = _dict(TEMPLATE, "TYPES")
    ports = _dict(TEMPLATE, "PORTS")
    assert all(len(v) == 1 for v in types.values()), "one verified type string each"
    assert all(len(v) == 1 for v in ports.values()), "one verified port member each"
    assert types["separator_3phase"] == ["sep3op"]
    assert types["reactor_cstr"] == ["kineticreactorop"]
    assert types["reactor_pfr"] == ["pfreactorop"]
    assert types["fired_heater"] == ["dynamicfiredheaterop"]
    assert ports["tee_in"] == ["FeedStream"]
    assert ports["fired_in"] == ["RadInlet.Add"] and ports["fired_out"] == [
        "RadOutlet.Add"
    ]
    assert "air_in" not in ports
    templates = _dict(TEMPLATE, "COLUMN_TEMPLATES")
    for key, value in types.items():
        if key.startswith("column_"):
            assert value[0] in templates, key
    assert "column_shortcut" not in types
    # the configuration agent validates roles against the same lists
    from sketch2sim.agents.configuration_agent import (
        COLUMN_PRODUCT_ROLES,
        CONNECT_ROLES,
    )

    assert set(ports) == set(CONNECT_ROLES)
    column_roles = set(_dict(TEMPLATE, "_COLUMN_PRODUCT_MEMBERS")) | {"side_draw"}
    assert column_roles == set(COLUMN_PRODUCT_ROLES)


def test_instantiation_instructions_match_template_types():
    types_in_template = set(_dict(TEMPLATE, "TYPES"))
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
    # verified spellings are documented, wrong ones are called out
    for spelling in ("sep3op", "kineticreactorop", "pfreactorop", "LoadColumnTemplate"):
        assert spelling in INST, spelling
    assert "CreateColumnTemplate() must never be called" in INST
    # the file echoes the contract of the agent's system prompt
    assert "# === Step 4: Add Unit Operations (Agent 2) ===" in INST
    assert "# === Step 5: Add Material Streams (Agent 2) ===" in INST
    assert "# === Step 6: Connect Streams (Agent 3) ===" in INST
    assert "Return only a single Python code block" in inst_system()


def test_configuration_instructions_match_template_ports():
    ports_in_template = set(_dict(TEMPLATE, "PORTS"))
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
    # the three skip reasons are quoted verbatim in the instruction file
    for reason in _tuple(TEMPLATE, "SKIP_REASONS"):
        assert f'"{reason}"' in CONF, reason
    assert "skip() is never used on a column" in CONF
    assert "connect_energy" in CONF and "position=" in CONF and "stage=" in CONF
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
    # every "# --- unit ---" example group of the column section is valid Python and
    # passes the configuration validator's role check
    from sketch2sim.agents.configuration_agent import _ROLE_RE, CONNECT_ROLES

    section = CONF[CONF.index("7. COLUMN EXAMPLES") : CONF.index("\n8. ")]
    groups = re.findall(r"(?ms)^# --- .*?(?=\n\n|\Z)", section)
    assert len(groups) >= 4
    for group in groups:
        ast.parse(group)
        for call, role in _ROLE_RE.findall(group):
            if call == "connect":
                assert role in CONNECT_ROLES, (role, group[:40])
    assert any("connect_energy(" in g for g in groups)
    assert any("fired_in" in g and "flue_gas_out" in g for g in groups)
