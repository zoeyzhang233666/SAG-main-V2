"""ChemClaw 结构化导入、导出与项目备份服务。"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
import uuid
import zipfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.errors import ValidationError
from sag_api.core.logging import get_logger
from sag_api.db.models import Document, Source
from sag_api.enums import DocumentStatus
from sag_api.jobs import JobQueue
from sag_api.sag import EngineManager
from sag_api.services.legacy_compat import StructuredFormat, parse_legacy_import
from sag_api.services.source_service import get_source

log = get_logger("services.structured_data")

ExportFormat = Literal["json", "csv", "markdown"]
ExportProfile = Literal["simple", "full"]


def _id() -> str:
    return uuid.uuid4().hex


def _safe_filename(filename: str) -> str:
    value = os.path.basename(filename).strip() or "structured-import.json"
    return re.sub(r"[^\w.\- ()\u4e00-\u9fff]", "_", value)[:240]


def _legacy_meta(kind: str, raw: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """统一的旧字段信封；raw 保证未知 V1 扩展字段也不会丢。"""
    return {
        "chemclaw_legacy": {
            "schemaVersion": 1,
            "kind": kind,
            "raw": raw,
            **fields,
        }
    }


async def import_structured_data(
    session: AsyncSession,
    source_id: str,
    *,
    filename: str,
    content: bytes,
    requested_format: StructuredFormat,
    engine_manager: EngineManager,
    job_queue: JobQueue | None,
    index_vectors: bool = True,
    on_progress: Callable[[float], Awaitable[None]] | None = None,
    import_job_id: str | None = None,
) -> dict[str, Any]:
    if len(content) > settings.structured_import_max_mb * 1024 * 1024:
        raise ValidationError(f"结构化数据文件超过 {settings.structured_import_max_mb}MB 上限")
    source = await get_source(session, source_id)
    payload = await asyncio.to_thread(
        parse_legacy_import, content, filename=filename, requested_format=requested_format,
    )
    batch_id = import_job_id or _id()
    safe_name = _safe_filename(filename)
    destination = Path(settings.upload_dir) / source.id / "structured" / batch_id / safe_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(destination.write_bytes, content)

    counts = {"documents": 0, "chunks": 0, "events": 0, "entities": 0, "relations": 0}
    warnings: list[str] = []
    imported: list[dict[str, Any]] = []
    article_ids: list[str] = []

    # EngineManager 的锁让本次直接写入与 V2 搜索/图谱读取保持同源串行，
    # 核心 DataEngine 不需修改。
    async with engine_manager.use_mutation(source.sag_source_config_id, source):
        from zleap.sag.db import get_session_factory
        from zleap.sag.db.models import (
            Article,
            ArticleParseStatus,
            Entity,
            EntityType,
            EventEntity,
            SourceChunk,
            SourceEvent,
        )

        from sag_api.db.models import Job
        from sag_api.enums import JobType
        from sag_api.services.document_service import refresh_source_document_counts

        factory = get_session_factory()
        async with factory() as engine_session:
            existing_types = list(
                (
                    await engine_session.execute(
                        select(EntityType).where(
                            EntityType.source_config_id == source.sag_source_config_id,
                            EntityType.scope == "source",
                        )
                    )
                ).scalars()
            )
            entity_types = {item.type.casefold(): item for item in existing_types}
            existing_entities = list(
                (
                    await engine_session.execute(
                        select(Entity).where(Entity.source_config_id == source.sag_source_config_id)
                    )
                ).scalars()
            )
            entities = {(item.type.casefold(), item.normalized_name.casefold()): item for item in existing_entities}

            total_documents = max(1, len(payload.documents))
            for document_index, legacy_document in enumerate(payload.documents):
                if import_job_id:
                    namespace = uuid.UUID(import_job_id)
                    article_id = uuid.uuid5(namespace, f"article:{document_index}").hex
                    document_id = uuid.uuid5(namespace, f"document:{document_index}").hex
                    existing_document = await session.get(Document, document_id)
                    deleted = await session.scalar(select(Job.id).where(
                        Job.source_id == source.id,
                        Job.type == JobType.DELETE_DOCUMENT,
                        Job.payload["target_document_id"].as_string() == document_id,
                    ).limit(1))
                    if deleted:
                        continue
                    if existing_document is not None:
                        counts["documents"] += 1
                        counts["chunks"] += existing_document.chunk_count
                        counts["events"] += existing_document.event_count
                        article_ids.append(article_id)
                        imported.append({
                            "id": document_id, "title": existing_document.filename,
                            "legacy_id": legacy_document.legacy_id or None,
                            "sag_source_id": article_id,
                            "chunks": existing_document.chunk_count,
                            "events": existing_document.event_count,
                        })
                        continue
                else:
                    article_id = _id()
                    document_id = _id()

                existing_article = await engine_session.get(Article, article_id)
                if existing_article is not None:
                    current_chunk_ids = list((await engine_session.scalars(
                        select(SourceChunk.id).where(SourceChunk.article_id == article_id)
                    )).all())
                    current_event_ids = list((await engine_session.scalars(
                        select(SourceEvent.id).where(SourceEvent.article_id == article_id)
                    )).all())
                    counts["chunks"] += len(current_chunk_ids)
                    counts["events"] += len(current_event_ids)
                else:
                    article_content = "\n\n".join(
                        f"## {chunk.heading}\n\n{chunk.content}" for chunk in legacy_document.chunks
                    )
                    article = Article(
                        id=article_id,
                        source_config_id=source.sag_source_config_id,
                        source_id=article_id,
                        title=legacy_document.title[:500],
                        summary="",
                        content=article_content,
                        status="COMPLETED",
                        parse_status=ArticleParseStatus.COMPLETED,
                        extra_data={
                            **_legacy_meta(
                                "document",
                                legacy_document.raw,
                                id=legacy_document.legacy_id,
                                source=legacy_document.source,
                                metadata=legacy_document.metadata,
                            ),
                            "structured_import": {"batch_id": batch_id, "filename": safe_name},
                            "url": legacy_document.source.get("url", ""),
                        },
                    )
                    engine_session.add(article)

                    current_chunk_ids: list[str] = []
                    current_event_ids: list[str] = []
                    for chunk_index, legacy_chunk in enumerate(legacy_document.chunks):
                        chunk_id = _id()
                        current_chunk_ids.append(chunk_id)
                        engine_session.add(
                            SourceChunk(
                                id=chunk_id,
                                source_config_id=source.sag_source_config_id,
                                source_type="ARTICLE",
                                source_id=article_id,
                                article_id=article_id,
                                heading=legacy_chunk.heading[:500],
                                content=legacy_chunk.content,
                                raw_content=legacy_chunk.raw_content or legacy_chunk.content,
                                rank=legacy_chunk.rank if legacy_chunk.rank is not None else chunk_index,
                                chunk_length=len(legacy_chunk.content),
                                references=[],
                                extra_data=_legacy_meta(
                                    "chunk",
                                    legacy_chunk.raw,
                                    id=legacy_chunk.key,
                                    systemChunkId=legacy_chunk.system_chunk_id,
                                    metadata=legacy_chunk.metadata,
                                ),
                            )
                        )
                        counts["chunks"] += 1

                        for event_index, legacy_event in enumerate(legacy_chunk.events):
                            event_id = _id()
                            current_event_ids.append(event_id)
                            event = SourceEvent(
                                id=event_id,
                                source_config_id=source.sag_source_config_id,
                                source_type="ARTICLE",
                                source_id=article_id,
                                article_id=article_id,
                                title=legacy_event.title[:255],
                                summary=legacy_event.summary,
                                content=legacy_event.content,
                                category=legacy_event.category[:50],
                                keywords=legacy_event.keywords,
                                status="COMPLETED",
                                rank=chunk_index * 100 + event_index,
                                chunk_id=chunk_id,
                                references=[chunk_id],
                                extra_data=_legacy_meta(
                                    "event",
                                    legacy_event.raw,
                                    id=legacy_event.legacy_id,
                                    keywords=legacy_event.keywords,
                                    category=legacy_event.category,
                                ),
                            )
                            engine_session.add(event)
                            counts["events"] += 1

                            seen_in_event: set[str] = set()
                            for legacy_entity in legacy_event.entities:
                                kind = (legacy_entity.type or "subject")[:50]
                                kind_key = kind.casefold()
                                entity_type = entity_types.get(kind_key)
                                if entity_type is None:
                                    entity_type = EntityType(
                                        id=_id(),
                                        scope="source",
                                        source_config_id=source.sag_source_config_id,
                                        type=kind,
                                        name=legacy_entity.type[:100] or "subject",
                                        description=f"从结构化化工数据导入的实体类型：{legacy_entity.type}",
                                        is_active=True,
                                        is_default=False,
                                        extra_data=_legacy_meta("entity_type", {}, originalType=legacy_entity.type),
                                    )
                                    engine_session.add(entity_type)
                                    entity_types[kind_key] = entity_type

                                normalized_name = legacy_entity.name.strip().casefold()
                                entity_key = (kind_key, normalized_name)
                                entity = entities.get(entity_key)
                                if entity is None:
                                    entity = Entity(
                                        id=_id(),
                                        source_config_id=source.sag_source_config_id,
                                        entity_type_id=entity_type.id,
                                        type=kind,
                                        name=legacy_entity.name[:500],
                                        normalized_name=normalized_name[:500],
                                        description=legacy_entity.description,
                                        extra_data=_legacy_meta(
                                            "entity",
                                            legacy_entity.raw,
                                            id=legacy_entity.legacy_id,
                                            type=legacy_entity.type,
                                            name=legacy_entity.name,
                                            description=legacy_entity.description,
                                        ),
                                    )
                                    engine_session.add(entity)
                                    entities[entity_key] = entity
                                    counts["entities"] += 1
                                elif not entity.description and legacy_entity.description:
                                    entity.description = legacy_entity.description
                                if entity.id in seen_in_event:
                                    continue
                                seen_in_event.add(entity.id)
                                engine_session.add(
                                    EventEntity(
                                        id=_id(),
                                        event_id=event_id,
                                        entity_id=entity.id,
                                        weight=1,
                                        description=legacy_entity.relation,
                                        extra_data=_legacy_meta(
                                            "event_entity",
                                            legacy_entity.raw,
                                            relationDescription=legacy_entity.relation,
                                            legacyEntityId=legacy_entity.legacy_id,
                                        ),
                                    )
                                )
                                counts["relations"] += 1

                article_ids.append(article_id)
                app_document = Document(
                    id=document_id,
                    source_id=source.id,
                    filename=legacy_document.title[:512],
                    content_type="application/x-chemclaw-structured",
                    size_bytes=len(content),
                    storage_path=str(destination),
                    status=DocumentStatus.READY,
                    chunk_count=len(current_chunk_ids),
                    event_count=len(current_event_ids),
                    progress=100,
                    token_usage=0,
                    sag_source_id=article_id,
                )
                session.add(app_document)
                imported.append(
                    {
                        "id": document_id,
                        "title": legacy_document.title,
                        "legacy_id": legacy_document.legacy_id or None,
                        "sag_source_id": article_id,
                        "chunks": len(current_chunk_ids),
                        "events": len(current_event_ids),
                    }
                )
                counts["documents"] += 1

                # 外部高精度数据按文档增量提交。即使是超大项目，也不会让 ORM
                # 长时间积累全部待写对象；同时可持续向前端报告真实入库进度。
                await engine_session.commit()
                await session.flush()
                await refresh_source_document_counts(session, source.id)
                await session.commit()
                if on_progress is not None:
                    await on_progress(0.12 + 0.68 * ((document_index + 1) / total_documents))

    # Background imports can finish as soon as the relationship data is durable.
    vector_indexed = False
    if settings.effective_embedding_api_key and index_vectors:
        await index_structured_data(
            source=source, article_ids=article_ids, engine_manager=engine_manager,
        )
        vector_indexed = True
    elif not settings.effective_embedding_api_key:
        warnings.append(
            "未配置 Embedding，关系图谱和原文读取可用；语义向量检索需配置 Embedding。"
        )

    await session.execute(update(Source).where(Source.id == source.id).values(id=Source.id))
    await session.refresh(source, attribute_names=["config"])
    config = dict(source.config or {})
    history = [
        entry for entry in config.get("chemclaw_import_history", [])
        if entry.get("batch_id") != batch_id
    ]
    history.append(
        {
            "batch_id": batch_id,
            "filename": safe_name,
            "imported_at": datetime.now(UTC).isoformat(),
            "schema_version": payload.schema_version,
            "project": payload.project,
            "counts": counts,
        }
    )
    config["chemclaw_import_history"] = history[-100:]
    source.config = config
    await session.commit()
    await session.refresh(source)

    # 后台导入由外层 Job 在数据完整落库后统一触发刷新。这里仅在调用方
    # 确实提供队列时直接调度，避免生成“已入库但从未入队”的悬空任务。
    if job_queue is not None:
        from sag_api.services.universe_service import schedule_universe_refresh

        await schedule_universe_refresh(
            session,
            job_queue,
            source_id=source.id,
            reason="structured_import",
        )
    result = {
        "ok": True,
        "batch_id": batch_id,
        "source_id": source.id,
        "source_name": source.name,
        "schema_version": payload.schema_version,
        "preserved_legacy_fields": True,
        "bypassed_builtin_chunking_and_extraction": True,
        "vector_indexed": vector_indexed,
        "counts": counts,
        "documents": imported,
        "warnings": warnings,
    }
    if not index_vectors and settings.effective_embedding_api_key:
        result["_index_plan"] = {
            "source_config_id": source.sag_source_config_id,
            "article_ids": article_ids,
        }
    return result


async def _ensure_embedding_usable() -> None:
    """Reject a broken endpoint once before a job can acquire the engine lock."""
    import openai

    api_key = settings.effective_embedding_api_key
    if not api_key:
        return
    async with openai.AsyncOpenAI(
        api_key=api_key,
        base_url=settings.effective_embedding_base_url,
        timeout=15.0,
        max_retries=0,
    ) as client:
        try:
            dimensions = settings.embedding_dimensions
            await client.embeddings.create(
                input="ChemClaw index check",
                model=settings.embedding_model,
                **({"dimensions": dimensions} if dimensions is not None else {}),
            )
        except openai.APIStatusError as error:
            if error.status_code in (401, 403):
                raise ValidationError(
                    f"Embedding 鉴权失败（HTTP {error.status_code}），请更新 Embedding 配置"
                ) from None
            raise ValidationError(f"Embedding 服务返回 HTTP {error.status_code}") from None
        except openai.APIError:
            raise ValidationError("Embedding 服务暂不可用，请检查连接与配置") from None


async def index_structured_data(
    *,
    source: Source,
    article_ids: list[str],
    engine_manager: EngineManager,
    on_progress: Callable[[float], Awaitable[None]] | None = None,
) -> None:
    """Index bounded batches and re-check document ownership under the engine lock."""
    from zleap.sag.core.storage import SourceChunkRepository
    from zleap.sag.core.storage.client import get_vector_client
    from zleap.sag.core.storage.repositories.entity_repository import EntityVectorRepository
    from zleap.sag.core.storage.repositories.event_repository import EventVectorRepository
    from zleap.sag.db import get_session_factory
    from zleap.sag.db.models import SourceChunk, SourceEvent
    from zleap.sag.modules.extract.config import ExtractConfig
    from zleap.sag.modules.extract.saver import EventSaver
    from zleap.sag.modules.load.loader import DocumentLoader

    from sag_api.core.db import SessionLocal

    if not settings.effective_embedding_api_key or not article_ids:
        return
    await _ensure_embedding_usable()

    source_config_id = source.sag_source_config_id
    await engine_manager.provision(source_config_id, source)
    vector_client = get_vector_client()
    chunk_repo = SourceChunkRepository(vector_client.client)
    loader = DocumentLoader()
    saver = EventSaver()
    saver._event_repo = EventVectorRepository(vector_client.client)  # noqa: SLF001
    saver._entity_repo = EntityVectorRepository(vector_client.client)  # noqa: SLF001
    factory = get_session_factory()
    batch_size = 20
    for article_index, article_id in enumerate(article_ids):
        indexed_entities: set[str] = set()
        for model in (SourceChunk, SourceEvent):
            last_id = ""
            while True:
                async with engine_manager.use_mutation(source_config_id, source):
                    async with SessionLocal() as control:
                        alive = await control.scalar(select(Document.id).where(
                            Document.source_id == source.id,
                            Document.sag_source_id == article_id,
                            Document.status.not_in(
                                [DocumentStatus.DELETING, DocumentStatus.DELETE_FAILED]
                            ),
                        ).limit(1))
                    if not alive:
                        break
                    async with factory() as session:
                        batch = list((await session.scalars(
                            select(model).where(
                                model.source_config_id == source_config_id,
                                model.article_id == article_id,
                                model.id > last_id,
                            ).order_by(model.id).limit(batch_size)
                        )).all())
                    if not batch:
                        break
                    last_id = batch[-1].id
                    # Upstream helpers can return a partial success. Surface that
                    # as a failed index instead of reporting a false completion.
                    try:
                        async with asyncio.timeout(45):
                            if model is SourceChunk:
                                stats = await loader._batch_index_chunks(  # noqa: SLF001
                                    batch, chunk_repo, vector_client, 10, batch_size, source_config_id
                                )
                                complete = stats["indexed_count"] == len(batch)
                            else:
                                events = await saver._load_events_with_relations(  # noqa: SLF001
                                    [record.id for record in batch]
                                )
                                config = ExtractConfig(
                                    source_config_id=source_config_id,
                                    chunk_ids=list({record.chunk_id for record in batch if record.chunk_id}),
                                )
                                entities = {
                                    association.entity_id: association.entity
                                    for event in events for association in event.event_associations
                                    if association.entity is not None
                                    and association.entity_id not in indexed_entities
                                }
                                stats = [
                                    await saver._sync_entities(list(entities.values()), config),  # noqa: SLF001
                                    await saver._sync_events(events, config),  # noqa: SLF001
                                    await saver._sync_event_entities(events, config),  # noqa: SLF001
                                ]
                                complete = all(result["indexed"] == result["total"] for result in stats)
                                indexed_entities.update(entities)
                    except TimeoutError:
                        raise ValidationError("Embedding 批次超时，向量索引未完成") from None
                    if not complete:
                        raise ValidationError("部分向量写入失败，向量索引未完成")
        if on_progress is not None:
            await on_progress((article_index + 1) / len(article_ids))


def _legacy_envelope(extra_data: Any) -> dict[str, Any]:
    return extra_data.get("chemclaw_legacy", {}) if isinstance(extra_data, dict) else {}


async def _export_payload(session: AsyncSession, source: Source) -> dict[str, Any]:
    """从 V2 当前关系库重建 V1 schemaVersion=1，不依赖导入原文件。"""
    documents = list(
        (
            await session.execute(
                select(Document).where(Document.source_id == source.id).order_by(Document.created_at, Document.id)
            )
        ).scalars()
    )
    article_ids = [item.sag_source_id for item in documents if item.sag_source_id]
    if not article_ids:
        return {
            "schemaVersion": 1,
            "exportedAt": datetime.now(UTC).isoformat(),
            "idPolicy": {"databaseIds": "preserve-legacy-when-available", "chunkKey": "preserved"},
            "project": _project_export(source),
            "documents": [],
        }

    from zleap.sag.db import get_session_factory
    from zleap.sag.db.models import Article, Entity, EventEntity, SourceChunk, SourceEvent

    factory = get_session_factory()
    async with factory() as engine_session:
        articles = {
            item.id: item
            for item in (await engine_session.execute(select(Article).where(Article.id.in_(article_ids)))).scalars()
        }
        chunks = list(
            (
                await engine_session.execute(
                    select(SourceChunk)
                    .where(SourceChunk.article_id.in_(article_ids))
                    .order_by(SourceChunk.article_id, SourceChunk.rank, SourceChunk.id)
                )
            ).scalars()
        )
        events = list(
            (
                await engine_session.execute(
                    select(SourceEvent)
                    .where(
                        SourceEvent.article_id.in_(article_ids),
                        (SourceEvent.status.is_(None)) | (SourceEvent.status != "DELETED"),
                    )
                    .order_by(SourceEvent.article_id, SourceEvent.rank, SourceEvent.id)
                )
            ).scalars()
        )
        event_ids = [item.id for item in events]
        association_rows = []
        if event_ids:
            association_rows = (
                await engine_session.execute(
                    select(EventEntity, Entity)
                    .join(Entity, Entity.id == EventEntity.entity_id)
                    .where(EventEntity.event_id.in_(event_ids))
                    .order_by(EventEntity.created_time, EventEntity.id)
                )
            ).all()

    chunks_by_article: dict[str, list[Any]] = {}
    for chunk in chunks:
        chunks_by_article.setdefault(chunk.article_id, []).append(chunk)
    events_by_article: dict[str, list[Any]] = {}
    events_by_chunk: dict[str, list[Any]] = {}
    for event in events:
        events_by_article.setdefault(event.article_id, []).append(event)
        if event.chunk_id:
            events_by_chunk.setdefault(event.chunk_id, []).append(event)
    entities_by_event: dict[str, list[tuple[Any, Any]]] = {}
    for association, entity in association_rows:
        entities_by_event.setdefault(association.event_id, []).append((association, entity))
    documents_by_article = {item.sag_source_id: item for item in documents if item.sag_source_id}

    result_documents: list[dict[str, Any]] = []
    for article_id in article_ids:
        article = articles.get(article_id)
        document = documents_by_article.get(article_id)
        if article is None or document is None:
            continue
        document_legacy = _legacy_envelope(article.extra_data)
        source_meta = document_legacy.get("source") if isinstance(document_legacy.get("source"), dict) else {}

        def event_export(
            event: Any,
            current_document_legacy: dict[str, Any],
            current_document: Document,
            current_article_chunks: list[Any],
        ) -> dict[str, Any]:
            event_legacy = _legacy_envelope(event.extra_data)
            entity_values = []
            for association, entity in entities_by_event.get(event.id, []):
                entity_legacy = _legacy_envelope(entity.extra_data)
                relation_legacy = _legacy_envelope(association.extra_data)
                entity_values.append(
                    {
                        "eventId": event_legacy.get("id") or event.id,
                        "entityId": entity_legacy.get("id") or entity.id,
                        "entityType": entity_legacy.get("type") or entity.type,
                        "entityName": entity_legacy.get("name") or entity.name,
                        "entityDescription": entity_legacy.get("description") or entity.description or "",
                        "relationDescription": relation_legacy.get("relationDescription")
                        or association.description
                        or "",
                        "systemEntityId": entity.id,
                    }
                )
            return {
                "id": event_legacy.get("id") or event.id,
                "documentId": current_document_legacy.get("id") or current_document.id,
                "chunkId": _legacy_envelope(
                    next(
                        (chunk.extra_data for chunk in current_article_chunks if chunk.id == event.chunk_id),
                        {},
                    )
                ).get("id")
                if event.chunk_id
                else None,
                "title": event.title,
                "summary": event.summary,
                "content": event.content,
                "category": event.category or "",
                "keywords": event.keywords or [],
                "rank": event.rank,
                "metadata": event_legacy.get("raw", {}).get("metadata", {})
                if isinstance(event_legacy.get("raw"), dict)
                else {},
                "entities": entity_values,
                "systemEventId": event.id,
            }

        exported_chunks = []
        chunk_ids = set()
        for chunk in chunks_by_article.get(article_id, []):
            chunk_ids.add(chunk.id)
            chunk_legacy = _legacy_envelope(chunk.extra_data)
            exported_chunks.append(
                {
                    "id": chunk_legacy.get("id") or chunk.id,
                    "systemChunkId": chunk.id,
                    "heading": chunk.heading or "",
                    "content": chunk.content or "",
                    "rawContent": chunk.raw_content or chunk.content or "",
                    "rank": chunk.rank,
                    "metadata": chunk_legacy.get("metadata") or {},
                    "events": [
                        event_export(event, document_legacy, document, chunks_by_article.get(article_id, []))
                        for event in events_by_chunk.get(chunk.id, [])
                    ],
                }
            )
        events_without_chunk = [
            event_export(event, document_legacy, document, chunks_by_article.get(article_id, []))
            for event in events_by_article.get(article_id, [])
            if not event.chunk_id or event.chunk_id not in chunk_ids
        ]
        raw_document = document_legacy.get("raw") if isinstance(document_legacy.get("raw"), dict) else {}
        result_documents.append(
            {
                "id": document_legacy.get("id") or document.id,
                "systemDocumentId": document.id,
                "title": article.title,
                "content": article.content or "",
                "sourceName": source_meta.get("name", ""),
                "sourceType": source_meta.get("type", ""),
                "sourceDate": source_meta.get("date", ""),
                "sourceAuthor": source_meta.get("author", ""),
                "sourceUrl": source_meta.get("url", ""),
                "status": raw_document.get("status", "ready"),
                "parseStatus": raw_document.get("parseStatus", "completed"),
                "metadata": document_legacy.get("metadata") or {},
                "archivedAt": raw_document.get("archivedAt"),
                "createdAt": raw_document.get("createdAt") or document.created_at.isoformat(),
                "updatedAt": raw_document.get("updatedAt") or document.updated_at.isoformat(),
                "chunks": exported_chunks,
                "eventsWithoutChunk": events_without_chunk,
            }
        )

    return {
        "schemaVersion": 1,
        "exportedAt": datetime.now(UTC).isoformat(),
        "idPolicy": {
            "databaseIds": "legacy ids are preserved; V2 ids are exposed as system*Id",
            "chunkKey": "legacy chunk id/key is preserved",
        },
        "project": _project_export(source),
        "documents": result_documents,
    }


def _project_export(source: Source) -> dict[str, Any]:
    histories = (source.config or {}).get("chemclaw_import_history") or []
    original_project = next((item.get("project") for item in reversed(histories) if item.get("project")), {})
    return {
        "id": original_project.get("id") or source.id,
        "systemProjectId": source.id,
        "name": source.name,
        "description": source.description,
        "metadata": original_project.get("metadata") or {},
        "archivedAt": original_project.get("archivedAt"),
        "createdAt": original_project.get("createdAt") or source.created_at.isoformat(),
        "updatedAt": original_project.get("updatedAt") or source.updated_at.isoformat(),
    }


def _simple_payload(full: dict[str, Any]) -> dict[str, Any]:
    documents = []
    for document in full["documents"]:
        chunks = []
        for chunk in document["chunks"]:
            for index, event in enumerate(chunk["events"]):
                chunks.append(
                    {
                        "key": chunk["id"] if len(chunk["events"]) == 1 else f"{chunk['id']}:{index + 1}",
                        "heading": chunk["heading"],
                        "content": chunk["rawContent"] or chunk["content"],
                        "event": {
                            "title": event["title"],
                            "summary": event["summary"],
                            "content": event["content"],
                            "category": event["category"],
                            "keywords": event["keywords"],
                            "entities": [
                                {
                                    "type": entity["entityType"],
                                    "name": entity["entityName"],
                                    "description": entity["entityDescription"],
                                    "relation": entity["relationDescription"],
                                }
                                for entity in event["entities"]
                            ],
                        },
                    }
                )
        documents.append(
            {
                "title": document["title"],
                "source": {
                    "name": document["sourceName"],
                    "type": document["sourceType"],
                    "date": document["sourceDate"],
                    "author": document["sourceAuthor"],
                    "url": document["sourceUrl"],
                },
                "chunks": chunks,
            }
        )
    return {"documents": documents}


_SIMPLE_HEADERS = [
    "文档标题",
    "来源名称",
    "来源类型",
    "来源日期",
    "来源作者",
    "来源链接",
    "切片编号",
    "切片标题",
    "原文切片",
    "事件标题",
    "事件摘要",
    "事件内容",
    "事件分类",
    "关键词",
    "实体类型",
    "实体名称",
    "实体说明",
    "关系说明",
]
_FULL_HEADERS = [
    "project_id",
    "project_name",
    "document_id",
    "document_title",
    "source_name",
    "source_type",
    "source_date",
    "source_author",
    "source_url",
    "chunk_id",
    "system_chunk_id",
    "chunk_heading",
    "chunk_content",
    "system_event_id",
    "event_title",
    "event_summary",
    "event_content",
    "event_category",
    "keywords",
    "system_entity_id",
    "entity_type",
    "entity_name",
    "entity_description",
    "relation_description",
    "document_archived_at",
]


def _flat_rows(full: dict[str, Any], profile: ExportProfile) -> tuple[list[str], list[list[Any]]]:
    rows: list[list[Any]] = []
    project = full["project"]
    for document in full["documents"]:
        for chunk in document["chunks"]:
            for event in chunk["events"]:
                for entity in event["entities"] or [{}]:
                    if profile == "simple":
                        rows.append(
                            [
                                document["title"],
                                document["sourceName"],
                                document["sourceType"],
                                document["sourceDate"],
                                document["sourceAuthor"],
                                document["sourceUrl"],
                                chunk["id"],
                                chunk["heading"],
                                chunk["rawContent"],
                                event["title"],
                                event["summary"],
                                event["content"],
                                event["category"],
                                "；".join(event["keywords"]),
                                entity.get("entityType", ""),
                                entity.get("entityName", ""),
                                entity.get("entityDescription", ""),
                                entity.get("relationDescription", ""),
                            ]
                        )
                    else:
                        rows.append(
                            [
                                project["id"],
                                project["name"],
                                document["id"],
                                document["title"],
                                document["sourceName"],
                                document["sourceType"],
                                document["sourceDate"],
                                document["sourceAuthor"],
                                document["sourceUrl"],
                                chunk["id"],
                                chunk["systemChunkId"],
                                chunk["heading"],
                                chunk["rawContent"],
                                event["systemEventId"],
                                event["title"],
                                event["summary"],
                                event["content"],
                                event["category"],
                                "；".join(event["keywords"]),
                                entity.get("systemEntityId", ""),
                                entity.get("entityType", ""),
                                entity.get("entityName", ""),
                                entity.get("entityDescription", ""),
                                entity.get("relationDescription", ""),
                                document["archivedAt"] or "",
                            ]
                        )
    return (_SIMPLE_HEADERS if profile == "simple" else _FULL_HEADERS), rows


async def export_structured_data(
    session: AsyncSession,
    source_id: str,
    *,
    format_name: ExportFormat,
    profile: ExportProfile,
) -> tuple[bytes, str, str]:
    source = await get_source(session, source_id)
    full = await _export_payload(session, source)
    if format_name == "json":
        value = _simple_payload(full) if profile == "simple" else full
        return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"), "application/json", "json"
    headers, rows = _flat_rows(full, profile)
    if format_name == "csv":
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)
        return ("\ufeff" + stream.getvalue()).encode("utf-8"), "text/csv; charset=utf-8", "csv"

    def escape(value: Any) -> str:
        return str(value or "").replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")

    lines = [
        "| " + " | ".join(map(escape, headers)) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(map(escape, row)) + " |" for row in rows),
    ]
    return "\n".join(lines).encode("utf-8"), "text/markdown; charset=utf-8", "md"


async def create_source_backup(session: AsyncSession, source_id: str) -> bytes:
    source = await get_source(session, source_id)
    full = await _export_payload(session, source)
    documents = list(
        (
            await session.execute(
                select(Document).where(Document.source_id == source.id).order_by(Document.created_at, Document.id)
            )
        ).scalars()
    )
    output = io.BytesIO()
    manifest = {
        "format": "chemclaw-sag-backup",
        "version": 1,
        "createdAt": datetime.now(UTC).isoformat(),
        "source": {"id": source.id, "name": source.name},
        "contains": ["project.json", "manifest.json", "originals/"],
        "restore": "project.json 可直接通过 ChemClaw 结构化导入恢复，旧字段保持不变。",
    }
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("project.json", json.dumps(full, ensure_ascii=False, indent=2))
        archived_paths: set[str] = set()
        for document in documents:
            path = Path(document.storage_path)
            if not path.is_file():
                continue
            resolved = str(path.resolve())
            if resolved in archived_paths:
                continue
            archived_paths.add(resolved)
            archive.write(path, f"originals/{document.id}-{_safe_filename(path.name)}")
    return output.getvalue()
