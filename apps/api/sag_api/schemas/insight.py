from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class EntityOut(BaseModel):
    id: str
    name: str
    type: str
    description: str
    heat: int


class GraphDocumentOut(BaseModel):
    id: str
    filename: str
    status: str
    chunk_count: int
    event_count: int
    created_at: datetime


class GraphEventOut(BaseModel):
    id: str
    document_id: str | None = None
    title: str
    summary: str = ""
    category: str = ""
    rank: int = 0
    parent_id: str | None = None
    chunk_id: str | None = None
    start_time: datetime | None = None


class GraphChunkOut(BaseModel):
    id: str
    document_id: str | None = None
    heading: str = ""
    rank: int = 0
    event_count: int = 0


GraphNodeKind = Literal["document", "event", "entity"]
GraphRelationKind = Literal["contains", "subevent", "mentions"]


class GraphRelationOut(BaseModel):
    source_id: str
    source_kind: GraphNodeKind
    target_id: str
    target_kind: GraphNodeKind
    kind: GraphRelationKind
    weight: float = 1.0
    description: str = ""


class GraphCountsOut(BaseModel):
    documents: int
    events: int
    entities: int
    shown_documents: int
    shown_events: int
    shown_entities: int
    shown_relations: int


class SourceGraphOut(BaseModel):
    documents: list[GraphDocumentOut]
    events: list[GraphEventOut]
    entities: list[EntityOut]
    relations: list[GraphRelationOut]
    counts: GraphCountsOut
    truncated: bool
    chunks: list[GraphChunkOut] = Field(default_factory=list)


class AssetTypeCountOut(BaseModel):
    type: str
    count: int


class ProjectAssetSummaryOut(BaseModel):
    documents: int
    chunks: int
    events: int
    entities: int
    entity_types: list[AssetTypeCountOut]


class ProjectAssetsOut(BaseModel):
    kind: Literal["chunks", "events", "entities"]
    page: int
    page_size: int
    total: int
    items: list[dict]
