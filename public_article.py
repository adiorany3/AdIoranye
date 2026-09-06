"""Bounded, DNS-pinned HTTPS retrieval for admin article imports only."""

import http.client
import ipaddress
import queue
import re
import socket
import ssl
import threading
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

from daily_kb_scraper import DEFAULT_USER_AGENT, extract_html_text

MAX_BYTES = 2 * 1024 * 1024
FETCH_SECONDS = 20


def _public_target(url):
    if not isinstance(url, str) or len(url) > 4096 or re.search(r"[\s\\\x00-\x1f\x7f]", url):
        raise ValueError("URL tidak valid.")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.port not in (None, 443)):
        raise ValueError("Hanya HTTPS port 443 tanpa kredensial diizinkan.")
    host = parsed.hostname.encode("idna").decode("ascii")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host) or host.endswith("."):
        raise ValueError("Hostname tidak valid.")
    return host, urlunsplit(("", "", parsed.path or "/", parsed.query, ""))


def _resolve(host, timeout):
    # Daemon bounds caller wait even when OS DNS resolver stalls.
    result = queue.Queue(maxsize=1)

    def resolve():
        try:
            result.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except Exception as exc:
            result.put(exc)

    threading.Thread(target=resolve, daemon=True).start()
    try:
        addresses = result.get(timeout=timeout)
    except queue.Empty:
        raise TimeoutError("DNS timeout.") from None
    if isinstance(addresses, Exception):
        raise addresses
    if not addresses:
        raise ValueError("DNS kosong.")
    for _, _, _, _, address in addresses:
        ip = ipaddress.ip_address(address[0])
        if (not ip.is_global or ip.is_multicast
            or (isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped or ip.sixtofour or ip.teredo))):
            raise ValueError("Alamat nonpublik ditolak.")
    return addresses[0]


def fetch_public_article(url):
    """Return final URL and HTML; no proxy, cookies, retries or secondary fetches."""
    deadline = time.monotonic() + FETCH_SECONDS

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError("Pengambilan artikel timeout.")
        return value

    for hop in range(4):
        host, target = _public_target(url)
        family, kind, proto, _, address = _resolve(host, remaining())
        context = ssl.create_default_context()
        raw = socket.socket(family, kind, proto)
        connection = http.client.HTTPSConnection(host, timeout=remaining(), context=context)
        sockets = [raw]

        def expire():
            for sock in sockets:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        timer = threading.Timer(remaining(), expire)
        timer.daemon = True
        timer.start()
        try:
            raw.settimeout(remaining())
            raw.connect(address)  # Numeric sockaddr from validated DNS; never resolve again.
            tls = context.wrap_socket(raw, server_hostname=host, do_handshake_on_connect=False)
            sockets.append(tls)
            tls.settimeout(remaining())
            tls.do_handshake()  # Default context validates chain AND original hostname.
            connection.sock = tls
            connection.request("GET", target, headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Encoding": "identity",
                "Connection": "close",
            })
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location or hop == 3:
                    raise ValueError("Redirect kosong atau terlalu banyak.")
                url = urljoin(url, location)
                continue  # Revalidate scheme, host and every DNS answer at each hop.
            if response.status != 200:
                raise ValueError(f"Artikel ditolak: HTTP {response.status}.")
            if response.headers.get_content_type() not in ("text/html", "application/xhtml+xml"):
                raise ValueError("Respons bukan HTML.")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("Respons terkompresi ditolak.")
            length = response.getheader("Content-Length")
            if length and (int(length) < 0 or int(length) > MAX_BYTES):
                raise ValueError("Artikel melebihi batas 2 MiB.")
            body = response.read(MAX_BYTES + 1)
            remaining()
            if len(body) > MAX_BYTES:
                raise ValueError("Artikel melebihi batas 2 MiB.")
            if length and len(body) != int(length):
                raise ValueError("Respons artikel tidak lengkap.")
            return url, body.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        finally:
            timer.cancel()
            connection.close()
            for sock in sockets:
                sock.close()
    raise ValueError("Redirect terlalu banyak.")


def public_article_source(url):
    final_url, page = fetch_public_article(url.strip())
    title, content = extract_html_text(page)
    # ponytail: conservative markers, not paywall circumvention; add site parsers only with fixtures.
    blocked = re.search(
        r'"isAccessibleForFree"\s*:\s*(?:false|"false")|'
        r'captcha|cf-chl-|challenge-platform|verify you are human|checking your browser|'
        r'just a moment|access denied|enable javascript and cookies|'
        r'berlangganan untuk (?:membaca|melanjutkan)|subscribe to (?:read|continue)|'
        r'sign in to (?:read|continue)|artikel (?:ini )?khusus pelanggan',
        page, re.IGNORECASE,
    )
    if blocked:
        raise ValueError("Halaman challenge/login/paywall ditolak; gunakan artikel publik lain.")
    if not title or not content.strip():
        raise ValueError("Judul atau isi artikel tidak ditemukan.")
    return {
        "name": urlsplit(final_url).hostname,
        "url": final_url,
        "type": "static",
        "static_title": title,
        "static_content": content,
        "collection": "Auto Update",
        "tags": "auto-update,artikel-admin",
        "max_items": 1,
    }