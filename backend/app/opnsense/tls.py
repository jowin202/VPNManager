"""TLS context for the OPNsense API.

OPNsense (26.7.x) corrupts responses larger than ~64 KB over HTTP/1.x
(broken chunked encoding or interleaved data); over HTTP/2 they arrive
intact. httpx offers HTTP/1.1 before h2 via ALPN, and OPNsense picks the
first offer. This context moves h2 to the front.
"""

from __future__ import annotations

import ssl

import certifi


class _PreferH2Context(ssl.SSLContext):
    def set_alpn_protocols(self, protocols):
        super().set_alpn_protocols(sorted(protocols, key=lambda p: p != "h2"))


def make_ssl_context(verify: bool | str) -> ssl.SSLContext:
    ctx = _PreferH2Context(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if verify is False:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    else:
        ctx.load_verify_locations(verify if isinstance(verify, str) else certifi.where())
    return ctx
