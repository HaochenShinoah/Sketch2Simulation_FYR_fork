import copy
import re
from dataclasses import dataclass
from typing import Any

from sketch2sim.utils.normalization_utils import stream_text

_ALLOWED_CONNECT_POINTS = {"overhead", "bottom", "side", "unknown"}
# Names that identify the two vessels of a column's overhead system when the
# extraction lost the stream that ties them to the condenser.
_REFLUX_DRUM_NAMES = ("reflux drum", "accumulator")
_REFLUX_PUMP_NAMES = ("reflux pump",)  # also matches "reflux pumps"
# Utility streams of a folded condenser or reboiler (cs4 drew the cooling water
# of the condenser E-104): dropped with the unit instead of becoming a column
# product or feed. "water" alone is not matched: the "waste water" of a
# condenser is a real product (water draw). "condensate" counts only on a
# reboiler (steam condensate); on a condenser it may be the distillate.
_UTILITY_RE = re.compile(
    r"\b(cooling water|chilled water|cw|cws|cwr|bfw|boiler feed water|refrigerant"
    r"|coolant|hot oil|brine|utility|steam|steam condensate)\b"
)
_REBOILER_UTILITY_RE = re.compile(r"\bcondensate\b")
# A product stream that leaves the column itself and is named as the reflux
# line (cs3 drew "Reflux" leaving the column) is internal: not a product. The
# whole name must say so; a "reflux drum vent" or a "circulating reflux" draw
# is a real stream.
_REFLUX_WORD_RE = re.compile(r"\breflux\b")
# Words that make a name containing "reflux" a real stream after all: the vent
# of the reflux drum, a circulating-reflux (pumparound) draw, a product.
_NOT_REFLUX_WORDS = frozenset(
    {
        "drum",
        "accumulator",
        "vent",
        "gas",
        "gases",
        "vapour",
        "vapor",
        "purge",
        "product",
        "distillate",
        "circulating",
        "pumparound",
        "pump",
        "cooler",
        "draw",
        "water",
        "naphtha",
    }
)


def _clean_connect_point(value: Any) -> str:
    cleaned = str(value or "").strip().lower()
    return cleaned if cleaned in _ALLOWED_CONNECT_POINTS else "unknown"


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


@dataclass(frozen=True)
class Edge:
    stream_id: str
    from_unit: str
    to_unit: str
    connect_point: str


def _collect_edges(
    extraction: dict[str, Any],
) -> tuple[
    list[Edge], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
]:
    intermediate_streams = extraction.get("intermediate_streams", []) or []
    edges = [
        Edge(
            stream_id=str(stream.get("id")),
            from_unit=str(stream.get("from_unit")),
            to_unit=str(stream.get("to_unit")),
            connect_point=_clean_connect_point(stream.get("connect_point")),
        )
        for stream in intermediate_streams
        if stream.get("id") and stream.get("from_unit") and stream.get("to_unit")
    ]
    return (
        edges,
        extraction.get("feed_streams", []) or [],
        intermediate_streams,
        extraction.get("product_streams", []) or [],
    )


def _outgoing(edges: list[Edge]) -> dict[str, list[Edge]]:
    outgoing_edges: dict[str, list[Edge]] = {}
    for edge in edges:
        outgoing_edges.setdefault(edge.from_unit, []).append(edge)
    return outgoing_edges


def _has_edge(edges: list[Edge], from_unit: str, to_unit: str) -> Edge | None:
    return next(
        (
            edge
            for edge in edges
            if edge.from_unit == from_unit and edge.to_unit == to_unit
        ),
        None,
    )


def _fed_only_from(
    unit_id: str,
    allowed_sources: set[str],
    edges: list[Edge],
    feed_streams: list[dict[str, Any]],
) -> bool:
    """True when every stream entering ``unit_id`` comes from ``allowed_sources``."""
    if any(str(stream.get("to_unit")) == unit_id for stream in feed_streams):
        return False
    return all(
        edge.from_unit in allowed_sources for edge in edges if edge.to_unit == unit_id
    )


def prune_orphaned_units(extraction: dict[str, Any]) -> dict[str, Any]:
    """Remove units that are not referenced by any feed, intermediate, or product stream."""
    connected_unit_ids = set()

    for stream in extraction.get("feed_streams", []) or []:
        connected_unit_ids.add(str(stream.get("to_unit", "")).strip())

    for stream in extraction.get("intermediate_streams", []) or []:
        connected_unit_ids.add(str(stream.get("from_unit", "")).strip())
        connected_unit_ids.add(str(stream.get("to_unit", "")).strip())

    for stream in extraction.get("product_streams", []) or []:
        connected_unit_ids.add(str(stream.get("from_unit", "")).strip())

    original_units = extraction.get("units", []) or []
    extraction["units"] = [
        unit
        for unit in original_units
        if str(unit.get("id", "")).strip() in connected_unit_ids
    ]
    return extraction


def identify_column_candidates(extraction: dict[str, Any]) -> list[str]:
    """Identify distillation-column-like units from their names."""
    units = extraction.get("units", []) or []
    return [
        str(unit.get("id"))
        for unit in units
        if any(
            keyword in _normalize_text(str(unit.get("name", "")))
            for keyword in ["column", "tower", "distillation"]
        )
    ]


def _find_overhead_system(
    column_id: str,
    condenser_id: str,
    edges: list[Edge],
    unit_names: dict[str, str],
    feed_streams: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """
    Identify the reflux drum and reflux pump that belong to ``column_id``.

    The complete chain column -> condenser -> drum -> pump -> column is folded
    as it always was. When the extraction lost an edge of that chain (the cs4
    first run had no condenser -> drum stream), the drum and the pump are still
    folded when their names identify them ("reflux drum", "accumulator",
    "reflux pump(s)") or they return reflux to the column, they are linked to
    this column's overhead system (reflux return, directly or through the
    pump, or a stream from the condenser or the column), and they carry no
    stream from outside that system: a feed stream or a stream from an
    unrelated unit keeps them as real units. A drum that returns reflux to the
    column without a pump is folded as well.

    Returns the folded unit ids (drum before pump, condenser excluded) and the
    ids of the streams that are the evidence for the fold.
    """
    outgoing_edges = _outgoing(edges)

    def name_contains(unit_id: str | None, keywords: tuple[str, ...]) -> bool:
        if not unit_id:
            return False
        normalized_name = _normalize_text(unit_names.get(unit_id, ""))
        return any(keyword in normalized_name for keyword in keywords)

    def returns_to_column(unit_id: str | None) -> Edge | None:
        return _has_edge(edges, unit_id, column_id) if unit_id else None

    def pumps_fed_by(unit_id: str) -> list[Edge]:
        return [
            edge
            for edge in outgoing_edges.get(unit_id, [])
            if edge.to_unit not in (column_id, condenser_id)
            and name_contains(edge.to_unit, ("pump",))
        ]

    # The drum: downstream of the condenser when that edge exists, otherwise a
    # vessel named as a reflux drum that is linked to this column.
    drum_id: str | None = None
    drum_edge: Edge | None = None  # condenser -> drum
    for edge in outgoing_edges.get(condenser_id, []):
        if edge.to_unit != column_id and name_contains(
            edge.to_unit, ("drum", "reflux", "accumulator")
        ):
            drum_id, drum_edge = edge.to_unit, edge
            break
    if drum_id is None:
        for unit_id in unit_names:
            if unit_id in (column_id, condenser_id):
                continue
            if not name_contains(unit_id, _REFLUX_DRUM_NAMES):
                continue
            # A drum fed by the column itself (a pumparound accumulator) is not
            # part of the overhead system: it must be named as a reflux drum or
            # be fed from the condenser, and return reflux (directly or via a pump).
            fed_from_column = _has_edge(edges, column_id, unit_id) is not None
            fed_from_condenser = _has_edge(edges, condenser_id, unit_id) is not None
            if fed_from_column and not fed_from_condenser:
                continue
            if not (fed_from_condenser or name_contains(unit_id, ("reflux drum",))):
                continue
            if returns_to_column(unit_id) or any(
                returns_to_column(e.to_unit) for e in pumps_fed_by(unit_id)
            ):
                drum_id = unit_id
                break

    # The pump: fed by the drum (preferring the one that returns the reflux),
    # otherwise a pump named as a reflux pump that is linked to this column.
    pump_id: str | None = None
    pump_edge: Edge | None = None  # drum -> pump
    if drum_id:
        candidates = pumps_fed_by(drum_id)
        chosen = next(
            (e for e in candidates if returns_to_column(e.to_unit)), None
        ) or next(
            (e for e in candidates if name_contains(e.to_unit, _REFLUX_PUMP_NAMES)),
            None,
        )
        if chosen:
            pump_id, pump_edge = chosen.to_unit, chosen
    if pump_id is None:
        for unit_id in unit_names:
            if unit_id in (column_id, condenser_id, drum_id):
                continue
            if not name_contains(unit_id, _REFLUX_PUMP_NAMES):
                continue
            if returns_to_column(unit_id) or _has_edge(edges, condenser_id, unit_id):
                pump_id = unit_id
                break

    pump_return = returns_to_column(pump_id)
    drum_return = returns_to_column(drum_id)

    # Complete chain condenser -> drum -> pump -> column: unchanged behaviour.
    if drum_id and pump_id and drum_edge and pump_edge and pump_return:
        return (
            [drum_id, pump_id],
            [drum_edge.stream_id, pump_edge.stream_id, pump_return.stream_id],
        )

    # Partial system: fold the units that their name or the reflux they return
    # identifies, as long as they carry only overhead-system streams.
    accepted: list[str] = []
    if drum_id and (
        name_contains(drum_id, _REFLUX_DRUM_NAMES)
        or drum_return is not None
        or (pump_edge is not None and pump_return is not None)
    ):
        accepted.append(drum_id)
    if pump_id and (
        name_contains(pump_id, _REFLUX_PUMP_NAMES) or pump_return is not None
    ):
        accepted.append(pump_id)

    while True:
        allowed_sources = {column_id, condenser_id, *accepted}
        rejected = [
            unit_id
            for unit_id in accepted
            if not _fed_only_from(unit_id, allowed_sources, edges, feed_streams)
        ]
        if not rejected:
            break
        accepted = [unit_id for unit_id in accepted if unit_id not in rejected]

    evidence: list[str] = []
    for unit_id in accepted:
        linking_edges = [
            _has_edge(edges, condenser_id, unit_id),
            _has_edge(edges, column_id, unit_id),
            pump_edge if unit_id == pump_id and drum_id in accepted else None,
            returns_to_column(unit_id),
        ]
        for edge in linking_edges:
            if edge is not None and edge.stream_id not in evidence:
                evidence.append(edge.stream_id)
    return accepted, evidence


def find_integrated_attachments(
    extraction: dict[str, Any],
    column_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """
    Detect equipment that should be integrated into distillation columns.

    Supported patterns:
    - Column -> Reboiler
    - Column -> Condenser
    - Column -> Condenser -> Drum -> Pump -> Column
    - Column -> Condenser plus a reflux drum / reflux pump that the names or
      the reflux return identify when the chain above lost an edge
      (see ``_find_overhead_system``)
    """
    edges, feed_streams, _, _ = _collect_edges(extraction)
    outgoing_edges = _outgoing(edges)

    unit_names = {
        str(unit.get("id")): str(unit.get("name", ""))
        for unit in extraction.get("units", []) or []
    }

    def unit_name_contains(unit_id: str, keywords: list[str]) -> bool:
        normalized_name = _normalize_text(unit_names.get(unit_id, ""))
        return any(keyword in normalized_name for keyword in keywords)

    def is_distillation_unit(unit_id: str) -> bool:
        return any(
            keyword in _normalize_text(unit_names.get(unit_id, ""))
            for keyword in ["column", "tower", "distillation"]
        )

    attachments: dict[str, dict[str, Any]] = {}

    for column_id in column_ids:
        overrides: dict[str, Any] = {}
        skip_unit_ids: list[str] = []

        if not is_distillation_unit(column_id):
            attachments[column_id] = {"overrides": {"skip_unit_ids": []}}
            continue

        condenser_id: str | None = None
        reboiler_id: str | None = None
        condenser_edge: Edge | None = None
        reboiler_edge: Edge | None = None

        for edge in outgoing_edges.get(column_id, []):
            destination_id = edge.to_unit
            if condenser_id is None and unit_name_contains(
                destination_id, ["condenser"]
            ):
                condenser_id, condenser_edge = destination_id, edge
            if reboiler_id is None and unit_name_contains(destination_id, ["reboiler"]):
                reboiler_id, reboiler_edge = destination_id, edge

        if reboiler_id and reboiler_edge:
            overrides["integrated_reboiler"] = {
                "unit_id": reboiler_id,
                "evidence_streams": [reboiler_edge.stream_id],
            }
            skip_unit_ids.append(reboiler_id)

        if condenser_id and condenser_edge:
            members, evidence = _find_overhead_system(
                column_id, condenser_id, edges, unit_names, feed_streams
            )
            if members:
                overrides["integrated_overhead_system"] = {
                    "unit_ids": [condenser_id, *members],
                    "evidence_streams": [condenser_edge.stream_id, *evidence],
                }
                skip_unit_ids.extend([condenser_id, *members])
            else:
                overrides["integrated_condenser"] = {
                    "unit_id": condenser_id,
                    "evidence_streams": [condenser_edge.stream_id],
                }
                skip_unit_ids.append(condenser_id)

        overrides["skip_unit_ids"] = list(dict.fromkeys(skip_unit_ids))
        attachments[column_id] = {"overrides": overrides}

    return attachments


def apply_coupling(
    extraction: dict[str, Any], attachments: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """
    Remove skipped units, attach overrides to the owning columns, rewrite stream
    endpoints, and remove self-loops created by the rewrite. Utility streams of
    the folded units and the internal reflux line are dropped and listed under
    ``removed_streams``.
    """
    output = copy.deepcopy(extraction)

    skipped_unit_ids = {
        unit_id
        for info in attachments.values()
        for unit_id in info["overrides"].get("skip_unit_ids", [])
    }
    owner_by_skipped_unit = {
        skipped_unit_id: column_id
        for column_id, info in attachments.items()
        for skipped_unit_id in info["overrides"].get("skip_unit_ids", [])
    }

    updated_units = []
    for unit in output.get("units", []) or []:
        unit_id = str(unit.get("id"))
        if unit_id in skipped_unit_ids:
            continue
        if unit_id in attachments:
            overrides = attachments[unit_id]["overrides"]
            # Only annotate units that actually absorb other equipment; a bare
            # {"skip_unit_ids": []} would just be noise for the coding agents.
            if overrides.get("skip_unit_ids") or any(
                key.startswith("integrated_") for key in overrides
            ):
                unit["hysys_overrides"] = overrides
        updated_units.append(unit)
    output["units"] = updated_units

    columns_with_condenser = {
        column_id
        for column_id, info in attachments.items()
        if "integrated_condenser" in info["overrides"]
        or "integrated_overhead_system" in info["overrides"]
    }
    reboiler_ids = {
        info["overrides"]["integrated_reboiler"]["unit_id"]
        for info in attachments.values()
        if "integrated_reboiler" in info["overrides"]
    }
    # keep what the mixing normaliser already dropped (duty labels)
    removed: list[dict[str, Any]] = list(output.get("removed_streams") or [])

    def is_utility(stream: dict[str, Any], folded: Any) -> bool:
        if stream.get("stage") not in (None, ""):
            return False  # a stream drawn to a tray is a column stream, never a utility
        text = stream_text(stream)
        if _UTILITY_RE.search(text):
            return True
        return folded in reboiler_ids and _REBOILER_UTILITY_RE.search(text) is not None

    def is_internal_reflux(stream: dict[str, Any], from_unit: Any) -> bool:
        # The reflux line that leaves the column itself or a member of its
        # folded overhead system (condenser, reflux drum, reflux pump) and is
        # named as the reflux: not a "reflux drum vent", not a "circulating
        # reflux" pumparound draw.
        leaves_overhead_system = from_unit in columns_with_condenser or (
            from_unit in skipped_unit_ids
            and from_unit not in reboiler_ids
            and owner_by_skipped_unit.get(from_unit) in columns_with_condenser
        )
        if not leaves_overhead_system:
            return False
        name = str(stream.get("name") or "").lower()
        if _REFLUX_WORD_RE.search(name) is None:
            return False
        return not (set(re.findall(r"[a-z]+", name)) & _NOT_REFLUX_WORDS)

    for stream_group in ["feed_streams", "intermediate_streams", "product_streams"]:
        streams = output.get(stream_group, []) or []
        kept: list[dict[str, Any]] = []

        for stream in streams:
            from_unit = stream.get("from_unit")
            to_unit = stream.get("to_unit")

            if stream_group == "product_streams" and is_internal_reflux(
                stream, from_unit
            ):
                removed.append(
                    {
                        "id": stream.get("id"),
                        "name": stream.get("name"),
                        "reason": "internal reflux of a column with an integrated condenser",
                    }
                )
                continue

            folded = from_unit if from_unit in skipped_unit_ids else to_unit
            if folded in skipped_unit_ids and is_utility(stream, folded):
                removed.append(
                    {
                        "id": stream.get("id"),
                        "name": stream.get("name"),
                        "reason": f"utility stream of the folded unit {folded}",
                    }
                )
                continue

            # Keep the absorbed unit's id so the Configuration Agent still knows
            # that e.g. a product "from T-101" really left the reflux drum
            # (distillate) or the reboiler (bottoms).
            if from_unit in skipped_unit_ids:
                stream["from_unit"] = owner_by_skipped_unit.get(from_unit, from_unit)
                stream["integrated_via"] = from_unit
            if to_unit in skipped_unit_ids:
                stream["to_unit"] = owner_by_skipped_unit.get(to_unit, to_unit)
                stream["integrated_via"] = to_unit

            if stream.get("from_unit") == stream.get("to_unit"):
                continue
            kept.append(stream)

        output[stream_group] = kept

    if removed:
        output["removed_streams"] = removed
    return output


def couple_extraction(extraction: dict[str, Any]) -> dict[str, Any]:
    """Run the full coupling-normalization pipeline."""
    extraction = prune_orphaned_units(extraction)
    column_ids = identify_column_candidates(extraction)
    attachments = find_integrated_attachments(extraction, column_ids)
    return apply_coupling(extraction, attachments)
