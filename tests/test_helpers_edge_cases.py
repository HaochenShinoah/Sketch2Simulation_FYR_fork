"""Edge cases of the template helpers against the fake COM layer."""

from __future__ import annotations

import textwrap

from tests.test_generated_script_runs import _run_script, _script_with_steps_4_to_6


def test_burner_feed_displacement_and_position_refusal(tmp_path):
    steps = textwrap.dedent(
        """
        # === Step 4: Add Unit Operations (Agent 2) ===
        units = {}
        add_unit("H-1", "Furnace", TYPES["fired_heater"])
        add_unit("T-1", "Absorber", TYPES["column_absorber"])
        # === Step 5: Add Material Streams (Agent 2) ===
        streams = {}
        add_stream("S1", "fuel")
        add_stream("S2", "air")
        add_stream("S3", "process in")
        add_stream("S4", "process out")
        add_stream("S5", "lean oil")
        # === Step 6: Connect Streams (Agent 3) ===
        connect("H-1", "S1", "fuel_in")
        connect("H-1", "S2", "fuel_in")
        connect("H-1", "S3", "fired_in")
        connect("H-1", "S4", "fired_out")
        connect_column_feed("T-1", "S5", position="top")
        """
    )
    script = _script_with_steps_4_to_6(tmp_path, steps)
    result = _run_script(script, tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "S1 -> H-1" in result.stdout and "displaced" in result.stdout
    assert "T-1.TopLiquidFeed <- S5" in result.stdout
    # a refused position never falls through to a tray feed
    result = _run_script(script, tmp_path, reject=["TopLiquidFeed"])
    assert "AddFeedStream" not in result.stdout
    assert "S5 -> T-1" in result.stdout and "TopLiquidFeed" in result.stdout


def test_stream_exported_by_one_column_is_not_exported_again(tmp_path):
    steps = textwrap.dedent(
        """
        # === Step 4: Add Unit Operations (Agent 2) ===
        units = {}
        add_unit("T-A", "Column A", TYPES["column_distillation"])
        add_unit("T-B", "Column B", TYPES["column_distillation"])
        # === Step 5: Add Material Streams (Agent 2) ===
        streams = {}
        add_stream("S1", "a to b")
        add_stream("S2", "b to a")
        # === Step 6: Connect Streams (Agent 3) ===
        connect_column_product("T-A", "S1", "side_draw")
        connect_column_feed("T-B", "S1")
        connect_column_feed("T-A", "S2")
        connect_column_product("T-B", "S2", "side_draw")
        """
    )
    result = _run_script(_script_with_steps_4_to_6(tmp_path, steps), tmp_path)
    out = result.stdout
    # draw first, feed second: the feed becomes a top feed, nothing is exported twice
    assert "T-A.AddDrawStream" in out and "T-B.TopLiquidFeed <- S1" in out
    # feed first, draw second: the draw is refused with an explicit reason
    assert "already exported by column T-A" in out
    assert result.returncode == 2


def test_stage_clamp_phase_override_and_unknown_role(tmp_path):
    steps = textwrap.dedent(
        """
        # === Step 4: Add Unit Operations (Agent 2) ===
        units = {}
        add_unit("T-1", "Column", TYPES["column_distillation"])
        add_unit("R-1", "PFR", TYPES["reactor_pfr"])
        add_unit("P-1", "Pump", TYPES["pump"])
        # === Step 5: Add Material Streams (Agent 2) ===
        streams = {}
        add_stream("S1", "feed")
        add_stream("S2", "Heavy Gas Oil")
        add_stream("S3", "reactor in")
        add_stream("S4", "reactor out")
        add_stream("S5", "pump in")
        # === Step 6: Connect Streams (Agent 3) ===
        connect_column_feed("T-1", "S1", stage=25)
        connect_column_product("T-1", "S2", "side_draw", stage=40, phase="vapour")
        connect("R-1", "S3", "multi_in")
        connect("R-1", "S4", "pfr_out")
        connect("P-1", "S5", "vapor_in")
        """
    )
    result = _run_script(_script_with_steps_4_to_6(tmp_path, steps), tmp_path)
    out = result.stdout
    assert "stage 25 is outside 1..10; using 10" in out
    assert "T-1.AddDrawStream(stage 10, phase 0) <- S2" in out
    assert "R-1.Product <- S4" in out
    assert "unknown role 'vapor_in'" in out
    assert result.returncode == 2


def test_gas_oil_is_a_liquid_draw_and_default_stages_spread(tmp_path):
    steps = textwrap.dedent(
        """
        # === Step 4: Add Unit Operations (Agent 2) ===
        units = {}
        add_unit("T-1", "Column", TYPES["column_distillation"])
        # === Step 5: Add Material Streams (Agent 2) ===
        streams = {}
        add_stream("S1", "Atmospheric Gas Oil")
        add_stream("S2", "Gasoline")
        add_stream("S3", "Vent Gas")
        add_stream("S4", "Sour Water")
        add_stream("S5", "fifth cut")
        # === Step 6: Connect Streams (Agent 3) ===
        connect_column_product("T-1", "S1", "side_draw")
        connect_column_product("T-1", "S2", "side_draw")
        connect_column_product("T-1", "S3", "side_draw")
        connect_column_product("T-1", "S4", "side_draw")
        connect_column_product("T-1", "S5", "side_draw")
        """
    )
    result = _run_script(_script_with_steps_4_to_6(tmp_path, steps), tmp_path)
    out = result.stdout
    assert "AddDrawStream(stage 2, phase 1) <- S1" in out
    assert "AddDrawStream(stage 4, phase 1) <- S2" in out
    assert "AddDrawStream(stage 6, phase 0) <- S3" in out
    assert "AddDrawStream(stage 8, phase 2) <- S4" in out
    assert "no free default stage left" in out and "S5 -> T-1" in out
