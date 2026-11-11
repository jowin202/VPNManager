"""Configuration comes exclusively from environment variables (or .env)."""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

MIN_OPNSENSE_VERSION = (26, 7, 7)

# Public anycast DNS servers that reliably answer ICMP echo
DEFAULT_MONITOR_IPS = (
    "1.1.1.1", "1.0.0.1", "9.9.9.9", "149.112.112.112", "8.8.8.8", "8.8.4.4",
    "208.67.222.222", "208.67.220.220", "94.140.14.14", "94.140.15.15",
)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(RuntimeError):
    pass


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    if raw.lower() in _TRUE:
        return True
    if raw.lower() in _FALSE:
        return False
    raise ConfigError(f"{name}: invalid boolean value '{raw}'")


def _list(name: str) -> list[str]:
    return [p.strip() for p in os.environ.get(name, "").split(",") if p.strip()]


@dataclass(frozen=True)
class Settings:
    opnsense_url: str
    api_key: str
    api_secret: str
    # True/False or path to a CA bundle
    verify_tls: bool | str
    api_token: str | None
    auth_disabled: bool = False
    prefix: str = "vpnmgr"
    # One monitor IP per VPN (OPNsense allows each only once and adds a host
    # route via the respective gateway)
    monitor_ips: list[str] = field(default_factory=lambda: list(DEFAULT_MONITOR_IPS))
    # Empty = detect automatically from the interfaces where hosts are seen
    rule_interfaces: list[str] = field(default_factory=list)
    local_networks: list[str] = field(
        default_factory=lambda: ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
    )
    # Existing alias to use instead of a LOCAL alias per VPN
    local_networks_alias: str | None = None
    # auto = only create a rule if OPNsense has no automatic rule for the interface
    source_nat: str = "auto"
    data_dir: Path = Path("data")
    skip_version_check: bool = False
    request_timeout: float = 30.0
    # How long to wait for handshake/gateway after creating a VPN
    verify_timeout: float = 90.0

    @property
    def tls_warning(self) -> str | None:
        if self.verify_tls is False:
            return "TLS verification is disabled (OPNSENSE_VERIFY_TLS=false)."
        if self.opnsense_url.startswith("http://"):
            return "Connection to OPNsense is not encrypted (http://)."
        return None


def load_settings() -> Settings:
    load_dotenv()
    url = (os.environ.get("OPNSENSE_URL") or os.environ.get("OPNSENSE_HOST") or "").rstrip("/")
    key = os.environ.get("OPNSENSE_API_KEY", "")
    secret = os.environ.get("OPNSENSE_API_SECRET", "")
    missing = [n for n, v in (("OPNSENSE_URL", url), ("OPNSENSE_API_KEY", key), ("OPNSENSE_API_SECRET", secret)) if not v]
    if missing:
        raise ConfigError(f"Missing environment variables: {', '.join(missing)} (see .env.example)")

    raw_verify = os.environ.get("OPNSENSE_VERIFY_TLS", "true")
    if raw_verify.lower() in _TRUE:
        verify: bool | str = True
    elif raw_verify.lower() in _FALSE:
        verify = False
    elif Path(raw_verify).is_file():
        verify = raw_verify
    else:
        raise ConfigError(f"OPNSENSE_VERIFY_TLS: neither a boolean nor an existing CA file: '{raw_verify}'")

    token = os.environ.get("VPNMGR_API_TOKEN") or None
    auth_disabled = _bool("VPNMGR_AUTH_DISABLED", False)
    if not token and not auth_disabled:
        raise ConfigError(
            "VPNMGR_API_TOKEN is missing. The app changes firewall rules and requires "
            "authentication (for local development only: VPNMGR_AUTH_DISABLED=true)."
        )
    if token and len(token) < 16:
        raise ConfigError("VPNMGR_API_TOKEN must be at least 16 characters long.")

    prefix = os.environ.get("VPNMGR_PREFIX", "vpnmgr")
    if not re.fullmatch(r"[a-z][a-z0-9]{1,15}", prefix):
        raise ConfigError("VPNMGR_PREFIX: lowercase letters/digits only, 2-16 characters, starting with a letter.")

    monitor_ips = _list("VPNMGR_MONITOR_IPS") or _list("VPNMGR_MONITOR_IP") or list(DEFAULT_MONITOR_IPS)
    for ip in monitor_ips:
        ipaddress.IPv4Address(ip)

    local_networks = _list("VPNMGR_LOCAL_NETWORKS") or Settings.__dataclass_fields__["local_networks"].default_factory()
    for net in local_networks:
        ipaddress.ip_network(net, strict=False)

    source_nat = os.environ.get("VPNMGR_SOURCE_NAT", "auto")
    if source_nat not in ("auto", "always", "never"):
        raise ConfigError("VPNMGR_SOURCE_NAT must be auto, always or never.")

    return Settings(
        opnsense_url=url,
        api_key=key,
        api_secret=secret,
        verify_tls=verify,
        api_token=token,
        auth_disabled=auth_disabled,
        prefix=prefix,
        monitor_ips=monitor_ips,
        rule_interfaces=_list("VPNMGR_RULE_INTERFACES"),
        local_networks=local_networks,
        local_networks_alias=os.environ.get("VPNMGR_LOCAL_NETWORKS_ALIAS") or None,
        source_nat=source_nat,
        data_dir=Path(os.environ.get("VPNMGR_DATA_DIR", "data")),
        skip_version_check=_bool("VPNMGR_SKIP_VERSION_CHECK", False),
        request_timeout=float(os.environ.get("VPNMGR_REQUEST_TIMEOUT", "30")),
        verify_timeout=float(os.environ.get("VPNMGR_VERIFY_TIMEOUT", "90")),
    )
