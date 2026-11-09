"""Metrics (Prometheus) and tracing (OpenTelemetry).

Metrics are always collected and exposed at ``/metrics`` (optionally token-protected).
Tracing is off unless ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set; until then every span is a
cheap no-op, so instrumentation can stay in the code unconditionally.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace
from prometheus_client import Counter, Histogram

from backend.app.core.config import Settings
from backend.app.core.logging import get_logger

logger = get_logger(__name__)

_LATENCY_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120)

HTTP_REQUESTS = Counter(
    "sovereign_http_requests_total", "HTTP requests", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "sovereign_http_request_seconds", "HTTP request latency", ["method", "route"],
    buckets=_LATENCY_BUCKETS,
)  # fmt: skip
AGENT_RUNS = Counter("sovereign_agent_runs_total", "Agent runs by outcome", ["status"])
AGENT_RUN_SECONDS = Histogram(
    "sovereign_agent_run_seconds", "Agent run duration", buckets=_LATENCY_BUCKETS
)
TOOL_CALLS = Counter("sovereign_tool_calls_total", "Tool calls", ["tool", "status"])
TOOL_SECONDS = Histogram(
    "sovereign_tool_call_seconds", "Tool call duration", ["tool"], buckets=_LATENCY_BUCKETS
)
LLM_TOKENS = Counter("sovereign_llm_tokens_total", "LLM tokens", ["model", "kind"])
LLM_SECONDS = Histogram(
    "sovereign_llm_request_seconds", "LLM request latency", ["model"], buckets=_LATENCY_BUCKETS
)
RATE_LIMITED = Counter("sovereign_rate_limited_total", "Requests rejected by rate limits")
APPROVALS = Counter("sovereign_approvals_total", "Approval decisions", ["outcome"])

tracer = trace.get_tracer("sovereign")
_tracing_configured = False


def configure_tracing(settings: Settings) -> bool:
    """Install an OTLP exporter once if an endpoint is configured. Returns True if active."""
    global _tracing_configured
    if _tracing_configured or not settings.otel_exporter_otlp_endpoint:
        return _tracing_configured

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": settings.otel_service_name,
                "deployment.environment": settings.environment,
            }
        )
    )
    endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/")
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
    )
    trace.set_tracer_provider(provider)
    _tracing_configured = True
    logger.info("OpenTelemetry tracing enabled", extra={"endpoint": endpoint})
    return True


@contextmanager
def span(name: str, **attributes: object) -> Iterator[trace.Span]:
    with tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(
                    key, value if isinstance(value, bool | int | float | str) else str(value)
                )
        yield current


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None
