from sketch2sim.agents.executor_agent import ExecutorState, build_executor_graph
from sketch2sim.utils.agent_utils import invoke_graph
from sketch2sim.utils.logging_utils import build_runtime_issue_packet, is_success


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
