"""FastAPI app: REST API under /api, Angular build under /."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from .api import hosts, system, vpns
from .api.deps import require_auth
from .config import Settings, load_settings
from .context import AppContext, Busy, VersionTooOld
from .opnsense.client import OpnsenseClient
from .opnsense.errors import (
    OpnsenseAuthError,
    OpnsenseError,
    OpnsenseUnreachable,
    OpnsenseValidationError,
    SchemaError,
)
from .opnsense.resources import Opnsense
from .vpn.hosts import AssignmentError
from .vpn.metadata import MetadataStore
from .vpn.naming import InvalidName
from .vpn.parser import ConfigParseError
from .vpn.provisioner import Conflict, HostsAssigned

log = logging.getLogger("vpnmgr")

STARTUP_CHECK_TIMEOUT = 10.0

FRONTEND_DIR =Path(__file__).resolve().parents[2] / "frontend" / "dist" / "vpn-manager" / "browser"


def create_app(settings: Settings | None = None, *, transport=None, check_on_startup: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        s = settings or load_settings()
        if s.tls_warning:
            log.warning(s.tls_warning)
        client = OpnsenseClient(s.opnsense_url, s.api_key, s.api_secret,
                                verify=s.verify_tls, timeout=s.request_timeout, transport=transport)
        ctx = AppContext(settings=s, ops=Opnsense.create(client), metadata=MetadataStore(s.data_dir))
        app.state.ctx = ctx
        if check_on_startup and not s.skip_version_check:
            # Hard minimum version: refuse to start with an OPNsense that is too old.
            # If the box is unreachable, start anyway – write operations re-check the
            # version before running and the system page shows the error.
            try:
                version = await asyncio.wait_for(ctx.ensure_version(), STARTUP_CHECK_TIMEOUT)
                log.warning("Detected OPNsense %s.", version)
            except (OpnsenseError, asyncio.TimeoutError) as e:
                ctx.version = None
                reason = e.message if isinstance(e, OpnsenseError) else "Timeout"
                log.error("OPNsense not reachable at startup (%s). Starting anyway.", reason)
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(title="OPNsense VPN Manager", lifespan=lifespan)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        # Liveness only; does not touch OPNsense and needs no token
        return {"status": "ok"}

    auth = [Depends(require_auth)]
    app.include_router(system.router, dependencies=auth)
    app.include_router(vpns.router, dependencies=auth)
    app.include_router(hosts.router, dependencies=auth)

    def err(status: int, message: str, **extra) -> JSONResponse:
        return JSONResponse(status_code=status, content={"detail": message, **extra})

    @app.exception_handler(ConfigParseError)
    async def _parse(_: Request, e: ConfigParseError):
        return err(422, str(e))

    @app.exception_handler(InvalidName)
    async def _name(_: Request, e: InvalidName):
        return err(422, str(e))

    @app.exception_handler(AssignmentError)
    async def _assign(_: Request, e: AssignmentError):
        return err(422, str(e))

    @app.exception_handler(ValueError)
    async def _value(_: Request, e: ValueError):
        return err(422, str(e))

    @app.exception_handler(LookupError)
    async def _lookup(_: Request, e: LookupError):
        return err(404, str(e.args[0]) if e.args else "Not found.")

    @app.exception_handler(Busy)
    async def _busy(_: Request, e: Busy):
        return err(409, "Another write operation is running. Please wait a moment.", code="busy")

    @app.exception_handler(Conflict)
    async def _conflict(_: Request, e: Conflict):
        return err(409, e.message, code="conflict", details=e.details)

    @app.exception_handler(HostsAssigned)
    async def _hosts(_: Request, e: HostsAssigned):
        return err(409, str(e), code="hosts_assigned", hosts=e.hosts)

    @app.exception_handler(VersionTooOld)
    async def _version(_: Request, e: VersionTooOld):
        return err(412, str(e), code="version")

    @app.exception_handler(OpnsenseError)
    async def _opnsense(_: Request, e: OpnsenseError):
        status = 504 if isinstance(e, OpnsenseUnreachable) else 422 if isinstance(
            e, (OpnsenseValidationError, SchemaError)) else 502
        if isinstance(e, OpnsenseAuthError):
            status = 502
        return err(status, e.message, opnsense=e.to_dict())

    if FRONTEND_DIR.is_dir():
        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api/"):
                return err(404, "Not found.")
            candidate = (FRONTEND_DIR / path).resolve()
            if path and candidate.is_file() and FRONTEND_DIR in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIR / "index.html")

    return app


app = create_app()
