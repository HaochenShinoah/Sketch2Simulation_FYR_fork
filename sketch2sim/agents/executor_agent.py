"""Executor (B4): run the generated HYSYS automation script and log the result.

Every attempt runs in a fresh ``AspenHysys.exe`` (the script uses DispatchEx).
The script quits its own instance from an ``atexit`` handler, except after a
success when ``S2S_HYSYS_KEEP_OPEN=1``. The executor records the HYSYS
processes that appeared during an attempt, waits for that instance to exit
before the next attempt starts (an instance that is still open or still
exiting makes the next script attach to it instead of starting its own
process, and ``SimulationCases.Add`` then fails), and kills the instance it
created when the script times out.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from typing import Any

from langchain_core.runnables import RunnableLambda
from langgraph.graph import StateGraph
from pydantic import BaseModel

from sketch2sim import config

HYSYS_EXIT_WAIT_SECONDS = 90


class ExecutorState(BaseModel):
    code_path: str
    log_dir: str = "logs"
    output_dir: str | None = None  # where the generated script saves the .hsc
    attempt: int = 1
    hsc_suffix: str = ""  # appended to the case file name (e.g. "_attempt2")
    result_summary: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int | None = None
    hysys_pids: list[int] = []


def python_executable() -> str:
    """Interpreter used for the generated script (the current venv by default)."""
    return config.PYTHON_EXECUTABLE or sys.executable


def hysys_pids() -> set[int]:
    """PIDs of the running AspenHysys.exe processes (empty where tasklist is absent)."""
    if os.name != "nt":
        return set()
    try:
        raw = subprocess.run(
            [
                "tasklist",
                "/FI",
                "IMAGENAME eq AspenHysys.exe",
                "/FI",
                "STATUS eq RUNNING",  # leftover zombie entries are not running
                "/FO",
                "CSV",
            ],
            capture_output=True,
            timeout=30,
        ).stdout.decode("mbcs", "replace")
    except Exception:
        return set()
    return {
        int(line.split('","')[1])
        for line in raw.splitlines()
        if line.startswith('"AspenHysys.exe"')
    }


def wait_for_exit(pids: set[int], timeout: float = HYSYS_EXIT_WAIT_SECONDS) -> set[int]:
    """Wait until the given HYSYS processes are gone; return the ones still alive."""
    started = time.time()
    alive = pids & hysys_pids()
    while alive and time.time() - started < timeout:
        time.sleep(2)
        alive = pids & hysys_pids()
    return alive


def kill_hysys(pids: set[int]) -> set[int]:
    """Terminate HYSYS processes this executor created (never any other).

    Returns the PIDs that could not be terminated.
    """
    failed: set[int] = set()
    for pid in sorted(pids):
        try:
            if os.name == "nt":
                result = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    message = result.stderr.decode("mbcs", "replace").strip()
                    raise RuntimeError(message or "taskkill failed")
            else:
                os.kill(pid, 9)
            print(f"[Executor Agent] killed AspenHysys.exe pid {pid}")
        except Exception as error:
            failed.add(pid)
            print(f"[Executor Agent] could not kill pid {pid}: {error}")
    return failed


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
    env["S2S_HSC_SUFFIX"] = state.hsc_suffix
    env["S2S_ATTEMPT"] = str(state.attempt)

    pids_before = hysys_pids()
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
        created = hysys_pids() - pids_before
        not_killed = kill_hysys(created)
        summary = (
            f"Timeout while executing {state.code_path} "
            f"(limit {config.EXECUTOR_TIMEOUT}s); killed HYSYS pids "
            f"{sorted(created - not_killed)}"
            + (f"; could not kill {sorted(not_killed)}" if not_killed else "")
        )
        with open(log_file, "w", encoding="utf-8") as file:
            file.write(summary)
        return {
            "result_summary": summary,
            "stdout": "",
            "stderr": "TimeoutExpired",
            "exit_code": None,
            "hysys_pids": sorted(created),
        }

    with open(log_file, "w", encoding="utf-8") as file:
        file.write("=== STDOUT ===\n")
        file.write(result.stdout or "(none)")
        file.write("\n\n=== STDERR ===\n")
        file.write(result.stderr or "(none)")
        file.write(f"\n\nExit Code: {result.returncode}\n")

    created = hysys_pids() - pids_before
    keep_open = result.returncode == 0 and env.get("S2S_HYSYS_KEEP_OPEN", "0") == "1"
    if created and not keep_open:
        # The script's atexit handler quits HYSYS; the next attempt (or the
        # next pipeline run) must not start while that instance is still exiting.
        alive = wait_for_exit(created)
        if alive:
            print(f"[Executor Agent] HYSYS pids {sorted(alive)} did not exit; killing")
            kill_hysys(alive)

    summary = (
        f"HYSYS Executor ran: {os.path.abspath(state.code_path)}\n"
        f"Exit Code: {result.returncode}\n"
        f"Log File:  {os.path.abspath(log_file)}"
    )
    if created:
        summary += f"\nHYSYS PIDs: {sorted(created)}"

    if result.returncode == 0:
        summary += "\nStatus: Success"
    else:
        summary += "\nStatus: Failed (see log)"

    return {
        "result_summary": summary,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.returncode,
        "hysys_pids": sorted(created),
    }


def build_executor_graph():
    graph = StateGraph(ExecutorState)
    graph.add_node("executor", RunnableLambda(run_executor))
    graph.set_entry_point("executor")
    graph.set_finish_point("executor")
    return graph.compile()
