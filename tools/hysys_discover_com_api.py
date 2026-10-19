"""Probe the Aspen HYSYS COM object model on a Windows machine with HYSYS.

Why this exists
---------------
The two instruction files used by the Instantiation and Configuration agents
(``sketch2sim/instructions/*_instructions_simple_v5.txt``) were recreated
without access to a HYSYS installation. Everything marked VERIFIED in them was
taken from working public code; everything marked CANDIDATE is a best guess
with fallbacks. Run this script once on the HYSYS laptop to turn guesses into
facts: it reads the ``TYPES`` and ``PORTS`` candidate lists straight from the
script template (``sketch2sim/instructions/hysys_template_base.py``), tries
every type string with ``Operations.Add`` (in the template's order, with the
three call shapes the template uses), lists the stream-port members of each
object that HYSYS creates, probes every port member of the template with real
streams, and writes a report that says, per unit category, whether the
template's first choice works.

Usage (from the repository root, in the project's virtual environment)::

    python tools/hysys_discover_com_api.py            # writes logs/hysys_com_discovery.{json,md}
    python tools/hysys_discover_com_api.py --visible  # keep the HYSYS window visible

The scratch case is never saved. Close HYSYS afterwards if it stays open.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1]
    / "sketch2sim"
    / "instructions"
    / "hysys_template_base.py"
)


def load_template_lists(
    path: str | Path = TEMPLATE_PATH,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Read the TYPES and PORTS dictionaries from the template without running it."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    found: dict[str, dict[str, list[str]]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in {"TYPES", "PORTS"}:
                found[target.id] = ast.literal_eval(node.value)
    return found.get("TYPES", {}), found.get("PORTS", {})


# Type strings tried in addition to the template's TYPES: further spellings for
# template categories, and categories the agents do not use (reported for
# completeness). Template candidates always come first and keep their order.
EXTRA_TYPE_NAMES: dict[str, list[str]] = {
    "separator": ["flashop"],
    "reactor_pfr": ["pfr"],
    "column_reboiled_absorber": ["reboiledabsorberop"],
    "column_refluxed_absorber": ["refluxedabsorberop"],
    "lng_exchanger": ["lngop", "lngexchangerop"],
    "reactor_yield_shift": ["yieldshiftreactorop", "yieldreactorop"],
    "column_liquid_liquid_extractor": ["liquidliquidextractor", "extractor", "lleop"],
    "balance": ["balanceop", "balance"],
    "stream_cutter": ["streamcutterop"],
    "relief_valve": ["reliefvalveop", "reliefvalve"],
    "ejector": ["ejectorop", "ejector"],
}


def build_candidate_type_names(
    template_types: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    """Template TYPES first (same keys, same order), then the extra guesses."""
    if template_types is None:
        template_types, _ = load_template_lists()
    merged: dict[str, list[str]] = {k: list(v) for k, v in template_types.items()}
    for category, names in EXTRA_TYPE_NAMES.items():
        bucket = merged.setdefault(category, [])
        lower = {name.lower() for name in bucket}
        for name in names:
            if name.lower() not in lower:
                bucket.append(name)
                lower.add(name.lower())
    return merged


# Which connection family each category belongs to, and which template PORTS
# roles (hence which members) are probed for that family.
FAMILY_BY_CATEGORY: dict[str, str] = {
    "pump": "single_port",
    "compressor": "single_port",
    "expander": "single_port",
    "valve": "single_port",
    "heater": "single_port",
    "cooler": "single_port",
    "air_cooler": "single_port",
    "recycle": "single_port",
    "pipe": "single_port",
    "relief_valve": "single_port",
    "mixer": "mixer",
    "tee": "tee",
    "separator": "vessel",
    "separator_3phase": "vessel",
    "tank": "vessel",
    "reactor_conversion": "vessel",
    "reactor_equilibrium": "vessel",
    "reactor_gibbs": "vessel",
    "reactor_cstr": "vessel",
    "reactor_pfr": "vessel",
    "reactor_yield_shift": "vessel",
    "ejector": "vessel",
    "heat_exchanger": "heat_exchanger",
    "lng_exchanger": "heat_exchanger",
    "component_splitter": "splitter",
    "fired_heater": "fired_heater",
    "column_distillation": "column",
    "column_absorber": "column",
    "column_reboiled_absorber": "column",
    "column_refluxed_absorber": "column",
    "column_shortcut": "column",
    "column_liquid_liquid_extractor": "column",
    "adjust": "logical",
    "set": "logical",
    "spreadsheet": "logical",
    "balance": "logical",
    "stream_cutter": "single_port",
}
ROLES_BY_FAMILY: dict[str, list[str]] = {
    "single_port": ["single_in", "single_out"],
    "mixer": ["multi_in", "mixer_out"],
    "tee": ["tee_in", "tee_out"],
    "vessel": [
        "multi_in",
        "vapour_out",
        "liquid_out",
        "light_liquid_out",
        "heavy_liquid_out",
    ],
    "heat_exchanger": ["tube_in", "tube_out", "shell_in", "shell_out"],
    "splitter": ["splitter_in", "splitter_overhead", "splitter_bottom"],
    "fired_heater": ["fired_in", "fired_out", "flue_gas_out", "air_in", "fuel_in"],
    "column": ["multi_in"],
    "logical": [],
}
# Probes added on top of the template members (energy streams are not in PORTS).
EXTRA_PROBES: dict[str, list[tuple[str, str]]] = {
    "single_port": [("set_energy", "EnergyStream")],
    "vessel": [("set_energy", "EnergyStream")],
    "fired_heater": [("set_energy", "EnergyStream")],
}
COLUMN_FAMILIES = {"column"}


def probe_from_member(member: str) -> tuple[str, str]:
    """Template PORTS entry -> (kind, member): "Feeds.Add" is an add, bare names are sets."""
    if member.endswith(".Add"):
        return "add", member[:-4]
    return "set", member


def build_connection_probes(
    template_ports: dict[str, list[str]] | None = None,
) -> dict[str, list[tuple[str, str]]]:
    """Connection members to probe with real streams, per family, from the template PORTS."""
    if template_ports is None:
        _, template_ports = load_template_lists()
    probes: dict[str, list[tuple[str, str]]] = {}
    for family, roles in ROLES_BY_FAMILY.items():
        bucket: list[tuple[str, str]] = []
        for role in roles:
            for member in template_ports.get(role, []):
                probe = probe_from_member(member)
                if probe not in bucket:
                    bucket.append(probe)
        for probe in EXTRA_PROBES.get(family, []):
            if probe not in bucket:
                bucket.append(probe)
        probes[family] = bucket
    return probes


# Member names worth reporting for every created object.
PORT_PATTERN = re.compile(
    r"(Feed|Product|Stream|Vapour|Vapor|Liquid|Energy|Attach|Inlet|Outlet|Draw|"
    r"Overhead|Bottom|Distillate|Reflux|Duty|Tube|Shell|Hot|Cold)",
    re.IGNORECASE,
)


def member_table(com_obj) -> dict[str, list[str]]:
    """List members from the object's type info (works without makepy)."""
    kinds = {1: "method", 2: "get", 4: "put", 8: "putref"}
    members: dict[str, set[str]] = {}
    try:
        info = com_obj._oleobj_.GetTypeInfo()
        attr = info.GetTypeAttr()
        for index in range(attr.cFuncs):
            desc = info.GetFuncDesc(index)
            name = info.GetNames(desc.memid)[0]
            members.setdefault(name, set()).add(
                kinds.get(desc.invkind, str(desc.invkind))
            )
        for index in range(attr.cVars):
            desc = info.GetVarDesc(index)
            name = info.GetNames(desc.memid)[0]
            members.setdefault(name, set()).add("var")
    except Exception as error:  # noqa: BLE001
        members["__error__"] = {str(error)}
    return {name: sorted(kinds_) for name, kinds_ in sorted(members.items())}


def try_add(flowsheet, name: str, type_name: str):
    """Operations.Add with the three call shapes the template tries, in order."""
    errors = []
    for shape, call in (
        ("Add(name, type)", lambda: flowsheet.Operations.Add(name, type_name)),
        ("Add(type, name)", lambda: flowsheet.Operations.Add(type_name, name)),
        ("Add(type)", lambda: flowsheet.Operations.Add(type_name)),
    ):
        try:
            op = call()
        except Exception as error:  # noqa: BLE001
            errors.append(f"{shape}: {type(error).__name__}: {error}")
            continue
        if op is None:
            errors.append(f"{shape}: Operations.Add returned None")
            continue
        return op, shape, None
    return None, None, " | ".join(errors)


def probe_connections(
    op, probes: list[tuple[str, str]], flowsheet, counter: list[int]
) -> dict[str, str]:
    results: dict[str, str] = {}
    for kind, member in probes:
        counter[0] += 1
        stream_name = f"TMP_S{counter[0]}"
        try:
            if kind == "set_energy":
                stream = flowsheet.EnergyStreams.Add(stream_name)
            else:
                stream = flowsheet.MaterialStreams.Add(stream_name)
        except Exception as error:  # noqa: BLE001
            results[f"{kind}:{member}"] = f"stream creation failed: {error}"
            continue
        try:
            if kind == "add":
                getattr(op, member).Add(stream)
            else:
                setattr(op, member, stream)
            results[f"{kind}:{member}"] = "OK"
        except Exception as error:  # noqa: BLE001
            results[f"{kind}:{member}"] = f"{type(error).__name__}: {str(error)[:160]}"
    return results


def probe_column(op) -> dict:
    report: dict = {}
    try:
        cf = op.ColumnFlowsheet
        report["column_flowsheet_members"] = member_table(cf)
        names = []
        try:
            names = list(cf.Operations.Names)
        except Exception:  # noqa: BLE001
            try:
                names = [cf.Operations.Item(i).name for i in range(cf.Operations.Count)]
            except Exception as error:  # noqa: BLE001
                report["operations_error"] = str(error)
        report["operations"] = names
        for sub_name in names:
            try:
                sub = cf.Operations.Item(sub_name)
                table = member_table(sub)
                report[f"members:{sub_name}"] = {
                    k: v
                    for k, v in table.items()
                    if k == "__error__"
                    or PORT_PATTERN.search(k)
                    or k in ("NumberOfTrays", "NumberOfStages")
                }
                if "__error__" in table:
                    print(
                        f"  warning: type info of {sub_name} unavailable: {table['__error__']}"
                    )
            except Exception as error:  # noqa: BLE001
                report[f"members:{sub_name}"] = {"__error__": str(error)}
    except Exception as error:  # noqa: BLE001
        report["error"] = f"{type(error).__name__}: {error}"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--visible", action="store_true", help="show the HYSYS window")
    parser.add_argument("--out-dir", default="logs")
    parser.add_argument("--progid", default="HYSYS.Application")
    args = parser.parse_args()

    try:
        from win32com.client import Dispatch
    except ImportError:
        print("pywin32 is required (pip install pywin32) and this must run on Windows.")
        return 2

    os.makedirs(args.out_dir, exist_ok=True)
    report: dict = {
        "started": datetime.now().isoformat(timespec="seconds"),
        "progid": args.progid,
    }

    hysys = Dispatch(args.progid)
    hysys.Visible = bool(args.visible)
    try:
        report["program_build"] = hysys.ProgramBuild
    except Exception:  # noqa: BLE001
        pass
    case = hysys.SimulationCases.Add("s2s_com_discovery")
    hybasis = case.BasisManager
    try:
        hybasis.StartBasisChange()
    except Exception as error:  # noqa: BLE001
        report["start_basis_change_error"] = str(error)
    fluidpkg = hybasis.FluidPackages.Add()
    for package in ("PengRob", "Peng-Robinson", "PengRobinson"):
        try:
            fluidpkg.PropertyPackageName = package
            report["property_package"] = {
                "set": package,
                "readback": str(fluidpkg.PropertyPackageName),
            }
            break
        except Exception as error:  # noqa: BLE001
            report.setdefault("property_package_errors", {})[package] = str(error)
    for comp in ("Methane", "Ethane", "H2O"):
        try:
            fluidpkg.Components.Add(comp)
        except Exception as error:  # noqa: BLE001
            report.setdefault("component_errors", {})[comp] = str(error)
    hybasis.EndBasisChange()
    flowsheet = case.Flowsheet
    report["flowsheet_members"] = member_table(flowsheet)

    template_types, template_ports = load_template_lists()
    candidates_by_unit = build_candidate_type_names(template_types)
    probes_by_family = build_connection_probes(template_ports)
    report["template"] = str(TEMPLATE_PATH)

    type_results: dict[str, dict] = {}
    counter = [0]
    index = 0
    for unit, candidates in candidates_by_unit.items():
        entry: dict = {"tried": {}, "template_candidates": template_types.get(unit, [])}
        family = FAMILY_BY_CATEGORY.get(unit, "")
        for type_name in candidates:
            index += 1
            op, shape, error = try_add(flowsheet, f"TMP_U{index}", type_name)
            if op is None:
                entry["tried"][type_name] = error
                continue
            entry["tried"][type_name] = f"OK via {shape}"
            entry["working_type_name"] = type_name
            entry["call_shape"] = shape
            if unit in template_types:
                entry["template_first_choice_ok"] = type_name == template_types[unit][0]
            for attr in ("TypeName", "VisibleTypeName"):
                try:
                    entry[attr] = getattr(op, attr)
                except Exception as error:  # noqa: BLE001
                    entry[attr] = f"error: {error}"
            table = member_table(op)
            entry["port_members"] = {
                k: v
                for k, v in table.items()
                if k == "__error__" or PORT_PATTERN.search(k)
            }
            if "__error__" in table:
                print(
                    f"  warning: type info of {unit} unavailable: {table['__error__']}"
                )
            entry["connection_probe"] = probe_connections(
                op, probes_by_family.get(family, []), flowsheet, counter
            )
            if family in COLUMN_FAMILIES:
                entry["column"] = probe_column(op)
            break
        type_results[unit] = entry
        first = entry.get("template_first_choice_ok")
        note = (
            ""
            if first is None
            else (
                " (template first choice)"
                if first
                else " (template first choice FAILED)"
            )
        )
        print(f"{unit:30s} -> {entry.get('working_type_name', 'NOT CREATED')}{note}")

    report["units"] = type_results
    json_path = os.path.join(args.out_dir, "hysys_com_discovery.json")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)

    lines = ["# HYSYS COM discovery report", "", f"Generated: {report['started']}", ""]
    lines.append(
        "| category (TYPES key) | working type string | template first choice | call shape | "
        "TypeName | VisibleTypeName | connection members that worked |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for unit, entry in type_results.items():
        ok = [k for k, v in entry.get("connection_probe", {}).items() if v == "OK"]
        first = entry.get("template_first_choice_ok")
        first_text = "n/a" if first is None else ("OK" if first else "FAILED")
        lines.append(
            f"| {unit} | {entry.get('working_type_name', '-')} | {first_text} | "
            f"{entry.get('call_shape', '-')} | {entry.get('TypeName', '-')} | "
            f"{entry.get('VisibleTypeName', '-')} | {', '.join(ok) or '-'} |"
        )
    failed = [
        f"{unit}: {', '.join(entry.get('template_candidates', []))}"
        for unit, entry in type_results.items()
        if entry.get("template_candidates") and "working_type_name" not in entry
    ]
    if failed:
        lines += [
            "",
            "Template categories with NO working type string (edit TYPES):",
            "",
        ]
        lines += [f"- {item}" for item in failed]
    lines += ["", "Port-like members per created object:", ""]
    for unit, entry in type_results.items():
        if "port_members" in entry:
            lines.append(
                f"- **{unit}**: "
                + ", ".join(
                    f"{k} ({'/'.join(v)})" for k, v in entry["port_members"].items()
                )
            )
    md_path = os.path.join(args.out_dir, "hysys_com_discovery.md")
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"\nReport written to {json_path} and {md_path}")
    print("The scratch case was not saved; close HYSYS when done.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
