import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


def write_stage_file(code: str, prefix: str, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"{prefix}_{timestamp}.py")
    with open(path, "w", encoding="utf-8") as file:
        file.write(code or "# (empty output)")
    return path


def invoke_graph(graph, state) -> dict[str, Any]:
    output = graph.invoke(state)
    if isinstance(output, dict):
        return output
    if hasattr(output, "model_dump"):
        return output.model_dump()
    return output.__dict__


def run_step(label: str, graph, state) -> dict[str, Any]:
    print(f"\n{label}")
    return invoke_graph(graph, state)


def agent_fields(output: dict) -> tuple[str, str, str]:
    return (
        output.get("python_code", "") or "",
        output.get("raw_output", "") or "",
        output.get("prompt_debug", "") or "",
    )


def read_text_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def run_code_agent(
    *,
    label: str,
    graph,
    state,
    log_dir: str,
    step: str,
    preview_title: str,
    output_prefix: str,
    output_dir: str,
    log_agent_step_fn,
):
    output = run_step(label, graph, state)
    code, raw_output, prompt_debug = agent_fields(output)

    log_agent_step_fn(
        log_dir=log_dir,
        step=step,
        title=preview_title,
        code=code,
        raw=raw_output,
        prompt=prompt_debug if prompt_debug else None,
    )

    path = write_stage_file(code, output_prefix, output_dir)
    return output, code, raw_output, prompt_debug, path


def run_stage(
    *,
    label,
    step,
    graph,
    state,
    prefix,
    log_dir,
    output_dir,
    log_agent_step_fn,
):
    _, _code, _raw_output, _prompt_debug, path = run_code_agent(
        label=label,
        graph=graph,
        state=state,
        log_dir=log_dir,
        step=step,
        preview_title=None,
        output_prefix=prefix,
        output_dir=output_dir,
        log_agent_step_fn=log_agent_step_fn,
    )
    return path


def run_merge_execute_with_runtime_fixer(
    *,
    basis_path: str,
    configuration_path: str,
    combined_path: str,
    executor_graph,
    unit_view_json_str: str,
    instantiation_instruction_paths,
    configuration_instruction_paths,
    fixer_model_name: str,
    log_dir: str,
    max_retries: int,
    merge_fn,
    merge_execute_and_log,
    handle_runtime_failure_and_fix,
    fixer_fn,
    output_dir: str,
    ExecutorState,
    is_success,
    finish_success,
    structure_json_str: str | None = None,
    log_prefix: str = "step9_executor",
    tail_lines: int = 300,
):
    """Step 9: merge, execute, and repair with the Fixer until the script succeeds.

    Every attempt saves its own case (``<case>_attemptN.hsc``) and topology
    report; the successful attempt is copied to ``<case>.hsc`` and the report
    into the log directory.
    """
    merge_kwargs = {
        "basis_path": basis_path,
        "configuration_path": configuration_path,
        "output_path": combined_path,
    }

    base_fixer_kwargs = {
        "instantiation_instruction_paths": instantiation_instruction_paths,
        "configuration_instruction_paths": configuration_instruction_paths,
        "unit_view_json_str": unit_view_json_str,
        "structure_json_str": structure_json_str,
        "model_name": fixer_model_name,
        "temperature": 0.0,
        "log_dir": log_dir,
    }

    current_configuration_path = configuration_path

    for attempt in range(1, max_retries + 1):
        print(f"\n[Executor] Attempt {attempt}/{max_retries}...")
        executor_state = ExecutorState(
            code_path=combined_path,
            log_dir=log_dir,
            output_dir=output_dir,
            attempt=attempt,
            hsc_suffix=f"_attempt{attempt}",
        )

        exec_result, final_summary, _ = merge_execute_and_log(
            merge_fn=merge_fn,
            executor_graph=executor_graph,
            executor_state=executor_state,
            log_dir=log_dir,
            log_prefix=log_prefix,
            merge_kwargs=merge_kwargs,
        )

        if is_success(final_summary):
            kept = _keep_successful_case(exec_result, attempt, log_dir)
            finish_success(log_dir, output_dir, kept)
            return current_configuration_path

        print("\nExecution failed. Calling fixer using runtime failure log...")

        current_configuration_path = handle_runtime_failure_and_fix(
            log_dir=log_dir,
            tail_lines=tail_lines,
            exec_result=exec_result,
            fixer_fn=fixer_fn,
            fixer_kwargs={**base_fixer_kwargs, "code_path": current_configuration_path},
        )

        merge_kwargs["configuration_path"] = current_configuration_path
        print("Retrying execution with fixed code...")

    raise RuntimeError(
        f"HYSYS execution failed after {max_retries} runtime-fixer attempts. "
        f"See logs in: {log_dir} (the case and topology report of every attempt "
        "are kept as *_attemptN.hsc / *_attemptN_topology.json next to the "
        "generated scripts)."
    )


def _keep_successful_case(exec_result: dict, attempt: int, log_dir: str) -> dict:
    """Copy the successful attempt's case to ``<case>.hsc`` and its topology to the logs."""
    kept: dict[str, str] = {}
    match = re.search(r"Simulation saved at: (.+)", exec_result.get("stdout") or "")
    if not match:
        return kept
    source = Path(match.group(1).strip())
    suffix = f"_attempt{attempt}"
    if source.is_file() and source.stem.endswith(suffix):
        target = source.with_name(source.stem[: -len(suffix)] + source.suffix)
        try:
            shutil.copy2(source, target)
            kept["Case"] = str(target)
        except OSError:
            pass
    topology = source.with_name(source.stem + "_topology.json")
    if topology.is_file():
        try:
            shutil.copy2(topology, Path(log_dir) / "step9_topology.json")
            kept["Topology"] = str(Path(log_dir) / "step9_topology.json")
        except OSError:
            pass
    return kept
