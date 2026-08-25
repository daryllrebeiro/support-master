"""OpenTelemetry span tracing configuration for enterprise APM backends.

When ``GOOGLE_CLOUD_PROJECT`` is set and the optional
``opentelemetry-exporter-gcp-trace`` package is installed, spans are exported
to **Cloud Trace** in addition to the console. Otherwise SupportMaster keeps
the zero-dependency console exporter so local demos never require GCP.
"""

from __future__ import annotations

import os
from typing import Any

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        ConsoleSpanExporter,
        SimpleSpanProcessor,
        SpanExporter,
    )
    from opentelemetry.sdk.resources import Resource
except ImportError:  # pragma: no cover - OTel is an optional dependency
    trace = None
    SpanExporter = None  # type: ignore[assignment,misc]


def build_cloud_trace_exporter(project_id: str) -> Any | None:
    """Return a Cloud Trace exporter when its package is installed.

    Returns ``None`` (caller keeps the console exporter) when the optional
    Google Cloud OTel exporter is not available in this environment.
    """
    try:
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
    except ImportError:
        return None
    return CloudTraceSpanExporter(project_id=project_id)


def setup_otel(
    service_name: str = "supportmaster",
    *,
    exporter: Any | None = None,
) -> Any:
    """Initialize OpenTelemetry tracer provider if available.

    ``exporter`` may be injected directly (used by tests). When omitted, a
    Cloud Trace exporter is attached if ``GOOGLE_CLOUD_PROJECT`` is set and
    the GCP exporter package is installed; the console exporter is always
    attached as a fallback so spans are observable everywhere.
    """
    if trace is None:
        return None

    resource = Resource.create(attributes={"service.name": service_name})
    provider = TracerProvider(resource=resource)

    processor = SimpleSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(processor)

    if exporter is None:
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
        if project_id:
            exporter = build_cloud_trace_exporter(project_id)
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))

    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)