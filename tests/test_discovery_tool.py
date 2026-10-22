"""The COM discovery tool must probe every category and role the script template uses."""

from __future__ import annotations

import importlib.util

import pytest

from tests.conftest import REPO_ROOT

# Type strings verified on Aspen HYSYS V11 (2026-10-22) that the tool must keep
# trying even if the template lists lose them.
VERIFIED_EXTRAS = {
    "separator_3phase": "sep3op",
    "reactor_cstr": "kineticreactorop",
    "reactor_pfr": "pfreactorop",
    "column_3phase": "threephase",
    "column_extractor": "liqliqextractor",
}


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location(
        "hysys_discover_com_api", REPO_ROOT / "tools" / "hysys_discover_com_api.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # imports no win32com at module level
    return module


def test_discovery_tool_covers_template_candidates(tool):
    types, ports = tool.load_template_lists()
    assert types and ports

    candidates = tool.build_candidate_type_names(types)
    for category, names in types.items():
        # the template's candidates come first and keep their order
        assert candidates[category][: len(names)] == names, category
        assert category in tool.FAMILY_BY_CATEGORY, category
        assert tool.FAMILY_BY_CATEGORY[category] in tool.ROLES_BY_FAMILY, category
    # the live-verified 2-phase separator name is tried first
    assert candidates["separator"][0] == "flashtank"
    # the V11-verified strings are tried whatever the template says
    for category, name in VERIFIED_EXTRAS.items():
        assert name in candidates[category], category
    # extras never shadow a template spelling (case-insensitive de-duplication)
    for names in candidates.values():
        assert len({n.lower() for n in names}) == len(names)

    probes = tool.build_connection_probes(ports)
    roles_probed = set()
    for family, roles in tool.ROLES_BY_FAMILY.items():
        for role in roles:
            roles_probed.add(role)
            for member in ports[role]:
                assert tool.probe_from_member(member) in probes[family], (
                    family,
                    role,
                    member,
                )
    # every PORTS role of the template is probed by some family
    assert set(ports) <= roles_probed
    assert tool.probe_from_member("Feeds.Add") == ("add", "Feeds")
    assert tool.probe_from_member("FeedStream") == ("set", "FeedStream")


def test_every_column_category_gets_the_column_probe(tool):
    types, _ = tool.load_template_lists()
    columns = {key for key in types if key.startswith("column_")}
    assert columns
    for category in columns:
        assert tool.FAMILY_BY_CATEGORY[category] in tool.COLUMN_FAMILIES, category
    # the column probe covers tray feeds, both feed members, the four product members
    assert set(tool.COLUMN_PRODUCT_MEMBERS) == {
        "TopVapourProduct",
        "TopLiquidProduct",
        "TopWaterProduct",
        "BtmLiquidProduct",
    }
    assert set(tool.COLUMN_FEED_MEMBERS) == {"TopLiquidFeed", "BtmVapourFeed"}


def test_every_probe_is_read_back_from_the_right_collection(tool):
    _, ports = tool.load_template_lists()
    probes = tool.build_connection_probes(ports)
    sides = tool.build_probe_sides(ports)
    for family, bucket in probes.items():
        for probe in bucket:
            assert sides[probe] in {"in", "out", "any"}, (family, probe)
    # roles ending in "_in" are feeds, every other role is a product
    assert tool.role_side("single_in") == "in"
    assert tool.role_side("heavy_liquid_out") == "out"
    assert tool.role_side("splitter_bottom") == "out"
    assert sides[("set", "FeedStream")] == "in"
    assert sides[("add", "RadInlet")] == "in"
    assert sides[("set", "HeavyLiquidProduct")] == "out"
    assert sides[("set_energy", "EnergyStream")] == "any"
    # the PFR family probes Feeds.Add and Product (its only outlet member)
    assert ("set", "Product") in probes["pfr"]
    assert tool.FAMILY_BY_CATEGORY["reactor_pfr"] == "pfr"


def test_probe_status_separates_silent_no_ops_from_attachments(tool):
    assert tool.probe_status(None, True) == "attached"
    assert tool.probe_status(None, False) == "accepted but not attached"
    assert tool.probe_status(None, None) == "accepted (read-back unavailable)"
    assert tool.probe_status("com_error: E_FAIL", None) == "FAILED: com_error: E_FAIL"


def test_try_add_uses_the_single_verified_call_shape(tool):
    calls = []

    class FakeOperations:
        def Add(self, *args):
            calls.append(args)
            if args[1] == "nope":
                raise RuntimeError("E_FAIL")
            return object() if args[1] != "none" else None

    class Flowsheet:
        Operations = FakeOperations()

    op, error = tool.try_add(Flowsheet(), "U1", "pumpop")
    assert op is not None and error is None
    assert calls == [("U1", "pumpop")]
    op, error = tool.try_add(Flowsheet(), "U2", "nope")
    assert op is None and "E_FAIL" in error
    op, error = tool.try_add(Flowsheet(), "U3", "none")
    assert op is None and "returned None" in error
