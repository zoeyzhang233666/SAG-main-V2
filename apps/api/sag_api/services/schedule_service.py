"""ChemClaw 日程任务：持久化调度，并通过现有 Agent 编排链执行。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from sag_api.core.db import SessionLocal
from sag_api.core.errors import ConfigurationError, NotFoundError
from sag_api.core.logging import get_logger
from sag_api.db.models import Agent, ScheduleRun, ScheduleTask
from sag_api.services import agent_domain, agent_service

log = get_logger("schedules")


def _now() -> datetime:
    return datetime.now(UTC)


def next_occurrence(task: ScheduleTask, *, after: datetime) -> datetime | None:
    if task.frequency == "once":
        return None
    value = task.next_run_at or after
    delta = timedelta(days=7 if task.frequency == "weekly" else 1)
    while value <= after:
        value += delta
    if task.frequency == "weekdays":
        while value.weekday() >= 5:
            value += timedelta(days=1)
    return value


class ScheduleRunner:
    def __init__(self, *, engine_manager, llm, tool_registry, runtime) -> None:
        self.engine_manager = engine_manager
        self.llm = llm
        self.tool_registry = tool_registry
        self.runtime = runtime
        self._loop_task: asyncio.Task | None = None
        self._runs: set[asyncio.Task] = set()

    async def start(self) -> None:
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._loop(), name="chemclaw-schedule-runner")

    async def stop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
            self._loop_task = None
        running = list(self._runs)
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        self._runs.clear()

    async def run_now(self, task_id: str) -> None:
        async with SessionLocal() as session:
            task = await session.get(ScheduleTask, task_id)
            if task is None:
                raise NotFoundError("日程任务不存在")
            if task.last_status == "running":
                return
            task.last_status = "running"
            task.last_error = None
            await session.commit()
        self._spawn(task_id, manual=True)

    def _spawn(self, task_id: str, *, manual: bool) -> None:
        run = asyncio.create_task(self._execute(task_id, manual=manual), name=f"schedule-{task_id}")
        self._runs.add(run)
        run.add_done_callback(self._runs.discard)

    async def _loop(self) -> None:
        while True:
            try:
                await self._claim_due()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("日程扫描失败")
            await asyncio.sleep(15)

    async def _claim_due(self) -> None:
        now = _now()
        async with SessionLocal() as session:
            tasks = list(
                (
                    await session.execute(
                        select(ScheduleTask).where(
                            ScheduleTask.enabled.is_(True),
                            ScheduleTask.next_run_at.is_not(None),
                            ScheduleTask.next_run_at <= now,
                            ScheduleTask.last_status != "running",
                        )
                    )
                ).scalars()
            )
            for task in tasks:
                task.last_status = "running"
                task.last_error = None
            await session.commit()
        for task in tasks:
            self._spawn(task.id, manual=False)

    async def _execute(self, task_id: str, *, manual: bool) -> None:
        run_id: str | None = None
        thread_id: str | None = None
        try:
            if not self.llm.configured:
                raise ConfigurationError("尚未配置模型，日程任务无法执行")
            async with SessionLocal() as session:
                task = await session.get(ScheduleTask, task_id)
                if task is None:
                    return
                agent = await session.get(Agent, task.agent_id)
                if agent is None:
                    raise NotFoundError("日程绑定的龙虾不存在")
                started = _now()
                run = ScheduleRun(task_id=task.id, status="running", started_at=started)
                session.add(run)
                thread = await agent_domain.create_thread(session, agent, task.name)
                thread_id = thread.id
                run.thread_id = thread.id
                await session.commit()
                await session.refresh(run)
                run_id = run.id
                plan = await agent_domain.prepare_ask(
                    session,
                    agent=agent,
                    thread=thread,
                    query=task.prompt,
                    attachments=None,
                    source_ids=None,
                    llm=self.llm,
                )

            async for _event in agent_service.generate_stream(
                SessionLocal,
                plan=plan,
                agent=agent,
                thread_id=thread_id,
                engine_manager=self.engine_manager,
                llm=self.llm,
                tool_registry=self.tool_registry,
                runtime=self.runtime,
                knowledge_only=False,
            ):
                pass

            async with SessionLocal() as session:
                task = await session.get(ScheduleTask, task_id)
                run = await session.get(ScheduleRun, run_id) if run_id else None
                finished = _now()
                if run is not None:
                    run.status = "succeeded"
                    run.finished_at = finished
                if task is not None:
                    task.last_status = "succeeded"
                    task.last_run_at = finished
                    task.last_thread_id = thread_id
                    task.last_error = None
                    if not manual:
                        task.next_run_at = next_occurrence(task, after=finished)
                        if task.next_run_at is None:
                            task.enabled = False
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            message = getattr(error, "message", None) or str(error)
            log.warning("日程执行失败 task=%s: %s", task_id, message)
            async with SessionLocal() as session:
                task = await session.get(ScheduleTask, task_id)
                run = await session.get(ScheduleRun, run_id) if run_id else None
                finished = _now()
                if run is not None:
                    run.status = "failed"
                    run.finished_at = finished
                    run.error = message
                if task is not None:
                    task.last_status = "failed"
                    task.last_run_at = finished
                    task.last_thread_id = thread_id
                    task.last_error = message
                    if not manual:
                        task.next_run_at = next_occurrence(task, after=finished)
                        if task.next_run_at is None:
                            task.enabled = False
                await session.commit()
