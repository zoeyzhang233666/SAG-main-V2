"""测试夹具：在导入 sag_api 之前把配置指向临时目录（settings 为进程级单例）。"""

import os
import sys
import tempfile

import httpx
import pytest

_TMP = tempfile.mkdtemp(prefix="sag-test-")
os.environ.setdefault("SAG_DATABASE_URL", f"sqlite+aiosqlite:///{_TMP}/sag.db")
os.environ.setdefault("SAG_DATA_DIR", f"{_TMP}/sag")
os.environ.setdefault("SAG_UPLOAD_DIR", f"{_TMP}/uploads")
os.environ.setdefault("SAG_DEBUG", "false")
os.environ.setdefault("SAG_SAG_LANGUAGE", "zh")
# 强制离线：即使存在带真实 key 的 .env，也保证测试确定性（不发起 LLM 调用）
os.environ["SAG_LLM_API_KEY"] = ""
os.environ["SAG_LLM_BASE_URL"] = ""
os.environ["SAG_EMBEDDING_API_KEY"] = ""
os.environ["SAG_MINERU_API_KEY"] = ""
os.environ["SAG_MINERU_BASE_URL"] = ""
# Warmup is a production optimization; avoid racing unrelated source engines in shared test lifespans.
os.environ["SAG_ENGINE_WARMUP_COUNT"] = "0"


@pytest.fixture(autouse=True)
def _offline_http(monkeypatch):
    """Real HTTP transports never contact model providers in offline tests.

    ASGITransport and explicit MockTransport fixtures remain untouched. Return a
    deterministic non-retryable auth response for unmocked outbound requests,
    matching the missing-credentials behavior the smoke test exercises.
    """
    def reject(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "Offline test: no external credentials", "type": "invalid_request_error"}},
            request=request,
        )

    async def reject_async(_transport, request: httpx.Request) -> httpx.Response:
        return reject(request)

    def reject_sync(_transport, request: httpx.Request) -> httpx.Response:
        return reject(request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject_sync)


@pytest.fixture(autouse=True)
async def _isolate_persisted_jobs():
    """A test must not recover queued jobs created by an earlier app lifespan."""
    yield
    if "sag_api.core.db" not in sys.modules:
        return

    from sqlalchemy import delete, inspect

    from sag_api.core.db import SessionLocal, engine
    from sag_api.db.models import Job

    async with engine.connect() as connection:
        exists = await connection.run_sync(lambda sync: inspect(sync).has_table(Job.__tablename__))
    if not exists:
        return

    async with SessionLocal() as session:
        await session.execute(delete(Job))
        await session.commit()
