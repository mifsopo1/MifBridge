"""Shared low-level plumbing for the audit_blender_*.py scripts.

One socket helper, kept in one place, because tools/mcp-server/server.py cannot be imported standalone
without pulling in the mcp package (see tools/mcp-server/requirements.txt) and these audits need to run
without that dependency. This is the same wire protocol test_blender_mesh.py's call() implements -
{"endpoint", "token", "params"} framed with a 4-byte big-endian length prefix - kept here rather than a
third copy once a second audit script needed it too.
"""
import json
import os
import socket
import struct

HOST = os.environ.get("MIF_BLENDER_HOST", "127.0.0.1")
try:
    PORT = int(os.environ.get("MIF_BLENDER_PORT", "8792"))
except ValueError:
    PORT = 8792
TOKEN = os.environ.get("MIF_BLENDER_TOKEN", os.environ.get("MIF_BRIDGE_TOKEN", "dev"))


def call(op, params=None, timeout=30.0):
    s = socket.create_connection((HOST, PORT), timeout=timeout)
    try:
        msg = json.dumps({"endpoint": op, "token": TOKEN, "params": params or {}}).encode("utf-8")
        s.sendall(struct.pack(">I", len(msg)) + msg)
        hdr = b""
        while len(hdr) < 4:
            chunk = s.recv(4 - len(hdr))
            if not chunk:
                raise RuntimeError("connection closed reading length header")
            hdr += chunk
        n = struct.unpack(">I", hdr)[0]
        body = b""
        while len(body) < n:
            chunk = s.recv(n - len(body))
            if not chunk:
                raise RuntimeError("connection closed reading body")
            body += chunk
        return json.loads(body.decode("utf-8"))
    finally:
        s.close()
