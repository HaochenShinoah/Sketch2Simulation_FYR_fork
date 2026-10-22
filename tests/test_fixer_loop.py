"""Step 9 offline: merge -> execute (COM stub) -> runtime fixer -> retry.

The generated script runs in a subprocess against the fake win32com layer of
test_generated_script_runs; STUB_REJECT makes the first attempt fail, the
(fake) fixer call clears it, and the second attempt succeeds.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sketch2sim import pipeline as pipeline_module
from sketch2sim.agents import fixer_agent
from sketch2sim.pipeline import PipelineConfig, run_pipeline
from tests.test_generated_script_runs import STUB, needs_instructions
from tests.test_offline_pipeline import _instruction_files

# The separator liquid outlet member rejected -> finish_connections() returns
# exit code 2 after the summary (see test_generated_script_runs).
REJECT_SEPARATOR_LIQUID_OUTLET = ["LiquidProduct"]

EXPECTED_FIXER_KWARGS = {
    "code_path",
    "validator_issues",
    "unit_view_json_str",
    "structure_json_str",
    "model_name",
    "temperature",
    "log_dir",
    "instantiation_instruction_paths",
    "configuration_instruction_paths",
}


def _arm_com_stub(monkeypatch, tmp_path: Path, reject: list[str]) -> None:
    """The executor subprocess inherits os.environ, so the stub is injected there."""
    (tmp_path / "sitecustomize.py").write_text(STUB, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("STUB_REJECT", json.dumps(reject))
    monkeypatch.setenv("S2S_STRICT_CONNECTIONS", "1")
    monkeypatch.delenv("S2S_HSC_DIR", raising=False)


def _config(tmp_path: Path, retries: int) -> PipelineConfig:
    inst_files, conf_files = _instruction_files(tmp_path)
    return PipelineConfig(
        image_path="images/cs1.png",
        log_dir=str(tmp_path / "logs"),
        output_dir=str(tmp_path / "outputs"),
        instantiation_instruction_files=inst_files,
        configuration_instruction_files=conf_files,
        execute=True,
        max_runtime_fix_retries=retries,
    )


class _FixerSpy:
    def __init__(self, repair: bool):
        self.repair = repair
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.repair:
            # Stand-in for a fix that makes the next run succeed.
            os.environ["STUB_REJECT"] = "[]"
        return fixer_agent.fix_code_file(**kwargs)


@needs_instructions
def test_runtime_fixer_loop_retries_until_success(
    fake_llm, repo_cwd, tmp_path, monkeypatch
):
    _arm_com_stub(monkeypatch, tmp_path, REJECT_SEPARATOR_LIQUID_OUTLET)
    spy = _FixerSpy(repair=True)
    monkeypatch.setattr(pipeline_module, "fix_code_file", spy)

    result = run_pipeline(_config(tmp_path, retries=3))

    assert result.executed is True
    assert result.final_configuration_path.endswith("_fixed.py")
    assert Path(result.final_configuration_path).is_file()

    # the fixer ran once, with the runtime packet the pipeline builds
    assert len(spy.calls) == 1
    call = spy.calls[0]
    assert set(call) == EXPECTED_FIXER_KWARGS
    assert call["validator_issues"].startswith("=== EXECUTOR RESULT SUMMARY ===")
    assert "Status: Failed" in call["validator_issues"]
    assert "=== LATEST EXECUTOR RUN LOG (TAIL) ===" in call["validator_issues"]
    assert "S8 -> ELECTROSTATIC_SEPARATOR" in call["validator_issues"]
    assert "Exit Code: 2" in call["validator_issues"]
    assert json.loads(call["unit_view_json_str"])["units_unit_view"]
    assert json.loads(call["structure_json_str"])["units"]
    assert Path(call["code_path"]) == Path(result.configuration_path)

    # the fake fixer call went through the LLM layer exactly once
    assert [c["label"] for c in fake_llm.calls].count("fixer") == 1

    logs = tmp_path / "logs"
    assert (logs / "step9_runtime_issues_for_fixer.txt").is_file()
    assert (logs / "step9_latest_executor_run_file_used.txt").is_file()
    assert (logs / "runtime_fixed_code.py").is_file()
    assert len(list(logs.glob("executor_run_*.log"))) == 2  # failed + fixed run
    assert "Status: Success" in (logs / "step9_executor_summary.txt").read_text()
    # every attempt keeps its own case; the successful one is copied to <case>.hsc
    outputs = tmp_path / "outputs"
    assert (outputs / "Crude_Oil_Desalting_attempt1.hsc").is_file()
    assert (outputs / "Crude_Oil_Desalting_attempt2.hsc").is_file()
    assert (outputs / "Crude_Oil_Desalting.hsc").is_file()
    assert (outputs / "Crude_Oil_Desalting_attempt2_topology.json").is_file()
    assert (logs / "step9_topology.json").is_file()

    # the merged script of the last attempt uses the fixed configuration
    combined = Path(result.combined_path).read_text(encoding="utf-8")
    assert "# === Step 6: Connect Streams (Agent 3) ===" in combined


@needs_instructions
def test_runtime_fixer_loop_gives_up_after_max_retries(
    fake_llm, repo_cwd, tmp_path, monkeypatch
):
    _arm_com_stub(monkeypatch, tmp_path, REJECT_SEPARATOR_LIQUID_OUTLET)
    spy = _FixerSpy(repair=False)
    monkeypatch.setattr(pipeline_module, "fix_code_file", spy)

    with pytest.raises(RuntimeError, match="after 2 runtime-fixer attempts"):
        run_pipeline(_config(tmp_path, retries=2))

    assert len(spy.calls) == 2
    # the second attempt was fed the already-fixed file, not the original
    assert str(spy.calls[1]["code_path"]).endswith("_fixed.py")
    assert len(list((tmp_path / "logs").glob("executor_run_*.log"))) == 2


@needs_instructions
def test_fixer_cannot_change_helpers_or_save_step(
    fake_llm, repo_cwd, tmp_path, monkeypatch
):
    """Step 3b and Step 7 are re-inserted from the basis output before every run."""
    _arm_com_stub(monkeypatch, tmp_path, REJECT_SEPARATOR_LIQUID_OUTLET)

    def tampering_fixer(**kwargs):
        os.environ["STUB_REJECT"] = "[]"
        code = Path(kwargs["code_path"]).read_text(encoding="utf-8")
        code = code.replace(
            "def finish_connections(", "def finish_connections_tampered("
        ).replace(
            "# === Step 7: Save Simulation ===",
            "# === Step 7: Save Simulation ===\nprint('TAMPERED')",
        )
        fixed = Path(kwargs["code_path"]).with_name("tampered_fixed.py")
        fixed.write_text(code, encoding="utf-8")
        return fixed

    monkeypatch.setattr(pipeline_module, "fix_code_file", tampering_fixer)
    result = run_pipeline(_config(tmp_path, retries=2))
    combined = Path(result.combined_path).read_text(encoding="utf-8")
    assert "finish_connections_tampered" not in combined
    assert "TAMPERED" not in combined
    assert (
        combined.count("# === Step 3b:") == 1 and combined.count("# === Step 7:") == 1
    )
