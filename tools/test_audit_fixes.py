"""Verification for the defects the audit harness found on its first pass.

Every case here was found by a tool in this folder, not by a person reading code:

  T40  fuzz_endpoints, ABSURD probe   a 64KB string killed the editor outright
                                      (FName's 1023 limit is a check(), not an error return)
  T41  fuzz_endpoints, GHOST probe    check_overlaps answered a question about a NONEXISTENT actor
                                      by auditing the whole level and reporting ok:true
  T42  fuzz_endpoints, GHOST probe    audit_unused returned unusedCount:0 for a prefix that matched
                                      nothing, which reads as "nothing is unused"
  T43  audit_postconditions           SetActorLabel is void and silently refuses a name the editor
                                      rejects, so 8 endpoints reported the requested label as fact
  T44  audit_postconditions           two more unchecked TrySetDefaultValue calls - the "banana"
                                      defect that set_pin_default was fixed for months ago
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    stamp = int(time.time() % 100000)
    root = "/Game/_MifAuditFix/BP_Fix_%d" % stamp
    bp = M.call("create_blueprint", {"path": root, "parentClass": "Actor"})
    bpid, graph = bp.get("blueprintId"), bp.get("eventGraphId")
    if not graph:
        print("setup failed:", json.dumps(bp)[:300])
        return 3

    # ------------------------------------------------------------------ T40 the killer input
    print("\n=== T40: a 64KB string is refused instead of asserting the editor to death ===")
    r = M.raw_post("list_graphs", {"blueprintId": "x" * 65536}, timeout=60)
    check("T40 refused", r.get("ok") is False, json.dumps(r)[:200])
    check("T40 says why and names the limit",
          "1023" in (r.get("error") or "") and "FName" in (r.get("error") or ""),
          (r.get("error") or "")[:200])
    alive, why = M.require_sdk_bridge(force=True)
    check("T40 EDITOR SURVIVED", alive, why)

    print("  --- nested, inside an array ---")
    r = M.raw_post("apply_graph_patch",
                   {"graphId": graph, "operations": [{"op": "connect_pins", "srcNode": "y" * 5000}]},
                   timeout=60)
    check("T40 nested case refused", r.get("ok") is False, json.dumps(r)[:160])
    check("T40 nested case names the exact path",
          "operations[0].srcNode" in (r.get("error") or ""), (r.get("error") or "")[:200])

    print("  --- a normal string still works ---")
    r = M.call("list_graphs", {"blueprintId": bpid})
    check("T40 normal payload unaffected", r.get("ok") is True, json.dumps(r)[:160])

    # ------------------------------------------------------------------ T41 check_overlaps
    print("\n=== T41: check_overlaps refuses an actor that does not exist ===")
    r = M.call("check_overlaps", {"actorPath": "/Game/_MifAudit_DoesNotExist/Nope"})
    check("T41 refused", r.get("ok") is False, json.dumps(r)[:220])
    check("T41 says nothing was tested",
          "Nothing was tested" in (r.get("error") or ""), (r.get("error") or "")[:220])
    check("T41 did NOT fall back to a whole-scene audit", "pairs" not in r, json.dumps(r)[:220])

    print("  --- omitting actorPath still audits the scene ---")
    r = M.call("check_overlaps", {})
    check("T41 whole-scene audit still works", r.get("ok") is True, json.dumps(r)[:160])

    # ------------------------------------------------------------------ T42 audit_unused
    print("\n=== T42: audit_unused distinguishes 'prefix matched nothing' from 'nothing unused' ===")
    r = M.call("audit_unused", {"pathPrefix": "/Game/_MifAudit_NoSuchFolder_XYZ"})
    check("T42 explains the empty scan", "scanNote" in r, json.dumps(r)[:240])
    check("T42 note says the prefix found nothing",
          "PREFIX FOUND" in (r.get("scanNote") or "").upper(), (r.get("scanNote") or "")[:200])

    # ------------------------------------------------------------------ T43 actor label
    print("\n=== T43: an actor label the editor refuses is reported, not echoed back ===")
    sp = M.call("spawn_actor_in_level", {"actorClass": "StaticMeshActor",
                                         "location": {"x": 0, "y": 0, "z": 5000},
                                         "label": "MifAuditLabel_%d" % stamp})
    actor_path = ((sp.get("actor") or {}).get("path")
                  or (sp.get("actor") or {}).get("objectPath") or "")
    check("T43 spawn ok", sp.get("ok") is True, json.dumps(sp)[:200])
    check("T43 spawn reports the ACTUAL label", "labelActual" in sp, json.dumps(sp)[:240])
    if actor_path:
        good = M.call("set_actor_label", {"actorPath": actor_path, "label": "MifAuditRenamed"})
        check("T43 a valid rename succeeds", good.get("ok") is True, json.dumps(good)[:200])
        check("T43 valid rename reports actual == requested",
              good.get("labelActual") == "MifAuditRenamed", json.dumps(good)[:200])

        # Whitespace is TRIMMED by the engine - the caller should be told, not left guessing.
        pad = M.call("set_actor_label", {"actorPath": actor_path, "label": "  MifAuditPadded  "})
        check("T43 trimmed label is reported as trimmed",
              pad.get("labelActual") == "MifAuditPadded", json.dumps(pad)[:220])
    else:
        print("  (skipped rename checks - no actor path in the spawn response)")

    # ------------------------------------------------------------------ T44 unchecked defaults
    print("\n=== T44: enum literal reports whether the value was actually accepted ===")
    r = M.call("add_enum_literal", {"graphId": graph, "enum": "ECollisionChannel",
                                    "value": "__not_an_enumerator__", "x": 100, "y": 700})
    blob = json.dumps(r)
    if r.get("ok") is False:
        check("T44 bad enumerator refused outright", True)
    else:
        check("T44 bad enumerator is reported, not silently dropped",
              "valueError" in r or "valueApplied" in r, blob[:260])

    print("\n=== T45: everything still compiles ===")
    c = M.call("compile", {"blueprintId": bpid})
    check("T45 compiles", c.get("ok") is True and c.get("numErrors", 1) == 0,
          "errors=%s" % c.get("numErrors"))

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s\n          %s" % f)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
