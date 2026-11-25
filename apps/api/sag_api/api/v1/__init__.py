from fastapi import APIRouter

from sag_api.api.v1 import (
    activity,
    agents,
    attachments,
    auth,
    documents,
    insights,
    jobs,
    openai,
    search,
    sources,
    structured_data,
    system,
    universe,
)

api_router = APIRouter(prefix="/api/v1")
for _module in (
    auth,
    sources,
    structured_data,
    documents,
    insights,
    jobs,
    search,
    agents,
    openai,
    activity,
    attachments,
    system,
    universe,
):
    api_router.include_router(_module.router)
api_router.include_router(search.global_router)

__all__ = ["api_router"]
