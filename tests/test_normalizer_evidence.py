"""Fired heaters and two-stream exchangers recognised from their streams."""

from __future__ import annotations

import copy

from sketch2sim.mixing_points_normalizer import normalize_mixing_points
from sketch2sim.splitting_points_normalizer import normalize_splitting_points
from sketch2sim.utils.normalization_utils import (
    build_unit_view_from_structure,
    is_fired_heater,
)


def _feed(stream_id, to_unit, name, point="side"):
    return {"id": stream_id, "to_unit": to_unit, "name": name, "connect_point": point}


def _link(stream_id, from_unit, to_unit, name, point="side"):
    return {
        "id": stream_id,
        "from_unit": from_unit,
        "to_unit": to_unit,
        "name": name,
        "connect_point": point,
    }


def _product(stream_id, from_unit, name, point="side"):
    return {
        "id": stream_id,
        "from_unit": from_unit,
        "name": name,
        "connect_point": point,
    }


def test_feed_heater_with_air_and_combustion_products_only_cs4_v8():
    """cs4 third run: the extractor sent no fuel gas to H-101, only air in and
    combustion products out. Still a fired heater: the air is its burner feed
    (no mixer, no burner mixer) and the flue gas is not teed."""
    structure = {
        "units": [
            {"id": "E-101", "name": "Feed Preheater", "tags": ["E-101"]},
            {"id": "H-101", "name": "Feed Heater", "tags": ["H-101"]},
            {"id": "R-101", "name": "Reactor", "tags": ["R-101"]},
        ],
        "feed_streams": [_feed("Air", "H-101", "Air", "bottom")],
        "intermediate_streams": [
            _link("S4", "E-101", "H-101", "Preheated process feed"),
            _link("S6", "H-101", "R-101", "Heated reactor feed"),
        ],
        "product_streams": [
            _product("Combustion_products", "H-101", "Combustion products", "overhead")
        ],
    }
    out = normalize_splitting_points(normalize_mixing_points(copy.deepcopy(structure)))
    ids = [u["id"] for u in out["units"]]
    assert ids == ["E-101", "H-101", "R-101"]
    heater = next(u for u in out["units"] if u["id"] == "H-101")
    assert heater["tags"] == ["H-101", "fired heater"]
    assert {s["id"] for s in out["feed_streams"] if s["to_unit"] == "H-101"} == {"Air"}
    flue = next(s for s in out["product_streams"] if s["id"] == "Combustion_products")
    assert flue["from_unit"] == "H-101"


def test_fired_heater_evidence_guards():
    def unit_with(name, inbound_names, outbound_names):
        structure = {
            "units": [{"id": "U", "name": name, "tags": []}],
            "feed_streams": [
                _feed(f"in{i}", "U", n) for i, n in enumerate(inbound_names)
            ],
            "intermediate_streams": [],
            "product_streams": [
                _product(f"out{i}", "U", n) for i, n in enumerate(outbound_names)
            ],
        }
        return structure["units"][0], structure

    # a heater that burns fuel without a drawn flue gas is still a fired heater
    assert is_fired_heater(*unit_with("Crude Heater", ["crude", "fuel gas"], ["hot"]))
    # a heater whose process stream is the fuel is a plain heater
    assert not is_fired_heater(*unit_with("Fuel Gas Heater", ["fuel gas"], ["hot"]))
    # an air preheater receives the flue gas: not a fired heater
    assert not is_fired_heater(
        *unit_with(
            "Air Preheater", ["combustion air", "flue gas"], ["hot air", "stack"]
        )
    )
    # only heaters are promoted by stream evidence (an FCC regenerator is not)
    assert not is_fired_heater(
        *unit_with("Regenerator", ["air", "spent cat"], ["flue"])
    )
    # name wins whatever the streams say
    assert is_fired_heater(*unit_with("Furnace", ["oil"], ["hot oil"]))


def test_preheater_crossed_by_the_reactor_effluent_is_an_exchanger_cs4():
    """cs4 first and third runs: hydrogen, toluene and the reactor effluent all
    enter E-101 "Feed Preheater", which has two outlets. It is a two-stream
    exchanger: the effluent keeps its own side, only hydrogen + toluene are
    mixed, and no tee is inserted after it."""
    structure = {
        "units": [
            {"id": "P-101A/B", "name": "Toluene Feed Pumps", "tags": []},
            {"id": "E-101", "name": "Feed Preheater", "tags": ["E-101"]},
            {"id": "H-101", "name": "Fired Heater", "tags": []},
            {"id": "R-101", "name": "Reactor", "tags": []},
            {"id": "E-102", "name": "Reactor Effluent Cooler", "tags": []},
        ],
        "feed_streams": [
            _feed("S1", "P-101A/B", "Toluene"),
            _feed("S2", "E-101", "Hydrogen"),
        ],
        "intermediate_streams": [
            _link("S3", "P-101A/B", "E-101", "Pumped toluene"),
            _link("S7", "R-101", "E-101", "Reactor effluent", "bottom"),
            _link("S4", "E-101", "H-101", "Preheated process feed"),
            _link("S8", "E-101", "E-102", "Cooled reactor effluent"),
            _link("S6", "H-101", "R-101", "Heated reactor feed"),
        ],
        "product_streams": [_product("S9", "E-102", "Cooled effluent")],
    }
    out = normalize_mixing_points(copy.deepcopy(structure))
    ids = [u["id"] for u in out["units"]]
    assert ids.index("MIXER_TO_E-101") == ids.index("E-101") - 1
    exchanger = next(u for u in out["units"] if u["id"] == "E-101")
    assert exchanger["tags"] == ["E-101", "heat exchanger"]
    streams = out["feed_streams"] + out["intermediate_streams"]
    assert sorted(s["id"] for s in streams if s["to_unit"] == "MIXER_TO_E-101") == [
        "S2",
        "S3",
    ]
    assert sorted(s["id"] for s in streams if s["to_unit"] == "E-101") == [
        "S7",
        "S_MIXER_TO_E-101",
    ]
    # idempotent, and the splitting normaliser leaves the two outlets alone
    assert normalize_mixing_points(copy.deepcopy(out)) == out
    split = normalize_splitting_points(copy.deepcopy(out))
    assert "TEE_FROM_E-101" not in [u["id"] for u in split["units"]]
    view = build_unit_view_from_structure(split)
    entry = next(u for u in view["units_unit_view"] if u["unit_id"] == "E-101")
    assert "heat exchanger" in entry["tags"]
    assert len(entry["streams_in"]) == 2 and len(entry["streams_out"]) == 2


def test_two_inlet_two_outlet_heater_without_name_match_is_still_an_exchanger():
    structure = {
        "units": [{"id": "E-1", "name": "Crude Preheater", "tags": []}],
        "feed_streams": [_feed("S1", "E-1", "crude"), _feed("S2", "E-1", "residue")],
        "intermediate_streams": [],
        "product_streams": [
            _product("S3", "E-1", "warm crude"),
            _product("S4", "E-1", "cool"),
        ],
    }
    out = normalize_mixing_points(copy.deepcopy(structure))
    assert [u["id"] for u in out["units"]] == ["E-1"]
    assert out["units"][0]["tags"] == ["heat exchanger"]


def test_heater_with_one_outlet_keeps_its_mixer_and_a_three_inlet_two_outlet_without_match_too():
    one_outlet = {
        "units": [{"id": "E-1", "name": "Feed Heater", "tags": []}],
        "feed_streams": [_feed("S1", "E-1", "a"), _feed("S2", "E-1", "b")],
        "intermediate_streams": [],
        "product_streams": [_product("S3", "E-1", "hot")],
    }
    out = normalize_mixing_points(copy.deepcopy(one_outlet))
    assert [u["id"] for u in out["units"]] == ["MIXER_TO_E-1", "E-1"]
    assert out["units"][1]["tags"] == []
    no_match = {
        "units": [{"id": "E-1", "name": "Feed Heater", "tags": []}],
        "feed_streams": [
            _feed("S1", "E-1", "a"),
            _feed("S2", "E-1", "b"),
            _feed("S3", "E-1", "c"),
        ],
        "intermediate_streams": [],
        "product_streams": [_product("S4", "E-1", "x"), _product("S5", "E-1", "y")],
    }
    out = normalize_mixing_points(copy.deepcopy(no_match))
    # three inlets that no outlet continues: the old behaviour (one mixer)
    assert [u["id"] for u in out["units"]] == ["MIXER_TO_E-1", "E-1"]
    assert out["units"][1]["tags"] == []


def test_heat_duty_labels_drawn_as_streams_are_dropped_cs3_v9():
    """cs3 third run: the sketch label "Q-trim" was emitted as a column feed on
    stage 8. Duty labels are dropped by the first normaliser, listed under
    removed_streams, and the coupling normaliser keeps that list."""
    from sketch2sim.coupling_normalizer import couple_extraction

    structure = {
        "units": [
            {"id": "T-1", "name": "Atmospheric distillation column", "tags": []},
            {"id": "E-1", "name": "Condenser", "tags": []},
        ],
        "feed_streams": [
            {**_feed("Q-trim", "T-1", "Q-trim"), "stage": 8},
            {**_feed("Main steam", "T-1", "Main steam"), "stage": 12},
            _feed("S1", "T-1", "Quench water"),
            _feed("Q-101", "T-1", "Q-101"),
        ],
        "intermediate_streams": [_link("S2", "T-1", "E-1", "overhead", "overhead")],
        "product_streams": [
            _product("Qreb", "T-1", "Qreb", "bottom"),
            _product("S3", "E-1", "Naphtha"),
            _product("S4", "T-1", "Residue", "bottom"),
        ],
    }
    out = normalize_mixing_points(copy.deepcopy(structure))
    assert [s["id"] for s in out["feed_streams"]] == ["Main steam", "S1"]
    assert [s["id"] for s in out["product_streams"]] == ["S3", "S4"]
    assert {r["id"] for r in out["removed_streams"]} == {"Q-trim", "Q-101", "Qreb"}
    coupled = couple_extraction(out)
    assert {r["id"] for r in coupled["removed_streams"]} == {"Q-trim", "Q-101", "Qreb"}
    assert [u["id"] for u in coupled["units"]] == ["T-1"]
    # idempotent
    assert normalize_mixing_points(copy.deepcopy(out)) == out


def test_exchanger_with_three_inlets_always_keeps_one_side_for_the_mixer():
    """cs4 first-run names: both outlets find an inlet by name ("Toluene feed"
    -> "Preheated toluene feed", "Reactor effluent" -> "Cooled reactor
    effluent"); the weaker match joins the hydrogen in the mixer so that the
    exchanger ends with exactly two inlets."""
    structure = {
        "units": [{"id": "E-101", "name": "Feed Preheater", "tags": ["E-101"]}],
        "feed_streams": [_feed("S3", "E-101", "Hydrogen")],
        "intermediate_streams": [
            _link("P-101A/B_to_E-101", "P-101A/B", "E-101", "Toluene feed"),
            _link("R-101_to_E-101", "R-101", "E-101", "Reactor effluent"),
            _link("E-101_to_H-101", "E-101", "H-101", "Preheated toluene feed"),
            _link("E-101_to_E-102", "E-101", "E-102", "Cooled reactor effluent"),
        ],
        "product_streams": [],
    }
    out = normalize_mixing_points(copy.deepcopy(structure))
    streams = out["feed_streams"] + out["intermediate_streams"]
    assert sorted(s["id"] for s in streams if s["to_unit"] == "MIXER_TO_E-101") == [
        "P-101A/B_to_E-101",
        "S3",
    ]
    assert sorted(s["id"] for s in streams if s["to_unit"] == "E-101") == [
        "R-101_to_E-101",
        "S_MIXER_TO_E-101",
    ]
    # ids like "<unit>_to_<unit>" share tokens with every stream of the unit and
    # must not drive the pairing: with nameless outlets nothing matches by id
    nameless = copy.deepcopy(structure)
    for s in nameless["intermediate_streams"]:
        if s["from_unit"] == "E-101":
            s["name"] = ""
    out = normalize_mixing_points(nameless)
    # no name match and three inlets: the old one-mixer behaviour, no tag
    assert [u["id"] for u in out["units"]] == ["MIXER_TO_E-101", "E-101"]
    assert "heat exchanger" not in out["units"][1]["tags"]
    # three outlets never make an exchanger (two ports): mixer + tee as before
    three_out = copy.deepcopy(structure)
    three_out["product_streams"] = [_product("S9", "E-101", "Drain")]
    out = normalize_splitting_points(normalize_mixing_points(three_out))
    assert (
        "heat exchanger"
        not in next(u for u in out["units"] if u["id"] == "E-101")["tags"]
    )
    assert "TEE_FROM_E-101" in [u["id"] for u in out["units"]]


def test_combustion_air_and_natural_gas_are_burner_streams_not_flue_gas():
    heater = {
        "units": [
            {"id": "H-101", "name": "Feed Heater", "tags": []},
            {"id": "R-101", "name": "Reactor", "tags": []},
        ],
        "feed_streams": [
            _feed("S1", "H-101", "Preheated feed"),
            _feed("AIR", "H-101", "Combustion air"),
            _feed("NG", "H-101", "Natural gas"),
        ],
        "intermediate_streams": [_link("S6", "H-101", "R-101", "Heated feed")],
        "product_streams": [_product("FLUE", "H-101", "Combustion products")],
    }
    out = normalize_splitting_points(normalize_mixing_points(copy.deepcopy(heater)))
    ids = [u["id"] for u in out["units"]]
    assert ids == ["MIXER_TO_H-101_BURNER", "H-101", "R-101"]
    streams = out["feed_streams"] + out["intermediate_streams"]
    assert sorted(
        s["id"] for s in streams if s["to_unit"] == "MIXER_TO_H-101_BURNER"
    ) == [
        "AIR",
        "NG",
    ]
    assert sorted(s["id"] for s in streams if s["to_unit"] == "H-101") == [
        "S1",
        "S_MIXER_TO_H-101_BURNER",
    ]
    assert "fired heater" in next(u for u in out["units"] if u["id"] == "H-101")["tags"]


def test_air_preheater_is_an_exchanger_and_never_promoted_later():
    """Flue gas in and stack gas out: an exchanger (both sides matched by name),
    and the splitting normaliser must not promote it to a fired heater."""
    aph = {
        "units": [{"id": "E-9", "name": "Air Preheater", "tags": []}],
        "feed_streams": [
            _feed("A1", "E-9", "Combustion air"),
            _feed("F1", "E-9", "Flue gas"),
        ],
        "intermediate_streams": [],
        "product_streams": [
            _product("A2", "E-9", "Hot combustion air"),
            _product("F2", "E-9", "Flue gas to stack"),
        ],
    }
    out = normalize_splitting_points(normalize_mixing_points(copy.deepcopy(aph)))
    assert [u["id"] for u in out["units"]] == ["E-9"]
    assert out["units"][0]["tags"] == ["heat exchanger"]
    # and a heater whose inlets were merged by the first normaliser is not
    # re-judged by the second one on the mixer outlet alone
    plain = {
        "units": [{"id": "H-2", "name": "Charge Heater", "tags": []}],
        "feed_streams": [_feed("S1", "H-2", "crude"), _feed("S2", "H-2", "Flue gas")],
        "intermediate_streams": [],
        "product_streams": [_product("S3", "H-2", "Stack gas")],
    }
    out = normalize_splitting_points(normalize_mixing_points(copy.deepcopy(plain)))
    assert [u["id"] for u in out["units"]] == ["MIXER_TO_H-2", "H-2"]
    assert "fired heater" not in out["units"][1]["tags"]


def test_reflux_names_and_reflux_leaving_the_folded_overhead_system():
    from sketch2sim.coupling_normalizer import couple_extraction

    def column_with(product_name, from_unit="T-1"):
        return {
            "units": [
                {"id": "T-1", "name": "Benzene Column", "tags": []},
                {"id": "E-1", "name": "Condenser", "tags": []},
                {"id": "V-1", "name": "Reflux Drum", "tags": []},
                {"id": "P-1", "name": "Reflux Pumps", "tags": []},
            ],
            "feed_streams": [_feed("S0", "T-1", "feed")],
            "intermediate_streams": [
                _link("S1", "T-1", "E-1", "overhead", "overhead"),
                _link("S2", "E-1", "V-1", "condensed overhead"),
                _link("S3", "V-1", "P-1", "reflux drum liquid", "bottom"),
                _link("S4", "P-1", "T-1", "Reflux", "overhead"),
            ],
            "product_streams": [
                _product("X", from_unit, product_name),
                _product("S5", "T-1", "Bottoms", "bottom"),
            ],
        }

    dropped = [
        "Reflux",
        "Reflux to column",
        "reflux (L)",
        "Liquid reflux",
        "Top reflux",
    ]
    kept = ["Reflux drum vent gas", "Top circulating reflux draw", "Distillate"]
    for name in dropped:
        out = couple_extraction(column_with(name))
        assert [s["id"] for s in out["product_streams"]] == ["S5"], name
    for name in kept:
        out = couple_extraction(column_with(name))
        assert [s["id"] for s in out["product_streams"]] == ["X", "S5"], name
    # the reflux drawn as a product leaving the folded pump or drum is internal
    for unit in ("P-1", "V-1", "E-1"):
        out = couple_extraction(column_with("Reflux", from_unit=unit))
        assert [s["id"] for s in out["product_streams"]] == ["S5"], unit
    out = couple_extraction(column_with("Reflux drum vent", from_unit="V-1"))
    assert [s["id"] for s in out["product_streams"]] == ["X", "S5"]
