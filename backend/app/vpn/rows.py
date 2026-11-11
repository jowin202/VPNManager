"""Tolerant access to search results. Column names of search_* responses are
not documented; candidates are tried in order."""

from __future__ import annotations


def pick(row: dict, *keys: str, default=None):
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return default


def uuid_of(row: dict) -> str | None:
    return pick(row, "uuid")


def as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in str(value).replace("\n", ",").split(",") if p.strip()]
