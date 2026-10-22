"""Smoke tests: the package imports and exposes a version."""

import invoice_pilot


def test_version() -> None:
    assert invoice_pilot.__version__ == "0.1.0"
