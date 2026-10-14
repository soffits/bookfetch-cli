import json
import socket
import struct
import threading

import pytest

from bookfetch.cdp import (
    OP_CLOSE,
    OP_CONT,
    OP_PING,
    OP_PONG,
    OP_TEXT,
    CDPError,
    Page,
    WebSocket,
    accept_key,
    apply_mask,
    encode_frame,
    read_frame,
)


def reader(data: bytes):
    buf = bytearray(data)

    def recv(n):
        assert len(buf) >= n, "read past the end"
        out = bytes(buf[:n])
        del buf[:n]
        return out

    return recv


def test_accept_key_rfc_example():
    # The worked example from RFC 6455 section 1.3.
    assert accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_encode_small_masked_frame_rfc_example():
    # RFC 6455 section 5.7: a masked "Hello".
    frame = encode_frame(b"Hello", OP_TEXT, mask=bytes.fromhex("37fa213d"))
    assert frame == bytes.fromhex("818537fa213d7f9f4d5158")


@pytest.mark.parametrize(("size", "header_len"), [(125, 2), (126, 4), (65535, 4), (65536, 10), (200_000, 10)])
def test_length_encodings_round_trip(size, header_len):
    payload = bytes(range(256)) * (size // 256) + bytes(size % 256)
    frame = encode_frame(payload, OP_TEXT, mask=b"\x01\x02\x03\x04")
    assert len(frame) == header_len + 4 + size
    fin, opcode, data = read_frame(reader(frame))
    assert (fin, opcode, data) == (True, OP_TEXT, payload)


def test_read_unmasked_server_frames():
    assert read_frame(reader(bytes.fromhex("810548656c6c6f"))) == (True, OP_TEXT, b"Hello")
    big = b"x" * 70000
    frame = bytes([0x81, 127]) + struct.pack("!Q", len(big)) + big
    assert read_frame(reader(frame)) == (True, OP_TEXT, big)
    frame = bytes([0x81, 126]) + struct.pack("!H", 300) + b"y" * 300
    assert read_frame(reader(frame))[2] == b"y" * 300


def test_apply_mask_is_symmetric():
    mask = b"\xaa\xbb\xcc\xdd"
    assert apply_mask(apply_mask(b"abcdefg", mask), mask) == b"abcdefg"
    assert apply_mask(b"", mask) == b""


def server_frame(payload: bytes, opcode=OP_TEXT, fin=True) -> bytes:
    n = len(payload)
    b0 = (0x80 if fin else 0) | opcode
    if n < 126:
        return bytes([b0, n]) + payload
    if n < 1 << 16:
        return bytes([b0, 126]) + struct.pack("!H", n) + payload
    return bytes([b0, 127]) + struct.pack("!Q", n) + payload


class FakeServer:
    """A one-connection WebSocket server on localhost that replays scripted frames."""

    def __init__(self, replies, bad_accept=False):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.replies = replies
        self.bad_accept = bad_accept
        self.received = []
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        conn, _ = self.sock.accept()
        with conn:
            data = b""
            while b"\r\n\r\n" not in data:
                data += conn.recv(4096)
            head, _, rest = data.partition(b"\r\n\r\n")
            key = [
                line.split(b":", 1)[1].strip().decode() for line in head.split(b"\r\n") if line.lower().startswith(b"sec-websocket-key")
            ][0]
            accept = "wrong" if self.bad_accept else accept_key(key)
            conn.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                + f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode()
            )
            buf = bytearray(rest)

            def recv(n):
                while len(buf) < n:
                    chunk = conn.recv(65536)
                    if not chunk:
                        raise EOFError
                    buf.extend(chunk)
                out = bytes(buf[:n])
                del buf[:n]
                return out

            try:
                for reply in self.replies:
                    fin, opcode, payload = read_frame(recv)
                    self.received.append((opcode, payload))
                    conn.sendall(reply(payload) if callable(reply) else reply)
                while True:  # collect anything else (pong, close)
                    self.received.append(read_frame(recv)[1:])
            except (EOFError, OSError, ValueError):
                pass


def test_websocket_round_trip_with_fragments_and_ping():
    big = "z" * 100_000

    def reply(payload):
        msg = json.loads(payload)
        body = json.dumps({"id": 99, "method": "Event"}).encode()  # an unrelated event first
        text = json.dumps({"id": msg["id"], "result": {"result": {"value": big}}}).encode()
        half = len(text) // 2
        return (
            server_frame(body)
            + server_frame(b"hb", OP_PING)
            + server_frame(text[:half], OP_TEXT, fin=False)
            + server_frame(text[half:], OP_CONT)
        )

    server = FakeServer([reply])
    page = Page(WebSocket(f"ws://127.0.0.1:{server.port}/devtools/page/X", timeout=5))
    assert page.evaluate("1+1") == big
    page.ws.close()
    server.thread.join(5)
    opcode, payload = server.received[0]
    assert opcode == OP_TEXT
    sent = json.loads(payload)
    assert sent["method"] == "Runtime.evaluate"
    assert sent["params"] == {"expression": "1+1", "awaitPromise": True, "returnByValue": True}
    assert (OP_PONG, b"hb") in server.received
    assert any(op == OP_CLOSE for op, _ in server.received[1:])


def test_websocket_errors_and_exceptions():
    def reply(payload):
        msg = json.loads(payload)
        return server_frame(
            json.dumps(
                {
                    "id": msg["id"],
                    "result": {"exceptionDetails": {"text": "Uncaught", "exception": {"description": "TypeError: boom\n at x"}}},
                }
            ).encode()
        )

    server = FakeServer([reply, lambda p: server_frame(json.dumps({"id": 2, "error": {"message": "No target"}}).encode())])
    page = Page(WebSocket(f"ws://127.0.0.1:{server.port}/", timeout=5))
    with pytest.raises(CDPError, match="TypeError: boom"):
        page.evaluate("x")
    with pytest.raises(CDPError, match="Page.navigate: No target"):
        page.call("Page.navigate", url="about:blank")
    page.ws.close()


def test_handshake_rejects_bad_accept():
    server = FakeServer([], bad_accept=True)
    with pytest.raises(CDPError, match="Sec-WebSocket-Accept"):
        WebSocket(f"ws://127.0.0.1:{server.port}/", timeout=5)


def test_closed_by_server():
    server = FakeServer([server_frame(b"\x03\xe8", OP_CLOSE)])
    ws = WebSocket(f"ws://127.0.0.1:{server.port}/", timeout=5)
    ws.send_text("{}")
    with pytest.raises(CDPError, match="closed"):
        ws.recv_text()


def test_bad_url():
    with pytest.raises(CDPError, match="not a WebSocket URL"):
        WebSocket("http://127.0.0.1:1/")
