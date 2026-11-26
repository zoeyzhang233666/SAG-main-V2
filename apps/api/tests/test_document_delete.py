"""Async document deletion: stop structured writers and clean graph rows."""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest


@pytest.mark.asyncio
async def test_delete_enqueues_job_and_pauses_structured_index(monkeypatch):
    from sag_api.core.config import settings
    from sag_api.core.db import SessionLocal
    from sag_api.db.models import Document, Job, Source
    from sag_api.enums import DocumentStatus, JobStatus, JobType
    from sag_api.main import app

    monkeypatch.setattr(settings, "embedding_api_key", "offline-fixture-key")
    article_id = uuid.uuid4().hex
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            token = (
                await client.post(
                    "/api/v1/auth/register",
                    json={"email": "delete-enqueue@x.com", "password": "password123"},
                )
            ).json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            source = (
                await client.post("/api/v1/sources", headers=headers, json={"name": "enqueue-lab"})
            ).json()
            source_id = source["id"]

            async with SessionLocal() as session:
                db_source = await session.get(Source, source_id)
                assert db_source is not None
                document_id = uuid.uuid4().hex
                session.add(
                    Document(
                        id=document_id,
                        source_id=source_id,
                        filename="handbook.json",
                        content_type="application/x-chemclaw-structured",
                        size_bytes=128,
                        storage_path="/tmp/handbook.json",
                        status=DocumentStatus.PENDING,
                        chunk_count=1,
                        event_count=1,
                        sag_source_id=article_id,
                    )
                )
                db_source.document_count = 1
                db_source.chunk_count = 1
                db_source.event_count = 1
                index_job = Job(
                    type=JobType.STRUCTURED_INDEX,
                    source_id=source_id,
                    status=JobStatus.QUEUED,
                    payload={
                        "source_config_id": db_source.sag_source_config_id,
                        "article_ids": [article_id],
                        "event_ids": [],
                        "chunk_ids": [],
                        "stage": "queued",
                    },
                )
                session.add(index_job)
                await session.commit()
                await session.refresh(index_job)
                index_job_id = index_job.id

            reindex = await client.post(
                f"/api/v1/sources/{source_id}/documents/{document_id}/reprocess",
                headers=headers,
            )
            assert reindex.status_code == 200
            assert reindex.json()["type"] == "structured_index"
            assert reindex.json()["id"] == index_job_id

            deleted = await client.delete(
                f"/api/v1/sources/{source_id}/documents/{document_id}",
                headers=headers,
            )
            assert deleted.status_code == 200
            body = deleted.json()
            assert body["type"] == "delete_document"
            assert body["status"] in {"queued", "running", "succeeded"}

            again = await client.delete(
                f"/api/v1/sources/{source_id}/documents/{document_id}",
                headers=headers,
            )
            assert again.status_code == 200
            assert again.json()["id"] == body["id"]

            async with SessionLocal() as session:
                document = await session.get(Document, document_id)
                assert document is None or document.status == DocumentStatus.DELETING
                index_row = await session.get(Job, index_job_id)
                assert index_row is not None
                # Queued structured index must be fenced before cleanup starts.
                assert index_row.status == JobStatus.PAUSED
                assert (index_row.payload or {}).get("pause_requested") is True

            for _ in range(200):
                docs = (
                    await client.get(f"/api/v1/sources/{source_id}/documents", headers=headers)
                ).json()
                async with SessionLocal() as session:
                    delete_row = await session.get(Job, body["id"])
                    if (
                        not any(item["id"] == document_id for item in docs)
                        and delete_row is not None
                        and delete_row.status == JobStatus.SUCCEEDED
                    ):
                        break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("queued structured delete did not finish")

            source_after = (
                await client.get(f"/api/v1/sources/{source_id}", headers=headers)
            ).json()
            assert source_after["document_count"] == 0


@pytest.mark.asyncio
async def test_delete_cancels_running_structured_index(monkeypatch):
    from sag_api.core.db import SessionLocal
    from sag_api.db.models import Document, Job, Source
    from sag_api.enums import DocumentStatus, JobStatus, JobType
    from sag_api.jobs import tasks as job_tasks
    from sag_api.jobs.control import JobPaused
    from sag_api.main import app
    from sag_api.sag.engine_manager import EngineManager

    article_id = uuid.uuid4().hex
    index_entered = asyncio.Event()
    delete_started = asyncio.Event()

    async def fake_delete_document_data(self, source_config_id, document_source_id, *, source=None):
        assert await document_source_id() == article_id
        delete_started.set()

    monkeypatch.setattr(EngineManager, "delete_document_data", fake_delete_document_data)

    async def blocking_index(session, job, *, engine_manager, job_queue=None):
        index_entered.set()
        while True:
            async with SessionLocal() as control:
                current = await control.get(Job, job.id)
                if current is None or (current.payload or {}).get("pause_requested"):
                    raise JobPaused()
            await asyncio.sleep(0.02)

    monkeypatch.setitem(job_tasks.TASK_HANDLERS, JobType.STRUCTURED_INDEX, blocking_index)

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            token = (
                await client.post(
                    "/api/v1/auth/register",
                    json={"email": "delete-cancel@x.com", "password": "password123"},
                )
            ).json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            source = (
                await client.post("/api/v1/sources", headers=headers, json={"name": "cancel-lab"})
            ).json()
            source_id = source["id"]

            async with SessionLocal() as session:
                db_source = await session.get(Source, source_id)
                assert db_source is not None
                document_id = uuid.uuid4().hex
                session.add(
                    Document(
                        id=document_id,
                        source_id=source_id,
                        filename="handbook.json",
                        content_type="application/x-chemclaw-structured",
                        size_bytes=64,
                        storage_path="/tmp/handbook-cancel.json",
                        status=DocumentStatus.READY,
                        chunk_count=1,
                        event_count=1,
                        sag_source_id=article_id,
                    )
                )
                db_source.document_count = 1
                db_source.chunk_count = 1
                db_source.event_count = 1
                index_job = Job(
                    type=JobType.STRUCTURED_INDEX,
                    source_id=source_id,
                    status=JobStatus.QUEUED,
                    payload={
                        "source_config_id": db_source.sag_source_config_id,
                        "article_ids": [article_id],
                        "event_ids": [],
                        "chunk_ids": [],
                        "stage": "queued",
                    },
                )
                session.add(index_job)
                await session.commit()
                await session.refresh(index_job)
                index_job_id = index_job.id

            await app.state.job_queue.enqueue(index_job_id)
            await asyncio.wait_for(index_entered.wait(), timeout=2)

            deleted = await client.delete(
                f"/api/v1/sources/{source_id}/documents/{document_id}",
                headers=headers,
            )
            assert deleted.status_code == 200
            body = deleted.json()

            for _ in range(400):
                docs = (
                    await client.get(f"/api/v1/sources/{source_id}/documents", headers=headers)
                ).json()
                async with SessionLocal() as session:
                    delete_row = await session.get(Job, body["id"])
                    index_row = await session.get(Job, index_job_id)
                    done = (
                        not any(item["id"] == document_id for item in docs)
                        and delete_row is not None
                        and delete_row.status == JobStatus.SUCCEEDED
                        and index_row is not None
                        and index_row.status in {JobStatus.PAUSED, JobStatus.FAILED}
                    )
                if done:
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("running structured index was not cancelled for delete")

            assert delete_started.is_set()


@pytest.mark.asyncio
async def test_delete_pending_metadata_only_document():
    from sag_api.core.db import SessionLocal
    from sag_api.db.models import Document, Source
    from sag_api.enums import DocumentStatus
    from sag_api.main import app

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            token = (
                await client.post(
                    "/api/v1/auth/register",
                    json={"email": "delete-meta@x.com", "password": "password123"},
                )
            ).json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            source = (
                await client.post("/api/v1/sources", headers=headers, json={"name": "meta"})
            ).json()

            document_id = uuid.uuid4().hex
            async with SessionLocal() as session:
                db_source = await session.get(Source, source["id"])
                assert db_source is not None
                session.add(
                    Document(
                        id=document_id,
                        source_id=source["id"],
                        filename="empty.md",
                        content_type="text/markdown",
                        size_bytes=1,
                        storage_path="",
                        status=DocumentStatus.PENDING,
                    )
                )
                db_source.document_count = 1
                await session.commit()

            deleted = await client.delete(
                f"/api/v1/sources/{source['id']}/documents/{document_id}",
                headers=headers,
            )
            assert deleted.status_code == 200
            assert deleted.json()["type"] == "delete_document"

            for _ in range(50):
                docs = (
                    await client.get(
                        f"/api/v1/sources/{source['id']}/documents", headers=headers
                    )
                ).json()
                if not any(item["id"] == document_id for item in docs):
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("metadata-only delete did not finish")

            source_after = (
                await client.get(f"/api/v1/sources/{source['id']}", headers=headers)
            ).json()
            assert source_after["document_count"] == 0


@pytest.mark.asyncio
async def test_cancel_running_keeps_worker_alive(monkeypatch):
    from sag_api.core.db import SessionLocal
    from sag_api.db.models import Job
    from sag_api.enums import JobStatus, JobType
    from sag_api.jobs import tasks as job_tasks
    from sag_api.main import app

    started = asyncio.Event()
    finished = asyncio.Event()

    async def hanging(session, job, *, engine_manager, job_queue=None):
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            finished.set()
            raise

    monkeypatch.setitem(job_tasks.TASK_HANDLERS, JobType.STRUCTURED_INDEX, hanging)
    async with app.router.lifespan_context(app):
        async with SessionLocal() as session:
            job = Job(type=JobType.STRUCTURED_INDEX, status=JobStatus.QUEUED)
            session.add(job)
            await session.commit()
            job_id = job.id
        await app.state.job_queue.enqueue(job_id)
        await asyncio.wait_for(started.wait(), timeout=2)
        assert app.state.job_queue.cancel_running(job_id) is True
        await asyncio.wait_for(finished.wait(), timeout=2)
        await asyncio.wait_for(app.state.job_queue._queue.join(), timeout=2)

        probe = asyncio.Event()

        async def quick(session, job, *, engine_manager, job_queue=None):
            probe.set()

        monkeypatch.setitem(job_tasks.TASK_HANDLERS, JobType.STRUCTURED_INDEX, quick)
        async with SessionLocal() as session:
            follow = Job(type=JobType.STRUCTURED_INDEX, status=JobStatus.QUEUED)
            session.add(follow)
            await session.commit()
            follow_id = follow.id
        await app.state.job_queue.enqueue(follow_id)
        await asyncio.wait_for(probe.wait(), timeout=2)
        async with SessionLocal() as session:
            cancelled = await session.get(Job, job_id)
            assert cancelled is not None
            assert cancelled.status == JobStatus.PAUSED
