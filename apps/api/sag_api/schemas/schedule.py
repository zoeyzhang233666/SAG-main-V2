from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ScheduleFrequency = Literal["once", "daily", "weekdays", "weekly"]


class ScheduleCreate(BaseModel):
    agent_id: str
    name: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=50_000)
    frequency: ScheduleFrequency = "once"
    timezone: str = "Asia/Shanghai"
    next_run_at: datetime
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    agent_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    prompt: str | None = Field(default=None, min_length=1, max_length=50_000)
    frequency: ScheduleFrequency | None = None
    timezone: str | None = None
    next_run_at: datetime | None = None
    enabled: bool | None = None


class ScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    name: str
    prompt: str
    frequency: str
    timezone: str
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_status: str
    last_error: str | None
    last_thread_id: str | None
    created_at: datetime
    updated_at: datetime


class ScheduleRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    status: str
    thread_id: str | None
    started_at: datetime
    finished_at: datetime | None
    error: str | None
