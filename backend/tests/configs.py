"""Sample configs from different providers (keys are test values)."""

PRIV = "yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk="
PRIV_PUB = "HIgo9xNzJMWLKASShiTqIybxZ0U3wGLiUeJ1PKf8ykw="
PEER = "xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg="
PSK = "FpCyhws9cxwWoV4xELtfJvjJN+zQVRPISllRWgeopVE="

PROTON = f"""[Interface]
# Key for opnsense
# Bouncing = 3
# NetShield = 1
PrivateKey = {PRIV}
Address = 10.2.0.2/32
DNS = 10.2.0.1

[Peer]
# NL-FREE#1
PublicKey = {PEER}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 185.159.157.1:51820
"""

MULLVAD = f"""[Interface]
# Device: Happy Otter
PrivateKey = {PRIV}
Address = 10.64.12.34/32,fc00:bbbb:bbbb:bb01::1:c22/128
DNS = 10.64.0.1

[Peer]
PublicKey = {PEER}
AllowedIPs = 0.0.0.0/0,::0/0
Endpoint = 193.32.127.66:51820
"""

AIRVPN = f"""[Interface]
Address = 10.128.42.7/32, fd7d:76ee:e68f:a993:1:2:3:4/128
PrivateKey = {PRIV}
MTU = 1320
DNS = 10.128.0.1, fd7d:76ee:e68f:a993::1

[Peer]
PublicKey = {PEER}
PresharedKey = {PSK}
Endpoint = europe3.vpn.airdns.org:1637
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 15
"""

IVPN = f"""[Interface]
PrivateKey = {PRIV}
Address = 172.24.11.5/32
DNS = 172.16.0.1

[Peer]
PublicKey = {PEER}
Endpoint = [2a07:a880:4601:1062::1]:2049
AllowedIPs = 0.0.0.0/0
"""

NORD_STYLE = f"""[interface]
privatekey={PRIV}
address=10.5.0.2/16
dns=103.86.96.100,103.86.99.100

[peer]
publickey={PEER}
allowedips=0.0.0.0/0
endpoint=de1234.nordvpn.com:51820
persistentkeepalive=25
"""

PROTON_V6 = PROTON.replace("Address = 10.2.0.2/32", "Address = 10.2.0.2/32, 2a07:b944::2:2/128")
