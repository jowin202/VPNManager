# Configuration reference

All settings are environment variables, usually set in `.env` next to
`docker-compose.yml`. A template is in [`.env.example`](../.env.example).

## Required

| Variable | Description |
|---|---|
| `OPNSENSE_URL` | Base URL of the OPNsense web GUI, e.g. `https://192.168.1.1` |
| `OPNSENSE_API_KEY` | API key of the OPNsense user (`key=` in `apikey.txt`) |
| `OPNSENSE_API_SECRET` | API secret (`secret=` in `apikey.txt`) |
| `VPNMGR_API_TOKEN` | Token to sign in to the VPN Manager, at least 16 characters |

## Optional

| Variable | Default | Description |
|---|---|---|
| `OPNSENSE_VERIFY_TLS` | `true` | `true`, `false`, or the path to a certificate/CA file inside the container. See [setup guide](setup-guide.md#63-configure). |
| `VPNMGR_PREFIX` | `vpnmgr` | Marker for all objects created in OPNsense. Only change it before creating the first VPN. |
| `VPNMGR_MONITOR_IPS` | `1.1.1.1,1.0.0.1,9.9.9.9,149.112.112.112,8.8.8.8,8.8.4.4,208.67.222.222,208.67.220.220,94.140.14.14,94.140.15.15` | Addresses pinged through the tunnels to check them. Each VPN uses a different one (OPNsense allows each monitor IP only once and routes it through that gateway). |
| `VPNMGR_RULE_INTERFACES` | *(automatic)* | Comma-separated OPNsense interfaces (`lan`, `opt2`, …) that get the policy rules. By default all interfaces on which devices are seen, otherwise `lan`. Set it when you use VLANs. |
| `VPNMGR_LOCAL_NETWORKS` | `10.0.0.0/8,172.16.0.0/12,192.168.0.0/16` | Destinations that are never sent through a VPN (your LAN, the firewall, local DNS). |
| `VPNMGR_LOCAL_NETWORKS_ALIAS` | – | Name of an existing OPNsense alias to use instead of `VPNMGR_LOCAL_NETWORKS`. |
| `VPNMGR_SOURCE_NAT` | `auto` | `auto`: create a source NAT rule unless OPNsense already has one for the tunnel interface. `always` / `never` force it. |
| `VPNMGR_VERIFY_TIMEOUT` | `90` | Seconds to wait for handshake and gateway after creating a VPN. |
| `VPNMGR_REQUEST_TIMEOUT` | `30` | Timeout for OPNsense API calls in seconds. |
| `VPNMGR_DATA_DIR` | `data` (`/app/data` in Docker) | Where local metadata is stored. Contains no secrets. |
| `VPNMGR_SKIP_VERSION_CHECK` | `false` | Skip the minimum version check. For development only. |
| `VPNMGR_AUTH_DISABLED` | `false` | Disable sign-in. For local development only – never on a reachable host. |
