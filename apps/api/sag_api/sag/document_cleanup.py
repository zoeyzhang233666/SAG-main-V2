"""Delete one document's derived zleap-sag records across relational and vector stores."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import bindparam, delete, or_, select, text

from sag_api.core.logging import get_logger

log = get_logger("sag.cleanup")


@dataclass(frozen=True)
class DeletedDocumentRecords:
    chunk_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    relation_ids: tuple[str, ...]
    entity_ids: tuple[str, ...]


def _batches(values: tuple[str, ...], size: int = 500) -> Iterator[tuple[str, ...]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _lance_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


async def _delete_vector_ids(client: Any, index: str, ids: tuple[str, ...]) -> None:
    if not ids:
        return

    # zleap-sag 0.7.1 has no bulk-delete facade. Keep provider details contained here
    # so deleting thousands of derived rows does not become thousands of transactions.
    open_table = getattr(client, "_open_table", None)
    if callable(open_table):
        table = await open_table(index)
        if table is None:
            return
        for batch in _batches(ids):
            values = ", ".join(_lance_literal(value) for value in batch)
            await table.delete(f"id IN ({values})")
        return

    index_exists = getattr(client, "index_exists", None)
    if callable(index_exists) and not await index_exists(index):
        return
    raw_client = getattr(client, "client", client)
    delete_by_query = getattr(raw_client, "delete_by_query", None)
    if callable(delete_by_query):
        for batch in _batches(ids):
            await delete_by_query(
                index=index,
                query={"ids": {"values": list(batch)}},
                conflicts="proceed",
                refresh=True,
            )
        return

    engine_factory = getattr(client, "_engine", None)
    if callable(engine_factory):
        quote = "`" if client.__class__.__name__ == "OceanBaseVectorStore" else '"'
        statement = text(
            f"DELETE FROM {quote}{index}{quote} WHERE id IN :ids"
        ).bindparams(bindparam("ids", expanding=True))
        for batch in _batches(ids):
            async with engine_factory().begin() as connection:
                await connection.execute(statement, {"ids": list(batch)})
        return

    for record_id in ids:
        await client.delete(index=index, id=record_id)


async def _delete_vectors(records: DeletedDocumentRecords) -> None:
    from zleap.sag.core.storage.client import get_vector_client

    client = get_vector_client()
    groups = (
        ("source_chunks", records.chunk_ids),
        ("event_vectors", records.event_ids),
        ("event_vectors_wide", records.event_ids),
        ("event_entity_vectors", records.relation_ids),
        ("entity_vectors", records.entity_ids),
    )
    for index, ids in groups:
        await _delete_vector_ids(client, index, ids)


async def delete_document_records(
    source_config_id: str,
    document_source_id: str,
) -> DeletedDocumentRecords:
    """Delete chunks, events, relations and now-orphaned entities for one document."""
    from zleap.sag.db import get_session_factory
    from zleap.sag.db.models import (
        Article,
        ArticleSection,
        Entity,
        EntityType,
        EventEntity,
        EventEntityEmbedding,
        SourceChunk,
        SourceEvent,
    )

    session_factory = get_session_factory()
    async with session_factory() as session:
        articles = select(Article.id).where(
            Article.source_config_id == source_config_id,
            or_(Article.id == document_source_id, Article.source_id == document_source_id),
        )
        chunks = select(SourceChunk.id).where(
            SourceChunk.source_config_id == source_config_id,
            or_(
                SourceChunk.source_id == document_source_id,
                SourceChunk.source_id.in_(articles),
                SourceChunk.article_id.in_(articles),
            ),
        )
        events = select(SourceEvent.id).where(
            SourceEvent.source_config_id == source_config_id,
            or_(
                SourceEvent.source_id == document_source_id,
                SourceEvent.source_id.in_(articles),
                SourceEvent.article_id.in_(articles),
                SourceEvent.chunk_id.in_(chunks),
            ),
        )
        target_relations = select(EventEntity.id).where(EventEntity.event_id.in_(events))
        related_entities = select(EventEntity.entity_id).where(EventEntity.event_id.in_(events))
        remaining_reference = select(EventEntity.id).where(
            EventEntity.entity_id == Entity.id, EventEntity.event_id.not_in(events)
        ).exists()
        orphans = select(Entity.id).where(Entity.id.in_(related_entities), ~remaining_reference)

        records = DeletedDocumentRecords(
            chunk_ids=tuple((await session.scalars(chunks)).all()),
            event_ids=tuple((await session.scalars(events)).all()),
            relation_ids=tuple((await session.scalars(target_relations)).all()),
            entity_ids=tuple((await session.scalars(orphans)).all()),
        )
        # Keep the SQL IDs intact until all vector deletes succeed. A restart or
        # partial vector failure can replay the same predicates without losing IDs.
        await _delete_vectors(records)
        for batch in _batches(records.relation_ids):
            await session.execute(
                delete(EventEntityEmbedding).where(EventEntityEmbedding.id.in_(batch))
            )
            await session.execute(delete(EventEntity).where(EventEntity.id.in_(batch)))
        for batch in _batches(records.event_ids):
            await session.execute(delete(SourceEvent).where(SourceEvent.id.in_(batch)))
        for batch in _batches(records.entity_ids):
            await session.execute(
                delete(Entity).where(
                    Entity.id.in_(batch),
                    ~select(EventEntity.id).where(EventEntity.entity_id == Entity.id).exists(),
                )
            )
        for batch in _batches(records.chunk_ids):
            await session.execute(delete(SourceChunk).where(SourceChunk.id.in_(batch)))
        await session.execute(delete(ArticleSection).where(ArticleSection.article_id.in_(articles)))
        await session.execute(
            delete(EntityType).where(
                EntityType.article_id.in_(articles),
                ~select(Entity.id).where(Entity.entity_type_id == EntityType.id).exists(),
            )
        )
        await session.execute(delete(Article).where(Article.id.in_(articles)))
        await session.commit()
        return records
