#!/usr/bin/env python3
"""DEPRECATED PATH SHIM — do not edit, do not add tools here.

The real MCP server moved in MifBridge 0.3.0:

    tools/ue5-mcp-bridge/server.py   ->   tools/mcp-server/server.py

It was renamed because the server is no longer UE5-only: the same process now
fronts two backends (the Unreal editor plugin over HTTP, and the MifBlender
addon over a local socket), so "ue5-mcp-bridge" named the wrong thing.

This file exists only so an existing `.mcp.json` that still points at the old
path keeps working. It forwards to the new server unchanged. Update your
config to `tools/mcp-server/server.py` and this shim can go away.

The warning goes to STDERR on purpose: stdout carries the MCP stdio JSON-RPC
stream and a single stray byte on it breaks the client handshake.
"""

import os
import runpy
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REAL = os.path.normpath(os.path.join(_HERE, os.pardir, "mcp-server", "server.py"))

if not os.path.isfile(_REAL):
    sys.stderr.write(
        "[mif-bridge] FATAL: deprecated shim at {shim} cannot find the real server at {real}. "
        "Point your .mcp.json at <repo>/tools/mcp-server/server.py.\n".format(
            shim=os.path.abspath(__file__), real=_REAL
        )
    )
    raise SystemExit(2)

sys.stderr.write(
    "[mif-bridge] DEPRECATED PATH: tools/ue5-mcp-bridge/server.py forwarded to "
    "tools/mcp-server/server.py. Update the 'args' entry in your .mcp.json; "
    "this shim will be removed in a future release.\n"
)
sys.stderr.flush()

runpy.run_path(_REAL, run_name="__main__")
