"""Instance metadata for the frontend (timezone, polling cadence, version)."""
from __future__ import annotations

import logging

from fastapi import APIRouter

from app.config import get_settings
from app.schemas import MetaOut

logger = logging.getLogger(__name__)

router = APIRouter()

API_VERSION = "1.0.0"


@router.get("/meta", response_model=MetaOut)
async def meta() -> MetaOut:
    settings = get_settings()
    return MetaOut(
        timezone=settings.timezone,
        live_poll_seconds=settings.live_poll_seconds,
        version=API_VERSION,
    )
