import copy
import re
from dataclasses import dataclass
from typing import Any

_ALLOWED_CONNECT_POINTS = {"overhead", "bottom", "side", "unknown"}


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
    """
    edges, _, _, _ = _collect_edges(extraction)
    outgoing_edges = _outgoing(edges)

    unit_names = {
        str(unit.get("id")): str(unit.get("name", ""))
        for unit in extraction.get("units", []) or []
    }

    def edge_between(from_unit: str, to_unit: str) -> Edge | None:
        return _has_edge(edges, from_unit, to_unit)

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
            drum_id: str | None = None
            pump_id: str | None = None
            drum_edge: Edge | None = None
            pump_edge: Edge | None = None
            reflux_return_edge: Edge | None = None

            for edge in outgoing_edges.get(condenser_id, []):
                if unit_name_contains(edge.to_unit, ["drum", "reflux", "accumulator"]):
                    drum_id, drum_edge = edge.to_unit, edge
                    break

            if drum_id:
                for edge in outgoing_edges.get(drum_id, []):
                    if unit_name_contains(edge.to_unit, ["pump"]):
                        pump_id, pump_edge = edge.to_unit, edge
                        break

            if pump_id:
                reflux_return_edge = edge_between(pump_id, column_id)

            if drum_id and pump_id and reflux_return_edge and drum_edge and pump_edge:
                overrides["integrated_overhead_system"] = {
                    "unit_ids": [condenser_id, drum_id, pump_id],
                    "evidence_streams": [
                        condenser_edge.stream_id,
                        drum_edge.stream_id,
                        pump_edge.stream_id,
                        reflux_return_edge.stream_id,
                    ],
                }
                skip_unit_ids.extend([condenser_id, drum_id, pump_id])
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
    endpoints, and remove self-loops created by the rewrite.
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

    for stream_group in ["feed_streams", "intermediate_streams", "product_streams"]:
        streams = output.get(stream_group, []) or []

        for stream in streams:
            from_unit = stream.get("from_unit")
            to_unit = stream.get("to_unit")

            # Keep the absorbed unit's id so the Configuration Agent still knows
            # that e.g. a product "from T-101" really left the reflux drum
            # (distillate) or the reboiler (bottoms).
            if from_unit in skipped_unit_ids:
                stream["from_unit"] = owner_by_skipped_unit.get(from_unit, from_unit)
                stream["integrated_via"] = from_unit
            if to_unit in skipped_unit_ids:
                stream["to_unit"] = owner_by_skipped_unit.get(to_unit, to_unit)
                stream["integrated_via"] = to_unit

        output[stream_group] = [
            stream
            for stream in streams
            if stream.get("from_unit") != stream.get("to_unit")
        ]

    return output


def couple_extraction(extraction: dict[str, Any]) -> dict[str, Any]:
    """Run the full coupling-normalization pipeline."""
    extraction = prune_orphaned_units(extraction)
    column_ids = identify_column_candidates(extraction)
    attachments = find_integrated_attachments(extraction, column_ids)
    return apply_coupling(extraction, attachments)
