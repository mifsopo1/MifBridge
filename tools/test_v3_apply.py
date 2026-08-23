"""Apply-time failure tests.

The previous run's tests all failed at PREFLIGHT (a nonexistent pin is caught before anything is
mutated), so they never reached the apply loop and never triggered a rollback - which made the
wildcard test pass vacuously over a graph that was never touched.

This exercises the one situation that genuinely passes preflight and fails during apply:

  op1  IntGetter    -> Array_Add.NewItem     legal; RESOLVES the wildcard pin to int
  op2  StringGetter -> Array_Add.NewItem     legal AT PREFLIGHT, because
                                             UEdGraphSchema_K2::ArePinTypesCompatible returns true
                                             unconditionally when either side is PC_Wildcard - and
                                             illegal by the time it runs, because op1 made it an int.

That is a real mid-apply failure on a node that RETYPES ITSELF, so it drives:
  - the apply-time invertibility/legality re-check,
  - the rollback of an already-applied connect,
  - the node-shape verification (did NewItem go back to wildcard?),
  - results[] restamping, and the skipped count.
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
        r = post("self_audit", summaryOnly=True)
        if r.get("ok"):
            print("bridge up: %d endpoints" % r.get("endpointCount", -1))
            return
        time.sleep(5)
    sys.exit(1)


def node_of(g):
    r = post("get_node", nodeGuid=g)
    return r.get("node") if r.get("ok") else None


def pin_type(g, name):
    nd = node_of(g) or {}
    for p in nd.get("pins", []):
        if p.get("name") == name:
            return json.dumps(p.get("type", {}), sort_keys=True)
    return None


def links(g, name):
    nd = node_of(g) or {}
    for p in nd.get("pins", []):
        if p.get("name") == name:
            return sorted((l.get("node"), l.get("pin")) for l in (p.get("linkedTo") or []))
    return None


wait()
BP = "/Game/_MifPatchApply/BP_ApplyTest"
post("delete_asset", path=BP, confirm=True)
bp = post("create_blueprint", path=BP, parentClass="Actor")
BPID, GRAPH = bp.get("blueprintId"), bp.get("eventGraphId")
if not GRAPH:
    print("setup failed:", bp)
    sys.exit(3)

post("add_variable", blueprintId=BPID, name="MyInt", type="int")
post("add_variable", blueprintId=BPID, name="MyStr", type="string")
IG = post("add_variable_get", graphId=GRAPH, variable="MyInt", x=100, y=200).get("nodeGuid")
SG = post("add_variable_get", graphId=GRAPH, variable="MyStr", x=100, y=400).get("nodeGuid")
AA = post("add_function_call", graphId=GRAPH, function="Array_Add",
          **{"class": "KismetArrayLibrary"}, x=600, y=300).get("nodeGuid")
print("int getter:%s  str getter:%s  Array_Add:%s" % (IG, SG, AA))
if not (IG and SG and AA):
    print("setup failed")
    sys.exit(3)

t_before = pin_type(AA, "NewItem")
l_before = links(AA, "NewItem")
print("NewItem type BEFORE: %s   links: %s" % (t_before, l_before))
if "wildcard" not in (t_before or ""):
    print("NOTE: NewItem is not wildcard; this test cannot do its job")

print("\n=== T16: preflight ACCEPTS the second connect (wildcard compatibility) ===")
dry = post("apply_graph_patch", graphId=GRAPH, dryRun=True, operations=[
    {"op": "connect_pins", "srcNode": IG, "srcPin": "MyInt", "dstNode": AA, "dstPin": "NewItem"},
    {"op": "connect_pins", "srcNode": SG, "srcPin": "MyStr", "dstNode": AA, "dstPin": "NewItem"},
])
check("T16 dryRun says both would apply", dry.get("ok") is True and dry.get("preflightErrors") == 0,
      json.dumps(dry)[:400])
print("     (this is the setup: preflight cannot see that op1 will retype the pin)")

print("\n=== T17 [KEY]: the failure happens AT APPLY, and rollback must be honest ===")
r = post("apply_graph_patch", graphId=GRAPH, allowPartial=False, stopOnFirstError=True, operations=[
    {"op": "connect_pins", "srcNode": IG, "srcPin": "MyInt", "dstNode": AA, "dstPin": "NewItem"},
    {"op": "connect_pins", "srcNode": SG, "srcPin": "MyStr", "dstNode": AA, "dstPin": "NewItem"},
    {"op": "set_pin_default", "node": AA, "pin": "NewItem", "value": "7"},
])
t_after = pin_type(AA, "NewItem")
l_after = links(AA, "NewItem")
print("  response:", json.dumps({k: v for k, v in r.items() if k != "results"})[:600])
print("  results :", json.dumps(r.get("results", []))[:500])
print("  NewItem type AFTER: %s   links: %s" % (t_after, l_after))

check("T17 reached APPLY, not preflight",
      r.get("preflightErrors", 0) == 0,
      "preflightErrors=%s - if >0 this test did not exercise the apply path"
      % r.get("preflightErrors"))
check("T17 patch failed", r.get("ok") is False, json.dumps(r)[:250])
check("T17 links restored", l_after == l_before, "before=%s after=%s" % (l_before, l_after))

restored = (t_after == t_before)
claimed_clean = r.get("rollbackComplete") is not False
check("T17 HONEST about pin type", restored or not claimed_clean,
      "type changed (%s -> %s) AND rollbackComplete=%s -> the endpoint LIED"
      % (t_before, t_after, r.get("rollbackComplete")))
if restored:
    print("     wildcard fully reverted; rollback is genuinely clean")
else:
    print("     wildcard NOT reverted, and the endpoint said so: reshaped=%s problems=%s"
          % (r.get("rollbackReshapedNodes"), json.dumps(r.get("rollbackProblems", []))[:300]))

print("\n=== T18: results[] restamped, skipped counted (apply path) ===")
rows = r.get("results", [])
oks = [x for x in rows if x.get("ok") is True]
undone = [x for x in rows if x.get("rolledBack") is True]
check("T18 no row claims ok:true after rollback", len(oks) == 0, json.dumps(rows)[:400])
check("T18 undone row flagged", len(undone) >= 1, json.dumps(rows)[:400])
check("T18 skipped counted", r.get("skipped", 0) >= 1,
      "skipped=%s (op3 should not have run)" % r.get("skipped"))

print("\n=== T19: the graph still compiles after the rollback ===")
c = post("compile", blueprintId=BPID)
check("T19 compiles clean", c.get("ok") is True and c.get("numErrors", 1) == 0,
      "errors=%s %s" % (c.get("numErrors"), json.dumps(c.get("messages", []))[:400]))

print("\n=== cleanup ===")
print(json.dumps(post("delete_asset", path=BP, confirm=True))[:160])
print("\n" + "=" * 70)
print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: %s\n          %s" % f)
print("=" * 70)
sys.exit(1 if FAIL else 0)
