"""Descriptor Agent (A1): multimodal description of the process sketch."""

from __future__ import annotations

import base64
import io
from typing import Any

from langchain_core.runnables import RunnableLambda
from langgraph.graph import StateGraph
from PIL import Image
from pydantic import BaseModel

from sketch2sim import config, llm

DESCRIPTOR_SYSTEM_PROMPT = (
    "You are a senior chemical process engineer. "
    "Follow the user's instructions carefully."
)


class DescriptorState(BaseModel):
    image_path: str
    description: str | None = None


def encode_image_to_jpeg_b64(image_path: str) -> str:
    """Standardise the sketch (RGB JPEG, capped width) and base64-encode it."""
    with Image.open(image_path) as img:
        img = img.convert("RGB")

        if img.width > config.DESCRIPTOR_MAX_IMAGE_WIDTH:
            scale = config.DESCRIPTOR_MAX_IMAGE_WIDTH / img.width
            img = img.resize(
                (config.DESCRIPTOR_MAX_IMAGE_WIDTH, int(img.height * scale))
            )

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85, optimize=True)

    return base64.b64encode(buffer.getvalue()).decode("utf-8")


# Backwards-compatible alias (older code imported the private name).
_encode_image_to_jpeg_b64 = encode_image_to_jpeg_b64


def _build_prompt() -> str:
    return (
        "You are a senior chemical process engineer. "
        "You are given a process flow diagram image.\n\n"
        "Return only the following sections:\n"
        "1. PROCESS DESCRIPTION\n"
        "2. EQUIPMENT AND CONNECTIONS\n"
        "3. FINAL CONSISTENCY CHECK\n\n"
        "Rules:\n"
        "- Describe the process from left to right.\n"
        "- List each visible equipment item once.\n"
        "- Do not invent hidden equipment.\n"
        "- Do not write code.\n"
    )


def describe_image(model_name: str, prompt: str, image_b64: str) -> str:
    return llm.chat(
        model_name,
        DESCRIPTOR_SYSTEM_PROMPT,
        prompt,
        images_b64=[image_b64],
        max_output_tokens=config.DESCRIPTOR_MAX_OUTPUT_TOKENS,
        log_label="descriptor",
    )


def run_descriptor(state: DescriptorState, model_name: str) -> dict[str, Any]:
    image_b64 = encode_image_to_jpeg_b64(state.image_path)
    prompt = _build_prompt()
    description = describe_image(model_name, prompt, image_b64)
    return {"description": description}


def build_descriptor_graph(model_name: str):
    graph = StateGraph(DescriptorState)
    graph.add_node(
        "descriptor",
        RunnableLambda(lambda state: run_descriptor(state, model_name=model_name)),
    )
    graph.set_entry_point("descriptor")
    graph.set_finish_point("descriptor")
    return graph.compile()
