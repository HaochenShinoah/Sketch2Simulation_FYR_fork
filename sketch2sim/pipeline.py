"""End-to-end orchestrator: process sketch -> executable Aspen HYSYS script.

This module holds the nine-step workflow of the paper so that it can be run
from the notebook (``run_workflow.ipynb``), from the command line
(``python -m sketch2sim.pipeline images/cs1.png``) and from the offline tests.

Steps
-----
1  Descriptor            image -> process description             (LLM, vision)
2  Description Validator description -> quality verdict           (LLM)
3  Extractor             description (+image) -> JSON structure   (LLM, vision)
4A Mixing/splitting-points normalizer (explicit mixers and tees)  (rule-based)
4B Coupling normalizer  (fold condenser/reboiler into columns)    (rule-based)
5  Basis Agent           case, property package, components       (LLM + RAG)
6  Instantiation Agent   unit operations + material streams       (LLM)
7  Unit IN/OUT view                                               (rule-based)
8  Configuration Agent   connect streams to unit ports            (LLM)
9  Merge + execute, with runtime-driven fixing retries            (HYSYS COM)
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sketch2sim import config, llm
from sketch2sim.agents.basis_agent import BasisState, build_basis_graph
from sketch2sim.agents.configuration_agent import (
    ConfigurationState,
    build_configuration_graph,
)
from sketch2sim.agents.description_validator_agent import (
    DescriptionValidatorState,
    build_description_validator_graph,
)
from sketch2sim.agents.descriptor_agent import DescriptorState, build_descriptor_graph
from sketch2sim.agents.executor_agent import ExecutorState, build_executor_graph
from sketch2sim.agents.extractor_agent import ExtractorState, build_extractor_graph
from sketch2sim.agents.fixer_agent import fix_code_file
from sketch2sim.agents.instantiation_agent import (
    InstantiationState,
    build_instantiation_graph,
)
from sketch2sim.coupling_normalizer import couple_extraction
from sketch2sim.merge_simulation_scripts import merge_basis_and_configuration
from sketch2sim.mixing_points_normalizer import normalize_mixing_points
from sketch2sim.splitting_points_normalizer import normalize_splitting_points
from sketch2sim.utils import (
    build_unit_view_from_structure,
    ensure_dir,
    finish_success,
    handle_runtime_failure_and_fix,
    is_success,
    log_agent_step,
    log_json,
    log_text,
    merge_execute_and_log,
    run_merge_execute_with_runtime_fixer,
    run_stage,
    run_step,
)

PACKAGE_DIR = Path(__file__).resolve().parent
INSTRUCTIONS_DIR = PACKAGE_DIR / "instructions"
DEFAULT_TEMPLATE_PATH = INSTRUCTIONS_DIR / "hysys_template_base.py"
DEFAULT_INSTANTIATION_INSTRUCTIONS = [
    INSTRUCTIONS_DIR / "instantiation_instructions_simple_v5.txt"
]
DEFAULT_CONFIGURATION_INSTRUCTIONS = [
    INSTRUCTIONS_DIR / "configuration_instructions_simple_v5.txt"
]

STEP = {
    "descriptor": "[1/9 Descriptor Agent] Running...",
    "description_validator": "[2/9 Description Validator] Running...",
    "extractor": "[3/9 Extractor] Running...",
    "basis": "[5/9 Basis Agent] Generating Basis Script...",
    "instantiation": "[6/9 Instantiation Agent] Generating Unit Operations + Streams...",
    "normalization": "[7/9 Normalization] Building Unit IN/OUT View...",
    "configuration": "[8/9 Configuration Agent] Connecting Streams...",
}


@dataclass
class PipelineConfig:
    """Everything a run needs. Defaults come from ``sketch2sim.config``."""

    image_path: str
    log_dir: str = "logs"
    output_dir: str = "outputs"

    descriptor_model: str = config.DESCRIPTOR_MODEL
    description_validator_model: str = config.DESCRIPTION_VALIDATOR_MODEL
    extractor_model: str = config.EXTRACTOR_MODEL
    basis_model: str = config.BASIS_MODEL
    instantiation_model: str = config.INSTANTIATION_MODEL
    configuration_model: str = config.CONFIGURATION_MODEL
    fixer_model: str = config.FIXER_MODEL

    template_path: str = str(DEFAULT_TEMPLATE_PATH)
    instantiation_instruction_files: list[str] = field(
        default_factory=lambda: [str(p) for p in DEFAULT_INSTANTIATION_INSTRUCTIONS]
    )
    configuration_instruction_files: list[str] = field(
        default_factory=lambda: [str(p) for p in DEFAULT_CONFIGURATION_INSTRUCTIONS]
    )

    max_runtime_fix_retries: int = 3
    # Pass the sketch to the extractor as well as the description (the paper's
    # Agent A2 is multimodal). Set False to reproduce the text-only release code.
    extractor_uses_image: bool = True
    # Step 9 needs Aspen HYSYS on Windows; set False to stop after merging.
    execute: bool = True

    def models(self) -> dict[str, str]:
        return {
            "descriptor": self.descriptor_model,
            "description_validator": self.description_validator_model,
            "extractor": self.extractor_model,
            "basis": self.basis_model,
            "instantiation": self.instantiation_model,
            "configuration": self.configuration_model,
            "fixer": self.fixer_model,
            "embeddings": config.EMBEDDING_MODEL,
        }


@dataclass
class PipelineResult:
    description: str
    validation: dict[str, Any]
    extraction: dict[str, Any]
    coupled_structure: dict[str, Any]
    unit_view: dict[str, Any]
    basis_path: str
    instantiation_path: str
    configuration_path: str
    combined_path: str
    executed: bool
    final_configuration_path: str | None


def _check_inputs(cfg: PipelineConfig) -> None:
    if not os.path.exists(cfg.image_path):
        raise FileNotFoundError(f"Input image not found: {cfg.image_path}")
    if not os.path.exists(cfg.template_path):
        raise FileNotFoundError(f"HYSYS template not found: {cfg.template_path}")
    for path in (
        cfg.instantiation_instruction_files + cfg.configuration_instruction_files
    ):
        if not os.path.exists(path):
            raise FileNotFoundError(f"Instruction file not found: {path}")


def run_pipeline(cfg: PipelineConfig) -> PipelineResult:
    llm.require_api_key()
    _check_inputs(cfg)
    ensure_dir(cfg.log_dir)
    ensure_dir(cfg.output_dir)
    llm.set_call_log(os.path.join(cfg.log_dir, "llm_calls.jsonl"))
    log_json(
        cfg.log_dir, "step0_run_config.json", {**asdict(cfg), "models": cfg.models()}
    )
    print("Models:", json.dumps(cfg.models(), indent=2))

    # STEP 1 Descriptor
    descriptor_output = run_step(
        STEP["descriptor"],
        build_descriptor_graph(cfg.descriptor_model),
        DescriptorState(image_path=cfg.image_path),
    )
    descriptor_description = (descriptor_output.get("description") or "").strip()
    log_text(cfg.log_dir, "step1_descriptor_result.txt", descriptor_description)
    if not descriptor_description:
        raise RuntimeError("Descriptor returned an empty description.")

    # STEP 2 Description Validator (diagnostic signal; does not alter the text)
    description_validation_output = run_step(
        STEP["description_validator"],
        build_description_validator_graph(cfg.description_validator_model),
        DescriptionValidatorState(description=descriptor_description),
    )
    description_validation = description_validation_output.get("validation") or {}
    log_json(cfg.log_dir, "step2_description_validation.json", description_validation)
    print(f"Description validation status: {description_validation.get('status')}")

    # The validator's lightly cleaned text replaces the description when it asked
    # for a revision (repetitions, contradictions); the original is kept in the logs.
    extractor_text = descriptor_description
    cleaned = (description_validation.get("cleaned_description") or "").strip()
    if description_validation.get("status") == "needs_revision" and cleaned:
        # A validator that over-condenses would drop equipment: only a text of
        # comparable length is trusted.
        if len(cleaned) >= 0.6 * len(descriptor_description):
            extractor_text = cleaned
            log_text(cfg.log_dir, "step2_description_for_extractor.txt", cleaned)
            print("Using the validator's cleaned description for the extractor.")
        else:
            print(
                "Validator asked for a revision but its cleaned text is much "
                "shorter than the description; keeping the original."
            )

    # STEP 3 Extractor
    extractor_output = run_step(
        STEP["extractor"],
        build_extractor_graph(cfg.extractor_model),
        ExtractorState(
            process_text=extractor_text,
            image_path=cfg.image_path if cfg.extractor_uses_image else None,
        ),
    )
    extraction = extractor_output.get("extraction") or {}
    log_json(cfg.log_dir, "step3_extraction.json", extraction)
    if extractor_output.get("error") or "units" not in extraction:
        raise RuntimeError(
            f"Extractor did not return a valid structure: {extractor_output.get('error')}"
        )

    # STEP 4A Mixing points normalization (explicit mixers)
    normalized_structure = normalize_mixing_points(copy.deepcopy(extraction))
    log_json(
        cfg.log_dir, "step4A_mixing_points_normalized.json", normalized_structure or {}
    )

    # STEP 4B Coupling Normalization (condenser / reflux drum / reflux pump /
    # reboiler folded into their column). Runs on the raw topology: a tee
    # inserted after the reflux pump would hide the pump -> column edge.
    coupled_structure = couple_extraction(normalized_structure)
    # Streams dropped with the folded units (utilities, the internal reflux
    # line) are logged on their own; the agents never see them.
    removed_streams = (
        coupled_structure.pop("removed_streams", []) if coupled_structure else []
    )
    log_json(cfg.log_dir, "step4B_coupled_structure.json", coupled_structure or {})
    if removed_streams:
        log_json(cfg.log_dir, "step4B_removed_streams.json", removed_streams)

    # STEP 4C Splitting points normalization (explicit tees after single-outlet
    # units and vessel ports that feed several streams)
    coupled_structure = normalize_splitting_points(coupled_structure)
    structure_json_path = log_json(
        cfg.log_dir, "step4C_splitting_points_normalized.json", coupled_structure or {}
    )

    # STEP 5 Basis
    basis_path = run_stage(
        label=STEP["basis"],
        step="step5_basis",
        graph=build_basis_graph(cfg.basis_model),
        state=BasisState(
            description=descriptor_description,
            template_path=cfg.template_path,
            structure_json=structure_json_path,
            log_dir=cfg.log_dir,
        ),
        prefix="hysys_basis",
        log_dir=cfg.log_dir,
        output_dir=cfg.output_dir,
        log_agent_step_fn=log_agent_step,
    )

    # STEP 6 Instantiation
    instantiation_path = run_stage(
        label=STEP["instantiation"],
        step="step6_instantiation",
        graph=build_instantiation_graph(cfg.instantiation_model),
        state=InstantiationState(
            description=structure_json_path,
            basis_path=basis_path,
            instruction_paths=list(cfg.instantiation_instruction_files),
        ),
        prefix="hysys_instantiation",
        log_dir=cfg.log_dir,
        output_dir=cfg.output_dir,
        log_agent_step_fn=log_agent_step,
    )

    # STEP 7 Unit View Normalization
    print(f"\n{STEP['normalization']}")
    unit_view = build_unit_view_from_structure(coupled_structure or {})
    unit_view_json_path = log_json(cfg.log_dir, "step7_unit_view.json", unit_view)

    # STEP 8 Configuration
    configuration_path = run_stage(
        label=STEP["configuration"],
        step="step8_configuration",
        graph=build_configuration_graph(cfg.configuration_model),
        state=ConfigurationState(
            description=unit_view_json_path,
            instantiation_path=instantiation_path,
            instruction_paths=list(cfg.configuration_instruction_files),
        ),
        prefix="hysys_configuration",
        log_dir=cfg.log_dir,
        output_dir=cfg.output_dir,
        log_agent_step_fn=log_agent_step,
    )

    # STEP 9 Merge + Execute with runtime-driven fixer retries
    combined_path = os.path.join(cfg.output_dir, "hysys_combined_latest.py")
    if not cfg.execute:
        merge_basis_and_configuration(
            basis_path=basis_path,
            configuration_path=configuration_path,
            output_path=combined_path,
        )
        print(
            "\n[9/9 Executor] Skipped (execute=False). "
            f"Run the merged script on a HYSYS machine: {combined_path}"
        )
        final_path: str | None = configuration_path
        executed = False
    else:
        final_path = run_merge_execute_with_runtime_fixer(
            basis_path=basis_path,
            configuration_path=configuration_path,
            combined_path=combined_path,
            executor_graph=build_executor_graph(),
            unit_view_json_str=json.dumps(unit_view, indent=2),
            structure_json_str=json.dumps(coupled_structure or {}, indent=2),
            instantiation_instruction_paths=list(cfg.instantiation_instruction_files),
            configuration_instruction_paths=list(cfg.configuration_instruction_files),
            fixer_model_name=cfg.fixer_model,
            log_dir=cfg.log_dir,
            max_retries=cfg.max_runtime_fix_retries,
            merge_fn=merge_basis_and_configuration,
            merge_execute_and_log=merge_execute_and_log,
            handle_runtime_failure_and_fix=handle_runtime_failure_and_fix,
            fixer_fn=fix_code_file,
            output_dir=cfg.output_dir,
            ExecutorState=ExecutorState,
            is_success=is_success,
            finish_success=finish_success,
        )
        executed = True

    return PipelineResult(
        description=descriptor_description,
        validation=description_validation,
        extraction=extraction,
        coupled_structure=coupled_structure or {},
        unit_view=unit_view,
        basis_path=basis_path,
        instantiation_path=instantiation_path,
        configuration_path=configuration_path,
        combined_path=combined_path,
        executed=executed,
        final_configuration_path=str(final_path) if final_path else None,
    )


def cli(argv: list[str] | None = None) -> PipelineResult:
    """Parse command-line arguments and run the pipeline."""
    for stream in (sys.stdout, sys.stderr):  # Windows consoles with a legacy code page
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(
        description="Sketch2Simulation: process sketch -> Aspen HYSYS script."
    )
    parser.add_argument("image", help="Path to the process sketch (png/jpg).")
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument(
        "--no-execute",
        action="store_true",
        help="Stop after merging the script (no HYSYS needed).",
    )
    parser.add_argument(
        "--text-only-extractor",
        action="store_true",
        help="Do not pass the sketch to the extractor (release-code behaviour).",
    )
    parser.add_argument("--max-retries", type=int, default=3)
    args = parser.parse_args(argv)

    cfg = PipelineConfig(
        image_path=args.image,
        log_dir=args.log_dir,
        output_dir=args.output_dir,
        max_runtime_fix_retries=args.max_retries,
        extractor_uses_image=not args.text_only_extractor,
        execute=not args.no_execute,
    )
    return run_pipeline(cfg)


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point (``sketch2sim ...``): returns a process exit code."""
    cli(argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
