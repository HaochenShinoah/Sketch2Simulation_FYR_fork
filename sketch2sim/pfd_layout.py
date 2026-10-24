"""Deterministic layout of the HYSYS PFD built by the generated script.

Pure functions compute target positions from the attachment graph (units with
their attached feed and product stream names); duck-typed helpers apply the
positions through the PFD COM objects (``flowsheet.PFDs.ActivePFD``) and read
the topology back from a case, so that the standalone tool
(``tools/hysys_layout_pfd.py``) and the generated script share one
implementation and the offline suite can test it with stubs. No win32com
import here.

Coordinate system (probed on HYSYS V11, 2026-10-24): PFD units, X to the
right, Y UP (a larger y is higher on the screen), ``XPosition``/``YPosition``
is the lower-left corner of the icon, a stream icon is 34 x 12. Rows with a
negative index and the recycle lanes therefore come out below the main row,
energy streams hang below their unit. HYSYS refuses a move that puts a unit left of its
feed icons or right of its product icons (and a stream icon across its unit);
the target layout respects that order and :func:`apply_layout` repeats its
passes until nothing moves any more. Labels follow their owner; lines are
routed by HYSYS.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

STREAM_ICON = (34.0, 12.0)
DEFAULT_ICON = (64.0, 64.0)
# Icon sizes by HYSYS TypeName (probed); used when the PFD item cannot be read.
ICON_SIZES: dict[str, tuple[float, float]] = {
    "pumpop": (56, 56),
    "compressor": (72, 56),
    "expandop": (72, 56),
    "valveop": (56, 56),
    "mixerop": (48, 76),
    "teeop": (48, 76),
    "heaterop": (64, 56),
    "coolerop": (64, 56),
    "heatexop": (64, 56),
    "aircoolerop": (64, 56),
    "dynamicfiredheaterop": (96, 96),
    "flashtank": (56, 144),
    "sep3op": (56, 144),
    "tankop": (56, 144),
    "conversionreactorop": (56, 144),
    "equilibriumreactorop": (56, 144),
    "gibbsreactorop": (56, 144),
    "kineticreactorop": (56, 144),
    "pfreactorop": (96, 56),
    "distillation": (138, 228),
    "absorber": (138, 228),
    "reboiledabsorber": (138, 228),
    "refluxedabsorber": (138, 228),
    "threephase": (138, 228),
    "liqliqextractor": (138, 228),
    "fractop": (64, 96),
    "recycle": (56, 56),
    "pipeseg": (64, 56),
}
RECYCLE_NAME_RE = re.compile(
    r"\b(recycle|recycled|return|reflux|pumparound|circulat\w*)\b", re.IGNORECASE
)
CHAR_WIDTH = 15.0  # PFD units per label character (probed: "Feed" 61, "CompOut" 115)
MIN_GAP = 160.0  # between the right edge of a layer and the left edge of the next
ROW_GAP = 70.0  # between the icons of two rows
STUB_GAP = 90.0  # a feed icon this far left of its unit, a product icon right of it
STACK_PITCH = 40.0  # vertical pitch of several feeds / products of one unit
LANE_PITCH = 40.0  # vertical pitch of the recycle lanes above the drawing

# PFD item type codes (PFDItemType_enum) and visibility (pfdVisibleandHidden)
PFD_OPERATION, PFD_STREAM_LINE, PFD_LABEL, PFD_ALL = -2, -3, -4, -1
PFD_VISIBLE_AND_HIDDEN = 2


@dataclass
class Unit:
    """A unit operation as read back from the case."""

    name: str
    type_name: str = ""
    feeds: list[str] = field(default_factory=list)  # attached stream names
    products: list[str] = field(default_factory=list)
    energy: list[str] = field(default_factory=list)  # attached energy streams
    width: float = DEFAULT_ICON[0]
    height: float = DEFAULT_ICON[1]


@dataclass
class Layout:
    positions: dict[str, tuple[float, float]]  # item name -> (x, y) top-left
    layers: dict[str, int]
    rows: dict[str, int]
    recycles: list[str]  # stream names drawn on the recycle lanes
    main_path: list[str]
    notes: list[str] = field(default_factory=list)


def icon_size(type_name: str) -> tuple[float, float]:
    return ICON_SIZES.get((type_name or "").lower(), DEFAULT_ICON)


def default_label_width(name: str) -> float:
    return CHAR_WIDTH * len(name) + 10.0


def build_edges(units: dict[str, Unit]) -> dict[str, tuple[str | None, str | None]]:
    """stream name -> (source unit, destination unit), either may be None."""
    sources: dict[str, str] = {}
    destinations: dict[str, str] = {}
    for key, unit in units.items():
        for stream in unit.products:
            sources.setdefault(stream, key)
        for stream in unit.feeds:
            destinations.setdefault(stream, key)
    edges: dict[str, tuple[str | None, str | None]] = {}
    for key, unit in units.items():
        for stream in unit.feeds + unit.products:
            edges.setdefault(stream, (sources.get(stream), destinations.get(stream)))
    return edges


# ---------------------------------------------------------------------------
# graph helpers (all deterministic: ties broken by creation order)
# ---------------------------------------------------------------------------
def _adjacency(
    units: dict[str, Unit],
    edges: dict[str, tuple[str | None, str | None]],
    exclude: set[str],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    order = {key: index for index, key in enumerate(units)}
    succ: dict[str, list[str]] = {key: [] for key in units}
    pred: dict[str, list[str]] = {key: [] for key in units}
    for stream, (a, b) in edges.items():
        if stream in exclude or not a or not b or a == b or a not in units:
            continue
        if b not in units:
            continue
        if b not in succ[a]:
            succ[a].append(b)
        if a not in pred[b]:
            pred[b].append(a)
    for key in units:
        succ[key].sort(key=order.__getitem__)
        pred[key].sort(key=order.__getitem__)
    return succ, pred


def _back_edges(
    units: dict[str, Unit],
    edges: dict[str, tuple[str | None, str | None]],
    exclude: set[str],
) -> set[str]:
    """Streams that close a cycle (depth-first search in creation order)."""
    succ, _ = _adjacency(units, edges, exclude)
    colour = dict.fromkeys(units, 0)  # 0 new, 1 on the stack, 2 done
    back_pairs: set[tuple[str, str]] = set()
    for start in units:
        if colour[start]:
            continue
        stack = [(start, iter(succ[start]))]
        colour[start] = 1
        while stack:
            node, children = stack[-1]
            for child in children:
                if colour[child] == 1:
                    back_pairs.add((node, child))
                elif colour[child] == 0:
                    colour[child] = 1
                    stack.append((child, iter(succ[child])))
                    break
            else:
                colour[node] = 2
                stack.pop()
    return {
        stream
        for stream, (a, b) in edges.items()
        if stream not in exclude and a and b and (a, b) in back_pairs
    }


def _cuts(
    units: dict[str, Unit], edges: dict[str, tuple[str | None, str | None]]
) -> set[str]:
    """Streams to draw as recycles: one per loop.

    A depth-first search rooted at the units fed from outside the drawing
    finds every edge that returns to a unit on the current path. The loop is
    cut at that edge when it is recycle-named, or when no edge of the loop
    is; otherwise at the last recycle-named edge of the loop. So a caustic
    circulation pump or a reboiler stays downstream of the unit it serves,
    and its return line is the recycle.
    """
    order = {key: index for index, key in enumerate(units)}
    stream_order = {stream: index for index, stream in enumerate(edges)}
    out: dict[str, list[tuple[str, str]]] = {key: [] for key in units}
    for stream, (a, b) in edges.items():
        if a and b and a != b and a in units and b in units:
            out[a].append((b, stream))
    for key in units:
        out[key].sort(key=lambda item: (order[item[0]], stream_order[item[1]]))
    external = [b for (a, b) in edges.values() if a is None and b in units]
    external_fed = set(external)
    roots = list(dict.fromkeys(external + list(units)))
    colour = dict.fromkeys(units, 0)  # 0 new, 1 on the path, 2 done
    cuts: set[str] = set()
    for root in roots:
        if colour[root]:
            continue
        colour[root] = 1
        # (unit, its remaining out-edges, the stream that led to it)
        stack: list[tuple[str, Any, str | None]] = [(root, iter(out[root]), None)]
        while stack:
            node, children, _ = stack[-1]
            for child, stream in children:
                if colour[child] == 1:  # back to a unit on the path: a loop
                    start = next(
                        i for i, entry in enumerate(stack) if entry[0] == child
                    )
                    loop = [entry[2] for entry in stack[start + 1 :]] + [stream]
                    named = [s for s in loop if s and RECYCLE_NAME_RE.search(s)]
                    # an edge into a unit that also has a feed from outside (a
                    # mixer with fresh feed, a burner with air) starts the loop
                    # there: the fuel gas back to the burner is the recycle
                    into_fed = [s for s in loop if s and edges[s][1] in external_fed]
                    if RECYCLE_NAME_RE.search(stream):
                        cuts.add(stream)
                    elif named:
                        cuts.add(named[-1])
                    elif into_fed:
                        cuts.add(into_fed[-1])
                    else:
                        cuts.add(stream)
                elif colour[child] == 0:
                    colour[child] = 1
                    stack.append((child, iter(out[child]), stream))
                    break
            else:
                colour[node] = 2
                stack.pop()
    return cuts


def _topological(
    units: dict[str, Unit], succ: dict[str, list[str]], pred: dict[str, list[str]]
) -> list[str]:
    order = {key: index for index, key in enumerate(units)}
    remaining = {key: len(pred[key]) for key in units}
    ready = sorted((key for key in units if remaining[key] == 0), key=order.__getitem__)
    result: list[str] = []
    while ready:
        node = ready.pop(0)
        result.append(node)
        for child in succ[node]:
            remaining[child] -= 1
            if remaining[child] == 0:
                ready.append(child)
                ready.sort(key=order.__getitem__)
    for key in units:  # a cycle that survived (should not happen): append
        if key not in result:
            result.append(key)
    return result


def _layers(
    units: dict[str, Unit],
    edges: dict[str, tuple[str | None, str | None]],
    exclude: set[str],
) -> dict[str, int]:
    """Longest-path layering, then every unit pulled next to its nearest successor."""
    succ, pred = _adjacency(units, edges, exclude)
    topo = _topological(units, succ, pred)
    layer = dict.fromkeys(units, 0)
    for node in topo:
        if pred[node]:
            layer[node] = max(layer[p] for p in pred[node]) + 1
    for node in reversed(topo):
        if succ[node]:
            layer[node] = max(layer[node], min(layer[s] for s in succ[node]) - 1)
    return layer


def _main_path(
    units: dict[str, Unit],
    edges: dict[str, tuple[str | None, str | None]],
    exclude: set[str],
) -> list[str]:
    """The longest chain of units (by count) from a feed to a product."""
    succ, pred = _adjacency(units, edges, exclude)
    topo = _topological(units, succ, pred)
    best = dict.fromkeys(units, 1)
    parent: dict[str, str | None] = dict.fromkeys(units, None)
    for node in topo:
        for p in pred[node]:
            if best[p] + 1 > best[node]:
                best[node], parent[node] = best[p] + 1, p
    if not units:
        return []
    order = {key: index for index, key in enumerate(units)}
    end = max(units, key=lambda key: (best[key], -order[key]))
    path = [end]
    while parent[path[-1]] is not None:
        path.append(parent[path[-1]])  # type: ignore[arg-type]
    return list(reversed(path))


def _rows(
    units: dict[str, Unit],
    edges: dict[str, tuple[str | None, str | None]],
    exclude: set[str],
    layers: dict[str, int],
    main_path: list[str],
) -> dict[str, int]:
    """Row of every unit: the main path on row 0, the rest by barycenter sweeps."""
    succ, pred = _adjacency(units, edges, exclude)
    order = {key: index for index, key in enumerate(units)}
    main = set(main_path)
    by_layer: dict[int, list[str]] = {}
    for key in units:
        by_layer.setdefault(layers[key], []).append(key)
    rows: dict[str, float] = {}
    for nodes in by_layer.values():
        k = 0
        for node in nodes:
            if node in main:
                rows[node] = 0.0
                continue
            k += 1
            rows[node] = float((k + 1) // 2 * (1 if k % 2 else -1))
    for sweep in range(4):
        forward = sweep % 2 == 0
        for layer in sorted(by_layer, reverse=not forward):
            nodes = by_layer[layer]
            target: dict[str, float] = {}
            for node in nodes:
                if node in main:
                    target[node] = 0.0
                    continue
                neighbours = (pred if forward else succ)[node] or (
                    succ if forward else pred
                )[node]
                values = [rows[n] for n in neighbours]
                target[node] = sum(values) / len(values) if values else rows[node]
            ordered = sorted(nodes, key=lambda n: (target[n], order[n]))
            if any(n in main for n in ordered):
                pivot = next(i for i, n in enumerate(ordered) if n in main)
                for i, node in enumerate(ordered):
                    rows[node] = float(i - pivot)
            else:
                values = [round(target[n]) for n in ordered]
                for i in range(1, len(values)):
                    values[i] = max(values[i], values[i - 1] + 1)
                for node, value in zip(ordered, values, strict=True):
                    rows[node] = float(value)
    return {key: int(rows[key]) for key in units}


# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------
def compute_layout(
    units: dict[str, Unit],
    stream_names: Iterable[str] = (),
    label_width: Callable[[str], float] | None = None,
    min_gap: float = MIN_GAP,
    row_gap: float = ROW_GAP,
) -> Layout:
    """Target top-left positions for every unit and stream icon.

    ``units`` is keyed by the HYSYS unit name in creation order; ``stream_names``
    lists the material streams (streams attached to nothing get no position).
    """
    label_width = label_width or default_label_width
    units = {key: unit for key, unit in units.items() if key}
    edges = build_edges(units)
    for stream in stream_names:
        edges.setdefault(stream, (None, None))
    notes: list[str] = []

    # 1. cycle breaking: every loop is cut at its recycle stream (see _cuts)
    recycles = _cuts(units, edges)
    recycles |= _back_edges(units, edges, exclude=recycles)  # anything left
    layers = _layers(units, edges, exclude=recycles)
    main_path = _main_path(units, edges, exclude=recycles)
    rows = _rows(units, edges, recycles, layers, main_path)

    # 2. coordinates of layers (x) and rows (y)
    by_layer: dict[int, list[str]] = {}
    for key in units:
        by_layer.setdefault(layers[key], []).append(key)
    layer_width = {
        layer: max(units[key].width for key in nodes)
        for layer, nodes in by_layer.items()
    }
    layer_x: dict[int, float] = {}
    x = 0.0
    for layer in sorted(by_layer):
        layer_x[layer] = x
        labels = [
            label_width(stream)
            for stream, (a, b) in edges.items()
            if a in by_layer[layer] and b and stream not in recycles
        ]
        # The label of a stream icon sits in the gap, between the labels of the
        # units on either side (HYSYS writes every label under its icon on the
        # same baseline): the gap holds the longest stream label and half of
        # the longest neighbouring unit label.
        neighbours = by_layer[layer] + by_layer.get(layer + 1, [])
        unit_labels = [label_width(units[key].name) for key in neighbours]
        gap = max(
            min_gap,
            0.9 * max(labels, default=0.0) + 0.5 * max(unit_labels, default=0.0) + 40.0,
        )
        x += layer_width[layer] + gap
    row_height: dict[int, float] = {}
    for key in units:
        row_height[rows[key]] = max(row_height.get(rows[key], 0.0), units[key].height)
    row_centre: dict[int, float] = {}
    present = sorted(row_height)
    if present:
        pivot = 0 if 0 in row_height else present[0]
        row_centre[pivot] = 0.0
        for row in [r for r in present if r > pivot]:
            previous = max(r for r in present if r < row)
            row_centre[row] = (
                row_centre[previous]
                + row_height[previous] / 2
                + row_height[row] / 2
                + row_gap
            )
        for row in sorted((r for r in present if r < pivot), reverse=True):
            following = min(r for r in present if r > row)
            row_centre[row] = (
                row_centre[following]
                - row_height[following] / 2
                - row_height[row] / 2
                - row_gap
            )

    positions: dict[str, tuple[float, float]] = {}
    centre: dict[str, tuple[float, float]] = {}
    for key, unit in units.items():
        layer, row = layers[key], rows[key]
        ux = layer_x[layer] + (layer_width[layer] - unit.width) / 2
        uy = row_centre[row] - unit.height / 2
        positions[key] = (round(ux, 1), round(uy, 1))
        centre[key] = (ux + unit.width / 2, row_centre[row])

    # 3. stream icons. Every side of a unit hands out slots (0, -1, +1, -2, ...
    #    rows of STACK_PITCH around the unit centre): the internal feeds of a
    #    unit take the first slots on its left, external feeds the next ones;
    #    external products skip as many slots as the unit has internal
    #    products, whose icons sit at the destination. So no two icons share
    #    a position. Recycles go to their own lanes below the drawing (y is
    #    up); a stream that leaves and re-enters the same unit is a duty.
    def slot_offset(k: int) -> float:
        return STACK_PITCH * ((k + 1) // 2) * (1 if k % 2 else -1)

    bottom = min((row_centre[r] - row_height[r] / 2 for r in present), default=0.0)
    lane_index = 0
    sw, sh = STREAM_ICON
    feed_slot: dict[str, int] = {}
    product_slot: dict[str, int] = {}
    loops: dict[str, list[str]] = {}
    internal = [
        (stream, a, b)
        for stream, (a, b) in edges.items()
        if a and b and a in units and b in units and a != b and stream not in recycles
    ]
    for _stream, a, _b in internal:
        product_slot[a] = product_slot.get(a, 0) + 1
    for stream, a, b in internal:
        k = feed_slot.get(b, 0)
        feed_slot[b] = k + 1
        right_of_a = positions[a][0] + units[a].width
        left_of_b = positions[b][0]
        sx = (right_of_a + left_of_b) / 2
        if left_of_b <= right_of_a:  # same layer (should not happen): beside
            sx = right_of_a + STUB_GAP
        sy = centre[b][1] + slot_offset(k)
        positions[stream] = (round(sx - sw / 2, 1), round(sy - sh / 2, 1))
    for stream, (a, b) in edges.items():
        if stream in positions:
            continue
        if a and b and a == b and a in units:  # a duty listed as feed and product
            loops.setdefault(a, []).append(stream)
        elif a and b and a in units and b in units:  # a recycle: its own lane
            lane_index += 1
            sy = bottom - row_gap - LANE_PITCH * lane_index
            sx = (centre[a][0] + centre[b][0]) / 2
            positions[stream] = (round(sx - sw / 2, 1), round(sy - sh / 2, 1))
        elif a and a in units:  # product leaving the drawing
            k = product_slot.get(a, 0)
            product_slot[a] = k + 1
            sx = positions[a][0] + units[a].width + STUB_GAP
            sy = centre[a][1] + slot_offset(k)
            positions[stream] = (round(sx, 1), round(sy - sh / 2, 1))
        elif b and b in units:  # feed entering the drawing
            k = feed_slot.get(b, 0)
            feed_slot[b] = k + 1
            sx = positions[b][0] - STUB_GAP - sw
            sy = centre[b][1] + slot_offset(k)
            positions[stream] = (round(sx, 1), round(sy - sh / 2, 1))
        else:
            notes.append(f"{stream}: attached to no unit, left where HYSYS put it")
    # energy streams (duties) hang below their unit
    for key, unit in units.items():
        duties = list(dict.fromkeys(unit.energy + loops.get(key, [])))
        for k, energy in enumerate(duties):
            ex = centre[key][0] - sw / 2 + STACK_PITCH * k
            ey = positions[key][1] - 30.0 - sh  # below the unit (y is up)
            positions.setdefault(energy, (round(ex, 1), round(ey, 1)))
    return Layout(
        positions=positions,
        layers=layers,
        rows=rows,
        recycles=sorted(recycles, key=lambda s: list(edges).index(s)),
        main_path=main_path,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# COM side (duck-typed; also exercised with stubs in the tests)
# ---------------------------------------------------------------------------
def _names(collection: Any) -> list[str]:
    try:
        return [str(name) for name in collection.Names]
    except Exception:
        return []


def _object_name(obj: Any) -> str:
    for member in ("name", "Name"):
        try:
            return str(getattr(obj, member))
        except Exception:
            continue
    return ""


def read_units(flowsheet: Any, pfd: Any = None) -> tuple[dict[str, Unit], list[str]]:
    """Units (creation order, with attachments and icon sizes) and stream names."""
    items = None
    if pfd is not None:
        try:
            items = pfd.GetItems(PFD_OPERATION, PFD_VISIBLE_AND_HIDDEN)
        except Exception:
            items = None
    energy_names = set(_names(flowsheet.EnergyStreams))
    units: dict[str, Unit] = {}
    for name in _names(flowsheet.Operations):
        try:
            op = flowsheet.Operations.Item(name)
            type_name = str(op.TypeName)
        except Exception:
            continue
        feeds, products = _names(op.AttachedFeeds), _names(op.AttachedProducts)
        # HYSYS lists the energy stream of a unit among its feeds and products
        # (a reactor: in both). It is a duty, not part of the stream graph.
        energy = [s for s in feeds + products if s in energy_names]
        try:
            member = _object_name(op.EnergyStream)
            if member and member not in energy:
                energy.append(member)
        except Exception:
            pass
        unit = Unit(
            name=name,
            type_name=type_name,
            feeds=[s for s in feeds if s not in energy_names],
            products=[s for s in products if s not in energy_names],
            energy=list(dict.fromkeys(energy)),
        )
        unit.width, unit.height = icon_size(type_name)
        if items is not None:
            try:
                item = items.Item(name)
                unit.width, unit.height = float(item.Width), float(item.Height)
            except Exception:
                pass
        units[name] = unit
    return units, _names(flowsheet.MaterialStreams)


def get_pfd(case: Any, pump: Callable[[float], None] | None = None) -> Any:
    """The active PFD of the case, after making sure the case window exists."""
    flowsheet = case.Flowsheet
    try:
        if flowsheet.PFDs.Count:
            return flowsheet.PFDs.ActivePFD
    except Exception:
        pass
    try:
        case.Visible = True
        case.Activate()
    except Exception:
        pass
    if pump:
        pump(2.0)
    if flowsheet.PFDs.Count:
        return flowsheet.PFDs.ActivePFD
    return None


def apply_layout(
    pfd: Any,
    positions: dict[str, tuple[float, float]],
    pump: Callable[[float], None] | None = None,
    passes: int = 8,
    tolerance: float = 1.0,
) -> dict[str, Any]:
    """Move the PFD items to ``positions`` in repeated passes (see module doc).

    Items are moved rightmost target first on even passes and leftmost first
    on odd passes: HYSYS only accepts a move once the neighbours that bound it
    (feed icons left, product icons right) are out of the way, so a wave from
    the far side frees each item in turn. Returns a report: items moved, items
    HYSYS did not accept (with the position they kept), items without a PFD
    item.
    """
    items = pfd.GetItems(PFD_OPERATION, PFD_VISIBLE_AND_HIDDEN)
    available = set(_names(items))
    missing = [name for name in positions if name not in available]
    moved: set[str] = set()
    attempts: dict[str, int] = {}
    for sweep in range(passes):
        changed = False
        ordered = sorted(
            positions.items(), key=lambda kv: kv[1][0], reverse=sweep % 2 == 0
        )
        for name, (x, y) in ordered:
            if name not in available:
                continue
            item = items.Item(name)
            x_ok = abs(item.XPosition - x) <= tolerance
            y_ok = abs(item.YPosition - y) <= tolerance
            if x_ok and (y_ok or attempts.get(name, 0) >= 1):
                # in place, or HYSYS aligned the icon to its nozzle height
                # after our first attempt: that is its business
                continue
            before = (float(item.XPosition), float(item.YPosition))
            item.XPosition = float(x)
            item.YPosition = float(y)
            attempts[name] = attempts.get(name, 0) + 1
            if pump:
                pump(0.05)
            if (float(item.XPosition), float(item.YPosition)) != before:
                moved.add(name)  # HYSYS accepted at least part of the move
                changed = True
        if pump:
            pump(0.5)
        if not changed:
            break
    refused: dict[str, tuple[float, float]] = {}
    adjusted: dict[str, tuple[float, float]] = {}
    final: dict[str, tuple[float, float]] = {}
    for name, (x, y) in positions.items():
        if name not in available:
            continue
        item = items.Item(name)
        got = (round(float(item.XPosition), 1), round(float(item.YPosition), 1))
        final[name] = got
        if abs(got[0] - x) > tolerance:
            refused[name] = got  # HYSYS did not accept the column (x)
        elif abs(got[1] - y) > tolerance:
            adjusted[name] = got  # HYSYS chose the height (nozzle alignment)
    try:
        pfd.Centre()
    except Exception:
        pass
    return {
        "moved": sorted(moved),
        "refused": refused,
        "adjusted": adjusted,
        "final": final,
        "missing": missing,
    }


def hide_labels(
    pfd: Any, owners: Iterable[str], pump: Callable[[float], None] | None = None
) -> list[str]:
    """Hide the labels of the given items. A label has no owner property: the
    owner is found by nudging it and watching which label moves."""
    items = pfd.GetItems(PFD_OPERATION, PFD_VISIBLE_AND_HIDDEN)
    labels = pfd.GetItems(PFD_LABEL, PFD_VISIBLE_AND_HIDDEN)
    available = set(_names(items))
    hidden: list[str] = []
    for owner in owners:
        if owner not in available:
            continue
        item = items.Item(owner)
        before = [
            (labels.Item(i).XPosition, labels.Item(i).YPosition)
            for i in range(labels.Count)
        ]
        item.YPosition = float(item.YPosition) + 1.0
        if pump:
            pump(0.05)
        for i in range(labels.Count):
            label = labels.Item(i)
            if (label.XPosition, label.YPosition) != before[i]:
                label.Hidden = True
                hidden.append(owner)
                break
        item.YPosition = float(item.YPosition) - 1.0
        if pump:
            pump(0.05)
    return hidden


def layout_case(
    case: Any,
    pump: Callable[[float], None] | None = None,
    hide_helper_labels: bool = False,
    log: Callable[[str], None] = print,
    preview_path: str | None = None,
) -> dict[str, Any] | None:
    """Lay out the PFD of an open case. Returns the report or None when there
    is no PFD. Never raises for layout problems: they are reported. With
    ``preview_path`` a PNG of the final arrangement is drawn (Pillow)."""
    pfd = get_pfd(case, pump)
    if pfd is None:
        log("[pfd] no PFD view (the case window is not shown); layout skipped")
        return None
    units, stream_names = read_units(case.Flowsheet, pfd)
    layout = compute_layout(units, stream_names)
    report = apply_layout(pfd, layout.positions, pump)
    for note in layout.notes:
        log(f"[pfd] NOTE {note}")
    if preview_path:
        try:
            final = {**layout.positions, **report.get("final", {})}
            if render_preview(units, final, preview_path, recycles=layout.recycles):
                log(f"[pfd] preview written to {preview_path}")
        except Exception as error:
            log(f"[pfd] could not draw the preview: {str(error)[:120]}")
    if hide_helper_labels:
        helpers = [
            name
            for name in stream_names
            if re.match(
                r"^(mixer outlet to|burner feed to|.* outlet to tee)", name, re.I
            )
        ]
        report["hidden_labels"] = hide_labels(pfd, helpers, pump)
    report.update(
        {
            "layers": layout.layers,
            "rows": layout.rows,
            "recycles": layout.recycles,
            "main_path": layout.main_path,
            "positions": layout.positions,
        }
    )
    log(
        f"[pfd] layout: {len(units)} units in {len(set(layout.layers.values()))} columns, "
        f"{len(set(layout.rows.values()))} rows; main path {' -> '.join(layout.main_path)}; "
        f"recycles {layout.recycles or 'none'}; moved {len(report['moved'])} items"
    )
    if report.get("adjusted"):
        log(
            f"[pfd] HYSYS aligned {len(report['adjusted'])} icon(s) to their nozzle "
            f"height: {sorted(report['adjusted'])}"
        )
    if report["refused"]:
        log(f"[pfd] NOTE HYSYS kept its own column for: {report['refused']}")
    if report["missing"]:
        log(f"[pfd] NOTE no PFD item for: {report['missing']}")
    return report


def case_signature(case: Any) -> dict[str, Any]:
    """What a simulation *is*, for the invariance check: fluid package and
    components, every operation with its type and attachments, every stream
    with its specified / known values. Positions are not part of it."""
    flowsheet = case.Flowsheet
    signature: dict[str, Any] = {
        "fluid_package": None,
        "components": [],
        "operations": {},
        "streams": {},
    }
    try:
        fp = flowsheet.FluidPackage
        signature["fluid_package"] = str(fp.PropertyPackageName)
        signature["components"] = _names(fp.Components)
    except Exception:
        pass
    for name in _names(flowsheet.Operations):
        try:
            op = flowsheet.Operations.Item(name)
            entry: dict[str, Any] = {
                "type": str(op.TypeName),
                "feeds": sorted(_names(op.AttachedFeeds)),
                "products": sorted(_names(op.AttachedProducts)),
            }
            try:
                entry["energy"] = _object_name(op.EnergyStream)
            except Exception:
                pass
            signature["operations"][name] = entry
        except Exception as error:
            signature["operations"][name] = {"error": str(error)[:80]}
    for name in _names(flowsheet.MaterialStreams):
        values: dict[str, Any] = {}
        try:
            stream = flowsheet.MaterialStreams.Item(name)
        except Exception:
            continue
        for member in (
            "TemperatureValue",
            "PressureValue",
            "MolarFlowValue",
            "MassFlowValue",
            "VapourFractionValue",
            "ComponentMolarFractionValue",
        ):
            try:
                value = getattr(stream, member)
                if isinstance(value, (tuple, list)):
                    value = [round(float(v), 9) for v in value]
                else:
                    value = round(float(value), 9)
                values[member] = value
            except Exception:
                continue
        signature["streams"][name] = values
    signature["energy_streams"] = sorted(_names(flowsheet.EnergyStreams))
    return signature


def signature_differences(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Human-readable differences between two signatures (empty = identical)."""
    diffs: list[str] = []
    for key in ("fluid_package", "components", "energy_streams"):
        if before.get(key) != after.get(key):
            diffs.append(f"{key}: {before.get(key)!r} -> {after.get(key)!r}")
    for section in ("operations", "streams"):
        a, b = before.get(section, {}), after.get(section, {})
        for name in sorted(set(a) | set(b)):
            if name not in a:
                diffs.append(f"{section[:-1]} {name!r} only in the laid-out case")
            elif name not in b:
                diffs.append(f"{section[:-1]} {name!r} only in the original case")
            elif a[name] != b[name]:
                diffs.append(f"{section[:-1]} {name!r}: {a[name]!r} -> {b[name]!r}")
    return diffs


def render_preview(
    units: dict[str, Unit],
    positions: dict[str, tuple[float, float]],
    path: str,
    scale: float = 0.3,
    recycles: Iterable[str] = (),
) -> bool:
    """Draw the arrangement (boxes, stream icons, straight connections) as a
    PNG with Pillow: a quick look at the layout without HYSYS. Returns False
    when Pillow is missing."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return False
    if not positions:
        return False
    sw, sh = STREAM_ICON
    sizes = {name: (units[name].width, units[name].height) for name in units}
    boxes = {
        name: (x, y, x + sizes.get(name, (sw, sh))[0], y + sizes.get(name, (sw, sh))[1])
        for name, (x, y) in positions.items()
    }
    margin = 60.0
    x0 = min(b[0] for b in boxes.values()) - margin
    y0 = min(b[1] for b in boxes.values()) - margin
    x1 = max(b[2] for b in boxes.values()) + margin
    y1 = max(b[3] for b in boxes.values()) + margin

    def px(x: float, y: float) -> tuple[int, int]:
        return int((x - x0) * scale), int((y1 - y) * scale)  # y is up in HYSYS

    image = Image.new(
        "RGB", (max(1, int((x1 - x0) * scale)), max(1, int((y1 - y0) * scale))), "white"
    )
    draw = ImageDraw.Draw(image)
    recycles = set(recycles)
    for stream, (a, b) in build_edges(units).items():
        if stream not in positions:
            continue
        colour = "orange" if stream in recycles else "steelblue"
        mid = px(positions[stream][0] + sw / 2, positions[stream][1] + sh / 2)
        for unit, end in ((a, True), (b, False)):
            if unit in boxes:
                bx = boxes[unit]
                centre = px((bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2)
                draw.line([centre, mid] if end else [mid, centre], fill=colour, width=1)
    for name, (ax, ay, bx, by) in boxes.items():
        top_left, bottom_right = px(ax, by), px(bx, ay)  # y is up: by is the top
        if name in units:
            draw.rectangle([top_left, bottom_right], outline="black", width=2)
            draw.text((top_left[0], bottom_right[1] + 2), name[:28], fill="black")
        else:
            draw.rectangle(
                [top_left, bottom_right], outline="steelblue", fill="lightblue"
            )
            draw.text((top_left[0], top_left[1] - 11), name[:24], fill="dimgray")
    image.save(path)
    return True
