"""Shared test fixtures.

Every test runs against an isolated data directory: no state leaks between
suites, and nothing touches the developer's ``var/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kyrx.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings pointing at a throwaway data directory.

    Fully protected ("fleet"): most suites exercise the rate limit, the audit
    file and the thread hop. The adaptive default has its own suite.
    """
    return Settings(data_dir=tmp_path / "var", mode="fleet")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Path of a throwaway data directory."""
    return tmp_path / "var"
