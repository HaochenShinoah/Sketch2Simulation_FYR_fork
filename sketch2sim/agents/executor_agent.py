"""Executor (B4): run the generated HYSYS automation script and log the result."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from typing import Any

from langchain_core.runnables import RunnableLambda
from langgraph.graph import StateGraph
from pydantic import BaseModel

from sketch2sim import config


class ExecutorState(BaseModel):
    code_path: str
    log_dir: str = "logs"
    output_dir: str | None = None  # where the generated script saves the .hsc
    result_summary: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int | None = None


def python_executable() -> str:
    """Interpreter used for the generated script (the current venv by default)."""
    return config.PYTHON_EXECUTABLE or sys.executable


def run_executor(state: ExecutorState) -> dict[str, Any]:
    os.makedirs(state.log_dir, exist_ok=True)

    if not os.path.exists(state.code_path):
        raise FileNotFoundError(f"Python script not found: {state.code_path}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(state.log_dir, f"executor_run_{timestamp}.log")
    attempt = 1
    while os.path.exists(log_file):  # retries within one second keep every run log
        attempt += 1
        log_file = os.path.join(
            state.log_dir, f"executor_run_{timestamp}_{attempt}.log"
        )

    print(f"[Executor Agent] Executing script -> {state.code_path}")

    env = dict(os.environ)
    # The child prints stream/unit names that may not fit a legacy code page.
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    if state.output_dir and "S2S_HSC_DIR" not in env:
        env["S2S_HSC_DIR"] = os.path.abspath(state.output_dir)

    try:
        result = subprocess.run(
            [python_executable(), state.code_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.EXECUTOR_TIMEOUT,
            env=env,
        )
    except subprocess.TimeoutExpired:
        summary = (
            f"Timeout while executing {state.code_path} "
            f"(limit {config.EXECUTOR_TIMEOUT}s)"
        )
        with open(log_file, "w", encoding="utf-8") as file:
            file.write(summary)
        return {
            "result_summary": summary,
            "stdout": "",
            "stderr": "TimeoutExpired",
            "exit_code": None,
        }

    with open(log_file, "w", encoding="utf-8") as file:
        file.write("=== STDOUT ===\n")
        file.write(result.stdout or "(none)")
        file.write("\n\n=== STDERR ===\n")
        file.write(result.stderr or "(none)")
        file.write(f"\n\nExit Code: {result.returncode}\n")

    summary = (
        f"HYSYS Executor ran: {os.path.abspath(state.code_path)}\n"
        f"Exit Code: {result.returncode}\n"
        f"Log File:  {os.path.abspath(log_file)}"
    )

    if result.returncode == 0:
        summary += "\nStatus: Success"
    else:
        summary += "\nStatus: Failed (see log)"

    return {
        "result_summary": summary,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.returncode,
    }


def build_executor_graph():
    graph = StateGraph(ExecutorState)
    graph.add_node("executor", RunnableLambda(run_executor))
    graph.set_entry_point("executor")
    graph.set_finish_point("executor")
    return graph.compile()
