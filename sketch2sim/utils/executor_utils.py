from typing import Any

from sketch2sim.utils.agent_utils import invoke_graph
from sketch2sim.utils.logging_utils import (
    build_runtime_issue_packet,
    get_latest_executor_log_tail,
    log_executor_result,
    log_text,
)


def execute_and_log(
    *,
    executor_graph,
    executor_state,
    log_dir: str,
    log_prefix: str,
) -> tuple[dict[str, Any], str, str]:
    """
    Execute and log summary/stderr.

    Returns:
      (exec_result_dict, summary, stderr)
    """
    exec_result = invoke_graph(executor_graph, executor_state)
    summary, stderr = log_executor_result(log_dir, log_prefix, exec_result)
    return exec_result, summary, stderr


def merge_execute_and_log(
    *,
    merge_fn,
    executor_graph,
    executor_state,
    log_dir: str,
    log_prefix: str,
    merge_kwargs: dict[str, Any],
) -> tuple[dict[str, Any], str, str]:
    """
    Merge, execute, and log summary/stderr.

    Returns:
      (exec_result_dict, summary, stderr)
    """
    merge_fn(**merge_kwargs)
    exec_result = invoke_graph(executor_graph, executor_state)
    summary, stderr = log_executor_result(log_dir, log_prefix, exec_result)
    return exec_result, summary, stderr


def handle_runtime_failure_and_fix(
    *,
    log_dir: str,
    tail_lines: int,
    exec_result: dict,
    fixer_fn,
    fixer_kwargs: dict,
) -> str:
    """
    Handle a failed execution:
      - collect latest executor log tail
      - build runtime issue packet
      - write logs
      - call fixer

    Returns:
      new_code_path
    """
    latest_path, runtime_tail = get_latest_executor_log_tail(log_dir, tail_lines)
    if latest_path:
        log_text(log_dir, "step9_latest_executor_run_file_used.txt", latest_path)

    runtime_issues = build_runtime_issue_packet(exec_result, runtime_tail)
    log_text(log_dir, "step9_runtime_issues_for_fixer.txt", runtime_issues)

    return fixer_fn(validator_issues=runtime_issues, **fixer_kwargs)
