"""The COM discovery tool must try every candidate the script template uses."""

from __future__ import annotations

import importlib.util

from tests.conftest import REPO_ROOT


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "hysys_discover_com_api", REPO_ROOT / "tools" / "hysys_discover_com_api.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # imports no win32com at module level
    return module


def test_discovery_tool_covers_template_candidates():
    tool = _load_tool()
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
