import json
import os
from pathlib import Path


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def log_text(log_dir: str, filename: str, content: str) -> str:
    ensure_dir(log_dir)
    full_path = os.path.join(log_dir, filename)
    with open(full_path, "w", encoding="utf-8") as file:
        file.write(content if content else "")
    return full_path


def log_json(log_dir: str, filename: str, data: dict) -> str:
    ensure_dir(log_dir)
    full_path = os.path.join(log_dir, filename)
    with open(full_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
    return full_path


def log_prompt(log_dir: str, filename: str, system_prompt: str, human_prompt: str):
    ensure_dir(log_dir)

    text = (
        "=== SYSTEM PROMPT ===\n"
        f"{system_prompt}\n\n"
        "=== HUMAN PROMPT ===\n"
        f"{human_prompt}\n"
    )

    path = os.path.join(log_dir, filename)
    with open(path, "w", encoding="utf-8") as file:
        file.write(text)

    return path


def log_agent_step(
    *,
    log_dir: str,
    step: str,
    title: str,
    code: str | None = None,
    raw: str | None = None,
    prompt: str | None = None,
):
    if code is not None:
        log_text(log_dir, f"{step}_latest_code.py", code)

    if raw is not None:
        log_text(log_dir, f"{step}_raw_output.txt", raw)

    if prompt is not None:
        log_text(log_dir, f"{step}_prompt_debug.txt", prompt)


def log_executor_result(
    log_dir: str, prefix: str, exec_result: dict
) -> tuple[str, str]:
    summary = exec_result.get("result_summary", "") or ""
    stderr = exec_result.get("stderr", "") or ""

    if summary:
        log_text(log_dir, f"{prefix}_summary.txt", summary)
    if stderr:
        log_text(log_dir, f"{prefix}_stderr.txt", stderr)

    return summary, stderr


def get_latest_executor_log_tail(log_dir: str, tail_lines: int) -> tuple[str, str]:
    directory = Path(log_dir)
    patterns = ("executor_run_*.log", "executor_run_*", "executor_run_*.txt")

    files = []
    for pattern in patterns:
        files += list(directory.glob(pattern))

    files = [file for file in files if file.is_file()]
    if not files:
        return "", ""

    files.sort(key=lambda file: file.stat().st_mtime, reverse=True)
    latest = files[0]

    text = latest.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    tail = "\n".join(lines[-tail_lines:]) if len(lines) > tail_lines else text

    return str(latest), tail


def build_runtime_issue_packet(exec_result: dict, latest_exec_file_text: str) -> str:
    summary = (exec_result.get("result_summary", "") or "").strip()
    stderr = (exec_result.get("stderr", "") or "").strip()

    parts = []
    if summary:
        parts.append("=== EXECUTOR RESULT SUMMARY ===\n" + summary)
    if stderr:
        parts.append("=== EXECUTOR STDERR ===\n" + stderr)
    if latest_exec_file_text:
        parts.append("=== LATEST EXECUTOR RUN LOG (TAIL) ===\n" + latest_exec_file_text)

    return "\n\n".join(parts).strip()


def is_success(summary: str) -> bool:
    return "Status: Success" in (summary or "")


def finish_success(log_dir: str, output_dir: str):
    print("\nFinal execution succeeded.")
    print("\nPipeline complete.")
    print("Logs stored in:", log_dir)
    print("Outputs in:", output_dir)
