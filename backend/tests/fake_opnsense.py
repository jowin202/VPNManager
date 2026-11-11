"""In-memory simulation of the OPNsense endpoints used by the VPN Manager.

Field names and response formats match a schema discovery against
OPNsense 26.7.8_2 (see scripts/discover_schema.py).
"""

from __future__ import annotations

import itertools
import json
import time
import uuid as uuidlib
from dataclasses import dataclass, field

import httpx


class Locked(Exception):
    pass


def opts(*keys: str, selected: str | None = None) -> dict:
    return {k: {"value": k, "selected": int(k == selected)} for k in keys}


@dataclass
class Model:
    root: str
    defaults: dict
    rows: dict = field(default_factory=dict)  # key -> flat record

    def schema(self) -> dict:
        return {self.root: json.loads(json.dumps(self.defaults))}


class FakeOpnsense:
    def __init__(self, version: str = "26.7.8_2"):
        self.version = version
        self.calls: list[tuple[str, str]] = []
        self.fail: dict[str, dict] = {}  # path prefix -> response with validations
        self.pending = False
        self.snat_mode = "automatic"
        self.wg_enabled = "0"
        self.handshake = True
        self.gateway_online = True
        self.strip_tunnel_address = False  # simulates opnsense/core#10996
        self.tables: dict[str, set[str]] = {}
        self.killed: list[str] = []
        self._instance = itertools.count(0)
        self._opt = itertools.count(1)
        self.filter_order: list[str] = []
        self.foreign_rule = str(uuidlib.uuid4())

        self.clients = Model("client", {
            "enabled": "1", "name": "", "pubkey": "", "psk": "", "tunneladdress": {},
            "serveraddress": "", "serverport": "", "endpoint": "", "keepalive": "", "servers": {},
        })
        self.servers = Model("server", {
            "enabled": "1", "name": "", "instance": "0", "pubkey": "", "privkey": "", "port": "",
            "mtu": "", "dns": {}, "tunneladdress": {}, "disableroutes": "0", "gateway": "",
            "carp_depend_on": {}, "peers": {}, "debug": "0", "endpoint": "", "peer_dns": "",
        })
        self.assignments = Model("interface", {
            "descr": "", "identifier": "", "icon": "", "optgroup": "", "if": {}, "lock": "1",
            "pending_action": "", "enable": "1", "mtu": "", "mss": "",
            "type4": opts("", "staticv4", "dhcp", selected=""),
            "type6": opts("", "staticv6", "dhcp6", selected=""),
        })
        self.gateways = Model("gateway_item", {
            "disabled": "0", "name": "", "descr": "", "interface": {}, "ipprotocol": opts("inet", "inet6", selected="inet"),
            "gateway": "", "defaultgw": "0", "fargw": "0", "monitor_disable": "1", "monitor": "",
            "priority": "255", "weight": "1", "latencylow": "", "latencyhigh": "",
        })
        self.aliases = Model("alias", {
            "enabled": "1", "name": "", "type": opts("host", "network", "port", "url", selected="host"),
            "content": {}, "description": "",
        })
        self.rules = Model("rule", {
            "enabled": "1", "sequence": "", "action": opts("pass", "block", "reject", selected="pass"),
            "quick": "1", "interface": {}, "direction": opts("in", "out", selected="in"),
            "ipprotocol": opts("inet", "inet6", "inet46", selected="inet"), "protocol": "any",
            "source_net": "any", "destination_net": "any", "gateway": {}, "description": "",
        })
        self.nat = Model("rule", {
            "enabled": "1", "interface": {}, "ipprotocol": opts("inet", "inet6", selected="inet"),
            "source_net": "any", "destination_net": "any", "target": "", "description": "",
        })
        self.interfaces = {"lan": "le1", "wan": "le0"}
        self.if_addresses: dict[str, list[str]] = {"le1": ["192.168.1.1"]}
        # Automatic/legacy rules: visible in search_rule, not movable
        self.auto_rules = [
            {"uuid": "02f4bab031b5", "description": "Default deny / state violation rule", "action": "block",
             "legacy": True, "is_automatic": True, "sort_order": "000001.1000001"},
            {"uuid": "01e83daa25c4", "description": "anti-lockout rule", "action": "pass",
             "legacy": True, "is_automatic": True, "sort_order": "000005.1000027"},
        ]
        self.rules.rows[self.foreign_rule] = {
            "enabled": "1", "sequence": "100", "action": "pass", "interface": "lan",
            "description": "Default allow LAN to any rule", "source_net": "lan", "destination_net": "any",
            "gateway": "", "quick": "1", "direction": "in", "ipprotocol": "inet", "protocol": "any",
        }
        self.filter_order.append(self.foreign_rule)
        self.discovery = [
            {"source": "discovery", "interface_name": "LAN", "ether_address": "aa:bb:cc:00:00:10",
             "ip_address": "192.168.1.10", "organization_name": "", "first_seen": "2026-11-06T18:00:00Z",
             "last_seen": "2026-11-06T19:00:00Z"},
            {"source": "discovery", "interface_name": "LAN", "ether_address": "aa:bb:cc:00:00:11",
             "ip_address": "192.168.1.11", "organization_name": "", "first_seen": "2026-11-06T18:00:00Z",
             "last_seen": "2026-11-06T19:00:00Z"},
            {"source": "discovery", "interface_name": "WAN", "ether_address": "3c:78:95:04:1e:a0",
             "ip_address": "192.168.0.1", "organization_name": "TP-Link", "first_seen": "2026-11-06T18:00:00Z",
             "last_seen": "2026-11-06T19:00:00Z"},
        ]
        self.leases = [
            {"address": "192.168.1.10", "hwaddr": "aa:bb:cc:00:00:10", "hostname": "laptop"},
            {"address": "192.168.1.12", "hwaddr": "aa:bb:cc:00:00:12", "hostname": "tv"},
        ]
        self.arp = [{"mac": "aa:bb:cc:00:00:11", "ip": "192.168.1.11", "intf": "le1", "expired": False,
                     "expires": 1199, "permanent": False, "type": "ethernet", "manufacturer": "",
                     "hostname": "", "intf_description": "LAN"},
                    {"mac": "02:00:00:00:00:01", "ip": "192.168.1.1", "intf": "le1", "expired": False,
                     "expires": 0, "permanent": True, "type": "ethernet", "manufacturer": "",
                     "hostname": "", "intf_description": "LAN"}]
        self.auto_nat = [{"uuid": "automatic_wan", "enabled": "1", "interface": "wan", "source_net": "lan,lo0",
                          "target": "wanip", "description": "Auto created rule", "is_automatic": True}]

    # -- Helpers ------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def all_descriptions(self) -> list[str]:
        out = []
        for m, f in ((self.clients, "name"), (self.servers, "name"), (self.gateways, "descr"),
                     (self.aliases, "description"), (self.rules, "description"), (self.nat, "description"),
                     (self.assignments, "descr")):
            out += [r.get(f, "") for r in m.rows.values()]
        return out

    def _schema_with_options(self, model: Model) -> dict:
        data = model.schema()
        rec = data[model.root]
        ifaces = {**self.interfaces, **{k: k for k in self.assignments.rows}}
        if model is self.assignments:
            rec["if"] = opts(*self.interfaces.values(), *[f"wg{r['instance']}" for r in self.servers.rows.values()])
        if model in (self.gateways, self.rules, self.nat):
            rec["interface"] = opts(*ifaces)
        if model is self.rules:
            rec["gateway"] = opts("", *[r["name"] for r in self.gateways.rows.values()])
        if model is self.servers:
            rec["peers"] = opts(*self.clients.rows)
        if model is self.clients:
            rec["servers"] = opts(*self.servers.rows)
        return data

    def _search(self, model: Model, key_name: str = "uuid") -> dict:
        rows = [dict(r) for r in self.auto_rules] if model is self.rules else []
        if model is self.nat:
            rows += [dict(r) for r in self.auto_nat] if self.snat_mode in ("automatic", "hybrid") else []
        keys = self.filter_order if model is self.rules else list(model.rows)
        if model is self.nat and self.snat_mode not in ("hybrid", "advanced"):
            keys = []
        for i, key in enumerate(keys):
            r = dict(model.rows[key])
            r.pop("privkey", None)
            r.pop("sequence", None)
            r[key_name] = key
            if model is self.rules:
                r["sort_order"] = f"400000.{i:07d}"
            rows.append(r)
        return {"rows": rows, "total": len(rows), "rowCount": len(rows), "current": 1}

    def _get(self, model: Model, key: str | None) -> dict:
        data = self._schema_with_options(model)
        if key is None:
            return data
        if key not in model.rows:
            return {}
        rec = data[model.root]
        for k, v in model.rows[key].items():
            if isinstance(rec.get(k), dict):
                selected = {p for p in str(v).replace("\n", ",").split(",") if p}
                for p in selected - set(rec[k]):
                    rec[k][p] = {"value": p, "selected": 1}
                for o, d in rec[k].items():
                    d["selected"] = int(o in selected)
            else:
                rec[k] = v
        return data

    def _validate(self, model: Model, record: dict) -> dict | None:
        unknown = [k for k in record if k not in model.defaults]
        if unknown:
            return {f"{model.root}.{k}": "unknown field" for k in unknown}
        return None

    def _write(self, model: Model, body: dict, key: str | None) -> dict:
        record = body.get(model.root)
        if not isinstance(record, dict):
            return {"result": "failed", "validations": {"root": f"missing {model.root}"}}
        problems = self._validate(model, record)
        if problems:
            return {"result": "failed", "validations": problems}
        if key is None:
            key = str(uuidlib.uuid4())
            base = {k: ("" if isinstance(v, dict) else v) for k, v in model.defaults.items()}
            model.rows[key] = {**base, **record}
        else:
            model.rows[key].update(record)
        return {"result": "saved", "uuid": key}

    # -- Router -------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/")
        self.calls.append((request.method, path))
        body = json.loads(request.content or b"{}") if request.method == "POST" else {}
        for prefix, resp in self.fail.items():
            if path.startswith(prefix):
                return httpx.Response(200, json=resp)
        try:
            result = self.route(request.method, path, body)
        except Locked:
            return httpx.Response(500, json={"errorMessage": "Interface locked, unset lock first before removal",
                                             "errorTitle": "locked", "errorLevel": "error"})
        except KeyError:
            return httpx.Response(404, json={"errorMessage": "Endpoint not found"})
        return httpx.Response(200, json=result)

    def route(self, method: str, path: str, body: dict):  # noqa: C901
        parts = path.split("/")
        mod, ctl, cmd, args = parts[0], parts[1], parts[2], parts[3:]
        arg = args[0] if args else None
        key = f"{mod}/{ctl}"

        if key == "core/firmware" and cmd == "info":
            return {"product_id": "opnsense", "product_version": self.version,
                    "product": {"CORE_VERSION": self.version}}

        crud = {
            "wireguard/client": (self.clients, "client"),
            "wireguard/server": (self.servers, "server"),
            "routing/settings": (self.gateways, "gateway"),
            "firewall/alias": (self.aliases, "item"),
            "firewall/filter": (self.rules, "rule"),
            "firewall/source_nat": (self.nat, "rule"),
        }
        if key in crud:
            model, suffix = crud[key]
            if cmd == f"search_{suffix}":
                return self._search(model)
            if cmd == f"get_{suffix}":
                return self._get(model, arg)
            if cmd == f"add_{suffix}":
                if model is self.gateways:
                    mon = body.get("gateway_item", {}).get("monitor")
                    if mon and any(g.get("monitor") == mon for g in model.rows.values()):
                        return {"result": "failed", "validations": {
                            "gateway_item.monitor": "This monitor IP address already exists."}}
                res = self._write(model, body, None)
                if model is self.servers and res.get("uuid"):
                    model.rows[res["uuid"]]["instance"] = str(next(self._instance))
                if model is self.rules and res.get("uuid"):
                    self.filter_order.append(res["uuid"])
                return res
            if cmd == f"set_{suffix}":
                if arg not in model.rows:
                    return {"result": "failed"}
                return self._write(model, body, arg)
            if cmd == f"del_{suffix}":
                if model.rows.pop(arg, None) is None:
                    return {"result": "not found"}
                if model is self.rules:
                    self.filter_order.remove(arg)
                return {"result": "deleted"}

        if key == "firewall/source_nat" and cmd in ("get", "set"):
            if cmd == "set":
                self.snat_mode = body["filter"]["general"]["snat_mode"]
                return {"result": "saved"}
            return {"filter": {"general": {"snat_mode": opts("automatic", "hybrid", "advanced", "disabled",
                                                             selected=self.snat_mode)}}}
        if key == "firewall/filter" and cmd == "get":
            return {"filter": {"general": {}, "rules": {"rule": {}},
                               "snatrules": {"rule": {u: dict(r) for u, r in self.nat.rows.items()}}}}
        if key == "firewall/filter" and cmd == "move_rule_before":
            sel, target = args
            self.filter_order.remove(sel)
            self.filter_order.insert(self.filter_order.index(target), sel)
            return {"status": "ok"}
        if key == "firewall/filter" and cmd == "apply":
            return {"status": "OK"}
        if key == "firewall/alias" and cmd == "reconfigure":
            for r in self.aliases.rows.values():
                self.tables[r["name"]] = {p for p in str(r.get("content", "")).replace("\n", ",").split(",") if p}
            return {"status": "ok"}
        if key == "firewall/alias_util":
            table = self.tables.setdefault(arg, set())
            if cmd == "list":
                return {"rows": [{"ip": ip} for ip in sorted(table)], "total": len(table)}
            if cmd == "add":
                table.add(body["address"])
                return {"status": "done"}
            if cmd == "delete":
                table.discard(body["address"])
                return {"status": "done"}

        if key == "wireguard/general":
            if cmd == "get":
                return {"general": {"enabled": self.wg_enabled}}
            if cmd == "set":
                self.wg_enabled = body["general"]["enabled"]
                return {"result": "saved"}
        if key == "wireguard/service":
            if cmd == "reconfigure":
                for r in self.servers.rows.values():
                    if not self.strip_tunnel_address:
                        self.if_addresses[f"wg{r['instance']}"] = [a.split("/")[0] for a in r["tunneladdress"].split(",")]
                return {"status": "ok"}
            if cmd == "show":
                rows = []
                for c in self.clients.rows.values():
                    rows.append({"type": "peer", "public-key": c["pubkey"],
                                 "latest-handshake": int(time.time()) - 5 if self.handshake else 0})
                return {"rows": rows}

        if key == "interfaces/assignment":
            m = self.assignments
            if cmd == "search_item":
                rows = [{"uuid": k, "identifier": k, "descr": "", "%descr": k.upper(), "if": dev,
                         "pending_action": "update" if self.pending and k == "lan" else ""}
                        for k, dev in self.interfaces.items()]
                rows += [{"uuid": k, "identifier": k, "%descr": v.get("descr", ""), **v} for k, v in m.rows.items()]
                return {"rows": rows, "rowCount": len(rows), "total": len(rows), "current": 1}
            if cmd == "get_item":
                return self._get(m, arg)
            if cmd == "pending":
                return {"status": "ok"}
            if cmd == "add_item":
                rec = body.get(m.root, {})
                problems = self._validate(m, rec)
                if problems:
                    return {"result": "failed", "validations": problems}
                ifname = f"opt{next(self._opt)}"
                m.rows[ifname] = {"lock": "1", **rec}
                return {"result": "saved", "ifname": ifname}
            if cmd == "set_item":
                rec = body.get(m.root, {})
                problems = self._validate(m, rec)
                if problems or arg not in m.rows:
                    return {"result": "failed", "validations": problems or {"interface": "not found"}}
                m.rows[arg].update(rec)
                return {"result": "saved"}
            if cmd == "del_item":
                if m.rows.get(arg, {}).get("lock") == "1":
                    raise Locked()
                m.rows.pop(arg, None)
                return {"result": "deleted"}
            if cmd == "reconfigure":
                if self.strip_tunnel_address:
                    for rec in m.rows.values():
                        self.if_addresses.pop(rec["if"], None)
                return {"status": "ok"}

        if key == "diagnostics/interface" and cmd == "get_interface_config":
            return {dev: {"ipv4": [{"ipaddr": ip} for ip in ips]} for dev, ips in self.if_addresses.items()}
        if key == "diagnostics/interface" and cmd == "search_arp":
            return {"rows": self.arp}
        if key == "diagnostics/firewall" and cmd == "kill_states":
            self.killed.append(body["filter"])
            return {"result": "ok", "dropped_states": 1}
        if key == "routing/settings" and cmd == "reconfigure":
            return {"status": "ok"}
        if key == "routes/gateway" and cmd == "status":
            return {"items": [{"name": g["name"], "monitor": g.get("monitor") or "~",
                               "status": "none" if self.gateway_online else "down",
                               "status_translated": "Online" if self.gateway_online else "Offline"}
                              for g in self.gateways.rows.values()], "status": "ok"}
        if key == "hostdiscovery/service" and cmd == "search":
            return {"rows": self.discovery}
        if key == "kea/leases4" and cmd == "search":
            return {"rows": self.leases}
        raise KeyError(path)
