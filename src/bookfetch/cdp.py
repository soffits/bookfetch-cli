"""A minimal Chrome DevTools Protocol client using only the standard library.

It speaks just enough WebSocket (RFC 6455) to send masked text frames and read
text, continuation, ping and close frames, which is all CDP needs. Targets are
listed and opened through the DevTools HTTP endpoints (/json/list, /json/new).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import urllib.error
import urllib.parse
import urllib.request

from .common import BookfetchError

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class CDPError(BookfetchError):
    """The browser could not be reached or a DevTools command failed."""


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


def encode_frame(payload: bytes, opcode: int = OP_TEXT, mask: bytes | None = None) -> bytes:
    """Encode one final client frame. Client frames must be masked."""
    mask = os.urandom(4) if mask is None else mask
    head = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 1 << 16:
        head += bytes([0x80 | 126]) + struct.pack("!H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack("!Q", n)
    return head + mask + apply_mask(payload, mask)


def apply_mask(data: bytes, mask: bytes) -> bytes:
    if not data:
        return b""
    n = len(data)
    key = int.from_bytes((mask * (n // 4 + 1))[:n], "big")
    return (int.from_bytes(data, "big") ^ key).to_bytes(n, "big")


def read_frame(recv) -> tuple[bool, int, bytes]:
    """Read one frame with ``recv(n)`` (which must return exactly n bytes)."""
    b0, b1 = recv(2)
    fin, opcode = bool(b0 & 0x80), b0 & 0x0F
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack("!H", recv(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", recv(8))[0]
    mask = recv(4) if b1 & 0x80 else None
    payload = recv(n) if n else b""
    return fin, opcode, apply_mask(payload, mask) if mask else payload


class WebSocket:
    def __init__(self, url: str, timeout: float = 60):
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("ws", "wss"):
            raise CDPError(f"not a WebSocket URL: {url}")
        port = u.port or (443 if u.scheme == "wss" else 80)
        try:
            sock = socket.create_connection((u.hostname, port), timeout=timeout)
            if u.scheme == "wss":
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=u.hostname)
        except OSError as e:
            raise CDPError(f"cannot connect to {u.hostname}:{port}: {e}") from None
        self.sock = sock
        self._buf = b""
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        head = self._read_until(b"\r\n\r\n").decode("latin-1")
        status = head.split("\r\n", 1)[0]
        if " 101 " not in status + " ":
            raise CDPError(f"WebSocket handshake failed: {status}")
        headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in head.split("\r\n")[1:]) if k}
        if headers.get("sec-websocket-accept") != accept_key(key):
            raise CDPError("WebSocket handshake failed: bad Sec-WebSocket-Accept")

    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self._buf:
            self._fill()
        head, _, self._buf = self._buf.partition(marker)
        return head

    def _fill(self) -> None:
        try:
            chunk = self.sock.recv(1 << 16)
        except OSError as e:
            raise CDPError(f"WebSocket read failed: {e}") from None
        if not chunk:
            raise CDPError("WebSocket closed by the browser")
        self._buf += chunk

    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            self._fill()
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send_text(self, text: str) -> None:
        self.sock.sendall(encode_frame(text.encode()))

    def recv_text(self) -> str:
        parts: list[bytes] = []
        while True:
            fin, opcode, payload = read_frame(self._recv_exact)
            if opcode == OP_PING:
                self.sock.sendall(encode_frame(payload, OP_PONG))
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                raise CDPError("WebSocket closed by the browser")
            parts.append(payload)
            if fin:
                return b"".join(parts).decode("utf-8")

    def close(self) -> None:
        try:
            self.sock.sendall(encode_frame(b"\x03\xe8", OP_CLOSE))
        except OSError:
            pass
        self.sock.close()


class Page:
    """A DevTools session attached to one page target."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.next_id = 0

    def call(self, method: str, **params) -> dict:
        self.next_id += 1
        msg_id = self.next_id
        self.ws.send_text(json.dumps({"id": msg_id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv_text())
            if msg.get("id") != msg_id:
                continue  # an event or a stale reply
            if "error" in msg:
                raise CDPError(f"{method}: {msg['error'].get('message', msg['error'])}")
            return msg.get("result", {})

    def evaluate(self, expression: str):
        """Evaluate JS in the page, awaiting promises, and return the value."""
        r = self.call("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            text = (d.get("exception") or {}).get("description") or d.get("text") or "exception"
            raise CDPError(f"page script failed: {text.splitlines()[0]}")
        return (r.get("result") or {}).get("value")


class Browser:
    """The DevTools HTTP endpoint of a running Chromium (e.g. http://127.0.0.1:9222)."""

    def __init__(self, endpoint: str, timeout: float = 60):
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    def _json(self, path: str, method: str = "GET"):
        req = urllib.request.Request(self.endpoint + path, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError:
            raise
        except (OSError, ValueError) as e:
            raise CDPError(f"cannot reach the browser at {self.endpoint}: {e}") from None

    def targets(self) -> list[dict]:
        return [t for t in self._json("/json/list") if t.get("type") == "page"]

    def new_tab(self, url: str) -> dict:
        path = "/json/new?" + urllib.parse.quote(url, safe=":/")
        try:
            return self._json(path, method="PUT")
        except urllib.error.HTTPError:
            try:
                return self._json(path)  # older Chromium only accepts GET
            except urllib.error.HTTPError as e:
                raise CDPError(f"cannot open a tab: HTTP {e.code}") from None

    def close_tab(self, target_id: str) -> None:
        try:
            req = urllib.request.Request(f"{self.endpoint}/json/close/{target_id}")
            urllib.request.urlopen(req, timeout=self.timeout).close()
        except OSError:
            pass

    def attach(self, target: dict) -> Page:
        url = target.get("webSocketDebuggerUrl")
        if not url:
            raise CDPError("the tab has no WebSocket URL (is another DevTools client attached?)")
        return Page(WebSocket(url, timeout=self.timeout))
