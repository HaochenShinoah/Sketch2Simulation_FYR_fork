"""Insert explicit mixers where several streams enter a single-inlet unit.

Two kinds of unit are treated specially:

* Fired heaters: their burner-side streams (fuel, air) never join the process
  tube pass. A fired heater is recognised by its name ("fired", "furnace") or
  by its streams (a flue gas out or a fuel stream in, see
  ``normalization_utils.mark_fired_heaters``). When fuel and air are both drawn
  they are merged by a burner mixer into one "Burner feed" stream, because the
  HYSYS fired heater has a single BurnerFeed port.
* Two-stream exchangers drawn as a heater or cooler: a "Feed Preheater" that
  the reactor effluent passes through (cs4) has two inlets and two outlets. It
  is tagged "heat exchanger", the inlet whose name an outlet echoes
  ("Reactor effluent" -> "Cooled reactor effluent") is left alone, and only the
  remaining inlets are mixed. Without the tag the unit would get a mixer that
  blends the effluent into the feed and a tee after it.
"""

from __future__ import annotations

import re
from typing import Any

from sketch2sim.utils.normalization_utils import (
    is_burner_side_stream,
    is_fired_heater,
    mark_fired_heaters,
    unit_text,
)

# Units that take exactly one process inlet. A two-stream heat exchanger has
# two inlets, so "exchanger" is deliberately absent: an exchanger only counts
# when its own name says so ("preheater", "cooler", "heater", ...).
_SINGLE_IN_OUT_HINTS: set[str] = {
    "valve",
    "pump",
    "compressor",
    "preheater",
    "heater",
    "cooler",
    "furnace",
    "fired",
    "expander",
    "turbine",
    "pipe",
}
# Units that merge or split streams by design are never given an upstream
# mixer, even when their id happens to contain a hint word (for example the
# mixer "MIXER_TO_FEED_VALVE" inserted by this very function).
_MULTI_INLET_HINTS: set[str] = {"mixer", "tee", "splitter", "header"}
# Units that accept several inlets in HYSYS, whatever their name says
# (a "cracking furnace" reactor, a "reflux pump" column, a two-stream exchanger).
_MULTI_INLET_UNIT_HINTS: set[str] = {
    "reactor",
    "column",
    "tower",
    "absorber",
    "stripper",
    "exchanger",
}
# Heat-transfer units that may really be a two-stream exchanger when the
# sketch gives them two inlets and two outlets.
_HEAT_TRANSFER_HINTS: set[str] = {"heater", "cooler", "preheater"}
# Words that say nothing about which inlet an outlet continues.
_GENERIC_WORDS: set[str] = {
    "the",
    "and",
    "stream",
    "outlet",
    "inlet",
    "feed",
    "product",
    "hot",
    "cold",
    "heated",
    "cooled",
    "preheated",
    "warm",
    "process",
    "line",
}


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
    # Reactors, columns and two-stream exchangers take several inlets whatever
    # else their name says. Tags alone do not disqualify an exchanger: the cs4
    # "Feed Preheater" is tagged "heat exchanger" and still needs its mixer.
    if any(
        hint in " ".join([unit_name] + tags)
        for hint in _MULTI_INLET_UNIT_HINTS
        if hint != "exchanger"
    ):
        return False
    if "exchanger" in unit_name:
        return False
    text = " ".join([unit_id, unit_name] + tags)
    return any(hint in text for hint in _SINGLE_IN_OUT_HINTS)


def _is_heat_transfer_unit(unit: dict[str, Any]) -> bool:
    return any(hint in unit_text(unit) for hint in _HEAT_TRANSFER_HINTS)


def _words(stream: dict[str, Any]) -> set[str]:
    """Content words of a stream name. Ids such as "P-101A/B_to_E-101" share
    tokens with every stream of the unit, so the id is used only when the
    stream has no name; purely numeric tokens never count."""
    text = str(stream.get("name") or stream.get("id") or "").lower()
    return {
        word
        for word in re.findall(r"[a-z0-9]+", text)
        if len(word) >= 3 and word not in _GENERIC_WORDS and not word.isdigit()
    }


def _exchanger_sides(
    inbound: list[dict[str, Any]], outbound: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split the inlets of a two-outlet heat-transfer unit.

    Returns (matched, rest): ``matched`` are the inlets whose name an outlet
    echoes (one inlet per outlet, best word overlap first), ``rest`` the
    inlets left for the other side.
    """
    pairs = sorted(
        (
            (len(_words(inlet) & _words(outlet)), out_index, in_index)
            for out_index, outlet in enumerate(outbound)
            for in_index, inlet in enumerate(inbound)
        ),
        key=lambda item: (-item[0], item[1], item[2]),
    )
    used_out: set[int] = set()
    matched_index: list[int] = []
    for score, out_index, in_index in pairs:
        if score < 1:
            break
        if out_index in used_out or in_index in matched_index:
            continue
        used_out.add(out_index)
        matched_index.append(in_index)
    # Two outlets mean two sides. When inlets are left over and every outlet
    # already took one, the weakest match shares the side of the leftovers
    # (cs4: "Pumped toluene" -> "Preheated toluene feed" joins the hydrogen in
    # the mixer, "Reactor effluent" keeps its own side).
    if (
        matched_index
        and len(matched_index) >= len(outbound)
        and len(matched_index) < len(inbound)
    ):
        matched_index.pop()  # pairs are in descending score: the last is weakest
    matched = [inbound[i] for i in sorted(matched_index)]
    rest = [s for i, s in enumerate(inbound) if i not in matched_index]
    return matched, rest


# A sketch label such as "Q-trim", "Q-101", "Qreb" or "Q-cond" is a heat duty
# (the HYSYS naming convention for energy streams), not a material stream.
# cs3's third run drew "Q-trim" as a column feed.
_DUTY_LABEL_RE = re.compile(r"^q[\s_-]*(trim|cond(enser)?|reb(oiler)?|duty|\d+)\b")


def _drop_duty_labels(
    extraction: dict[str, Any],
    feed_streams: list[dict[str, Any]],
    product_streams: list[dict[str, Any]],
) -> None:
    """Remove feed / product streams that are heat-duty labels (in place)."""
    removed = list(extraction.get("removed_streams") or [])
    for streams in (feed_streams, product_streams):
        for stream in list(streams):
            if not isinstance(stream, dict):
                continue
            name = str(stream.get("name") or stream.get("id") or "").strip().lower()
            if _DUTY_LABEL_RE.match(name):
                streams.remove(stream)
                removed.append(
                    {
                        "id": stream.get("id"),
                        "name": stream.get("name"),
                        "reason": "heat duty label drawn as a stream",
                    }
                )
    if removed:
        extraction["removed_streams"] = removed


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


def _index_of(units: list[dict[str, Any]], unit_id: str) -> int | None:
    for index, unit in enumerate(units):
        if isinstance(unit, dict) and unit.get("id") == unit_id:
            return index
    return None


def normalize_mixing_points(extraction: dict[str, Any] | None) -> dict[str, Any]:
    """
    Insert an explicit mixer upstream of single-inlet units when multiple inbound
    streams are detected.

    - Adds unit: MIXER_TO_<UNIT_ID>
    - Adds stream: S_MIXER_TO_<UNIT_ID>
    - Redirects inbound streams to the mixer
    - Places the mixer immediately before the target unit

    For a fired heater the burner-side streams (fuel, air) are left out of that
    count; when more than one of them is drawn they get their own mixer
    MIXER_TO_<UNIT_ID>_BURNER whose outlet "Burner feed to <UNIT_ID>" is the
    single burner feed of the heater. A heater or cooler with two inlets and
    two outlets is a two-stream exchanger (tagged "heat exchanger"): only the
    inlets that no outlet continues are mixed.

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

    mark_fired_heaters(extraction)
    _drop_duty_labels(extraction, feed_streams, product_streams)

    unit_by_id: dict[str, dict[str, Any]] = {
        unit.get("id"): unit
        for unit in units
        if isinstance(unit, dict) and unit.get("id")
    }

    inbound_streams_by_unit: dict[str, list[dict[str, Any]]] = {}
    outbound_streams_by_unit: dict[str, list[dict[str, Any]]] = {}

    def add_stream(
        index: dict[str, list[dict[str, Any]]], unit_id: Any, stream: dict[str, Any]
    ) -> None:
        if isinstance(unit_id, str) and unit_id.strip():
            index.setdefault(unit_id, []).append(stream)

    for stream in feed_streams:
        if isinstance(stream, dict):
            add_stream(inbound_streams_by_unit, stream.get("to_unit"), stream)

    for stream in intermediate_streams:
        if isinstance(stream, dict):
            add_stream(inbound_streams_by_unit, stream.get("to_unit"), stream)
            add_stream(outbound_streams_by_unit, stream.get("from_unit"), stream)

    for stream in product_streams:
        if isinstance(stream, dict):
            add_stream(outbound_streams_by_unit, stream.get("from_unit"), stream)

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

    def insert_mixer(
        unit_id: str,
        mixer_id: str,
        mixer_name: str,
        streams: list[dict[str, Any]],
        outlet_name: str,
    ) -> None:
        if mixer_id not in unit_by_id:
            mixer_unit = {"id": mixer_id, "name": mixer_name, "tags": ["mixer"]}
            _insert_unit_before(units, mixer_unit, before_id=unit_id)
            unit_by_id[mixer_id] = mixer_unit
        else:
            mixer_index = _index_of(units, mixer_id)
            unit_index = _index_of(units, unit_id)
            if (
                mixer_index is not None
                and unit_index is not None
                and mixer_index != unit_index - 1
            ):
                mixer_unit = units.pop(mixer_index)
                units.insert(_index_of(units, unit_id), mixer_unit)

        for stream in streams:
            if stream.get("to_unit") != mixer_id:
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
                    "id": unique_id(f"S_{mixer_id}"),
                    "from_unit": mixer_id,
                    "to_unit": unit_id,
                    "name": outlet_name,
                    "connect_point": "side",
                }
            )

    for unit in list(units):
        if not isinstance(unit, dict):
            continue
        if not _is_single_in_out_unit(unit):
            continue

        unit_id = unit.get("id")
        if not isinstance(unit_id, str) or not unit_id.strip():
            continue

        inbound_streams = [
            stream
            for stream in inbound_streams_by_unit.get(unit_id, [])
            if isinstance(stream, dict)
        ]
        if is_fired_heater(unit):
            # Air and fuel go to the burner, not into the process tube pass.
            # The outlet of this unit's own process mixer is never burner-side,
            # whatever words the unit id puts into its name.
            process_mixer_id = f"MIXER_TO_{unit_id}"
            burner_streams = [
                s
                for s in inbound_streams
                if is_burner_side_stream(s) and s.get("from_unit") != process_mixer_id
            ]
            inbound_streams = [s for s in inbound_streams if s not in burner_streams]
            if len(burner_streams) > 1:
                insert_mixer(
                    unit_id,
                    f"MIXER_TO_{unit_id}_BURNER",
                    f"Burner mixer to {unit_id}",
                    burner_streams,
                    f"Burner feed to {unit_id}",
                )
        elif _is_heat_transfer_unit(unit) and len(inbound_streams) >= 2:
            outbound_streams = [
                stream
                for stream in outbound_streams_by_unit.get(unit_id, [])
                if isinstance(stream, dict)
            ]
            if len(outbound_streams) == 2:
                matched, rest = _exchanger_sides(inbound_streams, outbound_streams)
                if matched or len(inbound_streams) == 2:
                    # A two-stream exchanger: the matched inlet keeps its own
                    # side, the other inlets share the second side (so the
                    # unit always ends with two inlets for its two ports).
                    if "exchanger" not in unit_text(unit):
                        unit["tags"] = [str(t) for t in (unit.get("tags") or [])] + [
                            "heat exchanger"
                        ]
                    inbound_streams = rest if matched else []
        if len(inbound_streams) <= 1:
            continue

        insert_mixer(
            unit_id,
            f"MIXER_TO_{unit_id}",
            f"Mixer to {unit_id}",
            inbound_streams,
            f"Mixer outlet to {unit_id}",
        )

    extraction["units"] = units
    extraction["feed_streams"] = feed_streams
    extraction["intermediate_streams"] = intermediate_streams
    extraction["product_streams"] = product_streams

    return extraction
