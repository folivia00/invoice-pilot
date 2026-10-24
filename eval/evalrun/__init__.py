"""Offline evaluation runner: replay recorded model answers, compare with ground truth, report.

Pipeline: select dataset documents -> run ``services.extraction.extract_invoice``
through a ``RecordingProvider`` (replay by default, record only with ``--live``)
-> compare fields (``compare``) -> aggregate (``metrics``) -> write
``predictions.jsonl``, ``metrics.json`` and ``summary.md`` (``report``).
The methodology is fixed in ``docs/evaluation.md``.
"""

METHODOLOGY_VERSION = "1.0"
