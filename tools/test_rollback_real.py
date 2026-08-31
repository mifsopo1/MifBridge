"""T7/T8 redone so they actually exercise the rollback.

The earlier versions used a nonexistent pin as the "failing op". That fails at PREFLIGHT, so the
patch was refused before anything was applied - and the assertions "the displaced link came back"
and "the wiped default came back" were trivially true over a graph that was never touched. They
proved nothing.

A real mid-apply failure needs an op that is LEGAL at preflight and ILLEGAL by the time it runs.
The tripwire below is that op:

    tripwire_1  IntGetter -> Array_Add.NewItem    resolves the wildcard pin to int
    tripwire_2  StrGetter -> Array_Add.NewItem    preflight says yes (wildcard accepts anything);
                                                  apply says no (it is an int now)

Appending those two to any patch turns it into a patch that applies its real ops, then fails, then
must roll everything back. Now "did the displaced link come back" is a question with teeth.
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8791/api"
PASS, FAIL = [], []


def post(_ep, **payload):
    body = json.dumps({k: v for k, v in payload.items() if v is not None}).encode("utf-8")
    req = urllib.request.Request(BASE + "/" + _ep, data=body,
                                 headers={"X-Mif-Token": "dev", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error": "HTTP %s" % e.code}
    except Exception as e:
        return {"ok": False, "error": "unreachable: %s" % e}


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def wait():
    for _ in range(150):
        if post("self_audit", summaryOnly=True).get("ok"):
            return
        time.sleep(5)
    sys.exit(1)


def node_of(g):
    r = post("get_node", nodeGuid=g)
    return r.get("node") if r.get("ok") else None


def pin_of(g, name):
    for p in (node_of(g) or {}).get("pins", []):
        if p.get("name", "").lower() == name.lower():
            return p
    return None


def links(g, name):
    p = pin_of(g, name)
    return None if p is None else sorted((l.get("node"), l.get("pin"))
                                         for l in (p.get("linkedTo") or []))


def default_of(g, name):
    p = pin_of(g, name)
    return None if p is None else p.get("default", "")


wait()
# Per-RUN unique, like the other 34 suites. A fixed scratch path passes the first time and
# fails every time after, because the asset is still in memory until the editor restarts -
# so this suite was green all night and then failed the moment the full run happened twice
# in one editor session. A test whose result depends on how recently the editor started is
# not a test.
BP = "/Game/_MifRollbackReal/BP_RollbackReal_%d" % int(time.time() % 100000)
post("delete_asset", path=BP, confirm=True)
bp = post("create_blueprint", path=BP, parentClass="Actor")
BPID, GRAPH = bp.get("blueprintId"), bp.get("eventGraphId")
if not GRAPH:
    print("setup failed:", bp)
    sys.exit(3)

EVENT = None
for nd in post("list_nodes", graphId=GRAPH).get("nodes", []):
    if "BeginPlay" in (nd.get("title") or ""):
        EVENT = nd.get("guid")
P = [post("add_function_call", graphId=GRAPH, function="PrintString",
          **{"class": "KismetSystemLibrary"}, x=500 + 300 * i, y=0).get("nodeGuid")
     for i in range(3)]

post("add_variable", blueprintId=BPID, name="TripInt", type="int")
post("add_variable", blueprintId=BPID, name="TripStr", type="string")
IG = post("add_variable_get", graphId=GRAPH, variable="TripInt", x=100, y=600).get("nodeGuid")
SG = post("add_variable_get", graphId=GRAPH, variable="TripStr", x=100, y=800).get("nodeGuid")
AA = post("add_function_call", graphId=GRAPH, function="Array_Add",
          **{"class": "KismetArrayLibrary"}, x=600, y=700).get("nodeGuid")
print("event:%s  prints:%s\ntripwire int:%s str:%s Array_Add:%s" % (EVENT, P, IG, SG, AA))
if not (EVENT and all(P) and IG and SG and AA):
    print("setup failed")
    sys.exit(3)

TRIP = [
    {"op": "connect_pins", "srcNode": IG, "srcPin": "TripInt", "dstNode": AA, "dstPin": "NewItem"},
    {"op": "connect_pins", "srcNode": SG, "srcPin": "TripStr", "dstNode": AA, "dstPin": "NewItem"},
]

# Baseline wiring: EVENT.then -> P0
r = post("apply_graph_patch", graphId=GRAPH, operations=[
    {"op": "connect_pins", "srcNode": EVENT, "srcPin": "then", "dstNode": P[0], "dstPin": "execute"},
    {"op": "set_pin_default", "node": P[2], "pin": "InString", "value": "DEFAULT_TO_PRESERVE"},
])
print("baseline:", json.dumps({k: v for k, v in r.items() if k != "results"})[:200])

# --------------------------------------------------------------------------- the residue
# WHAT THE ROLLBACK COULD NOT PUT BACK. apply_graph_patch computes a clean flag from three counters
# and, when it is false, emits rollbackUnresolvedPins and rollbackLostLinks - "an INCOMPLETE rollback
# must never be reported as a clean one", in the endpoint's own words. Nothing asserted any of it:
# a scan for consequence-reporting response fields that no suite names found both residue fields on
# 2026-08-31, and this is the only suite that reaches a real mid-apply rollback at all.
#
# Two assertions per rollback, and the second is the one with teeth:
#   rollbackComplete is TRUE          - strictly true, not "not False". test_graph_patch asserts
#                                       `is not False`, which passes when the field is ABSENT, so it
#                                       would survive the field being dropped entirely.
#   the residue fields are ABSENT     - they are emitted ONLY when the rollback was incomplete, so
#                                       their presence here would mean the graph was left damaged
#                                       while the patch reported a tidy failure.
def check_rollback_was_clean(tag, resp):
    check("%s rollbackComplete is TRUE, not merely 'not false' - an absent field must not pass"
          % tag,
          resp.get("rollbackComplete") is True,
          "rollbackComplete=%r (present=%s)"
          % (resp.get("rollbackComplete"), "rollbackComplete" in resp))
    residue = {k: resp[k] for k in ("rollbackUnresolvedPins", "rollbackLostLinks")
               if k in resp}
    check("%s reports NO rollback residue - those fields appear only when the rollback left the "
          "graph damaged, and a tidy-looking failure over a damaged graph is the worst outcome "
          "this endpoint has" % tag,
          not residue, "residue reported: %s" % residue)


print("\n=== T7-real: rollback restores a link that connect SILENTLY DISPLACED ===")
orig = links(EVENT, "then")
print("  EVENT.then before:", orig)
r = post("apply_graph_patch", graphId=GRAPH, allowPartial=False, stopOnFirstError=True, operations=[
    # This displaces EVENT.then -> P0, because an exec output is single-link.
    {"op": "connect_pins", "srcNode": EVENT, "srcPin": "then", "dstNode": P[1], "dstPin": "execute"},
] + TRIP)
after = links(EVENT, "then")
print("  EVENT.then after :", after)
print("  resp:", json.dumps({k: v for k, v in r.items() if k != "results"})[:400])
check("T7-real reached APPLY not preflight", r.get("preflightErrors", 0) == 0,
      "preflightErrors=%s" % r.get("preflightErrors"))
check("T7-real patch failed", r.get("ok") is False)
check("T7-real applied>0 before rollback", r.get("rolledBack", 0) >= 1,
      "rolledBack=%s - if 0, nothing was ever applied and this test is vacuous" % r.get("rolledBack"))
check("T7-real DISPLACED LINK RESTORED", after == orig,
      "before=%s after=%s" % (orig, after))
check_rollback_was_clean("T7-real", r)

print("\n=== T8-real: rollback restores a default that connecting WIPED ===")
d_before = default_of(P[2], "InString")
print("  P2.InString before:", repr(d_before))
r = post("apply_graph_patch", graphId=GRAPH, allowPartial=False, stopOnFirstError=True, operations=[
    # Connecting into this input pin makes the engine wipe its literal default.
    {"op": "connect_pins", "srcNode": SG, "srcPin": "TripStr", "dstNode": P[2], "dstPin": "InString"},
] + TRIP)
d_after = default_of(P[2], "InString")
print("  P2.InString after :", repr(d_after))
print("  resp:", json.dumps({k: v for k, v in r.items() if k != "results"})[:400])
check("T8-real reached APPLY not preflight", r.get("preflightErrors", 0) == 0,
      "preflightErrors=%s" % r.get("preflightErrors"))
check("T8-real patch failed", r.get("ok") is False)
check("T8-real applied>0 before rollback", r.get("rolledBack", 0) >= 1,
      "rolledBack=%s - if 0 this test is vacuous" % r.get("rolledBack"))
check("T8-real WIPED DEFAULT RESTORED", d_after == d_before,
      "before=%r after=%r" % (d_before, d_after))
check("T8-real link not left behind", links(P[2], "InString") == [],
      "InString still linked: %s" % links(P[2], "InString"))
check_rollback_was_clean("T8-real", r)

print("\n=== T20: still compiles after both rollbacks ===")
c = post("compile", blueprintId=BPID)
check("T20 compiles clean", c.get("ok") is True and c.get("numErrors", 1) == 0,
      "errors=%s %s" % (c.get("numErrors"), json.dumps(c.get("messages", []))[:300]))

print("\n=== cleanup ===")
print(json.dumps(post("delete_asset", path=BP, confirm=True))[:160])
print("\n" + "=" * 70)
print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: %s\n          %s" % f)
print("=" * 70)
sys.exit(1 if FAIL else 0)
