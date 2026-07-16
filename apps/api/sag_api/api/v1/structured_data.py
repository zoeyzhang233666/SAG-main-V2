from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.db import get_session
from sag_api.core.deps import get_current_user, get_engine_manager, get_job_queue
from sag_api.core.errors import ValidationError
from sag_api.db.models import Job, User
from sag_api.enums import JobStatus, JobType
from sag_api.jobs import JobQueue
from sag_api.sag import EngineManager
from sag_api.schemas.job import JobOut
from sag_api.services.source_service import get_source
from sag_api.services.structured_data_service import (
    create_source_backup,
    export_structured_data,
)

router = APIRouter(prefix="/sources", tags=["structured-data"])


@router.post(
    "/{source_id}/structured-import",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def structured_import(
    source_id: str,
    file: UploadFile = File(...),
    format_name: Literal["auto", "json", "csv", "markdown"] = Query("auto", alias="format"),
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    _engine_manager: EngineManager = Depends(get_engine_manager),
    job_queue: JobQueue = Depends(get_job_queue),
) -> JobOut:
    """流式接收外部已完成切片、事件与实体抽取的数据，并立即交给后台批量入库。"""
    source = await get_source(session, source_id)
    filename = os.path.basename(file.filename or "structured-import.json")
    upload_id = uuid.uuid4().hex
    destination = Path(settings.upload_dir) / source.id / "structured-queue" / upload_id / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = settings.structured_import_max_mb * 1024 * 1024
    size = 0
    try:
        with destination.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise ValidationError(f"结构化数据文件超过 {settings.structured_import_max_mb}MB 上限")
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    job = Job(
        type=JobType.STRUCTURED_IMPORT,
        status=JobStatus.QUEUED,
        source_id=source.id,
        progress=0.01,
        payload={
            "path": str(destination),
            "filename": filename,
            "format": format_name,
            "size_bytes": size,
            "stage": "queued",
        },
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    await job_queue.enqueue(job.id)
    return JobOut.model_validate(job)


@router.get("/{source_id}/structured-export")
async def structured_export(
    source_id: str,
    format_name: Literal["json", "csv", "markdown"] = Query("json", alias="format"),
    profile: Literal["simple", "full"] = Query("full"),
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    source = await get_source(session, source_id)
    content, media_type, extension = await export_structured_data(
        session,
        source_id,
        format_name=format_name,
        profile=profile,
    )
    filename = quote(f"{source.name}-ChemClaw.{extension}")
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.get("/{source_id}/backup")
async def source_backup(
    source_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    source = await get_source(session, source_id)
    content = await create_source_backup(session, source_id)
    filename = quote(f"{source.name}-ChemClaw-backup.zip")
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
