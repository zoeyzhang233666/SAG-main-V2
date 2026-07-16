from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.db import get_session
from sag_api.core.deps import get_current_user
from sag_api.core.errors import NotFoundError
from sag_api.db.models import Agent, ScheduleRun, ScheduleTask, User
from sag_api.schemas.common import Ok
from sag_api.schemas.schedule import ScheduleCreate, ScheduleOut, ScheduleRunOut, ScheduleUpdate

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.get("", response_model=list[ScheduleOut])
async def list_tasks(
    _user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> list[ScheduleOut]:
    rows = list(
        (
            await session.execute(
                select(ScheduleTask).order_by(ScheduleTask.next_run_at, ScheduleTask.created_at.desc())
            )
        ).scalars()
    )
    return [ScheduleOut.model_validate(row) for row in rows]


@router.post("", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
async def create_task(
    body: ScheduleCreate,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ScheduleOut:
    if await session.get(Agent, body.agent_id) is None:
        raise NotFoundError("龙虾不存在")
    task = ScheduleTask(**body.model_dump())
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return ScheduleOut.model_validate(task)


@router.patch("/{task_id}", response_model=ScheduleOut)
async def update_task(
    task_id: str,
    body: ScheduleUpdate,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ScheduleOut:
    task = await session.get(ScheduleTask, task_id)
    if task is None:
        raise NotFoundError("日程任务不存在")
    values = body.model_dump(exclude_unset=True)
    if values.get("agent_id") and await session.get(Agent, values["agent_id"]) is None:
        raise NotFoundError("龙虾不存在")
    for key, value in values.items():
        setattr(task, key, value)
    await session.commit()
    await session.refresh(task)
    return ScheduleOut.model_validate(task)


@router.delete("/{task_id}", response_model=Ok)
async def delete_task(
    task_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Ok:
    task = await session.get(ScheduleTask, task_id)
    if task is None:
        raise NotFoundError("日程任务不存在")
    await session.delete(task)
    await session.commit()
    return Ok(detail="日程任务已删除")


@router.post("/{task_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_task(
    task_id: str,
    request: Request,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if await session.get(ScheduleTask, task_id) is None:
        raise NotFoundError("日程任务不存在")
    await request.app.state.schedule_runner.run_now(task_id)
    return {"ok": True, "task_id": task_id}


@router.get("/{task_id}/runs", response_model=list[ScheduleRunOut])
async def list_runs(
    task_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ScheduleRunOut]:
    if await session.get(ScheduleTask, task_id) is None:
        raise NotFoundError("日程任务不存在")
    rows = list(
        (
            await session.execute(
                select(ScheduleRun)
                .where(ScheduleRun.task_id == task_id)
                .order_by(ScheduleRun.started_at.desc())
                .limit(30)
            )
        ).scalars()
    )
    return [ScheduleRunOut.model_validate(row) for row in rows]
