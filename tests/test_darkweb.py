"""Tests for the onion-address aggregator — no live Tor needed.

The torfetch round-trip runs against a fake in-process server that speaks the
SOCKS5 handshake and then answers as the origin, exercising the SOCKS5 client
and the HTTP/redirect/size policy for real.
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

import discover
import parse
import safety as S
import torfetch
from store import Store

ADDR1 = "a" * 56 + ".onion"          # a valid-shaped v3 address
ADDR2 = "b2cd" * 14 + ".onion"       # 56 base32 chars


# --------------------------------------------------------------------------- discover
def test_extract_finds_v3_and_tags_by_target():
    text = f"BreachForums official mirror: {ADDR1} — signed. Random {ADDR2} here."
    sights = discover.extract(text, "ahmia", targets=["breachforums", "xss"])
    by_addr = {s.address: s for s in sights}
    assert ADDR1 in by_addr and ADDR2 in by_addr
    assert "breachforums" in by_addr[ADDR1].tags
    assert by_addr[ADDR2].tags == []          # no target near it


def test_extract_ignores_non_onion_and_short():
    text = "not an onion: deadbeef.onion (too short) or example.com"
    assert discover.extract(text, "x") == []


def test_expand_source_urls_templating():
    src = {"name": "ahmia", "url": "https://ahmia.fi/search/?q={target}"}
    pairs = discover.expand_source_urls(src, ["breach forums", "xss"])
    names = [n for n, _ in pairs]
    urls = [u for _, u in pairs]
    assert names == ["ahmia:breach forums", "ahmia:xss"]
    assert urls[0].endswith("q=breach+forums")
    plain = discover.expand_source_urls({"name": "x", "url": "http://y.onion/"}, ["a"])
    assert plain == [("x", "http://y.onion/")]


# --------------------------------------------------------------------------- safety (CSAM)
def test_safety_blocks_csam_terms_but_not_tech_cp():
    f = S.SafetyFilter()
    assert f.text_flagged("selling pthc collection")          # specific CSAM abbr
    assert f.text_flagged("child porn vendor")
    assert f.text_flagged("nude children videos")             # co-occurrence
    # must NOT flag legitimate threat-intel / tech usage of 'cp'
    assert not f.text_flagged("use cp to copy the db, then scp it")
    assert not f.text_flagged("BreachForums cp = control panel access for sale")
    assert not f.text_flagged("new RCE exploit and stealer logs for sale")


def test_safety_address_blocklist_and_hash():
    import hashlib
    a = ADDR1
    f = S.SafetyFilter(blocked_addresses={a})
    assert f.address_blocked(a)
    h = hashlib.md5(ADDR2.encode()).hexdigest()
    f2 = S.SafetyFilter(blocked_hashes={h})
    assert f2.address_blocked(ADDR2)
    assert not f2.address_blocked(ADDR1)


def test_safety_allow_counts_blocked():
    f = S.SafetyFilter()
    assert f.allow(ADDR1, "BreachForums databases for sale")
    assert not f.allow(ADDR2, "pthc pack here")
    assert f.blocked_count == 1


# --------------------------------------------------------------------------- store
def test_store_sighting_dedup_tags_and_liveness():
    with tempfile.TemporaryDirectory() as d:
        st = Store(Path(d) / "o.db")
        assert st.add_sighting(ADDR1, "ahmia", "BF mirror", "context here", ["breachforums"])
        assert not st.add_sighting(ADDR1, "ahmia", "BF mirror", "context here", ["breachforums"])  # dup addr
        st.set_liveness(ADDR1, status="up", http_status=200, title="BreachForums")
        rows = st.listing()
        assert len(rows) == 1 and rows[0]["status"] == "up"
        assert rows[0]["title"] == "BreachForums" and "breachforums" in (rows[0]["tags"] or "")
        s = st.stats()
        assert s["onions"] == 1 and s["up"] == 1 and s["tagged"] == 1
        st.close()


def test_store_blocked_is_hidden_and_purged():
    with tempfile.TemporaryDirectory() as d:
        st = Store(Path(d) / "o.db")
        st.add_sighting(ADDR1, "ahmia", "label", "ctx", ["xss"])
        st.mark_blocked(ADDR1)
        assert st.is_blocked(ADDR1)
        assert st.listing() == []                       # never listed
        assert st.addresses_to_check() == []            # never re-checked
        s = st.stats()
        assert s["onions"] == 0 and s["blocked_csam"] == 1
        # its sightings + tags were purged
        assert st.db.execute("SELECT COUNT(*) FROM sightings").fetchone()[0] == 0
        assert st.db.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0
        st.close()


# --------------------------------------------------------------------------- parse
def test_parse_title_and_same_host_links():
    html = ("<html><head><title>Idx</title><script>x()</script></head>"
            "<body><a href='/p2'>next</a><a href='http://other.onion/x'>off</a>"
            "<p>text</p></body></html>")
    out = parse.parse_page(html, "http://idx.onion/p1")
    assert out["title"] == "Idx" and "text" in out["text"] and "x()" not in out["text"]
    urls = {l["url"] for l in out["links"]}
    assert "http://idx.onion/p2" in urls and "http://other.onion/x" not in urls


# --------------------------------------------------------------------------- torfetch policy
def test_refuses_non_onion_without_override():
    with pytest.raises(torfetch.FetchRefused):
        torfetch.fetch("http://example.com/", max_redirects=0)


def test_charset_and_gzip_helpers():
    assert torfetch._charset("text/html; charset=ISO-8859-1") == "iso-8859-1"
    assert torfetch._charset("text/html") == "utf-8"
    assert torfetch._maybe_decompress(gzip.compress(b"body"), "gzip") == b"body"
    assert torfetch._maybe_decompress(b"plain", "") == b"plain"


# ---- fake SOCKS5 + origin server -------------------------------------------
class _FakeSocks:
    def __init__(self, response: bytes):
        self.response = response
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.requests = []
        threading.Thread(target=self._serve, daemon=True).start()

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
            self._recvn(c, 2)
            nm = 2
            methods = self._recvn(c, nm)
            if 0x02 in methods:
                c.sendall(b"\x05\x02")
                self._recvn(c, 1)
                ul = self._recvn(c, 1)[0]; self._recvn(c, ul)
                pl = self._recvn(c, 1)[0]; self._recvn(c, pl)
                c.sendall(b"\x01\x00")
            else:
                c.sendall(b"\x05\x00")
            hdr = self._recvn(c, 4)
            if hdr[3] == 0x03:
                dl = self._recvn(c, 1)[0]; self._recvn(c, dl)
            self._recvn(c, 2)
            c.sendall(b"\x05\x00\x00\x01" + b"\x00\x00\x00\x00" + struct.pack(">H", 0))
            self.requests.append(c.recv(8192))
            c.sendall(self.response)
        finally:
            c.close()
            self.srv.close()


def test_socks5_http_roundtrip():
    body = b"<html><title>Hi</title><body>onion body</body></html>"
    resp = (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: %d\r\n"
            b"Connection: close\r\n\r\n" % len(body)) + body
    fake = _FakeSocks(resp)
    r = torfetch.fetch("http://darkforum.onion/board", proxy_host="127.0.0.1",
                       proxy_port=fake.port, timeout=5)
    assert r.status == 200 and "onion body" in r.text
    assert fake.requests and b"GET /board HTTP" in fake.requests[0]
    assert b"Host: darkforum.onion" in fake.requests[0]


def test_socks5_refuses_cross_host_redirect():
    resp = (b"HTTP/1.1 302 Found\r\nLocation: http://evil.onion/x\r\n"
            b"Content-Length: 0\r\nConnection: close\r\n\r\n")
    fake = _FakeSocks(resp)
    with pytest.raises(torfetch.FetchRefused):
        torfetch.fetch("http://darkforum.onion/board", proxy_host="127.0.0.1",
                       proxy_port=fake.port, timeout=5)
