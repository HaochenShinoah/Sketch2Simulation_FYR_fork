"""Probe the Aspen HYSYS COM object model on a Windows machine with HYSYS.

Why this exists
---------------
The two instruction files used by the Instantiation and Configuration agents
(``sketch2sim/instructions/*_instructions_simple_v5.txt``) were recreated
without access to a HYSYS installation. Everything marked VERIFIED in them was
taken from working public code; everything marked CANDIDATE is a best guess
with fallbacks. Run this script once on the HYSYS laptop to turn guesses into
facts: it tries every candidate ``Operations.Add`` type string, lists the
stream-port members of each object that HYSYS creates, probes the connection
members with real streams, and writes a report you can paste back into the
instruction files.

Usage (from the repository root, in the project's virtual environment)::

    python tools/hysys_discover_com_api.py            # writes logs/hysys_com_discovery.{json,md}
    python tools/hysys_discover_com_api.py --visible  # keep the HYSYS window visible

The scratch case is never saved. Close HYSYS afterwards if it stays open.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from datetime import datetime

# Type strings to try with Flowsheet.Operations.Add(name, type_name).
# (verified) = seen as TypeName of real objects in public working code.
CANDIDATE_TYPE_NAMES: dict[str, list[str]] = {
    "mixer": ["mixerop"],  # verified
    "tee": ["teeop"],  # verified
    "valve": ["valveop"],  # verified
    "pump": ["pumpop"],  # verified
    "compressor": ["compressor", "compressorop"],  # "compressor" verified
    "expander": ["expandop", "expander", "expanderop"],
    "heater": ["heaterop"],  # verified
    "cooler": ["coolerop"],  # verified
    "heat_exchanger": ["heatexop"],  # verified
    "air_cooler": ["aircoolerop", "aircooler"],
    "lng_exchanger": ["lngop", "lngexchangerop"],
    "fired_heater": ["dynamicfiredheaterop", "firedheaterop", "firedheater"],
    "separator_2phase": ["separatorop", "separator", "flashop"],
    "separator_3phase": [
        "3phaseseparatorop",
        "threephaseseparatorop",
        "3phaseseparator",
        "separator3op",
    ],
    "tank": ["tankop", "tank"],
    "conversion_reactor": ["conversionreactorop"],  # verified
    "equilibrium_reactor": [
        "equilibriumreactorop",
        "eqreactorop",
        "equilibriumreactor",
    ],
    "gibbs_reactor": ["gibbsreactorop", "gibbsreactor"],
    "yield_shift_reactor": ["yieldshiftreactorop", "yieldreactorop"],
    "cstr": ["cstrop", "cstr", "cstreactorop"],
    "pfr": ["pfrop", "pfreactorop", "plugflowreactorop", "pfr"],
    "distillation_column": ["distillation"],  # verified
    "absorber": ["absorber", "absorberop"],
    "reboiled_absorber": ["reboiledabsorber", "rebabsorber", "reboiledabsorberop"],
    "refluxed_absorber": ["refluxedabsorber", "refabsorber", "refluxedabsorberop"],
    "liquid_liquid_extractor": ["liquidliquidextractor", "extractor", "lleop"],
    "shortcut_column": ["shdistop"],  # verified
    "component_splitter": ["fractop"],  # verified
    "recycle": ["recycle"],  # verified
    "adjust": ["adjust"],  # verified
    "set": ["setop"],  # verified
    "balance": ["balanceop", "balance"],
    "spreadsheet": ["spreadsheetop"],  # verified
    "stream_cutter": ["streamcutterop"],  # verified
    "pipe_segment": ["pipeseg", "pipesegop"],
    "relief_valve": ["reliefvalveop", "reliefvalve"],
    "ejector": ["ejectorop", "ejector"],
}

# Member names worth reporting for every created object.
PORT_PATTERN = re.compile(
    r"(Feed|Product|Stream|Vapour|Vapor|Liquid|Energy|Attach|Inlet|Outlet|Draw|"
    r"Overhead|Bottom|Distillate|Reflux|Duty|Tube|Shell|Hot|Cold)",
    re.IGNORECASE,
)

# Connection members to probe with real streams, per unit family.
CONNECTION_PROBES: dict[str, list[tuple[str, str]]] = {
    # (kind, member): kind is "set" (op.<member> = stream) or "add" (op.<member>.Add(stream))
    "single_port": [
        ("set", "FeedStream"),
        ("set", "ProductStream"),
        ("set_energy", "EnergyStream"),
    ],
    "multi_feed": [
        ("add", "Feeds"),
        ("add", "AttachedFeeds"),
        ("set", "VapourProduct"),
        ("set", "LiquidProduct"),
        ("set", "HeavyLiquidProduct"),
        ("set", "LightLiquidProduct"),
        ("set", "Product"),
        ("add", "Products"),
        ("add", "AttachedProducts"),
        ("set_energy", "EnergyStream"),
    ],
    "tee": [
        ("set", "Feed"),
        ("set", "FeedStream"),
        ("add", "Products"),
        ("add", "AttachedProducts"),
    ],
    "heat_exchanger": [
        ("set", "TubeSideFeed"),
        ("set", "TubeSideProduct"),
        ("set", "ShellSideFeed"),
        ("set", "ShellSideProduct"),
    ],
}
FAMILY_BY_UNIT = {
    "mixer": "multi_feed",
    "tee": "tee",
    "valve": "single_port",
    "pump": "single_port",
    "compressor": "single_port",
    "expander": "single_port",
    "heater": "single_port",
    "cooler": "single_port",
    "heat_exchanger": "heat_exchanger",
    "air_cooler": "single_port",
    "fired_heater": "multi_feed",
    "separator_2phase": "multi_feed",
    "separator_3phase": "multi_feed",
    "tank": "multi_feed",
    "conversion_reactor": "multi_feed",
    "equilibrium_reactor": "multi_feed",
    "gibbs_reactor": "multi_feed",
    "yield_shift_reactor": "multi_feed",
    "cstr": "multi_feed",
    "pfr": "multi_feed",
    "component_splitter": "multi_feed",
    "recycle": "single_port",
}


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
    try:
        op = flowsheet.Operations.Add(name, type_name)
    except Exception as error:  # noqa: BLE001
        return None, f"{type(error).__name__}: {error}"
    if op is None:
        return None, "Operations.Add returned None"
    return op, None


def probe_connections(op, family: str, flowsheet, counter: list[int]) -> dict[str, str]:
    results: dict[str, str] = {}
    for kind, member in CONNECTION_PROBES.get(family, []):
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
    fluidpkg = hybasis.FluidPackages.Add()
    fluidpkg.PropertyPackageName = "Peng-Robinson"
    for comp in ("Methane", "Ethane", "H2O"):
        try:
            fluidpkg.Components.Add(comp)
        except Exception as error:  # noqa: BLE001
            report.setdefault("component_errors", {})[comp] = str(error)
    hybasis.EndBasisChange()
    flowsheet = case.Flowsheet
    report["flowsheet_members"] = member_table(flowsheet)

    type_results: dict[str, dict] = {}
    counter = [0]
    index = 0
    for unit, candidates in CANDIDATE_TYPE_NAMES.items():
        entry: dict = {"tried": {}}
        for type_name in candidates:
            index += 1
            op, error = try_add(flowsheet, f"TMP_U{index}", type_name)
            if op is None:
                entry["tried"][type_name] = error
                continue
            entry["tried"][type_name] = "OK"
            entry["working_type_name"] = type_name
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
                op, FAMILY_BY_UNIT.get(unit, ""), flowsheet, counter
            )
            if unit in {
                "distillation_column",
                "absorber",
                "reboiled_absorber",
                "refluxed_absorber",
                "liquid_liquid_extractor",
            }:
                entry["column"] = probe_column(op)
            break
        type_results[unit] = entry
        print(f"{unit:26s} -> {entry.get('working_type_name', 'NOT CREATED')}")

    report["units"] = type_results
    json_path = os.path.join(args.out_dir, "hysys_com_discovery.json")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)

    lines = ["# HYSYS COM discovery report", "", f"Generated: {report['started']}", ""]
    lines.append(
        "| unit | working type string | TypeName | VisibleTypeName | connection members that worked |"
    )
    lines.append("|---|---|---|---|---|")
    for unit, entry in type_results.items():
        ok = [k for k, v in entry.get("connection_probe", {}).items() if v == "OK"]
        lines.append(
            f"| {unit} | {entry.get('working_type_name', '-')} | {entry.get('TypeName', '-')} | "
            f"{entry.get('VisibleTypeName', '-')} | {', '.join(ok) or '-'} |"
        )
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
