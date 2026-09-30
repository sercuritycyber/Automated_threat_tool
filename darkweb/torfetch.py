#!/usr/bin/env python3
"""Fetch a single URL over Tor, stdlib-only.

A minimal SOCKS5 client opens a stream through the local Tor daemon and hands
the connected socket to ``http.client`` so HTTP/1.1 (chunked, content-length,
gzip) is parsed correctly; HTTPS is wrapped with ``ssl``.

Design constraints, on purpose:

* **Targeted, not a spider.** ``fetch()`` retrieves exactly the URL it is
  given. Redirects are followed only to the *same onion host* and only a few
  hops. It never follows arbitrary outbound links, so the collector cannot
  wander off a configured forum into unknown hidden services.
* **Text only, capped.** Non-text content types and bodies over ``max_bytes``
  are refused, so the tool never pulls binary dumps / media off these sites.
* **Stream isolation.** Each fetch uses a random SOCKS username/password. With
  Tor's ``IsolateSOCKSAuth`` (default on) that puts each request on its own
  circuit, so one site cannot trivially link our requests.

No third-party dependencies: works on the stdlib alone.
"""
from __future__ import annotations

import gzip
import http.client
import io
import os
import socket
import ssl
import struct
import zlib
from dataclasses import dataclass, field
from secrets import token_hex
from urllib.parse import urlsplit

DEFAULT_PROXY_HOST = os.environ.get("TOR_SOCKS_HOST", "127.0.0.1")
DEFAULT_PROXY_PORT = int(os.environ.get("TOR_SOCKS_PORT", "9050"))

# A plain, common browser UA. We are not trying to be evasive, just not to look
# like a scripted default that some front-ends block outright.
DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
)

TEXT_TYPES = ("text/", "application/xhtml", "application/xml", "application/json")


class TorError(RuntimeError):
    """Any failure reaching Tor or the target over it."""


class FetchRefused(RuntimeError):
    """The response was rejected by our own policy (type/size/redirect)."""


# --------------------------------------------------------------------------- #
#  SOCKS5 (RFC 1928) — just enough to CONNECT to host:port, with username/pw
#  (RFC 1929) for Tor stream isolation. Names are resolved by Tor, never here.
# --------------------------------------------------------------------------- #
def _socks5_connect(
    dest_host: str,
    dest_port: int,
    proxy_host: str,
    proxy_port: int,
    timeout: float,
    username: str,
    password: str,
) -> socket.socket:
    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        # Greeting: offer no-auth (0x00) and username/password (0x02).
        sock.sendall(b"\x05\x02\x00\x02")
        ver, method = _recv_exact(sock, 2)
        if ver != 0x05:
            raise TorError(f"bad SOCKS version from proxy: {ver}")
        if method == 0x02:
            u = username.encode()[:255]
            p = password.encode()[:255]
            sock.sendall(
                b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p
            )
            avers, status = _recv_exact(sock, 2)
            if status != 0x00:
                raise TorError("SOCKS username/password auth rejected")
        elif method != 0x00:
            raise TorError(
                "proxy wants an auth method we do not support "
                f"(0x{method:02x}); is this really a Tor SOCKS port?"
            )
        # CONNECT, address type 0x03 = domain name (Tor resolves it).
        host = dest_host.encode("idna") if _is_idna(dest_host) else dest_host.encode()
        if len(host) > 255:
            raise TorError("destination host name too long for SOCKS5")
        req = (
            b"\x05\x01\x00\x03"
            + bytes([len(host)])
            + host
            + struct.pack(">H", dest_port)
        )
        sock.sendall(req)
        ver, rep, _rsv, atyp = _recv_exact(sock, 4)
        if rep != 0x00:
            raise TorError(f"SOCKS CONNECT failed: {_SOCKS_ERR.get(rep, rep)}")
        # Drain the bound address so the socket is left at the start of the stream.
        if atyp == 0x01:
            _recv_exact(sock, 4 + 2)
        elif atyp == 0x04:
            _recv_exact(sock, 16 + 2)
        elif atyp == 0x03:
            n = _recv_exact(sock, 1)[0]
            _recv_exact(sock, n + 2)
        else:
            raise TorError(f"unexpected SOCKS bound-address type {atyp}")
        return sock
    except Exception:
        sock.close()
        raise


_SOCKS_ERR = {
    0x01: "general failure",
    0x02: "connection not allowed",
    0x03: "network unreachable",
    0x04: "host unreachable (onion down or wrong address?)",
    0x05: "connection refused",
    0x06: "TTL expired",
    0x07: "command not supported",
    0x08: "address type not supported",
}


def _is_idna(host: str) -> bool:
    try:
        host.encode("ascii")
        return False
    except UnicodeEncodeError:
        return True


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise TorError("proxy closed the connection early")
        buf += chunk
    return buf


# --------------------------------------------------------------------------- #
#  http.client connections that ride the SOCKS socket.
# --------------------------------------------------------------------------- #
class _SocksHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, port, *, proxy, timeout, socks_auth):
        super().__init__(host, port, timeout=timeout)
        self._proxy = proxy
        self._socks_auth = socks_auth

    def connect(self):
        self.sock = _socks5_connect(
            self.host, self.port, *self._proxy, self.timeout, *self._socks_auth
        )


class _SocksHTTPSConnection(_SocksHTTPConnection):
    def __init__(self, host, port, *, proxy, timeout, socks_auth, ssl_context):
        super().__init__(host, port, proxy=proxy, timeout=timeout, socks_auth=socks_auth)
        self._ssl_context = ssl_context

    def connect(self):
        raw = _socks5_connect(
            self.host, self.port, *self._proxy, self.timeout, *self._socks_auth
        )
        self.sock = self._ssl_context.wrap_socket(raw, server_hostname=self.host)


@dataclass
class Response:
    url: str
    status: int
    reason: str
    headers: dict = field(default_factory=dict)
    text: str = ""
    content_type: str = ""
    final_url: str = ""
    bytes_len: int = 0


def fetch(
    url: str,
    *,
    proxy_host: str = DEFAULT_PROXY_HOST,
    proxy_port: int = DEFAULT_PROXY_PORT,
    timeout: float = 60.0,
    max_bytes: int = 4_000_000,
    user_agent: str = DEFAULT_UA,
    cookie: str | None = None,
    extra_headers: dict | None = None,
    max_redirects: int = 3,
    allow_clearnet: bool = False,
) -> Response:
    """GET ``url`` over Tor and return a :class:`Response`.

    ``cookie`` is a raw ``Cookie:`` header value (for forums that need an
    authenticated session — supply it out of band, never hard-code it).
    Redirects are followed only within the same host.
    """
    seen: set[str] = set()
    current = url
    for _hop in range(max_redirects + 1):
        if current in seen:
            raise FetchRefused("redirect loop")
        seen.add(current)
        parts = urlsplit(current)
        host = parts.hostname or ""
        if not allow_clearnet and not host.endswith(".onion"):
            raise FetchRefused(
                f"refusing non-onion host {host!r} (set allow_clearnet=True to override)"
            )
        resp = _one_get(
            current, parts, proxy_host, proxy_port, timeout,
            max_bytes, user_agent, cookie, extra_headers,
        )
        if resp.status in (301, 302, 303, 307, 308) and resp.headers.get("location"):
            nxt = _resolve_redirect(current, resp.headers["location"])
            if urlsplit(nxt).hostname != host:
                raise FetchRefused(
                    f"refusing cross-host redirect {host} -> {urlsplit(nxt).hostname}"
                )
            current = nxt
            continue
        resp.final_url = current
        return resp
    raise FetchRefused("too many redirects")


def _one_get(url, parts, proxy_host, proxy_port, timeout, max_bytes,
             user_agent, cookie, extra_headers) -> Response:
    scheme = parts.scheme or "http"
    host = parts.hostname
    port = parts.port or (443 if scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    proxy = (proxy_host, proxy_port)
    socks_auth = (token_hex(8), token_hex(8))  # per-request circuit isolation

    if scheme == "https":
        ctx = ssl.create_default_context()
        # Onion services carry their own end-to-end crypto and often use
        # self-signed certs; the .onion address itself is the trust anchor.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = _SocksHTTPSConnection(
            host, port, proxy=proxy, timeout=timeout,
            socks_auth=socks_auth, ssl_context=ctx,
        )
    else:
        conn = _SocksHTTPConnection(
            host, port, proxy=proxy, timeout=timeout, socks_auth=socks_auth,
        )

    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
        "Host": host if port in (80, 443) else f"{host}:{port}",
    }
    if cookie:
        headers["Cookie"] = cookie
    if extra_headers:
        headers.update(extra_headers)

    try:
        conn.request("GET", path, headers=headers)
        r = conn.getresponse()
        raw = _read_capped(r, max_bytes)
    except (socket.timeout, TimeoutError) as e:
        raise TorError(f"timeout fetching {url}: {e}") from e
    except (OSError, http.client.HTTPException) as e:
        raise TorError(f"error fetching {url}: {e}") from e
    finally:
        conn.close()

    hdrs = {k.lower(): v for k, v in r.getheaders()}
    ctype = hdrs.get("content-type", "")
    body = _maybe_decompress(raw, hdrs.get("content-encoding", ""))

    # Policy: text only. A redirect (3xx) legitimately has no/short body.
    is_text = any(t in ctype for t in TEXT_TYPES) or not ctype
    if r.status < 300 and not is_text:
        raise FetchRefused(f"non-text content-type {ctype!r} at {url}")

    text = body.decode(_charset(ctype), errors="replace") if is_text else ""
    return Response(
        url=url, status=r.status, reason=r.reason, headers=hdrs,
        text=text, content_type=ctype, bytes_len=len(raw),
    )


def _read_capped(r, max_bytes: int) -> bytes:
    buf = io.BytesIO()
    while True:
        chunk = r.read(65536)
        if not chunk:
            break
        buf.write(chunk)
        if buf.tell() > max_bytes:
            raise FetchRefused(f"response exceeds max_bytes ({max_bytes})")
    return buf.getvalue()


def _maybe_decompress(raw: bytes, encoding: str) -> bytes:
    enc = encoding.lower()
    try:
        if "gzip" in enc:
            return gzip.decompress(raw)
        if "deflate" in enc:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
    except (OSError, zlib.error):
        return raw
    return raw


def _charset(content_type: str) -> str:
    for part in content_type.split(";"):
        part = part.strip().lower()
        if part.startswith("charset="):
            cs = part.split("=", 1)[1].strip().strip('"')
            if cs:
                return cs
    return "utf-8"


def _resolve_redirect(base: str, location: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base, location)


def check_tor(proxy_host: str = DEFAULT_PROXY_HOST,
              proxy_port: int = DEFAULT_PROXY_PORT,
              timeout: float = 30.0) -> str:
    """Confirm Tor is reachable and exiting. Returns the observed exit note.

    Uses Tor Project's own check service over the SOCKS proxy.
    """
    resp = fetch(
        "https://check.torproject.org/api/ip",
        proxy_host=proxy_host, proxy_port=proxy_port, timeout=timeout,
        allow_clearnet=True,
    )
    return resp.text.strip()[:200]


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import sys

    if len(sys.argv) < 2:
        print("usage: torfetch.py <url|--check>")
        sys.exit(2)
    if sys.argv[1] == "--check":
        print(check_tor())
    else:
        r = fetch(sys.argv[1])
        print(r.status, r.reason, r.content_type, r.bytes_len, "bytes")
        print(r.text[:2000])
