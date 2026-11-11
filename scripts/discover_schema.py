"""Dumps the schemas of all OPNsense controllers used by the VPN Manager to
docs/opnsense_schema/ (useful when a new OPNsense release changes the API).

Read-only GET calls only. Secrets (private keys, PSKs) are masked before
anything is written. The output contains data about your network – do not
publish it.

    OPNSENSE_URL=https://192.168.1.1 OPNSENSE_API_KEY=... OPNSENSE_API_SECRET=... \
        python scripts/discover_schema.py
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app.opnsense.tls import make_ssl_context  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "opnsense_schema"

# (file name, path below /api/). An empty get_item returns defaults and option lists.
ENDPOINTS = [
    ("firmware_info", "core/firmware/info"),
    ("system_status", "core/system/status"),
    ("wireguard_general", "wireguard/general/get"),
    ("wireguard_server_item", "wireguard/server/get_server"),
    ("wireguard_server_search", "wireguard/server/search_server"),
    ("wireguard_client_item", "wireguard/client/get_client"),
    ("wireguard_client_search", "wireguard/client/search_client"),
    ("wireguard_service_show", "wireguard/service/show"),
    ("assignment_item", "interfaces/assignment/get_item"),
    ("assignment_search", "interfaces/assignment/search_item"),
    ("assignment_pending", "interfaces/assignment/pending"),
    ("gateway_item", "routing/settings/get_gateway"),
    ("gateway_search", "routing/settings/search_gateway"),
    ("gateway_status", "routes/gateway/status"),
    ("filter_rule_item", "firewall/filter/get_rule"),
    ("filter_rule_search", "firewall/filter/search_rule"),
    ("source_nat_item", "firewall/source_nat/get_rule"),
    ("source_nat_search", "firewall/source_nat/search_rule"),
    ("alias_item", "firewall/alias/get_item"),
    ("alias_search", "firewall/alias/search_item"),
    ("alias_util_aliases", "firewall/alias_util/aliases"),
    ("hostdiscovery_search", "hostdiscovery/service/search"),
    ("kea_leases4_search", "kea/leases4/search"),
    ("kea_leases_search", "kea/leases/search"),
    ("dnsmasq_leases_search", "dnsmasq/leases/search"),
    ("arp_search", "diagnostics/interface/search_arp"),
]

SECRET_KEY = re.compile(r"^(privkey|psk|password)$|secret", re.IGNORECASE)


def redact(obj):
    if isinstance(obj, dict):
        return {
            k: ("<redacted>" if SECRET_KEY.search(k) and isinstance(v, str) and v else redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


async def main() -> int:
    load_dotenv()
    base = (os.environ.get("OPNSENSE_URL") or os.environ.get("OPNSENSE_HOST") or "").rstrip("/")
    key, secret = os.environ.get("OPNSENSE_API_KEY"), os.environ.get("OPNSENSE_API_SECRET")
    if not (base and key and secret):
        print("Set OPNSENSE_URL, OPNSENSE_API_KEY and OPNSENSE_API_SECRET.", file=sys.stderr)
        return 2
    verify_env = os.environ.get("OPNSENSE_VERIFY_TLS", "true")
    verify = verify_env if os.path.isfile(verify_env) else verify_env.lower() not in ("0", "false", "no")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = {}
    async with httpx.AsyncClient(base_url=f"{base}/api/", auth=(key, secret), http2=True,
                                 verify=make_ssl_context(verify), timeout=20) as c:
        for name, path in ENDPOINTS:
            try:
                r = await c.get(path)
            except httpx.TransportError as e:
                print(f"OPNsense at {base} is not reachable: {type(e).__name__}", file=sys.stderr)
                return 1
            except httpx.HTTPError as e:
                summary[path] = f"error: {e}"
                print(f"{path:45} ERROR {e}")
                continue
            summary[path] = r.status_code
            print(f"{path:45} {r.status_code}")
            if r.status_code != 200:
                continue
            try:
                data = r.json()
            except ValueError:
                continue
            (OUT_DIR / f"{name}.json").write_text(json.dumps(redact(data), indent=2, ensure_ascii=False))
    (OUT_DIR / "_summary.json").write_text(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
