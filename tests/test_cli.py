"""실제 프로세스 파이프에서 JSONL 프레이밍, 오류 이후 복구, 진단/정리 CLI를 검증한다."""

import json
import subprocess
import sys


def run_cli(*arguments, tmp_path, stdin: str | None = None, timeout: int = 60):
    """소스 트리의 CLI를 별도 프로세스로 실행한다."""
    return subprocess.run([sys.executable, "-m", "aribrowser", "--data-dir", str(tmp_path), *arguments],
                          input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=timeout)


def test_cli_jsonl_recovers_and_closes(tmp_path):
    """손상 JSON/알 수 없는 명령 뒤에도 capabilities/close 응답의 ID를 보존한다."""
    lines = ['not json', '{"id":2,"command":"unknown"}', '{"id":3,"command":"capabilities"}',
             '{"id":4,"command":"close"}']
    result = run_cli(tmp_path=tmp_path, stdin='\n'.join(lines) + '\n', timeout=15)
    assert result.returncode == 0, result.stderr
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(responses) == 4
    assert responses[0]["error"]["code"] == "INVALID_JSON"
    assert responses[1]["error"]["code"] == "UNKNOWN_COMMAND"
    assert responses[2]["id"] == 3 and responses[2]["result"]["version"] == "0.1.0"
    assert responses[3]["id"] == 4 and responses[3]["result"]["closed"] and responses[3]["result"]["processes"]["remaining"] == 0


def test_cli_doctor_and_cleanup(tmp_path):
    """--doctor는 단계별 검사 JSON을, --cleanup은 정리 결과 JSON을 출력한다."""
    result = run_cli("--doctor", "--no-launch", "--no-sandbox", tmp_path=tmp_path)
    report = json.loads(result.stdout)
    assert report["aribrowser"] == "0.1.0" and report["chromium"]["chromium"]
    assert {c["id"] for c in report["checks"]} >= {"user", "display", "chromium"}
    assert "sandbox" not in report  # --no-launch는 실제 실행 검사를 생략한다.
    cleanup = run_cli("--cleanup", tmp_path=tmp_path)
    assert cleanup.returncode == 0 and json.loads(cleanup.stdout)["processes"]["requested"] == 0


def test_cli_install_sandbox_without_root(tmp_path):
    """--install-sandbox는 root가 아니면 정책 본문과 안내를 출력하고, 불필요한 환경이면 그 사실을 알린다."""
    if sys.platform != "linux":
        return
    result = run_cli("--install-sandbox", tmp_path=tmp_path)
    output = json.loads(result.stdout)
    assert output["install"]["status"] in {"needs_root", "not_needed", "installed"}
    assert result.returncode == (2 if output["install"]["status"] == "needs_root" else 0)
    if output["install"]["status"] == "needs_root":
        assert "profile aribrowser-chromium" in output["install"]["profile"] and "userns," in output["install"]["profile"]
