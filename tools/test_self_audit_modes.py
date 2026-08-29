"""self_audit's response-size controls: summaryOnly plus the two independent overrides.

Found via tools/param_reach.py, not coverage_gaps.py - a different question (can the MCP tools SEND
every parameter a C++ endpoint accepts, not just whether the endpoint name is covered somewhere).
includeEndpointDetails and includeEndpoints were both accepted by H_self_audit (MifBridgeCommon.cpp) but
no MCP tool ever sent either - the tool only exposed summaryOnly, a single binary toggle, when the C++
side actually offers two INDEPENDENT overrides on top of it (each defaults to `not summaryOnly` but can
be set on its own). Andre never saw the middle ground - a compact health response that still carries the
flat endpoint-name list, without the heavy per-endpoint detail rows that make the full response run into
the tens of KB (the exact size problem summaryOnly was originally built to solve).

T1720: the two independent overrides genuinely produce a response DIFFERENT from either pure summaryOnly
or the pure full response - summaryOnly=True with includeEndpoints=True carries the endpoint name list
but not the per-endpoint detail rows.

T1721: the reverse combination - full detail rows without the flat name list - also works.

T1722: with neither override set, summaryOnly's own default behavior (both follow it) is unchanged from
before this fix - a regression check, since self_audit is used as a basic sanity/setup call all over this
test suite and must keep behaving the same way when called with no arguments or with plain summaryOnly.
"""
import json
import sys

import mifaudit as M


PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    print("\n=== T1720: summaryOnly + includeEndpoints - compact health, but WITH the name list ===")
    r = M.call("self_audit", {"summaryOnly": True, "includeEndpoints": True})
    check("T1720 succeeds", r.get("ok") is True, json.dumps(r)[:200])
    check("T1720 it reports the health/summary fields (compact mode's own point)",
          "healthy" in r and "surfaceSignature" in r, list(r.keys()))
    check("T1720 it carries the flat endpoint name list, overriding the compact default",
          isinstance(r.get("endpoints"), list) and len(r.get("endpoints")) > 100,
          "endpoints field: %s" % (type(r.get("endpoints")), ))
    check("T1720 but NOT the heavy per-endpoint detail rows - that is what makes this genuinely a "
          "middle ground, not just full-response-with-extra-steps",
          "endpointDetails" not in r or not r.get("endpointDetails"), list(r.keys()))

    print("\n=== T1721: summaryOnly + includeEndpointDetails - the reverse combination ===")
    r2 = M.call("self_audit", {"summaryOnly": True, "includeEndpointDetails": True})
    check("T1721 succeeds", r2.get("ok") is True, json.dumps(r2)[:200])
    check("T1721 it reports per-endpoint detail rows despite summaryOnly",
          bool(r2.get("endpointDetails")), list(r2.keys()))

    print("\n=== T1722: plain summaryOnly (no overrides) is unchanged - a regression check ===")
    full = M.call("self_audit", {})
    check("T1722 (setup) the plain default call succeeds", full.get("ok") is True, json.dumps(full)[:150])
    check("T1722 the default (no summaryOnly) still carries the flat endpoint list",
          isinstance(full.get("endpoints"), list) and len(full.get("endpoints")) > 100,
          type(full.get("endpoints")))

    compact = M.call("self_audit", {"summaryOnly": True})
    check("T1722 plain summaryOnly=true still succeeds", compact.get("ok") is True, json.dumps(compact)[:150])
    check("T1722 and still omits the endpoint list by default, same as before this fix",
          not compact.get("endpoints"), compact.get("endpoints"))

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
