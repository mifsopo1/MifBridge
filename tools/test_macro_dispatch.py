"""Regression suite for the two reports: macro round-tripping, and external dispatcher binding.

REPORT 1 (retracted, corrected). A user hit "Switch Has Authority", guessed at add_macro_instance,
was refused, and concluded the node needed a dedicated K2Node endpoint. It is a K2Node_MacroInstance.
The real gap was that a read-back could not tell you what to pass back in, and a miss listed no
candidates. T31-T34 pin down the round trip.

REPORT 2. add_bind_dispatcher "exposes the dispatcher name but no target class". The C++ endpoint has
always accepted targetClass - the MCP tool never sent it. T35 proves the endpoint half works against
a real external class; the tool half is verified by tools/param_reach.py.
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


wait()
ROOT = "/Game/_MifMacroTest/BP_MacroTest_%d" % int(time.time() % 100000)
bp = post("create_blueprint", path=ROOT, parentClass="Actor")
BPID, GRAPH = bp.get("blueprintId"), bp.get("eventGraphId")
if not GRAPH:
    print("setup failed:", json.dumps(bp)[:300])
    sys.exit(3)
print("bp:", BPID)

# ---------------------------------------------------------------- T31 the reported spellings
print("\n=== T31: the reported call now points at the right library instead of just failing ===")
ACTOR_MACROS = "/Engine/EditorBlueprintResources/ActorMacros.ActorMacros"

r_wronglib = post("add_macro_instance", graphId=GRAPH, macroGraph="Switch Has Authority", x=200, y=100)
print("  default library ->", json.dumps(r_wronglib)[:300])
check("T31 refused (it really is not in StandardMacros)", r_wronglib.get("ok") is False)
check("T31 found it in another library",
      isinstance(r_wronglib.get("foundInOtherLibrary"), list)
      and len(r_wronglib.get("foundInOtherLibrary")) > 0, json.dumps(r_wronglib)[:400])
# Switch Has Authority exists in BOTH ActorMacros and ActorComponentMacros, and they are not
# interchangeable, so the error must name every match rather than an arbitrary first one.
_err = r_wronglib.get("error") or ""
check("T31 error names a macroPath to retry with", "Macros" in _err, _err[:300])
check("T31 error lists BOTH matching libraries",
      "ActorMacros" in _err and "ActorComponentMacros" in _err, _err[:400])

print("  --- retry with the macroPath the error handed back ---")
hits = r_wronglib.get("foundInOtherLibrary") or [{}]
r_right = post("add_macro_instance", graphId=GRAPH,
               macroGraph=hits[0].get("macroGraph"), macroPath=hits[0].get("macroPath"),
               x=200, y=300)
print("  ->", json.dumps(r_right)[:220])
check("T31b resolves with the suggested path", r_right.get("ok") is True, json.dumps(r_right)[:300])

MACRO = r_right.get("nodeGuid")

# ---------------------------------------------------------------- T32 identity on read-back
print("\n=== T32: reading a macro instance back tells you how to recreate it ===")
if MACRO:
    nd = post("get_node", nodeGuid=MACRO).get("node", {})
    macro = nd.get("macro")
    print("  class:", nd.get("class"))
    print("  macro:", json.dumps(macro)[:400])
    check("T32 class is K2Node_MacroInstance", nd.get("class") == "K2Node_MacroInstance", str(nd.get("class")))
    check("T32 macro block present", isinstance(macro, dict), json.dumps(nd)[:300])
    if isinstance(macro, dict):
        check("T32 reports graphName", bool(macro.get("graphName")), json.dumps(macro)[:200])
        check("T32 reports library", bool(macro.get("library")), json.dumps(macro)[:200])
        args = macro.get("addMacroInstanceArgs") or {}
        check("T32 reports addMacroInstanceArgs", bool(args.get("macroGraph")), json.dumps(macro)[:250])

        # ---------------------------------------------------------- T33 the round trip
        print("\n=== T33 [KEY]: feeding those args straight back in recreates the node ===")
        rt = post("add_macro_instance", graphId=GRAPH,
                  macroGraph=args.get("macroGraph"), macroPath=args.get("macroPath"),
                  x=600, y=100)
        print("  round trip ->", json.dumps(rt)[:250])
        check("T33 round trip succeeds", rt.get("ok") is True, json.dumps(rt)[:300])
        if rt.get("ok"):
            nd2 = post("get_node", nodeGuid=rt.get("nodeGuid")).get("node", {})
            m2 = nd2.get("macro") or {}
            check("T33 recreated the SAME macro",
                  m2.get("graphName") == macro.get("graphName"),
                  "orig=%s new=%s" % (macro.get("graphName"), m2.get("graphName")))
else:
    print("  SKIPPED - no macro node created")

# ---------------------------------------------------------------- T34 helpful failure
print("\n=== T34: a miss lists candidates instead of just saying no ===")
r = post("add_macro_instance", graphId=GRAPH, macroGraph="NoSuchMacroXYZ", x=900, y=100)
blob = json.dumps(r)
print("  ", blob[:400])
check("T34 refused", r.get("ok") is False)
check("T34 lists available graphs", isinstance(r.get("availableMacroGraphs"), list)
      and len(r.get("availableMacroGraphs")) > 0, blob[:300])
check("T34 explains display vs graph name", "hint" in r, blob[:300])

print()
print('=== T34c: library discovery comes from the REGISTRY, not a hardcoded list ===')
# The first version hardcoded the three /Engine/EditorBlueprintResources libraries and could never
# have found ArtTools/RenderToTexture/Macros/RenderToTextureMacros, which a later engine-wide search
# turned up. Ask for one of ITS macros from the wrong library and see if the search reaches it.
r = post('add_macro_instance', graphId=GRAPH, macroGraph='Array to HLSL Int Array', x=1500, y=100)
print('  ', json.dumps(r)[:360])
check('T34c searched other libraries', (r.get('otherLibrariesSearched') or 0) > 0, json.dumps(r)[:250])
found = r.get('foundInOtherLibrary') or []
check('T34c reached a library outside EditorBlueprintResources',
      any('RenderToTexture' in (h.get('macroPath') or '') for h in found),
      json.dumps(found)[:300])

print("\n=== T34b: case/spacing variants resolve within the right library ===")
r = post("add_macro_instance", graphId=GRAPH, macroGraph="switchhasauthority",
         macroPath=ACTOR_MACROS, x=900, y=300)
print("  'switchhasauthority' in ActorMacros ->", json.dumps(r)[:240])
check("T34b normalized match works", r.get("ok") is True, json.dumps(r)[:300])
check("T34b reports it was not an exact match",
      "normalized" in (r.get("matchedBy") or ""), str(r.get("matchedBy")))

# ---------------------------------------------------------------- T35 external dispatcher
print("\n=== T35 [REPORT 2]: bind a dispatcher declared on an EXTERNAL class ===")
# Find a class with a multicast delegate. Actor has OnDestroyed / OnTakeAnyDamage.
r = post("add_bind_dispatcher", graphId=GRAPH, dispatcher="OnDestroyed",
         targetClass="Actor", x=1200, y=100)
print("  external bind ->", json.dumps(r)[:300])
check("T35 external bind succeeds", r.get("ok") is True, json.dumps(r)[:400])
BIND = r.get("nodeGuid")
if BIND:
    nd = post("get_node", nodeGuid=BIND).get("node", {})
    pins = [p.get("name") for p in nd.get("pins", [])]
    print("  bind node pins:", pins)
    check("T35 node has a Target pin for the object",
          any(p.lower() in ("self", "target") for p in pins), str(pins))
    check("T35 node has a Delegate pin for the handler",
          any("delegate" in (p or "").lower() for p in pins), str(pins))

print("\n=== T36: self-declared dispatcher still works (no regression) ===")
post("add_event_dispatcher", blueprintId=BPID, name="MyOwnDispatcher",
     inputs=[{"name": "Amount", "type": "int"}])
r = post("add_bind_dispatcher", graphId=GRAPH, dispatcher="MyOwnDispatcher", x=1200, y=400)
check("T36 self dispatcher binds", r.get("ok") is True, json.dumps(r)[:300])

print("\n=== T37: compiles clean ===")
c = post("compile", blueprintId=BPID)
check("T37 compiles", c.get("ok") is True and c.get("numErrors", 1) == 0,
      "errors=%s %s" % (c.get("numErrors"), json.dumps(c.get("messages", []))[:400]))

post("delete_asset", path=ROOT, confirm=True)
print("\n" + "=" * 70)
print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: %s\n          %s" % f)
print("=" * 70)
sys.exit(1 if FAIL else 0)
