from sketch2sim.agents.executor_agent import ExecutorState, build_executor_graph
from sketch2sim.merge_simulation_scripts import merge_script_texts
from sketch2sim.utils.agent_utils import invoke_graph
from sketch2sim.utils.logging_utils import (
    build_runtime_issue_packet,
    compact_executor_log,
    is_success,
)


def test_compact_executor_log_keeps_what_the_fixer_needs():
    lines = [f"[stream] S{i} = 'stream {i}'" for i in range(100)]
    lines += ["[connect] WARNING S9 NOT attached to V-1: ['LiquidProduct: boom']"]
    lines += [f"[connect] V-1.Feeds.Add <- S{i}" for i in range(50)]
    lines += [
        "[summary] 1 connection(s) FAILED:",
        "  - S9 -> V-1: boom",
        "Exit Code: 2",
    ]
    text = "\n".join(lines)
    compact = compact_executor_log(text, keep_last=10)
    assert "WARNING S9 NOT attached" in compact
    assert "  - S9 -> V-1: boom" in compact and "Exit Code: 2" in compact
    assert "[stream] S1 = " not in compact
    assert len(compact.splitlines()) < 20


def test_merge_takes_head_helpers_and_save_step_from_the_basis():
    basis = (
        "head\n# === Step 1: launch\nLAUNCH\n# === Step 2: basis\nBASIS\n"
        "# === Step 3b: x\nHELPERS\n# === Step 4: a\nU\n# === Step 7: s\nSAVE\n"
    )
    fixed = (
        "evil head\n# === Step 1: launch\nEVIL LAUNCH\n# === Step 2: basis\nFIXED BASIS\n"
        "# === Step 3b: x\nEVIL\n# === Step 4: a\nFIXED UNITS\n"
        "# === Step 6: c\nFIXED LINKS\n# === Step 7: s\nEVIL SAVE\n"
    )
    merged = merge_script_texts(basis, fixed)
    assert merged == (
        "head\n# === Step 1: launch\nLAUNCH\n# === Step 2: basis\nFIXED BASIS\n"
        "# === Step 3b: x\nHELPERS\n# === Step 4: a\nFIXED UNITS\n"
        "# === Step 6: c\nFIXED LINKS\n# === Step 7: s\nSAVE\n"
    )
    import pytest

    with pytest.raises(ValueError, match="exactly once"):
        merge_script_texts(basis, fixed.replace("# === Step 7: s\n", ""))


def test_wait_for_exit_and_kill_use_only_the_given_pids(monkeypatch):
    from sketch2sim.agents import executor_agent

    alive = {111, 222}
    monkeypatch.setattr(executor_agent, "hysys_pids", lambda: set(alive))
    monkeypatch.setattr(executor_agent.time, "sleep", lambda s: alive.discard(222))
    assert executor_agent.wait_for_exit({222, 333}, timeout=5) == set()
    assert executor_agent.wait_for_exit({111}, timeout=0) == {111}

    calls = []

    class Result:
        def __init__(self, rc):
            self.returncode, self.stderr = rc, b"denied"

    def fake_run(args, **kwargs):
        calls.append(args)
        return Result(1 if args[2] == "111" else 0)

    monkeypatch.setattr(executor_agent.subprocess, "run", fake_run)
    monkeypatch.setattr(executor_agent.os, "name", "nt")
    assert executor_agent.kill_hysys({111, 222}) == {111}
    assert [c[2] for c in calls] == ["111", "222"]


def test_executor_success_and_failure(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text("print('hello')\n", encoding="utf-8")
    bad = tmp_path / "bad.py"
    bad.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    graph = build_executor_graph()

    out = invoke_graph(
        graph, ExecutorState(code_path=str(ok), log_dir=str(tmp_path / "logs"))
    )
    assert is_success(out["result_summary"])
    assert out["exit_code"] == 0 and "hello" in out["stdout"]

    out = invoke_graph(
        graph, ExecutorState(code_path=str(bad), log_dir=str(tmp_path / "logs"))
    )
    assert not is_success(out["result_summary"])
    assert out["exit_code"] == 1 and "boom" in out["stderr"]
    packet = build_runtime_issue_packet(out, "tail")
    assert "RuntimeError: boom" in packet and "=== EXECUTOR STDERR ===" in packet
