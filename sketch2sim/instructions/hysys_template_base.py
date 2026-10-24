import atexit
import json
import os
import re
import subprocess
import sys
import time

import pythoncom
from win32com.client import DispatchEx

_RESULT = {"ok": False}
_HYSYS = {"app": None, "pid": None}


def _hysys_pids():
    """PIDs of the running AspenHysys.exe processes (empty where tasklist is absent)."""
    try:
        raw = subprocess.run(
            [
                "tasklist",
                "/FI",
                "IMAGENAME eq AspenHysys.exe",
                "/FI",
                "STATUS eq RUNNING",
                "/FO",
                "CSV",
            ],
            capture_output=True,
            timeout=30,
        ).stdout.decode("mbcs", "replace")
    except Exception:
        return set()
    return {
        int(line.split('","')[1])
        for line in raw.splitlines()
        if line.startswith('"AspenHysys.exe"')
    }


def _shutdown():
    """Quit the HYSYS instance this script created.

    A successful run keeps it open only when S2S_HYSYS_KEEP_OPEN=1; a run that
    failed, or that stopped early in Steps 1-3, always quits so the next attempt
    does not attach to a half-dead instance.
    """
    app = _HYSYS["app"]
    if app is None:
        return
    if _RESULT["ok"] and os.environ.get("S2S_HYSYS_KEEP_OPEN", "0") == "1":
        print("[hysys] left open (S2S_HYSYS_KEEP_OPEN=1)")
        return
    try:
        app.Quit()
        print("[hysys] Quit() called")
    except Exception as error:
        print(f"[hysys] Quit() failed: {str(error)[:120]}")


atexit.register(_shutdown)

# === Step 1: Launch HYSYS and create a new case (Agent 1) ===
case_name = "FILL_CASE_NAME"  # Basis Agent must fill
_pids_before = _hysys_pids()
hysys = DispatchEx("HYSYS.Application")  # a new HYSYS process (when none is open)
_HYSYS["app"] = hysys
_new_pids = sorted(_hysys_pids() - _pids_before)
_HYSYS["pid"] = _new_pids[0] if _new_pids else None
print(
    f"[hysys] attempt {os.environ.get('S2S_ATTEMPT', '?')}: "
    f"pid={','.join(str(p) for p in _new_pids) or '?'}"
)
if not _new_pids:
    print(
        "[hysys] WARNING no new AspenHysys.exe appeared: this script is talking to an "
        "instance that was already running (close HYSYS before running the pipeline)"
    )
hysys.Visible = True
case = None
for _attempt in range(12):
    # HYSYS may still be starting, or an instance of a previous attempt may
    # still be exiting; both make SimulationCases.Add fail for a few seconds.
    try:
        case = hysys.SimulationCases.Add(case_name)
        break
    except Exception as _error:
        print(
            f"[hysys] SimulationCases.Add not ready ({str(_error)[:80]}); retry in 5 s"
        )
        time.sleep(5)
if case is None:
    raise RuntimeError("HYSYS did not accept a new simulation case within 60 s")
case.Visible = True
# Show the case window and let HYSYS draw it: the PFD (the flowsheet diagram)
# exists only then, and every object added afterwards gets its graphic item at
# once. Step 7 lays the diagram out before saving.
try:
    case.Activate()
    for _ in range(10):
        hysys.DoEvents()
        time.sleep(0.2)
except Exception as _error:
    print(f"[hysys] could not activate the case window ({str(_error)[:80]})")

# === Step 2: Setup Property Package (Agent 1) ===
hybasis = case.BasisManager
hybasis.StartBasisChange()  # Customization Guide: bracket every basis change
fluidpkg = hybasis.FluidPackages.Add()
property_package = "FILL_PROPERTY_PACKAGE"  # Basis Agent must fill, e.g. "PengRob"
fluidpkg.PropertyPackageName = property_package

# === Step 3: Add Components ===
components = [
    "COMPONENT_1",
    "COMPONENT_2",
]  # Basis Agent must fill the component list

# --- Component replacement blocks ---
# Add as many replacement blocks as needed.
if "COMPONENT_1" in components:
    components.remove("COMPONENT_1")
    components.extend(["HYSYS_COMP_1", "HYSYS_COMP_2"])  # Basis Agent must fill

if "COMPONENT_2" in components:
    components.remove("COMPONENT_2")
    components.extend(["HYSYS_COMP_1", "HYSYS_COMP_2"])  # Basis Agent must fill

for comp in components:
    fluidpkg.Components.Add(comp)
hybasis.EndBasisChange()
flowsheet = case.Flowsheet

# === Step 3b: HYSYS COM helpers (shared by Agents 2 and 3; do not edit) ===
# Every type string and port member below was verified live on Aspen HYSYS V11
# (2026-10-22, see docs/HYSYS_COM_FACTS.md). The agents only call the helper
# functions. Each attachment is read back from AttachedFeeds / AttachedProducts,
# so a call that HYSYS accepts without attaching anything counts as a failure.
# The file head, Step 1, this block and Step 7 are owned by the template: the
# pipeline re-inserts them before every execution, so edits here are discarded.

# One verified HYSYS type string per equipment category (flowsheet.Operations.Add).
TYPES = {
    "pump": ["pumpop"],
    "compressor": ["compressor"],
    "expander": ["expandop"],
    "valve": ["valveop"],
    "mixer": ["mixerop"],
    "tee": ["teeop"],
    "heater": ["heaterop"],
    "cooler": ["coolerop"],
    "heat_exchanger": ["heatexop"],
    "air_cooler": ["aircoolerop"],
    "fired_heater": ["dynamicfiredheaterop"],
    "separator": ["flashtank"],
    "separator_3phase": ["sep3op"],
    "tank": ["tankop"],
    "reactor_conversion": ["conversionreactorop"],
    "reactor_equilibrium": ["equilibriumreactorop"],
    "reactor_gibbs": ["gibbsreactorop"],
    "reactor_cstr": ["kineticreactorop"],
    "reactor_pfr": ["pfreactorop"],
    "column_distillation": ["distillation"],
    "column_absorber": ["absorber"],
    "column_reboiled_absorber": ["reboiledabsorber"],
    "column_refluxed_absorber": ["refluxedabsorber"],
    "column_3phase": ["threephase"],
    "column_extractor": ["liqliqextractor"],
    "component_splitter": ["fractop"],
    "recycle": ["recycle"],
    "adjust": ["adjust"],
    "set": ["setop"],
    "spreadsheet": ["spreadsheetop"],
    "pipe": ["pipeseg"],
}

# Column type string -> argument of LoadColumnTemplate verified for it (None =
# the no-argument form, which loads the type's default template). The other
# form is tried when the first one leaves the column without a tray section.
COLUMN_TEMPLATES = {
    "distillation": None,
    "absorber": "absorber",
    "reboiledabsorber": None,
    "refluxedabsorber": None,
    "threephase": "3phase",
    "liqliqextractor": None,
}
_COLUMN_TEMPLATE_FILES = {
    "distillation": "distil",
    "absorber": "absorber",
    "reboiledabsorber": "rebabs",
    "refluxedabsorber": "refabs",
    "threephase": "3phase",
    "liqliqextractor": "liqliqex",
}

# One verified COM member per connection role. "Name.Add" means
# op.Name.Add(stream) (a collection); a bare name means op.Name = stream.
# A role ending in "_in" is read back in AttachedFeeds, every other role in
# AttachedProducts.
PORTS = {
    "single_in": ["FeedStream"],
    "single_out": ["ProductStream"],
    "multi_in": ["Feeds.Add"],
    "mixer_out": ["Product"],
    "tee_in": ["FeedStream"],
    "tee_out": ["Products.Add"],
    "vapour_out": ["VapourProduct"],
    "liquid_out": ["LiquidProduct"],
    "light_liquid_out": ["LiquidProduct"],
    "heavy_liquid_out": ["HeavyLiquidProduct"],
    "pfr_out": ["Product"],
    "tube_in": ["TubeSideFeed"],
    "tube_out": ["TubeSideProduct"],
    "shell_in": ["ShellSideFeed"],
    "shell_out": ["ShellSideProduct"],
    "splitter_in": ["Inlets.Add"],
    "splitter_overhead": ["OverheadOutlets.Add"],
    "splitter_bottom": ["BottomOutlets"],
    "fired_in": ["RadInlet.Add"],
    "fired_out": ["RadOutlet.Add"],
    "flue_gas_out": ["CombustionProduct"],
    "fuel_in": ["BurnerFeed"],
}

# The only reasons skip() accepts (quoted verbatim in the instruction files).
SKIP_REASONS = (
    "single-port unit, no free port",
    "no free vessel port",
    "single burner feed",
)

_COLUMN_PRODUCT_MEMBERS = {
    "overhead_vapour": "TopVapourProduct",
    "distillate": "TopLiquidProduct",
    "water_draw": "TopWaterProduct",
    "bottoms": "BtmLiquidProduct",
}
_DRAW_PHASES = {"vapour": 0, "vapor": 0, "gas": 0, "liquid": 1, "water": 2}
_GAS_NAME_RE = re.compile(r"\b(gas|vapou?r|vent|off-?gas|purge)\b")
_NOT_GAS_NAME_RE = re.compile(r"gas ?oil|gasoline|degas|gas ?condensate")
_WATER_NAME_RE = re.compile(r"\b(water|aqueous|brine|sour)\b")

_CONNECTED = []  # (unit_key, stream_key, member, kind, label)
_UNCONNECTED = []  # (unit_key, stream_key, reason)
_NOTES = []  # deliberate, documented skips
_ENERGY = []  # (unit_key, energy stream name)
_SIDE_DRAWS = {}  # unit_key -> number of draws placed so far
_TMP = [0]


def _short(error):
    return str(error).replace("\n", " ")[:120]


def _names(collection):
    try:
        return [str(name) for name in collection.Names]
    except Exception:
        return []


def _object_name(obj):
    for member in ("name", "Name"):
        try:
            return str(getattr(obj, member))
        except Exception:
            continue
    return ""


def _set_object_name(obj, name):
    for member in ("name", "Name"):
        try:
            setattr(obj, member, name)
            return True
        except Exception:
            continue
    return False


def _role_kind(role):
    return "in" if role.endswith("_in") else "out"


def _is_attached(op, stream, kind):
    collection = op.AttachedFeeds if kind == "in" else op.AttachedProducts
    return _object_name(stream) in _names(collection)


def _apply_member(target, member, stream):
    if member.endswith(".Add"):
        getattr(target, member[:-4]).Add(stream)
        return
    try:
        setattr(target, member, stream)
    except Exception as first_error:
        # Late-bound pywin32 may issue PROPERTYPUT for an object-valued
        # property; retry with an explicit PROPERTYPUTREF before giving up.
        try:
            oleobj = target._oleobj_
            dispid = oleobj.GetIDsOfNames(0, member)
            oleobj.Invoke(dispid, 0, pythoncom.DISPATCH_PROPERTYPUTREF, 0, stream)
        except Exception:
            raise first_error


def _attach(target, member, stream, unit_key, stream_key, kind, label=None):
    """Apply one member and verify the stream is really attached to units[unit_key]."""
    _apply_member(target, member, stream)
    if not _is_attached(units[unit_key], stream, kind):
        raise RuntimeError(
            f"{member}: accepted by HYSYS but the stream is not attached"
        )
    _CONNECTED.append((unit_key, stream_key, member, kind, label or member))
    print(f"[connect] {unit_key}.{label or member} <- {stream_key}")
    return True


def _fail(unit_key, stream_key, reason, what="stream"):
    _UNCONNECTED.append((unit_key, stream_key, reason))
    print(f"[connect] WARNING {what} {stream_key} NOT attached to {unit_key}: {reason}")
    return False


def _is_column(op):
    try:
        return str(op.TypeName).lower() in COLUMN_TEMPLATES
    except Exception:
        return False


def _tray_section(col):
    try:
        operations = col.ColumnFlowsheet.Operations
    except Exception:
        return None
    for name in _names(operations):
        op = operations.Item(name)
        try:
            if "tray" in str(op.TypeName).lower():
                return op
        except Exception:
            continue
    return None


def _load_column_template(op, type_name):
    """Load the column template; returns the error text when no tray section appears."""
    forms = [COLUMN_TEMPLATES[type_name], _COLUMN_TEMPLATE_FILES[type_name]]
    errors = []
    for form in forms:
        try:
            if form is None:
                op.LoadColumnTemplate()
            else:
                op.LoadColumnTemplate(form)
        except Exception as error:
            errors.append(f"LoadColumnTemplate({form!r}): {_short(error)}")
            continue
        if _tray_section(op) is not None:
            return None
        errors.append(f"LoadColumnTemplate({form!r}): no tray section afterwards")
    return " | ".join(errors)


def add_unit(key, name, type_names):
    """Create one unit operation; columns get their template loaded immediately."""
    errors = []
    for type_name in type_names:
        try:
            op = flowsheet.Operations.Add(name, type_name)
        except Exception as error:
            errors.append(f"{type_name}: {_short(error)}")
            continue
        if op is None:
            errors.append(f"{type_name}: Operations.Add returned None")
            continue
        if _object_name(op) != name:
            _set_object_name(op, name)
        if type_name.lower() in COLUMN_TEMPLATES:
            # A column created through COM is an empty shell until its template
            # (tray section, condenser, reboiler) is loaded. Never call
            # CreateColumnTemplate(): it installs an empty sub-flowsheet instead.
            problem = _load_column_template(op, type_name.lower())
            if problem:
                errors.append(f"{type_name}: {problem}")
                continue
            try:
                sub_ops = _names(op.ColumnFlowsheet.Operations)
            except Exception:
                sub_ops = []
            print(f"[unit] {key} = {name!r} ({type_name}, template: {sub_ops})")
        else:
            print(f"[unit] {key} = {name!r} ({type_name})")
        units[key] = op
        return op
    raise RuntimeError(f"Could not create unit {key!r} ({name!r}); tried {errors}")


def add_stream(key, name):
    """Create one material stream; the dictionary key is the stream id."""
    stream = flowsheet.MaterialStreams.Add(name)
    if stream is None:
        raise RuntimeError(f"Could not create material stream {key!r} ({name!r})")
    streams[key] = stream
    print(f"[stream] {key} = {name!r}")
    return stream


def connect(unit_key, stream_key, role):
    """Attach streams[stream_key] to units[unit_key] through the port of `role`."""
    op, stream = units[unit_key], streams[stream_key]
    if _is_column(op):
        return _fail(
            unit_key,
            stream_key,
            f"connect() used on a column ({role}); use connect_column_*",
        )
    if role not in PORTS:
        return _fail(
            unit_key, stream_key, f"unknown role {role!r}; roles: {sorted(PORTS)}"
        )
    kind = _role_kind(role)
    errors = []
    for member in PORTS[role]:
        try:
            return _attach(op, member, stream, unit_key, stream_key, kind)
        except Exception as error:
            errors.append(f"{member}: {_short(error)}")
    return _fail(unit_key, stream_key, f"{role}: " + " | ".join(errors))


def connect_energy(unit_key, name=None):
    """Create an energy stream and attach it to the unit's EnergyStream port."""
    op = units[unit_key]
    name = name or f"E-{_object_name(op) or unit_key}"
    try:
        energy = flowsheet.EnergyStreams.Add(name)
        op.EnergyStream = energy
        if _object_name(op.EnergyStream) != name:
            raise RuntimeError("EnergyStream read-back differs")
    except Exception as error:
        return _fail(unit_key, name, f"EnergyStream: {_short(error)}", "energy stream")
    _ENERGY.append((unit_key, name))
    print(f"[connect] {unit_key}.EnergyStream <- {name!r}")
    return True


def skip(unit_key, stream_key, reason):
    """Record a listed stream deliberately left unconnected (never on a column)."""
    if _is_column(units[unit_key]):
        return _fail(
            unit_key, stream_key, f"skip() is not allowed on columns ({reason})"
        )
    if reason not in SKIP_REASONS:
        return _fail(
            unit_key,
            stream_key,
            f"skip() reason not allowed: {reason!r}; use one of {SKIP_REASONS}",
        )
    _NOTES.append(f"{stream_key} not attached to {unit_key}: {reason}")
    print(f"[connect] NOTE {stream_key} not attached to {unit_key}: {reason}")
    return False


def _exported_by(stream_key):
    """The column that exported streams[stream_key] (AddFeedStream / AddDrawStream), if any."""
    for unit_key, s_key, member, _kind, _label in _CONNECTED:
        if s_key == stream_key and member in ("AddFeedStream", "AddDrawStream"):
            return unit_key
    return None


def _reapply_connections(stream_key, new_stream):
    """Point every connection already made for stream_key at the replacement object."""
    for entry in list(_CONNECTED):
        unit_key, s_key, member, kind, label = entry
        if s_key != stream_key or member in ("AddFeedStream", "AddDrawStream"):
            continue
        try:
            _apply_member(units[unit_key], member, new_stream)
            if not _is_attached(units[unit_key], new_stream, kind):
                raise RuntimeError("not attached after re-apply")
            print(
                f"[connect] {unit_key}.{label} <- {stream_key} (moved to the exported stream)"
            )
        except Exception as error:
            _CONNECTED.remove(entry)
            _UNCONNECTED.append(
                (unit_key, stream_key, f"re-apply {label}: {_short(error)}")
            )


def _swap_stream(stream_key, new_stream, column_flowsheet, tmp_name):
    """Replace the placeholder streams[stream_key] by the stream a column exported."""
    old = streams[stream_key]
    old_name = _object_name(old)
    _reapply_connections(stream_key, new_stream)
    try:
        flowsheet.MaterialStreams.Remove(old_name)
    except Exception as error:
        print(f"[stream] could not remove placeholder {old_name!r}: {_short(error)}")
    _set_object_name(new_stream, old_name)
    try:  # the column-internal copy keeps the placeholder name otherwise
        _set_object_name(column_flowsheet.MaterialStreams.Item(tmp_name), old_name)
    except Exception:
        pass
    streams[stream_key] = new_stream


def _stages(tower):
    try:
        return max(1, int(tower.NumberOfStages))
    except Exception:
        return 10


def _clamp_stage(stage, n_stages, what):
    stage = int(stage)
    if 1 <= stage <= n_stages:
        return stage
    clamped = min(max(stage, 1), n_stages)
    print(
        f"[connect] NOTE {what}: stage {stage} is outside 1..{n_stages}; using {clamped}"
    )
    return clamped


def connect_column_feed(unit_key, stream_key, stage=None, position=None):
    """Attach a main-flowsheet stream as a column feed.

    Default: a tray feed. The tray section exports a new main-flowsheet stream
    (AddFeedStream) which takes over the name and the connections of
    streams[stream_key]. position="top" -> TopLiquidFeed, "bottom" ->
    BtmVapourFeed (absorber/extractor feeds, and streams another column exported,
    which cannot be placed on a tray).
    """
    col, stream = units[unit_key], streams[stream_key]
    if not _is_column(col):
        return _fail(
            unit_key,
            stream_key,
            "connect_column_feed() used on a unit that is not a column",
            "column feed",
        )
    exporter = _exported_by(stream_key)
    if position is None and exporter is not None:
        position = "top"
        print(
            f"[connect] NOTE {stream_key} leaves column {exporter}: attached as top feed of {unit_key}"
        )
    if position in ("top", "bottom"):
        member = "TopLiquidFeed" if position == "top" else "BtmVapourFeed"
        try:
            return _attach(col, member, stream, unit_key, stream_key, "in")
        except Exception as error:
            return _fail(
                unit_key, stream_key, f"{member}: {_short(error)}", "column feed"
            )
    if position is not None:
        return _fail(
            unit_key,
            stream_key,
            f"unknown position {position!r} (use 'top' or 'bottom')",
            "column feed",
        )
    tower = _tray_section(col)
    if tower is None:
        return _fail(
            unit_key,
            stream_key,
            "no tray section (column template not loaded)",
            "column feed",
        )
    n_stages = _stages(tower)
    stage = (
        _clamp_stage(stage, n_stages, f"feed {stream_key}")
        if stage
        else max(1, n_stages // 2)
    )
    _TMP[0] += 1
    tmp = f"__s2s_feed_{_TMP[0]}"
    try:
        tower.AddFeedStream(tmp, stage, False)
        new_stream = flowsheet.MaterialStreams.Item(tmp)
        _swap_stream(stream_key, new_stream, col.ColumnFlowsheet, tmp)
        if not _is_attached(col, streams[stream_key], "in"):
            raise RuntimeError("exported stream not attached")
    except Exception as error:
        return _fail(
            unit_key,
            stream_key,
            f"AddFeedStream(stage {stage}): {_short(error)}",
            "column feed",
        )
    label = f"AddFeedStream(stage {stage})"
    _CONNECTED.append((unit_key, stream_key, "AddFeedStream", "in", label))
    print(f"[connect] {unit_key}.{label} <- {stream_key}")
    return True


def _draw_phase(stream_name, phase=None):
    if phase is not None:
        if isinstance(phase, str):
            if phase.lower() not in _DRAW_PHASES:
                raise ValueError(f"phase must be one of {sorted(_DRAW_PHASES)}")
            return _DRAW_PHASES[phase.lower()]
        return int(phase)
    name = stream_name.lower()
    if _GAS_NAME_RE.search(name) and not _NOT_GAS_NAME_RE.search(name):
        return 0  # vapour draw
    if _WATER_NAME_RE.search(name):
        return 2  # water draw
    return 1  # liquid draw


def connect_column_product(unit_key, stream_key, role, stage=None, phase=None):
    """Attach a column product.

    role: overhead_vapour | distillate | water_draw | bottoms (ColumnOp members,
    each usable once) or side_draw (AddDrawStream: the tray section exports a new
    main-flowsheet stream that takes over the name of streams[stream_key];
    phase "liquid" | "vapour" | "water" overrides the name-based choice).
    """
    col, stream = units[unit_key], streams[stream_key]
    if not _is_column(col):
        return _fail(
            unit_key,
            stream_key,
            "connect_column_product() used on a unit that is not a column",
            "column product",
        )
    if role in _COLUMN_PRODUCT_MEMBERS:
        member = _COLUMN_PRODUCT_MEMBERS[role]
        try:
            return _attach(
                col, member, stream, unit_key, stream_key, "out", f"{role} ({member})"
            )
        except Exception as error:
            return _fail(
                unit_key,
                stream_key,
                f"{role}: {member}: {_short(error)}",
                "column product",
            )
    if role != "side_draw":
        return _fail(
            unit_key,
            stream_key,
            f"unknown column product role {role!r}",
            "column product",
        )
    exporter = _exported_by(stream_key)
    if exporter is not None:
        return _fail(
            unit_key,
            stream_key,
            f"already exported by column {exporter}: connect the draw of {unit_key} before "
            f"the feed of {exporter}, or give that feed position='top'/'bottom'",
            "column product",
        )
    tower = _tray_section(col)
    if tower is None:
        return _fail(
            unit_key,
            stream_key,
            "no tray section (column template not loaded)",
            "column product",
        )
    n_stages = _stages(tower)
    count = _SIDE_DRAWS.get(unit_key, 0)
    if stage:
        stage = _clamp_stage(stage, n_stages, f"draw {stream_key}")
    else:
        stage = 2 + 2 * count  # 2, 4, 6, 8 on the default 10-stage tower
        if stage > n_stages - 1:
            return _fail(
                unit_key,
                stream_key,
                f"no free default stage left for draw {count + 1}; pass stage=<n>",
                "column product",
            )
    _SIDE_DRAWS[unit_key] = count + 1
    try:
        draw_phase = _draw_phase(_object_name(stream), phase)
    except ValueError as error:
        return _fail(unit_key, stream_key, str(error), "column product")
    _TMP[0] += 1
    tmp = f"__s2s_draw_{_TMP[0]}"
    try:
        tower.AddDrawStream(tmp, draw_phase, int(stage), True, False)
        new_stream = flowsheet.MaterialStreams.Item(tmp)
        _swap_stream(stream_key, new_stream, col.ColumnFlowsheet, tmp)
        if not _is_attached(col, streams[stream_key], "out"):
            raise RuntimeError("exported stream not attached")
    except Exception as error:
        return _fail(
            unit_key,
            stream_key,
            f"AddDrawStream(stage {stage}, phase {draw_phase}): {_short(error)}",
            "column product",
        )
    label = f"AddDrawStream(stage {stage}, phase {draw_phase})"
    _CONNECTED.append((unit_key, stream_key, "AddDrawStream", "out", label))
    print(f"[connect] {unit_key}.{label} <- {stream_key}")
    return True


def ensure_property_package(requested):
    """Report the fluid package HYSYS carries after Step 2 (never raises)."""
    try:
        current = str(fluidpkg.PropertyPackageName)
    except Exception as error:
        current = ""
        print(f"[basis] could not read PropertyPackageName: {_short(error)}")
    normalise = lambda text: re.sub(r"[^a-z0-9]", "", str(text).lower())  # noqa: E731
    wanted, got = normalise(requested), normalise(current)
    if requested and current and got != wanted:
        pr_aliases = {"pengrob", "pr", "pengrobinson"}
        if not (wanted in pr_aliases and got in pr_aliases):
            _NOTES.append(f"property package {requested!r} reads back as {current!r}")
    print(f"[basis] property package: {current!r} (requested {requested!r})")
    return bool(current)


def set_solver_hold(hold):
    """Hold (True) or release (False) the HYSYS solver while the flowsheet is built."""
    try:
        case.Solver.CanSolve = not hold
        print(f"[solver] CanSolve = {not hold}")
        return True
    except Exception as error:
        print(f"[solver] could not set CanSolve: {_short(error)}")
        return False


def _topology():
    report = {}
    for key, op in units.items():
        try:
            feeds, products = _names(op.AttachedFeeds), _names(op.AttachedProducts)
        except Exception as error:
            feeds, products = [f"<error {_short(error)}>"], []
        report[key] = {"name": _object_name(op), "feeds": feeds, "products": products}
    return report


def finish_connections(topology_path=None):
    """Re-check every attachment, print the summary, return the exit code (0 or 2)."""
    for unit_key, stream_key, member, kind, label in list(_CONNECTED):
        try:
            still = _is_attached(units[unit_key], streams[stream_key], kind)
        except Exception as error:
            still, label = False, f"{label} ({_short(error)})"
        if not still:
            _UNCONNECTED.append(
                (unit_key, stream_key, f"{label}: displaced by a later connection")
            )
    print(
        f"\n[summary] units: {len(units)}  streams: {len(streams)}  connections: {len(_CONNECTED)}"
    )
    for message in _NOTES:
        print(f"[summary] NOTE {message}")
    topology = _topology()
    for key, entry in topology.items():
        print(f"[verify] {key}: feeds={entry['feeds']} products={entry['products']}")
        if not entry["feeds"]:
            print(f"[summary] INFO {key} has no feed attached (check the extraction)")
        if not entry["products"]:
            print(
                f"[summary] INFO {key} has no product attached (check the extraction)"
            )
    if topology_path:
        try:
            with open(topology_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "units": topology,
                        "failed": _UNCONNECTED,
                        "notes": _NOTES,
                        "energy": _ENERGY,
                    },
                    handle,
                    indent=1,
                )
            print(f"[summary] topology written to {topology_path}")
        except Exception as error:
            print(f"[summary] could not write topology: {_short(error)}")
    if _UNCONNECTED:
        print(f"[summary] {len(_UNCONNECTED)} connection(s) FAILED:")
        for unit_key, stream_key, reason in _UNCONNECTED:
            print(f"  - {stream_key} -> {unit_key}: {reason}")
        return 2 if os.environ.get("S2S_STRICT_CONNECTIONS", "1") != "0" else 0
    print("[summary] all listed connections attached")
    return 0


def save_case(path):
    """Release the solver, wait until it is idle, save; retry once with the solver held."""
    if os.path.exists(path):
        os.remove(path)
    set_solver_hold(False)
    started = time.time()
    try:
        while case.Solver.IsSolving and time.time() - started < 60:
            time.sleep(0.5)
    except Exception:
        pass
    try:
        case.SaveAs(path)
    except Exception as error:
        print(
            f"[save] SaveAs failed with the solver released ({_short(error)}); retrying held"
        )
        set_solver_hold(True)
        case.SaveAs(path)


def _pump(seconds=0.5):
    """Let HYSYS process its window messages (the PFD updates on them)."""
    end = time.time() + seconds
    while time.time() < end:
        try:
            hysys.DoEvents()
        except Exception:
            pass
        time.sleep(0.1)


def layout_pfd(report_path=None):
    """Arrange the PFD (graphics only: positions of icons) before the case is
    saved. Units go left to right in process order, feeds enter from the left,
    products leave to the right, recycles get their own lanes. Never fails the
    run: problems are reported and the case is saved as built."""
    if os.environ.get("S2S_PFD_LAYOUT", "1") == "0":
        print("[pfd] layout disabled (S2S_PFD_LAYOUT=0)")
        return None
    try:
        from sketch2sim.pfd_layout import layout_case
    except ImportError as error:
        print(
            f"[pfd] layout skipped: sketch2sim.pfd_layout not importable ({_short(error)})"
        )
        return None
    try:
        report = layout_case(
            case,
            pump=_pump,
            hide_helper_labels=os.environ.get("S2S_PFD_HIDE_HELPER_LABELS", "0") == "1",
            preview_path=report_path[:-5] + ".png" if report_path else None,
        )
    except Exception as error:
        print(f"[pfd] layout failed ({_short(error)}); the case is saved as built")
        return None
    if report and report_path:
        try:
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump(report, handle, indent=1, default=str)
        except Exception as error:
            print(f"[pfd] could not write the layout report: {_short(error)}")
    return report


ensure_property_package(globals().get("property_package"))
set_solver_hold(True)  # build the whole flowsheet before HYSYS starts solving

# === Step 4: Add Unit Operations (Agent 2) ===
units = {}  # Instantiation Agent must fill, for example: add_unit("P-101", "Feed Pump", TYPES["pump"])
# Instantiation Agent must add each unit operation in units according to the instructions file.

# === Step 5: Add Material Streams (Agent 2) ===
streams = {}  # Instantiation Agent must fill, for example: add_stream("S1", "Crude oil")
# Instantiation Agent must add each material stream in streams according to the instructions file.

# === Step 6: Connect Streams (Agent 3) ===
# Configuration Agent must connect streams to units here according to the instructions file.
# Connections must be grouped per unit.

# === Step 7: Save Simulation ===
# Owned by the template (re-inserted before every execution). The .hsc is written
# next to the generated scripts (S2S_HSC_DIR, default outputs/); the executor sets
# S2S_HSC_SUFFIX so every attempt keeps its own case and topology report.
base_dir = os.path.abspath(os.environ.get("S2S_HSC_DIR", "outputs"))
os.makedirs(base_dir, exist_ok=True)
safe_case_name = re.sub(r'[\\/*?:"<>|]', "_", case_name) + os.environ.get(
    "S2S_HSC_SUFFIX", ""
)
save_path = os.path.join(base_dir, safe_case_name + ".hsc")
# The diagram is arranged first, so that the attachment check below also
# proves that the layout changed nothing but positions.
layout_pfd(os.path.join(base_dir, safe_case_name + "_pfd.json"))
exit_code = finish_connections(
    os.path.join(base_dir, safe_case_name + "_topology.json")
)
save_case(save_path)
print(f"Simulation saved at: {save_path}")
_RESULT["ok"] = exit_code == 0
sys.exit(exit_code)

# === End of Template ===
