"""Regression for the offline test lifecycle: queued work finishes before stop."""

import asyncio

import pytest


@pytest.mark.asyncio
async def test_test_queue_shutdown_drains_pending_work(monkeypatch):
    from sag_api.jobs.inproc import InProcessAsyncQueue

    completed = []

    async def recover():
        pass

    async def run(job_id):
        await asyncio.sleep(0.01)
        completed.append(job_id)

    queue = InProcessAsyncQueue(None, None, concurrency=1)
    monkeypatch.setattr(queue, "_recover", recover)
    monkeypatch.setattr(queue, "_run", run)
    await queue.start()
    try:
        await queue.enqueue("first")
        await queue.enqueue("second")
    finally:
        await queue.stop()

    assert completed == ["first", "second"]
    assert not queue._workers
