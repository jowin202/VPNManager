# Security policy

The VPN Manager has write access to your firewall. Please report security
issues privately via GitHub's *Report a vulnerability* button (Security tab)
instead of opening a public issue.

Recommendations for operators:

- Expose the VPN Manager only to your local network, never to the internet.
- Use a long random `VPNMGR_API_TOKEN`.
- Give the OPNsense API user only the privileges listed in the setup guide.
- Keep WireGuard config files private; revoke them in your VPN account if they
  leak.
