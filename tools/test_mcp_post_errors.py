"""What the MCP layer hands back when the HTTP layer answers instead of MifBridge.

Every tool in server.py goes through one `_post`, so whatever that returns on a failure is what 286
tools return. Most of it was already handled - connect timeouts, read timeouts, a dead editor, a bad
token all come back as {"ok": False, "error": "..."} with something usable in the error.

One case was not, and it is the one that shows up in normal use. Routes are bound ONE PER ENDPOINT
(MifBridgeServer.cpp), so a request for an endpoint the running DLL does not have never reaches
MifBridge's own "unknown endpoint" error at all - Epic's HTTP router answers first with:

    {"errorCode": "errors.com.epicgames.httpserver.route_handler_not_found", "errorMessage": ""}

No `ok`, and an EMPTY message. A caller testing r["ok"] gets None; one reading r["error"] gets
nothing. The realistic way to meet this is not a typo, it is DRIFT - server.py gains a tool while the
editor is still running a DLL built before it - and the fix is a rebuild, which nothing in that
response hints at.

HOW THIS RUNS WITHOUT THE EDITOR. `requests` and `mcp` are not installed in the interpreter the suites
use, and the point is to test the RESPONSE HANDLING rather than the network, so both are stubbed and
`_post` is driven against fabricated responses. That also makes the failure cases reachable at all:
there is no way to ask a healthy editor for a 500.

The pass-through cases matter as much as the error ones. A translation layer that mangles ordinary
answers is worse than one that leaves a bad error alone, so a normal ok:true and a normal ok:false
are both asserted to come through untouched.
"""
import json
import os
import sys
import types

PASS, FAIL = [], []

MCP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp-server")
EPIC_404 = {"errorCode": "errors.com.epicgames.httpserver.route_handler_not_found",
            "errorMessage": ""}


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def load_server():
    """Import server.py with requests and mcp stubbed out."""
    req = types.ModuleType("requests")

    class _Exc(Exception):
        pass

    req.exceptions = types.SimpleNamespace(
        ConnectTimeout=type("ConnectTimeout", (_Exc,), {}),
        ReadTimeout=type("ReadTimeout", (_Exc,), {}),
        ConnectionError=type("ConnectionError", (_Exc,), {}),
        RequestException=type("RequestException", (_Exc,), {}),
    )
    req.post = lambda *a, **k: FakeResponse({}, 200)
    sys.modules["requests"] = req

    for name in ("mcp", "mcp.server", "mcp.server.fastmcp"):
        sys.modules.setdefault(name, types.ModuleType(name))

    class FakeMCP:
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


def main():
    if not os.path.isdir(MCP_DIR):
        print("mcp-server directory not found at %s" % MCP_DIR)
        return 1
    try:
        server = load_server()
    except Exception as exc:
        print("could not import server.py even with requests/mcp stubbed: %s: %s"
              % (type(exc).__name__, exc))
        return 1

    def post_with(resp, endpoint="remove_event_dispatcher"):
        server.requests.post = lambda *a, **k: resp
        return server._post(endpoint)

    print("")
    print("=== T390: an endpoint the running DLL does not have ===")
    r = post_with(FakeResponse(EPIC_404, 404))
    # THE assertion: a caller must be able to test ok, which Epic's raw body does not allow.
    check("T390 the response has an ok field at all", isinstance(r.get("ok"), bool), json.dumps(r)[:170])
    check("T390 and it is false", r.get("ok") is False, json.dumps(r)[:170])
    check("T390 the error is not empty", len(r.get("error") or "") > 20,
          "error=%r - the raw Epic body has an EMPTY errorMessage, which is the whole problem"
          % (r.get("error") or ""))
    check("T390 it names the endpoint that was missing", "remove_event_dispatcher" in (r.get("error") or ""),
          (r.get("error") or "")[:170])
    # The fix is a rebuild; an error that does not say so sends the reader to the wrong place.
    check("T390 and points at the likely cause rather than just the symptom",
          "older" in (r.get("error") or "") or "rebuild" in (r.get("error") or ""),
          (r.get("error") or "")[:170])

    print("")
    print("=== T391: any other bare errorCode from the HTTP layer ===")
    r = post_with(FakeResponse({"errorCode": "errors.com.epicgames.other",
                                "errorMessage": "something else"}, 500))
    check("T391 it is normalised to a testable shape", r.get("ok") is False, json.dumps(r)[:170])
    check("T391 and keeps the message it was given", r.get("error") == "something else",
          json.dumps(r)[:170])
    check("T391 and keeps the code for anyone who wants it",
          "errors.com.epicgames.other" in str(r.get("errorCode")), json.dumps(r)[:170])

    print("")
    print("=== T392: ordinary answers must pass through untouched ===")
    # A translation layer that mangles normal responses is worse than the bug it fixes.
    ok_body = {"ok": True, "endpointCount": 286, "note": "unchanged"}
    r = post_with(FakeResponse(ok_body, 200))
    check("T392 a successful answer is unchanged", r == ok_body, json.dumps(r)[:170])
    fail_body = {"ok": False, "error": "name is required"}
    r = post_with(FakeResponse(fail_body, 200))
    check("T392 a normal refusal is unchanged", r == fail_body, json.dumps(r)[:170])

    print("")
    print("=== T393: the transport failures that were already handled stay handled ===")
    for label, exc in (("connect timeout", server.requests.exceptions.ConnectTimeout),
                       ("read timeout", server.requests.exceptions.ReadTimeout),
                       ("editor not running", server.requests.exceptions.ConnectionError)):
        def raiser(*a, **k):
            raise exc("simulated")
        server.requests.post = raiser
        r = server._post("self_audit")
        check("T393 %s reports ok:false" % label, r.get("ok") is False, json.dumps(r)[:150])
        check("T393 %s says something usable" % label, len(r.get("error") or "") > 15,
              (r.get("error") or "")[:150])
        # Every transport failure is worth retrying - none of them means the request was rejected
        # on its merits.
        check("T393 %s is marked retryable" % label, r.get("retryable") is True,
              json.dumps(r)[:170])

    print("")
    print("=== T394: a caller can tell a BUSY editor from a DOWN one, without parsing English ===")
    # THE DIFFERENCE IS THE ASSERTION. Reporting one value for both states would pass any check
    # that only looked for the key, and would be exactly as useless as the single "bridge failed"
    # string this replaced - which is what made this repo's own sweep runner relaunch the editor
    # beside a working one until the two raced for port 8791.
    states = {}
    for label, exc in (("read timeout", server.requests.exceptions.ReadTimeout),
                       ("connection refused", server.requests.exceptions.ConnectionError),
                       ("connect timeout", server.requests.exceptions.ConnectTimeout)):
        def raiser(*a, **k):
            raise exc("simulated")
        server.requests.post = raiser
        states[label] = server._post("self_audit").get("editorState")

    check("T394 a READ timeout says the editor is BUSY - it accepted the connection, so it is alive",
          states["read timeout"] == "busy", json.dumps(states))
    check("T394 a refused connection says DOWN - nothing is listening",
          states["connection refused"] == "down", json.dumps(states))
    check("T394 and the two are DIFFERENT, which is the whole point",
          states["read timeout"] != states["connection refused"], json.dumps(states))
    check("T394 a connect timeout is reported as unreachable rather than guessing either way",
          states["connect timeout"] == "unreachable", json.dumps(states))

    # The busy message must not tell someone to restart - that is the failure mode being prevented.
    server.requests.post = lambda *a, **k: (_ for _ in ()).throw(
        server.requests.exceptions.ReadTimeout("simulated"))
    busy = server._post("self_audit")
    check("T394 the busy message explicitly says NOT to restart the editor",
          "do not restart" in (busy.get("error") or "").lower(), (busy.get("error") or "")[:200])
    check("T394 and explains WHY it is busy - every endpoint runs on the game thread",
          "game thread" in (busy.get("error") or ""), (busy.get("error") or "")[:200])

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
