"""Tests for the dark-web collector — no live Tor needed.

The torfetch round-trip runs against a fake in-process server that speaks the
SOCKS5 handshake and then answers as the origin, so the SOCKS5 client and the
HTTP/redirect/size policy are all exercised for real.
"""
import gzip
import socket
import struct
import threading
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(ROOT / "darkweb"))

import parse
import match as M
import torfetch
from store import Store


# --------------------------------------------------------------------------- store
def test_store_dedup_and_hits():
    with tempfile.TemporaryDirectory() as d:
        st = Store(Path(d) / "t.db")
        pid, sid, new = st.record_fetch("site", "http://x.onion/", title="T",
                                        status=200, content_type="text/html",
                                        bytes_len=10, text="hello world")
        assert new and sid
        # same content -> no new snapshot
        _pid2, sid2, new2 = st.record_fetch("site", "http://x.onion/", title="T",
                                            status=200, content_type="text/html",
                                            bytes_len=10, text="hello world")
        assert not new2 and sid2 == sid
        # changed content -> new snapshot, same page
        _pid3, sid3, new3 = st.record_fetch("site", "http://x.onion/", title="T",
                                            status=200, content_type="text/html",
                                            bytes_len=12, text="hello mars")
        assert new3 and sid3 != sid
        assert st.add_hit(sid3, pid, "watchlist", "mars", "hello mars")
        assert not st.add_hit(sid3, pid, "watchlist", "mars", "hello mars")  # dupe
        s = st.stats()
        assert s["pages"] == 1 and s["snapshots"] == 2 and s["hits"] == 1
        st.close()


# --------------------------------------------------------------------------- parse
def test_parse_strips_scripts_and_lists_same_host_links():
    html = """<html><head><title>Board &amp; Market</title>
      <style>.x{}</style><script>var secret=1;</script></head>
      <body><h1>Threads</h1>
      <a href="/thread/1">First leak</a>
      <a href="http://other.onion/x">offsite</a>
      <p>visible text</p></body></html>"""
    out = parse.parse_page(html, "http://board.onion/index")
    assert out["title"] == "Board & Market"
    assert "secret" not in out["text"] and "var secret" not in out["text"]
    assert "visible text" in out["text"] and "First leak" in out["text"]
    urls = {l["url"] for l in out["links"]}
    assert "http://board.onion/thread/1" in urls
    assert "http://other.onion/x" not in urls  # cross-host dropped


# --------------------------------------------------------------------------- match
def test_watchlist_word_boundary_and_domains():
    indicators = [
        M.Indicator("watchlist", "Acme", "acme", word_boundary=True),
        M.Indicator("watchlist", "acme.com", "acme.com"),
    ]
    m = M.Matcher(indicators)
    # bare word 'Acme' must NOT match when embedded in a longer word
    embedded = m.scan("selling acmecorp portal access")
    assert not any(h["indicator"] == "Acme" for h in embedded)
    # domain matches as substring
    dom = m.scan("data from acme.com dumps")
    assert any(h["indicator"] == "acme.com" for h in dom)
    # bare word matches on a real boundary
    word = m.scan("the Acme breach")
    assert any(h["indicator"] == "Acme" for h in word)


def test_honeypot_creds_drops_common_and_short(tmp_path):
    csv = tmp_path / "creds.csv"
    csv.write_text("username,password\n"
                   "admin,admin\n"                       # both common -> dropped
                   "svc_backup,Hunter2Longpw\n"          # both kept
                   "ab,xy\n")                            # too short -> dropped
    inds = M.load_honeypot_creds(csv)
    vals = {(i.kind, i.value) for i in inds}
    assert ("honeypot-user", "svc_backup") in vals
    assert ("honeypot-cred", "Hunter2Longpw") in vals
    assert all(i.value not in ("admin", "ab", "xy") for i in inds)


def test_honeypot_ips_only_global(tmp_path):
    csv = tmp_path / "iocs.csv"
    csv.write_text("src_ip\n8.8.8.8\n10.0.0.5\n127.0.0.1\n45.146.164.110\n")
    inds = M.load_honeypot_ips(csv)
    ips = {i.value for i in inds}
    assert "8.8.8.8" in ips and "45.146.164.110" in ips
    assert "10.0.0.5" not in ips and "127.0.0.1" not in ips


def test_cred_pair_context_matches_snapshot():
    m = M.Matcher([M.Indicator("honeypot-cred", "S3cretSprayPw", "s3cretspraypw")])
    hits = m.scan("combo list: victim@corp.com:S3cretSprayPw for sale 5 BTC")
    assert hits and hits[0]["kind"] == "honeypot-cred"
    assert "for sale" in hits[0]["context"]


# --------------------------------------------------------------------------- torfetch policy
def test_refuses_non_onion_without_override():
    with pytest.raises(torfetch.FetchRefused):
        torfetch.fetch("http://example.com/", max_redirects=0)


def test_charset_and_gzip_helpers():
    assert torfetch._charset("text/html; charset=ISO-8859-1") == "iso-8859-1"
    assert torfetch._charset("text/html") == "utf-8"
    raw = gzip.compress(b"body bytes")
    assert torfetch._maybe_decompress(raw, "gzip") == b"body bytes"
    assert torfetch._maybe_decompress(b"plain", "") == b"plain"


# ---- fake SOCKS5 + origin server, to exercise the SOCKS5 client end-to-end --
class _FakeSocks:
    """Accepts a SOCKS5 CONNECT (with or without user/pass), then replies with a
    fixed HTTP response as if it were the origin."""
    def __init__(self, response: bytes):
        self.response = response
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.requests = []
        self.t = threading.Thread(target=self._serve, daemon=True)
        self.t.start()

    def _recvn(self, c, n):
        b = b""
        while len(b) < n:
            x = c.recv(n - len(b))
            if not x:
                break
            b += x
        return b

    def _serve(self):
        c, _ = self.srv.accept()
        try:
            ver, nm = self._recvn(c, 2)
            methods = self._recvn(c, nm)
            if 0x02 in methods:                    # ask for user/pass
                c.sendall(b"\x05\x02")
                v = self._recvn(c, 1)
                ul = self._recvn(c, 1)[0]
                self._recvn(c, ul)
                pl = self._recvn(c, 1)[0]
                self._recvn(c, pl)
                c.sendall(b"\x01\x00")             # auth ok
            else:
                c.sendall(b"\x05\x00")
            # CONNECT request
            hdr = self._recvn(c, 4)               # ver,cmd,rsv,atyp
            atyp = hdr[3]
            if atyp == 0x03:
                dl = self._recvn(c, 1)[0]
                self._recvn(c, dl)
            self._recvn(c, 2)                      # port
            c.sendall(b"\x05\x00\x00\x01" + b"\x00\x00\x00\x00" + struct.pack(">H", 0))
            # now the client sends the HTTP request (single read; it stays open)
            req = c.recv(8192)
            self.requests.append(req)
            c.sendall(self.response)
        finally:
            c.close()
            self.srv.close()


def test_socks5_http_roundtrip():
    body = b"<html><title>Hi</title><body>onion body</body></html>"
    resp = (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
            b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body)) + body
    fake = _FakeSocks(resp)
    r = torfetch.fetch("http://darkforum.onion/board",
                       proxy_host="127.0.0.1", proxy_port=fake.port, timeout=5)
    assert r.status == 200
    assert "onion body" in r.text
    assert fake.requests and b"GET /board HTTP" in fake.requests[0]
    assert b"Host: darkforum.onion" in fake.requests[0]


def test_socks5_refuses_cross_host_redirect():
    resp = (b"HTTP/1.1 302 Found\r\nLocation: http://evil.onion/x\r\n"
            b"Content-Length: 0\r\nConnection: close\r\n\r\n")
    fake = _FakeSocks(resp)
    with pytest.raises(torfetch.FetchRefused):
        torfetch.fetch("http://darkforum.onion/board",
                       proxy_host="127.0.0.1", proxy_port=fake.port, timeout=5)
