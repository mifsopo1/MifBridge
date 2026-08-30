"""map_input_key / unmap_input_key - the write half of list_input_mappings.

THE BRIDGE COULD ALREADY BUILD BOTH ENDS AND NOT CONNECT THEM. create_asset makes an
InputMappingContext, add_enhanced_input_action makes the IA_ event node, list_input_mappings reads a
context back - and nothing could put a single mapping into one. An empty IMC plus a disconnected
input event is the whole feature except the part that makes it work.

WHY THIS IS AN ENDPOINT AND NOT A DOCUMENTED edit_container RECIPE. The reflective workaround exists
on 5.3: UInputMappingContext::Mappings is protected-but-EditAnywhere, so edit_container can append to
it. On 5.7 that array is DEPRECATED and the live data lives in DefaultKeyMappings.Mappings - verified
by reading both engines: 5.3's MapKey ends `Mappings.Add_GetRef(...)`, 5.7's ends
`DefaultKeyMappings.Mappings.Add_GetRef(...)`. So the reflective append lands in an array nothing
reads, and not even list_input_mappings would show it. A silent version-dependent no-op is not
something an agent can rely on across the 5.3-5.7 range this plugin targets. GetMappings() is
undeprecated on both and returns the right array on each, which is what these endpoints go through.

T2502 IS THE ONE THAT WOULD HAVE SHIPPED A DEAD BINDING. FKey accepts ANY FName, so a mistyped key
constructs perfectly and binds to nothing - the mapping would exist, the endpoint would report
success, and the input would never fire. EKeys::GetKeyDetails is the engine's own existence test and
runs before anything is touched.

T2504 IS THE ONE THAT PROTECTS WORK. Letting an omitted `key` mean "delete everything" is exactly the
kind of implicit widening that destroys a day's input setup, so an omitted key unbinds one ACTION and
clearing the whole context has to be asked for by name AND confirmed.

NOT ASSERTED HERE: the rebuild. MapKey calls RequestRebuildControlMappingsUsingContext BEFORE its
Add on both engines, so the mapping it just made is not in the state that was rebuilt - these
endpoints therefore always issue their own rebuild afterwards. Whether that rebuild took effect is
only observable in a running player's applied contexts, which do not exist outside PIE, so this
suite cannot assert it and says so rather than pretending. What it does assert is the postcondition
that is observable: the context lists the mapping afterwards.

RUNS IN SCRATCH MODE, deliberately. These endpoints mutate a UDataAsset in memory and mark it
dirty; they never persist, and save_package is already on the gate's unsafe list. So there is
nothing here for the gate to refuse and no reason to skip - unlike the PIE suites.

CLEANS UP: the scratch IMC and InputAction are deleted at the end. Nothing is saved, and no asset
outside /Game/_Mif* is touched - including for the cooked branch, which is why that is unexercised.
"""
import json
import sys

import mifaudit as M
import scratch_confirm as SC

PASS = []
FAIL = []

IMC = "/Game/_MifInput/IMC_MifTest"
IA = "/Game/_MifInput/IA_MifTest"
IA2 = "/Game/_MifInput/IA_MifTest2"


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:400]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:400]))


def count():
    return M.call("list_input_mappings", {"path": IMC}).get("count")


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    made = []
    try:
        # ------------------------------------------------------------------ setup
        print("=== setup: a scratch context and two actions ===")
        for path, cls in ((IMC, "InputMappingContext"), (IA, "InputAction"), (IA2, "InputAction")):
            r = M.raw_post("create_asset", {"path": path, "class": cls})
            check("(setup) %s created" % path.rsplit("/", 1)[-1], r.get("ok") is True,
                  json.dumps(r)[:200])
            if r.get("ok"):
                made.append(r.get("assetPath") or path)
        if len(made) < 3:
            return 1
        check("(setup) it starts with no mappings", count() == 0, count())

        # ------------------------------------------------------------------ T2500 the write half
        print("\n=== T2500: a key can be bound at all, confirmed by reading the context back ===")
        r = M.raw_post("map_input_key", {"context": IMC, "action": IA, "key": "SpaceBar"})
        check("T2500 map_input_key succeeds", r.get("ok") is True, json.dumps(r)[:280])
        check("T2500 it reports mapped:true and a count that moved", r.get("mapped") is True
              and r.get("mappingCount") == 1 and r.get("mappingCountBefore") == 0,
              json.dumps(r)[:280])
        check("T2500 it resolves the display name, not just the FName",
              r.get("keyDisplay") == "Space Bar", r.get("keyDisplay"))
        # THE postcondition - read through the OTHER endpoint, not from map's own response.
        rows = M.call("list_input_mappings", {"path": IMC}).get("mappings") or []
        check("T2500 list_input_mappings really shows it - the two halves agree",
              any(m.get("key") == "SpaceBar" and m.get("actionPath", "").startswith(IA)
                  for m in rows), json.dumps(rows)[:300])
        check("T2500 nothing was saved, and it says so",
              "NOTHING has been saved" in (r.get("assetNote") or ""), r.get("assetNote"))

        # ------------------------------------------------------------------ T2501 idempotence
        print("\n=== T2501: asking for a binding that already exists is not a failure ===")
        again = M.raw_post("map_input_key", {"context": IMC, "action": IA, "key": "SpaceBar"})
        check("T2501 a duplicate map succeeds rather than erroring", again.get("ok") is True,
              json.dumps(again)[:250])
        check("T2501 and reports mapped:false - the end state asked for already holds",
              again.get("mapped") is False, json.dumps(again)[:250])
        check("T2501 and did NOT add a second identical mapping", count() == 1, count())

        # ------------------------------------------------------------------ T2502 the dead binding
        print("\n=== T2502: an unknown key is REFUSED - FKey would accept the typo silently ===")
        bad = M.raw_post("map_input_key", {"context": IMC, "action": IA, "key": "Space"})
        check("T2502 a mistyped key is refused", bad.get("ok") is False, json.dumps(bad)[:250])
        check("T2502 and the refusal explains it would have bound to nothing",
              "never fire" in (bad.get("error") or ""), (bad.get("error") or "")[:200])
        check("T2502 it suggests the real key", "SpaceBar" in (bad.get("error") or ""),
              (bad.get("error") or "")[:200])
        # The suggestion list has to be USABLE. Without a length floor on the reverse containment
        # test, every single-letter key is a substring of almost any typo and "Space" suggests
        # A, C, E, P, S alongside the real answer.
        noise = [k for k in ("A", "C", "E", "P", "S")
                 if (", %s," % k) in (bad.get("error") or "") or (", %s?" % k) in (bad.get("error") or "")]
        check("T2502 and the suggestions are not padded with single-letter keys",
              not noise, "suggested junk: %s in %r" % (noise, (bad.get("error") or "")[:200]))
        check("T2502 nothing was added by the refused call", count() == 1, count())

        for missing in ({"context": IMC, "key": "A"}, {"context": IMC, "action": IA}):
            mr = M.raw_post("map_input_key", missing)
            check("T2502 a call missing %s is refused"
                  % ("action" if "action" not in missing else "key"),
                  mr.get("ok") is False, json.dumps(mr)[:200])

        nc = M.raw_post("map_input_key", {"context": "/Game/_MifInput/NoSuchContext",
                                          "action": IA, "key": "A"})
        check("T2502 an unresolvable context is refused and names the way to find one",
              nc.get("ok") is False and "find_assets" in (nc.get("error") or ""),
              (nc.get("error") or "")[:200])

        # ------------------------------------------------------------------ T2503 removal
        print("\n=== T2503: unmapping, with the count measured rather than assumed ===")
        M.raw_post("map_input_key", {"context": IMC, "action": IA, "key": "LeftMouseButton"})
        M.raw_post("map_input_key", {"context": IMC, "action": IA2, "key": "Gamepad_FaceButton_Bottom"})
        check("T2503 (setup) three mappings across two actions", count() == 3, count())

        u = M.raw_post("unmap_input_key", {"context": IMC, "action": IA, "key": "SpaceBar"})
        check("T2503 unmapping one key succeeds", u.get("ok") is True, json.dumps(u)[:250])
        check("T2503 removed:1 and the count really dropped",
              u.get("removed") == 1 and u.get("mappingCount") == 2, json.dumps(u)[:250])

        # UnmapKey and UnmapAllKeysFromAction are both void - neither reports whether it matched.
        # So `removed` has to be the measured difference, and a miss has to be visible as 0.
        miss = M.raw_post("unmap_input_key", {"context": IMC, "action": IA, "key": "Nine"})
        check("T2503 unmapping something not bound is not an error", miss.get("ok") is True,
              json.dumps(miss)[:250])
        check("T2503 and honestly reports removed:0 - measured, since UnmapKey returns void",
              miss.get("removed") == 0 and miss.get("mappingCount") == 2, json.dumps(miss)[:250])

        allk = M.raw_post("unmap_input_key", {"context": IMC, "action": IA})
        check("T2503 omitting key unbinds every key from that ONE action",
              allk.get("ok") is True and allk.get("removed") == 1, json.dumps(allk)[:250])
        check("T2503 and left the OTHER action's mapping alone - this is the blast radius that "
              "matters", count() == 1, count())

        # ------------------------------------------------------------------ T2504 blast radius
        print("\n=== T2504: clearing the whole context must be asked for, and confirmed ===")
        nope = M.raw_post("unmap_input_key", {"context": IMC, "all": True})
        check("T2504 all:true without confirm is refused", nope.get("ok") is False,
              json.dumps(nope)[:250])
        check("T2504 and the refusal says how many it would have destroyed",
              "1 of them" in (nope.get("error") or "") or "EVERY" in (nope.get("error") or ""),
              (nope.get("error") or "")[:200])
        check("T2504 the refused call really changed nothing", count() == 1, count())

        noargs = M.raw_post("unmap_input_key", {"context": IMC})
        check("T2504 and an unmap with neither action nor all:true is refused rather than "
              "guessing - a missing key must never mean 'delete everything'",
              noargs.get("ok") is False, json.dumps(noargs)[:250])

        # confirm goes through scratch_confirm, which PROVES every path in the payload is
        # scratch before sending - never by hand, however obviously scratch it looks.
        yes = SC.confirm_call("unmap_input_key", {"context": IMC, "all": True})
        check("T2504 all:true with confirm:true clears the context",
              yes.get("ok") is True and yes.get("clearedAll") is True, json.dumps(yes)[:250])
        check("T2504 and the context really is empty afterwards", count() == 0, count())

        # NOT EXERCISED, named rather than left to be discovered. The cooked branch - the
        # cooked:true / cookedNote pair from IsCookedOrContainerPackage - is unverified here. This
        # project HAS cooked InputMappingContexts (/DDS2Casino/Blueprints/Inputs/InputMap_Casino
        # reports origin:"container"), but exercising it would mean mutating and dirtying a real
        # project asset, which the scratch rule forbids. The path is a direct reuse of the house
        # helper already used by other endpoints; what is untested is this endpoint's call of it.
        print("\n  NOT EXERCISED: the cooked-package branch. Reaching it needs a cooked IMC, and")
        print("  the only ones here are real project assets - mutating one to test a warning")
        print("  string is not a trade worth making. create_asset cannot produce a cooked package.")
    finally:
        for path in made:
            SC.confirm_call("delete_asset", {"path": path})
        left = M.call("find_assets", {"pathPrefix": "/Game/_MifInput"})
        check("T2505 (cleanup) the scratch input assets are gone",
              (left.get("count") or 0) == 0, json.dumps(left)[:250])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
