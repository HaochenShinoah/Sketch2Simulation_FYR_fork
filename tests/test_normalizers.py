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
    # the reverse order (the bug) loses the overhead system
    wrong = couple_extraction(
        normalize_splitting_points(
            normalize_mixing_points(_load("cs4_extraction.json"))
        )
    )
    wrong_ids = [u["id"] for u in wrong["units"]]
    assert "P-102" in wrong_ids and "TEE_FROM_P-102" in wrong_ids


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
