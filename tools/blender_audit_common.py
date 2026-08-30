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


def reachable(timeout=1.5):
    """True if something is listening on the addon port.

    Every Blender suite needs this before its first call, because call() raises rather than
    returning an error dict when nothing is there - so `if not call("ping").get("ok")` never runs
    and the suite dies with a traceback and exit 1 instead of skipping with exit 2. It lived as a
    private copy inside test_blender_ops.py, which is precisely why the two suites written later
    did not have it. Shared here so the next one cannot miss it.
    """
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((HOST, PORT))
        return True
    except Exception:
        return False
    finally:
        s.close()


def skip_banner(name):
    """The loud skip every Blender suite should print. A skip that looks like a pass is how an
    untested thing gets believed, so it names what was NOT verified and why."""
    print("")
    print("SKIPPED - nothing was verified.")
    print("  Blender is not listening on %s:%d, so no %s op was exercised." % (HOST, PORT, name))
    print("  Start one with tools/run_blender_suites.py, or run Blender with the MifBlender addon.")
    print("  Exit code 2 means SKIPPED, distinct from 0 (passed) and 1 (failed) on purpose.")
    return 2


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
