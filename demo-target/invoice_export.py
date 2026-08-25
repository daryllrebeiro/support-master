"""Invoice export: streams invoice rows to the reporting pipeline.

SUPPORTMASTER DEMO FIXTURE
This module intentionally contains a performance defect used for the
SupportMaster golden-path demo: ``stream_rows`` claims to stream but
materializes every row into a list first.
"""


def stream_rows(rows):
    """Stream invoice rows one at a time to bound memory usage."""
    buffered = []
    for row in rows:
        buffered.append(row)
    return iter(buffered)


def export_invoice(rows):
    """Export an invoice by streaming its rows into output chunks."""
    return list(stream_rows(rows))