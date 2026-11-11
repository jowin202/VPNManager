# Troubleshooting

Start with the **System** page – it shows whether OPNsense is reachable, the
version and missing privileges. The container log (`docker compose logs -f`)
contains the details of every error.

## The web interface does not load

- Wait 10–15 seconds after starting the container. On startup the app checks the
  OPNsense version; if OPNsense is unreachable it still starts after 10 seconds.
- `docker compose ps` shows whether the container is running and healthy.
- If the log says `OPNsense ... is too old`, update OPNsense (26.7.7 or newer).
- If the log complains about missing variables, check `.env`.

## "OPNsense is not reachable"

- Can the Docker host reach the firewall? `curl -k https://192.168.1.1` on the
  Docker host should return HTML.
- `OPNSENSE_URL` must use `https://` (OPNsense redirects plain HTTP).
- With certificate verification on, the host name in `OPNSENSE_URL` must match
  the certificate. Use `OPNSENSE_VERIFY_TLS=false` to rule this out.

## "Access denied" / privileges missing

The API key is accepted, but the user lacks a privilege. The System page lists
which one. Add it to the `vpnmgr` group in **System → Access → Groups**.
A wrong key or secret results in HTTP 401 instead.

## Gateway shows "offline" or "degraded"

- **Right after creating a VPN** this is normal for about a minute: monitoring
  averages packet loss over time and counts the pings sent before the tunnel was
  up. The card shows *monitoring starting* during this phase.
- **High latency** to far-away servers (Asia, Americas) was reported as
  "degraded" by OPNsense's default thresholds; the VPN Manager uses 400/800 ms.
- **Handshake missing or old**: the config or endpoint is wrong, the config was
  deleted in the provider account, or the same config is used elsewhere at the
  same time. Create a new config.
- **Handshake fine but gateway stays offline** with a changed tunnel address
  (second Proton tunnel and later): some Proton servers answer pings to
  `10.2.0.2` instead. Normal traffic still works – check on a device. Choosing a
  different server usually helps.

## Creating a VPN fails

The step log shows the failing step and what was rolled back. Common messages:

| Message | Solution |
|---|---|
| *OPNsense has unapplied interface changes* | Apply or discard them in the OPNsense GUI (Interfaces → Assignments), or tick the checkbox to apply them as well. |
| *Objects for '…' already exist* | A previous attempt left objects behind (e.g. after a power cut). Delete the VPN in the overview and create it again. |
| *Source NAT is set to 'automatic', but there are … foreign manual rules* | Switch Firewall → NAT → Source NAT to *Hybrid* yourself. |
| *All monitor IPs are already used* | Add more addresses to `VPNMGR_MONITOR_IPS`. |
| *Validation failed: …* | OPNsense rejected a value. The message names the field. Please open an issue with the message. |

## A device does not use the VPN

- Is the device's IP in the list correct? It must have a fixed IP / DHCP
  reservation – otherwise it may have got a new address.
- Is the device behind another router? Then OPNsense only sees that router.
- Apps may cache their location: restart the app or the device.
- The device may use IPv6. Only IPv4 is routed through the VPN – disable IPv6 on
  the device or the LAN if this matters.

## Slow speed through the VPN

- Pick a server close to you, or check the latency on the VPN card.
- Weak CPUs limit WireGuard throughput – see [hardware](hardware.md).
- In virtual machines, use virtio or Intel network adapters instead of emulated
  AMD PCnet adapters (`le0`).
- VPNs created with older versions of the VPN Manager have no MSS clamping;
  delete and recreate them.
