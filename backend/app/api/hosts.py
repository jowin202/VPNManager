from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..context import AppContext
from ..vpn import hosts
from .deps import get_ctx

router = APIRouter(prefix="/api/hosts", tags=["Hosts"])


class Assignment(BaseModel):
    ip: str = Field(max_length=15)
    vpn: str | None = Field(default=None, max_length=16)


@router.get("")
async def list_hosts(ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    return [h.to_dict() for h in await hosts.list_hosts(ctx)]


@router.put("/assignments")
async def put_assignments(body: list[Assignment], ctx: AppContext = Depends(get_ctx)) -> dict:
    await ctx.ensure_version()
    async with ctx.writing():
        return await hosts.assign(ctx, [(a.ip, a.vpn) for a in body])
