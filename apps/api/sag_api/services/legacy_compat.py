"""SAG V1 化工数据格式兼容层。

这里故意不依赖 V2 的解析/抽取管线：传入的切片、事件和实体已经由外部模型
完成高精度处理，兼容层只做格式规范化与字段校验，绝不改写原始字段语义。
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from sag_api.core.errors import ValidationError

StructuredFormat = Literal["auto", "json", "csv", "markdown"]


@dataclass(slots=True)
class LegacyEntity:
    type: str
    name: str
    description: str
    relation: str = ""
    legacy_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LegacyEvent:
    title: str
    summary: str
    content: str
    category: str = ""
    keywords: list[str] = field(default_factory=list)
    entities: list[LegacyEntity] = field(default_factory=list)
    legacy_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LegacyChunk:
    key: str
    heading: str
    content: str
    events: list[LegacyEvent]
    system_chunk_id: str = ""
    raw_content: str = ""
    rank: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LegacyDocument:
    title: str
    chunks: list[LegacyChunk]
    source: dict[str, str] = field(default_factory=dict)
    legacy_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LegacyImport:
    documents: list[LegacyDocument]
    project: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1


_ALIASES: dict[str, tuple[str, ...]] = {
    "document_id": ("document_id", "documentid", "system_document_id"),
    "document_title": ("文档标题", "document_title", "documenttitle", "document"),
    "source_name": ("来源名称", "source_name", "sourcename", "source_title", "sourcetitle"),
    "source_type": ("来源类型", "source_type", "sourcetype"),
    "source_date": ("来源日期", "source_date", "sourcedate", "publish_date", "published_at"),
    "source_author": (
        "来源作者",
        "来源机构",
        "作者",
        "机构",
        "source_author",
        "sourceauthor",
        "source_org",
        "sourceorg",
        "organization",
        "author",
    ),
    "source_url": ("来源链接", "来源url", "来源网址", "source_url", "sourceurl", "url", "link"),
    "chunk_id": ("切片编号", "切片id", "chunk_id", "chunkid", "chunk_key"),
    "system_chunk_id": ("system_chunk_id", "systemchunkid"),
    "chunk_heading": ("切片标题", "chunk_heading", "chunkheading", "heading"),
    "chunk_content": ("原文切片", "切片原文", "chunk_content", "chunkcontent", "source_text", "原文"),
    "event_id": ("system_event_id", "event_id", "eventid"),
    "event_title": ("事件标题", "event_title", "eventtitle"),
    "event_summary": ("事件摘要", "event_summary", "eventsummary", "summary"),
    "event_content": ("事件内容", "event_content", "eventcontent"),
    "event_category": ("事件分类", "event_category", "eventcategory", "category"),
    "keywords": ("关键词", "keywords", "keyword"),
    "entity_id": ("system_entity_id", "entity_id", "entityid"),
    "entity_type": ("实体类型", "entity_type", "entitytype"),
    "entity_name": ("实体名称", "entity_name", "entityname", "entity"),
    "entity_description": ("实体说明", "entity_description", "entitydescription", "description"),
    "relation_description": ("关系说明", "relation_description", "relationdescription", "relation"),
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _metadata(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _keywords(value: Any) -> list[str]:
    values = value if isinstance(value, list) else re.split(r"[;；、,，]", _text(value))
    return list(dict.fromkeys(filter(None, (_text(item) for item in values))))


def _entity(value: dict[str, Any], event_title: str) -> LegacyEntity | None:
    name = _text(value.get("entityName") or value.get("name"))
    if not name:
        return None
    return LegacyEntity(
        type=_text(value.get("entityType") or value.get("type")) or "subject",
        name=name,
        description=_text(value.get("entityDescription") or value.get("description"))
        or f"在事件「{event_title}」中被提及",
        relation=_text(value.get("relationDescription") or value.get("relation")),
        legacy_id=_text(value.get("entityId") or value.get("id")),
        raw=dict(value),
    )


def _event(value: dict[str, Any], chunk: dict[str, Any], index: int) -> LegacyEvent:
    content = _text(
        value.get("content") or value.get("eventContent") or chunk.get("content") or chunk.get("rawContent")
    )
    title = _text(value.get("title") or value.get("eventTitle") or chunk.get("heading")) or f"事件 {index + 1}"
    entities = [item for item in (_entity(_record(entity), title) for entity in value.get("entities", [])) if item]
    return LegacyEvent(
        title=title,
        summary=_text(value.get("summary") or value.get("eventSummary")) or content,
        content=content,
        category=_text(value.get("category") or value.get("eventCategory")),
        keywords=_keywords(value.get("keywords")),
        entities=_unique_entities(entities),
        legacy_id=_text(value.get("id") or value.get("eventId")),
        raw=dict(value),
    )


def _chunk(value: dict[str, Any], index: int, *, full_export: bool = False) -> LegacyChunk:
    raw_events = value.get("events") if isinstance(value.get("events"), list) else None
    if raw_events is None:
        raw_events = [value.get("event") if isinstance(value.get("event"), dict) else value]
    events = [_event(_record(item), value, event_index) for event_index, item in enumerate(raw_events)]
    key = _text(value.get("id") or value.get("key") or value.get("chunkId")) or str(index + 1)
    content = _text(value.get("rawContent") or value.get("content") or value.get("chunkContent"))
    if not content and events:
        content = events[0].content
    return LegacyChunk(
        key=key,
        system_chunk_id=_text(value.get("systemChunkId")) if full_export else "",
        heading=_text(value.get("heading") or value.get("chunkHeading"))
        or (events[0].title if events else f"切片 {index + 1}"),
        content=content,
        raw_content=_text(value.get("rawContent")) or content,
        rank=int(value.get("rank") or index),
        metadata=_metadata(value.get("metadata")),
        events=events,
        raw=dict(value),
    )


def _source(value: dict[str, Any]) -> dict[str, str]:
    pairs = {
        "name": value.get("name") or value.get("sourceName") or value.get("source_name"),
        "type": value.get("type") or value.get("sourceType") or value.get("source_type"),
        "date": value.get("date") or value.get("sourceDate") or value.get("source_date"),
        "author": value.get("author")
        or value.get("sourceAuthor")
        or value.get("source_author")
        or value.get("org")
        or value.get("sourceOrg")
        or value.get("source_org"),
        "url": value.get("url") or value.get("sourceUrl") or value.get("source_url"),
    }
    return {key: _text(item) for key, item in pairs.items() if _text(item)}


def _document(value: dict[str, Any], index: int, fallback_title: str, *, full_export: bool = False) -> LegacyDocument:
    chunks = [
        _chunk(_record(item), chunk_index, full_export=full_export)
        for chunk_index, item in enumerate(value.get("chunks", []))
    ]
    if full_export:
        for event_index, raw_event in enumerate(value.get("eventsWithoutChunk", [])):
            event_data = _record(raw_event)
            synthetic = {
                "id": f"event-{_text(event_data.get('id')) or event_index + 1}",
                "heading": event_data.get("title"),
                "content": event_data.get("content"),
                "events": [event_data],
            }
            chunks.append(_chunk(synthetic, len(chunks), full_export=True))
    source_value = value if full_export else _record(value.get("source")) or value
    return LegacyDocument(
        title=_text(value.get("title") or value.get("documentTitle")) or fallback_title or f"导入文档 {index + 1}",
        chunks=chunks,
        source=_source(source_value),
        legacy_id=_text(value.get("id") or value.get("documentId")),
        metadata=_metadata(value.get("metadata")),
        raw=dict(value),
    )


def _normalize_header(value: str) -> str:
    return re.sub(r"[\s_-]+", "", value.strip().lower())


def _normalize_row(row: dict[str, Any]) -> dict[str, str]:
    values = {_normalize_header(key): _text(value) for key, value in row.items()}
    normalized: dict[str, str] = {}
    for field_name, aliases in _ALIASES.items():
        normalized[field_name] = next(
            (values[_normalize_header(alias)] for alias in aliases if values.get(_normalize_header(alias))),
            "",
        )
    return normalized


def _rows_to_documents(rows: list[dict[str, Any]], fallback_title: str) -> list[LegacyDocument]:
    if not rows:
        raise ValidationError("表格没有数据行")
    normalized = [_normalize_row(row) for row in rows]
    document_groups: dict[str, list[dict[str, str]]] = {}
    for row in normalized:
        key = row["document_id"] or row["document_title"] or fallback_title or "__single_document__"
        document_groups.setdefault(key, []).append(row)

    documents: list[LegacyDocument] = []
    for document_rows in document_groups.values():
        chunk_groups: dict[str, list[dict[str, str]]] = {}
        for index, row in enumerate(document_rows):
            key = (
                row["chunk_id"]
                or f"{row['event_title']}\0{row['chunk_heading']}\0{row['chunk_content'][:120]}"
                or str(index + 1)
            )
            chunk_groups.setdefault(key, []).append(row)
        chunks: list[LegacyChunk] = []
        for index, (key, group) in enumerate(chunk_groups.items()):
            first = group[0]
            event_title = first["event_title"] or first["chunk_heading"] or f"事件 {index + 1}"
            chunk_content = first["chunk_content"] or first["event_content"] or first["event_summary"]
            entities = _unique_entities(
                [
                    LegacyEntity(
                        type=row["entity_type"] or "subject",
                        name=row["entity_name"],
                        description=row["entity_description"] or f"在事件「{event_title}」中被提及",
                        relation=row["relation_description"],
                        legacy_id=row["entity_id"],
                        raw=dict(row),
                    )
                    for row in group
                    if row["entity_name"]
                ]
            )
            event = LegacyEvent(
                title=event_title,
                summary=first["event_summary"] or first["event_content"] or chunk_content,
                content=first["event_content"] or chunk_content,
                category=first["event_category"],
                keywords=list(dict.fromkeys(word for row in group for word in _keywords(row["keywords"]))),
                entities=entities,
                legacy_id=first["event_id"],
                raw=dict(first),
            )
            chunks.append(
                LegacyChunk(
                    key=key,
                    system_chunk_id=first["system_chunk_id"],
                    heading=first["chunk_heading"] or event_title,
                    content=chunk_content,
                    raw_content=chunk_content,
                    rank=index,
                    events=[event],
                    raw=dict(first),
                )
            )
        source_row = next(
            (
                row
                for row in document_rows
                if any(
                    row[name] for name in ("source_name", "source_type", "source_date", "source_author", "source_url")
                )
            ),
            None,
        )
        source = (
            {}
            if source_row is None
            else {
                key.removeprefix("source_"): value
                for key, value in source_row.items()
                if key in {"source_name", "source_type", "source_date", "source_author", "source_url"} and value
            }
        )
        first = document_rows[0]
        documents.append(
            LegacyDocument(
                title=next(
                    (row["document_title"] for row in document_rows if row["document_title"]),
                    fallback_title or "网页版 AI 导入",
                ),
                chunks=chunks,
                source=source,
                legacy_id=first["document_id"],
                raw={"rows": [dict(row) for row in document_rows]},
            )
        )
    return documents


def _unique_entities(entities: list[LegacyEntity]) -> list[LegacyEntity]:
    unique: dict[tuple[str, str], LegacyEntity] = {}
    for entity in entities:
        key = (entity.type.casefold().strip(), entity.name.casefold().strip())
        existing = unique.get(key)
        if existing is None:
            unique[key] = entity
        else:
            existing.description = existing.description or entity.description
            existing.relation = existing.relation or entity.relation
            existing.legacy_id = existing.legacy_id or entity.legacy_id
    return list(unique.values())


def _markdown_rows(text: str) -> list[dict[str, str]]:
    lines = text.splitlines()

    def split(line: str) -> list[str]:
        body = line.strip().removeprefix("|").removesuffix("|")
        if "|" not in body:
            return []
        return [
            part.strip().replace("<br>", "\n").replace("<br/>", "\n").replace("\\|", "|")
            for part in re.split(r"(?<!\\)\|", body)
        ]

    for index in range(len(lines) - 1):
        headers, separator = split(lines[index]), split(lines[index + 1])
        if (
            len(headers) < 2
            or len(separator) != len(headers)
            or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)
        ):
            continue
        rows: list[dict[str, str]] = []
        for line in lines[index + 2 :]:
            values = split(line)
            if not values:
                break
            if len(values) != len(headers):
                raise ValidationError("Markdown 表格数据列数与表头不一致")
            rows.append(dict(zip(headers, values, strict=True)))
        if rows:
            return rows
    raise ValidationError("没有找到有效的 Markdown 表格")


def _unwrap_fence(text: str) -> str:
    match = re.fullmatch(r"```(?:csv|tsv|json|markdown|md)?\s*\n([\s\S]*?)\n```\s*", text, re.IGNORECASE)
    return match.group(1).strip() if match else text


def _validate(data: LegacyImport) -> LegacyImport:
    if not data.documents:
        raise ValidationError("导入文件中没有 documents")
    for document_index, document in enumerate(data.documents, 1):
        if not document.chunks:
            raise ValidationError(f"第 {document_index} 篇文档没有可导入的切片")
        for chunk_index, chunk in enumerate(document.chunks, 1):
            if not chunk.content:
                raise ValidationError(f"第 {document_index} 篇文档的第 {chunk_index} 个切片缺少“原文切片”")
            if not chunk.events:
                raise ValidationError(f"第 {document_index} 篇文档的第 {chunk_index} 个切片缺少事件")
            for event in chunk.events:
                if not event.title:
                    raise ValidationError(f"第 {document_index} 篇文档的第 {chunk_index} 个切片缺少“事件标题”")
                if not event.entities:
                    raise ValidationError(f"事件“{event.title}”没有实体；至少需要一条实体数据")
    return data


def parse_legacy_import(
    content: bytes,
    *,
    filename: str,
    requested_format: StructuredFormat = "auto",
) -> LegacyImport:
    """解析 V1 完整备份、简化 JSON、CSV/TSV 或 Markdown 表格。"""
    try:
        text = _unwrap_fence(content.decode("utf-8-sig").strip())
    except UnicodeDecodeError as error:
        raise ValidationError("结构化导入文件必须使用 UTF-8 编码") from error
    if not text:
        raise ValidationError("导入内容为空")
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    format_name: StructuredFormat = requested_format
    if format_name == "auto":
        if extension == "json" or text.startswith(("{", "[")):
            format_name = "json"
        elif extension in {"md", "markdown"}:
            format_name = "markdown"
        else:
            format_name = "csv"
    fallback_title = filename.rsplit(".", 1)[0] or "结构化导入"

    if format_name == "json":
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValidationError("JSON 格式无效，请移除多余说明或 Markdown 围栏") from error
        if isinstance(value, dict) and value.get("schemaVersion") == 1 and isinstance(value.get("documents"), list):
            result = LegacyImport(
                documents=[
                    _document(_record(item), index, fallback_title, full_export=True)
                    for index, item in enumerate(value["documents"])
                ],
                project=_record(value.get("project")),
                schema_version=1,
            )
        elif isinstance(value, dict) and isinstance(value.get("documents"), list):
            result = LegacyImport(
                documents=[
                    _document(_record(item), index, fallback_title) for index, item in enumerate(value["documents"])
                ],
                project=_record(value.get("project")),
            )
        elif isinstance(value, dict) and isinstance(value.get("chunks"), list):
            result = LegacyImport(documents=[_document(value, 0, fallback_title)])
        else:
            rows = value if isinstance(value, list) else value.get("rows") if isinstance(value, dict) else None
            if not isinstance(rows, list):
                raise ValidationError("JSON 需要包含 documents、chunks、rows，或直接使用行对象数组")
            result = LegacyImport(documents=_rows_to_documents([_record(item) for item in rows], fallback_title))
        return _validate(result)

    if format_name == "markdown":
        return _validate(LegacyImport(documents=_rows_to_documents(_markdown_rows(text), fallback_title)))

    delimiter = (
        "\t" if extension == "tsv" or text.splitlines()[0].count("\t") > text.splitlines()[0].count(",") else ","
    )
    try:
        rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))
    except csv.Error as error:
        raise ValidationError(f"CSV 格式无效：{error}") from error
    return _validate(LegacyImport(documents=_rows_to_documents(rows, fallback_title)))
