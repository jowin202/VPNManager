"""Parser for wg-quick configs ([Interface] plus exactly one [Peer])."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import re
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

DEFAULT_KEEPALIVE = 25

IPInterface = ipaddress.IPv4Interface | ipaddress.IPv6Interface
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class ConfigParseError(ValueError):
    pass


@dataclass
class PeerConfig:
    public_key: str
    endpoint_host: str
    endpoint_port: int
    allowed_ips: list[IPNetwork]
    preshared_key: str | None = None
    keepalive: int = DEFAULT_KEEPALIVE


@dataclass
class WgConfig:
    private_key: str = field(repr=False)
    public_key: str
    addresses: list[IPInterface]
    peer: PeerConfig
    dns: list[IPAddress] = field(default_factory=list)
    mtu: int | None = None

    @property
    def ipv4_addresses(self) -> list[ipaddress.IPv4Interface]:
        return [a for a in self.addresses if a.version == 4]

    @property
    def ipv6_addresses(self) -> list[ipaddress.IPv6Interface]:
        return [a for a in self.addresses if a.version == 6]


_SECTION = re.compile(r"^\[\s*([A-Za-z]+)\s*\]$")


def _strip_comment(line: str) -> str:
    # wg-quick allows '#' comments; base64 never contains '#'
    return line.split("#", 1)[0].strip()


def _key(value: str, what: str) -> str:
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ConfigParseError(f"{what} is not valid base64.") from None
    if len(raw) != 32:
        raise ConfigParseError(f"{what} must be 32 bytes long (is {len(raw)}).")
    return value


def derive_public_key(private_key: str) -> str:
    priv = X25519PrivateKey.from_private_bytes(base64.b64decode(private_key))
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(pub).decode()


def _split(value: str) -> list[str]:
    return [p.strip() for p in value.split(",") if p.strip()]


def parse_endpoint(value: str) -> tuple[str, int]:
    m = re.fullmatch(r"\[([0-9A-Fa-f:.]+)\]:(\d+)", value) or re.fullmatch(r"([^:\s\[\]]+):(\d+)", value)
    if not m:
        raise ConfigParseError(f"Endpoint '{value}' muss host:port bzw. [IPv6]:port sein.")
    host, port = m.group(1), int(m.group(2))
    if not 1 <= port <= 65535:
        raise ConfigParseError(f"Endpoint port {port} is invalid.")
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            raise ConfigParseError(f"Endpoint host '{host}' is not a valid IPv6 address.") from None
    elif not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9\-.]*[A-Za-z0-9])?", host):
        raise ConfigParseError(f"Endpoint host '{host}' is invalid.")
    return host, port


def parse_wg_quick(text: str) -> WgConfig:
    sections: list[tuple[str, dict[str, str]]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw)
        if not line:
            continue
        m = _SECTION.match(line)
        if m:
            name = m.group(1).lower()
            if name not in ("interface", "peer"):
                raise ConfigParseError(f"Line {lineno}: unknown section [{m.group(1)}].")
            sections.append((name, {}))
            continue
        if "=" not in line:
            raise ConfigParseError(f"Line {lineno}: expected 'Key = Value'.")
        if not sections:
            raise ConfigParseError(f"Line {lineno}: entry outside of a section.")
        k, v = line.split("=", 1)
        values = sections[-1][1]
        key = k.strip().lower()
        # wg-quick allows repeated Address/DNS/AllowedIPs lines
        if key in values and key in ("address", "dns", "allowedips"):
            values[key] += "," + v.strip()
        else:
            values[key] = v.strip()

    interfaces = [s for n, s in sections if n == "interface"]
    peers = [s for n, s in sections if n == "peer"]
    if len(interfaces) != 1:
        raise ConfigParseError(f"Expected exactly one [Interface] section, found {len(interfaces)}.")
    if len(peers) != 1:
        raise ConfigParseError(
            f"Expected exactly one [Peer] section, found {len(peers)}. "
            "Please use a config for a single server."
        )
    iface, peer = interfaces[0], peers[0]

    for req in ("privatekey", "address"):
        if not iface.get(req):
            raise ConfigParseError(f"[Interface]: required field {req_name(req)} is missing.")
    for req in ("publickey", "endpoint", "allowedips"):
        if not peer.get(req):
            raise ConfigParseError(f"[Peer]: required field {req_name(req)} is missing.")

    private_key = _key(iface["privatekey"], "PrivateKey")

    addresses: list[IPInterface] = []
    for a in _split(iface["address"]):
        try:
            addresses.append(ipaddress.ip_interface(a))
        except ValueError:
            raise ConfigParseError(f"Address '{a}' is invalid.") from None
    if not any(a.version == 4 for a in addresses):
        raise ConfigParseError("Address contains no IPv4 address (required for gateway/NAT).")

    dns: list[IPAddress] = []
    for d in _split(iface.get("dns", "")):
        try:
            dns.append(ipaddress.ip_address(d))
        except ValueError:
            # wg-quick allows search domains in the DNS field; they are ignored
            continue

    mtu = None
    if iface.get("mtu"):
        if not iface["mtu"].isdigit() or not 1280 <= int(iface["mtu"]) <= 9000:
            raise ConfigParseError(f"MTU '{iface['mtu']}' is invalid (1280–9000).")
        mtu = int(iface["mtu"])

    allowed: list[IPNetwork] = []
    for a in _split(peer["allowedips"]):
        try:
            allowed.append(ipaddress.ip_network(a, strict=False))
        except ValueError:
            raise ConfigParseError(f"AllowedIPs '{a}' is invalid.") from None

    keepalive = DEFAULT_KEEPALIVE
    if peer.get("persistentkeepalive"):
        val = peer["persistentkeepalive"]
        if val.lower() == "off":
            keepalive = 0
        elif val.isdigit() and int(val) <= 65535:
            keepalive = int(val) or DEFAULT_KEEPALIVE
        else:
            raise ConfigParseError(f"PersistentKeepalive '{val}' is invalid.")

    host, port = parse_endpoint(peer["endpoint"])

    return WgConfig(
        private_key=private_key,
        public_key=derive_public_key(private_key),
        addresses=addresses,
        dns=dns,
        mtu=mtu,
        peer=PeerConfig(
            public_key=_key(peer["publickey"], "PublicKey"),
            preshared_key=_key(peer["presharedkey"], "PresharedKey") if peer.get("presharedkey") else None,
            endpoint_host=host,
            endpoint_port=port,
            allowed_ips=allowed,
            keepalive=keepalive,
        ),
    )


def req_name(key: str) -> str:
    return {
        "privatekey": "PrivateKey",
        "address": "Address",
        "publickey": "PublicKey",
        "endpoint": "Endpoint",
        "allowedips": "AllowedIPs",
    }[key]
