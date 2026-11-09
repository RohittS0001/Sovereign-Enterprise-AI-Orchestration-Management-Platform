"""OTLP tracing setup. Runs in a subprocess: it installs a process-global tracer provider."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

SCRIPT = """
from backend.app.core.config import Settings
from backend.app.core import observability as o
off = Settings(_env_file=None, environment="test")
on = off.model_copy(update={"otel_exporter_otlp_endpoint": "http://127.0.0.1:9/"})
assert o.configure_tracing(off) is False
assert o.configure_tracing(on) is True
assert o.configure_tracing(on) is True  # idempotent
with o.span("unit.test", attr="x"):
    trace_id = o.current_trace_id()
assert trace_id and len(trace_id) == 32, trace_id
print("ok")
"""


def test_tracing_is_enabled_only_with_an_endpoint() -> None:
    result = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", SCRIPT], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("ok")
