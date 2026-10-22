import copy

from sketch2sim.coupling_normalizer import couple_extraction
from sketch2sim.mixing_points_normalizer import normalize_mixing_points
from sketch2sim.splitting_points_normalizer import normalize_splitting_points
from sketch2sim.utils.normalization_utils import build_unit_view_from_structure


def test_mixing_normalizer_inserts_mixer_before_valve(cs1_extraction):
    structure = normalize_mixing_points(copy.deepcopy(cs1_extraction))
    unit_ids = [unit["id"] for unit in structure["units"]]

    assert "MIXER_TO_MIXING_VALVE" in unit_ids
    # mixer is placed immediately before the valve
    assert unit_ids.index("MIXER_TO_MIXING_VALVE") == unit_ids.index("MIXING_VALVE") - 1

    inbound_to_valve = [
        s
        for s in structure["feed_streams"] + structure["intermediate_streams"]
        if s["to_unit"] == "MIXING_VALVE"
    ]
    assert [s["id"] for s in inbound_to_valve] == ["S_MIXER_TO_MIXING_VALVE"]
    assert all(
        s["to_unit"] == "MIXER_TO_MIXING_VALVE"
        for s in structure["feed_streams"] + structure["intermediate_streams"]
        if s["id"] in {"S3", "S4", "S5"}
    )

    # idempotent
    again = normalize_mixing_points(copy.deepcopy(structure))
    assert [u["id"] for u in again["units"]] == unit_ids


def test_coupling_and_unit_view(cs1_extraction):
    structure = couple_extraction(
        normalize_mixing_points(copy.deepcopy(cs1_extraction))
    )
    view = build_unit_view_from_structure(structure)
    by_id = {unit["unit_id"]: unit for unit in view["units_unit_view"]}

    assert set(by_id) == {
        "CRUDE_OIL_PUMP",
        "FRESH_WATER_PUMP",
        "MIXER_TO_MIXING_VALVE",
        "MIXING_VALVE",
        "ELECTROSTATIC_SEPARATOR",
    }
    separator = by_id["ELECTROSTATIC_SEPARATOR"]
    assert [s["id"] for s in separator["streams_in"]] == ["S6"]
    assert {s["id"]: s["connect_point"] for s in separator["streams_out"]} == {
        "S7": "overhead",
        "S8": "bottom",
    }
    assert len(by_id["MIXER_TO_MIXING_VALVE"]["streams_in"]) == 3


def _load(name):
    import json
    from pathlib import Path

    return json.loads(
        (Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")
    )


def test_coupling_folds_condenser_and_reboiler_into_column_cs3():
    structure = couple_extraction(normalize_mixing_points(_load("cs3_extraction.json")))
    ids = [u["id"] for u in structure["units"]]
    assert "CONDENSER" not in ids and "REBOILER" not in ids
    column = next(
        u for u in structure["units"] if u["id"] == "ATMOSPHERIC_DISTILLATION_COLUMN"
    )
    overrides = column["hysys_overrides"]
    assert overrides["integrated_condenser"]["unit_id"] == "CONDENSER"
    assert overrides["integrated_reboiler"]["unit_id"] == "REBOILER"
    assert set(overrides["skip_unit_ids"]) == {"CONDENSER", "REBOILER"}
    # products of the absorbed units now leave the column; internal loops vanish
    products = {s["id"]: s["from_unit"] for s in structure["product_streams"]}
    assert products["S16"] == "ATMOSPHERIC_DISTILLATION_COLUMN"  # off-gas
    assert products["S22"] == "ATMOSPHERIC_DISTILLATION_COLUMN"  # residue
    assert all(
        s["from_unit"] != s["to_unit"] for s in structure["intermediate_streams"]
    )
    assert {s["id"] for s in structure["intermediate_streams"]}.isdisjoint(
        {"S10", "S11", "S12", "S13"}
    )


def test_coupling_overhead_system_and_mixer_cs4():
    structure = couple_extraction(normalize_mixing_points(_load("cs4_extraction.json")))
    ids = [u["id"] for u in structure["units"]]
    # mixer inserted upstream of the preheater that had three inlets
    assert ids.index("MIXER_TO_E-101") == ids.index("E-101") - 1
    # condenser + reflux drum + reflux pump + reboiler folded into T-101
    for absorbed in ("E-104", "V-104", "P-102", "E-106"):
        assert absorbed not in ids
    column = next(u for u in structure["units"] if u["id"] == "T-101")
    assert column["hysys_overrides"]["integrated_overhead_system"]["unit_ids"] == [
        "E-104",
        "V-104",
        "P-102",
    ]
    assert column["hysys_overrides"]["integrated_reboiler"]["unit_id"] == "E-106"
    # a heater whose name merely contains "Tower" is not annotated
    heater = next(u for u in structure["units"] if u["id"] == "E-103")
    assert "hysys_overrides" not in heater
    # the recycle toluene now leaves the column towards the storage drum
    recycle = next(s for s in structure["intermediate_streams"] if s["id"] == "S11")
    assert (recycle["from_unit"], recycle["to_unit"]) == ("T-101", "V-101")
    view = build_unit_view_from_structure(structure)
    column_view = next(u for u in view["units_unit_view"] if u["unit_id"] == "T-101")
    assert {s["id"] for s in column_view["streams_out"]} == {"S14", "S19", "S11"}


def test_splitting_normalizer_inserts_tees_cs4():
    structure = normalize_splitting_points(
        normalize_mixing_points(_load("cs4_extraction.json"))
    )
    ids = [u["id"] for u in structure["units"]]
    # compressor C-101 feeds the quench (S7) and the recycle (S5): one tee after it
    assert ids.index("TEE_FROM_C-101") == ids.index("C-101") + 1
    by_id = {
        s["id"]: s
        for s in structure["intermediate_streams"] + structure["product_streams"]
    }
    assert (
        by_id["S7"]["from_unit"] == "TEE_FROM_C-101"
        and by_id["S5"]["from_unit"] == "TEE_FROM_C-101"
    )
    link = by_id["S_TEE_FROM_C-101"]
    assert (link["from_unit"], link["to_unit"]) == ("C-101", "TEE_FROM_C-101")
    # separator V-102: two overhead gas outlets (S8 recycle gas, S16 purge) -> tee on the overhead port
    assert "TEE_FROM_V-102_OVERHEAD" in ids
    assert (
        by_id["S8"]["from_unit"]
        == by_id["S16"]["from_unit"]
        == "TEE_FROM_V-102_OVERHEAD"
    )
    assert by_id["S_TEE_FROM_V-102_OVERHEAD"]["connect_point"] == "overhead"
    assert by_id["S18"]["from_unit"] == "V-102"  # the bottom outlet is untouched
    # idempotent, and columns are never split
    again = normalize_splitting_points(copy.deepcopy(structure))
    assert [u["id"] for u in again["units"]] == ids
    coupled = couple_extraction(copy.deepcopy(again))
    assert "TEE_FROM_T-101" not in [u["id"] for u in coupled["units"]]


def test_pipeline_normalizer_order_keeps_overhead_system_cs4():
    """Coupling must run before the tees are inserted (pipeline step order).

    The reflux pump P-102 has two outlets (reflux to T-101, product to E-105).
    A tee inserted after it would hide the pump -> column edge that the
    coupling normaliser needs to fold condenser, drum and pump into the column.
    """
    structure = normalize_splitting_points(
        couple_extraction(normalize_mixing_points(_load("cs4_extraction.json")))
    )
    ids = [u["id"] for u in structure["units"]]
    for absorbed in ("E-104", "V-104", "P-102", "E-106"):
        assert absorbed not in ids
    assert "TEE_FROM_P-102" not in ids
    column = next(u for u in structure["units"] if u["id"] == "T-101")
    assert "integrated_overhead_system" in column["hysys_overrides"]
    # the tees that do belong are still inserted
    assert "TEE_FROM_C-101" in ids and "TEE_FROM_V-102_OVERHEAD" in ids
    assert "TEE_FROM_T-101" not in ids and not any("TEE_FROM_T-101" in i for i in ids)
    # the reverse order (the bug) routes the reflux through a tee: the pump's
    # outlets were moved to TEE_FROM_P-102 before the coupling ran, so the
    # column gets its reflux back from a tee instead of absorbing the pump
    wrong = couple_extraction(
        normalize_splitting_points(
            normalize_mixing_points(_load("cs4_extraction.json"))
        )
    )
    wrong_ids = [u["id"] for u in wrong["units"]]
    assert "TEE_FROM_P-102" in wrong_ids
    reflux = next(s for s in wrong["intermediate_streams"] if s["id"] == "S12A")
    assert (reflux["from_unit"], reflux["to_unit"]) == ("TEE_FROM_P-102", "T-101")


def test_splitting_normalizer_leaves_simple_cases_alone():
    for case in ("cs1_extraction.json", "cs2_extraction.json", "cs3_extraction.json"):
        structure = _load(case)
        before = [u["id"] for u in structure["units"]]
        after = normalize_splitting_points(normalize_mixing_points(structure))
        assert [
            u["id"] for u in after["units"] if not u["id"].startswith("MIXER_TO_")
        ] == before


def test_mixing_normalizer_tag_based_exclusion():
    # a pump merely *named* after a splitter still gets its mixer ...
    extraction = {
        "units": [{"id": "P-105", "name": "Splitter Feed Pump", "tags": ["pump"]}],
        "feed_streams": [
            {"id": "S20", "to_unit": "P-105", "name": "a", "connect_point": "side"},
            {"id": "S21", "to_unit": "P-105", "name": "b", "connect_point": "side"},
        ],
        "intermediate_streams": [],
        "product_streams": [],
    }
    out = normalize_mixing_points(copy.deepcopy(extraction))
    assert [u["id"] for u in out["units"]] == ["MIXER_TO_P-105", "P-105"]
    # ... while a unit tagged as a mixer or tee never does
    extraction["units"] = [{"id": "X-1", "name": "Pump header", "tags": ["mixer"]}]
    for s in extraction["feed_streams"]:
        s["to_unit"] = "X-1"
    out = normalize_mixing_points(copy.deepcopy(extraction))
    assert [u["id"] for u in out["units"]] == ["X-1"]


def _two_inlet_extraction(unit):
    return {
        "units": [unit],
        "feed_streams": [
            {"id": "S1", "to_unit": unit["id"], "name": "a", "connect_point": "side"},
            {"id": "S2", "to_unit": unit["id"], "name": "b", "connect_point": "side"},
        ],
        "intermediate_streams": [],
        "product_streams": [],
    }


def test_mixing_normalizer_single_inlet_hints():
    """Heaters, coolers, furnaces, expanders and pipes take one process inlet;
    a two-stream heat exchanger takes two and never gets a mixer."""
    heater = {"id": "H-101", "name": "Feed Heater", "tags": ["H-101"]}
    out = normalize_mixing_points(_two_inlet_extraction(heater))
    assert [u["id"] for u in out["units"]] == ["MIXER_TO_H-101", "H-101"]
    assert {s["to_unit"] for s in out["feed_streams"]} == {"MIXER_TO_H-101"}
    (link,) = out["intermediate_streams"]
    assert (link["from_unit"], link["to_unit"]) == ("MIXER_TO_H-101", "H-101")

    for unit in (
        {"id": "E-201", "name": "Product Cooler", "tags": ["cooler"]},
        {"id": "H-201", "name": "Furnace", "tags": ["fired heater"]},
        {"id": "K-201", "name": "Turbo Expander", "tags": ["expander"]},
        {"id": "L-201", "name": "Transfer Line", "tags": ["pipe segment"]},
    ):
        out = normalize_mixing_points(_two_inlet_extraction(unit))
        assert [u["id"] for u in out["units"]] == [
            f"MIXER_TO_{unit['id']}",
            unit["id"],
        ], unit

    for unit in (
        {
            "id": "E-202",
            "name": "Heat Exchanger",
            "tags": ["heat exchanger", "shell and tube"],
        },
        {"id": "E-203", "name": "Feed/Effluent Exchanger", "tags": ["exchanger"]},
    ):
        out = normalize_mixing_points(_two_inlet_extraction(unit))
        assert [u["id"] for u in out["units"]] == [unit["id"]], unit
        assert out["intermediate_streams"] == []
        assert {s["to_unit"] for s in out["feed_streams"]} == {unit["id"]}


def test_mixing_normalizer_fired_heater_cs4_first_run():
    """cs4 first run: hydrogen, preheated toluene and recycle gas all enter the
    fired heater H-101; they now pass through one mixer (one tube pass)."""
    structure = normalize_mixing_points(_load("cs4_first_run_extraction.json"))
    ids = [u["id"] for u in structure["units"]]
    assert ids.index("MIXER_TO_H-101") == ids.index("H-101") - 1
    inbound = sorted(
        s["id"]
        for s in structure["feed_streams"] + structure["intermediate_streams"]
        if s["to_unit"] == "MIXER_TO_H-101"
    )
    assert inbound == ["S3", "S4", "S5"]
    assert [
        s["id"]
        for s in structure["feed_streams"] + structure["intermediate_streams"]
        if s["to_unit"] == "H-101"
    ] == ["S_MIXER_TO_H-101"]


def test_coupling_folds_partial_overhead_system_cs4_first_run():
    """cs4 first run: the extractor lost the condenser -> reflux drum stream.

    T-101 -> E-104 (condenser, no outlet drawn); V-104 "Reflux Drum" feeds the
    reflux pumps P-102A/B (S14) and vents S19; P-102A/B returns the reflux S12
    to T-101 and sends the net benzene S20 to the product cooler E-105. The
    drum and the pumps are still folded into the column.
    """
    structure = couple_extraction(
        normalize_mixing_points(_load("cs4_first_run_extraction.json"))
    )
    ids = [u["id"] for u in structure["units"]]
    for absorbed in ("E-104", "V-104", "P-102A/B", "E-106"):
        assert absorbed not in ids
    assert "E-105" in ids  # the product cooler is a real downstream unit
    column = next(u for u in structure["units"] if u["id"] == "T-101")
    overrides = column["hysys_overrides"]
    assert overrides["integrated_overhead_system"] == {
        "unit_ids": ["E-104", "V-104", "P-102A/B"],
        "evidence_streams": ["S13", "S14", "S12"],
    }
    assert "integrated_condenser" not in overrides
    assert overrides["integrated_reboiler"]["unit_id"] == "E-106"
    assert set(overrides["skip_unit_ids"]) == {"E-106", "E-104", "V-104", "P-102A/B"}
    # the internal loops are gone, the net product now leaves the column
    assert {s["id"] for s in structure["intermediate_streams"]}.isdisjoint(
        {"S11", "S12", "S13", "S14"}
    )
    net_product = next(s for s in structure["intermediate_streams"] if s["id"] == "S20")
    assert (net_product["from_unit"], net_product["to_unit"]) == ("T-101", "E-105")
    assert net_product["integrated_via"] == "P-102A/B"

    view = build_unit_view_from_structure(structure)
    column_view = next(u for u in view["units_unit_view"] if u["unit_id"] == "T-101")
    # the reflux return is no longer an inlet of the column
    assert [s["id"] for s in column_view["streams_in"]] == ["S18"]
    out = {s["id"]: s for s in column_view["streams_out"]}
    assert set(out) == {"S19", "S20"}
    assert out["S19"]["integrated_via"] == "V-104"  # fuel-gas vent
    assert out["S20"]["integrated_via"] == "P-102A/B"  # net distillate
    assert out["S20"]["to_unit"] == "E-105"


def _stabilizer_extraction():
    return {
        "units": [
            {"id": "T-1", "name": "Stabilizer Column", "tags": ["column"]},
            {"id": "E-1", "name": "Overhead Condenser", "tags": ["condenser"]},
            {"id": "V-1", "name": "Reflux Drum", "tags": ["drum"]},
            {"id": "E-2", "name": "Bottoms Cooler", "tags": ["cooler"]},
        ],
        "feed_streams": [
            {"id": "S1", "to_unit": "T-1", "name": "Feed", "connect_point": "side"},
        ],
        "intermediate_streams": [
            {
                "id": "S2",
                "from_unit": "T-1",
                "to_unit": "E-1",
                "name": "Overhead",
                "connect_point": "overhead",
            },
            {
                "id": "S3",
                "from_unit": "E-1",
                "to_unit": "V-1",
                "name": "Condensate",
                "connect_point": "side",
            },
            {
                "id": "S4",
                "from_unit": "V-1",
                "to_unit": "T-1",
                "name": "Reflux",
                "connect_point": "overhead",
            },
            {
                "id": "S5",
                "from_unit": "T-1",
                "to_unit": "E-2",
                "name": "Bottoms",
                "connect_point": "bottom",
            },
        ],
        "product_streams": [
            {
                "id": "S6",
                "from_unit": "V-1",
                "name": "Distillate",
                "connect_point": "bottom",
            },
            {
                "id": "S7",
                "from_unit": "V-1",
                "name": "Vent gas",
                "connect_point": "overhead",
            },
            {
                "id": "S8",
                "from_unit": "E-2",
                "name": "Product",
                "connect_point": "side",
            },
        ],
    }


def test_coupling_folds_reflux_drum_without_pump():
    structure = couple_extraction(_stabilizer_extraction())
    assert [u["id"] for u in structure["units"]] == ["T-1", "E-2"]
    column = structure["units"][0]
    assert column["hysys_overrides"]["integrated_overhead_system"] == {
        "unit_ids": ["E-1", "V-1"],
        "evidence_streams": ["S2", "S3", "S4"],
    }
    assert column["hysys_overrides"]["skip_unit_ids"] == ["E-1", "V-1"]
    products = {s["id"]: s for s in structure["product_streams"]}
    assert products["S6"]["from_unit"] == "T-1"
    assert products["S6"]["integrated_via"] == "V-1"
    assert products["S7"]["integrated_via"] == "V-1"
    assert [s["id"] for s in structure["intermediate_streams"]] == ["S5"]


def test_coupling_keeps_drum_that_carries_a_foreign_stream():
    """A "reflux drum" with a makeup feed from outside is not part of the
    overhead system; only the condenser is folded."""
    extraction = _stabilizer_extraction()
    extraction["feed_streams"].append(
        {"id": "S9", "to_unit": "V-1", "name": "Makeup", "connect_point": "side"}
    )
    structure = couple_extraction(extraction)
    assert [u["id"] for u in structure["units"]] == ["T-1", "V-1", "E-2"]
    overrides = structure["units"][0]["hysys_overrides"]
    assert overrides["integrated_condenser"]["unit_id"] == "E-1"
    assert "integrated_overhead_system" not in overrides
    assert overrides["skip_unit_ids"] == ["E-1"]
    # the drum keeps its external reflux return and its condensate inlet
    condensate = next(s for s in structure["intermediate_streams"] if s["id"] == "S3")
    assert (condensate["from_unit"], condensate["to_unit"]) == ("T-1", "V-1")
    assert condensate["integrated_via"] == "E-1"
    assert any(
        s["id"] == "S4" and (s["from_unit"], s["to_unit"]) == ("V-1", "T-1")
        for s in structure["intermediate_streams"]
    )


def test_unit_view_passes_stage_through():
    structure = {
        "units": [
            {"id": "T-1", "name": "Column", "tags": []},
            {"id": "E-1", "name": "Cooler", "tags": []},
        ],
        "feed_streams": [
            {
                "id": "S1",
                "to_unit": "T-1",
                "name": "Feed",
                "connect_point": "side",
                "stage": 7,
            },
            {
                "id": "S2",
                "to_unit": "T-1",
                "name": "Second feed",
                "connect_point": "side",
                "stage": None,
            },
        ],
        "intermediate_streams": [
            {
                "id": "S3",
                "from_unit": "T-1",
                "to_unit": "E-1",
                "name": "Side draw",
                "connect_point": "side",
                "stage": 12,
            }
        ],
        "product_streams": [
            {
                "id": "S4",
                "from_unit": "T-1",
                "name": "Bottoms",
                "connect_point": "bottom",
            },
            {
                "id": "S5",
                "from_unit": "T-1",
                "name": "Naphtha",
                "connect_point": "side",
                "stage": "3",
            },
        ],
    }
    view = build_unit_view_from_structure(structure)
    by_id = {u["unit_id"]: u for u in view["units_unit_view"]}
    column_in = {s["id"]: s for s in by_id["T-1"]["streams_in"]}
    assert column_in["S1"]["stage"] == 7
    assert "stage" not in column_in["S2"]  # null means "not stated"
    column_out = {s["id"]: s for s in by_id["T-1"]["streams_out"]}
    assert column_out["S3"]["stage"] == 12
    assert "stage" not in column_out["S4"]
    assert column_out["S5"]["stage"] == 3  # a numeric string is accepted
    (cooler_in,) = by_id["E-1"]["streams_in"]
    assert cooler_in["stage"] == 12 and cooler_in["from_unit"] == "T-1"
    # every existing key is still there
    assert set(column_in["S1"]) == {
        "id",
        "name",
        "role",
        "from_unit",
        "connect_point",
        "stage",
    }
    assert set(column_out["S4"]) == {"id", "name", "role", "to_unit", "connect_point"}


def test_extractor_schema_carries_optional_stage():
    from sketch2sim.agents.extractor_agent import EXTRACTION_JSON_SCHEMA

    assert EXTRACTION_JSON_SCHEMA["required"] == [
        "units",
        "feed_streams",
        "intermediate_streams",
        "product_streams",
    ]
    for group in ("feed_streams", "intermediate_streams", "product_streams"):
        item = EXTRACTION_JSON_SCHEMA["properties"][group]["items"]
        assert item["properties"]["stage"] == {"type": ["integer", "null"]}
        # strict Structured Outputs: every property is required, none is missed
        assert set(item["required"]) == set(item["properties"])
        assert item["required"][-1] == "stage"
    units = EXTRACTION_JSON_SCHEMA["properties"]["units"]["items"]
    assert "stage" not in units["properties"]
