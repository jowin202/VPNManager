"""Naming conventions for all objects the VPN Manager creates.

Free-text descriptions carry `<prefix>:<name>`. Fields with a restricted
character set (WireGuard names) carry `<prefix>_<name>`. Only objects with
these markers are ever touched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NAME_RE = re.compile(r"[A-Za-z0-9_]{1,16}")


class InvalidName(ValueError):
    pass


def validate_name(name: str) -> str:
    if not NAME_RE.fullmatch(name or ""):
        raise InvalidName("Name: only A-Z, a-z, 0-9 and _, 1–16 characters.")
    if not name[0].isalpha():
        raise InvalidName("Name must start with a letter (OPNsense alias names).")
    return name


@dataclass(frozen=True)
class Names:
    prefix: str
    name: str

    @property
    def upper(self) -> str:
        return self.name.upper()

    @property
    def tag(self) -> str:
        """Description for free-text fields."""
        return f"{self.prefix}:{self.name}"

    @property
    def wg_name(self) -> str:
        return f"{self.prefix}_{self.name}"

    @property
    def interface_descr(self) -> str:
        # Interface descriptions only allow [A-Za-z0-9_]
        return f"{self.prefix.upper()}_{self.upper}"

    @property
    def gateway(self) -> str:
        return f"VPN_{self.upper}_GW"

    @property
    def hosts_alias(self) -> str:
        return f"VPN_{self.upper}_HOSTS"

    @property
    def local_alias(self) -> str:
        return f"VPN_{self.upper}_LOCAL"

    def rule_descr(self, role: str) -> str:
        return f"{self.tag} {role}"


def interface_owner(descr: str | None, prefix: str) -> str | None:
    """Upper-case VPN name from an interface description `<PREFIX>_<NAME>`."""
    marker = f"{prefix.upper()}_"
    if descr and descr.startswith(marker) and len(descr) > len(marker):
        return descr[len(marker):]
    return None


def owner_of(text: str | None, prefix: str) -> str | None:
    """VPN name from a description/name if the object belongs to us."""
    if not text:
        return None
    m = re.match(rf"^{re.escape(prefix)}[:_]([A-Za-z0-9_]{{1,16}})(?:\s|$)", text)
    return m.group(1) if m else None
