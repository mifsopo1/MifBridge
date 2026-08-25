"""Regression suite for the pin-lifetime audit.

Four sites held a UEdGraphPin* across a call that can free pins - the add_pin crash class, which the
engine itself warns about in UEdGraphSchema_K2::BreakPinLinks ("can trigger a node reconstruction
invalidating the TargetPin reference"). They now capture node-guid + name + direction first and
re-resolve.

The crash needs a reconstruct to coincide with a break, which is not reliably forceable from the
outside, so these tests do NOT claim to reproduce it. What they DO establish is that the rewrite did
not break the working behaviour of the paths it touched - which is the real regression risk of
swapping raw pointers for re-resolution:

    remove_pin      userDefined branch (function output mirrored across sibling Return nodes)
                    userDefined branch (custom event parameter)
    splice_into_exec SpliceExecAfter - the downstream targets must end up on the inserted node
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


def node_of(g):
    r = post("get_node", nodeGuid=g)
    return r.get("node") if r.get("ok") else None


def pin_names(g):
    nd = node_of(g)
    return None if not nd else sorted(p.get("name", "") for p in nd.get("pins", []))


def links(g, pin):
    nd = node_of(g) or {}
    for p in nd.get("pins", []):
        if p.get("name", "").lower() == pin.lower():
            return sorted((l.get("node"), l.get("pin")) for l in (p.get("linkedTo") or []))
    return None


wait()
ROOT = "/Game/_MifPinLife/BP_PinLife_%d" % int(time.time() % 100000)
bp = post("create_blueprint", path=ROOT, parentClass="Actor")
BPID, GRAPH = bp.get("blueprintId"), bp.get("eventGraphId")
if not GRAPH:
    print("setup failed:", json.dumps(bp)[:300])
    sys.exit(3)
print("bp:", BPID)

# ---------------------------------------------------------------- T27 custom event param removal
print("\n=== T27: remove_pin on a custom event parameter (userDefined branch) ===")
ev = post("add_custom_event", graphId=GRAPH, name="EvtPinLife", x=200, y=100,
          inputs=[{"name": "Alpha", "type": "int"}, {"name": "Beta", "type": "string"}])
EV = ev.get("nodeGuid")
print("  event:", EV, "pins:", pin_names(EV))
before = pin_names(EV)
check("T27 params created", before is not None and "Alpha" in before and "Beta" in before, str(before))

# wire Beta into something so BreakPinLinks actually has links to break - that is the code path
pr = post("add_function_call", graphId=GRAPH, function="PrintString",
          **{"class": "KismetSystemLibrary"}, x=700, y=100)
PR = pr.get("nodeGuid")
post("connect_pins", srcNode=EV, srcPin="Beta", dstNode=PR, dstPin="InString")
print("  Beta linked to:", links(EV, "Beta"))
check("T27 Beta wired", len(links(EV, "Beta") or []) == 1, str(links(EV, "Beta")))

r = post("remove_pin", nodeGuid=EV, pin="Beta", confirm=True)
after = pin_names(EV)
print("  remove_pin:", json.dumps(r)[:250])
print("  pins after:", after)
check("T27 remove_pin ok", r.get("ok") is True, json.dumps(r)[:250])
check("T27 Beta gone", after is not None and "Beta" not in after, str(after))
check("T27 Alpha kept", after is not None and "Alpha" in after, str(after))
check("T27 consumer link cleared", links(PR, "InString") == [], str(links(PR, "InString")))

# ---------------------------------------------------------------- T28 function output + siblings
print("\n=== T28: remove_pin on a function output mirrored across Return nodes ===")
fn = post("create_function", blueprintId=BPID, name="FnPinLife")
FG = fn.get("graphId")
print("  function graph:", FG, json.dumps(fn)[:200])
if FG:
    a = post("add_pin", graphId=FG, name="OutA", type="int", direction="output")
    b = post("add_pin", graphId=FG, name="OutB", type="int", direction="output")
    print("  add_pin OutA:", a.get("ok"), " OutB:", b.get("ok"))
    ln = post("list_nodes", graphId=FG)
    RET = [n.get("guid") for n in ln.get("nodes", []) if "Result" in (n.get("class") or "")]
    print("  return nodes:", RET)
    if RET:
        pre = pin_names(RET[0])
        print("  return pins before:", pre)
        r = post("remove_pin", nodeGuid=RET[0], pin="OutB", confirm=True)
        post_pins = pin_names(RET[0])
        print("  remove_pin:", json.dumps(r)[:250])
        print("  return pins after :", post_pins)
        check("T28 remove ok", r.get("ok") is True, json.dumps(r)[:250])
        check("T28 OutB gone", post_pins is not None and "OutB" not in post_pins, str(post_pins))
        check("T28 OutA kept", post_pins is not None and "OutA" in post_pins, str(post_pins))
    else:
        print("  SKIPPED - no Return node found")
else:
    print("  SKIPPED - could not create function:", json.dumps(fn)[:200])

# ---------------------------------------------------------------- T29 splice preserves the chain
print("\n=== T29: splice_into_exec (SpliceExecAfter) rewires downstream correctly ===")
ln = post("list_nodes", graphId=GRAPH)
EVENT = None
for nd in ln.get("nodes", []):
    if "BeginPlay" in (nd.get("title") or ""):
        EVENT = nd.get("guid")
A = post("add_function_call", graphId=GRAPH, function="PrintString",
         **{"class": "KismetSystemLibrary"}, x=500, y=600).get("nodeGuid")
MID = post("add_function_call", graphId=GRAPH, function="PrintString",
           **{"class": "KismetSystemLibrary"}, x=900, y=600).get("nodeGuid")
print("  event=%s A=%s MID=%s" % (EVENT, A, MID))
if EVENT and A and MID:
    post("connect_pins", srcNode=EVENT, srcPin="then", dstNode=A, dstPin="execute")
    before_chain = links(EVENT, "then")
    print("  BeginPlay.then before:", before_chain)
    r = post("splice_into_exec", afterNode=EVENT, insertNode=MID,
             afterPin="then", insertExecIn="execute", insertExecOut="then")
    print("  splice:", json.dumps(r)[:300])
    ev_then = links(EVENT, "then")
    mid_then = links(MID, "then")
    print("  BeginPlay.then after :", ev_then)
    print("  MID.then after       :", mid_then)
    check("T29 splice ok", r.get("ok") is True, json.dumps(r)[:250])
    check("T29 BeginPlay now feeds MID", ev_then == [(MID, "execute")], str(ev_then))
    check("T29 MID now feeds the old target", mid_then == [(A, "execute")], str(mid_then))
else:
    print("  SKIPPED - could not build exec chain")

print("\n=== T30: compiles clean after all of it ===")
c = post("compile", blueprintId=BPID)
check("T30 compiles", c.get("ok") is True and c.get("numErrors", 1) == 0,
      "errors=%s %s" % (c.get("numErrors"), json.dumps(c.get("messages", []))[:400]))

post("delete_asset", path=ROOT, confirm=True)
print("\n" + "=" * 70)
print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: %s\n          %s" % f)
print("=" * 70)
sys.exit(1 if FAIL else 0)
