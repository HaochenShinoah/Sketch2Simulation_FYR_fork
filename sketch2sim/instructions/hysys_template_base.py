import os
import re
import sys

from win32com.client import Dispatch

# === Step 1: Launch HYSYS and create a new case (Agent 1) ===
case_name = "FILL_CASE_NAME"  # Basis Agent must fill
hysys = Dispatch("HYSYS.Application")
hysys.Visible = True
case = hysys.SimulationCases.Add(case_name)
case.Visible = True

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
# The agents only call the functions below. Every HYSYS type string and port
# member is tried from an ordered candidate list, so one wrong spelling does not
# abort the build; failures are reported at the end (see finish_connections).

# Ordered candidate type strings for flowsheet.Operations.Add(name, type).
# First entry = verified HYSYS TypeName where known.
TYPES = {
    "pump": ["pumpop"],
    "compressor": ["compressor", "CompressOp", "compressorop"],
    "expander": ["expandop", "expander", "expanderop"],
    "valve": ["valveop"],
    "mixer": ["mixerop"],
    "tee": ["teeop"],
    "heater": ["heaterop"],
    "cooler": ["coolerop"],
    "heat_exchanger": ["heatexop", "HeatExchanger"],
    "air_cooler": ["aircoolerop", "aircooler", "coolerop"],
    "fired_heater": [
        "dynamicfiredheaterop",
        "firedheaterop",
        "firedheater",
        "heaterop",
    ],
    "separator": ["flashtank", "separatorop", "separator"],
    "separator_3phase": [
        "separator3op",
        "3phaseseparatorop",
        "threephaseseparatorop",
        "3phaseseparator",
        "flashtank",
    ],
    "tank": ["tankop", "tank", "flashtank"],
    "reactor_conversion": ["conversionreactorop"],
    "reactor_equilibrium": [
        "equilibriumreactorop",
        "eqreactorop",
        "equilibriumreactor",
        "conversionreactorop",
    ],
    "reactor_gibbs": ["gibbsreactorop", "gibbsreactor", "conversionreactorop"],
    "reactor_cstr": ["cstrop", "cstr", "cstreactorop", "conversionreactorop"],
    "reactor_pfr": ["pfrop", "pfreactorop", "plugflowreactorop", "conversionreactorop"],
    "column_distillation": ["distillation", "ColumnOp"],
    "column_absorber": ["absorber", "absorberop", "distillation", "ColumnOp"],
    "column_reboiled_absorber": [
        "reboiledabsorber",
        "rebabsorber",
        "distillation",
        "ColumnOp",
    ],
    "column_refluxed_absorber": [
        "refluxedabsorber",
        "refabsorber",
        "distillation",
        "ColumnOp",
    ],
    "column_shortcut": ["shdistop", "fractop"],
    "component_splitter": ["fractop", "componentsplitterop", "compsplitterop"],
    "recycle": ["recycle"],
    "adjust": ["adjust"],
    "set": ["setop"],
    "spreadsheet": ["spreadsheetop"],
    "pipe": ["pipeseg", "pipesegop"],
}

# Ordered candidate COM members per connection role. "Name.Add" means
# op.Name.Add(stream) (collection); a bare name means op.Name = stream.
PORTS = {
    "single_in": ["FeedStream", "Feeds.Add", "Feed", "Inlets.Add", "AttachedFeeds.Add"],
    "single_out": [
        "ProductStream",
        "Product",
        "Products.Add",
        "Outlets.Add",
        "AttachedProducts.Add",
    ],
    "multi_in": ["Feeds.Add", "FeedStream", "Inlets.Add", "AttachedFeeds.Add"],
    "mixer_out": ["Product", "Products.Add", "ProductStream", "AttachedProducts.Add"],
    "tee_in": ["Feed", "FeedStream", "Feeds.Add", "AttachedFeeds.Add"],
    "tee_out": ["Products.Add", "Product", "AttachedProducts.Add"],
    "vapour_out": [
        "VapourProduct",
        "VapourOutlet",
        "OverheadProduct",
        "Products.Add",
        "AttachedProducts.Add",
        "ProductStream",
    ],
    "liquid_out": [
        "LiquidProduct",
        "LiquidOutlet",
        "BottomsProduct",
        "Products.Add",
        "AttachedProducts.Add",
        "ProductStream",
    ],
    "light_liquid_out": [
        "LightLiquidProduct",
        "LiquidProduct",
        "Products.Add",
        "AttachedProducts.Add",
    ],
    "heavy_liquid_out": [
        "HeavyLiquidProduct",
        "WaterProduct",
        "Products.Add",
        "AttachedProducts.Add",
    ],
    "tube_in": ["TubeSideFeed", "TubeSideFeedStream", "Feeds.Add", "AttachedFeeds.Add"],
    "tube_out": [
        "TubeSideProduct",
        "TubeSideProductStream",
        "Products.Add",
        "AttachedProducts.Add",
    ],
    "shell_in": [
        "ShellSideFeed",
        "ShellSideFeedStream",
        "Feeds.Add",
        "AttachedFeeds.Add",
    ],
    "shell_out": [
        "ShellSideProduct",
        "ShellSideProductStream",
        "Products.Add",
        "AttachedProducts.Add",
    ],
    "splitter_in": ["Inlets.Add", "Feeds.Add", "FeedStream", "AttachedFeeds.Add"],
    "splitter_overhead": [
        "OverheadOutlets.Add",
        "Products.Add",
        "AttachedProducts.Add",
    ],
    "splitter_bottom": [
        "BottomOutlets",
        "BottomOutlets.Add",
        "Products.Add",
        "AttachedProducts.Add",
    ],
    "fired_in": ["Feeds.Add", "FeedStream", "TubeFeeds.Add", "AttachedFeeds.Add"],
    "fired_out": [
        "Products.Add",
        "ProductStream",
        "TubeProducts.Add",
        "AttachedProducts.Add",
    ],
    "flue_gas_out": [
        "FlueGasStream",
        "FlueGas",
        "StackGas",
        "Products.Add",
        "AttachedProducts.Add",
    ],
    "air_in": ["AirStream", "AirFeed", "Feeds.Add", "AttachedFeeds.Add"],
    "fuel_in": ["FuelStream", "FuelFeed", "Feeds.Add", "AttachedFeeds.Add"],
}

_CONNECTED = []  # (unit_key, stream_key, member)
_UNCONNECTED = []  # (unit_key, stream_key, reason)
_NOTES = []  # known limitations; reported but not counted as failures


def _short(error):
    return str(error).replace("\n", " ")[:120]


def _set_object_name(obj, name):
    """Rename a HYSYS object; the member is `name` in pywin32 late binding, `Name` elsewhere."""
    for member in ("name", "Name"):
        try:
            setattr(obj, member, name)
            return True
        except Exception:
            continue
    return False


def _object_name(obj):
    for member in ("name", "Name"):
        try:
            return str(getattr(obj, member))
        except Exception:
            continue
    return ""


def add_unit(key, name, type_names):
    """Create one unit operation, trying each HYSYS type string in order.

    Operations.Add has been seen with the argument orders (name, type) and
    (type, name) and as Add(type) + rename, so all three are tried per type.
    """
    errors = []
    for type_name in type_names:
        for label, call in (
            ("Add(name, type)", lambda: flowsheet.Operations.Add(name, type_name)),
            ("Add(type, name)", lambda: flowsheet.Operations.Add(type_name, name)),
            ("Add(type)+rename", lambda: flowsheet.Operations.Add(type_name)),
        ):
            try:
                op = call()
            except Exception as error:
                errors.append(f"{type_name} {label}: {_short(error)}")
                continue
            if op is None:
                # Some builds return nothing from Add; the object may still exist.
                try:
                    op = flowsheet.Operations.Item(name)
                except Exception:
                    op = None
            if op is None:
                errors.append(f"{type_name} {label}: Operations.Add returned None")
                continue
            if _object_name(op) != name:
                _set_object_name(op, name)
            units[key] = op
            print(f"[unit] {key} = {name!r} ({type_name}, {label})")
            return op
    raise RuntimeError(f"Could not create unit {key!r} ({name!r}); tried {errors}")


def add_stream(key, name):
    """Create one material stream; the dictionary key is the stream id."""
    errors = []
    stream = None
    try:
        stream = flowsheet.MaterialStreams.Add(name)
    except Exception as error:
        errors.append(f"Add(name): {_short(error)}")
    if stream is None:
        try:
            stream = flowsheet.MaterialStreams.Add()
            _set_object_name(stream, name)
        except Exception as error:
            errors.append(f"Add()+rename: {_short(error)}")
            stream = None
    if stream is None:
        try:
            stream = flowsheet.MaterialStreams.Item(name)
        except Exception as error:
            errors.append(f"Item(name): {_short(error)}")
            stream = None
    if stream is None:
        raise RuntimeError(
            f"Could not create material stream {key!r} ({name!r}); tried {errors}"
        )
    streams[key] = stream
    print(f"[stream] {key} = {name!r}")
    return stream


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
            import pythoncom

            oleobj = target._oleobj_
            dispid = oleobj.GetIDsOfNames(0, member)
            oleobj.Invoke(dispid, 0, pythoncom.DISPATCH_PROPERTYPUTREF, 0, stream)
        except Exception:
            raise first_error


def _attempt(target, members, stream, unit_key, stream_key):
    errors = []
    for member in members:
        try:
            _apply_member(target, member, stream)
        except Exception as error:
            errors.append(f"{member}: {_short(error)}")
            continue
        _CONNECTED.append((unit_key, stream_key, member))
        print(f"[connect] {unit_key}.{member} <- {stream_key}")
        return True
    _UNCONNECTED.append((unit_key, stream_key, " | ".join(errors)))
    print(f"[connect] WARNING {stream_key} NOT attached to {unit_key}: {errors}")
    return False


def connect(unit_key, stream_key, role, members=None):
    """Attach streams[stream_key] to units[unit_key] using the port role."""
    return _attempt(
        units[unit_key],
        members or PORTS[role],
        streams[stream_key],
        unit_key,
        stream_key,
    )


def skip(unit_key, stream_key, reason):
    """Record a stream deliberately left unconnected (no free port); not a failure."""
    _NOTES.append(f"{stream_key} not attached to {unit_key}: {reason}")
    print(f"[connect] NOTE {stream_key} not attached to {unit_key}: {reason}")
    return False


def _sub_operation(column_flowsheet, names, type_hint=None):
    """Find a column sub-operation by exact name, then by TypeName, then by name substring."""
    operations = column_flowsheet.Operations
    for name in names:
        try:
            item = operations.Item(name)
        except Exception:
            item = None
        if item is not None:
            return item
    if type_hint:
        # Live-verified V14 approach: the tray section's TypeName contains "tray".
        try:
            for op in operations:
                try:
                    if type_hint.lower() in str(op.TypeName).lower():
                        return op
                except Exception:
                    continue
        except Exception:
            pass
    try:
        for name in list(operations.Names):
            if any(token.lower() in str(name).lower() for token in names):
                return operations.Item(name)
    except Exception:
        pass
    return None


def _column_parts(col):
    try:
        case.Activate()  # column internals may be unreachable until the case is active
    except Exception:
        pass
    try:
        column_flowsheet = col.ColumnFlowsheet
    except Exception:
        # Column sub-flowsheets are also reachable through the Flowsheets collection.
        column_flowsheet = flowsheet.Flowsheets.Item(col.name)
    tower = _sub_operation(
        column_flowsheet, ["Main Tower", "Main TS", "Tower", "TS"], type_hint="tray"
    )
    condenser = _sub_operation(column_flowsheet, ["Condenser"])
    reboiler = _sub_operation(column_flowsheet, ["Reboiler"])
    return column_flowsheet, tower, condenser, reboiler


def _default_stage(tower):
    try:
        trays = int(tower.NumberOfTrays)
        if trays >= 2:
            return max(1, trays // 2)
    except Exception:
        pass
    return 5


def connect_column_feed(unit_key, stream_key, stage=None):
    """Attach a main-flowsheet stream as a feed to a column template."""
    col, stream = units[unit_key], streams[stream_key]
    errors = []
    try:
        column_flowsheet, tower, _, _ = _column_parts(col)
    except Exception as error:
        column_flowsheet, tower = None, None
        errors.append(f"ColumnFlowsheet: {_short(error)}")
    if tower is not None:
        stage = stage or _default_stage(tower)
        for label, action in (
            (
                "Main Tower.SpecifyFeedLocation",
                lambda: tower.SpecifyFeedLocation(stream, stage),
            ),
            (
                "Main Tower.AddFeedStream",
                lambda: tower.AddFeedStream(
                    _object_name(stream) or stream_key, stage, False
                ),
            ),
        ):
            try:
                action()
                _CONNECTED.append((unit_key, stream_key, label))
                print(f"[connect] {unit_key}.{label} <- {stream_key} (stage {stage})")
                return True
            except Exception as error:
                errors.append(f"{label}: {_short(error)}")
    fallbacks = [(col, "AttachedFeeds.Add"), (col, "Feeds.Add")]
    if column_flowsheet is not None:
        fallbacks.append((column_flowsheet, "FeedStreams.Add"))
    for target, member in fallbacks:
        try:
            _apply_member(target, member, stream)
            _CONNECTED.append((unit_key, stream_key, member))
            print(f"[connect] {unit_key}.{member} <- {stream_key}")
            return True
        except Exception as error:
            errors.append(f"{member}: {_short(error)}")
    _UNCONNECTED.append((unit_key, stream_key, " | ".join(errors)))
    print(
        f"[connect] WARNING column feed {stream_key} NOT attached to {unit_key}: {errors}"
    )
    return False


_COLUMN_PRODUCT_MEMBERS = {
    "overhead_vapour": (
        "condenser",
        ["VapourProduct", "VapourOutlet", "OverheadVapour", "OverheadProduct"],
    ),
    "distillate": (
        "condenser",
        ["LiquidProduct", "Distillate", "DistillateProduct", "LiquidOutlet"],
    ),
    "water_draw": (
        "condenser",
        ["HeavyLiquidProduct", "WaterDraw", "AqueousProduct", "WaterProduct"],
    ),
    "bottoms": (
        "reboiler",
        ["LiquidProduct", "BottomsProduct", "Bottoms", "LiquidOutlet"],
    ),
}


def connect_column_product(unit_key, stream_key, role, stage=None):
    """Attach a column product; role: overhead_vapour | distillate | water_draw | bottoms | side_draw."""
    col, stream = units[unit_key], streams[stream_key]
    errors = []
    try:
        column_flowsheet, tower, condenser, reboiler = _column_parts(col)
    except Exception as error:
        column_flowsheet = tower = condenser = reboiler = None
        errors.append(f"ColumnFlowsheet: {_short(error)}")
    if role == "side_draw":
        if tower is not None:
            stage = stage or _default_stage(tower)
            draw_name = _object_name(stream) or stream_key
            for label, action in (
                (
                    "Main Tower.AddDrawStream(stage, kind, name)",
                    lambda: tower.AddDrawStream(stage, "Liquid", draw_name),
                ),
                (
                    "Main Tower.AddDrawStream(name, stage, kind)",
                    lambda: tower.AddDrawStream(draw_name, stage, "Liquid"),
                ),
                (
                    "Main Tower.AddLiquidDraw",
                    lambda: tower.AddLiquidDraw(draw_name, stage),
                ),
                (
                    "Main Tower.AddDrawStream(name, stage, False)",
                    lambda: tower.AddDrawStream(draw_name, stage, False),
                ),
                (
                    "Main Tower.AddProductStream",
                    lambda: tower.AddProductStream(draw_name, stage, False),
                ),
            ):
                try:
                    action()
                    _CONNECTED.append((unit_key, stream_key, label))
                    print(
                        f"[connect] {unit_key}.{label} <- {stream_key} (stage {stage})"
                    )
                    return True
                except Exception as error:
                    errors.append(f"{label}: {_short(error)}")
        _NOTES.append(
            f"{stream_key} is a side draw of {unit_key}; side draws are not attachable through "
            f"the COM column template in this workflow and were left unconnected ({errors})."
        )
        print(f"[connect] NOTE side draw {stream_key} of {unit_key} left unconnected")
        return False
    owner_name, members = _COLUMN_PRODUCT_MEMBERS[role]
    owner = condenser if owner_name == "condenser" else reboiler
    targets = []
    if owner is not None:
        targets += [(owner, member) for member in members]
    targets += [(col, "AttachedProducts.Add"), (col, "Products.Add")]
    if column_flowsheet is not None:
        targets.append((column_flowsheet, "ProductStreams.Add"))
    for target, member in targets:
        try:
            _apply_member(target, member, stream)
            _CONNECTED.append((unit_key, stream_key, f"{role}:{member}"))
            print(f"[connect] {unit_key}.{role} ({member}) <- {stream_key}")
            return True
        except Exception as error:
            errors.append(f"{member}: {_short(error)}")
    _UNCONNECTED.append((unit_key, stream_key, " | ".join(errors)))
    print(
        f"[connect] WARNING column product {stream_key} ({role}) NOT attached to {unit_key}: {errors}"
    )
    return False


PROPERTY_PACKAGE_ALIASES = {
    "pengrob": ["PengRob", "Peng-Robinson", "PengRobinson", "PR"],
    "pengrobinson": ["PengRob", "Peng-Robinson", "PengRobinson", "PR"],
    "pr": ["PengRob", "Peng-Robinson", "PengRobinson"],
    "srk": ["SRK", "Soave-Redlich-Kwong", "SoaveRedlichKwong"],
    "nrtl": ["NRTL"],
    "uniquac": ["UNIQUAC"],
    "wilson": ["Wilson"],
    "antoine": ["Antoine"],
    "asmesteam": ["ASME Steam", "ASMESteam", "ASME_Steam"],
    "aminepkg": ["Amine Pkg", "AminePkg", "Amines", "Acid Gas - Chemical Solvents"],
    "sourpr": ["Sour PR", "SourPR", "Sour Peng-Robinson"],
    "chaoseader": ["Chao Seader", "ChaoSeader", "Chao-Seader"],
    "graysonstreed": ["Grayson Streed", "GraysonStreed", "Grayson-Streed"],
}


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _same_package(a, b):
    a, b = _norm(a), _norm(b)
    return bool(a) and bool(b) and (a in b or b in a)


def ensure_property_package(requested):
    """Confirm the fluid package carries the requested property package.

    HYSYS accepts several spellings ("PengRob" is the one verified live); when the
    read-back disagrees, the known aliases are applied inside a basis change.
    Never raises: a package that cannot be confirmed is reported as a NOTE.
    """
    try:
        current = str(fluidpkg.PropertyPackageName)
    except Exception as error:
        current = ""
        print(f"[basis] could not read PropertyPackageName: {_short(error)}")
    if not requested:
        print(f"[basis] property package: {current!r}")
        return bool(current)
    if _same_package(requested, current):
        print(f"[basis] property package confirmed: {current!r}")
        return True
    errors = []
    candidates = [requested] + PROPERTY_PACKAGE_ALIASES.get(_norm(requested), [])
    for candidate in candidates:
        try:
            hybasis.StartBasisChange()
        except Exception:
            pass
        try:
            fluidpkg.PropertyPackageName = candidate
            readback = str(fluidpkg.PropertyPackageName)
            ok = _same_package(candidate, readback)
        except Exception as error:
            errors.append(f"{candidate}: {_short(error)}")
            readback, ok = "", False
        try:
            hybasis.EndBasisChange()
        except Exception:
            pass
        if ok:
            print(f"[basis] property package set via {candidate!r}: {readback!r}")
            return True
        errors.append(f"{candidate}: HYSYS reports {readback!r}")
    _NOTES.append(
        f"property package {requested!r} could not be confirmed (HYSYS reports "
        f"{current!r}); tried {errors}"
    )
    print(f"[basis] WARNING property package {requested!r} not confirmed: {errors}")
    return False


def set_solver_hold(hold):
    """Hold (True) or release (False) the HYSYS solver while the flowsheet is built."""
    try:
        case.Solver.CanSolve = not hold
        print(f"[solver] CanSolve = {not hold}")
        return True
    except Exception as error:
        print(f"[solver] could not set CanSolve: {_short(error)}")
        return False


def finish_connections():
    """Report the connection outcome; exit 2 if a required connection failed."""
    print(
        f"\n[summary] units: {len(units)}  streams: {len(streams)}  connections: {len(_CONNECTED)}"
    )
    for message in _NOTES:
        print(f"[summary] NOTE {message}")
    if _UNCONNECTED:
        print(f"[summary] {len(_UNCONNECTED)} connection(s) FAILED:")
        for unit_key, stream_key, reason in _UNCONNECTED:
            print(f"  - {stream_key} -> {unit_key}: {reason}")
        if os.environ.get("S2S_STRICT_CONNECTIONS", "1") != "0":
            sys.exit(2)
    print("[summary] all listed connections attached")


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
# The .hsc is written next to the generated scripts (outputs/ by default);
# override with the S2S_HSC_DIR environment variable. A relative path would be
# resolved by HYSYS against its own working folder, not this repository.
base_dir = os.path.abspath(os.environ.get("S2S_HSC_DIR", "outputs"))
os.makedirs(base_dir, exist_ok=True)

safe_case_name = re.sub(r'[\\/*?:"<>|]', "_", case_name)
save_filename = safe_case_name + ".hsc"
save_path = os.path.join(base_dir, save_filename)
set_solver_hold(False)
case.SaveAs(save_path)
print(f"Simulation saved at: {save_path}")
finish_connections()

# === End of Template ===
