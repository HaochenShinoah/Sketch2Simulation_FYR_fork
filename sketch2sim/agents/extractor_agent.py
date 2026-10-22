"""Extractor Agent (A2): description (+ optional sketch) -> structured JSON IR."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.runnables import RunnableLambda
from langgraph.graph import StateGraph
from pydantic import BaseModel

from sketch2sim import config, llm
from sketch2sim.agents.descriptor_agent import encode_image_to_jpeg_b64


class ExtractorState(BaseModel):
    process_text: str
    image_path: str | None = None
    extraction: dict[str, Any] | None = None
    error: str | None = None


_CONNECT_POINT = {"type": "string", "enum": ["overhead", "bottom", "side", "unknown"]}
# Optional tray/stage hint (strict schemas require every property, hence null).
_STAGE = {"type": ["integer", "null"]}

# Strict JSON schema of the Intermediate Representation (Structured Outputs).
EXTRACTION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "units": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "name", "tags"],
            },
        },
        "feed_streams": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "to_unit": {"type": "string"},
                    "name": {"type": "string"},
                    "connect_point": _CONNECT_POINT,
                    "stage": _STAGE,
                },
                "required": ["id", "to_unit", "name", "connect_point", "stage"],
            },
        },
        "intermediate_streams": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "from_unit": {"type": "string"},
                    "to_unit": {"type": "string"},
                    "name": {"type": "string"},
                    "connect_point": _CONNECT_POINT,
                    "stage": _STAGE,
                },
                "required": [
                    "id",
                    "from_unit",
                    "to_unit",
                    "name",
                    "connect_point",
                    "stage",
                ],
            },
        },
        "product_streams": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "from_unit": {"type": "string"},
                    "name": {"type": "string"},
                    "connect_point": _CONNECT_POINT,
                    "stage": _STAGE,
                },
                "required": ["id", "from_unit", "name", "connect_point", "stage"],
            },
        },
    },
    "required": ["units", "feed_streams", "intermediate_streams", "product_streams"],
}

EXTRACTOR_SYSTEM_PROMPT = (
    "You are a Process Engineering Assistant. Extract structured JSON from a chemical process description.\n\n"
    "Return only one JSON object with these keys:\n"
    "{\n"
    '  "units": [ {"id": "Str", "name": "Str", "tags": ["Str"]} ],\n'
    '  "feed_streams": [ {"id": "Str", "to_unit": "Str", "name": "Str", "connect_point": "Str", "stage": Int|null} ],\n'
    '  "intermediate_streams": [ {"id": "Str", "from_unit": "Str", "to_unit": "Str", "name": "Str", "connect_point": "Str", "stage": Int|null} ],\n'
    '  "product_streams": [ {"id": "Str", "from_unit": "Str", "name": "Str", "connect_point": "Str", "stage": Int|null} ]\n'
    "}\n\n"
    "Rules:\n"
    "- Feed streams enter the system from outside.\n"
    "- Product streams leave the system.\n"
    "- Intermediate streams connect two internal units.\n"
    '- Use exact IDs from the description. If a stream has a number, use "S" plus the number.\n'
    '- Use "overhead", "bottom", or "side" for connect_point when clear; otherwise use "unknown".\n'
    "- stage: the tray/stage number where a stream enters or leaves a column when the drawing states it; otherwise null.\n"
    "- Exclude utilities and energy streams. A label starting with Q (Q-trim, Q-101, Qcond, Qreb) is a heat duty: never emit it as a stream.\n"
    "- Flue gas leaving a fired heater and water drawn from a column condenser are material product streams, not utilities; keep them.\n"
    "- Internal sections of a vessel (coalescer section, catalyst bed, demister, packing) are not equipment; do not emit them as units.\n"
    "- A storage tank or feed drum at the start of the process that only shows an outlet still receives the process feed drawn into it: emit that feed stream. Never invent a feed for a reflux drum, accumulator or knock-out drum downstream of a condenser or separator.\n"
    "- A stream that joins a line before a unit enters that unit.\n"
    "- Return JSON only. No explanations.\n"
)

_IMAGE_NOTE = (
    "\nThe original process flow diagram image is attached. The diagram is the "
    "primary source of truth; the description is a semantic prior that helps "
    "resolve ambiguity. Do not add equipment that is not visible in the diagram.\n"
)


def _clean_json_text(text: str) -> str:
    cleaned = (text or "").replace("```json", "").replace("```", "").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1:
        return cleaned[start : end + 1]
    return cleaned or "{}"


def run_extractor(state: ExtractorState, model_name: str) -> dict[str, Any]:
    process_text = (state.process_text or "").strip()
    if not process_text:
        return {"extraction": None, "error": "No process_text provided."}

    system_prompt = EXTRACTOR_SYSTEM_PROMPT
    images: list[str] = []
    if state.image_path:
        images.append(encode_image_to_jpeg_b64(state.image_path))
        system_prompt += _IMAGE_NOTE

    raw = llm.chat(
        model_name,
        system_prompt,
        process_text,
        images_b64=images,
        json_schema=EXTRACTION_JSON_SCHEMA,
        schema_name="process_structure",
        max_output_tokens=config.EXTRACTOR_MAX_OUTPUT_TOKENS,
        log_label="extractor",
    )

    if not (raw or "").strip():
        return {
            "extraction": {"_error": "empty response"},
            "error": "Extractor returned no text (refusal or failed response).",
        }

    try:
        extraction = json.loads(_clean_json_text(raw))
        if not isinstance(extraction, dict):
            raise ValueError("Extractor did not return a JSON object.")
        return {"extraction": extraction, "error": None}
    except Exception as error:
        return {"extraction": {"_error": str(error)}, "error": str(error)}


def build_extractor_graph(model_name: str):
    graph = StateGraph(ExtractorState)
    graph.add_node(
        "extractor",
        RunnableLambda(lambda state: run_extractor(state, model_name=model_name)),
    )
    graph.set_entry_point("extractor")
    graph.set_finish_point("extractor")
    return graph.compile()
