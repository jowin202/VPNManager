from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..context import AppContext, Busy
from ..vpn import provisioner
from ..vpn.inventory import scan
from ..vpn.naming import Names
from ..vpn.parser import parse_wg_quick
from ..vpn.provisioner import CreateRequest
from ..vpn.rows import as_list, pick, uuid_of
from ..vpn.status import effective_gateway_state, gateway_state, handshake_age
from .deps import get_ctx

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["VPN"])

_tasks: set[asyncio.Task] = set()


class CreateVpnBody(BaseModel):
    name: str = Field(max_length=16)
    config: str = Field(max_length=10_000)
    gateway_ip: str | None = None
    allow_pending_changes: bool = False
    tunnel_address: str | None = Field(default=None, max_length=18)


class ParseBody(BaseModel):
    config: str = Field(max_length=10_000)


@router.get("/vpns")
async def list_vpns(ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    ops, prefix = ctx.ops, ctx.settings.prefix
    inventory = await scan(ops, prefix)
    show = await ops.wireguard.show()
    gw_status = await ops.gateways.status()
    meta = ctx.metadata.all()
    result = []
    for name, objs in sorted(inventory.items()):
        names = Names(prefix, name)
        m = meta.get(name, {})
        peer_key = m.get("peer_public_key") or (pick(objs.peers[0], "pubkey") if objs.peers else None)
        hosts: list[str] = []
        hosts_row = next((r for r in objs.aliases if pick(r, "name") == names.hosts_alias), None)
        if hosts_row and uuid_of(hosts_row):
            hosts = as_list((await ops.aliases.get(uuid_of(hosts_row))).get("content"))
        result.append({
            "name": name,
            "complete": objs.complete,
            "objects": objs.summary(),
            "device": objs.devices[0] if objs.devices else None,
            "interface": objs.interfaces[0] if objs.interfaces else None,
            "gateway_name": names.gateway,
            "hosts_alias": names.hosts_alias,
            "handshake_age": (age := handshake_age(show, peer_key) if peer_key else None),
            "gateway": effective_gateway_state(gateway_state(gw_status, names.gateway), age, m.get("created_at")),
            "hosts": hosts,
            "host_count": len(hosts),
            "dns": m.get("dns", []),
            "endpoint": m.get("endpoint"),
            "addresses": m.get("addresses", []),
            "gateway_ip": m.get("gateway_ip"),
            "created_at": m.get("created_at"),
        })
    return result


@router.post("/vpns/parse")
async def parse_config(body: ParseBody, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Preview of a config without secrets, including an adjusted tunnel address if needed."""
    original = parse_wg_quick(body.config)
    used, interfaces = await provisioner.used_addresses(ctx)
    cfg, rewritten_from = provisioner.resolve_tunnel_address(original, used, interfaces)
    return {
        "rewritten_from": rewritten_from,
        "dropped_ipv6": [str(a) for a in original.ipv6_addresses if a not in cfg.addresses],
        "public_key": cfg.public_key,
        "addresses": [str(a) for a in cfg.addresses],
        "dns": [str(d) for d in cfg.dns],
        "mtu": cfg.mtu,
        "peer": {
            "public_key": cfg.peer.public_key,
            "endpoint": f"{cfg.peer.endpoint_host}:{cfg.peer.endpoint_port}",
            "allowed_ips": [str(n) for n in cfg.peer.allowed_ips],
            "keepalive": cfg.peer.keepalive,
            "preshared_key": bool(cfg.peer.preshared_key),
        },
        "gateway_ip": provisioner.choose_gateway_ip(cfg, use_dns=rewritten_from is None),
    }


@router.post("/vpns", status_code=202)
async def create_vpn(body: CreateVpnBody, ctx: AppContext = Depends(get_ctx)) -> dict:
    await ctx.ensure_version()
    req = CreateRequest(body.name, body.config, body.gateway_ip, body.allow_pending_changes,
                        body.tunnel_address or None)
    names, cfg = provisioner.prepare(ctx.settings.prefix, req)

    # Take the lock right away; the actual workflow continues in the background
    if ctx.write_lock.locked():
        raise Busy()
    await ctx.write_lock.acquire()
    try:
        plan = await provisioner.precheck(ctx, names, cfg, req)
    except BaseException:
        ctx.write_lock.release()
        raise
    job = ctx.jobs.add(provisioner.new_create_job(names.name))

    async def run() -> None:
        try:
            await provisioner.create_vpn(ctx, job, plan, allow_pending_changes=req.allow_pending_changes,
                                        verify_timeout=ctx.settings.verify_timeout)
        finally:
            ctx.write_lock.release()

    task = asyncio.create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"job_id": job.id, "job": job.to_dict()}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    job = ctx.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    return job.to_dict()


@router.delete("/vpns/{name}")
async def delete_vpn(
    name: str,
    confirm_hosts: bool = Query(False),
    allow_pending_changes: bool = Query(False),
    ctx: AppContext = Depends(get_ctx),
) -> dict:
    await ctx.ensure_version()
    async with ctx.writing():
        job = await provisioner.delete_vpn(
            ctx, name, confirm_hosts=confirm_hosts, allow_pending_changes=allow_pending_changes
        )
    ctx.jobs.add(job)
    return job.to_dict()
