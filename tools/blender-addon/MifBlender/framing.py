"""Length-prefixed JSON framing for the MifBlender TCP protocol.

Wire format, identical on both ends:

    [ 4 bytes, big-endian uint32 = body length ][ body: UTF-8 JSON ]

Why length-prefixed and not newline-delimited or "recv until it parses":
a newline framer breaks the moment a payload contains a raw newline, and the
recv-until-parses trick silently mis-frames when two messages arrive in one TCP
segment. A length prefix has neither failure mode and handles multi-megabyte
bodies without a special case.

Adapted from blender-mcp (github.com/MCPBlender/blender-mcp),
(c) 2025 Siddharth Ahuja, MIT licence. MifBridge is proprietary as of 2026-09-04; MIT
permits that, on the condition that the notice travels with the code - it is reproduced in
full in NOTICE.md.

This module deliberately imports NOTHING from bpy. It is safe to call from the
socket thread, and only from there -- see server.py for the threading contract.
"""

from __future__ import annotations

import json
import socket
import struct

HEADER_SIZE = 4
HEADER_FORMAT = ">I"
MAX_MESSAGE_BYTES = 64 * 1024 * 1024  # 64 MiB


def pack_json_message(payload) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    if len(body) > MAX_MESSAGE_BYTES:
        raise ValueError(
            "frame too large: %d bytes (cap %d)" % (len(body), MAX_MESSAGE_BYTES)
        )
    return struct.pack(HEADER_FORMAT, len(body)) + body


def send_json_message(sock: socket.socket, payload) -> None:
    sock.sendall(pack_json_message(payload))


def recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly n bytes or raise. A short recv is normal on TCP, not an error."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed mid-frame (%d of %d bytes)" % (len(buf), n))
        buf += chunk
    return buf


def receive_framed_bytes(sock: socket.socket, timeout=None) -> bytes:
    if timeout is not None:
        sock.settimeout(timeout)
    header = recv_exact(sock, HEADER_SIZE)
    (length,) = struct.unpack(HEADER_FORMAT, header)
    if length <= 0 or length > MAX_MESSAGE_BYTES:
        raise ValueError("invalid frame length: %d" % length)
    return recv_exact(sock, length)


def receive_framed_json(sock: socket.socket, timeout=None):
    return json.loads(receive_framed_bytes(sock, timeout).decode("utf-8"))
