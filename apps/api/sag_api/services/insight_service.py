"""洞察领域逻辑：事件—实体图谱读取（供未来图谱视图与 get_entity 工具）。"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.db.models import Document, Source
from sag_api.sag import EngineManager, EntityInfo
from sag_api.schemas.insight import (
    AssetTypeCountOut,
    EntityOut,
    GraphChunkOut,
    GraphCountsOut,
    GraphDocumentOut,
    GraphEventOut,
    GraphRelationOut,
    ProjectAssetsOut,
    ProjectAssetSummaryOut,
    SourceGraphOut,
)


async def _project_documents(session: AsyncSession, source_id: str) -> tuple[list[Document], dict[str, str]]:
    documents = list(
        (
            await session.execute(
                select(Document).where(Document.source_id == source_id).order_by(Document.created_at.desc())
            )
        ).scalars()
    )
    return documents, {
        str(document.sag_source_id): document.id
        for document in documents
        if document.sag_source_id
    }


async def get_source_asset_summary(session: AsyncSession, source: Source) -> ProjectAssetSummaryOut:
    """返回项目阅读器的精确资产计数与实体类型分布。"""
    documents, article_to_document = await _project_documents(session, source.id)
    article_ids = list(article_to_document)
    if not article_ids:
        return ProjectAssetSummaryOut(
            documents=len(documents), chunks=0, events=0, entities=0, entity_types=[]
        )

    from zleap.sag.db import get_session_factory
    from zleap.sag.db.models import Entity, EventEntity, SourceChunk, SourceEvent

    factory = get_session_factory()
    async with factory() as engine_session:
        chunks = int(
            await engine_session.scalar(
                select(func.count()).select_from(SourceChunk).where(SourceChunk.article_id.in_(article_ids))
            )
            or 0
        )
        active_events = (
            SourceEvent.article_id.in_(article_ids),
            or_(SourceEvent.status.is_(None), SourceEvent.status != "DELETED"),
        )
        events = int(
            await engine_session.scalar(select(func.count()).select_from(SourceEvent).where(*active_events)) or 0
        )
        type_rows = (
            await engine_session.execute(
                select(Entity.type, func.count(func.distinct(Entity.id)))
                .join(EventEntity, EventEntity.entity_id == Entity.id)
                .join(SourceEvent, SourceEvent.id == EventEntity.event_id)
                .where(*active_events)
                .group_by(Entity.type)
                .order_by(func.count(func.distinct(Entity.id)).desc(), Entity.type)
            )
        ).all()
        entity_total = int(sum(int(row[1]) for row in type_rows))
    return ProjectAssetSummaryOut(
        documents=len(documents),
        chunks=chunks,
        events=events,
        entities=entity_total,
        entity_types=[AssetTypeCountOut(type=str(kind or "未分类"), count=int(count)) for kind, count in type_rows],
    )


async def get_source_assets(
    session: AsyncSession,
    source: Source,
    *,
    kind: Literal["chunks", "events", "entities"],
    q: str,
    document_id: str | None,
    entity_types: list[str],
    page: int,
    page_size: int,
) -> ProjectAssetsOut:
    """分页读取项目切片、事件或实体，支持文档范围、全文与实体类型筛选。"""
    _documents, article_to_document = await _project_documents(session, source.id)
    article_ids = list(article_to_document)
    if document_id:
        article_ids = [article_id for article_id, value in article_to_document.items() if value == document_id]
    if not article_ids:
        return ProjectAssetsOut(kind=kind, page=page, page_size=page_size, total=0, items=[])

    from zleap.sag.db import get_session_factory
    from zleap.sag.db.models import Entity, EventEntity, SourceChunk, SourceEvent

    offset = (page - 1) * page_size
    factory = get_session_factory()
    async with factory() as engine_session:
        if kind == "chunks":
            conditions = [SourceChunk.article_id.in_(article_ids)]
            if q:
                pattern = f"%{q}%"
                conditions.append(or_(SourceChunk.heading.ilike(pattern), SourceChunk.content.ilike(pattern)))
            total = int(
                await engine_session.scalar(select(func.count()).select_from(SourceChunk).where(*conditions)) or 0
            )
            event_counts = (
                select(SourceEvent.chunk_id.label("chunk_id"), func.count(SourceEvent.id).label("event_count"))
                .where(
                    SourceEvent.article_id.in_(article_ids),
                    or_(SourceEvent.status.is_(None), SourceEvent.status != "DELETED"),
                )
                .group_by(SourceEvent.chunk_id)
                .subquery()
            )
            rows = (
                await engine_session.execute(
                    select(SourceChunk, func.coalesce(event_counts.c.event_count, 0))
                    .outerjoin(event_counts, event_counts.c.chunk_id == SourceChunk.id)
                    .where(*conditions)
                    .order_by(SourceChunk.article_id, SourceChunk.rank, SourceChunk.id)
                    .offset(offset)
                    .limit(page_size)
                )
            ).all()
            items = [
                {
                    "id": chunk.id,
                    "document_id": article_to_document.get(str(chunk.article_id)),
                    "heading": chunk.heading or "",
                    "content": chunk.content or "",
                    "raw_content": chunk.raw_content or chunk.content or "",
                    "rank": int(chunk.rank or 0),
                    "event_count": int(event_count or 0),
                }
                for chunk, event_count in rows
            ]
        elif kind == "events":
            conditions = [
                SourceEvent.article_id.in_(article_ids),
                or_(SourceEvent.status.is_(None), SourceEvent.status != "DELETED"),
            ]
            if q:
                pattern = f"%{q}%"
                conditions.append(
                    or_(
                        SourceEvent.title.ilike(pattern),
                        SourceEvent.summary.ilike(pattern),
                        SourceEvent.content.ilike(pattern),
                    )
                )
            total = int(
                await engine_session.scalar(select(func.count()).select_from(SourceEvent).where(*conditions)) or 0
            )
            entity_counts = (
                select(EventEntity.event_id.label("event_id"), func.count(EventEntity.entity_id).label("entity_count"))
                .group_by(EventEntity.event_id)
                .subquery()
            )
            rows = (
                await engine_session.execute(
                    select(SourceEvent, func.coalesce(entity_counts.c.entity_count, 0))
                    .outerjoin(entity_counts, entity_counts.c.event_id == SourceEvent.id)
                    .where(*conditions)
                    .order_by(SourceEvent.article_id, SourceEvent.rank, SourceEvent.id)
                    .offset(offset)
                    .limit(page_size)
                )
            ).all()
            items = [
                {
                    "id": event.id,
                    "document_id": article_to_document.get(str(event.article_id)),
                    "chunk_id": event.chunk_id,
                    "title": event.title or "",
                    "summary": event.summary or "",
                    "content": event.content or "",
                    "category": event.category or "",
                    "keywords": list(event.keywords or []),
                    "rank": int(event.rank or 0),
                    "entity_count": int(entity_count or 0),
                }
                for event, entity_count in rows
            ]
        else:
            active_events = (
                SourceEvent.article_id.in_(article_ids),
                or_(SourceEvent.status.is_(None), SourceEvent.status != "DELETED"),
            )
            conditions = list(active_events)
            if q:
                pattern = f"%{q}%"
                conditions.append(or_(Entity.name.ilike(pattern), Entity.description.ilike(pattern)))
            if entity_types:
                conditions.append(Entity.type.in_(entity_types))
            base = (
                select(
                    Entity.id.label("id"),
                    Entity.name.label("name"),
                    Entity.type.label("type"),
                    Entity.description.label("description"),
                    func.count(func.distinct(SourceEvent.id)).label("event_count"),
                )
                .join(EventEntity, EventEntity.entity_id == Entity.id)
                .join(SourceEvent, SourceEvent.id == EventEntity.event_id)
                .where(*conditions)
                .group_by(Entity.id, Entity.name, Entity.type, Entity.description)
            )
            total = int(await engine_session.scalar(select(func.count()).select_from(base.subquery())) or 0)
            rows = (
                await engine_session.execute(
                    base.order_by(func.count(func.distinct(SourceEvent.id)).desc(), Entity.name)
                    .offset(offset)
                    .limit(page_size)
                )
            ).all()
            items = [
                {
                    "id": row.id,
                    "name": row.name or "",
                    "type": row.type or "未分类",
                    "description": row.description or "",
                    "event_count": int(row.event_count or 0),
                }
                for row in rows
            ]
    return ProjectAssetsOut(kind=kind, page=page, page_size=page_size, total=total, items=items)


async def list_entities(
    engine_manager: EngineManager, source: Source, *, types: list[str] | None = None, limit: int = 100
) -> list[EntityInfo]:
    return await engine_manager.list_entities(
        source.sag_source_config_id, source=source, types=types, limit=limit
    )


async def get_source_graph(
    session: AsyncSession,
    engine_manager: EngineManager,
    source: Source,
    *,
    document_limit: int = 1_000,
    event_limit: int = 1_000,
    entity_limit: int = 1_000,
) -> SourceGraphOut:
    """拼装 Web 文档与引擎事件/实体，按调用方给出的性能预算返回图谱。"""
    documents = list(
        (
            await session.execute(
                select(Document)
                .where(Document.source_id == source.id)
                .order_by(Document.created_at.desc())
                .limit(document_limit)
            )
        )
        .scalars()
        .all()
    )
    source_id_to_document_id = {document.sag_source_id: document.id for document in documents if document.sag_source_id}
    document_event_count = sum(max(0, int(document.event_count or 0)) for document in documents)
    graph = await engine_manager.source_graph(
        source.sag_source_config_id,
        list(source_id_to_document_id),
        source=source,
        event_limit=event_limit,
        entity_limit=entity_limit,
        expected_event_count=document_event_count,
    )

    document_nodes = [
        GraphDocumentOut(
            id=document.id,
            filename=document.filename,
            status=document.status.value,
            chunk_count=document.chunk_count,
            event_count=document.event_count,
            created_at=document.created_at,
        )
        for document in documents
    ]
    event_nodes = [
        GraphEventOut(
            id=event.id,
            document_id=source_id_to_document_id.get(event.source_id),
            title=event.title,
            summary=event.summary,
            category=event.category,
            rank=event.rank,
            parent_id=event.parent_id,
            chunk_id=event.chunk_id,
            start_time=event.start_time,
        )
        for event in graph.events
    ]
    entity_nodes = [EntityOut(**entity.model_dump()) for entity in graph.entities]
    selected_chunk_ids = {event.chunk_id for event in event_nodes if event.chunk_id}
    chunk_nodes: list[GraphChunkOut] = []
    if selected_chunk_ids:
        from zleap.sag.db import get_session_factory
        from zleap.sag.db.models import SourceChunk

        factory = get_session_factory()
        async with factory() as engine_session:
            chunks = list(
                (
                    await engine_session.execute(
                        select(SourceChunk)
                        .where(SourceChunk.id.in_(selected_chunk_ids))
                        .order_by(SourceChunk.article_id, SourceChunk.rank, SourceChunk.id)
                    )
                ).scalars()
            )
        event_counts_by_chunk: dict[str, int] = {}
        for event in event_nodes:
            if event.chunk_id:
                event_counts_by_chunk[event.chunk_id] = event_counts_by_chunk.get(event.chunk_id, 0) + 1
        chunk_nodes = [
            GraphChunkOut(
                id=chunk.id,
                document_id=source_id_to_document_id.get(str(chunk.article_id)),
                heading=chunk.heading or "",
                rank=int(chunk.rank or 0),
                event_count=event_counts_by_chunk.get(chunk.id, 0),
            )
            for chunk in chunks
        ]

    selected_event_ids = {event.id for event in event_nodes}
    relations: list[GraphRelationOut] = []
    relation_keys: set[tuple[str, str, str]] = set()

    def add_relation(relation: GraphRelationOut) -> None:
        key = (relation.source_id, relation.target_id, relation.kind)
        if key not in relation_keys:
            relations.append(relation)
            relation_keys.add(key)

    for event in event_nodes:
        if event.parent_id and event.parent_id in selected_event_ids:
            add_relation(
                GraphRelationOut(
                    source_id=event.parent_id,
                    source_kind="event",
                    target_id=event.id,
                    target_kind="event",
                    kind="subevent",
                )
            )
        elif event.document_id:
            add_relation(
                GraphRelationOut(
                    source_id=event.document_id,
                    source_kind="document",
                    target_id=event.id,
                    target_kind="event",
                    kind="contains",
                )
            )
    for association in graph.associations:
        add_relation(
            GraphRelationOut(
                source_id=association.event_id,
                source_kind="event",
                target_id=association.entity_id,
                target_kind="entity",
                kind="mentions",
                weight=association.weight,
                description=association.description,
            )
        )

    counts = GraphCountsOut(
        documents=max(source.document_count, len(document_nodes)),
        # Document checkpoints advance while extraction is still running;
        # Source.event_count is committed only after the whole document. Use
        # the strongest available total so a live graph reports 3 / 73 rather
        # than claiming its current three-node slice is the entire dataset.
        events=max(source.event_count, document_event_count, len(event_nodes)),
        entities=max(graph.total_entities, len(entity_nodes)),
        shown_documents=len(document_nodes),
        shown_events=len(event_nodes),
        shown_entities=len(entity_nodes),
        shown_relations=len(relations),
    )
    return SourceGraphOut(
        documents=document_nodes,
        chunks=chunk_nodes,
        events=event_nodes,
        entities=entity_nodes,
        relations=relations,
        counts=counts,
        truncated=(
            counts.documents > counts.shown_documents
            or counts.events > counts.shown_events
            or counts.entities > counts.shown_entities
        ),
    )
