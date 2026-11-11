"""Thin async HTTP client for the OPNsense API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .errors import (
    OpnsenseAuthError,
    OpnsenseError,
    OpnsenseNotFound,
    OpnsenseUnreachable,
    OpnsenseValidationError,
)
from .tls import make_ssl_context

log = logging.getLogger(__name__)

READ_RETRIES = 2


class OpnsenseClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        *,
        verify: bool | str = True,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._http = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/api/",
            auth=(api_key, api_secret),
            # HTTP/2 is required, see tls.py
            http2=True,
            verify=make_ssl_context(verify),
            timeout=timeout,
            transport=transport,
            headers={"Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(self, path: str, params: dict | None = None) -> Any:
        """Read call, retried on network errors."""
        for attempt in range(READ_RETRIES + 1):
            try:
                return await self._request("GET", path, params=params)
            except OpnsenseUnreachable:
                if attempt == READ_RETRIES:
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))
        raise AssertionError("unreachable")

    async def post(self, path: str, payload: dict | None = None) -> Any:
        """Write call, never retried automatically."""
        return await self._request("POST", path, json=payload if payload is not None else {})

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        path = path.lstrip("/")
        log.debug("%s /api/%s", method, path)
        try:
            r = await self._http.request(method, path, **kwargs)
        except httpx.TransportError as e:
            raise OpnsenseUnreachable(f"OPNsense is not reachable: {type(e).__name__}", path=path) from None

        if r.status_code in (401, 403):
            raise OpnsenseAuthError(
                "Access denied – wrong API key, or the API user lacks the privilege for this endpoint.",
                path=path,
                status=r.status_code,
            )
        if r.status_code == 404:
            raise OpnsenseNotFound("Endpoint does not exist (plugin/version?).", path=path, status=404)
        if r.status_code >= 400:
            raise OpnsenseError(f"HTTP {r.status_code}: {r.text[:300]}", path=path, status=r.status_code)
        try:
            data = r.json()
        except ValueError:
            raise OpnsenseError("Response is not JSON.", path=path, status=r.status_code) from None

        _raise_for_result(data, path)
        return data


def _raise_for_result(data: Any, path: str) -> None:
    if not isinstance(data, dict):
        return
    validations = data.get("validations")
    if validations:
        details = "; ".join(f"{k}: {v}" for k, v in validations.items())
        raise OpnsenseValidationError(f"Validation failed: {details}", validations, path=path)
    result = data.get("result")
    if isinstance(result, str) and result.lower() in ("failed", "error"):
        raise OpnsenseError(f"OPNsense reported '{result}': {data.get('message', '')}".strip(), path=path)
    if data.get("status") == "error":
        raise OpnsenseError(f"OPNsense reported an error: {data.get('message', '')}".strip(), path=path)
