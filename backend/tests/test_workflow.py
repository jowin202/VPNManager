"""End-to-end tests against the fake OPNsense via the REST API."""

import time

import pytest

from . import configs as c


def wait_job(api, job_id: str) -> dict:
    for _ in range(200):
        job = api.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("Job is taking too long")


def create(api, name="proton", config=c.PROTON, **kw) -> dict:
    r = api.post("/api/vpns", json={"name": name, "config": config, **kw})
    assert r.status_code == 202, r.text
    return wait_job(api, r.json()["job_id"])


def test_auth_required(api):
    assert api.get("/api/vpns", headers={"Authorization": "Bearer wrong-token-xxxxxx"}).status_code == 401
    assert api.get("/api/vpns", headers={"Authorization": ""}).status_code == 401


def test_create_list_delete_leaves_nothing(api, fake):
    job = create(api)
    assert job["status"] == "succeeded", job
    assert [s["status"] for s in job["steps"]].count("failed") == 0

    # Instance: disable routes, peer linked; interface: none; gateway: far + monitor
    server = next(iter(fake.servers.rows.values()))
    assert server["disableroutes"] == "1"
    assert server["peers"] in fake.clients.rows
    assignment = next(iter(fake.assignments.rows.values()))
    assert assignment["lock"] == "0" and assignment["descr"] == "VPNMGR_PROTON"
    assert assignment["mss"] == "1420"
    assert assignment["type4"] == "" and assignment["type6"] == "" and assignment["if"] == "wg0" and assignment["enable"] == "1"
    gw = next(iter(fake.gateways.rows.values()))
    assert gw["name"] == "VPN_PROTON_GW" and gw["fargw"] == "1" and gw["defaultgw"] == "0"
    assert gw["gateway"] == "10.2.0.1" and gw["monitor"] == "1.1.1.1"
    assert gw["latencylow"] == "400" and gw["latencyhigh"] == "800"
    assert fake.wg_enabled == "1"

    # Rules: local -> vpn -> killswitch, all before the default LAN rule
    order = [fake.rules.rows[u]["description"] for u in fake.filter_order]
    assert order == [
        "vpnmgr:proton local lan",
        "vpnmgr:proton vpn lan",
        "vpnmgr:proton killswitch lan",
        "Default allow LAN to any rule",
    ]
    vpn_rule = next(r for r in fake.rules.rows.values() if r["description"] == "vpnmgr:proton vpn lan")
    assert vpn_rule["gateway"] == "VPN_PROTON_GW" and vpn_rule["source_net"] == "VPN_PROTON_HOSTS"
    kill = next(r for r in fake.rules.rows.values() if "killswitch" in r["description"])
    assert kill["action"] == "block"
    nat = next(iter(fake.nat.rows.values()))
    assert nat["interface"] == "opt1" and nat["target"] == "opt1ip"
    assert fake.snat_mode == "hybrid"

    vpns = api.get("/api/vpns").json()
    assert vpns[0]["name"] == "proton" and vpns[0]["complete"]
    assert vpns[0]["gateway"]["state"] == "online"
    assert vpns[0]["handshake_age"] is not None

    r = api.delete("/api/vpns/proton")
    assert r.status_code == 200 and r.json()["status"] == "succeeded", r.text
    assert not any(d.startswith("vpnmgr") for d in fake.all_descriptions())
    assert not fake.assignments.rows and not fake.gateways.rows and not fake.aliases.rows
    assert fake.filter_order == [fake.foreign_rule]
    assert fake.snat_mode == "automatic"
    assert api.get("/api/vpns").json() == []


def test_no_secrets_in_responses(api, fake):
    job = create(api, config=c.AIRVPN, name="air")
    assert job["status"] == "succeeded", job
    assert next(iter(fake.assignments.rows.values()))["mss"] == "1320"  # MTU from the config
    for body in (str(job), api.get("/api/vpns").text, api.post("/api/vpns/parse", json={"config": c.AIRVPN}).text):
        assert c.PRIV not in body and c.PSK not in body
    # The private key was handed to OPNsense, though
    assert next(iter(fake.servers.rows.values()))["privkey"] == c.PRIV


def test_rollback_on_failure(api, fake):
    fake.fail["routing/settings/add_gateway"] = {"result": "failed", "validations": {"gateway_item.gateway": "invalid"}}
    job = create(api)
    assert job["status"] == "failed"
    failed = next(s for s in job["steps"] if s["status"] == "failed")
    assert failed["key"] == "gateway"
    assert "invalid" in failed["detail"]
    assert all(r["status"] == "ok" for r in job["rollback"])
    assert not fake.clients.rows and not fake.servers.rows and not fake.assignments.rows
    assert not any(d.startswith("vpnmgr") for d in fake.all_descriptions())


def test_rollback_when_tunnel_address_stripped(api, fake):
    fake.strip_tunnel_address = True
    job = create(api)
    assert job["status"] == "failed"
    assert next(s for s in job["steps"] if s["status"] == "failed")["key"] == "assignment"
    assert not fake.servers.rows and not fake.assignments.rows


def test_pending_changes_abort(api, fake):
    fake.pending = True
    r = api.post("/api/vpns", json={"name": "proton", "config": c.PROTON})
    assert r.status_code == 409 and r.json()["code"] == "conflict"
    assert not fake.clients.rows


def test_duplicate_and_half_created(api, fake):
    assert create(api)["status"] == "succeeded"
    r = api.post("/api/vpns", json={"name": "PROTON", "config": c.MULLVAD})
    assert r.status_code == 409 and "case-insensitive" in r.json()["detail"]
    r = api.post("/api/vpns", json={"name": "proton", "config": c.MULLVAD})
    assert r.status_code == 409 and r.json()["details"]["objects"]["instances"] == 1


def test_handshake_missing_is_warning_not_rollback(api, fake):
    fake.handshake = False
    job = create(api)
    assert job["status"] == "succeeded"
    assert job["steps"][-1]["status"] == "warning"
    assert fake.servers.rows


def test_old_version_rejected(api, fake):
    fake.version = "26.7.6_3"
    api.app.state.ctx.version = None
    r = api.post("/api/vpns", json={"name": "proton", "config": c.PROTON})
    assert r.status_code == 412
    check = api.get("/api/system/check").json()
    assert check["version_ok"] is False and "too old" in check["error"]


def test_system_check_ok(api):
    check = api.get("/api/system/check").json()
    assert check["ok"], check


def test_invalid_input(api):
    assert api.post("/api/vpns", json={"name": "bad-name", "config": c.PROTON}).status_code == 422
    assert api.post("/api/vpns", json={"name": "ok", "config": "[Interface]\n"}).status_code == 422


def test_hosts_and_assignment(api, fake):
    assert create(api)["status"] == "succeeded"
    assert create(api, name="mullvad", config=c.MULLVAD)["status"] == "succeeded"

    hosts = {h["ip"]: h for h in api.get("/api/hosts").json()}
    assert set(hosts) == {"192.168.1.10", "192.168.1.11", "192.168.1.12"}
    assert hosts["192.168.1.10"]["mac"] == "aa:bb:cc:00:00:10"
    assert hosts["192.168.1.10"]["hostname"] == "laptop" and hosts["192.168.1.10"]["online"]
    assert hosts["192.168.1.12"]["online"] is False

    r = api.put("/api/hosts/assignments", json=[{"ip": "192.168.1.10", "vpn": "proton"},
                                                 {"ip": "192.168.1.11", "vpn": "proton"}])
    assert r.status_code == 200 and r.json()["verified"], r.text
    assert fake.tables["VPN_PROTON_HOSTS"] == {"192.168.1.10", "192.168.1.11"}

    # Moving is exclusive
    r = api.put("/api/hosts/assignments", json=[{"ip": "192.168.1.10", "vpn": "mullvad"}])
    assert r.json()["verified"]
    assert fake.tables["VPN_PROTON_HOSTS"] == {"192.168.1.11"}
    assert fake.tables["VPN_MULLVAD_HOSTS"] == {"192.168.1.10"}
    assert "192.168.1.10" in fake.killed

    # Back to WAN
    r = api.put("/api/hosts/assignments", json=[{"ip": "192.168.1.11", "vpn": None}])
    assert fake.tables["VPN_PROTON_HOSTS"] == set()

    # An offline host in the alias is still listed
    api.put("/api/hosts/assignments", json=[{"ip": "192.168.1.50", "vpn": "proton"}])
    hosts = {h["ip"]: h for h in api.get("/api/hosts").json()}
    assert hosts["192.168.1.50"]["vpn"] == "proton" and not hosts["192.168.1.50"]["online"]

    # Deleting with assigned hosts requires confirmation
    r = api.delete("/api/vpns/proton")
    assert r.status_code == 409 and r.json()["hosts"] == ["192.168.1.50"]
    r = api.delete("/api/vpns/proton", params={"confirm_hosts": True})
    assert r.json()["status"] == "succeeded"
    assert "192.168.1.50" in fake.killed


@pytest.mark.parametrize("payload", [
    [{"ip": "999.1.1.1", "vpn": None}],
    [{"ip": "192.168.1.10", "vpn": "doesnotexist"}],
    [{"ip": "192.168.1.10", "vpn": None}, {"ip": "192.168.1.10", "vpn": "x"}],
])
def test_assignment_validation(api, payload):
    assert api.put("/api/hosts/assignments", json=payload).status_code == 422


def test_busy_lock(api):
    ctx = api.app.state.ctx

    async def hold():
        await ctx.write_lock.acquire()

    api.portal.call(hold)
    try:
        r = api.put("/api/hosts/assignments", json=[])
        assert r.status_code == 409 and r.json()["code"] == "busy"
    finally:
        api.portal.call(lambda: ctx.write_lock.release())


def test_starts_without_reachable_opnsense(tmp_path):
    import httpx
    from fastapi.testclient import TestClient

    from app.main import create_app

    from .conftest import TOKEN, make_settings

    def down(request):
        raise httpx.ConnectError("down")

    app = create_app(make_settings(tmp_path), transport=httpx.MockTransport(down))
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        check = client.get("/api/system/check").json()
        assert check["reachable"] is False and check["error"]
        r = client.post("/api/vpns", json={"name": "proton", "config": c.PROTON})
        assert r.status_code == 504


def test_too_old_version_aborts_startup(tmp_path):
    from fastapi.testclient import TestClient

    from app.context import VersionTooOld
    from app.main import create_app

    from .conftest import make_settings
    from .fake_opnsense import FakeOpnsense

    app = create_app(make_settings(tmp_path), transport=FakeOpnsense(version="26.7.6_3").transport())
    with pytest.raises(VersionTooOld):
        with TestClient(app):
            pass


def test_locked_assignment_is_unlocked_before_delete(api, fake):
    assert create(api)["status"] == "succeeded"
    next(iter(fake.assignments.rows.values()))["lock"] = "1"
    r = api.delete("/api/vpns/proton")
    assert r.json()["status"] == "succeeded", r.text
    assert not fake.assignments.rows


def test_orphan_assignment_is_found_and_deleted(api, fake):
    assert create(api)["status"] == "succeeded"
    # Half removed: only the interface assignment is left
    fake.clients.rows.clear(); fake.servers.rows.clear(); fake.gateways.rows.clear()
    fake.aliases.rows.clear(); fake.nat.rows.clear()
    for u in [u for u in fake.filter_order if u != fake.foreign_rule]:
        fake.filter_order.remove(u); fake.rules.rows.pop(u)
    vpns = api.get("/api/vpns").json()
    assert [v["name"] for v in vpns] == ["proton"] and vpns[0]["interface"] == "opt1"


def test_nat_mode_restored_on_rollback(api, fake):
    fake.fail["firewall/filter/add_rule"] = {"result": "failed", "validations": {"rule.gateway": "x"}}
    assert create(api)["status"] == "failed"
    assert fake.snat_mode == "automatic" and not fake.nat.rows


def test_nat_mode_not_changed_with_foreign_manual_rules(api, fake):
    fake.nat.rows["foreign"] = {"enabled": "1", "interface": "wan", "description": "my own rule"}
    job = create(api)
    assert job["status"] == "failed"
    assert next(s for s in job["steps"] if s["status"] == "failed")["key"] == "source_nat"
    assert fake.snat_mode == "automatic"


def test_nat_mode_kept_when_already_hybrid(api, fake):
    fake.snat_mode = "hybrid"
    assert create(api)["status"] == "succeeded"
    assert api.delete("/api/vpns/proton").json()["status"] == "succeeded"
    assert fake.snat_mode == "hybrid"


def test_same_tunnel_address_is_rewritten(api, fake):
    assert create(api)["status"] == "succeeded"
    preview = api.post("/api/vpns/parse", json={"config": c.PROTON}).json()
    assert preview["rewritten_from"] == "10.2.0.2" and preview["addresses"] == ["10.3.0.2/32"]
    assert preview["gateway_ip"] == "10.3.0.1"

    job = create(api, name="proton2")
    assert job["status"] == "succeeded", job
    assert "using 10.3.0.2/32" in job["steps"][1]["detail"]
    servers = sorted(r["tunneladdress"] for r in fake.servers.rows.values())
    assert servers == ["10.2.0.2/32", "10.3.0.2/32"]
    gw = next(g for g in fake.gateways.rows.values() if g["name"] == "VPN_PROTON2_GW")
    assert gw["gateway"] == "10.3.0.1"
    # Each monitor IP only once
    assert sorted(g["monitor"] for g in fake.gateways.rows.values()) == ["1.0.0.1", "1.1.1.1"]

    job = create(api, name="proton3")
    assert "using 10.4.0.2/32" in job["steps"][1]["detail"]


def test_explicit_tunnel_address(api, fake):
    assert create(api)["status"] == "succeeded"
    job = create(api, name="proton2", tunnel_address="10.77.0.2/32")
    assert job["status"] == "succeeded"
    assert "10.77.0.2/32" in [r["tunneladdress"] for r in fake.servers.rows.values()]
    r = api.post("/api/vpns", json={"name": "proton3", "config": c.PROTON, "tunnel_address": "10.77.0.2/32"})
    assert r.status_code == 409


def test_rewrite_skips_lan_networks(fake):
    from app.vpn.provisioner import resolve_tunnel_address
    import ipaddress
    from app.vpn.parser import parse_wg_quick
    cfg, old = resolve_tunnel_address(parse_wg_quick(c.PROTON), {"10.2.0.2"},
                                      [ipaddress.IPv4Interface("10.3.0.1/24")])
    assert old == "10.2.0.2" and str(cfg.addresses[0]) == "10.4.0.2/32"


def test_young_gateway_shows_starting(api, fake):
    assert create(api)["status"] == "succeeded"
    fake.gateway_online = False
    assert api.get("/api/vpns").json()[0]["gateway"]["state"] == "starting"
    fake.handshake = False
    assert api.get("/api/vpns").json()[0]["gateway"]["state"] == "offline"


def test_duplicate_ipv6_address_is_dropped(api, fake):
    assert create(api, config=c.PROTON_V6)["status"] == "succeeded"
    preview = api.post("/api/vpns/parse", json={"config": c.PROTON_V6}).json()
    assert preview["dropped_ipv6"] == ["2a07:b944::2:2/128"] and preview["addresses"] == ["10.3.0.2/32"]
    job = create(api, name="proton2", config=c.PROTON_V6)
    assert job["status"] == "succeeded", job
    assert "2a07:b944::2:2/128" in job["steps"][1]["detail"]
    second = next(r for r in fake.servers.rows.values() if r["name"] == "vpnmgr_proton2")
    assert second["tunneladdress"] == "10.3.0.2/32"
