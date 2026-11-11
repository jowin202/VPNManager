"""Shared runtime context (settings, OPNsense access, write lock, jobs)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from .config import MIN_OPNSENSE_VERSION, Settings
from .opnsense.resources import Opnsense
from .opnsense.resources.system import parse_version
from .vpn.metadata import MetadataStore
from .vpn.steps import JobStore


class Busy(Exception):
    """Another write operation is already running."""


class VersionTooOld(Exception):
    pass


@dataclass
class AppContext:
    settings: Settings
    ops: Opnsense
    metadata: MetadataStore
    jobs: JobStore = field(default_factory=JobStore)
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    version: str | None = None

    @asynccontextmanager
    async def writing(self):
        """Only one write operation at a time: OPNsense applies act globally."""
        if self.write_lock.locked():
            raise Busy()
        async with self.write_lock:
            yield

    async def ensure_version(self) -> str | None:
        if self.settings.skip_version_check:
            return self.version
        if self.version is None:
            self.version = await self.ops.system.version()
        if not self.version:
            raise VersionTooOld("Could not determine the OPNsense version.")
        if parse_version(self.version) < MIN_OPNSENSE_VERSION:
            need = ".".join(map(str, MIN_OPNSENSE_VERSION))
            raise VersionTooOld(
                f"OPNsense {self.version} is too old, at least {need} is required "
                "(interface assignment via API and the fix for opnsense/core#10996)."
            )
        return self.version
