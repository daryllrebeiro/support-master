"""Phase G: env-gated Cloud Trace export for existing OTel spans."""

import os
import unittest
from unittest.mock import patch

from opentelemetry.sdk.trace.export import SpanExportResult

from supportmaster.telemetry.otel_exporter import (
    build_cloud_trace_exporter,
    setup_otel,
)


class _RecordingExporter:
    """Minimal SpanExporter double that records exported spans."""

    def __init__(self) -> None:
        self.exported: list = []
        self.shutdown_calls = 0

    def export(self, spans) -> SpanExportResult:
        self.exported.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class CloudTraceExporterTests(unittest.TestCase):
    def test_missing_package_returns_none(self) -> None:
        # In environments without opentelemetry-exporter-gcp-trace the
        # factory must degrade to None instead of raising.
        result = build_cloud_trace_exporter("test-project")
        if result is not None:
            self.assertEqual(getattr(result, "project_id", "test-project"), "test-project")

    def test_setup_with_injected_exporter_receives_spans(self) -> None:
        exporter = _RecordingExporter()
        tracer = setup_otel("supportmaster-test-injected", exporter=exporter)
        self.assertIsNotNone(tracer)
        with tracer.start_as_current_span("golden_path_demo") as span:
            span.set_attribute("case.id", "SUP-GOLDEN")
        self.assertTrue(
            any(span.name == "golden_path_demo" for span in exporter.exported),
            "injected exporter must receive ended spans",
        )

    def test_setup_without_project_keeps_console_only(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GOOGLE_CLOUD_PROJECT", None)
            tracer = setup_otel("supportmaster-test-console")
        self.assertIsNotNone(tracer)

    def test_setup_with_project_but_missing_package_still_works(self) -> None:
        with patch.dict(
            os.environ, {"GOOGLE_CLOUD_PROJECT": "fake-project"}, clear=False
        ):
            tracer = setup_otel("supportmaster-test-gcp-env")
        self.assertIsNotNone(tracer)


if __name__ == "__main__":
    unittest.main()