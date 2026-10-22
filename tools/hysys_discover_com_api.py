"""Probe the Aspen HYSYS COM object model on a Windows machine with HYSYS.

Why this exists
---------------
The script template (``sketch2sim/instructions/hysys_template_base.py``)
carries one verified HYSYS type string per equipment category (``TYPES``) and
one verified COM member per connection role (``PORTS``). They were verified
live on Aspen HYSYS V11 on 2026-10-22 (``docs/HYSYS_COM_FACTS.md``,
``logs/hysys_com_discovery.md``). Run this tool to re-check them on another
HYSYS version or installation. It

* reads ``TYPES`` and ``PORTS`` from the template with ``ast`` (no import),
* tries every type string with ``flowsheet.Operations.Add(name, type)``
  (template candidates first, then a few extra spellings),
* lists the stream-port members of every object HYSYS creates,
* probes every port member with real streams and reads
  ``AttachedFeeds.Names`` / ``AttachedProducts.Names`` back after each probe,
  so the report separates "attached" from "accepted but not attached" (the
  silent ``HeavyLiquidProduct`` no-op on a 2-phase vessel, for example),
* loads the column template (``op.LoadColumnTemplate()``) on every column,
  lists the sub-flowsheet operations and probes ``AddFeedStream``, the
  ``Top*`` / ``Btm*`` feed and product members and ``AddDrawStream`` with
  read-back,
* starts its own HYSYS instance (``DispatchEx``), quits it at the end and
  waits until that process has exited (only a process the tool created is
  ever killed, and only if it is still alive 60 s after ``Quit()``).

Usage (from the repository root, in the project's virtual environment)::

    python tools/hysys_discover_com_api.py            # writes logs/hysys_com_discovery.{json,md}
    python tools/hysys_discover_com_api.py --visible  # show the HYSYS window
    python tools/hysys_discover_com_api.py --out-dir logs --progid HYSYS.Application

The scratch case is never saved.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1]
    / "sketch2sim"
    / "instructions"
    / "hysys_template_base.py"
)
CASE_NAME = "s2s_com_discovery"
ADD_RETRIES = 12  # SimulationCases.Add retries, 5 s apart (HYSYS may still start)
ADD_RETRY_SLEEP_S = 5.0
QUIT_TIMEOUT_S = 60.0


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


# Type strings tried in addition to the template's TYPES. The first group holds
# the V11-verified strings of template categories, so they are still tried when
# an older template lacks them; the second group holds verified types the agents
# do not use (reported for completeness). Template candidates always come first
# and keep their order.
EXTRA_TYPE_NAMES: dict[str, list[str]] = {
    "separator_3phase": ["sep3op"],
    "reactor_cstr": ["kineticreactorop"],
    "reactor_pfr": ["pfreactorop"],
    "column_3phase": ["threephase"],
    "column_extractor": ["liqliqextractor"],
    "lng_exchanger": ["lngop"],
    "balance": ["balanceop"],
    "stream_cutter": ["streamcutterop"],
    "relief_valve": ["reliefvalveop"],
}


def build_candidate_type_names(
    template_types: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    """Template TYPES first (same keys, same order), then the extra spellings."""
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
    "reactor_pfr": "pfr",
    "heat_exchanger": "heat_exchanger",
    "lng_exchanger": "heat_exchanger",
    "component_splitter": "splitter",
    "fired_heater": "fired_heater",
    "column_distillation": "column",
    "column_absorber": "column",
    "column_reboiled_absorber": "column",
    "column_refluxed_absorber": "column",
    "column_3phase": "column",
    "column_extractor": "column",
    "column_shortcut": "column",
    "adjust": "logical",
    "set": "logical",
    "spreadsheet": "logical",
    "balance": "logical",
    "stream_cutter": "logical",
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
    # The PFR exposes `Product`, not VapourProduct/LiquidProduct (V11 member list).
    "pfr": ["multi_in", "pfr_out"],
    "heat_exchanger": ["tube_in", "tube_out", "shell_in", "shell_out"],
    "splitter": ["splitter_in", "splitter_overhead", "splitter_bottom"],
    "fired_heater": ["fired_in", "fired_out", "flue_gas_out", "fuel_in"],
    "column": [],  # probed by probe_column after LoadColumnTemplate()
    "logical": [],
}
# Probes added on top of the template members (energy streams are not in PORTS).
EXTRA_PROBES: dict[str, list[tuple[str, str]]] = {
    "single_port": [("set_energy", "EnergyStream")],
    "vessel": [("set_energy", "EnergyStream")],
    "pfr": [("set_energy", "EnergyStream")],
}
COLUMN_FAMILIES = {"column"}
# Column members probed on the column object after the template is loaded.
COLUMN_FEED_MEMBERS = ("TopLiquidFeed", "BtmVapourFeed")
COLUMN_PRODUCT_MEMBERS = (
    "TopVapourProduct",
    "TopLiquidProduct",
    "TopWaterProduct",
    "BtmLiquidProduct",
)


def probe_from_member(member: str) -> tuple[str, str]:
    """Template PORTS entry -> (kind, member): "Feeds.Add" is an add, bare names are sets."""
    if member.endswith(".Add"):
        return "add", member[:-4]
    return "set", member


def role_side(role: str) -> str:
    """Where a role's attachment is read back: "in" (AttachedFeeds) or "out"."""
    return "in" if role.endswith("_in") else "out"


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


def build_probe_sides(
    template_ports: dict[str, list[str]] | None = None,
) -> dict[tuple[str, str], str]:
    """(kind, member) -> "in" | "out" | "any": the collection the probe is read back from.

    An energy stream is a feed on a heater and a product on a cooler, so the
    energy probe is looked up in both collections ("any").
    """
    if template_ports is None:
        _, template_ports = load_template_lists()
    sides: dict[tuple[str, str], str] = {}
    for family, roles in ROLES_BY_FAMILY.items():
        for role in roles:
            for member in template_ports.get(role, []):
                sides.setdefault(probe_from_member(member), role_side(role))
        for probe in EXTRA_PROBES.get(family, []):
            sides.setdefault(probe, "any")
    return sides


def probe_status(error: str | None, attached: bool | None) -> str:
    """Classify one probe: HYSYS raised, attached, or accepted without attaching."""
    if error:
        return f"FAILED: {error}"
    if attached is None:
        return "accepted (read-back unavailable)"
    if attached:
        return "attached"
    return "accepted but not attached"


# Member names worth reporting for every created object.
PORT_PATTERN = re.compile(
    r"(Feed|Product|Stream|Vapour|Vapor|Liquid|Energy|Attach|Inlet|Outlet|Draw|"
    r"Overhead|Bottom|Distillate|Reflux|Duty|Tube|Shell|Hot|Cold)",
    re.IGNORECASE,
)


def _error_text(error: BaseException) -> str:
    return f"{type(error).__name__}: {str(error)[:160]}"


def _names(collection) -> list[str]:
    """Names of a HYSYS collection (raises when the collection cannot be read)."""
    return [str(name) for name in collection.Names]


def is_attached(op, stream_name: str, side: str) -> bool | None:
    """Read the attachment back; None when AttachedFeeds/AttachedProducts cannot be read."""
    try:
        feeds = _names(op.AttachedFeeds)
        products = _names(op.AttachedProducts)
    except Exception:  # noqa: BLE001
        return None
    if side == "in":
        return stream_name in feeds
    if side == "out":
        return stream_name in products
    return stream_name in feeds or stream_name in products


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
    """Operations.Add(name, type), the call shape the template uses."""
    try:
        op = flowsheet.Operations.Add(name, type_name)
    except Exception as error:  # noqa: BLE001
        return None, _error_text(error)
    if op is None:
        return None, "Operations.Add returned None"
    return op, None


def _new_stream(flowsheet, counter: list[int], prefix: str, energy: bool = False):
    counter[0] += 1
    name = f"{prefix}{counter[0]}"
    if energy:
        return name, flowsheet.EnergyStreams.Add(name)
    return name, flowsheet.MaterialStreams.Add(name)


def probe_connections(
    op,
    probes: list[tuple[str, str]],
    sides: dict[tuple[str, str], str],
    flowsheet,
    counter: list[int],
) -> dict[str, str]:
    """Apply every probe with a fresh stream and read the attachment back."""
    results: dict[str, str] = {}
    for kind, member in probes:
        key = f"{kind}:{member}"
        try:
            stream_name, stream = _new_stream(
                flowsheet, counter, "TMP_S", energy=kind == "set_energy"
            )
        except Exception as error:  # noqa: BLE001
            results[key] = f"stream creation failed: {_error_text(error)}"
            continue
        error_text = None
        try:
            if kind == "add":
                getattr(op, member).Add(stream)
            else:
                setattr(op, member, stream)
        except Exception as error:  # noqa: BLE001
            error_text = _error_text(error)
        attached = None
        if error_text is None:
            attached = is_attached(op, stream_name, sides.get((kind, member), "any"))
        results[key] = probe_status(error_text, attached)
    return results


def _tray_section(column_flowsheet):
    """The tray section of a loaded column template (TypeName contains "tray")."""
    for name in _names(column_flowsheet.Operations):
        sub = column_flowsheet.Operations.Item(name)
        try:
            if "tray" in str(sub.TypeName).lower():
                return name, sub
        except Exception:  # noqa: BLE001
            continue
    return None, None


def _probe_column_member(op, member: str, side: str, flowsheet, counter) -> str:
    try:
        stream_name, stream = _new_stream(flowsheet, counter, "TMP_C")
    except Exception as error:  # noqa: BLE001
        return f"stream creation failed: {_error_text(error)}"
    try:
        setattr(op, member, stream)
    except Exception as error:  # noqa: BLE001
        return probe_status(_error_text(error), None)
    return probe_status(None, is_attached(op, stream_name, side))


def probe_column(op, flowsheet, counter: list[int]) -> dict:
    """Load the column template, list the sub-flowsheet and probe feeds, products, draws."""
    report: dict = {}
    try:
        op.LoadColumnTemplate()
        report["load_column_template"] = "OK"
    except Exception as error:  # noqa: BLE001
        report["load_column_template"] = f"FAILED: {_error_text(error)}"
    try:
        column_flowsheet = op.ColumnFlowsheet
        operations = []
        for name in _names(column_flowsheet.Operations):
            sub = column_flowsheet.Operations.Item(name)
            try:
                operations.append([name, str(sub.TypeName)])
            except Exception as error:  # noqa: BLE001
                operations.append([name, f"<TypeName error: {_error_text(error)}>"])
        report["operations"] = operations
    except Exception as error:  # noqa: BLE001
        report["column_flowsheet"] = f"FAILED: {_error_text(error)}"
        return report
    tray_name, tray = _tray_section(column_flowsheet)
    report["tray_section"] = tray_name
    probes: dict[str, str] = {}
    stage = 1
    if tray is not None:
        try:
            n_stages = int(tray.NumberOfStages)
            report["number_of_stages"] = n_stages
            stage = max(1, n_stages // 2)
        except Exception as error:  # noqa: BLE001
            report["number_of_stages"] = f"<error: {_error_text(error)}>"
        counter[0] += 1
        feed_name = f"TMP_F{counter[0]}"
        try:
            tray.AddFeedStream(feed_name, stage, False)
            attached = is_attached(op, feed_name, "in")
            exported = feed_name in _names(flowsheet.MaterialStreams)
            probes[f"AddFeedStream(name, {stage}, False)"] = probe_status(
                None, attached
            ) + (" (main stream exported)" if exported else " (no main stream)")
        except Exception as error:  # noqa: BLE001
            probes["AddFeedStream(name, stage, False)"] = probe_status(
                _error_text(error), None
            )
        counter[0] += 1
        draw_name = f"TMP_D{counter[0]}"
        try:
            tray.AddDrawStream(draw_name, 1, stage, True, False)
            attached = is_attached(op, draw_name, "out")
            exported = draw_name in _names(flowsheet.MaterialStreams)
            probes[f"AddDrawStream(name, 1, {stage}, True, False)"] = probe_status(
                None, attached
            ) + (" (main stream exported)" if exported else " (no main stream)")
        except Exception as error:  # noqa: BLE001
            probes["AddDrawStream(name, 1, stage, True, False)"] = probe_status(
                _error_text(error), None
            )
    else:
        probes["AddFeedStream"] = "not probed: no tray section"
        probes["AddDrawStream"] = "not probed: no tray section"
    for member in COLUMN_FEED_MEMBERS:
        probes[f"set:{member}"] = _probe_column_member(
            op, member, "in", flowsheet, counter
        )
    for member in COLUMN_PRODUCT_MEMBERS:
        probes[f"set:{member}"] = _probe_column_member(
            op, member, "out", flowsheet, counter
        )
    report["probes"] = probes
    try:
        report["operations_after_probes"] = _names(column_flowsheet.Operations)
    except Exception as error:  # noqa: BLE001
        report["operations_after_probes"] = f"<error: {_error_text(error)}>"
    return report


def hysys_pids() -> set[int]:
    """PIDs of the running AspenHysys.exe processes (empty where tasklist is absent)."""
    try:
        raw = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq AspenHysys.exe", "/FO", "CSV"],
            capture_output=True,
            timeout=30,
        ).stdout.decode("mbcs", "replace")
    except Exception:  # noqa: BLE001
        return set()
    return {
        int(line.split('","')[1])
        for line in raw.splitlines()
        if line.startswith('"AspenHysys.exe"')
    }


def wait_for_exit(pids: set[int], timeout: float) -> set[int]:
    """Poll tasklist until none of `pids` is alive; return the PIDs still alive."""
    started = time.time()
    alive = hysys_pids() & pids
    while alive and time.time() - started < timeout:
        time.sleep(2)
        alive = hysys_pids() & pids
    return alive


def shutdown(hysys, created: set[int], report: dict) -> None:
    """Quit the instance this tool created and wait until its process is gone."""
    started = time.time()
    try:
        hysys.Quit()
        report["quit"] = "Quit() returned"
    except Exception as error:  # noqa: BLE001
        report["quit"] = f"Quit() failed: {_error_text(error)}"
    alive = wait_for_exit(created, QUIT_TIMEOUT_S)
    report["quit_wait_s"] = round(time.time() - started, 1)
    if alive:
        # Only a process this tool started is ever killed.
        for pid in sorted(alive):
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        report["killed_pids"] = sorted(alive)
        alive = wait_for_exit(alive, 15.0)
    report["hysys_exited"] = not alive
    print(f"[hysys] {report['quit']}; exited={report['hysys_exited']}")


def start_hysys(dispatch_ex, progid: str, visible: bool, report: dict):
    """Start a fresh HYSYS instance and create the scratch case (with retries)."""
    pids_before = hysys_pids()
    hysys = dispatch_ex(progid)
    created = hysys_pids() - pids_before
    report["hysys_pids"] = sorted(created)
    print(f"[hysys] started pid={','.join(str(p) for p in sorted(created)) or '?'}")
    try:
        hysys.Visible = visible
    except Exception as error:  # noqa: BLE001
        report["visible_error"] = _error_text(error)
    try:
        report["program_build"] = hysys.ProgramBuild
    except Exception:  # noqa: BLE001
        pass
    case = None
    for attempt in range(ADD_RETRIES):
        try:
            case = hysys.SimulationCases.Add(CASE_NAME)
            report["simulation_cases_add_tries"] = attempt + 1
            break
        except Exception as error:  # noqa: BLE001
            # HYSYS may still be starting (or another instance still exiting).
            print(f"[hysys] SimulationCases.Add not ready ({str(error)[:80]}); retry")
            time.sleep(ADD_RETRY_SLEEP_S)
    return hysys, created, case


def setup_basis(case, report: dict):
    """Fluid package and a few components, so streams can be created."""
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
    try:
        case.Solver.CanSolve = False  # build without solving, as the template does
        report["solver_held"] = True
    except Exception as error:  # noqa: BLE001
        report["solver_held"] = f"could not hold the solver: {_error_text(error)}"


def run_probes(case, report: dict) -> dict[str, dict]:
    """Create one object per category and probe its ports; fill report["units"]."""
    flowsheet = case.Flowsheet
    report["flowsheet_members"] = member_table(flowsheet)

    template_types, template_ports = load_template_lists()
    candidates_by_unit = build_candidate_type_names(template_types)
    probes_by_family = build_connection_probes(template_ports)
    sides = build_probe_sides(template_ports)
    report["template"] = str(TEMPLATE_PATH)

    type_results: dict[str, dict] = {}
    report["units"] = type_results
    counter = [0]
    index = 0
    for unit, candidates in candidates_by_unit.items():
        entry: dict = {"tried": {}, "template_candidates": template_types.get(unit, [])}
        family = FAMILY_BY_CATEGORY.get(unit, "")
        for type_name in candidates:
            index += 1
            op, error = try_add(flowsheet, f"TMP_U{index}", type_name)
            if op is None:
                entry["tried"][type_name] = error
                continue
            entry["tried"][type_name] = "OK via Add(name, type)"
            entry["working_type_name"] = type_name
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
            if family in COLUMN_FAMILIES:
                entry["column"] = probe_column(op, flowsheet, counter)
            else:
                entry["connection_probe"] = probe_connections(
                    op, probes_by_family.get(family, []), sides, flowsheet, counter
                )
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
    return type_results


def _split_probe_results(results: dict[str, str]) -> tuple[list, list, list]:
    attached = [k for k, v in results.items() if v.startswith("attached")]
    silent = [k for k, v in results.items() if v.startswith("accepted")]
    failed = [k for k, v in results.items() if v.startswith("FAILED")]
    return attached, silent, failed


def write_reports(report: dict, out_dir: str) -> tuple[str, str]:
    """Write the JSON and Markdown reports; return their paths."""
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "hysys_com_discovery.json")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)

    type_results: dict[str, dict] = report.get("units", {})
    lines = ["# HYSYS COM discovery report", "", f"Generated: {report['started']}", ""]
    lines.append(
        f"HYSYS build: {report.get('program_build', '?')} | pid(s) started: "
        f"{report.get('hysys_pids', [])} | SimulationCases.Add tries: "
        f"{report.get('simulation_cases_add_tries', '?')} | "
        f"{report.get('quit', 'Quit() not called')} | exited: "
        f"{report.get('hysys_exited', '?')} (killed: {report.get('killed_pids', [])})"
    )
    lines.append("")
    lines.append(
        "| category (TYPES key) | working type string | template first choice | "
        "TypeName | VisibleTypeName | attached | accepted but NOT attached | failed |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for unit, entry in type_results.items():
        if "column" in entry:
            column = entry["column"]
            probes = column.get("probes", {})
            attached, silent, failed = _split_probe_results(probes)
            if column.get("load_column_template") != "OK":
                failed.insert(
                    0, f"LoadColumnTemplate: {column.get('load_column_template')}"
                )
        else:
            attached, silent, failed = _split_probe_results(
                entry.get("connection_probe", {})
            )
        first = entry.get("template_first_choice_ok")
        first_text = "n/a" if first is None else ("OK" if first else "FAILED")
        lines.append(
            f"| {unit} | {entry.get('working_type_name', '-')} | {first_text} | "
            f"{entry.get('TypeName', '-')} | {entry.get('VisibleTypeName', '-')} | "
            f"{', '.join(attached) or '-'} | {', '.join(silent) or '-'} | "
            f"{', '.join(failed) or '-'} |"
        )
    failed_units = [
        f"{unit}: {', '.join(entry.get('template_candidates', []))}"
        for unit, entry in type_results.items()
        if entry.get("template_candidates") and "working_type_name" not in entry
    ]
    if failed_units:
        lines += [
            "",
            "Template categories with NO working type string (edit TYPES):",
            "",
        ]
        lines += [f"- {item}" for item in failed_units]
    lines += ["", "## Columns (after LoadColumnTemplate())", ""]
    for unit, entry in type_results.items():
        column = entry.get("column")
        if not column:
            continue
        lines.append(
            f"- **{unit}** ({entry.get('working_type_name', '-')}): "
            f"LoadColumnTemplate {column.get('load_column_template')}; "
            f"tray section {column.get('tray_section')!r}; stages "
            f"{column.get('number_of_stages', '?')}; operations "
            f"{column.get('operations', column.get('column_flowsheet'))}"
        )
        for probe, result in column.get("probes", {}).items():
            lines.append(f"  - {probe}: {result}")
    lines += ["", "## Port-like members per created object", ""]
    for unit, entry in type_results.items():
        if "port_members" in entry:
            lines.append(
                f"- **{unit}**: "
                + ", ".join(
                    f"{k} ({'/'.join(v)})" for k, v in entry["port_members"].items()
                )
            )
    md_path = os.path.join(out_dir, "hysys_com_discovery.md")
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--visible", action="store_true", help="show the HYSYS window")
    parser.add_argument("--out-dir", default="logs")
    parser.add_argument("--progid", default="HYSYS.Application")
    args = parser.parse_args()

    try:
        from win32com.client import DispatchEx
    except ImportError:
        print("pywin32 is required (pip install pywin32) and this must run on Windows.")
        return 2

    report: dict = {
        "started": datetime.now().isoformat(timespec="seconds"),
        "progid": args.progid,
    }
    hysys, created, case = start_hysys(DispatchEx, args.progid, args.visible, report)
    exit_code = 0
    try:
        if case is None:
            raise RuntimeError(
                f"HYSYS did not accept a new simulation case within "
                f"{ADD_RETRIES * ADD_RETRY_SLEEP_S:.0f} s"
            )
        setup_basis(case, report)
        run_probes(case, report)
    except Exception:  # noqa: BLE001
        report["error"] = traceback.format_exc()
        traceback.print_exc()
        exit_code = 1
    finally:
        shutdown(hysys, created, report)  # the scratch case is never saved
        json_path, md_path = write_reports(report, args.out_dir)
        print(f"\nReport written to {json_path} and {md_path}")
    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
