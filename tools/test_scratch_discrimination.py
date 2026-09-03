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

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
