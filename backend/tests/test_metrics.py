"""Prometheus ``/api/metrics`` endpoint tests.

Builds its own FastAPI app (no scheduler, no providers) with sessions
from a local in-memory engine, per ``test_api.py``.  Breaker and
job-registry state is reset around every test so metrics assertions
can't leak across tests (or into other modules).  All fixture names are
fictional.
"""

from __future__ import annotations

import re
from typing import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import get_session
from app.routes import metrics as metrics_route
from app.routes import router as api_router
from app.scheduler import jobs
from app.services import cache, circuit_breaker, metrics_state
from tests.db_engine import create_test_schema, make_test_engine


@pytest.fixture(autouse=True)
def _clean_registries() -> Iterator[None]:
    circuit_breaker.reset_all()
    metrics_state.reset_all()
    yield
    circuit_breaker.reset_all()
    metrics_state.reset_all()


@pytest.fixture
async def db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_test_engine()
    await create_test_schema(engine)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def app(db: async_sessionmaker[AsyncSession]) -> FastAPI:
    application = FastAPI()
    application.include_router(api_router, prefix="/api")

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with db() as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# A sample line: metric name, optional {labels}, one space, a number.
_SAMPLE_RE = re.compile(
    r"^[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^{}]*\})? -?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$"
)

_EXPECTED_FAMILIES = [
    ("sportsdash_database_up", "gauge"),
    ("sportsdash_provider_circuit_state", "gauge"),
    ("sportsdash_provider_consecutive_failures", "gauge"),
    ("sportsdash_provider_last_error_timestamp_seconds", "gauge"),
    ("sportsdash_background_tasks", "gauge"),
    ("sportsdash_cache_hits_total", "counter"),
    ("sportsdash_cache_misses_total", "counter"),
    ("sportsdash_scheduler_job_runs_total", "counter"),
    ("sportsdash_scheduler_job_last_run_timestamp_seconds", "gauge"),
]


async def test_metrics_content_type_and_wellformed_lines(client: AsyncClient) -> None:
    resp = await client.get("/api/metrics")
    assert resp.status_code == 200
    # Exposition format version is part of the content type.
    assert resp.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    body = resp.text
    assert body.endswith("\n")

    lines = body.splitlines()
    for line in lines:
        assert line, "exposition must not contain blank lines"
        if line.startswith("# HELP ") or line.startswith("# TYPE "):
            continue
        assert _SAMPLE_RE.match(line), f"malformed sample line: {line!r}"

    for family, kind in _EXPECTED_FAMILIES:
        assert f"# TYPE {family} {kind}" in lines
        assert any(line.startswith(f"# HELP {family} ") for line in lines)

    # Healthy local app: database up, both registered providers reported
    # closed (never called -> permanently closed, same as /api/health).
    assert "sportsdash_database_up 1" in lines
    assert 'sportsdash_provider_circuit_state{provider="espn"} 0' in lines
    assert 'sportsdash_provider_circuit_state{provider="thesportsdb"} 0' in lines
    assert any(line.startswith("sportsdash_background_tasks ") for line in lines)


async def test_database_up_flips_with_failing_session() -> None:
    """A dead database reads as 0 — and /api/health tells the same story."""
    application = FastAPI()
    application.include_router(api_router, prefix="/api")

    class _BrokenSession:
        async def execute(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("database unplugged")

    async def broken_session() -> AsyncIterator[_BrokenSession]:
        yield _BrokenSession()

    application.dependency_overrides[get_session] = broken_session
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        metrics_resp = await c.get("/api/metrics")
        health_resp = await c.get("/api/health")

    assert metrics_resp.status_code == 200  # degrades, never 500s
    assert "sportsdash_database_up 0" in metrics_resp.text.splitlines()
    assert health_resp.json()["database"] is False


async def test_breaker_state_reflected_and_agrees_with_health(client: AsyncClient) -> None:
    breaker = circuit_breaker.get_breaker("espn")
    for _ in range(breaker.failure_threshold):
        breaker.record_failure("simulated outage")
    assert breaker.state is circuit_breaker.CircuitState.OPEN
    assert breaker.last_error_at is not None

    lines = (await client.get("/api/metrics")).text.splitlines()
    assert 'sportsdash_provider_circuit_state{provider="espn"} 2' in lines
    assert (
        f'sportsdash_provider_consecutive_failures{{provider="espn"}} {breaker.failure_threshold}'
        in lines
    )
    assert (
        'sportsdash_provider_last_error_timestamp_seconds{provider="espn"}'
        f" {breaker.last_error_at.timestamp()}"
    ) in lines
    # thesportsdb never errored: closed/0, and NO last-error series.
    assert 'sportsdash_provider_circuit_state{provider="thesportsdb"} 0' in lines
    assert 'sportsdash_provider_consecutive_failures{provider="thesportsdb"} 0' in lines
    assert not any(
        line.startswith('sportsdash_provider_last_error_timestamp_seconds{provider="thesportsdb"}')
        for line in lines
    )

    # The ROADMAP criterion: /api/health reads the same breaker the same way.
    health = (await client.get("/api/health")).json()
    assert health["provider_health"]["espn"]["circuit"] == "open"
    assert health["status"] == "degraded"

    # Half-open maps to 1.
    breaker.state = circuit_breaker.CircuitState.HALF_OPEN
    lines = (await client.get("/api/metrics")).text.splitlines()
    assert 'sportsdash_provider_circuit_state{provider="espn"} 1' in lines

    # Recovery: closed again, consecutive failures reset to 0.
    breaker.record_success()
    lines = (await client.get("/api/metrics")).text.splitlines()
    assert 'sportsdash_provider_circuit_state{provider="espn"} 0' in lines
    assert 'sportsdash_provider_consecutive_failures{provider="espn"} 0' in lines


async def test_cache_counters_tick(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    hits_before, misses_before = cache.cache_counters()

    # No client configured -> miss.
    monkeypatch.setattr(cache, "_get_client", lambda: None)
    assert await cache.cache_get_json("plum:absent") is None

    class _StubRedis:
        async def get(self, key: str) -> str:
            return '{"flavor": "plum"}'

    monkeypatch.setattr(cache, "_get_client", lambda: _StubRedis())
    assert await cache.cache_get_json("plum:present") == {"flavor": "plum"}

    hits, misses = cache.cache_counters()
    assert hits == hits_before + 1
    assert misses == misses_before + 1

    lines = (await client.get("/api/metrics")).text.splitlines()
    assert f"sportsdash_cache_hits_total {hits}" in lines
    assert f"sportsdash_cache_misses_total {misses}" in lines


async def test_job_registry_ticks_via_wrapper(client: AsyncClient) -> None:
    async def confetti_sweep() -> None:
        return None

    wrapped = metrics_state.instrumented("confetti_sweep", confetti_sweep)

    # Wrapping alone seeds the registry with zero counts and no last-run.
    lines = (await client.get("/api/metrics")).text.splitlines()
    assert 'sportsdash_scheduler_job_runs_total{job="confetti_sweep",result="success"} 0' in lines
    assert 'sportsdash_scheduler_job_runs_total{job="confetti_sweep",result="error"} 0' in lines
    assert not any(
        line.startswith('sportsdash_scheduler_job_last_run_timestamp_seconds{job="confetti_sweep"}')
        for line in lines
    )

    await wrapped()
    await wrapped()

    async def broken_sweep() -> None:
        raise RuntimeError("stray escape")

    wrapped_broken = metrics_state.instrumented("broken_sweep", broken_sweep)
    # Jobs are defensive and shouldn't raise; when one escapes anyway the
    # wrapper counts an error and re-raises for APScheduler's logging.
    with pytest.raises(RuntimeError):
        await wrapped_broken()

    lines = (await client.get("/api/metrics")).text.splitlines()
    assert 'sportsdash_scheduler_job_runs_total{job="confetti_sweep",result="success"} 2' in lines
    assert 'sportsdash_scheduler_job_runs_total{job="confetti_sweep",result="error"} 0' in lines
    assert 'sportsdash_scheduler_job_runs_total{job="broken_sweep",result="success"} 0' in lines
    assert 'sportsdash_scheduler_job_runs_total{job="broken_sweep",result="error"} 1' in lines

    stats = metrics_state.get_job_stats("confetti_sweep")
    assert stats.last_run_at is not None
    assert (
        'sportsdash_scheduler_job_last_run_timestamp_seconds{job="confetti_sweep"}'
        f" {stats.last_run_at.timestamp()}"
    ) in lines


def test_setup_scheduler_seeds_job_registry() -> None:
    """Every scheduled job appears in metrics from startup, not first run."""
    scheduler = jobs.setup_scheduler()
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert job_ids == {
        "daily_refresh",
        "refresh_news",
        "refresh_pregame_rosters",
        "live_tick",
        "events_tick",
    }
    assert set(metrics_state.all_job_stats()) == job_ids


def test_label_escaping() -> None:
    assert metrics_route._escape('pl"um\\jam\nline') == 'pl\\"um\\\\jam\\nline'
