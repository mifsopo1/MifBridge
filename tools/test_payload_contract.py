"""`_post` and `_blender` drop None and SEND EVERYTHING ELSE - the contract six shipped bugs turned on.

WHY THIS EXISTS. On 2026-09-03 six MCP tools were found uncallable, every one of them the same shape:
the handler refuses a key for being PRESENT, and the wrapper carried a concrete default, so the
transport never dropped it.

    sculpt_landscape                flatten and smooth unreachable, v0.3.0 to v0.8.1
    override_inherited_component    uncallable at all, every one of eight tagged releases
    map_legacy_input                uncallable in BOTH modes, v0.7.0 onward
    set_struct_member               uncallable
    set_enum_value                  bitflags mode unreachable
    set_collision                   APPLIED the profile, then answered "NOTHING was changed"

Every one of those depends on one line in each transport - `{k: v for k, v in payload.items() if v is
not None}` - behaving in a specific way that NOTHING TESTED. audit_mcp_default_sends watches the
wrappers for concrete defaults, which is the other half; this pins the rule those wrappers are
written against. If the filter ever grew to drop falsy values instead of None, every `or None` in
server.py would become redundant, every audit_mcp_default_sends row would go quiet, and the tools
would start working by accident - until somebody needed to send `enabled: false` on purpose.

THE ASSERTION THAT MATTERS IS THAT FALSE IS SENT. Dropping None is the easy half and the obvious
half; keeping False, 0, 0.0 and "" is the half that makes a refusal reachable and a deliberate
`false` distinguishable from silence. mifaudit.AUTHORISING_ONLY rests on exactly that distinction -
"a false is a REFUSAL to authorise, and must reach the handler".

Static. Stubs requests and the Blender socket, imports server.py, and asserts on the body each
transport BUILDS. No editor, no Blender, no bridge - which is the point: the contract six shipped
bugs turned on should not need a live machine to check.
"""
import json
import os
import sys
import types

MCP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp-server")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


class FakeResponse(object):
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def load_server(captured):
    """Import server.py with requests and mcp stubbed, capturing every _post body."""
    req = types.ModuleType("requests")

    class _Exc(Exception):
        pass

    req.exceptions = types.SimpleNamespace(
        ConnectTimeout=type("ConnectTimeout", (_Exc,), {}),
        ReadTimeout=type("ReadTimeout", (_Exc,), {}),
        ConnectionError=type("ConnectionError", (_Exc,), {}),
        RequestException=type("RequestException", (_Exc,), {}),
    )

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        captured.append(json)
        return FakeResponse({"ok": True})

    req.post = fake_post
    sys.modules["requests"] = req

    for name in ("mcp", "mcp.server", "mcp.server.fastmcp"):
        sys.modules.setdefault(name, types.ModuleType(name))

    class FakeMCP(object):
        def __init__(self, *a, **k):
            pass

        def tool(self, *a, **k):
            return lambda fn: fn

        def run(self, *a, **k):
            pass

    sys.modules["mcp.server.fastmcp"].FastMCP = FakeMCP
    sys.path.insert(0, MCP_DIR)
    import server
    return server


def blender_body(server, sent):
    """Make _blender build and 'send' a frame, returning the params dict it built.

    _bl_connect is replaced rather than the socket module: it is the one seam the transport goes
    through, and patching it leaves the framing, the size guard and the JSON encoding intact - all
    of which run BEFORE the params reach the wire and any of which could drop a key.
    """
    class FakeSock(object):
        def sendall(self, data):
            sent.append(data)

        def settimeout(self, *_a):
            pass

        def close(self):
            pass

    server._bl_connect = lambda: FakeSock()
    return FakeSock


def main():
    if not os.path.isdir(MCP_DIR):
        print("mcp-server directory not found at %s" % MCP_DIR)
        return 1
    captured = []
    try:
        server = load_server(captured)
    except Exception as exc:                       # noqa: BLE001
        print("could not import server.py even with requests/mcp stubbed: %s: %s"
              % (type(exc).__name__, exc))
        return 1

    # ---------------------------------------------------------------- P100 the _post contract
    print("=== P100: _post drops None and sends every other falsy value ===")
    del captured[:]
    server._post("probe_zz", dropped=None, false_=False, zero=0, zerof=0.0, empty="",
                 emptylist=[], truthy="yes")
    body = captured[-1] if captured else {}
    check("P100 (setup) _post built a body", isinstance(body, dict), body)
    check("P100 None is DROPPED - the whole reason wrappers default to None",
          "dropped" not in body, sorted(body))
    # THE FOUR THAT MATTER. Each is a value a handler can refuse for being present, and each was
    # the actual payload in at least one of the six shipped bugs: False in map_legacy_input and
    # override_inherited_component, 0.0 in sculpt_landscape and map_legacy_input's scale, "" in
    # set_struct_member, set_enum_value and set_collision.
    check("P100 False is SENT - a deliberate false must reach the handler, not vanish",
          body.get("false_") is False, body)
    check("P100 0 is SENT", body.get("zero") == 0 and "zero" in body, body)
    check("P100 0.0 is SENT - sculpt_landscape's amount was exactly this",
          body.get("zerof") == 0.0 and "zerof" in body, body)
    check("P100 empty string is SENT - set_struct_member's newName was exactly this",
          body.get("empty") == "" and "empty" in body, body)
    check("P100 empty list is SENT", body.get("emptylist") == [] and "emptylist" in body, body)
    check("P100 an ordinary value is SENT", body.get("truthy") == "yes", body)

    # ---------------------------------------------------------------- P101 nothing but None goes
    print("")
    print("=== P101: a call with only None args sends an EMPTY body, not a body of nulls ===")
    del captured[:]
    server._post("probe_zz", a=None, b=None)
    body = captured[-1] if captured else None
    check("P101 the body is empty", body == {}, body)

    # ---------------------------------------------------------------- P102 the Blender transport
    print("")
    print("=== P102: _blender applies the SAME rule, on the other transport ===")
    sent = []
    blender_body(server, sent)
    try:
        server._blender("probe_zz", dropped=None, false_=False, zero=0, empty="", truthy="yes")
    except Exception:
        # The fake socket never answers, so the read side raises. The frame was already built and
        # captured by then, which is the half under test.
        pass
    # THE HEADER WIDTH IS ASKED FOR, NOT GUESSED. _blender sends
    # struct.pack(_BL_HDR_FMT, len(payload)) + payload, so the JSON starts at calcsize(_BL_HDR_FMT).
    # A first version split on the first newline, which is not the framing at all - it produced an
    # empty dict, and the setup check below passed anyway because isinstance({}, dict) is True.
    # That is the shape this repo keeps writing up: a setup assertion satisfied by the empty case it
    # was meant to rule out. It now requires the params to be NON-EMPTY.
    import struct as _struct
    frame = {}
    if sent:
        try:
            off = _struct.calcsize(server._BL_HDR_FMT)
            frame = json.loads(sent[-1][off:].decode("utf-8"))
        except Exception:                          # noqa: BLE001
            frame = {}
    params = frame.get("params") if isinstance(frame, dict) else {}
    params = params if isinstance(params, dict) else {}
    check("P102 (setup) _blender built a frame carrying a NON-EMPTY params object",
          bool(sent) and bool(params),
          (len(sent), list(frame)[:6] if isinstance(frame, dict) else frame))
    if sent and params:
        check("P102 None is DROPPED on the Blender transport too", "dropped" not in params,
              sorted(params))
        check("P102 False is SENT - bl_create_camera and bl_set_viewport_view both refuse a "
              "defaulted key, so this is the same hazard on the other side",
              params.get("false_") is False, params)
        check("P102 empty string is SENT", params.get("empty") == "" and "empty" in params, params)
    else:
        check("P102 the Blender frame could not be read, so this arm proved NOTHING", False,
              "sent=%d frame=%r" % (len(sent), frame))

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
