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
    # THIS TEST WAS VACUOUS UNTIL 2026-08-31 and passed the whole time. It spelled the parameter
    # `enum`, which this endpoint refuses BY NAME ("spell it enumName here - list_enum_values takes
    # either, this endpoint reads only enumName"). So the call failed for the wrong reason, the
    # `ok is False` branch was taken, and that branch asserted literally `check(..., True)`. A test
    # can be green for years while exercising nothing but its own typo.
    #
    # The else-branch was no better: `"valueError" in r or "valueApplied" in r` passes on
    # valueApplied alone, and valueApplied is emitted on BOTH the accepted and refused paths.
    r = M.call("add_enum_literal", {"graphId": graph, "enumName": "ECollisionChannel",
                                    "value": "__not_an_enumerator__", "x": 100, "y": 700})
    blob = json.dumps(r)
    check("T44 the call SUCCEEDS - a bad default is not a failed node spawn, and conflating them "
          "would lose the node the caller asked for", r.get("ok") is True, blob[:260])
    check("T44 valueError is present - TrySetDefaultValue is void and silently refuses a literal it "
          "cannot parse, which is the defect set_pin_default was fixed for",
          isinstance(r.get("valueError"), str) and r.get("valueError"), blob[:300])
    check("T44 and it quotes the value that was refused",
          "__not_an_enumerator__" in (r.get("valueError") or ""), (r.get("valueError") or "")[:240])
    check("T44 valueApplied reports what the pin ACTUALLY holds, not what was asked for - that is "
          "the difference between reporting a postcondition and echoing the request",
          isinstance(r.get("valueApplied"), str)
          and "__not_an_enumerator__" not in (r.get("valueApplied") or ""),
          "valueApplied=%r" % r.get("valueApplied"))

    # THE OTHER HALF: a valid enumerator must NOT produce valueError. Without this, a field hardcoded
    # to always report an error would pass every assertion above.
    ok_lit = M.call("add_enum_literal", {"graphId": graph, "enumName": "ECollisionChannel",
                                         "value": "ECC_WorldStatic", "x": 100, "y": 900})
    check("T44 a VALID enumerator reports no valueError - which is what proves the field tracks the "
          "outcome rather than always being there",
          ok_lit.get("ok") is True and "valueError" not in ok_lit, json.dumps(ok_lit)[:260])

    # ------------------------------------------------------------------ T46 mode-dependent param
    print("\n=== T46: invoke_editor_tab refuses an 'asset' it would have ignored ===")
    # Found by the sweep's ghost probe. UiResolveTabManager returns early for manager:"global" and
    # never reads the asset, so passing one with the DEFAULT manager did nothing and said nothing.
    # RejectUnknownParams cannot catch it - 'asset' is a valid declared parameter, ignored by MODE.
    # Any endpoint whose parameters mean different things in different modes has the same hole.
    r = M.call("invoke_editor_tab", {"asset": "/Game/Whatever", "probe": True})
    check("T46 refused rather than ignoring the asset", r.get("ok") is False, json.dumps(r)[:220])
    check("T46 and it says why, naming the mode",
          "assetEditor" in (r.get("error") or "") and "ignored" in (r.get("error") or ""),
          (r.get("error") or "")[:220])
    # The ordinary path must still work - a fix that refuses too much is its own defect.
    ok = M.call("invoke_editor_tab", {"probe": True})
    check("T46 the plain global form still works", ok.get("ok") is True, json.dumps(ok)[:200])

    # ------------------------------------------------------------------ T47 silent ignore #2
    print("\n=== T47: trace_ground refuses an ignoreActor that does not resolve ===")
    # The old code was `if (AActor* Ignore = FindActorInWorld(...)) { AddIgnoredActor(Ignore); }`.
    # An unresolvable name meant the if never fired, the trace ran WITHOUT ignoring anything, and the
    # caller got a confident hit:true - possibly against the very actor they asked to exclude, which
    # is the one answer they had ruled out. Same class as T46, found by the same ghost probe.
    r = M.call("trace_ground", {"x": 0, "y": 0, "ignoreActor": "NoSuchActor_zzz"})
    check("T47 refused rather than tracing anyway", r.get("ok") is False, json.dumps(r)[:220])
    check("T47 and it explains the consequence",
          "without ignoring" in (r.get("error") or "").lower(), (r.get("error") or "")[:200])
    # A trace with no ignoreActor at all must still work - the ordinary path is the common one.
    ok = M.call("trace_ground", {"x": 0, "y": 0})
    check("T47 a plain trace still works", ok.get("ok") is True, json.dumps(ok)[:200])

    # T47b: the SUCCESS side of the same guard. A name is not an identity - FindActorInWorld matches
    # by label or path, and two actors can share a label - so the response echoes WHICH actor the
    # name resolved to. Without reading it, a trace that ignored the wrong actor is indistinguishable
    # from one that ignored the right one, and the whole point of T47 is that this endpoint must not
    # let you believe you excluded something you did not.
    victim = M.pick_adoptable(M.call("list_level_actors", {"limit": 20}).get("actors"))
    if victim and victim.get("label") and victim.get("actorPath"):
        ig = M.call("trace_ground", {"x": 0, "y": 0, "ignoreActor": victim.get("label")})
        check("T47b a resolvable ignoreActor traces", ig.get("ok") is True, json.dumps(ig)[:200])
        # COMPARED AGAINST THE PATH, NOT THE LABEL. This asserted `label in ignoredActor`, which a
        # bare echo of the caller's own input satisfies - and an echo is the exact failure the
        # comment above says this test exists to catch: "a trace that ignored the wrong actor is
        # indistinguishable from one that ignored the right one". It also claimed "by full path"
        # while never checking that a path came back at all.
        #
        # Equality is safe because both sides are the SAME call: list_level_actors writes actorPath
        # from Actor->GetPathName() (MifBridgeLevel.cpp) and trace_ground writes ignoredActor from
        # Ignore->GetPathName() (MifBridgeSpatial.cpp:1346). A label cannot satisfy this, which is
        # the point - the endpoint resolved a label to an actor and has to show which one.
        check("T47b and ignoredActor is the resolved actor's FULL PATH, not an echo of the label "
              "that was passed in",
              ig.get("ignoredActor") == victim.get("actorPath"),
              {"ignoredActor": ig.get("ignoredActor"), "expected": victim.get("actorPath"),
               "label_passed": victim.get("label")})
    else:
        print("  NOTE  no non-scratch level actor to exclude, so T47b's success path is UNEXERCISED.")

    print("\n=== T45: everything still compiles ===")
    c = M.call("compile", {"blueprintId": bpid})
    check("T45 compiles", c.get("ok") is True and c.get("numErrors", 1) == 0,
          "errors=%s" % c.get("numErrors"))

    # ------------------------------------------------------------------ T48 the guard that inverted
    print("")
    print("=== T48: guarded_payload must not strip a caller's REFUSAL to authorise ===")
    # WHY THIS IS A REAL DEFECT AND NOT A STYLE POINT. FORBIDDEN_KEYS exists so a suite cannot
    # authorise a destructive act by accident, and it stripped those keys whatever their VALUE. That
    # is right for a default-false flag and exactly backwards for a default-TRUE one. Three endpoints
    # default `save` to true on purpose - import_texture ("Save is ON by default here, unlike
    # create_material"), set_plugin_enabled and write_thumbnail_texture - so a suite writing
    # save:False to stay off the disk had that key deleted and the file written anyway. The guard
    # removed the only thing standing between the suite and a disk write.
    #
    # Found by sweeping for default-true booleans after `clear` on set_blendspace_samples turned out
    # to be one. Latent rather than live - no suite passes save:False today - which is precisely why
    # it would otherwise have been found by somebody's lost afternoon.
    #
    # Pure Python, no bridge: the states are known exactly, so this proves the rule rather than
    # sampling it.
    for payload, want, why in [
        ({"save": False}, {"save": False}, "save:false reaches the handler"),
        ({"save": True}, {}, "save:true is still stripped"),
        ({"save": "false"}, {"save": "false"}, "the STRING false counts as false"),
        ({"force": 0}, {"force": 0}, "zero is a false"),
        ({"overwrite": True}, {}, "overwrite:true is still stripped"),
        ({"overwrite": False}, {"overwrite": False}, "overwrite:false reaches the handler"),
        # confirm stays absolute in BOTH directions. override_inherited_component refuses outright on
        # an explicit confirm:false where a stripped one succeeds, so passing it through would change
        # behaviour suites already rely on; scratch_confirm.py is the sanctioned route for the
        # confirm-gated success paths. This guard is about not authorising, not about arguing with a
        # handler.
        ({"confirm": False}, {}, "confirm:false is stripped, unlike the others"),
        ({"confirm": True}, {}, "confirm:true is stripped"),
        ({"path": "/Game/X", "save": True}, {"path": "/Game/X"}, "ordinary keys are untouched"),
        # THE NESTED CASE. batch carries other endpoints as DATA, so a confirm one level down
        # walked past this guard entirely until 2026-09-03 - the strip stopped at the top level.
        # Latent (no suite builds that shape) but batch is the sharpest destructive endpoint there
        # is: it commits every prior op before reporting the failure.
        ({"ops": [{"endpoint": "delete_asset", "payload": {"path": "/Game/X", "confirm": True}}]},
         {"ops": [{"endpoint": "delete_asset", "payload": {"path": "/Game/X"}}]},
         "a confirm NESTED inside batch's ops[] is stripped, not just a top-level one"),
        ({"ops": [{"force": True, "keep": 9}]}, {"ops": [{"keep": 9}]},
         "the walk goes through LISTS as well as dicts"),
        # AND THE RECURSION MUST NOT EAT ORDINARY DATA. A payload that merely contains nested
        # structure has to come back identical - without this, a strip that returned {} for
        # everything would pass every check above.
        ({"a": {"b": [1, {"c": 2}]}}, {"a": {"b": [1, {"c": 2}]}},
         "ordinary nested data is returned unchanged - the negative control for the recursion"),
    ]:
        got = M.guarded_payload(payload)
        check("T48 %s" % why, got == want,
              "guarded_payload(%r) = %r, want %r" % (payload, got, want))

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s\n          %s" % f)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
