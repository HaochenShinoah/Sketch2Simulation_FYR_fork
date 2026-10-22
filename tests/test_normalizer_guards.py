"""Normalizer guards: multi-inlet units, burner-side streams, flue gas, pumparounds."""

from __future__ import annotations

import copy

from sketch2sim.coupling_normalizer import couple_extraction
from sketch2sim.mixing_points_normalizer import normalize_mixing_points
from sketch2sim.splitting_points_normalizer import normalize_splitting_points


def _two_inlet_unit(unit, stream_names=("a", "b")):
    return {
        "units": [unit],
        "feed_streams": [
            {"id": f"S{i}", "to_unit": unit["id"], "name": n, "connect_point": "side"}
            for i, n in enumerate(stream_names, 1)
        ],
        "intermediate_streams": [],
        "product_streams": [],
    }


def test_mixing_normalizer_leaves_reactors_columns_and_exchangers_alone():
    for unit in (
        {"id": "R-1", "name": "Cracking Furnace", "tags": ["reactor"]},
        {"id": "T-1", "name": "Stripper Feed Heater", "tags": ["column"]},
        {"id": "E-1", "name": "Feed/Effluent Exchanger", "tags": ["heater"]},
    ):
        out = normalize_mixing_points(copy.deepcopy(_two_inlet_unit(unit)))
        assert [u["id"] for u in out["units"]] == [unit["id"]], unit["name"]


def test_mixing_normalizer_keeps_burner_streams_off_the_furnace_pass():
    unit = {"id": "H-1", "name": "Fired Heater", "tags": ["furnace"]}
    # one process feed plus air: no mixer (air goes to the burner)
    out = normalize_mixing_points(
        copy.deepcopy(_two_inlet_unit(unit, ("crude", "combustion air")))
    )
    assert [u["id"] for u in out["units"]] == ["H-1"]
    # two process feeds plus fuel: the mixer merges only the process feeds
    out = normalize_mixing_points(
        copy.deepcopy(_two_inlet_unit(unit, ("crude", "naphtha", "fuel gas")))
    )
    assert [u["id"] for u in out["units"]] == ["MIXER_TO_H-1", "H-1"]
    to_mixer = [
        s["name"] for s in out["feed_streams"] if s["to_unit"] == "MIXER_TO_H-1"
    ]
    assert to_mixer == ["crude", "naphtha"]
    assert [s["name"] for s in out["feed_streams"] if s["to_unit"] == "H-1"] == [
        "fuel gas"
    ]


def test_splitting_normalizer_never_tees_the_flue_gas():
    structure = {
        "units": [{"id": "H-1", "name": "Fired Heater", "tags": ["furnace"]}],
        "feed_streams": [
            {"id": "S1", "to_unit": "H-1", "name": "crude", "connect_point": "side"}
        ],
        "intermediate_streams": [],
        "product_streams": [
            {
                "id": "S2",
                "from_unit": "H-1",
                "name": "hot crude",
                "connect_point": "side",
            },
            {
                "id": "S3",
                "from_unit": "H-1",
                "name": "Flue Gas",
                "connect_point": "side",
            },
        ],
    }
    out = normalize_splitting_points(copy.deepcopy(structure))
    assert [u["id"] for u in out["units"]] == ["H-1"]
    assert all(s["from_unit"] == "H-1" for s in out["product_streams"])


def test_coupling_does_not_fold_a_pumparound_accumulator():
    structure = {
        "units": [
            {"id": "T-1", "name": "Distillation Column", "tags": []},
            {"id": "E-1", "name": "Condenser", "tags": []},
            {"id": "V-9", "name": "Pumparound Accumulator", "tags": []},
            {"id": "P-9", "name": "Pumparound Pump", "tags": []},
        ],
        "feed_streams": [
            {"id": "S0", "to_unit": "T-1", "name": "feed", "connect_point": "side"}
        ],
        "intermediate_streams": [
            {
                "id": "S1",
                "from_unit": "T-1",
                "to_unit": "E-1",
                "name": "ovhd",
                "connect_point": "overhead",
            },
            {
                "id": "S2",
                "from_unit": "T-1",
                "to_unit": "V-9",
                "name": "draw",
                "connect_point": "side",
            },
            {
                "id": "S3",
                "from_unit": "V-9",
                "to_unit": "P-9",
                "name": "liquid",
                "connect_point": "bottom",
            },
            {
                "id": "S4",
                "from_unit": "P-9",
                "to_unit": "T-1",
                "name": "return",
                "connect_point": "side",
            },
        ],
        "product_streams": [
            {
                "id": "S5",
                "from_unit": "E-1",
                "name": "distillate",
                "connect_point": "side",
            },
            {
                "id": "S6",
                "from_unit": "T-1",
                "name": "bottoms",
                "connect_point": "bottom",
            },
        ],
    }
    out = couple_extraction(copy.deepcopy(structure))
    ids = [u["id"] for u in out["units"]]
    assert "V-9" in ids and "P-9" in ids and "E-1" not in ids


def test_fired_heater_recognised_from_its_streams_cs4():
    """cs4 (second run): H-101 "Feed Heater" is drawn with air, fuel gas and
    combustion products. It is a fired heater: no mixer in front of its tube
    pass, fuel + air merged into one burner feed, the flue gas never teed, and
    the unit tagged so that the coding agents see it."""
    from sketch2sim.utils.normalization_utils import build_unit_view_from_structure

    structure = {
        "units": [
            {"id": "E-101", "name": "Feed Preheater", "tags": ["E-101"]},
            {"id": "H-101", "name": "Feed Heater", "tags": ["H-101"]},
            {"id": "R-101", "name": "Reactor", "tags": ["R-101"]},
            {"id": "V-103", "name": "Low-Pressure Separator", "tags": ["V-103"]},
        ],
        "feed_streams": [
            {
                "id": "S1",
                "to_unit": "E-101",
                "name": "Toluene",
                "connect_point": "side",
            },
            {"id": "Air", "to_unit": "H-101", "name": "Air", "connect_point": "bottom"},
        ],
        "intermediate_streams": [
            {
                "id": "S4",
                "from_unit": "E-101",
                "to_unit": "H-101",
                "name": "Preheated Feed",
                "connect_point": "side",
            },
            {
                "id": "S17",
                "from_unit": "V-103",
                "to_unit": "H-101",
                "name": "Fuel Gas",
                "connect_point": "overhead",
            },
            {
                "id": "S6",
                "from_unit": "H-101",
                "to_unit": "R-101",
                "name": "Heated Reactant Feed",
                "connect_point": "side",
            },
        ],
        "product_streams": [
            {
                "id": "Combustion_Products",
                "from_unit": "H-101",
                "name": "Combustion Products",
                "connect_point": "overhead",
            },
            {
                "id": "S16",
                "from_unit": "V-103",
                "name": "Fuel Gas",
                "connect_point": "overhead",
            },
            {
                "id": "S9",
                "from_unit": "R-101",
                "name": "Effluent",
                "connect_point": "bottom",
            },
        ],
    }
    out = normalize_mixing_points(copy.deepcopy(structure))
    ids = [u["id"] for u in out["units"]]
    assert "MIXER_TO_H-101" not in ids
    assert ids.index("MIXER_TO_H-101_BURNER") == ids.index("H-101") - 1
    heater = next(u for u in out["units"] if u["id"] == "H-101")
    assert heater["tags"] == ["H-101", "fired heater"]
    streams = out["feed_streams"] + out["intermediate_streams"]
    to_burner = sorted(
        s["id"] for s in streams if s["to_unit"] == "MIXER_TO_H-101_BURNER"
    )
    assert to_burner == ["Air", "S17"]
    into_heater = {s["id"]: s for s in streams if s["to_unit"] == "H-101"}
    assert sorted(into_heater) == ["S4", "S_MIXER_TO_H-101_BURNER"]
    assert into_heater["S_MIXER_TO_H-101_BURNER"]["name"] == "Burner feed to H-101"
    # idempotent
    assert normalize_mixing_points(copy.deepcopy(out)) == out
    # the splitting normaliser leaves the flue gas on the heater
    split = normalize_splitting_points(copy.deepcopy(out))
    assert "TEE_FROM_H-101" not in [u["id"] for u in split["units"]]
    flue = next(s for s in split["product_streams"] if s["id"] == "Combustion_Products")
    assert flue["from_unit"] == "H-101"
    # the unit view carries the tag to the coding agents
    view = build_unit_view_from_structure(split)
    entry = next(u for u in view["units_unit_view"] if u["unit_id"] == "H-101")
    assert "fired heater" in entry["tags"]


def test_heater_whose_process_stream_is_the_fuel_is_a_plain_heater():
    plain = {
        "units": [{"id": "E-1", "name": "Fuel Gas Heater", "tags": []}],
        "feed_streams": [
            {"id": "S1", "to_unit": "E-1", "name": "crude", "connect_point": "side"},
            {"id": "S2", "to_unit": "E-1", "name": "fuel gas", "connect_point": "side"},
        ],
        "intermediate_streams": [],
        "product_streams": [
            {
                "id": "S3",
                "from_unit": "E-1",
                "name": "hot crude",
                "connect_point": "side",
            }
        ],
    }
    out = normalize_mixing_points(copy.deepcopy(plain))
    assert [u["id"] for u in out["units"]] == ["MIXER_TO_E-1", "E-1"]
    assert out["units"][1]["tags"] == []


def test_coupling_drops_condenser_utilities_and_internal_reflux():
    structure = {
        "units": [
            {"id": "T-1", "name": "Benzene Column", "tags": []},
            {"id": "E-1", "name": "Condenser", "tags": []},
            {"id": "E-2", "name": "Reboiler", "tags": []},
        ],
        "feed_streams": [
            {"id": "S0", "to_unit": "T-1", "name": "feed", "connect_point": "side"},
            {
                "id": "CW_in",
                "to_unit": "E-1",
                "name": "Cooling Water",
                "connect_point": "side",
            },
            {
                "id": "Steam",
                "to_unit": "E-2",
                "name": "LP Steam",
                "connect_point": "side",
            },
        ],
        "intermediate_streams": [
            {
                "id": "S1",
                "from_unit": "T-1",
                "to_unit": "E-1",
                "name": "ovhd vapour",
                "connect_point": "overhead",
            },
            {
                "id": "S6",
                "from_unit": "T-1",
                "to_unit": "E-2",
                "name": "to reboiler",
                "connect_point": "bottom",
            },
        ],
        "product_streams": [
            {
                "id": "S2",
                "from_unit": "E-1",
                "name": "Distillate",
                "connect_point": "side",
            },
            {
                "id": "CW_out",
                "from_unit": "E-1",
                "name": "Cooling Water",
                "connect_point": "side",
            },
            {
                "id": "S3",
                "from_unit": "E-1",
                "name": "Waste water",
                "connect_point": "bottom",
            },
            {"id": "S4", "from_unit": "T-1", "name": "Reflux", "connect_point": "side"},
            {
                "id": "S5",
                "from_unit": "E-2",
                "name": "Bottoms",
                "connect_point": "bottom",
            },
            {
                "id": "S7",
                "from_unit": "E-2",
                "name": "Condensate",
                "connect_point": "bottom",
            },
        ],
    }
    out = couple_extraction(copy.deepcopy(structure))
    assert [u["id"] for u in out["units"]] == ["T-1"]
    assert [s["id"] for s in out["feed_streams"]] == ["S0"]
    assert [s["id"] for s in out["product_streams"]] == ["S2", "S3", "S5"]
    assert out["product_streams"][0]["integrated_via"] == "E-1"
    assert out["product_streams"][2]["integrated_via"] == "E-2"
    removed = {r["id"]: r["reason"] for r in out["removed_streams"]}
    assert set(removed) == {"CW_in", "Steam", "CW_out", "S4", "S7"}
    assert "utility" in removed["CW_out"] and "reflux" in removed["S4"]
    # a condenser named "Condensate" product is the distillate, not a utility
    structure["product_streams"][0]["name"] = "Condensate"
    out = couple_extraction(copy.deepcopy(structure))
    assert [s["id"] for s in out["product_streams"]] == ["S2", "S3", "S5"]
