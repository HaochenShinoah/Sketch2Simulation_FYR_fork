"""Execute the generated HYSYS script against a fake COM layer.

This does not prove the COM member names are right (only HYSYS can), but it
proves the helper layer, the agents' output format and the template splice
produce a script that runs end to end, verifies every attachment by reading it
back, reports failures and exits correctly.

The fake models what the live HYSYS V11 experiments showed: property sets and
collection Adds record attachments that AttachedFeeds / AttachedProducts read
back; HeavyLiquidProduct on a 2-phase vessel is accepted but attaches nothing;
a column has no ColumnFlowsheet until LoadColumnTemplate; AddFeedStream and
AddDrawStream export main-flowsheet streams; a single-valued member assigned
twice keeps only the last stream.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from sketch2sim.pipeline import PipelineConfig, run_pipeline
from tests.conftest import REPO_ROOT, TEMPLATE_PATH

INSTRUCTIONS_DIR = REPO_ROOT / "sketch2sim" / "instructions"

# Members listed in STUB_REJECT raise like a COM "Member not found" error;
# members listed in STUB_SILENT are accepted but attach nothing (the HYSYS
# silent no-op, e.g. HeavyLiquidProduct on a 2-phase separator).
STUB = textwrap.dedent(
    """
    import json, os, sys, types

    REJECT = set(json.loads(os.environ.get("STUB_REJECT", "[]")))
    SILENT = set(json.loads(os.environ.get("STUB_SILENT", "[]")))

    class ComError(Exception):
        pass

    def _check(member):
        if member in REJECT:
            raise ComError(f"(-2147352573, 'Member not found.', None, None) {member}")

    class Generic:
        '''Permissive stand-in for members the fake does not model.'''
        def __init__(self, path="HYSYS"):
            object.__setattr__(self, "_path", path)
            object.__setattr__(self, "_children", {})
        def __getattr__(self, item):
            if item.startswith("_"):
                raise AttributeError(item)
            _check(item)
            children = object.__getattribute__(self, "_children")
            if item not in children:
                children[item] = Generic(f"{self._path}.{item}")
            return children[item]
        def __setattr__(self, item, value):
            _check(item)
            object.__setattr__(self, item, value)
        def __call__(self, *args, **kwargs):
            return Generic(f"{self._path}()")
        def __iter__(self):
            return iter([])
        def __int__(self):
            return 10

    class Stream:
        def __init__(self, name, energy=False):
            self.name = name
            self.energy = energy
        @property
        def Name(self):
            return self.name
        @Name.setter
        def Name(self, value):
            self.name = value

    class StreamList:
        def __init__(self, streams):
            self._streams = streams
        @property
        def Names(self):
            return [s.name for s in self._streams]
        @property
        def Count(self):
            return len(self._streams)

    class AttachCollection:
        def __init__(self, op, kind, member):
            self._op, self._kind, self._member = op, kind, member
        def Add(self, stream):
            _check(self._member)
            if self._member in SILENT:
                return
            self._op._attach(self._kind, stream, self._member, single=False)
        @property
        def Names(self):
            return [s.name for s in self._op._list(self._kind)]
        @property
        def Count(self):
            return len(self.Names)

    IN_MEMBERS = {"FeedStream", "Feed", "TubeSideFeed", "ShellSideFeed",
                  "BurnerFeed", "TopLiquidFeed", "BtmVapourFeed"}
    OUT_MEMBERS = {"ProductStream", "Product", "VapourProduct", "LiquidProduct",
                   "HeavyLiquidProduct", "TubeSideProduct", "ShellSideProduct",
                   "CombustionProduct", "BottomOutlets", "TopVapourProduct",
                   "TopLiquidProduct", "TopWaterProduct", "BtmLiquidProduct"}
    COLUMN_MEMBERS = {"TopLiquidFeed", "BtmVapourFeed", "TopVapourProduct",
                      "TopLiquidProduct", "TopWaterProduct", "BtmLiquidProduct"}
    IN_COLLECTIONS = {"Feeds", "Inlets", "RadInlet", "FuelsIn"}
    OUT_COLLECTIONS = {"Products", "OverheadOutlets", "RadOutlet"}
    COLUMN_TYPES = {"distillation", "absorber", "reboiledabsorber",
                    "refluxedabsorber", "threephase", "liqliqextractor"}

    class Op:
        def __init__(self, flowsheet, name, type_name):
            d = self.__dict__
            d["_fs"], d["name"], d["TypeName"] = flowsheet, name, type_name
            d["VisibleTypeName"] = type_name
            d["_feeds"], d["_products"], d["_single"] = [], [], {}
            d["_template"], d["_generic"], d["EnergyStream"] = None, {}, None
        def _list(self, kind):
            return self._feeds if kind == "in" else self._products
        def _attach(self, kind, stream, member, single):
            streams = self._list(kind)
            if single:
                old = self._single.get(member)
                if old is not None and old in streams:
                    streams.remove(old)  # a single-valued member keeps one stream
                self._single[member] = stream
            if stream not in streams:
                streams.append(stream)
        def _detach(self, stream):
            for streams in (self._feeds, self._products):
                if stream in streams:
                    streams.remove(stream)
        def LoadColumnTemplate(self, name=None):
            _check("LoadColumnTemplate")
            if self.TypeName not in COLUMN_TYPES:
                raise ComError("(-2147352567, 'Exception occurred.', None, None)")
            if self._template is None:
                self.__dict__["_template"] = ColumnFlowsheet(self)
        def CreateColumnTemplate(self):
            self.__dict__["_template"] = ColumnFlowsheet(self, empty=True)
        def __getattr__(self, item):
            if item.startswith("_"):
                raise AttributeError(item)
            _check(item)
            if item == "AttachedFeeds":
                return StreamList(self._feeds)
            if item == "AttachedProducts":
                return StreamList(self._products)
            if item in IN_COLLECTIONS:
                return AttachCollection(self, "in", item)
            if item in OUT_COLLECTIONS:
                return AttachCollection(self, "out", item)
            if item in IN_MEMBERS or item in OUT_MEMBERS:
                return self._single.get(item)
            if item == "ColumnFlowsheet":
                if self._template is None:
                    raise ComError("(-2147352567, 'Exception occurred.', "
                                   "(0, None, None, None, 0, -2147467259), None)")
                return self._template
            generic = self.__dict__["_generic"]
            if item not in generic:
                generic[item] = Generic(f"{self.name}.{item}")
            return generic[item]
        def __setattr__(self, item, value):
            _check(item)
            if item in IN_MEMBERS or item in OUT_MEMBERS:
                silent = item in SILENT
                silent |= item == "HeavyLiquidProduct" and self.TypeName == "flashtank"
                silent |= item in COLUMN_MEMBERS and (
                    self._template is None or self._template.empty)
                if silent:
                    return  # accepted by HYSYS, nothing attached
                self._attach("in" if item in IN_MEMBERS else "out", value, item, True)
                return
            if item == "EnergyStream" and value is not None:
                # HYSYS lists the energy stream among the feeds and products
                for streams in (self._feeds, self._products):
                    if value not in streams:
                        streams.append(value)
            if item in ("name", "Name"):
                while any(o is not self and o.name == value for o in self._fs._ops):
                    value = value + "_2"  # HYSYS keeps operation names unique
                self.__dict__["name"] = value
                return
            self.__dict__[item] = value

    class TraySection(Op):
        def __init__(self, column):
            super().__init__(column._fs, "Main Tower", "traysection")
            self.__dict__["_column"] = column
            self.__dict__["NumberOfStages"] = 10
            self.__dict__["NumberOfTrays"] = 10
            self.__dict__["_internal"] = []
        def AddFeedStream(self, name, stage, is_energy):
            _check("AddFeedStream")
            if self._fs._find_stream(name) is not None:
                name = name + "_2"  # HYSYS never attaches an existing main stream
            stream = self._fs.MaterialStreams.Add(name)
            self._column._attach("in", stream, "AddFeedStream", single=False)
            self.__dict__["_internal"].append(Stream(name))
        def AddDrawStream(self, name, phase, stage, export, add_spec):
            _check("AddDrawStream")
            if self._fs._find_stream(name) is not None:
                name = name + "_2"
            stream = self._fs.MaterialStreams.Add(name)
            self._column._attach("out", stream, "AddDrawStream", single=False)
            self.__dict__["_internal"].append(Stream(name))

    class ColumnFlowsheet:
        def __init__(self, column, empty=False):
            self._column, self.empty = column, empty
            self._tray = None if empty else TraySection(column)
            self._ops = {} if empty else {
                "Main Tower": self._tray,
                "Condenser": Generic("Condenser"),
                "Reboiler": Generic("Reboiler"),
            }
            for name, op in self._ops.items():
                if isinstance(op, Generic):
                    object.__setattr__(op, "TypeName", name.lower())
        @property
        def Operations(self):
            fake = self
            class Ops:
                Names = list(fake._ops)
                def Item(self, name):
                    return fake._ops[name]
            return Ops()
        @property
        def MaterialStreams(self):
            internal = self._tray.__dict__["_internal"] if self._tray else []
            class Internal:
                Names = [s.name for s in internal]
                def Item(self, name):
                    for s in internal:
                        if s.name == name:
                            return s
                    raise ComError("not found")
            return Internal()
        @property
        def FeedStreams(self):
            return StreamList(self._column._feeds)

    class OperationsColl:
        def __init__(self, fs):
            self._fs = fs
        def Add(self, name, type_name=None):
            _check("Operations")
            while any(op.name == name for op in self._fs._ops):
                name = name + "_2"  # HYSYS keeps operation names unique
            op = Op(self._fs, name, str(type_name))
            self._fs._ops.append(op)
            return op
        def Item(self, name):
            for op in self._fs._ops:
                if op.name == name:
                    return op
            raise ComError("not found")
        @property
        def Names(self):
            return [op.name for op in self._fs._ops]

    class MaterialStreamsColl:
        def __init__(self, fs):
            self._fs = fs
        def Add(self, name):
            _check("MaterialStreams")
            while self._fs._find_stream(name) is not None:
                name = name + "_2"  # HYSYS keeps stream names unique
            stream = Stream(name)
            self._fs._streams.append(stream)
            return stream
        def Item(self, name):
            stream = self._fs._find_stream(name)
            if stream is None:
                raise ComError("not found")
            return stream
        def Remove(self, name):
            stream = self._fs._find_stream(name)
            if stream is None:
                raise ComError("not found")
            self._fs._streams.remove(stream)
            for op in self._fs._ops:
                op._detach(stream)
        @property
        def Names(self):
            return [s.name for s in self._fs._streams]

    class EnergyStreamsColl:
        def __init__(self, fs):
            self._fs = fs
        def Add(self, name):
            stream = Stream(name, energy=True)
            self._fs._energy.append(stream)
            return stream

    # --- PFD (graphics) model: items with positions, HYSYS's ordering rule ---
    ICON = {"distillation": (138.0, 228.0), "flashtank": (56.0, 144.0),
            "sep3op": (56.0, 144.0), "tankop": (56.0, 144.0), "mixerop": (48.0, 76.0),
            "teeop": (48.0, 76.0), "compressor": (72.0, 56.0)}

    class PfdItem:
        def __init__(self, fs, name, kind, width, height, index):
            d = self.__dict__
            d["_fs"], d["name"], d["Type"] = fs, name, kind
            d["Width"], d["Height"], d["Hidden"] = width, height, False
            d["XPosition"], d["YPosition"] = 60.0 * index, 0.0
        def _unit(self):
            for op in self._fs._ops:
                if op.name == self.name:
                    return op
            return None
        def _ends(self, stream_name):
            '''(source unit item, destination unit item) of a stream, or Nones.'''
            items = self._fs._pfd._items
            source = destination = None
            for other in self._fs._ops:
                if any(s.name == stream_name for s in other._products):
                    source = items.get(other.name)
                if any(s.name == stream_name for s in other._feeds):
                    destination = items.get(other.name)
            return source, destination
        def _forward(self, stream_name):
            source, destination = self._ends(stream_name)
            return not (source and destination and source.XPosition > destination.XPosition)
        def _allowed(self, x):
            '''HYSYS keeps feed icon < unit < product icon on the x axis (for
            streams that run forward; a recycle icon can satisfy neither side).'''
            op = self._unit()
            items = self._fs._pfd._items
            if op is not None:
                for s in op._feeds:
                    if s.energy or not self._forward(s.name):
                        continue
                    if s.name in items and items[s.name].XPosition >= x:
                        return False
                for s in op._products:
                    if s.energy or not self._forward(s.name):
                        continue
                    if s.name in items and items[s.name].XPosition <= x:
                        return False
                return True
            if any(s.name == self.name for s in self._fs._energy):
                return True  # duty icons move freely (cs4 astra: E-Reactor)
            if not self._forward(self.name):
                return False
            source, destination = self._ends(self.name)
            if source is not None and source.XPosition >= x:
                return False
            return not (destination is not None and destination.XPosition <= x)
        def __setattr__(self, item, value):
            if item == "XPosition" and not self._allowed(float(value)):
                return  # silently refused, as HYSYS does
            if item in ("Width", "Height"):
                return  # ignored, as HYSYS does
            self.__dict__[item] = value

    class PfdItems:
        def __init__(self, items):
            self._list = list(items)
        @property
        def Names(self):
            return [it.name for it in self._list]
        @property
        def Count(self):
            return len(self._list)
        def Item(self, key):
            if isinstance(key, int):
                return self._list[key]
            for it in self._list:
                if it.name == key:
                    return it
            raise ComError("not found")

    class Pfd:
        def __init__(self, fs):
            self._fs, self.name, self._items, self._labels = fs, "PFD 1", {}, {}
        def _sync(self):
            '''Items as HYSYS creates them: units in a row, every stream icon
            next to its units (so the ordering rule holds from the start).'''
            index = len(self._items)
            for op in self._fs._ops:
                if op.name not in self._items:
                    w, h = ICON.get(op.TypeName, (64.0, 56.0))
                    self._items[op.name] = PfdItem(self._fs, op.name, -2, w, h, index)
                    self._items[op.name].__dict__["XPosition"] = 120.0 * index
                    self._labels[op.name] = PfdItem(self._fs, "", -4, 60.0, 0.0, index)
                    index += 1
            for s in self._fs._streams + self._fs._energy:
                if s.name in self._items:
                    continue
                item = PfdItem(self._fs, s.name, -2, 34.0, 12.0, index)
                source, destination = item._ends(s.name)
                if source is not None and destination is not None:
                    x = (source.XPosition + destination.XPosition) / 2
                elif source is not None:
                    x = source.XPosition + 60.0
                elif destination is not None:
                    x = destination.XPosition - 60.0
                else:
                    x = 120.0 * index
                item.__dict__["XPosition"] = x
                self._items[s.name] = item
                self._labels[s.name] = PfdItem(self._fs, "", -4, 60.0, 0.0, index)
                index += 1
        @property
        def Items(self):
            return None  # the real property returns nothing either
        def GetItems(self, kind, visibility):
            self._sync()
            if kind == -2:
                return PfdItems(self._items.values())
            if kind == -4:
                for owner, label in self._labels.items():
                    it = self._items[owner]
                    label.__dict__["XPosition"] = it.XPosition
                    label.__dict__["YPosition"] = it.YPosition + it.Height + 8
                return PfdItems(self._labels.values())
            return PfdItems([])
        def Centre(self):
            pass

    class PFDsColl:
        def __init__(self, fs):
            self._fs = fs
        @property
        def Count(self):
            return 1 if self._fs._shown else 0
        @property
        def ActivePFD(self):
            if not self._fs._shown:
                raise ComError("(-2147352567, 'Exception occurred.', None, None)")
            return self._fs._pfd

    class Flowsheet:
        def __init__(self):
            self._ops, self._streams, self._energy = [], [], []
            self._shown = False
            self._pfd = Pfd(self)
            self.Operations = OperationsColl(self)
            self.MaterialStreams = MaterialStreamsColl(self)
            self.EnergyStreams = EnergyStreamsColl(self)
            self.PFDs = PFDsColl(self)
        def _find_stream(self, name):
            for s in self._streams:
                if s.name == name:
                    return s
            return None
        def __getattr__(self, item):
            if item.startswith("_"):
                raise AttributeError(item)
            _check(item)
            return Generic(f"Flowsheet.{item}")

    class Solver:
        CanSolve = True
        IsSolving = False

    class Case:
        def __init__(self, name):
            self.name = name
            self.Flowsheet = Flowsheet()
            self.BasisManager = Generic("BasisManager")
            self.Solver = Solver()
            self.Visible = False
        def SaveAs(self, path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as handle:
                handle.write("fake hsc")
        def Close(self):
            pass
        def Activate(self):
            self.Flowsheet._shown = True  # the PFD view exists from now on

    class SimulationCases:
        def Add(self, name):
            return Case(name)

    class App:
        def __init__(self):
            self.SimulationCases = SimulationCases()
            self.Visible = False
        def Quit(self):
            pass
        def DoEvents(self):
            pass

    def Dispatch(progid):
        return App()

    module = types.ModuleType("win32com.client")
    module.Dispatch = Dispatch
    module.DispatchEx = Dispatch
    package = types.ModuleType("win32com")
    package.client = module
    sys.modules["win32com"] = package
    sys.modules["win32com.client"] = module
    pythoncom = types.ModuleType("pythoncom")
    pythoncom.DISPATCH_PROPERTYPUTREF = 8
    sys.modules["pythoncom"] = pythoncom
    """
)


def _run_script(
    script: Path,
    workdir: Path,
    reject: list[str] | None = None,
    strict: bool = True,
    silent: list[str] | None = None,
):
    (workdir / "sitecustomize.py").write_text(STUB, encoding="utf-8")
    env = {
        **os.environ,
        "PYTHONPATH": str(workdir),
        "STUB_REJECT": json.dumps(reject or []),
        "STUB_SILENT": json.dumps(silent or []),
        "S2S_HSC_DIR": str(workdir / "hsc"),
        "S2S_STRICT_CONNECTIONS": "1" if strict else "0",
    }
    env.pop("S2S_HSC_SUFFIX", None)
    return subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        env=env,
        cwd=workdir,
        timeout=120,
    )


def _generate(fake_llm, tmp_path: Path, case: str) -> Path:
    fake_llm.case = case
    cfg = PipelineConfig(
        image_path="images/cs1.png",
        log_dir=str(tmp_path / f"logs_{case}"),
        output_dir=str(tmp_path / f"outputs_{case}"),
        execute=False,
    )
    return Path(run_pipeline(cfg).combined_path)


def _script_with_steps_4_to_6(tmp_path: Path, steps_4_to_6: str) -> Path:
    """The filled template with hand-written Steps 4-6 (unit tests of the helpers)."""
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    code = template.replace('case_name = "FILL_CASE_NAME"', 'case_name = "Unit_Test"')
    code = code.replace(
        'property_package = "FILL_PROPERTY_PACKAGE"', 'property_package = "PengRob"'
    )
    code = re.sub(
        r"(?s)# === Step 4:.*?(?=# === Step 7:)", lambda _: steps_4_to_6 + "\n", code
    )
    script = tmp_path / "helpers_script.py"
    script.write_text(code, encoding="utf-8")
    return script


needs_instructions = pytest.mark.skipif(
    not (INSTRUCTIONS_DIR / "instantiation_instructions_simple_v5.txt").exists(),
    reason="instruction files not present",
)


@needs_instructions
@pytest.mark.parametrize("case", ["cs1", "cs2", "cs3", "cs4", "cs3b", "cs4b"])
def test_generated_script_runs_against_com_stub(fake_llm, repo_cwd, tmp_path, case):
    """cs3b / cs4b are the extractor outputs of the second real run (fuel and
    air drawn into the fired heater, a "Reflux" product and condenser cooling
    water): the burner mixer, fuel_in / flue_gas_out and the dropped streams go
    through the fake agents, the validators and the COM stub."""
    script = _generate(fake_llm, tmp_path, case)
    result = _run_script(script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Simulation saved at:" in result.stdout
    assert "[summary] all listed connections attached" in result.stdout
    assert "[connect] WARNING" not in result.stdout
    assert list((tmp_path / "hsc").glob("*_topology.json")), "topology report"
    # Step 7 arranged the diagram: every item placed, only recycle icons (which
    # can satisfy neither side of the ordering rule) may keep HYSYS's position
    assert "[pfd] layout:" in result.stdout and "layout skipped" not in result.stdout
    pfd_report = json.loads(
        next((tmp_path / "hsc").glob("*_pfd.json")).read_text(encoding="utf-8")
    )
    assert set(pfd_report["refused"]) <= set(pfd_report["recycles"]), pfd_report
    assert pfd_report["missing"] == []
    if case in ("cs3", "cs4", "cs3b", "cs4b"):
        # columns get their template loaded and real tray feeds
        assert "distillation, template:" in result.stdout
        assert "AddFeedStream(stage" in result.stdout
    if case in ("cs3", "cs3b"):
        # the side cuts are real draws now, not notes
        assert result.stdout.count("AddDrawStream(stage") == 3  # Kero, Diesel, AGO
        assert "NOTE side draw" not in result.stdout
    if case in ("cs3b", "cs4b"):
        # fuel + air merged into one burner feed, flue gas on its own port
        assert "_BURNER" in result.stdout
        assert "BurnerFeed" in result.stdout and "CombustionProduct" in result.stdout
        assert "skip" not in result.stdout.lower() or "single burner feed" not in (
            result.stdout
        )
        assert "TEE_FROM_H-101" not in result.stdout
        assert "Cooling Water" not in result.stdout and "Reflux" not in result.stdout
    if case == "cs3":
        # when HYSYS refuses the draw, that is a failure, not a known limitation
        result = _run_script(script, tmp_path, reject=["AddDrawStream"])
        assert result.returncode == 2, result.stdout + result.stderr
        assert "AddDrawStream" in result.stdout and "FAILED" in result.stdout


@needs_instructions
def test_generated_script_reports_failed_connections(fake_llm, repo_cwd, tmp_path):
    script = _generate(fake_llm, tmp_path, "cs1")
    # Pumps: FeedStream rejected -> there is no fallback member any more; exit 2.
    result = _run_script(script, tmp_path, reject=["FeedStream"])
    assert result.returncode == 2, result.stdout + result.stderr
    assert "S1 -> CRUDE_OIL_PUMP" in result.stdout
    # The separator liquid outlet rejected -> exit 2 after saving.
    result = _run_script(script, tmp_path, reject=["LiquidProduct"])
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Simulation saved at:" in result.stdout
    assert "S8 -> ELECTROSTATIC_SEPARATOR" in result.stdout
    # A member HYSYS accepts without attaching anything is a failure too.
    result = _run_script(script, tmp_path, silent=["LiquidProduct"])
    assert result.returncode == 2, result.stdout + result.stderr
    assert "not attached" in result.stdout
    # Non-strict mode keeps exit code 0 but still prints the failure.
    result = _run_script(script, tmp_path, reject=["LiquidProduct"], strict=False)
    assert result.returncode == 0
    assert "connection(s) FAILED" in result.stdout


@needs_instructions
def test_unit_creation_failure_is_fatal(fake_llm, repo_cwd, tmp_path):
    script = _generate(fake_llm, tmp_path, "cs1")
    result = _run_script(script, tmp_path, reject=["Operations"])
    assert result.returncode == 1
    assert "Could not create unit" in result.stderr


def test_displaced_connection_is_reported(tmp_path):
    steps = textwrap.dedent(
        """
        # === Step 4: Add Unit Operations (Agent 2) ===
        units = {}
        add_unit("P-1", "Pump", TYPES["pump"])
        # === Step 5: Add Material Streams (Agent 2) ===
        streams = {}
        add_stream("S1", "first")
        add_stream("S2", "second")
        add_stream("S3", "out")
        # === Step 6: Connect Streams (Agent 3) ===
        connect("P-1", "S1", "single_in")
        connect("P-1", "S2", "single_in")
        connect("P-1", "S3", "single_out")
        """
    )
    result = _run_script(_script_with_steps_4_to_6(tmp_path, steps), tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "displaced by a later connection" in result.stdout
    assert "S1 -> P-1" in result.stdout


def test_skip_on_a_column_and_unknown_reasons_fail(tmp_path):
    steps = textwrap.dedent(
        """
        # === Step 4: Add Unit Operations (Agent 2) ===
        units = {}
        add_unit("T-1", "Column", TYPES["column_distillation"])
        add_unit("V-1", "Drum", TYPES["separator"])
        # === Step 5: Add Material Streams (Agent 2) ===
        streams = {}
        add_stream("S1", "feed")
        add_stream("S2", "extra")
        add_stream("S3", "drum feed")
        # === Step 6: Connect Streams (Agent 3) ===
        skip("T-1", "S1", "no free vessel port")
        connect("V-1", "S3", "multi_in")
        skip("V-1", "S2", "because I felt like it")
        """
    )
    result = _run_script(_script_with_steps_4_to_6(tmp_path, steps), tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "skip() is not allowed on columns" in result.stdout
    assert "reason not allowed" in result.stdout


def test_column_helpers_and_silent_three_phase_member(tmp_path):
    steps = textwrap.dedent(
        """
        # === Step 4: Add Unit Operations (Agent 2) ===
        units = {}
        add_unit("E-1", "Heater", TYPES["heater"])
        add_unit("T-1", "Column", TYPES["column_distillation"])
        add_unit("V-1", "Settler", TYPES["separator"])
        add_unit("V-3", "Decanter", TYPES["separator_3phase"])
        add_unit("R-1", "Reactor", TYPES["reactor_conversion"])
        # === Step 5: Add Material Streams (Agent 2) ===
        streams = {}
        add_stream("S0", "heater in")
        add_stream("S1", "column feed")
        add_stream("S2", "overhead gas")
        add_stream("S3", "distillate")
        add_stream("S4", "bottoms")
        add_stream("S5", "kero")
        add_stream("S6", "settler feed")
        add_stream("S7", "heavy")
        add_stream("S8", "light")
        add_stream("S9", "decanter feed")
        add_stream("S10", "decanter heavy")
        add_stream("S11", "reactor feed")
        # === Step 6: Connect Streams (Agent 3) ===
        connect("E-1", "S0", "single_in")
        connect("E-1", "S1", "single_out")
        connect_column_feed("T-1", "S1")
        connect_column_product("T-1", "S2", "overhead_vapour")
        connect_column_product("T-1", "S3", "distillate")
        connect_column_product("T-1", "S4", "bottoms")
        connect_column_product("T-1", "S5", "side_draw", stage=4)
        connect("V-1", "S6", "multi_in")
        connect("V-1", "S8", "liquid_out")
        connect("V-1", "S7", "heavy_liquid_out")
        connect("V-3", "S9", "multi_in")
        connect("V-3", "S10", "heavy_liquid_out")
        connect("R-1", "S11", "multi_in")
        connect_energy("R-1")
        """
    )
    result = _run_script(_script_with_steps_4_to_6(tmp_path, steps), tmp_path)
    out = result.stdout
    assert result.returncode == 2, out + result.stderr
    # the heater keeps its product after the feed was exported by the column
    assert "E-1.ProductStream <- S1 (moved to the exported stream)" in out
    assert "T-1.AddFeedStream(stage 5) <- S1" in out
    assert "T-1.AddDrawStream(stage 4, phase 1) <- S5" in out
    assert "T-1.overhead_vapour (TopVapourProduct) <- S2" in out
    assert "R-1.EnergyStream <- 'E-Reactor'" in out
    # HeavyLiquidProduct on the 2-phase settler is the silent no-op -> failure
    assert "S7 -> V-1" in out and "not attached" in out
    # ... while the 3-phase vessel attaches it
    assert "V-3.HeavyLiquidProduct <- S10" in out
    topology = json.loads(
        next((tmp_path / "hsc").glob("*_topology.json")).read_text(encoding="utf-8")
    )
    assert topology["units"]["T-1"]["feeds"] == ["column feed"]
    assert set(topology["units"]["T-1"]["products"]) == {
        "overhead gas",
        "distillate",
        "bottoms",
        "kero",
    }
    assert topology["units"]["E-1"]["products"] == ["column feed"]
