"""find_tools - the keyword search over this MCP server's OWN tool registry.

WHY THIS EXISTS. server.py wraps 300+ endpoints as individual @mcp.tool functions, one per plugin
capability, and that count only grows. Before find_tools, an LLM driving MifBridge that wanted "the
skeletal mesh tools" had exactly two options: scan every registered tool name by eye, or already know
what to look for. find_tools answers "what tools exist for X" in one call, ranked (name hits before
description hits) and trimmed (a ~200-char summary, not the full docstring) so the answer is cheap to
read and act on.

HOW THIS RUNS WITHOUT THE EDITOR, and without a real `mcp` install. Same reason and same technique as
test_mcp_post_errors.py: `requests` and `mcp` are not installed in the interpreter these suites run
under, so both are stubbed before server.py is imported. That suite's FakeMCP.tool() is a bare
passthrough (`lambda fn: fn`) with no registry behind it at all, which is enough to test _post's error
handling but cannot exercise find_tools - there would be nothing to search. So this file's FakeMCP
actually RECORDS what @mcp.tool() decorates, and exposes it through a `_tool_manager.list_tools()`
shaped like the real mcp.server.fastmcp.tools.base.Tool (name / description / parameters), which is
the exact surface find_tools reads. This tests find_tools' own logic - matching, ranking, truncation,
whitespace collapse - against a small FAKE registry, not the real 300+ tool one; that is what
tools/mcp-server smoke-tests via `python server.py --debug` and what parity_check.py's "@mcp.tool
wrapper... every one can be called" check covers for the real file.
"""
import json
import os
import sys
import types

PASS, FAIL = [], []

MCP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp-server")


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


class FakeTool:
    def __init__(self, name, description, params):
        self.name = name
        self.description = description
        # Real Tool.parameters is a JSON-schema-shaped dict; only "properties" is read.
        self.parameters = {"properties": {p: {} for p in params}}


def load_server():
    """Import server.py with requests and mcp stubbed - mcp's stub actually records tools."""
    req = types.ModuleType("requests")

    class _Exc(Exception):
        pass

    req.exceptions = types.SimpleNamespace(
        ConnectTimeout=type("ConnectTimeout", (_Exc,), {}),
        ReadTimeout=type("ReadTimeout", (_Exc,), {}),
        ConnectionError=type("ConnectionError", (_Exc,), {}),
        RequestException=type("RequestException", (_Exc,), {}),
    )
    req.post = lambda *a, **k: None
    sys.modules["requests"] = req

    for name in ("mcp", "mcp.server", "mcp.server.fastmcp"):
        sys.modules.setdefault(name, types.ModuleType(name))

    class FakeToolManager:
        def __init__(self):
            self._tools = []

        def list_tools(self):
            return list(self._tools)

    class FakeMCP:
        def __init__(self, *a, **k):
            self._tool_manager = FakeToolManager()

        def tool(self, *a, **k):
            def deco(fn):
                doc = (fn.__doc__ or "").strip()
                # Same param-name source real FastMCP uses: the function's own signature.
                import inspect
                params = [p for p in inspect.signature(fn).parameters]
                self._tool_manager._tools.append(FakeTool(fn.__name__, doc, params))
                return fn
            return deco

        def run(self, *a, **k):
            pass

    sys.modules["mcp.server.fastmcp"].FastMCP = FakeMCP

    if MCP_DIR not in sys.path:
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

    # The REAL server.py's own ~366 tools are what got registered when it was imported above -
    # find_tools reads exactly that registry. Nothing fabricated from here down.

    print("")
    print("=== T780: a keyword that hits real tool NAMES ranks those first ===")
    r = server.find_tools("skeletal")
    check("T780 it succeeds", r.get("ok") is True, json.dumps(r)[:200])
    names = [x["name"] for x in r.get("results") or []]
    check("T780 and finds a tool that genuinely has 'skeletal' in its name",
          "analyze_skeletal_split" in names, names[:10])
    check("T780 every result actually contains the keyword somewhere",
          all("skeletal" in (x["name"] + " " + x["summary"]).lower() for x in r.get("results") or []),
          json.dumps(r.get("results"))[:300])

    print("")
    print("=== T781: name hits are ranked before description-only hits ===")
    r = server.find_tools("bone")
    results = r.get("results") or []
    name_flags = [("bone" in x["name"].lower()) for x in results]
    # A True must never follow a False: once the run switches to description-only hits it must stay
    # there. Written as "no False is immediately followed by a True" so a single misordered pair fails
    # it, rather than a looser property a broken sort could still satisfy by accident.
    ok_order = all(name_flags[i] or not name_flags[i + 1] for i in range(len(name_flags) - 1))
    check("T781 name-hit tools never appear after a description-only one",
          ok_order, name_flags)
    check("T781 (sanity: this keyword actually produced BOTH kinds, or the ordering check above is vacuous)",
          any(name_flags) and not all(name_flags), name_flags)

    print("")
    print("=== T782: summaries are trimmed and whitespace-collapsed ===")
    r = server.find_tools("list_bones")
    hit = next((x for x in (r.get("results") or []) if x["name"] == "list_bones"), None)
    check("T782 list_bones itself is found by its own name", hit is not None, r)
    if hit:
        check("T782 the summary has no raw newline", "\n" not in hit["summary"], repr(hit["summary"])[:200])
        check("T782 the summary has no doubled internal whitespace",
              "  " not in hit["summary"], repr(hit["summary"])[:200])
        check("T782 the summary is capped near 200 chars (plus an ellipsis)",
              len(hit["summary"]) <= 204, len(hit["summary"]))
        check("T782 params are reported for a tool that has them",
              "path" in hit["params"], hit["params"])

    print("")
    print("=== T783: limit truncates and says so honestly ===")
    r = server.find_tools("skeletal", limit=1)
    check("T783 count respects limit", r.get("count") == 1, r.get("count"))
    check("T783 matched reports the TRUE total, not the trimmed one",
          isinstance(r.get("matched"), int) and r.get("matched") >= r.get("count"),
          (r.get("matched"), r.get("count")))
    if r.get("matched", 0) > r.get("count", 0):
        check("T783 truncated is set when there is more than what was returned",
              r.get("truncated") is True, r)
        check("T783 and the note says so in a way a caller can act on",
              "limit" in (r.get("note") or "").lower() or "narrow" in (r.get("note") or "").lower(),
              r.get("note"))

    print("")
    print("=== T784: no match is a real, explained answer - not silence or a crash ===")
    r = server.find_tools("zzz_definitely_not_a_real_keyword_zzz")
    check("T784 it still succeeds", r.get("ok") is True, json.dumps(r)[:200])
    check("T784 with zero results", r.get("count") == 0 and r.get("results") == [], r)
    check("T784 and a note explaining why rather than an empty body",
          bool(r.get("note")), r)

    print("")
    print("=== T785: an empty keyword is refused rather than returning everything ===")
    for bad in ("", "   "):
        r = server.find_tools(bad)
        check("T785 keyword=%r is refused" % bad, r.get("ok") is False, json.dumps(r)[:150])
        check("T785 and the refusal says why", "keyword" in (r.get("error") or "").lower(),
              r.get("error"))

    print("")
    print("=== T786: find_tools does not recommend searching for itself ===")
    # Every real tool's OWN docstring inevitably mentions common words; "tool" or "find" as a keyword
    # must not surface find_tools as a match for itself - that would be a search tool one call deep in
    # its own results, which is not useful to anyone reading them.
    r = server.find_tools("find_tools")
    names = [x["name"] for x in r.get("results") or []]
    check("T786 find_tools excludes itself from its own results", "find_tools" not in names, names)

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
