"""Regression suite for the reported self-pin double-link.

The reported case: a 12-connect rewire returned 12/12 OK while 8 destinations kept BOTH the old and
the new source. Root cause is deliberate engine behaviour - EdGraphSchema_K2.cpp:2112
bMultipleSelfException lets a `self` pin on an impure, no-return, non-latent function accept multiple
sources - so whether a rewire replaced or appended depended on the CALLEE'S SIGNATURE:

    SetActorHiddenInGame  impure, no return  -> multi-target self -> engine APPENDS
    K2_GetActorLocation   pure,   has return -> ordinary input    -> engine REPLACES

Two nodes, identically-named `self` pins, opposite outcomes, both reported ok. These tests pin down
the new contract: the outcome now follows existingLinkPolicy, not the callee.
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
    print("waiting for bridge...")
    for _ in range(180):
        r = post("self_audit", summaryOnly=True)
        if r.get("ok"):
            print("bridge up: %d endpoints, built %s %s"
                  % (r.get("endpointCount", -1), r.get("buildDate"), r.get("buildTime")))
            return
        time.sleep(5)
    print("bridge never came up")
    sys.exit(1)


def sources(g, pin):
    r = post("get_node", nodeGuid=g)
    nd = r.get("node") if r.get("ok") else None
    if not nd:
        return None
    for p in nd.get("pins", []):
        if p.get("name", "").lower() == pin.lower():
            return sorted((l.get("node"), l.get("pin")) for l in (p.get("linkedTo") or []))
    return None


wait()
# Run-unique root. delete_asset cannot remove a Blueprint that is still LOADED, so reusing a path
# across runs fails at create_blueprint with "already exists" and the suite dies at setup instead of
# testing anything. A per-run suffix sidesteps that entirely.
BP_ROOT = "/Game/_MifSelfPinTest/BP_SelfPin_%d" % int(time.time() % 100000)
_seq = [0]
MADE = []


def build():
    """Fresh asset PATH per test. Reusing one path fails: delete_asset cannot remove a Blueprint
    that is still loaded/referenced, so the next create_blueprint hits 'already exists' and the
    test collapses at setup rather than testing anything."""
    _seq[0] += 1
    bp_path = "%s_%d" % (BP_ROOT, _seq[0])
    MADE.append(bp_path)
    post("delete_asset", path=bp_path, confirm=True)
    bp = post("create_blueprint", path=bp_path, parentClass="Actor")
    bpid, graph = bp.get("blueprintId"), bp.get("eventGraphId")
    if not graph:
        print("setup failed:", json.dumps(bp)[:300])
        sys.exit(3)
    post("add_variable", blueprintId=bpid, name="OldRef", type="Actor")
    post("add_variable", blueprintId=bpid, name="NewRef", type="Actor")
    old = post("add_variable_get", graphId=graph, variable="OldRef", x=100, y=100).get("nodeGuid")
    new = post("add_variable_get", graphId=graph, variable="NewRef", x=100, y=400).get("nodeGuid")
    hid = post("add_function_call", graphId=graph, function="SetActorHiddenInGame",
               **{"class": "Actor"}, x=600, y=100).get("nodeGuid")
    loc = post("add_function_call", graphId=graph, function="K2_GetActorLocation",
               **{"class": "Actor"}, x=600, y=400).get("nodeGuid")
    for t in (hid, loc):
        post("connect_pins", srcNode=old, srcPin="OldRef", dstNode=t, dstPin="self")
    return bpid, graph, old, new, hid, loc


# ---------------------------------------------------------------- T21 default replace
print("\n=== T21: default policy REPLACES on both, regardless of callee signature ===")
BPID, GRAPH, OLD, NEW, HID, LOC = build()
before_hid, before_loc = sources(HID, "self"), sources(LOC, "self")
print("  before: HID=%s LOC=%s" % (before_hid, before_loc))
r = post("apply_graph_patch", graphId=GRAPH, operations=[
    {"op": "connect_pins", "srcNode": NEW, "srcPin": "NewRef", "dstNode": HID, "dstPin": "self"},
    {"op": "connect_pins", "srcNode": NEW, "srcPin": "NewRef", "dstNode": LOC, "dstPin": "self"},
])
a_hid, a_loc = sources(HID, "self"), sources(LOC, "self")
print("  after : HID=%s LOC=%s" % (a_hid, a_loc))
print("  results:", json.dumps(r.get("results", []))[:700])
check("T21 patch ok", r.get("ok") is True, json.dumps(r)[:250])
check("T21 impure/no-return self has ONE source", len(a_hid or []) == 1,
      "got %d: %s  <-- this is the reported bug" % (len(a_hid or []), a_hid))
check("T21 pure/has-return self has ONE source", len(a_loc or []) == 1, str(a_loc))
check("T21 both point at NewRef", a_hid == [(NEW, "NewRef")] and a_loc == [(NEW, "NewRef")],
      "hid=%s loc=%s" % (a_hid, a_loc))
rows = r.get("results", [])
check("T21 rows report replacedExisting", all(x.get("replacedExisting") is True for x in rows),
      json.dumps(rows)[:400])
check("T21 rows carry sourcesBefore/After",
      all("sourcesBefore" in x and "sourcesAfter" in x for x in rows), json.dumps(rows)[:300])

# ---------------------------------------------------------------- T22 preserve
print("\n=== T22: preserve keeps the incumbent AND says so ===")
BPID, GRAPH, OLD, NEW, HID, LOC = build()
r = post("apply_graph_patch", graphId=GRAPH, operations=[
    {"op": "connect_pins", "srcNode": NEW, "srcPin": "NewRef", "dstNode": HID, "dstPin": "self",
     "existingLinkPolicy": "preserve"},
])
a_hid = sources(HID, "self")
row = (r.get("results") or [{}])[0]
print("  HID sources:", a_hid)
print("  row:", json.dumps(row)[:400])
check("T22 kept both sources", len(a_hid or []) == 2, str(a_hid))
check("T22 flagged appendedToExisting", row.get("appendedToExisting") is True, json.dumps(row)[:300])
check("T22 detail says APPENDED", "APPENDED" in json.dumps(row), json.dumps(row)[:300])

# ---------------------------------------------------------------- T23 reject
print("\n=== T23: reject refuses and names the incumbent, touching nothing ===")
BPID, GRAPH, OLD, NEW, HID, LOC = build()
before = sources(HID, "self")
r = post("apply_graph_patch", graphId=GRAPH, operations=[
    {"op": "connect_pins", "srcNode": NEW, "srcPin": "NewRef", "dstNode": HID, "dstPin": "self",
     "existingLinkPolicy": "reject"},
])
check("T23 refused", r.get("ok") is False, json.dumps(r)[:250])
check("T23 names the existing source", "already fed by" in json.dumps(r), json.dumps(r)[:400])
check("T23 graph untouched", sources(HID, "self") == before,
      "before=%s after=%s" % (before, sources(HID, "self")))

# ---------------------------------------------------------------- T24 dry run warns
print("\n=== T24: dryRun warns that the destination is occupied ===")
BPID, GRAPH, OLD, NEW, HID, LOC = build()
d = post("apply_graph_patch", graphId=GRAPH, dryRun=True, operations=[
    {"op": "connect_pins", "srcNode": NEW, "srcPin": "NewRef", "dstNode": HID, "dstPin": "self"},
])
blob = json.dumps(d)
print("  ", blob[:500])
check("T24 dryRun ok", d.get("ok") is True)
check("T24 warns ALREADY fed", "ALREADY fed by" in blob, blob[:300])
check("T24 states the action", "REMOVE the existing" in blob, blob[:300])

# ---------------------------------------------------------------- T25 exec fan-in preserved
print("\n=== T25 [GUARD]: exec fan-in is NOT torn down by the replace policy ===")
BPID, GRAPH, OLD, NEW, HID, LOC = build()
ln = post("list_nodes", graphId=GRAPH)
EVENT = None
for nd in ln.get("nodes", []):
    if "BeginPlay" in (nd.get("title") or ""):
        EVENT = nd.get("guid")
p1 = post("add_function_call", graphId=GRAPH, function="PrintString",
          **{"class": "KismetSystemLibrary"}, x=1200, y=100).get("nodeGuid")
p2 = post("add_function_call", graphId=GRAPH, function="PrintString",
          **{"class": "KismetSystemLibrary"}, x=1200, y=300).get("nodeGuid")
sink = post("add_function_call", graphId=GRAPH, function="PrintString",
            **{"class": "KismetSystemLibrary"}, x=1600, y=200).get("nodeGuid")
if p1 and p2 and sink:
    post("connect_pins", srcNode=p1, srcPin="then", dstNode=sink, dstPin="execute")
    before_fan = sources(sink, "execute")
    r = post("apply_graph_patch", graphId=GRAPH, operations=[
        {"op": "connect_pins", "srcNode": p2, "srcPin": "then", "dstNode": sink, "dstPin": "execute"},
    ])
    after_fan = sources(sink, "execute")
    print("  exec input before:", before_fan, "\n  exec input after :", after_fan)
    check("T25 exec fan-in kept BOTH sources", len(after_fan or []) == 2,
          "replace policy must not break exec fan-in; got %s" % (after_fan,))
    check("T25 patch ok", r.get("ok") is True, json.dumps(r)[:200])
else:
    print("  SKIPPED - could not build fan-in")

print("\n=== T26: compiles clean ===")
c = post("compile", blueprintId=BPID)
check("T26 compiles", c.get("ok") is True and c.get("numErrors", 1) == 0,
      "errors=%s %s" % (c.get("numErrors"), json.dumps(c.get("messages", []))[:300]))

for _p in MADE:
    post("delete_asset", path=_p, confirm=True)
print("\n" + "=" * 70)
print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: %s\n          %s" % f)
print("=" * 70)
sys.exit(1 if FAIL else 0)
