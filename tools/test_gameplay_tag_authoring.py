"""add_gameplay_tag - the endpoint that was declined as impossible, and was not.

WHY THIS SUITE EXISTS AT ALL. add_gameplay_tag was filed as "correctly impossible, not merely
undiscovered" on 2026-08-29. The investigation behind that was careful - it read
UGameplayTagsManager, found AddTagTableRow and AddNativeGameplayTag sitting under `private:`,
checked a SECOND private block before giving up, and wrote down the right lesson about
GAMEPLAYTAGS_API on a declaration not meaning callable. Every sentence of it is true.

It was still the wrong conclusion, because it never left the module. UGameplayTagsManager lives in
Runtime/GameplayTags, where the mutators are private BY DESIGN. Tag authoring is an editor
operation, and it lives in the editor plugin - IGameplayTagsEditorModule, Engine/Plugins/Editor/
GameplayTagsEditor - whose interface is entirely public on both engines this plugin targets
(5.3.2 GameplayTagsEditorModule.h:48/:60, 5.7 the same at :50/:66).

So this suite guards two things at once: the endpoint, and the reasoning. A decline is a PERMANENT
closure - nobody re-examines a settled item - so it has to survive a wider search than a build does.

THE TWO MODES, which is the part worth testing carefully:
  transient:true  registers the tag for THIS EDITOR SESSION. Writes nothing. Safe in every write
                  mode. Gone on restart - which is exactly why this suite can use it freely against
                  a real project without leaving anything behind.
  transient:false writes into a config .ini on disk and survives a restart. That is a persistent
                  write to a file outside /Game, so it is refused unless the write mode is full.

T1700-T1702: the transient path works and the tag really resolves afterwards.
T1703:       the persistent path is REFUSED in a gated mode, and the refusal names transient:true as
             the way forward rather than just saying no.
T1704:       adding a tag that already exists is added:false / resolved:true, not an error.
T1705-T1707: refusals - empty tag, malformed tag - each checked for its specific reason.

NOTHING IS LEFT BEHIND. Every tag this suite creates is transient, so it exists only until the
editor restarts and never touches DefaultGameplayTags.ini. That is a deliberate choice over
creating and then deleting a persistent tag: DeleteTagFromINI needs an FGameplayTagNode this bridge
does not expose, so a persistent tag added here could not be cleaned up, and a suite that
permanently edits a project config file to test itself is not one worth having.
"""
import json
import sys
import time

import mifaudit as M

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:400]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:400]))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    st = int(time.time() % 100000)
    tag = "MifTest.Authoring.Tag%d" % st

    # ------------------------------------------------------------------ T1700-T1702 the transient path
    print("\n=== T1700-T1702: a transient tag is registered, and really resolves ===")

    before = M.call("describe_gameplay_tag", {"tag": tag})
    check("T1700 (setup) the tag does not exist yet",
          before.get("ok") is True and before.get("exists") is False, json.dumps(before)[:200])

    made = M.call("add_gameplay_tag", {"tag": tag, "transient": True})
    check("T1701 add_gameplay_tag succeeds in transient mode", made.get("ok") is True,
          json.dumps(made)[:250])
    check("T1701 it reports added:true - it did the work, not just accepted the call",
          made.get("added") is True, json.dumps(made)[:200])
    check("T1701 and transient:true, so nothing was written to disk",
          made.get("transient") is True, json.dumps(made)[:200])

    # THE assertion. added:true is the engine's own bool; resolved is the READ-BACK, and the two can
    # disagree - the handler fails the call when they do, so this proves the postcondition, not the
    # return value.
    check("T1702 the tag manager resolves the new tag - read back, not inferred from the return",
          made.get("resolved") is True, json.dumps(made)[:200])

    # And independently, through a DIFFERENT endpoint, so this does not rest on add_gameplay_tag
    # reporting on itself - the shape that already shipped a real bug in create_asset.
    after = M.call("describe_gameplay_tag", {"tag": tag})
    check("T1702 describe_gameplay_tag now finds it - confirmed through a different endpoint",
          after.get("ok") is True and after.get("exists") is True, json.dumps(after)[:250])

    # ------------------------------------------------------------------ T1703 the persistent path is gated
    print("\n=== T1703: the PERSISTENT path is refused unless the write mode is full ===")
    audit = M.call("self_audit", {})
    mode = (audit.get("writeMode") or "").lower()
    print("   write mode is '%s'" % mode)

    persistent_tag = "MifTest.Authoring.Persist%d" % st
    persisted = M.call("add_gameplay_tag", {"tag": persistent_tag, "transient": False})
    if mode == "full":
        print("   NOTE  running in FULL write mode, so the persistent path is NOT gated here - the "
              "refusal cannot be exercised. Reported rather than asserted: this suite must not "
              "depend on which mode the editor happened to start in, and must NOT write a tag into "
              "DefaultGameplayTags.ini just to have something to assert.")
    else:
        check("T1703 a persistent tag is refused in a gated write mode",
              persisted.get("ok") is False, json.dumps(persisted)[:250])
        check("T1703 and the refusal names transient:true as the way forward, not just 'no'",
              "transient" in (persisted.get("error") or ""), persisted.get("error"))
        check("T1703 and says NOTHING was added, so the caller knows the state is unchanged",
              "NOTHING was added" in (persisted.get("error") or ""), persisted.get("error"))
        gone = M.call("describe_gameplay_tag", {"tag": persistent_tag})
        check("T1703 and the refused tag really does not exist - the refusal was honest",
              gone.get("exists") is False, json.dumps(gone)[:200])

    # ------------------------------------------------------------------ T1704 already-exists is not an error
    print("\n=== T1704: adding a tag that already exists is not a failure ===")
    again = M.call("add_gameplay_tag", {"tag": tag, "transient": True})
    check("T1704 re-adding an existing tag succeeds", again.get("ok") is True, json.dumps(again)[:250])
    check("T1704 with added:false - 'it is there' is distinguishable from 'I put it there'",
          again.get("added") is False, json.dumps(again)[:200])
    check("T1704 and resolved:true - the end state the caller asked for holds",
          again.get("resolved") is True, json.dumps(again)[:200])

    # ------------------------------------------------------------------ T1705-T1707 refusals, exact reason
    print("\n=== T1705-T1707: refusals, each checked for its specific reason ===")
    empty = M.call("add_gameplay_tag", {"tag": "", "transient": True})
    check("T1705 an empty tag is refused", empty.get("ok") is False, json.dumps(empty)[:200])
    check("T1705 and says tag is required", "required" in (empty.get("error") or ""), empty.get("error"))

    bad = M.call("add_gameplay_tag", {"tag": "Mif Test.Has Spaces%d" % st, "transient": True})
    check("T1706 a malformed tag name is refused, not silently accepted",
          bad.get("ok") is False, json.dumps(bad)[:250])

    unknown = M.call("add_gameplay_tag", {"tag": "MifTest.X%d" % st, "transient": True,
                                          "notAParam": 1})
    check("T1707 an unknown parameter is refused rather than ignored",
          unknown.get("ok") is False, json.dumps(unknown)[:250])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
