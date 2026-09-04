"""is_scratch_fixture / pick_adoptable - can a suite tell somebody else's scratch from real content?

WHY THIS EXISTS. Several suites prefer an existing fixture and only build their own if the level has
none. That is safe exactly as long as no OTHER suite produces something the selector matches, and it
stopped being true on 2026-09-01: test_landscape_heightmap takes the first landscape with no edit
layers - a branch that never fired, because this project's own landscape HAS them - and then
test_landscape_layer_register began creating one through create_landscape, which deliberately leaves
edit layers off. On the second pass of a sweep, heightmap adopted the leftover and measured collision
against heights it had never set, reporting 1590uu of error against a perfectly good endpoint.

NO BRIDGE, NO EDITOR. This is a pure predicate over rows, so it runs anywhere and is worth running
before the thing it guards. A discriminator nobody has fed a known instance to is not a discriminator.

Usage:  python tools/test_scratch_discrimination.py
Exit:   0 passed   1 failed
"""
import sys

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:300]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:300]))


# Real rows, copied from actual landscape_info / list_level_actors responses this session.
REAL_LANDSCAPE = {
    "actorPath": "/Temp/Untitled_1.Untitled_1:PersistentLevel.Landscape_UAID_A85E45CFE404FBD100_1221515703",
    "label": "Landscape",
    "editLayers": [{"name": "Base Landscape"}, {"name": "Flat Middle"}],
}
SCRATCH_LANDSCAPE = {
    "actorPath": "/Temp/Untitled_1.Untitled_1:PersistentLevel.Landscape_UAID_E8C8292EF2A66DFD02_1681456847",
    "label": "MifLayerReg54669",
    "editLayers": [],
}
SCRATCH_ACTOR = {
    "actorPath": "/Temp/Untitled_1.Untitled_1:PersistentLevel.StaticMeshActor_UAID_E8C8292EF2A66DFD02_1078085847",
    "label": "MifGrp_A_51234",
}
REAL_ACTOR = {
    "actorPath": "/Temp/Untitled_1.Untitled_1:PersistentLevel.BP_StreetLamp_C_UAID_0001",
    "label": "StreetLamp_03",
}
SCRATCH_ASSET = {"objectPath": "/Game/_MifSock/SK_59141.SK_59141", "label": ""}
WEAPON_TEST_MAP = {"path": "/Game/Maps/MifWeaponTest", "label": ""}
UNLABELLED_SCRATCH_SPAWN = {
    "actorPath": "/Temp/Untitled_1.Untitled_1:PersistentLevel.BP_ASCFix46961_C_UAID_E8C8292EF2A66AFD02_1419936401",
    "label": "",
}


def main():
    print("=== S100: the two rows the real bug turned on ===")
    check("S100 the project's own landscape is NOT scratch",
          M.is_scratch_fixture(REAL_LANDSCAPE) is False, REAL_LANDSCAPE)
    check("S100 a suite's scratch landscape IS scratch",
          M.is_scratch_fixture(SCRATCH_LANDSCAPE) is True, SCRATCH_LANDSCAPE)
    # THE ACTUAL REGRESSION, replayed: heightmap wants a landscape with no edit layers. Both the
    # scratch one and (hypothetically) a real one could match; only the scratch one must be skipped.
    rows = [REAL_LANDSCAPE, SCRATCH_LANDSCAPE]
    picked = M.pick_adoptable(rows, lambda r: not r.get("editLayers"))
    check("S100 asking for a no-edit-layer landscape now returns NOTHING rather than the scratch one",
          picked is None, picked)
    check("S100 and asking without that filter returns the project's own",
          (M.pick_adoptable(rows) or {}).get("label") == "Landscape", M.pick_adoptable(rows))

    print("\n=== S101: labels and paths ===")
    check("S101 a Mif-prefixed actor label is scratch",
          M.is_scratch_fixture(SCRATCH_ACTOR) is True, SCRATCH_ACTOR)
    check("S101 an ordinary actor label is not",
          M.is_scratch_fixture(REAL_ACTOR) is False, REAL_ACTOR)
    check("S101 a /Game/_Mif asset path is scratch",
          M.is_scratch_fixture(SCRATCH_ASSET) is True, SCRATCH_ASSET)

    print("\n=== S102: the exception that makes label and path different questions ===")
    # /Game/Maps/MifWeaponTest is a REAL map used deliberately by the sublevel suites because it is
    # one of the very few loose maps here. Mif in a PACKAGE path does not mean scratch.
    check("S102 /Game/Maps/MifWeaponTest is NOT scratch despite the name",
          M.is_scratch_fixture(WEAPON_TEST_MAP) is False, WEAPON_TEST_MAP)

    print("\n=== S103: the gap, asserted so it cannot be forgotten ===")
    # MEASURED, and the first measurement was WRONG in a way worth keeping visible. This used to say
    # every spawn_actor_in_level call in tools/ passes a Mif-prefixed label, so "the hole below is in
    # the code path, not in the suite set". Swept across all 32 spawner call sites on 2026-09-03 and
    # it was false at two: audit_read_purity spawned "PureSpline_%d" and "PureWaterProbe_%d" into the
    # editor world and never removed them, so each run leaked two actors this function could not see.
    # The SUITES were fine; tools/ is wider than the suites, and audits spawn too.
    #
    # Fixed, and the convention is now a check rather than a habit: audit_spawn_labels.py is in the
    # release gate and goes red on a spawn whose label is not Mif-prefixed. The hole below is still
    # real in the code path - an unlabelled actor is still undetectable here - but nothing is
    # currently sitting in it, and something now notices if that changes.
    # An actor spawned from a scratch BLUEPRINT with no label is NOT detected - those classes are
    # BP_ASCFix, BP_NoASC, BP_NS_, BP_Probe with no shared prefix. Asserting the FALSE result rather
    # than leaving it undocumented: if someone later makes this detectable, this check goes red and
    # they will find the comment explaining why it was ever false.
    check("S103 an unlabelled scratch-blueprint spawn is NOT detected - known gap, label your spawns",
          M.is_scratch_fixture(UNLABELLED_SCRATCH_SPAWN) is False, UNLABELLED_SCRATCH_SPAWN)

    print("\n=== S104: shapes that must not raise ===")
    for bad in (None, "", 42, [], {}):
        try:
            M.is_scratch_fixture(bad)
        except Exception as exc:
            check("S104 is_scratch_fixture(%r) does not raise" % (bad,), False, exc)
            break
    else:
        check("S104 junk input returns False rather than raising", True)
    check("S104 pick_adoptable([]) is None", M.pick_adoptable([]) is None)
    check("S104 pick_adoptable(None) is None", M.pick_adoptable(None) is None)

    # ------------------------------------------------------------------ S105 the find_assets shape
    print("\n=== S105: a find_assets ROW, which names its path differently ===")
    # THE FIELD THAT WAS MISSING UNTIL 2026-09-03. is_scratch_fixture read actorPath/objectPath/path
    # - the shape list_level_actors returns - and find_assets rows carry the package path under
    # `packageName`. So every caller handing a find_assets row straight in got False for a scratch
    # asset: the FALSE NEGATIVE direction, which the function's own docstring calls the actual bug.
    # audit_fixture_adoption's IDENT pattern had the identical omission, for the identical reason -
    # both field lists were written from list_level_actors' shape and never revisited.
    check("S105 a scratch packageName is scratch",
          M.is_scratch_fixture({"packageName": "/Game/_MifProbe/SM_x"}) is True,
          "/Game/_MifProbe/SM_x")
    check("S105 an ordinary packageName is not",
          M.is_scratch_fixture({"packageName": "/Game/Meshes/SM_Rock"}) is False,
          "/Game/Meshes/SM_Rock")
    # ORDER MATTERS AND IS ASSERTED. packageName is read LAST, after path/objectPath/actorPath, so a
    # row carrying both must be judged on the primary field rather than the fallback.
    check("S105 an explicit path still wins over packageName",
          M.is_scratch_fixture({"path": "/Game/_MifProbe/A", "packageName": "/Game/Real/B"}) is True,
          "path scratch, packageName real")
    check("S105 pick_adoptable skips a scratch row that only has packageName",
          (M.pick_adoptable([{"packageName": "/Game/_MifProbe/A"},
                             {"packageName": "/Game/Real/B"}]) or {}).get("packageName")
          == "/Game/Real/B",
          M.pick_adoptable([{"packageName": "/Game/_MifProbe/A"},
                            {"packageName": "/Game/Real/B"}]))

    # ------------------------------------------------------------------
    # THE SAME QUESTION ABOUT THE OTHER BACKEND
    # ------------------------------------------------------------------
    # On 2026-09-04 an audit was pointed at port 8792 believing it had just started the server
    # there. The start had failed, a real Blender held the port, and seventeen read-only ops ran
    # against somebody's open file. Read-only is the only reason that was cheap: test_blender_mesh
    # calls clear_scene SEVEN times, and clear_scene deletes every object in the file with no
    # confirmation. blender_audit_common now refuses to aim a scene-wide destructive op at any
    # instance that is interactive or has a file open.
    #
    # Gated HERE because the guard was built and mutation-tested by hand in one sitting, and a
    # guard nobody re-runs is one refactor away from allowing everything. Every branch below has
    # been watched firing; these checks are what keep it that way.
    print("")
    print("=== a scratch Blender, told apart from somebody's session ===")

    import blender_audit_common as B

    def _ping(**kw):
        base = {"pid": 5488, "blenderVersionString": "5.0.1", "background": True,
                "blendFile": None, "objectCount": 4}
        base.update(kw)
        return lambda op, params=None, timeout=30.0: base

    def _verdict(op, **kw):
        """(refused?, message) for one op against a faked instance."""
        saved = B._raw_call
        B._raw_call = _ping(**kw)
        try:
            B._guard_destructive(op)
            return False, ""
        except B.LiveInstanceRefused as exc:
            return True, str(exc)
        finally:
            B._raw_call = saved

    refused, msg = _verdict("clear_scene", background=False)
    check("clear_scene is REFUSED against an interactive Blender - the case that would have "
          "emptied a live file", refused, msg)
    check("and the refusal says NOTHING was sent, the promise every other refusal in this "
          "project makes", "NOTHING was sent" in msg, msg)
    check("and it names the pid, which is the only thing telling two Blenders apart",
          "5488" in msg, msg)

    refused, msg = _verdict("clear_scene", blendFile=r"D:\work\MIF_OilRig.blend")
    check("clear_scene is REFUSED against a BACKGROUND Blender that has a file open - background "
          "is not the same as empty, and a batch render holds real work too", refused, msg)
    check("and it names the file, so the operator can tell whose it is",
          "MIF_OilRig.blend" in msg, msg)

    for op in ("open_file", "save_file"):
        refused, _ = _verdict(op, background=False)
        check("%s is REFUSED too - it replaces or overwrites the file, which destroys as much as "
              "clearing it" % op, refused, op)

    refused, _ = _verdict("clear_scene")
    check("a real scratch instance - background, no file - is ALLOWED, without which this guard "
          "would just break every suite it touches", not refused, "scratch was refused")

    for op in ("delete_object", "create_primitive", "ping", "scene_info"):
        refused, _ = _verdict(op, background=False, blendFile=r"D:\work\real.blend")
        check("%s is ALLOWED even against a live instance - it names its target, so guarding it "
              "would fail loudly on work that was never dangerous" % op, not refused, op)

    import os as _os
    _os.environ[B.ALLOW_LIVE_ENV] = "1"
    try:
        refused, _ = _verdict("clear_scene", background=False)
    finally:
        del _os.environ[B.ALLOW_LIVE_ENV]
    check("%s=1 overrides the refusal - a guard with no way past it is a guard people delete"
          % B.ALLOW_LIVE_ENV, not refused, "override was ignored")

    saved = B._raw_call
    B._raw_call = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no such endpoint"))
    try:
        B._guard_destructive("clear_scene")
        opened = True
    except B.LiveInstanceRefused:
        opened = False
    finally:
        B._raw_call = saved
    check("an unreadable ping FAILS OPEN - deliberately, because refusing to run at all against an "
          "older addon buys nothing and gets the guard switched off", opened,
          "an unidentifiable instance was refused")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
