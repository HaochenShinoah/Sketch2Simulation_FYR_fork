"""The PFD layout: graph analysis, coordinates, and the COM-side helpers on stubs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sketch2sim import pfd_layout as pl
from sketch2sim.pfd_layout import Unit, compute_layout

REPO = Path(__file__).resolve().parents[1]


def _hda() -> dict[str, Unit]:
    """A small HDA plant (cs4-like) in creation order, with a recycle loop."""
    return {
        "V-101": Unit("V-101", "tankop", ["Toluene"], ["To pump"]),
        "P-101": Unit("P-101", "pumpop", ["To pump"], ["Pumped toluene"]),
        "MIX-1": Unit(
            "MIX-1",
            "mixerop",
            ["Pumped toluene", "Hydrogen", "Recycle gas"],
            ["Mixed feed"],
        ),
        "E-101": Unit("E-101", "heaterop", ["Mixed feed"], ["Preheated feed"]),
        "H-101": Unit(
            "H-101",
            "dynamicfiredheaterop",
            ["Preheated feed", "Fuel gas"],
            ["Hot feed", "Flue gas"],
        ),
        "R-101": Unit("R-101", "conversionreactorop", ["Hot feed"], ["Effluent"]),
        "E-102": Unit("E-102", "coolerop", ["Effluent"], ["Cooled effluent"]),
        "V-102": Unit("V-102", "flashtank", ["Cooled effluent"], ["Vapour", "Liquid"]),
        "TEE-1": Unit("TEE-1", "teeop", ["Vapour"], ["Recycle gas", "Purge"]),
        "T-101": Unit("T-101", "distillation", ["Liquid"], ["Benzene", "Bottoms"]),
    }


def _overlaps(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def test_layout_of_the_hda_plant():
    units = _hda()
    layout = compute_layout(
        units, ["Toluene", "Purge", "Flue gas", "Hydrogen", "Fuel gas"]
    )
    pos = layout.positions
    # the main process order goes left to right, one column per unit of the chain
    chain = ["V-101", "P-101", "MIX-1", "E-101", "H-101", "R-101", "E-102", "V-102"]
    assert [layout.layers[u] for u in chain] == list(range(len(chain)))
    assert layout.main_path[:8] == chain
    # the recycle gas is recognised by its name and drawn on a lane above
    assert layout.recycles == ["Recycle gas"]
    top_of_units = min(pos[u][1] for u in units)
    assert pos["Recycle gas"][1] < top_of_units
    # the branch (tee -> recycle, purge) and the column share the last columns
    assert layout.layers["TEE-1"] == layout.layers["T-101"] == len(chain)
    assert layout.rows["TEE-1"] != layout.rows["T-101"]
    # stream icons sit between their units, feeds on the left, products on the right
    for stream, (a, b) in pl.build_edges(units).items():
        if stream in layout.recycles:
            continue
        if a and b:
            assert pos[a][0] + units[a].width < pos[stream][0] < pos[b][0], stream
        elif a:
            assert pos[stream][0] > pos[a][0] + units[a].width, stream
        elif b:
            assert pos[stream][0] + pl.STREAM_ICON[0] < pos[b][0], stream
    # several feeds of one unit are stacked, not on top of each other
    assert pos["Hydrogen"] != pos["Pumped toluene"]
    # no two units overlap
    boxes = {u: (*pos[u], units[u].width, units[u].height) for u in units}
    for a in boxes:
        for b in boxes:
            assert a == b or not _overlaps(boxes[a], boxes[b]), (a, b)
    # deterministic
    again = compute_layout(
        _hda(), ["Toluene", "Purge", "Flue gas", "Hydrogen", "Fuel gas"]
    )
    assert again.positions == pos
    assert layout.notes == []


def test_unnamed_recycle_is_found_by_the_cycle_and_a_forward_recycle_name_is_not():
    units = {
        "A": Unit("A", "pumpop", ["Feed", "Back"], ["A out"]),
        "B": Unit("B", "heaterop", ["A out"], ["B out"]),
        "C": Unit("C", "flashtank", ["B out"], ["Back", "Product"]),
        "D": Unit("D", "coolerop", ["Recycle gas feed"], ["D out"]),
    }
    units["C"].products.append("Recycle gas feed")  # goes forward to D despite its name
    layout = compute_layout(units, ["Feed", "Product", "D out"])
    assert layout.recycles == ["Back"]
    assert layout.layers["D"] > layout.layers["C"]
    assert layout.layers["A"] < layout.layers["B"] < layout.layers["C"]


def _icon_positions(layout, units):
    return [p for name, p in layout.positions.items() if name not in units]


def test_no_two_stream_icons_share_a_position_and_stubs_clear_internal_icons():
    # two pumps feeding one mixer: both internal icons on the mixer's left
    units = {
        "P1": Unit("P1", "pumpop", ["Crude"], ["Crude out"]),
        "P2": Unit("P2", "pumpop", ["Water"], ["Water out"]),
        "M": Unit("M", "mixerop", ["Crude out", "Water out", "Chemical"], ["Mixed"]),
        "H": Unit("H", "dynamicfiredheaterop", ["Mixed", "Fuel"], ["Hot", "Flue"]),
        "R": Unit("R", "conversionreactorop", ["Hot"], ["Effluent"]),
    }
    layout = compute_layout(
        units, ["Crude", "Water", "Chemical", "Fuel", "Flue", "Effluent"]
    )
    icons = _icon_positions(layout, units)
    assert len(set(icons)) == len(icons)
    # the external product of H (Flue) does not sit on its internal product (Hot)
    assert layout.positions["Flue"][1] != layout.positions["Hot"][1]
    # the mixer's external feed (Chemical) comes after its two internal feeds
    ys = {s: layout.positions[s][1] for s in ("Crude out", "Water out", "Chemical")}
    assert len(set(ys.values())) == 3


def test_loop_is_cut_at_the_named_return_line_not_at_the_circulation():
    """cs2: settler -> circulation pump -> reactor; the pump stays downstream of
    the settler and its 'Caustic recycle' return is the recycle."""
    units = {
        "Prewash": Unit("Prewash", "flashtank", ["Jet fuel"], ["Prewashed"]),
        "Reactor": Unit(
            "Reactor",
            "conversionreactorop",
            ["Prewashed", "Caustic recycle"],
            ["Effluent"],
        ),
        "Settler": Unit(
            "Settler", "flashtank", ["Effluent"], ["Hydrocarbon", "Caustic circulation"]
        ),
        "Pump": Unit("Pump", "pumpop", ["Caustic circulation"], ["Caustic recycle"]),
        "Wash": Unit("Wash", "flashtank", ["Hydrocarbon"], ["Washed"]),
    }
    layout = compute_layout(units, ["Jet fuel", "Washed"])
    assert layout.recycles == ["Caustic recycle"]
    assert layout.layers["Settler"] < layout.layers["Pump"]
    # a reboiler drawn as a unit: the boil-up return is the recycle
    column = {
        "T": Unit(
            "T",
            "distillation",
            ["Feed", "T boil-up return"],
            ["Overhead", "To reboiler"],
        ),
        "E": Unit("E", "heaterop", ["To reboiler"], ["T boil-up return", "Bottoms"]),
    }
    layout = compute_layout(column, ["Feed", "Overhead", "Bottoms"])
    assert layout.recycles == ["T boil-up return"] and layout.layers["E"] == 1
    # fuel gas from a downstream separator back to the burner mixer (which
    # also takes air from outside): the fuel gas line is the recycle, the
    # burner mixer stays next to its heater
    hda = _hda()
    hda["TEE-1"].products = ["Recycle gas", "Fuel gas"]
    hda["BMIX"] = Unit("BMIX", "mixerop", ["Fuel gas", "Air"], ["Burner feed"])
    hda["H-101"].feeds = ["Preheated feed", "Burner feed"]
    layout = compute_layout(hda, ["Toluene", "Hydrogen", "Air", "Flue gas"])
    assert set(layout.recycles) == {"Recycle gas", "Fuel gas"}
    assert layout.layers["BMIX"] == layout.layers["H-101"] - 1


def test_energy_stream_listed_as_feed_and_product_hangs_below_its_unit():
    units = {
        "R": Unit("R", "conversionreactorop", ["In", "E-R"], ["Out", "E-R"]),
        "C": Unit("C", "coolerop", ["Out"], ["Cold"], energy=["E-C"]),
    }
    layout = compute_layout(units, ["In", "Cold"])
    assert layout.layers == {"R": 0, "C": 1} and layout.notes == []
    for duty, owner in (("E-R", "R"), ("E-C", "C")):
        x, y = layout.positions[duty]
        assert y < layout.positions[owner][1]  # below (y is up)
        assert (
            layout.positions[owner][0]
            <= x
            <= layout.positions[owner][0] + units[owner].width
        )


def test_dangling_stream_and_single_unit():
    layout = compute_layout(
        {"P": Unit("P", "pumpop", ["In"], ["Out"])}, ["In", "Out", "Loose"]
    )
    assert set(layout.positions) == {"P", "In", "Out"}
    assert layout.notes == ["Loose: attached to no unit, left where HYSYS put it"]
    assert compute_layout({}, []).positions == {}


def test_layout_on_the_recorded_topologies():
    """Every stored topology lays out without error, units in distinct boxes."""
    for path in sorted((REPO / "logs").glob("cs*_v11/step9_topology.json")):
        report = json.loads(path.read_text(encoding="utf-8"))["units"]
        units = {
            entry["name"]: Unit(entry["name"], "", entry["feeds"], entry["products"])
            for entry in report.values()
        }
        streams = sorted({s for u in units.values() for s in u.feeds + u.products})
        layout = compute_layout(units, streams)
        boxes = [(*layout.positions[u], units[u].width, units[u].height) for u in units]
        for i, a in enumerate(boxes):
            for b in boxes[i + 1 :]:
                assert not _overlaps(a, b), path
        assert set(streams) <= set(layout.positions)
        icons = _icon_positions(layout, units)
        assert len(set(icons)) == len(icons), path


# --- COM-side helpers on stubs -----------------------------------------------
class _Item:
    def __init__(self, pfd, name, x, y, width=56.0, height=56.0, kind=-2):
        self.__dict__.update(
            _pfd=pfd,
            name=name,
            XPosition=x,
            YPosition=y,
            Width=width,
            Height=height,
            Type=kind,
            Hidden=False,
        )

    def __setattr__(self, key, value):
        if key == "XPosition" and not self._pfd.allowed(self.name, float(value)):
            self._pfd.refusals.append(self.name)
            return
        self.__dict__[key] = value


class _Label:
    """A label item: no name, no owner property, follows its owner."""

    def __init__(self, owner):
        self.owner, self.name, self.Type, self.Hidden = owner, "", -4, False

    @property
    def XPosition(self):
        return self.owner.XPosition

    @property
    def YPosition(self):
        return self.owner.YPosition + 40


class _Items:
    def __init__(self, items):
        self._items = items

    @property
    def Names(self):
        return [it.name for it in self._items]

    @property
    def Count(self):
        return len(self._items)

    def Item(self, key):
        if isinstance(key, int):
            return self._items[key]
        return next(it for it in self._items if it.name == key)


class _Pfd:
    """HYSYS's rule: a unit never left of its feed icons nor right of its products."""

    def __init__(self, units: dict[str, Unit]):
        self.units = units
        self.edges = pl.build_edges(units)
        self.items = {}
        self.refusals = []
        self.centred = 0
        # HYSYS places objects as they are created: units in a row, every
        # stream icon next to its units (so the rule holds from the start)
        for index, (name, unit) in enumerate(units.items()):
            self.items[name] = _Item(
                self, name, 100.0 * index, 0.0, unit.width, unit.height
            )
        for stream, (a, b) in self.edges.items():
            if a and b:
                x = (self.items[a].XPosition + self.items[b].XPosition) / 2
            elif a:
                x = self.items[a].XPosition + 50.0
            else:
                x = self.items[b].XPosition - 50.0
            self.items[stream] = _Item(self, stream, x, 0.0, 34.0, 12.0)
        self.labels = [_Label(it) for it in self.items.values()]

    def _forward(self, stream):
        a, b = self.edges.get(stream, (None, None))
        return not (a and b and self.items[a].XPosition > self.items[b].XPosition)

    def allowed(self, name, x):
        """Feed icon < unit < product icon, for streams that run forward."""
        if name in self.units:
            for stream, (a, b) in self.edges.items():
                if not self._forward(stream):
                    continue
                if b == name and self.items[stream].XPosition >= x:
                    return False
                if a == name and self.items[stream].XPosition <= x:
                    return False
            return True
        if not self._forward(name):
            return False  # a recycle icon can satisfy neither side: refused
        a, b = self.edges.get(name, (None, None))
        if a and self.items[a].XPosition >= x:
            return False
        return not (b and self.items[b].XPosition <= x)

    def GetItems(self, kind, visibility):
        if kind == pl.PFD_OPERATION:
            return _Items(list(self.items.values()))
        if kind == pl.PFD_LABEL:
            return _Items(self.labels)
        return _Items([])

    def Centre(self):
        self.centred += 1


def test_apply_layout_converges_under_the_ordering_rule():
    units = _hda()
    layout = compute_layout(
        units, ["Toluene", "Purge", "Flue gas", "Hydrogen", "Fuel gas"]
    )
    pfd = _Pfd(units)
    pumps = []
    report = pl.apply_layout(pfd, layout.positions, pump=pumps.append)
    assert report["missing"] == []
    # everything but the recycle icon (no x satisfies the rule for it) lands exactly
    assert set(report["refused"]) <= {"Recycle gas"}
    for name, (x, y) in layout.positions.items():
        if name in report["refused"]:
            continue
        assert (pfd.items[name].XPosition, pfd.items[name].YPosition) == (x, y), name
    assert pfd.refusals  # some first-pass moves were refused and retried later
    assert pfd.centred == 1 and pumps
    # a second application changes nothing
    again = pl.apply_layout(pfd, layout.positions)
    assert again["moved"] == [] or set(again["moved"]) <= {"Recycle gas"}
    assert set(again) == {"moved", "refused", "adjusted", "final", "missing"}
    assert again["final"]["E-101"] == layout.positions["E-101"]


def test_render_preview_writes_a_png(tmp_path):
    units = _hda()
    layout = compute_layout(
        units, ["Toluene", "Purge", "Flue gas", "Hydrogen", "Fuel gas"]
    )
    path = tmp_path / "preview.png"
    assert pl.render_preview(
        units, layout.positions, str(path), recycles=layout.recycles
    )
    assert path.stat().st_size > 1000
    assert pl.render_preview(units, {}, str(tmp_path / "empty.png")) is False


def test_hide_labels_finds_the_owner_by_nudging():
    units = {"P": Unit("P", "pumpop", ["In"], ["Out"])}
    pfd = _Pfd(units)
    hidden = pl.hide_labels(pfd, ["Out", "Nope"])
    assert hidden == ["Out"]
    assert [lab.Hidden for lab in pfd.labels] == [False, False, True]
    assert pfd.items["Out"].YPosition == 0.0  # nudged back


class _Stream:
    def __init__(self, name, **values):
        self.name = name
        self.__dict__.update(values)


class _Op:
    def __init__(self, name, type_name, feeds, products):
        self.name, self.TypeName = name, type_name
        self.AttachedFeeds = _Items([_Stream(s) for s in feeds])
        self.AttachedProducts = _Items([_Stream(s) for s in products])
        self.EnergyStream = _Stream("Q-1")


class _Coll(_Items):
    pass


class _Flowsheet:
    def __init__(self, ops, streams):
        self.Operations = _Coll(ops)
        self.MaterialStreams = _Coll(streams)
        self.EnergyStreams = _Coll([_Stream("Q-1")])
        self.FluidPackage = type(
            "FP",
            (),
            {
                "PropertyPackageName": "PengRob",
                "Components": _Coll([_Stream("Methane")]),
            },
        )()


class _Case:
    def __init__(self, ops, streams):
        self.Flowsheet = _Flowsheet(ops, streams)


def test_read_units_and_case_signature_differences():
    # HYSYS lists the energy stream Q-1 among the attachments: it is stripped
    ops = [
        _Op("P", "pumpop", ["In", "Q-1"], ["Out", "Q-1"]),
        _Op("E", "heaterop", ["Out"], ["Hot"]),
    ]
    streams = [
        _Stream(
            "In",
            TemperatureValue=25.0,
            PressureValue=101.3,
            ComponentMolarFractionValue=(1.0,),
        ),
        _Stream("Out", TemperatureValue=-32767.0),
        _Stream("Hot", TemperatureValue=80.0),
    ]
    case = _Case(ops, streams)
    units, names = pl.read_units(case.Flowsheet)
    assert list(units) == ["P", "E"] and names == ["In", "Out", "Hot"]
    assert units["P"].feeds == ["In"] and units["E"].products == ["Hot"]
    assert units["P"].products == ["Out"] and units["P"].energy == ["Q-1"]
    assert (units["P"].width, units["P"].height) == (56, 56)
    before = pl.case_signature(case)
    assert before["operations"]["P"] == {
        "type": "pumpop",
        "feeds": ["In", "Q-1"],  # the signature keeps what HYSYS reports
        "products": ["Out", "Q-1"],
        "energy": "Q-1",
    }
    assert before["streams"]["In"]["ComponentMolarFractionValue"] == [1.0]
    assert pl.signature_differences(before, pl.case_signature(case)) == []
    # a change of the simulation shows up, a change of positions would not
    streams[2].TemperatureValue = 90.0
    ops[1].AttachedProducts = _Items([])
    diffs = pl.signature_differences(before, pl.case_signature(case))
    assert any("stream 'Hot'" in d for d in diffs) and any(
        "operation 'E'" in d for d in diffs
    )


def test_layout_case_on_the_stub_reports_and_skips_without_pfd():
    class Shown:
        Count = 1

        def __init__(self, pfd):
            self.ActivePFD = pfd

    units = _hda()
    pfd = _Pfd(units)
    case = _Case(
        [_Op(u.name, u.type_name, u.feeds, u.products) for u in units.values()],
        [_Stream(s) for s in pfd.edges],
    )
    case.Flowsheet.PFDs = Shown(pfd)
    messages = []
    report = pl.layout_case(case, log=messages.append)
    assert report and report["main_path"][0] == "V-101" and "positions" in report
    assert any(m.startswith("[pfd] layout: 10 units") for m in messages)

    class NotShown:
        Count = 0

    case.Flowsheet.PFDs = NotShown()
    case.Visible = False
    case.Activate = lambda: None
    assert pl.layout_case(case, log=messages.append) is None
    assert messages[-1].startswith("[pfd] no PFD view")


@pytest.mark.parametrize("name,width", [("Feed", 70.0), ("", 10.0)])
def test_default_label_width(name, width):
    assert pl.default_label_width(name) == width
