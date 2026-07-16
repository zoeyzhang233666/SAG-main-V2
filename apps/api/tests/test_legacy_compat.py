from __future__ import annotations

import json

import pytest

from sag_api.core.errors import ValidationError
from sag_api.services.legacy_compat import parse_legacy_import


def _assert_chemical_payload(data: bytes, filename: str) -> None:
    imported = parse_legacy_import(data, filename=filename)
    document = imported.documents[0]
    chunk = document.chunks[0]
    event = chunk.events[0]
    entity = event.entities[0]
    assert document.title == "催化剂项目"
    assert chunk.key == "chunk-001"
    assert chunk.content == "乙烯在催化剂作用下发生反应。"
    assert event.title == "乙烯催化反应"
    assert event.keywords == ["乙烯", "催化"]
    assert entity.type == "化学品"
    assert entity.name == "乙烯"
    assert entity.relation == "反应物"


def test_simple_json_preserves_external_chunks() -> None:
    value = {
        "documents": [
            {
                "title": "催化剂项目",
                "source": {"name": "外部模型", "type": "manual", "url": "https://example.test"},
                "chunks": [
                    {
                        "key": "chunk-001",
                        "heading": "反应步骤",
                        "content": "乙烯在催化剂作用下发生反应。",
                        "event": {
                            "title": "乙烯催化反应",
                            "summary": "乙烯发生催化反应。",
                            "content": "乙烯在催化剂作用下发生反应。",
                            "keywords": ["乙烯", "催化"],
                            "entities": [
                                {
                                    "type": "化学品",
                                    "name": "乙烯",
                                    "description": "烯烃原料",
                                    "relation": "反应物",
                                    "customAccuracy": 0.997,
                                }
                            ],
                        },
                        "customChunkField": "must-survive",
                    }
                ],
            }
        ],
    }
    imported = parse_legacy_import(json.dumps(value, ensure_ascii=False).encode(), filename="chemical.json")
    _assert_chemical_payload(json.dumps(value, ensure_ascii=False).encode(), "chemical.json")
    assert imported.documents[0].chunks[0].raw["customChunkField"] == "must-survive"
    assert imported.documents[0].chunks[0].events[0].entities[0].raw["customAccuracy"] == 0.997


def test_chinese_csv_and_markdown_tables_are_compatible() -> None:
    header = "文档标题,切片编号,切片标题,原文切片,事件标题,事件摘要,事件内容,关键词,实体类型,实体名称,实体说明,关系说明"
    row = (
        "催化剂项目,chunk-001,反应步骤,乙烯在催化剂作用下发生反应。,"
        "乙烯催化反应,乙烯发生催化反应。,乙烯在催化剂作用下发生反应。,"
        "乙烯；催化,化学品,乙烯,烯烃原料,反应物"
    )
    _assert_chemical_payload(f"{header}\n{row}\n".encode(), "chemical.csv")

    markdown = "\n".join(
        (
            "| 文档标题 | 切片编号 | 切片标题 | 原文切片 | 事件标题 | 事件摘要 | 事件内容 | "
            "关键词 | 实体类型 | 实体名称 | 实体说明 | 关系说明 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            "| 催化剂项目 | chunk-001 | 反应步骤 | 乙烯在催化剂作用下发生反应。 | 乙烯催化反应 | "
            "乙烯发生催化反应。 | 乙烯在催化剂作用下发生反应。 | 乙烯；催化 | 化学品 | 乙烯 | 烯烃原料 | 反应物 |",
        )
    )
    _assert_chemical_payload(markdown.encode(), "chemical.md")


def test_full_v1_backup_keeps_legacy_ids_and_unknown_fields() -> None:
    value = {
        "schemaVersion": 1,
        "project": {"id": "old-project", "name": "旧项目", "custom": {"precision": "high"}},
        "documents": [
            {
                "id": "old-document",
                "title": "催化剂项目",
                "sourceName": "旧来源",
                "chunks": [
                    {
                        "id": "chunk-001",
                        "systemChunkId": "old-system-chunk",
                        "heading": "反应步骤",
                        "rawContent": "乙烯在催化剂作用下发生反应。",
                        "events": [
                            {
                                "id": "old-event",
                                "title": "乙烯催化反应",
                                "summary": "乙烯发生催化反应。",
                                "content": "乙烯在催化剂作用下发生反应。",
                                "keywords": ["乙烯", "催化"],
                                "entities": [
                                    {
                                        "entityId": "old-entity",
                                        "entityType": "化学品",
                                        "entityName": "乙烯",
                                        "entityDescription": "烯烃原料",
                                        "relationDescription": "反应物",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    imported = parse_legacy_import(json.dumps(value, ensure_ascii=False).encode(), filename="backup.json")
    document = imported.documents[0]
    assert imported.project["custom"]["precision"] == "high"
    assert document.legacy_id == "old-document"
    assert document.chunks[0].system_chunk_id == "old-system-chunk"
    assert document.chunks[0].events[0].legacy_id == "old-event"
    assert document.chunks[0].events[0].entities[0].legacy_id == "old-entity"


def test_v1_strict_validation_rejects_event_without_entity() -> None:
    value = {
        "title": "bad",
        "chunks": [{"key": "1", "content": "raw", "event": {"title": "event", "entities": []}}],
    }
    with pytest.raises(ValidationError, match="没有实体"):
        parse_legacy_import(json.dumps(value).encode(), filename="bad.json")
