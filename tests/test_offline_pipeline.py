"""Runs the whole pipeline (steps 1-8 + merge) with the fake LLM, no HYSYS."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from sketch2sim.pipeline import PipelineConfig, run_pipeline
from tests.conftest import REPO_ROOT, write_dummy_instruction

INSTRUCTIONS_DIR = REPO_ROOT / "sketch2sim" / "instructions"


def _instruction_files(tmp_path: Path) -> tuple[list[str], list[str]]:
    inst = INSTRUCTIONS_DIR / "instantiation_instructions_simple_v5.txt"
    conf = INSTRUCTIONS_DIR / "configuration_instructions_simple_v5.txt"
    if not inst.exists():
        inst = write_dummy_instruction(tmp_path / "inst.txt", "INSTANTIATION")
    if not conf.exists():
        conf = write_dummy_instruction(tmp_path / "conf.txt", "CONFIGURATION")
    return [str(inst)], [str(conf)]


def test_pipeline_offline_cs1(fake_llm, repo_cwd, tmp_path):
    inst_files, conf_files = _instruction_files(tmp_path)
    cfg = PipelineConfig(
        image_path="images/cs1.png",
        log_dir=str(tmp_path / "logs"),
        output_dir=str(tmp_path / "outputs"),
        instantiation_instruction_files=inst_files,
        configuration_instruction_files=conf_files,
        execute=False,
    )
    result = run_pipeline(cfg)

    # every LLM-backed step ran exactly once, in order
    assert [c["label"] for c in fake_llm.calls] == [
        "descriptor",
        "description_validator",
        "extractor",
        "basis",
        "instantiation",
        "configuration",
    ]
    descriptor_call = fake_llm.calls[0]
    assert descriptor_call["kwargs"]["images_b64"], "descriptor must receive the image"
    extractor_call = fake_llm.calls[2]
    assert extractor_call["kwargs"]["images_b64"], (
        "extractor receives the image by default"
    )
    assert extractor_call["kwargs"]["json_schema"]["required"] == [
        "units",
        "feed_streams",
        "intermediate_streams",
        "product_streams",
    ]

    # the instruction files reach the two coding agents verbatim
    inst_text = Path(inst_files[0]).read_text(encoding="utf-8").strip()
    conf_text = Path(conf_files[0]).read_text(encoding="utf-8").strip()
    assert inst_text in fake_llm.calls[4]["user_prompt"]
    assert conf_text in fake_llm.calls[5]["user_prompt"]
    assert "[INSTRUCTIONS START]" in fake_llm.calls[4]["user_prompt"]

    # Basis prompt carries the RAG knowledge base for every feed material
    basis_prompt = fake_llm.calls[3]["user_prompt"]
    assert "Feed materials:\n- Crude oil\n- water\n- Demulsifier agent" in basis_prompt
    assert "Mixture_name: Crude Oil" in basis_prompt
    assert "Mixture_name: Demulsifier Agent" in basis_prompt

    # logs and outputs
    logs = tmp_path / "logs"
    for name in [
        "step0_run_config.json",
        "step1_descriptor_result.txt",
        "step2_description_validation.json",
        "step3_extraction.json",
        "step4A_mixing_points_normalized.json",
        "step4B_coupled_structure.json",
        "step4C_splitting_points_normalized.json",
        "step5_basis_latest_code.py",
        "step6_instantiation_latest_code.py",
        "step7_unit_view.json",
        "step8_configuration_latest_code.py",
    ]:
        assert (logs / name).exists(), name

    final = Path(result.combined_path).read_text(encoding="utf-8")
    ast.parse(final)  # valid Python
    for marker in [
        "# === Step 1:",
        "# === Step 2:",
        "# === Step 3:",
        "# === Step 4: Add Unit Operations (Agent 2) ===",
        "# === Step 5: Add Material Streams (Agent 2) ===",
        "# === Step 6: Connect Streams (Agent 3) ===",
        "# === Step 7: Save Simulation ===",
    ]:
        assert final.count(marker) == 1, marker
    assert 'case_name = "Crude_Oil_Desalting"' in final
    assert 'property_package = "PengRob"' in final
    assert "FILL_" not in final
    # all units and streams of the normalised structure are instantiated
    structure = json.loads(
        (logs / "step4C_splitting_points_normalized.json").read_text()
    )
    for unit in structure["units"]:
        assert f'add_unit("{unit["id"]}", ' in final
    for group in ("feed_streams", "intermediate_streams", "product_streams"):
        for stream in structure[group]:
            assert f'add_stream("{stream["id"]}", ' in final
    assert re.search(r'connect\("ELECTROSTATIC_SEPARATOR", "S7", "vapour_out"\)', final)
    # the template's helper layer survived the basis splice
    assert final.count("# === Step 3b: HYSYS COM helpers") == 1
    assert "def add_unit(" in final and "def connect_column_feed(" in final
    assert "finish_connections()" in final
    assert not result.executed


def test_pipeline_text_only_extractor(fake_llm, repo_cwd, tmp_path):
    inst_files, conf_files = _instruction_files(tmp_path)
    cfg = PipelineConfig(
        image_path="images/cs1.png",
        log_dir=str(tmp_path / "logs"),
        output_dir=str(tmp_path / "outputs"),
        instantiation_instruction_files=inst_files,
        configuration_instruction_files=conf_files,
        extractor_uses_image=False,
        execute=False,
    )
    run_pipeline(cfg)
    extractor_call = next(c for c in fake_llm.calls if c["label"] == "extractor")
    assert not extractor_call["kwargs"]["images_b64"]
