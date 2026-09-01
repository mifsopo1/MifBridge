"""Blend profiles - per-bone blend scales, and the two ways the engine writes nothing quietly.

WHAT THEY ARE. A UBlendProfile is a named per-bone weighting stored on a USkeleton. It is what makes
an upper-body montage blend in fast on the spine and slowly on the legs: the transition has one
duration and the profile scales it per bone.

BOTH SILENT NO-OPS LIVE IN UBlendProfile::SetSingleBoneBlendScale, and this suite exists mostly to
pin them down:

  * bCreate DEFAULTS TO FALSE. `if(!Entry && bCreate)` - with no existing entry for that bone and
    bCreate false, the engine writes nothing, creates nothing and reports nothing. That is EVERY
    first write to a bone. The endpoint always passes true, and D303 proves a first write lands.

  * SETTING THE DEFAULT SCALE DELETES THE ENTRY. The same function ends with
        if (Entry->BlendScale == GetDefaultBlendScale()) { ProfileEntries.RemoveAll(...) }
    which is correct - a profile only stores bones that deviate - but it means a read-back returns
    the default whether the value was stored or the entry was removed. D305 asserts the two are
    reported differently, because nothing about the scale can tell them apart.

AND THE MODE DECIDES WHAT THE NUMBER MEANS: timeFactor 0.5 is "this bone takes half the transition
time", weightFactor 0.5 is "this bone's blend weight is halved". Same number, opposite intent, so
every response names the mode - and which value ERASES an entry depends on it too (0.0 for a
blendMask profile, 1.0 for the others).

FIXTURE: whatever USkeleton this project already has. No asset is created and nothing is saved.

THE PROFILE THIS SUITE ADDS IS NOT REMOVED, because there is no remove_blend_profile endpoint yet -
USkeleton offers no public removal and it would mean editing BlendProfiles directly. The skeleton
is never saved here so it does not persist, but a caller who saves that skeleton afterwards keeps
it. Said here as well as at the end of the run rather than quietly left behind.

Usage:  python tools/test_blend_profiles.py
Exit:   0 passed   1 failed   2 SKIPPED, no bridge or no skeleton
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
    ok, why = M.require_sdk_bridge()
    if not ok:
        print("skipped: %s" % why)
        return 2
    if not M.call("describe_endpoint", {"endpoint": "create_blend_profile"}).get("registered"):
        print("skipped: blend profiles are not in this build")
        return 2

    # `class` (aliases className, type) - NOT classNames, which find_assets refuses by name.
    found = M.call("find_assets", {"className": "Skeleton", "limit": 5})
    assets = found.get("assets") or []
    skel = None
    for a in assets:
        skel = str(a.get("objectPath") or a.get("path")).split(".")[0]
        break
    if not skel:
        print("skipped: this project has no USkeleton to test against")
        return 2
    print("skeleton: %s" % skel)

    bones = M.call("list_bones", {"path": skel})
    names = [b.get("name") if isinstance(b, dict) else b for b in (bones.get("bones") or [])]
    if len(names) < 3:
        print("skipped: the skeleton reports fewer than 3 bones (%s)" % len(names))
        return 2
    root = names[0]
    child = names[1]
    print("bones: root=%s child=%s (of %d)" % (root, child, len(names)))

    prof = "MifTestProfile%d" % int(time.time() % 100000)

    # ------------------------------------------------------------------ D300 create
    print("\n=== D300: create, and read back BY NAME rather than by pointer ===")
    c = M.call("create_blend_profile", {"skeleton": skel, "name": prof})
    check("D300 create succeeds", c.get("ok") is not False, json.dumps(c)[:260])
    check("D300 it is findable by name - which is how everything else reaches it",
          c.get("findableByName") is True, json.dumps(c)[:220])
    check("D300 a new profile starts EMPTY",
          (c.get("profile") or {}).get("boneCount") == 0, json.dumps(c.get("profile"))[:220])
    check("D300 it reports the mode, because the mode decides what the numbers mean",
          (c.get("profile") or {}).get("mode") in ("timeFactor", "weightFactor", "blendMask"),
          json.dumps(c.get("profile"))[:220])
    check("D300 and says the SKELETON is what got dirtied",
          "SKELETON" in str(c.get("saveNote", "")), c.get("saveNote"))
    mode = (c.get("profile") or {}).get("mode")
    default = (c.get("profile") or {}).get("defaultBlendScale")
    print("  mode=%s defaultBlendScale=%s" % (mode, default))

    # ------------------------------------------------------------------ D301 refusals
    print("\n=== D301: refusals, each naming its own cause ===")
    dup = M.call("create_blend_profile", {"skeleton": skel, "name": prof})
    check("D301 a duplicate name is refused rather than handing back the existing profile",
          dup.get("ok") is False and "already has" in str(dup.get("error", "")),
          str(dup.get("error"))[:220])

    nomode = M.call("create_blend_profile", {"skeleton": skel, "name": prof + "X", "mode": "weightFactor"})
    check("D301 `mode` is refused honestly - there is no setter to pretend with",
          nomode.get("ok") is False and "no setter" in str(nomode.get("error", "")),
          str(nomode.get("error"))[:240])

    badprof = M.call("set_blend_profile_bone",
                     {"skeleton": skel, "profile": "NoSuchProfile", "bone": root, "scale": 0.5})
    check("D301 an unknown profile is refused and points at create_blend_profile",
          badprof.get("ok") is False and "create_blend_profile" in str(badprof.get("error", "")),
          str(badprof.get("error"))[:240])

    badbone = M.call("set_blend_profile_bone",
                     {"skeleton": skel, "profile": prof, "bone": "NoSuchBone", "scale": 0.5})
    check("D301 an unknown bone is refused, saying the engine returns SILENTLY for one",
          badbone.get("ok") is False and "silently" in str(badbone.get("error", "")).lower(),
          str(badbone.get("error"))[:240])

    nocreate = M.call("set_blend_profile_bone",
                      {"skeleton": skel, "profile": prof, "bone": root, "scale": 0.5,
                       "create": True})
    check("D301 `create` is refused - its false value is 'silently do nothing'",
          nocreate.get("ok") is False and "bCreate" in str(nocreate.get("error", "")),
          str(nocreate.get("error"))[:240])

    # ------------------------------------------------------------------ D302 the first write
    print("\n=== D302: the FIRST write to a bone - what bCreate=false would have swallowed ===")
    non_default = 0.25 if (default is None or abs(default - 0.25) > 0.01) else 0.75
    s = M.call("set_blend_profile_bone",
               {"skeleton": skel, "profile": prof, "bone": root, "scale": non_default})
    check("D302 the write succeeds", s.get("ok") is not False, json.dumps(s)[:260])
    check("D302 the profile went from 0 bones to 1 - the entry was CREATED",
          s.get("boneCountBefore") == 0 and s.get("boneCountAfter") == 1,
          "before=%s after=%s" % (s.get("boneCountBefore"), s.get("boneCountAfter")))
    check("D302 the scale reads back as what was asked for",
          s.get("scaleStored") is True and abs(s.get("scaleReadBack", 0) - non_default) < 0.001,
          json.dumps(s)[:240])
    check("D302 and it was NOT reported as an entry removal",
          s.get("entryRemoved") is False, json.dumps(s)[:200])

    lst = M.call("list_blend_profiles", {"skeleton": skel, "profile": prof})
    got = (lst.get("profiles") or [{}])[0]
    check("D302 an independent read finds the bone in the profile",
          any(x.get("bone") == root for x in (got.get("bones") or [])),
          json.dumps(got)[:250])

    # ------------------------------------------------------------------ D303 recurse
    print("\n=== D303: recurse covers a whole limb from its root ===")
    r = M.call("set_blend_profile_bone",
               {"skeleton": skel, "profile": prof, "bone": root, "scale": non_default,
                "recurse": True})
    check("D303 recurse succeeds", r.get("ok") is not False, json.dumps(r)[:220])
    check("D303 and it added more bones than the single write did",
          r.get("boneCountAfter") > 1, "after=%s" % r.get("boneCountAfter"))

    # ------------------------------------------------------------------ D304 the deletion trap
    print("\n=== D304: setting the DEFAULT scale removes the entry - and says so ===")
    d = M.call("set_blend_profile_bone",
               {"skeleton": skel, "profile": prof, "bone": root, "scale": default})
    check("D304 the call succeeds", d.get("ok") is not False, json.dumps(d)[:240])
    # THE CHECK THAT MATTERS. GetBoneBlendScale returns the default either way, so the response has
    # to say which happened - nothing about the number can.
    check("D304 entryRemoved is TRUE - the read-back alone could never tell you",
          d.get("entryRemoved") is True, json.dumps(d)[:260])
    check("D304 and it explains that a profile only stores bones that deviate",
          "deviate" in str(d.get("entryNote", "")).lower(), d.get("entryNote"))
    check("D304 the bone count really went down", d.get("boneCountAfter") < d.get("boneCountBefore"),
          "before=%s after=%s" % (d.get("boneCountBefore"), d.get("boneCountAfter")))

    after = M.call("list_blend_profiles", {"skeleton": skel, "profile": prof})
    gone = (after.get("profiles") or [{}])[0]
    check("D304 and an independent read no longer lists that bone",
          not any(x.get("bone") == root for x in (gone.get("bones") or [])),
          json.dumps(gone)[:250])

    # ------------------------------------------------------------------ D305 list
    print("\n=== D305: listing ===")
    all_p = M.call("list_blend_profiles", {"skeleton": skel})
    check("D305 the list includes the profile this suite made",
          any(p.get("name") == prof for p in (all_p.get("profiles") or [])),
          [p.get("name") for p in (all_p.get("profiles") or [])])
    missing = M.call("list_blend_profiles", {"skeleton": skel, "profile": "NoSuchProfile"})
    check("D305 asking for a profile that is not there says which ones ARE",
          missing.get("count") == 0 and "It has" in str(missing.get("note", "")),
          str(missing.get("note"))[:220])

    # ------------------------------------------------------------------ cleanup
    print("")
    # NOT COVERED and said out loud: there is no remove_blend_profile endpoint, so the profile this
    # suite created stays on the skeleton for the session. The skeleton is NEVER saved, so it does
    # not persist - but a caller who saves the skeleton afterwards keeps it.
    print("  NOT COVERED: there is no remove_blend_profile endpoint, so '%s' stays on the" % prof)
    print("  skeleton for this session. Nothing here saves, so it does not persist - but if you")
    print("  save this skeleton afterwards it will. Filed in the spec.")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
