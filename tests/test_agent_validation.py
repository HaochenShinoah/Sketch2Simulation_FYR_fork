"""Structural validation of the coding agents' answers and .env parsing."""

from __future__ import annotations

import json

import pytest

from sketch2sim import config
from sketch2sim.agents import configuration_agent as ca
from sketch2sim.agents import instantiation_agent as ia

STRUCTURE = json.dumps(
    {
        "units": [{"id": "P-1", "name": "Pump", "tags": ["pump"]}],
        "feed_streams": [
            {"id": "S1", "to_unit": "P-1", "name": "in", "connect_point": "side"}
        ],
        "intermediate_streams": [],
        "product_streams": [
            {"id": "S2", "from_unit": "P-1", "name": "out", "connect_point": "side"}
        ],
    }
)
UNIT_VIEW = json.dumps(
    {
        "units_unit_view": [
            {
                "unit_id": "P-1",
                "unit_name": "Pump",
                "streams_in": [{"id": "S1"}],
                "streams_out": [{"id": "S2"}],
            }
        ]
    }
)


def test_instantiation_validation():
    good = (
        "# === Step 4: Add Unit Operations (Agent 2) ===\nunits = {}\n"
        'add_unit("P-1", "Pump", TYPES["pump"])\n'
        "# === Step 5: Add Material Streams (Agent 2) ===\nstreams = {}\n"
        'add_stream("S1", "in")\nadd_stream("S2", "out")\n'
    )
    ia._validate_block(good, STRUCTURE)
    with pytest.raises(ValueError, match="streams without add_stream: \\['S2'\\]"):
        ia._validate_block(good.replace('add_stream("S2", "out")\n', ""), STRUCTURE)
    with pytest.raises(ValueError, match="add_unit keys not in STRUCTURED_JSON"):
        ia._validate_block(
            good + 'add_unit("P-9", "Ghost", TYPES["pump"])\n', STRUCTURE
        )
    ia._validate_block(good, "not json")  # unparsable structure: validation is skipped


def test_configuration_validation():
    good = (
        "# === Step 6: Connect Streams (Agent 3) ===\n# --- P-1 (Pump) ---\n"
        'connect("P-1", "S1", "single_in")\nconnect("P-1", "S2", "single_out")\n'
    )
    ca._validate_step_6(good, UNIT_VIEW)
    ca._validate_step_6(
        good.replace(
            'connect("P-1", "S2", "single_out")', 'skip("P-1", "S2", "no port")'
        ),
        UNIT_VIEW,
    )
    with pytest.raises(ValueError, match="never connected"):
        ca._validate_step_6(
            good.replace('connect("P-1", "S2", "single_out")\n', ""), UNIT_VIEW
        )
    with pytest.raises(ValueError, match="more than once"):
        ca._validate_step_6(good + 'connect("P-1", "S2", "single_out")\n', UNIT_VIEW)
    with pytest.raises(ValueError, match="Step 6 marker"):
        ca._validate_step_6('connect("P-1", "S1", "single_in")', UNIT_VIEW)


_SCRIPT = (
    "import os\n# === Step 1: Launch ===\ncase_name = 'x'\n"
    "# === Step 2: Setup ===\nproperty_package = 'PengRob'\n"
    "# === Step 3b: HYSYS COM helpers ===\nTYPES = {{}}\n"
    "# === Step 4: Add Unit Operations (Agent 2) ===\nunits = {{}}\n"
    'add_unit("P-1", "Pump", TYPES["pump"])\n'
    'add_unit("T-1", "Column", TYPES["column_distillation"])\n'
    "# === Step 5: Add Material Streams (Agent 2) ===\nstreams = {{}}\n"
    'add_stream("S1", "in")\nadd_stream("S2", "out")\n'
    "# === Step 6: Connect Streams (Agent 3) ===\n# --- P-1 (Pump) ---\n"
    "{step6}"
    "# === Step 7: Save Simulation ===\ncase.SaveAs(path)\n"
)
_COLUMN_STRUCTURE = json.dumps(
    {
        "units": [
            {"id": "P-1", "name": "Pump", "tags": ["pump"]},
            {"id": "T-1", "name": "Column", "tags": ["column"]},
        ],
        "feed_streams": [
            {"id": "S1", "to_unit": "P-1", "name": "in", "connect_point": "side"}
        ],
        "intermediate_streams": [],
        "product_streams": [
            {"id": "S2", "from_unit": "P-1", "name": "out", "connect_point": "side"}
        ],
    }
)


def test_fixed_script_validation():
    from sketch2sim.agents.fixer_agent import validate_fixed_script

    good = _SCRIPT.format(
        step6='connect("P-1", "S1", "single_in")\nconnect("P-1", "S2", "single_out")\n'
    )
    assert validate_fixed_script(good, _COLUMN_STRUCTURE, UNIT_VIEW) == []
    # skip() on a column is refused
    bad = _SCRIPT.format(
        step6='connect("P-1", "S1", "single_in")\nskip("T-1", "S2", "no free vessel port")\n'
    )
    problems = validate_fixed_script(bad, _COLUMN_STRUCTURE, UNIT_VIEW)
    assert any("skip() is not allowed on column 'T-1'" in p for p in problems)
    # helpers or raw COM calls in Steps 4-6 are refused
    bad = _SCRIPT.format(
        step6='def connect(*a):\n    pass\nconnect("P-1", "S1", "single_in")\n'
        'connect("P-1", "S2", "single_out")\n'
    )
    problems = validate_fixed_script(bad, _COLUMN_STRUCTURE, UNIT_VIEW)
    assert any("may only call the helpers" in p for p in problems)
    # a missing template marker is refused before anything else
    problems = validate_fixed_script(
        good.replace("# === Step 7:", "# Step 7"), None, None
    )
    assert problems and "exactly once" in problems[0]
    # a stream that is never connected is refused
    bad = _SCRIPT.format(step6='connect("P-1", "S1", "single_in")\n')
    problems = validate_fixed_script(bad, _COLUMN_STRUCTURE, UNIT_VIEW)
    assert any("never connected" in p for p in problems)


def test_dotenv_parsing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        'S2S_TEST_A=plain # inline comment\nS2S_TEST_B="quoted # not a comment"\n'
        "export S2S_TEST_C=exported\n# comment line\nS2S_TEST_D=\n",
        encoding="utf-8",
    )
    for key in ("S2S_TEST_A", "S2S_TEST_B", "S2S_TEST_C", "S2S_TEST_D"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("S2S_TEST_C", "already-set")
    loaded = config.load_dotenv(env_file)
    assert loaded["S2S_TEST_A"] == "plain"
    assert loaded["S2S_TEST_B"] == "quoted # not a comment"
    assert "S2S_TEST_C" not in loaded  # existing variables win
    assert loaded["S2S_TEST_D"] == ""
