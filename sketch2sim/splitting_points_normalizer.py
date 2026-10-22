"""Insert explicit tees where one outlet port feeds several streams.

Counterpart of ``mixing_points_normalizer``: the paper's Normalization Agent
"inserts explicit mixing or splitting units" wherever the sketch violates the
simulator's port constraints. The released code only handled the mixing side;
without this step a compressor that feeds both a recycle and a quench line, or
a separator whose overhead gas is split into recycle and purge, leaves one of
the streams without a port to attach to.

Rules
-----
* Single-outlet units (pump, compressor, expander, valve, heater, cooler,
  preheater, pipe): if more than one stream leaves the unit, a tee
  ``TEE_FROM_<UNIT>`` is inserted after it and all outgoing streams are moved
  to the tee.
* Two-port vessels (separator, flash, drum, tank, vessel, settler, reactor):
  if more than one stream leaves the same port (connect_point ``overhead`` or
  ``bottom``), a tee ``TEE_FROM_<UNIT>_<PORT>`` is inserted for that port.

Columns, mixers, tees and component splitters are never touched. Safe to run
repeatedly.
"""

from __future__ import annotations

from typing import Any

from sketch2sim.utils.normalization_utils import (
    is_fired_heater,
    is_flue_gas_stream,
    mark_fired_heaters,
)

_SINGLE_OUTLET_HINTS: set[str] = {
    "pump",
    "compressor",
    "expander",
    "valve",
    "heater",
    "cooler",
    "preheater",
    "pipe",
}
_TWO_PORT_VESSEL_HINTS: set[str] = {
    "separator",
    "flash",
    "drum",
    "tank",
    "vessel",
    "settler",
    "reactor",
    "knock",
}
_NEVER_SPLIT_HINTS: set[str] = {
    "tee",
    "splitter",
    "mixer",
    "column",
    "tower",
    "distillation",
    "absorber",
    "fraction",
    "exchanger",
}
_PORTS = ("overhead", "bottom")


def _text(unit: dict[str, Any]) -> str:
    tags = [str(tag) for tag in (unit.get("tags") or [])]
    return " ".join(
        [str(unit.get("id") or ""), str(unit.get("name") or "")] + tags
    ).lower()


def _kind(unit: dict[str, Any]) -> str | None:
    text = _text(unit)
    if any(hint in text for hint in _NEVER_SPLIT_HINTS):
        return None
    # A fired heater has a flue-gas outlet besides its process outlet: treat it
    # like a vessel (one tee per port) rather than as a single-outlet unit.
    if is_fired_heater(unit):
        return "vessel"
    if any(hint in text for hint in _SINGLE_OUTLET_HINTS):
        return "single"
    if any(hint in text for hint in _TWO_PORT_VESSEL_HINTS):
        return "vessel"
    return None


def _insert_unit_after(
    units: list[dict[str, Any]], new_unit: dict[str, Any], after_id: str
) -> None:
    for index, unit in enumerate(units):
        if isinstance(unit, dict) and unit.get("id") == after_id:
            units.insert(index + 1, new_unit)
            return
    units.append(new_unit)


def normalize_splitting_points(extraction: dict[str, Any] | None) -> dict[str, Any]:
    """Insert tees where several streams leave one outlet port (see module doc)."""
    if not isinstance(extraction, dict):
        return {}

    units: list[dict[str, Any]] = extraction.get("units") or []
    feed_streams: list[dict[str, Any]] = extraction.get("feed_streams") or []
    intermediate_streams: list[dict[str, Any]] = (
        extraction.get("intermediate_streams") or []
    )
    product_streams: list[dict[str, Any]] = extraction.get("product_streams") or []

    mark_fired_heaters(extraction)
    existing_unit_ids = {u.get("id") for u in units if isinstance(u, dict)}
    existing_stream_ids = {
        s.get("id")
        for s in feed_streams + intermediate_streams + product_streams
        if isinstance(s, dict) and s.get("id")
    }

    def unique_stream_id(base: str) -> str:
        if base not in existing_stream_ids:
            existing_stream_ids.add(base)
            return base
        suffix = 2
        while f"{base}_{suffix}" in existing_stream_ids:
            suffix += 1
        existing_stream_ids.add(f"{base}_{suffix}")
        return f"{base}_{suffix}"

    outgoing: list[dict[str, Any]] = [
        s for s in intermediate_streams + product_streams if isinstance(s, dict)
    ]

    for unit in list(units):
        if not isinstance(unit, dict):
            continue
        unit_id = unit.get("id")
        kind = _kind(unit)
        if not isinstance(unit_id, str) or not unit_id.strip() or kind is None:
            continue

        leaving = [s for s in outgoing if s.get("from_unit") == unit_id]
        if is_fired_heater(unit):
            # The flue gas has its own port on a fired heater (CombustionProduct);
            # it never shares a tee with the process outlet.
            leaving = [s for s in leaving if not is_flue_gas_stream(s)]
        if kind == "single":
            groups = {None: leaving} if len(leaving) > 1 else {}
        else:
            groups = {}
            for port in _PORTS:
                same_port = [
                    s
                    for s in leaving
                    if str(s.get("connect_point") or "").lower() == port
                ]
                if len(same_port) > 1:
                    groups[port] = same_port

        for port, streams in groups.items():
            tee_id = (
                f"TEE_FROM_{unit_id}"
                if port is None
                else f"TEE_FROM_{unit_id}_{port.upper()}"
            )
            if tee_id in existing_unit_ids:
                continue  # already normalised
            tee_unit = {
                "id": tee_id,
                "name": f"Tee from {unit_id}" + (f" ({port})" if port else ""),
                "tags": ["tee"],
            }
            _insert_unit_after(units, tee_unit, unit_id)
            existing_unit_ids.add(tee_id)

            for stream in streams:
                stream["from_unit"] = tee_id
                stream["connect_point"] = "side"

            intermediate_streams.append(
                {
                    "id": unique_stream_id(f"S_{tee_id}"),
                    "from_unit": unit_id,
                    "to_unit": tee_id,
                    "name": f"{unit_id} outlet to tee" + (f" ({port})" if port else ""),
                    "connect_point": port or "side",
                }
            )

    extraction["units"] = units
    extraction["feed_streams"] = feed_streams
    extraction["intermediate_streams"] = intermediate_streams
    extraction["product_streams"] = product_streams
    return extraction
