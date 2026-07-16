from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.db import get_session
from sag_api.core.deps import get_current_user, get_engine_manager
from sag_api.db.models import User
from sag_api.sag import EngineManager
from sag_api.schemas.insight import EntityOut, ProjectAssetsOut, ProjectAssetSummaryOut, SourceGraphOut
from sag_api.services.insight_service import (
    get_source_asset_summary,
    get_source_assets,
    get_source_graph,
    list_entities,
)
from sag_api.services.source_service import get_source

router = APIRouter(prefix="/sources/{source_id}", tags=["insights"])


@router.get("/assets/summary", response_model=ProjectAssetSummaryOut)
async def asset_summary(
    source_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProjectAssetSummaryOut:
    source = await get_source(session, source_id)
    return await get_source_asset_summary(session, source)


@router.get("/assets", response_model=ProjectAssetsOut)
async def assets(
    source_id: str,
    kind: Literal["chunks", "events", "entities"] = "chunks",
    q: str = "",
    document_id: str | None = None,
    entity_types: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=200),
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProjectAssetsOut:
    source = await get_source(session, source_id)
    types = [value.strip() for value in (entity_types or "").split(",") if value.strip()]
    return await get_source_assets(
        session,
        source,
        kind=kind,
        q=q.strip(),
        document_id=document_id,
        entity_types=types,
        page=page,
        page_size=page_size,
    )


@router.get("/entities", response_model=list[EntityOut])
async def entities(
    source_id: str,
    types: str | None = None,
    limit: int = 100,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
) -> list[EntityOut]:
    source = await get_source(session, source_id)
    type_list = [t for t in (types or "").split(",") if t] or None
    ents = await list_entities(engine_manager, source, types=type_list, limit=limit)
    return [EntityOut(**e.model_dump()) for e in ents]


@router.get("/graph", response_model=SourceGraphOut)
async def graph(
    source_id: str,
    document_limit: int = Query(default=1_000, ge=1, le=10_000),
    event_limit: int = Query(default=1_000, ge=1, le=10_000),
    entity_limit: int = Query(default=1_000, ge=1, le=10_000),
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
) -> SourceGraphOut:
    source = await get_source(session, source_id)
    return await get_source_graph(
        session,
        engine_manager,
        source,
        document_limit=document_limit,
        event_limit=event_limit,
        entity_limit=entity_limit,
    )
