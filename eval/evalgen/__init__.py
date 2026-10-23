"""Synthetic invoice dataset generator for evaluation (dev tooling, not part of the bot).

Pipeline: plan (composition) -> content (domain Invoice + printed strings) ->
layout (one list of drawing operations per page) -> renderers (text PDF via
reportlab, raster via Pillow for scans and photos) -> dataset files, ground
truth JSON and manifest.
"""

DATASET_VERSION = "1.0"
REFERENCE_DATE = "2026-10-01"
