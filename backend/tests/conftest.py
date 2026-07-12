"""Shared fixtures for the backend test suite."""

from __future__ import annotations

import pytest

from app.services import tsdb_client


@pytest.fixture(autouse=True)
def _fast_tsdb_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero out the process-wide TSDB pacing gate.

    The real gate spaces requests ~0.34s apart to be polite to the shared
    free key; in tests that would add real sleeps to every TSDB-touching
    test, so the spacing is removed and the gate reset.
    """
    monkeypatch.setattr(tsdb_client, "_MIN_SPACING_SECONDS", 0.0)
    monkeypatch.setattr(tsdb_client, "_next_allowed", 0.0)
