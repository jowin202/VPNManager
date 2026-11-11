import ipaddress

import pytest

from app.vpn.parser import ConfigParseError, derive_public_key, parse_endpoint, parse_wg_quick

from . import configs as c


def test_derive_public_key():
    assert derive_public_key(c.PRIV) == c.PRIV_PUB


def test_proton():
    cfg = parse_wg_quick(c.PROTON)
    assert cfg.public_key == c.PRIV_PUB
    assert [str(a) for a in cfg.addresses] == ["10.2.0.2/32"]
    assert [str(d) for d in cfg.dns] == ["10.2.0.1"]
    assert cfg.peer.endpoint_host == "185.159.157.1"
    assert cfg.peer.endpoint_port == 51820
    assert [str(n) for n in cfg.peer.allowed_ips] == ["0.0.0.0/0", "::/0"]
    assert cfg.peer.keepalive == 25
    assert cfg.peer.preshared_key is None
    assert cfg.mtu is None


def test_mullvad_ipv6_and_no_spaces():
    cfg = parse_wg_quick(c.MULLVAD)
    assert len(cfg.ipv4_addresses) == 1 and len(cfg.ipv6_addresses) == 1
    assert ipaddress.ip_network("::/0") in cfg.peer.allowed_ips


def test_airvpn_psk_mtu_keepalive_hostname():
    cfg = parse_wg_quick(c.AIRVPN)
    assert cfg.peer.preshared_key == c.PSK
    assert cfg.mtu == 1320
    assert cfg.peer.keepalive == 15
    assert cfg.peer.endpoint_host == "europe3.vpn.airdns.org"
    assert len(cfg.dns) == 2


def test_ivpn_ipv6_endpoint():
    cfg = parse_wg_quick(c.IVPN)
    assert cfg.peer.endpoint_host == "2a07:a880:4601:1062::1"
    assert cfg.peer.endpoint_port == 2049


def test_lowercase_keys_and_sections():
    cfg = parse_wg_quick(c.NORD_STYLE)
    assert str(cfg.addresses[0]) == "10.5.0.2/16"
    assert cfg.peer.endpoint_host == "de1234.nordvpn.com"


def test_private_key_not_in_repr():
    assert c.PRIV not in repr(parse_wg_quick(c.PROTON))


def test_multiple_peers_rejected():
    with pytest.raises(ConfigParseError, match="exactly one \\[Peer\\]"):
        parse_wg_quick(c.PROTON + f"\n[Peer]\nPublicKey = {c.PEER}\nEndpoint = 1.2.3.4:1\nAllowedIPs = 0.0.0.0/0\n")


@pytest.mark.parametrize("field", ["PrivateKey", "Address"])
def test_missing_interface_fields(field):
    text = "\n".join(line for line in c.PROTON.splitlines() if not line.startswith(field))
    with pytest.raises(ConfigParseError, match=field):
        parse_wg_quick(text)


@pytest.mark.parametrize("field", ["PublicKey", "Endpoint", "AllowedIPs"])
def test_missing_peer_fields(field):
    text = "\n".join(line for line in c.PROTON.splitlines() if not line.startswith(field))
    with pytest.raises(ConfigParseError, match=field):
        parse_wg_quick(text)


def test_bad_key():
    with pytest.raises(ConfigParseError, match="PrivateKey"):
        parse_wg_quick(c.PROTON.replace(c.PRIV, "abc"))


def test_ipv6_only_rejected():
    with pytest.raises(ConfigParseError, match="IPv4"):
        parse_wg_quick(c.PROTON.replace("10.2.0.2/32", "fd00::2/128"))


def test_unknown_section():
    with pytest.raises(ConfigParseError, match="unknown section"):
        parse_wg_quick("[Foo]\n" + c.PROTON)


def test_inline_comment():
    cfg = parse_wg_quick(c.PROTON.replace("DNS = 10.2.0.1", "DNS = 10.2.0.1  # Proton DNS"))
    assert [str(d) for d in cfg.dns] == ["10.2.0.1"]


@pytest.mark.parametrize("value", ["host", "host:0", "host:70000", "[zz::1]:5", "ho st:5"])
def test_bad_endpoint(value):
    with pytest.raises(ConfigParseError):
        parse_endpoint(value)
