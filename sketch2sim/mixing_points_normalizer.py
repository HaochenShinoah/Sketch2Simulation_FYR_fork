from typing import Any

_SINGLE_IN_OUT_HINTS: set[str] = {"valve", "pump", "compressor", "preheater"}
# Units that merge or split streams by design are never given an upstream
# mixer, even when their id happens to contain a hint word (for example the
# mixer "MIXER_TO_FEED_VALVE" inserted by this very function).
_MULTI_INLET_HINTS: set[str] = {"mixer", "tee", "splitter", "header"}


def _is_single_in_out_unit(unit: dict[str, Any]) -> bool:
    unit_id = (unit.get("id") or "").lower()
    unit_name = (unit.get("name") or "").lower()
    tags = [str(tag).lower() for tag in (unit.get("tags") or [])]
    # Units inserted by the normalisers, and units whose tags say they merge or
    # split streams, never get an upstream mixer. (Only the tags are checked so
    # that a "Splitter Feed Pump" is still treated as a single-inlet pump.)
    if unit_id.startswith(("mixer_to_", "tee_from_")):
        return False
    if any(hint in " ".join(tags) for hint in _MULTI_INLET_HINTS):
        return False
    text = " ".join([unit_id, unit_name] + tags)
    return any(hint in text for hint in _SINGLE_IN_OUT_HINTS)


def _insert_unit_before(
    units: list[dict[str, Any]], new_unit: dict[str, Any], before_id: str
) -> None:
    target_id = (before_id or "").strip()
    if not target_id:
        units.append(new_unit)
        return

    for index, unit in enumerate(units):
        if isinstance(unit, dict) and unit.get("id") == target_id:
            units.insert(index, new_unit)
            return

    units.append(new_unit)


def normalize_mixing_points(extraction: dict[str, Any] | None) -> dict[str, Any]:
    """
    Insert an explicit mixer upstream of single-inlet units when multiple inbound
    streams are detected.

    - Adds unit: MIXER_TO_<UNIT_ID>
    - Adds stream: S_MIXER_TO_<UNIT_ID>
    - Redirects inbound streams to the mixer
    - Places the mixer immediately before the target unit

    Safe to run multiple times.
    """
    if not isinstance(extraction, dict):
        return {}

    units: list[dict[str, Any]] = extraction.get("units") or []
    feed_streams: list[dict[str, Any]] = extraction.get("feed_streams") or []
    intermediate_streams: list[dict[str, Any]] = (
        extraction.get("intermediate_streams") or []
    )
    product_streams: list[dict[str, Any]] = extraction.get("product_streams") or []

    unit_by_id: dict[str, dict[str, Any]] = {
        unit.get("id"): unit
        for unit in units
        if isinstance(unit, dict) and unit.get("id")
    }

    inbound_streams_by_unit: dict[str, list[dict[str, Any]]] = {}

    def add_inbound_stream(to_unit: Any, stream: dict[str, Any]) -> None:
        if isinstance(to_unit, str) and to_unit.strip():
            inbound_streams_by_unit.setdefault(to_unit, []).append(stream)

    for stream in feed_streams:
        if isinstance(stream, dict):
            add_inbound_stream(stream.get("to_unit"), stream)

    for stream in intermediate_streams:
        if isinstance(stream, dict):
            add_inbound_stream(stream.get("to_unit"), stream)

    existing_stream_ids: set[str] = {
        stream.get("id")
        for stream in (feed_streams + intermediate_streams + product_streams)
        if isinstance(stream, dict) and stream.get("id")
    }

    def unique_id(base_id: str) -> str:
        if base_id not in existing_stream_ids:
            existing_stream_ids.add(base_id)
            return base_id

        suffix = 2
        while f"{base_id}_{suffix}" in existing_stream_ids:
            suffix += 1

        new_id = f"{base_id}_{suffix}"
        existing_stream_ids.add(new_id)
        return new_id

    for unit in list(units):
        if not isinstance(unit, dict):
            continue
        if not _is_single_in_out_unit(unit):
            continue

        unit_id = unit.get("id")
        if not isinstance(unit_id, str) or not unit_id.strip():
            continue

        inbound_streams = inbound_streams_by_unit.get(unit_id, [])
        if len(inbound_streams) <= 1:
            continue

        mixer_id = f"MIXER_TO_{unit_id}"
        mixer_stream_base = f"S_MIXER_TO_{unit_id}"

        if mixer_id not in unit_by_id:
            mixer_unit = {
                "id": mixer_id,
                "name": f"Mixer to {unit_id}",
                "tags": ["mixer"],
            }
            _insert_unit_before(units, mixer_unit, before_id=unit_id)
            unit_by_id[mixer_id] = mixer_unit
        else:
            try:
                mixer_index = next(
                    index
                    for index, existing_unit in enumerate(units)
                    if isinstance(existing_unit, dict)
                    and existing_unit.get("id") == mixer_id
                )
                unit_index = next(
                    index
                    for index, existing_unit in enumerate(units)
                    if isinstance(existing_unit, dict)
                    and existing_unit.get("id") == unit_id
                )
                if mixer_index != unit_index - 1:
                    mixer_unit = units.pop(mixer_index)
                    unit_index = next(
                        index
                        for index, existing_unit in enumerate(units)
                        if isinstance(existing_unit, dict)
                        and existing_unit.get("id") == unit_id
                    )
                    units.insert(unit_index, mixer_unit)
            except StopIteration:
                pass

        for stream in inbound_streams:
            if isinstance(stream, dict) and stream.get("to_unit") != mixer_id:
                stream["to_unit"] = mixer_id

        already_has_link = any(
            isinstance(stream, dict)
            and stream.get("from_unit") == mixer_id
            and stream.get("to_unit") == unit_id
            for stream in intermediate_streams
        )

        if not already_has_link:
            intermediate_streams.append(
                {
                    "id": unique_id(mixer_stream_base),
                    "from_unit": mixer_id,
                    "to_unit": unit_id,
                    "name": f"Mixer outlet to {unit_id}",
                    "connect_point": "side",
                }
            )

    extraction["units"] = units
    extraction["feed_streams"] = feed_streams
    extraction["intermediate_streams"] = intermediate_streams
    extraction["product_streams"] = product_streams

    return extraction
