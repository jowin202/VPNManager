# How it works

The VPN Manager uses only the official OPNsense REST API. OPNsense remains the
source of truth: the app reads the current state from OPNsense every time and
recognises its own objects by the marker `vpnmgr` (configurable with
`VPNMGR_PREFIX`). Objects without this marker are never changed.

## Objects created per VPN

For a VPN named `tv_usa`:

| # | Object | Name / description | Notes |
|---|---|---|---|
| 1 | WireGuard peer | `vpnmgr_tv_usa` | Server public key, endpoint, keepalive 25 s, optional PSK |
| 2 | WireGuard instance | `vpnmgr_tv_usa` | Private key, tunnel address, **Disable routes** on – otherwise `AllowedIPs = 0.0.0.0/0` would send the whole network through the VPN |
| 3 | Interface assignment | `optX` → `wgN`, description `VPNMGR_TV_USA` | Enabled, IPv4/IPv6 type *None* (the address comes from WireGuard), MSS clamping to the tunnel MTU |
| 4 | Gateway | `VPN_TV_USA_GW` | *Far gateway*, not a default gateway, own monitor IP, latency thresholds 400/800 ms |
| 5 | Aliases | `VPN_TV_USA_HOSTS`, `VPN_TV_USA_LOCAL` | Assigned devices; local networks that bypass the VPN |
| 6 | Source NAT rule | `vpnmgr:tv_usa nat` | Traffic of the assigned devices leaving `optX` is translated to the tunnel address |
| 7 | Firewall rules (per LAN interface) | `vpnmgr:tv_usa local/vpn/killswitch lan` | See below |

### Firewall rules

On each LAN interface three rules are created and moved above the default
"allow LAN to any" rule:

1. **local** – pass, source `VPN_TV_USA_HOSTS`, destination `VPN_TV_USA_LOCAL`,
   no gateway → devices can still reach the LAN, the firewall and local DNS.
2. **vpn** – pass, source `VPN_TV_USA_HOSTS`, destination any, gateway
   `VPN_TV_USA_GW` → policy routing into the tunnel.
3. **killswitch** – block, source `VPN_TV_USA_HOSTS`, destination any → if the
   gateway is down and OPNsense skips rule 2, nothing leaks via WAN.

### Assigning devices

Assigning a device adds its IP address to `VPN_<NAME>_HOSTS` (and removes it
from all other VPN aliases). The change is written to the configuration, applied
to the live pf table and the device's existing connections (states) are reset,
so they are re-established through the new route.

## Notable behaviour

- **Rollback** – every step registers how to undo it. If a step fails, all
  completed steps are undone in reverse order.
- **One change at a time** – OPNsense applies changes globally, so the VPN
  Manager never runs two write operations in parallel.
- **Pending changes** – before applying interface changes the app checks for
  unapplied changes made in the OPNsense GUI and stops instead of applying them.
- **Source NAT mode** – OPNsense ignores manual source NAT rules in *automatic*
  mode. If the mode is *automatic* and there are no other manual rules, the VPN
  Manager switches it to *hybrid* (automatic rules plus ours – otherwise
  identical) and switches it back when the last VPN is deleted. If you have own
  manual rules, it asks you to switch the mode yourself.
- **Identical tunnel addresses** – Proton uses `10.2.0.2` (and
  `2a07:b944::2:2`) in every config. Two interfaces cannot share an address, so
  additional tunnels get `10.3.0.2`, `10.4.0.2`, … and a matching gateway
  `10.x.0.1`. Proton translates the address on its side, so this works. A
  duplicate IPv6 tunnel address is simply left out (only IPv4 is routed).
- **Monitor IPs** – each gateway pings a different public DNS server through its
  tunnel; OPNsense allows each monitor IP only once and adds a host route for it
  via that gateway.
- **Gateway options cache** – OPNsense only accepts a new gateway in firewall
  rules after ~20 seconds (internal cache). The step waits for it.

## Data stored by the VPN Manager

`data/vpns.json` holds metadata only: creation time, provider DNS, endpoint,
tunnel and gateway addresses, monitor IP and the server's public key, plus a flag
if the app changed the source NAT mode. **No private keys, pre-shared keys or
API credentials** are stored or shown. If the file is lost, everything keeps
working; only the extra information on the VPN cards is missing.
