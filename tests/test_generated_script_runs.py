"""Execute the generated HYSYS script against a fake COM layer.

This does not prove the COM member names are right (only HYSYS can), but it
proves the helper layer, the agents' output format and the template splice
produce a script that runs end to end, reports failures and exits correctly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from sketch2sim.pipeline import PipelineConfig, run_pipeline
from tests.conftest import REPO_ROOT

INSTRUCTIONS_DIR = REPO_ROOT / "sketch2sim" / "instructions"

# A permissive stand-in for win32com: every attribute exists, every call works,
# except members listed in STUB_REJECT (environment variable), which raise like
# a COM "Member not found" error.
STUB = textwrap.dedent(
    """
    import json, os, sys, types

    REJECT = set(json.loads(os.environ.get("STUB_REJECT", "[]")))

    class ComError(Exception):
        pass

    class Obj:
        def __init__(self, path="HYSYS"):
            object.__setattr__(self, "_path", path)
            object.__setattr__(self, "_children", {})
            object.__setattr__(self, "name", path.rsplit(":", 1)[-1])
        def __getattr__(self, item):
            if item.startswith("_"):
                raise AttributeError(item)
            if item in REJECT:
                raise ComError(f"(-2147352573, 'Member not found.', None, None) {item}")
            children = object.__getattribute__(self, "_children")
            if item not in children:
                children[item] = Obj(f"{self._path}.{item}")
            return children[item]
        def __setattr__(self, item, value):
            if item in REJECT:
                raise ComError(f"(-2147352573, 'Member not found.', None, None) {item}")
            object.__setattr__(self, item, value)
        def __call__(self, *args, **kwargs):
            leaf = self._path.rsplit(".", 1)[-1]
            if leaf == "Add":
                name = args[0] if args else "obj"
                return Obj(f"{self._path}:{name}")
            if leaf == "Item":
                return Obj(f"{self._path}:{args[0]}")
            return Obj(f"{self._path}()")
        def __iter__(self):
            return iter([])
        def __int__(self):
            return 10

    def Dispatch(progid):
        return Obj(progid)

    module = types.ModuleType("win32com.client")
    module.Dispatch = Dispatch
    package = types.ModuleType("win32com")
    package.client = module
    sys.modules["win32com"] = package
    sys.modules["win32com.client"] = module
    """
)


def _run_script(
    script: Path, workdir: Path, reject: list[str] | None = None, strict: bool = True
):
    (workdir / "sitecustomize.py").write_text(STUB, encoding="utf-8")
    env = {
        **os.environ,
        "PYTHONPATH": str(workdir),
        "STUB_REJECT": json.dumps(reject or []),
        "S2S_HSC_DIR": str(workdir / "hsc"),
        "S2S_STRICT_CONNECTIONS": "1" if strict else "0",
    }
    return subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        env=env,
        cwd=workdir,
        timeout=120,
    )


def _generate(fake_llm, tmp_path: Path, case: str) -> Path:
    fake_llm.case = case
    cfg = PipelineConfig(
        image_path="images/cs1.png",
        log_dir=str(tmp_path / f"logs_{case}"),
        output_dir=str(tmp_path / f"outputs_{case}"),
        execute=False,
    )
    return Path(run_pipeline(cfg).combined_path)


needs_instructions = pytest.mark.skipif(
    not (INSTRUCTIONS_DIR / "instantiation_instructions_simple_v5.txt").exists(),
    reason="instruction files not present",
)


@needs_instructions
@pytest.mark.parametrize("case", ["cs1", "cs2", "cs3", "cs4"])
def test_generated_script_runs_against_com_stub(fake_llm, repo_cwd, tmp_path, case):
    script = _generate(fake_llm, tmp_path, case)
    result = _run_script(script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Simulation saved at:" in result.stdout
    assert "[summary] all listed connections attached" in result.stdout
    assert "WARNING" not in result.stdout
    if case == "cs3":
        # When HYSYS offers no side-draw member, the side cuts are reported as a
        # known limitation (NOTE), never as a failure: exit code stays 0.
        result = _run_script(
            script,
            tmp_path,
            reject=["AddLiquidDraw", "AddDrawStream", "AddProductStream"],
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.count("NOTE side draw") == 3  # Kero, Diesel, AGO
        assert "[summary] all listed connections attached" in result.stdout


@needs_instructions
def test_generated_script_reports_failed_connections(fake_llm, repo_cwd, tmp_path):
    script = _generate(fake_llm, tmp_path, "cs1")
    # Pumps: FeedStream rejected -> falls back to Feeds.Add (still OK).
    result = _run_script(script, tmp_path, reject=["FeedStream"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CRUDE_OIL_PUMP.Feeds.Add <- S1" in result.stdout
    # Every candidate for the separator liquid outlet rejected -> exit 2 after saving.
    reject = [
        "LiquidProduct",
        "LiquidOutlet",
        "BottomsProduct",
        "Products",
        "AttachedProducts",
        "ProductStream",
    ]
    result = _run_script(script, tmp_path, reject=reject)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Simulation saved at:" in result.stdout
    assert "S8 -> ELECTROSTATIC_SEPARATOR" in result.stdout
    # Non-strict mode keeps exit code 0 but still prints the failure.
    result = _run_script(script, tmp_path, reject=reject, strict=False)
    assert result.returncode == 0
    assert "connection(s) FAILED" in result.stdout


@needs_instructions
def test_unit_creation_failure_is_fatal(fake_llm, repo_cwd, tmp_path):
    script = _generate(fake_llm, tmp_path, "cs1")
    result = _run_script(script, tmp_path, reject=["Operations"])
    assert result.returncode == 1
    assert "Could not create unit" in result.stderr
