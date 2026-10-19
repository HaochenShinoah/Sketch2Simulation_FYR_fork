from typing import Any


def build_unit_view_from_structure(extraction: dict[str, Any]) -> dict[str, Any]:
    """
    Convert structure JSON into a unit-centric input/output stream view.
    """
    units: list[dict[str, Any]] = extraction.get("units", []) or []

    def build_stream(
        stream_id,
        name,
        from_unit,
        to_unit,
        role,
        connect_point=None,
        integrated_via=None,
    ):
        stream = {
            "id": stream_id,
            "name": name or "",
            "from_unit": from_unit,
            "to_unit": to_unit,
            "role": role,
            "connect_point": connect_point,
        }
        if integrated_via:
            stream["integrated_via"] = integrated_via
        return stream

    all_streams: list[dict[str, Any]] = []

    for stream in extraction.get("feed_streams", []) or []:
        all_streams.append(
            build_stream(
                stream["id"],
                stream.get("name", ""),
                None,
                stream.get("to_unit"),
                "feed",
                stream.get("connect_point"),
                stream.get("integrated_via"),
            )
        )

    for stream in extraction.get("intermediate_streams", []) or []:
        all_streams.append(
            build_stream(
                stream["id"],
                stream.get("name", ""),
                stream.get("from_unit"),
                stream.get("to_unit"),
                "intermediate",
                stream.get("connect_point"),
                stream.get("integrated_via"),
            )
        )

    for stream in extraction.get("product_streams", []) or []:
        all_streams.append(
            build_stream(
                stream["id"],
                stream.get("name", ""),
                stream.get("from_unit"),
                None,
                "product",
                stream.get("connect_point"),
                stream.get("integrated_via"),
            )
        )

    unit_view_units: list[dict[str, Any]] = []
    for unit in units:
        unit_id = unit.get("id")
        if not unit_id:
            continue

        def view(stream, endpoint_key):
            entry = {
                "id": stream["id"],
                "name": stream.get("name", ""),
                "role": stream.get("role", ""),
                endpoint_key: stream.get(endpoint_key),
                "connect_point": stream.get("connect_point"),
            }
            if stream.get("integrated_via"):
                entry["integrated_via"] = stream["integrated_via"]
            return entry

        streams_in = [
            view(stream, "from_unit")
            for stream in all_streams
            if stream.get("to_unit") == unit_id
        ]
        streams_out = [
            view(stream, "to_unit")
            for stream in all_streams
            if stream.get("from_unit") == unit_id
        ]

        unit_entry: dict[str, Any] = {
            "unit_id": unit_id,
            "unit_name": unit.get("name", ""),
            "tags": list(unit.get("tags") or []),
            "streams_in": streams_in,
            "streams_out": streams_out,
        }
        if unit.get("hysys_overrides"):
            unit_entry["hysys_overrides"] = unit["hysys_overrides"]
        unit_view_units.append(unit_entry)

    return {"units_unit_view": unit_view_units}
