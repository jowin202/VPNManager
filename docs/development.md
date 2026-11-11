# Development

## Layout

```
backend/
  app/
    config.py              environment variables
    context.py             runtime context, write lock, version check
    main.py                FastAPI app, error handling, serves the frontend
    api/                   REST endpoints (system, vpns, hosts)
    opnsense/
      client.py            async HTTP client (HTTP/2, retries for reads only)
      tls.py               TLS context that prefers HTTP/2 (see below)
      schema.py            schema discovery and payload validation
      resources/           one thin wrapper per API area, no business logic
    vpn/
      parser.py            wg-quick parser
      provisioner.py       create/delete workflows with rollback
      inventory.py         reads the actual state from OPNsense
      hosts.py             host list and assignment
  tests/                   pytest suite with a simulated OPNsense
  dev_fake.py              run the backend against the simulated OPNsense
frontend/                  Angular app
scripts/
  discover_schema.py       dump the live API schemas of your OPNsense
  screenshots.py           regenerate docs/images
```

## Backend

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
cd backend
../.venv/bin/python -m pytest            # unit and end-to-end tests
../.venv/bin/python dev_fake.py          # http://localhost:8000, token dev-token-0123456789
```

`dev_fake.py` runs the real app against `tests/fake_opnsense.py`, an in-memory
simulation of all endpoints used, built from the API schemas of OPNsense
26.7.8. No firewall is needed.

## Frontend

```bash
cd frontend
npm ci
npx ng serve      # http://localhost:4200, proxies /api to localhost:8000
npx ng build      # output is served by the backend
```

## Screenshots

```bash
.venv/bin/pip install playwright
.venv/bin/python scripts/screenshots.py   # uses the installed Google Chrome
```

## Working against a real OPNsense

Field names of the OPNsense API are never guessed: every payload is checked
against the live schema (`GET …/get_item` without UUID). If a future OPNsense
release renames a field, the app stops with a clear `SchemaError` instead of
sending wrong data. `scripts/discover_schema.py` dumps all schemas used to
`docs/opnsense_schema/` (read-only; ignored by git because it contains data
about your network).

Write tests against a real firewall only on a test system.

## OPNsense API notes

Things learned while building this, useful when extending it:

- **HTTP/1.1 corrupts large responses** (> ~64 KB, e.g. `core/firmware/info`,
  big rule lists) – broken chunked encoding. HTTP/2 works. httpx offers
  `http/1.1` before `h2` in ALPN and OPNsense picks the first one, so `tls.py`
  reorders the ALPN list.
- **Interface assignments** (`interfaces/assignment/*`, since 26.7; settings
  since 26.7.6): the record root key is `interface`, the device field is `if`,
  IPv4/IPv6 type *None* is the option value `""`, and new assignments default to
  `lock=1` – locked assignments cannot be deleted. Unlocking requires the device
  to still exist. Pending GUI changes are reported per row in `pending_action`.
- **Gateways in firewall rules**: the gateway option list comes from a configd
  cache with ~20 s TTL; a new gateway is rejected in rules until it appears.
- **Monitor IPs** must be unique across gateways.
- **Source NAT**: `firewall/source_nat/search_rule` only returns manual rules in
  *hybrid*/*advanced* mode and OPNsense ignores them in *automatic* mode. The
  mode is `filter.general.snat_mode` (`firewall/source_nat/get|set`); manual
  rules can always be read from `firewall/filter/get` → `filter.snatrules.rule`.
- **Firewall rule order**: `search_rule` returns automatic/legacy rules
  (`is_automatic`, `legacy`) that cannot be moved; order by `sort_order`.
- **Host discovery** returns the interface *description* (`LAN`), ARP the device
  (`le1`); the firewall's own addresses appear as permanent ARP entries.
- **Proton VPN** uses `10.2.0.2/32` and `2a07:b944::2:2/128` in every config.

## Releases

The CI workflow runs the tests and the frontend build on every push. The Docker
image is built and pushed to `ghcr.io/<owner>/<repo>` by the manually started
*Build Docker image* workflow.
