"""Instance metadata for the frontend (timezone, polling cadence, version)."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.config import get_settings
from app.schemas import MetaOut

# The shipped app version, re-exported so this module stays the name
# everything imports: ``app.main`` builds ``FastAPI(version=...)`` from it
# instead of repeating the literal, so /api/meta, /docs and the OpenAPI
# schema can never disagree.  Bump it in ``app/version.py`` at release, in
# step with the desktop bundle version in
# ``frontend/src-tauri/tauri.conf.json`` (tests/test_api.py pins the two).
# The constant lives in that leaf module so ``app.useragent`` can stamp the
# version into outbound requests without importing the whole route package.
from app.version import APP_VERSION as APP_VERSION

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/meta", response_model=MetaOut)
async def meta() -> MetaOut:
    settings = get_settings()
    return MetaOut(
        timezone=settings.timezone,
        live_poll_seconds=settings.live_poll_seconds,
        version=APP_VERSION,
    )
