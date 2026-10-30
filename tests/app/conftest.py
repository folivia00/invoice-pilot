"""Fixtures: a migrated SQLite template (migrated once per session) copied per test."""

from pathlib import Path

import pytest
from helpers_app import FakeClock, copy_db, migrate


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("template") / "template.db"
    migrate(path)
    return path


@pytest.fixture
def db_path(migrated_template: Path, tmp_path: Path) -> Path:
    return copy_db(migrated_template, tmp_path / "invoice_pilot.db")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
