"""Interpretation of wireguard/service/show and routes/gateway/status."""

from __future__ import annotations

import time

from .rows import pick

HANDSHAKE_MAX_AGE = 180


def handshake_age(show: dict | list | None, peer_pubkey: str | None, now: float | None = None) -> int | None:
    """Seconds since the peer's last handshake, None if there was none."""
    rows = show.get("rows", []) if isinstance(show, dict) else (show or [])
    now = now or time.time()
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = pick(row, "public-key", "public_key", "pubkey", "peer")
        if peer_pubkey and key != peer_pubkey:
            continue
        age = pick(row, "latest-handshake-age", "latest_handshake_age")
        if age is not None:
            try:
                return int(float(age))
            except (TypeError, ValueError):
                pass
        ts = pick(row, "latest-handshake", "latest_handshake")
        try:
            ts = int(float(ts))
        except (TypeError, ValueError):
            continue
        if ts > 0:
            return max(0, int(now - ts))
    return None


def gateway_state(items: list[dict], gateway_name: str | None) -> dict:
    for item in items:
        if pick(item, "name") != gateway_name:
            continue
        raw = str(pick(item, "status", default="")).lower()
        if raw in ("none", "online", "up"):
            state = "online"
        elif "down" in raw:
            state = "offline"
        elif raw in ("loss", "delay", "delay+loss"):
            state = "warning"
        else:
            state = "unknown"
        return {
            "state": state,
            "detail": pick(item, "status_translated", "status"),
            "delay": pick(item, "delay"),
            "loss": pick(item, "loss"),
        }
    return {"state": "missing", "detail": None, "delay": None, "loss": None}


STARTUP_GRACE = 180


def effective_gateway_state(gw: dict, handshake: int | None, created_at: float | None,
                            now: float | None = None) -> dict:
    """Gateway monitoring averages loss over ~60 s; right after creation, pings
    sent before the tunnel came up still count. With a fresh handshake,
    "offline" during this phase is not an error."""
    now = now or time.time()
    young = created_at is not None and now - created_at < STARTUP_GRACE
    if young and gw["state"] in ("offline", "warning") and handshake is not None and handshake <= HANDSHAKE_MAX_AGE:
        return {**gw, "state": "starting"}
    return gw
