import re
from typing import Any


def _stage_of(stream: dict[str, Any]) -> int | None:
    """The tray/stage number the extraction attached to a column stream, if any."""
    stage = stream.get("stage")
    if isinstance(stage, bool):
        return None
    if isinstance(stage, int):
        return stage
    if isinstance(stage, str) and stage.strip().isdigit():
        return int(stage.strip())
    return None


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
        stage=None,
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
        if stage is not None:
            stream["stage"] = stage
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
                _stage_of(stream),
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
                _stage_of(stream),
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
                _stage_of(stream),
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
            if stream.get("stage") is not None:
                entry["stage"] = stream["stage"]
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


# --- fired heaters, burner-side and flue-gas streams -------------------------
_FIRED_UNIT_WORDS: tuple[str, ...] = ("fired", "furnace")
# Burner-side streams of a fired heater (never part of the tube pass). The fuel
# words among them are also the inlet evidence that promotes a plain heater;
# the burner feed made by the mixing normaliser counts as fuel so that a
# re-run reaches the same verdict.
_FUEL_WORDS = r"fuel|natural gas|fuel oil|burner"
_BURNER_SIDE_RE = re.compile(rf"\b(air|combustion|{_FUEL_WORDS})\b")
_FUEL_RE = re.compile(rf"\b({_FUEL_WORDS})\b")
_FLUE_RE = re.compile(r"\b(flue|combustion|stack)\b")
_AIR_RE = re.compile(r"\bair\b")


def unit_text(unit: dict[str, Any]) -> str:
    """Lower-case id, name and tags of a unit, for keyword checks."""
    tags = [str(tag) for tag in (unit.get("tags") or [])]
    return " ".join(
        [str(unit.get("id") or ""), str(unit.get("name") or "")] + tags
    ).lower()


def stream_text(stream: dict[str, Any]) -> str:
    """Lower-case id and name of a stream, for keyword checks."""
    return f"{stream.get('id') or ''} {stream.get('name') or ''}".lower()


def is_burner_side_stream(stream: dict[str, Any]) -> bool:
    """Air, fuel, combustion air or a burner feed: never part of the tube pass."""
    return _BURNER_SIDE_RE.search(stream_text(stream)) is not None


def is_flue_gas_stream(stream: dict[str, Any]) -> bool:
    return _FLUE_RE.search(stream_text(stream)) is not None


def _is_flue_gas_inbound(stream: dict[str, Any]) -> bool:
    """A flue gas entering a unit (an air preheater or waste-heat exchanger);
    "combustion air" is burner air, not a flue gas."""
    return is_flue_gas_stream(stream) and _AIR_RE.search(stream_text(stream)) is None


def _streams_of(
    extraction: dict[str, Any], unit_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    feeds = extraction.get("feed_streams") or []
    intermediates = extraction.get("intermediate_streams") or []
    products = extraction.get("product_streams") or []
    inbound = [
        s
        for s in feeds + intermediates
        if isinstance(s, dict) and s.get("to_unit") == unit_id
    ]
    outbound = [
        s
        for s in intermediates + products
        if isinstance(s, dict) and s.get("from_unit") == unit_id
    ]
    return inbound, outbound


def is_fired_heater(
    unit: dict[str, Any], extraction: dict[str, Any] | None = None
) -> bool:
    """A fired heater by name or tags ("fired", "furnace") or, when the
    extraction is given, a *heater* that its streams identify as one: a flue
    gas / combustion products stream leaves it, or a fuel stream enters it.
    cs4's "Feed Heater" H-101 is drawn with air and combustion products (and in
    some extractions the fuel gas) and carries no such word in its name.

    Guards: only units named as heaters are promoted (an FCC regenerator with a
    flue gas is not a heater); a unit that *receives* a flue gas is an air
    preheater or waste-heat exchanger, not a fired heater; a "Fuel Gas Heater"
    heats the fuel as its process stream."""
    text = unit_text(unit)
    if any(word in text for word in _FIRED_UNIT_WORDS):
        return True
    if not isinstance(extraction, dict):
        return False
    unit_id = unit.get("id")
    if not isinstance(unit_id, str) or not unit_id:
        return False
    if "heater" not in text or "fuel" in text:
        return False
    inbound, outbound = _streams_of(extraction, unit_id)
    if any(_is_flue_gas_inbound(s) for s in inbound):
        return False
    has_fuel = any(_FUEL_RE.search(stream_text(s)) for s in inbound)
    has_flue = any(is_flue_gas_stream(s) for s in outbound)
    return has_fuel or has_flue


def mark_fired_heaters(extraction: dict[str, Any]) -> list[str]:
    """Tag every unit that its streams identify as a fired heater with
    "fired heater", so that the later normalisers, the unit view and the
    coding agents all see it. Returns the ids of the newly tagged units."""
    tagged: list[str] = []
    intermediates = extraction.get("intermediate_streams") or []
    for unit in extraction.get("units") or []:
        if not isinstance(unit, dict):
            continue
        if any(word in unit_text(unit) for word in _FIRED_UNIT_WORDS):
            continue
        process_mixer_id = f"MIXER_TO_{unit.get('id')}"
        if any(
            isinstance(s, dict) and s.get("from_unit") == process_mixer_id
            for s in intermediates
        ):
            # The mixing normaliser already judged this unit (and merged its
            # inlets): the evidence is gone, the verdict stands.
            continue
        if is_fired_heater(unit, extraction):
            tags = [str(tag) for tag in (unit.get("tags") or [])]
            unit["tags"] = tags + ["fired heater"]
            tagged.append(str(unit.get("id")))
    return tagged
