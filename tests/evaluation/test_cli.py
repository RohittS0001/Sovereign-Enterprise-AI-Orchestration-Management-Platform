"""`python -m evaluation run` writes reports and exits by the suite thresholds."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_cli_runs_a_suite_and_writes_reports(tmp_path) -> None:
    result = subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-m", "evaluation", "run", "--suite", "guardrails", "--out", str(tmp_path)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )  # fmt: skip

    assert result.returncode == 0, result.stdout + result.stderr
    reports = {p.suffix: p for p in tmp_path.iterdir()}
    assert set(reports) == {".json", ".md"}
    data = json.loads(reports[".json"].read_text(encoding="utf-8"))
    assert [s["suite"] for s in data] == ["guardrails"] and data[0]["passed"] is True
