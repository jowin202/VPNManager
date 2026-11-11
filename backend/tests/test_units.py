import httpx
import pytest

from app.opnsense.client import OpnsenseClient
from app.opnsense.errors import (
    OpnsenseAuthError,
    OpnsenseNotFound,
    OpnsenseUnreachable,
    OpnsenseValidationError,
    SchemaError,
)
from app.opnsense.resources.system import find_version, parse_version
from app.opnsense.schema import ModelSchema, flatten
from app.vpn.naming import InvalidName, Names, owner_of, validate_name
from app.vpn.parser import parse_wg_quick
from app.vpn.provisioner import choose_gateway_ip
from app.vpn.status import gateway_state, handshake_age

from . import configs as c


def client_for(handler) -> OpnsenseClient:
    return OpnsenseClient("https://fw.test", "k", "s", transport=httpx.MockTransport(handler))


async def test_validation_errors_raised():
    cl = client_for(lambda r: httpx.Response(200, json={"result": "failed", "validations": {"server.name": "bad"}}))
    with pytest.raises(OpnsenseValidationError) as e:
        await cl.post("wireguard/server/add_server", {"server": {"privkey": c.PRIV}})
    assert e.value.validations == {"server.name": "bad"}
    assert c.PRIV not in str(e.value.to_dict())


@pytest.mark.parametrize("status,exc", [(401, OpnsenseAuthError), (403, OpnsenseAuthError), (404, OpnsenseNotFound)])
async def test_http_errors(status, exc):
    cl = client_for(lambda r: httpx.Response(status, text="x"))
    with pytest.raises(exc):
        await cl.get("a/b/c")


async def test_reads_are_retried_writes_not():
    calls = []

    def handler(r):
        calls.append(r.method)
        raise httpx.ConnectError("down")

    cl = client_for(handler)
    with pytest.raises(OpnsenseUnreachable):
        await cl.get("a/b/c")
    assert calls.count("GET") == 3
    with pytest.raises(OpnsenseUnreachable):
        await cl.post("a/b/c")
    assert calls.count("POST") == 1


def test_schema_wrap_and_field():
    s = ModelSchema.from_response("x/get", {"gateway_item": {"name": "", "fargw": "0", "interface": {"lan": {"value": "LAN", "selected": 1}}}})
    assert s.root_key == "gateway_item"
    assert s.field("descr", "name") == "name"
    assert s.options("interface") == {"lan": "LAN"}
    assert s.wrap({"name": "a"}) == {"gateway_item": {"name": "a"}}
    with pytest.raises(SchemaError):
        s.wrap({"nope": 1})
    with pytest.raises(SchemaError):
        s.field("descr")


def test_flatten():
    assert flatten({"a": {"x": {"value": "X", "selected": 1}, "y": {"value": "Y", "selected": 0}}, "b": "1"}) == {"a": "x", "b": "1"}


def test_names():
    n = Names("vpnmgr", "proton_nl")
    assert n.tag == "vpnmgr:proton_nl"
    assert n.hosts_alias == "VPN_PROTON_NL_HOSTS"
    assert n.gateway == "VPN_PROTON_NL_GW"
    assert owner_of(n.tag, "vpnmgr") == "proton_nl"
    assert owner_of(n.wg_name, "vpnmgr") == "proton_nl"
    assert owner_of(n.rule_descr("vpn lan"), "vpnmgr") == "proton_nl"
    assert owner_of("vpnmgrx:foo", "vpnmgr") is None
    assert owner_of("Default allow LAN", "vpnmgr") is None


@pytest.mark.parametrize("name", ["", "1abc", "a-b", "a" * 17, "a b", "ä"])
def test_invalid_names(name):
    with pytest.raises(InvalidName):
        validate_name(name)


def test_version_parsing():
    assert parse_version("26.7.8_2") == (26, 7, 8, 2)
    assert parse_version("26.7.7") >= (26, 7, 7)
    assert parse_version("26.7.6_3") < (26, 7, 7)
    assert find_version({"product": {"product_version": "26.7.8"}}) == "26.7.8"


def test_gateway_ip_strategy():
    assert choose_gateway_ip(parse_wg_quick(c.PROTON)) == "10.2.0.1"
    assert choose_gateway_ip(parse_wg_quick(c.PROTON), {"10.2.0.1"}) == "10.2.0.3"
    # A public DNS is not used; the tunnel network is used instead
    assert choose_gateway_ip(parse_wg_quick(c.NORD_STYLE)) == "10.5.0.1"


def test_status_helpers():
    show = {"rows": [{"public-key": "A", "latest-handshake": 1000}]}
    assert handshake_age(show, "A", now=1030) == 30
    assert handshake_age(show, "B", now=1030) is None
    assert handshake_age({"rows": [{"public-key": "A", "latest-handshake": 0}]}, "A") is None
    assert gateway_state([{"name": "G", "status": "none"}], "G")["state"] == "online"
    assert gateway_state([{"name": "G", "status": "down"}], "G")["state"] == "offline"
    assert gateway_state([], "G")["state"] == "missing"
