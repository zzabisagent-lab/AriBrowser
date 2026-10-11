"""실제 프로세스 파이프에서 JSONL 프레이밍과 오류 이후 복구를 검증한다."""

import json
import subprocess
import sys


def test_cli_jsonl_recovers_and_closes(tmp_path):
    """손상 JSON/알 수 없는 명령 뒤에도 capabilities/close 응답의 ID를 보존한다."""
    lines = ['not json', '{"id":2,"command":"unknown"}', '{"id":3,"command":"capabilities"}',
             '{"id":4,"command":"close"}']
    result = subprocess.run([sys.executable, "-m", "aribrowser", "--data-dir", str(tmp_path)],
                            input='\n'.join(lines) + '\n', capture_output=True, text=True,
                            encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(responses) == 4
    assert responses[0]["error"]["code"] == "INVALID_JSON"
    assert responses[1]["error"]["code"] == "UNKNOWN_COMMAND"
    assert responses[2]["id"] == 3 and responses[2]["result"]["version"] == "0.0.1"
    assert responses[3]["id"] == 4 and responses[3]["result"]["closed"]
