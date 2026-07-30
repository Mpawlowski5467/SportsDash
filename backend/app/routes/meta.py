"""Instance metadata for the frontend (timezone, polling cadence, version)."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.config import get_settings
from app.schemas import MetaOut

logger = logging.getLogger(__name__)

router = APIRouter()

# The shipped app version, and the backend's single source of truth for it:
# ``app.main`` builds ``FastAPI(version=...)`` from this constant instead of
# repeating the literal, so /api/meta, /docs and the OpenAPI schema can never
# disagree.  Bump it here at release, in step with the desktop bundle version
# in ``frontend/src-tauri/tauri.conf.json`` (tests/test_api.py pins the two).
APP_VERSION = "1.4.0"


@router.get("/meta", response_model=MetaOut)
async def meta() -> MetaOut:
    settings = get_settings()
    return MetaOut(
        timezone=settings.timezone,
        live_poll_seconds=settings.live_poll_seconds,
        version=APP_VERSION,
    )
