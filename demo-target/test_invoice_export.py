"""Regression tests for invoice export streaming behavior.

SUPPORTMASTER DEMO FIXTURE
These tests fail against the intentionally buggy ``stream_rows`` and pass
once the export pipeline streams lazily. Uses stdlib unittest only.
"""

import inspect
import unittest

from invoice_export import export_invoice, stream_rows


class InvoiceExportStreamingTests(unittest.TestCase):
    def test_stream_rows_is_a_generator_function(self):
        self.assertTrue(
            inspect.isgeneratorfunction(stream_rows),
            "stream_rows must stream lazily, not materialize a list",
        )

    def test_stream_rows_yields_without_full_materialization(self):
        def million_rows():
            for index in range(1_000_000):
                yield f"row-{index}"

        streamed = stream_rows(million_rows())
        self.assertEqual(next(streamed), "row-0")

    def test_export_invoice_preserves_order(self):
        rows = ["a", "b", "c"]
        self.assertEqual(export_invoice(iter(rows)), ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()