"""list_game_feature_plugins and describe_game_feature_plugin - the modding subsystem.

Of the 14 subsystems on docs/13_COMPETITOR_GAP_MAP.md, this is the one that is ABOUT MODDING: a Game
Feature plugin is how content gets added to a shipped game without patching the base game. DDS2 ships a
real one - DDS2Casino, the Casino DLC - so this suite runs against genuine content rather than an empty
list, which matters because an endpoint whose only evidence is "returned zero rows" is barely tested.

THE THING THIS SUITE EXISTS TO PROTECT: `state` IS DERIVED, NOT READ.

UGameFeaturesSubsystem::GetPluginState returns the exact state enum in one call and is the obvious thing
to reach for. It is UE 5.7-ONLY - it does not exist in 5.3 at all, which is the engine the SDK actually
runs on. This is the reverse of the usual trap here: normally 5.3 has something 5.7 deleted, so the
danger is writing against the OLD engine. Here the danger is writing against the NEW one and breaking
the old. So the handler derives state from the four predicates present in BOTH trees
(installed/registered/loaded/active) and reports the raw predicates alongside it.

T602 asserts the derivation is internally consistent - the states are a LADDER, so anything Active must
also be Loaded, Registered and Installed. If that ever fails, either the ladder assumption is wrong or
the derivation is, and every state this endpoint has ever reported is suspect.

T603 asserts the response SAYS the state is derived. That is not decoration: a caller who believes they
are reading the engine's own answer will trust it across engine versions in a way they should not.

SAFETY: read-only. Enumerates plugins and reads descriptors. Loads nothing, activates nothing - and
deliberately so, since activating a game feature changes what is mounted in the running editor.
"""
import json
import sys

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ------------------------------------------------------------------ T600 the guard
    print("=== T600 [the guard]: registered whether or not the plugin is here ===")
    eps = M.endpoint_names()
    for e in ("list_game_feature_plugins", "describe_game_feature_plugin"):
        check("T600 %s is registered" % e, e in eps,
              "%d endpoints and this one is absent - MIF_WITH_GAMEFEATURES is supposed to keep it "
              "registered and compile a refusal, not drop it" % len(eps))

    r = M.call("list_game_feature_plugins", {}, timeout=180)
    if "no GameFeatures plugin" in (r.get("error") or ""):
        print("  (this engine has no GameFeatures plugin - asserting the refusal is well-formed)")
        check("T600 the refusal explains the plugin is what is missing",
              "plugin" in (r.get("error") or ""), (r.get("error") or "")[:200])
        print("")
        print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
        return 1 if FAIL else 0

    # ------------------------------------------------------------------ T601 the listing
    print("")
    print("=== T601: enumerate the project's game features ===")
    check("T601 the listing succeeds", r.get("ok") is True, json.dumps(r)[:200])
    rows = r.get("plugins") or []
    check("T601 count agrees with the array it returned", r.get("count") == len(rows),
          "count=%s but %d rows" % (r.get("count"), len(rows)))
    # Three numbers, each answering a different question. Conflating them is how a caller concludes a
    # project has no game features when it merely has none MATCHING.
    check("T601 gameFeaturePluginCount is present and at least count",
          isinstance(r.get("gameFeaturePluginCount"), (int, float))
          and r.get("gameFeaturePluginCount") >= len(rows), json.dumps(
              {k: r.get(k) for k in ("count", "gameFeaturePluginCount", "totalDiscoveredPlugins")}))
    check("T601 totalDiscoveredPlugins is at least the game feature count",
          isinstance(r.get("totalDiscoveredPlugins"), (int, float))
          and r.get("totalDiscoveredPlugins") >= (r.get("gameFeaturePluginCount") or 0),
          json.dumps({k: r.get(k) for k in ("gameFeaturePluginCount", "totalDiscoveredPlugins")}))
    # The project really does discover plugins; a zero here means enumeration itself is broken.
    check("T601 the editor discovered SOME plugins at all",
          (r.get("totalDiscoveredPlugins") or 0) > 0,
          "totalDiscoveredPlugins=%s - IPluginManager returned nothing, which cannot be right in a "
          "running editor" % r.get("totalDiscoveredPlugins"))

    for p in rows[:6]:
        nm = str(p.get("name") or "?")
        check("T601 %s has a name" % nm, bool(p.get("name")), json.dumps(p)[:160])
        check("T601 %s has a file-protocol URL" % nm, bool(p.get("url")), json.dumps(p)[:160])
        # detectedBy says WHICH test matched rather than asking the caller to trust a bare yes.
        check("T601 %s says how it was detected" % nm,
              p.get("detectedBy") in ("subsystem", "descriptor", "subsystem+descriptor"),
              json.dumps(p)[:180])
        check("T601 %s reports a bool enabled" % nm, isinstance(p.get("enabled"), bool),
              json.dumps(p)[:160])

    # ------------------------------------------------------------------ T602 the ladder
    print("")
    print("=== T602 [the derivation]: the four predicates are a LADDER and must not contradict ===")
    LADDER = ("Active", "Loaded", "Registered", "Installed", "NotLoaded")
    for p in rows[:6]:
        nm = str(p.get("name") or "?")
        flags = p.get("stateFlags") or {}
        check("T602 %s reports a state name from the known set" % nm, p.get("state") in LADDER,
              "state=%r" % p.get("state"))
        check("T602 %s reports all four raw predicates" % nm,
              all(isinstance(flags.get(k), bool)
                  for k in ("installed", "registered", "loaded", "active")), json.dumps(flags))
        if all(isinstance(flags.get(k), bool)
               for k in ("installed", "registered", "loaded", "active")):
            # THE assertion. Active implies the rungs below it. If this fails, the derivation and the
            # engine disagree about what these predicates mean.
            if flags.get("active"):
                check("T602 %s: Active implies Loaded, Registered and Installed" % nm,
                      flags.get("loaded") and flags.get("registered") and flags.get("installed"),
                      json.dumps(flags))
            if flags.get("loaded"):
                check("T602 %s: Loaded implies Registered and Installed" % nm,
                      flags.get("registered") and flags.get("installed"), json.dumps(flags))
            if flags.get("registered"):
                check("T602 %s: Registered implies Installed" % nm, flags.get("installed"),
                      json.dumps(flags))
            # And the derived name must agree with the raw flags it was derived FROM.
            expect = ("Active" if flags.get("active") else
                      "Loaded" if flags.get("loaded") else
                      "Registered" if flags.get("registered") else
                      "Installed" if flags.get("installed") else "NotLoaded")
            check("T602 %s: the derived name matches its own raw predicates" % nm,
                  p.get("state") == expect,
                  "state=%r but flags imply %r - %s" % (p.get("state"), expect, json.dumps(flags)))
            check("T602 %s: knownToSubsystem agrees with the predicates" % nm,
                  p.get("knownToSubsystem") == any(
                      flags.get(k) for k in ("installed", "registered", "loaded", "active")),
                  json.dumps(p)[:180])

    # ------------------------------------------------------------------ T603 honesty about derivation
    print("")
    print("=== T603 [honesty]: the response must SAY the state is derived, not the engine's answer ===")
    note = r.get("stateNote") or ""
    check("T603 the listing carries a stateNote", bool(note), json.dumps(r)[:200])
    check("T603 and it says the state is DERIVED", "DERIVED" in note or "derived" in note, note[:190])
    # Naming the 5.7-only API is what stops someone "simplifying" this to GetPluginState later and
    # silently breaking the 5.3 build the SDK runs on.
    check("T603 and it names GetPluginState as the 5.7-only API",
          "GetPluginState" in note and "5.7" in note, note[:220])

    # ------------------------------------------------------------------ T604 describe
    print("")
    print("=== T604: describe one by name ===")
    if not rows:
        check("T604 (not exercised: this project has no game feature plugins)", True)
    else:
        nm = rows[0].get("name")
        d = M.call("describe_game_feature_plugin", {"name": nm}, timeout=180)
        check("T604 %s describes" % nm, d.get("ok") is True, json.dumps(d)[:200])
        check("T604 it echoes the name", d.get("name") == nm, json.dumps(d)[:180])
        check("T604 it reports isGameFeature true", d.get("isGameFeature") is True, json.dumps(d)[:200])
        check("T604 and agrees with the listing about state",
              d.get("state") == rows[0].get("state"),
              "describe=%s list=%s" % (d.get("state"), rows[0].get("state")))
        desc = d.get("descriptor") or {}
        for k in ("friendlyName", "category", "explicitlyLoaded", "enabledByDefault",
                  "canContainContent"):
            check("T604 descriptor.%s is present" % k, k in desc, json.dumps(desc)[:200])
        check("T604 explicitlyLoaded is a bool", isinstance(desc.get("explicitlyLoaded"), bool),
              json.dumps(desc)[:200])
        # enabledByDefault is a TRI-STATE, not a bool - the .uplugin JSON key reads like a bool but
        # FPluginDescriptor declares EPluginEnabledByDefault {Unspecified, Enabled, Disabled}, and
        # Unspecified means "the descriptor did not say", which a bool cannot express.
        check("T604 enabledByDefault is one of the three real states",
              desc.get("enabledByDefault") in ("Unspecified", "Enabled", "Disabled"),
              "got %r - if this is a bool, the tri-state was flattened and Unspecified was lost"
              % desc.get("enabledByDefault"))
        check("T604 moduleCount agrees with the modules array",
              d.get("moduleCount") == len(d.get("modules") or []),
              "moduleCount=%s but %d rows" % (d.get("moduleCount"), len(d.get("modules") or [])))
        check("T604 it reports a descriptor file path",
              str(d.get("descriptorFile", "")).endswith(".uplugin"), json.dumps(d)[:200])

        # Filtering by the real name must find it; filtering by nonsense must not - and must still
        # report the true total.
        one = M.call("list_game_feature_plugins", {"nameContains": nm}, timeout=120)
        check("T604 filtering by its own name finds it", (one.get("count") or 0) >= 1,
              json.dumps(one)[:180])
        none = M.call("list_game_feature_plugins", {"nameContains": "zzNoSuchPlugin_zz"}, timeout=120)
        check("T604 a filter matching nothing still succeeds", none.get("ok") is True,
              json.dumps(none)[:180])
        check("T604 and returns zero rows", none.get("count") == 0, json.dumps(none)[:180])
        # An empty list because the FILTER excluded everything means the opposite of an empty list
        # because the project has none. Both must be distinguishable without reading counts carefully.
        check("T604 and a filtered-to-nothing result explains itself",
              bool(none.get("note")) and "filter" in str(none.get("note")),
              json.dumps(none)[:220])
        act = M.call("list_game_feature_plugins", {"activeOnly": True}, timeout=120)
        check("T604 activeOnly succeeds", act.get("ok") is True, json.dumps(act)[:180])
        check("T604 and never reports more than the unfiltered total",
              (act.get("count") or 0) <= (r.get("gameFeaturePluginCount") or 0),
              json.dumps({k: act.get(k) for k in ("count", "gameFeaturePluginCount")}))
        if (act.get("count") or 0) == 0:
            check("T604 and an empty activeOnly result says activeOnly caused it",
                  "activeOnly" in str(act.get("note") or ""), json.dumps(act)[:240])

        check("T604 but gameFeaturePluginCount STILL reports the real total",
              none.get("gameFeaturePluginCount") == r.get("gameFeaturePluginCount"),
              "filtered-to-nothing said %s, real total is %s"
              % (none.get("gameFeaturePluginCount"), r.get("gameFeaturePluginCount")))

    # ------------------------------------------------------------------ T605 the non-game-feature case
    print("")
    print("=== T605: a plugin that exists but is NOT a game feature is ANSWERED, not refused ===")
    # "This is not a game feature" is the useful answer to that question - refusing would make the
    # caller unable to distinguish it from a plugin that does not exist.
    d = M.call("describe_game_feature_plugin", {"name": "MifBridge"}, timeout=120)
    check("T605 describing MifBridge itself succeeds", d.get("ok") is True, json.dumps(d)[:200])
    check("T605 and reports isGameFeature false", d.get("isGameFeature") is False, json.dumps(d)[:200])
    check("T605 and detectedBy is 'none'", d.get("detectedBy") == "none", json.dumps(d)[:180])
    check("T605 and it explains why in a note", bool(d.get("note")), json.dumps(d)[:220])

    # ------------------------------------------------------------------ T606 contracts
    print("")
    print("=== T606: bad references and unknown keys are refused with a pointer ===")
    q = M.call("describe_game_feature_plugin", {"name": "NoSuchPlugin_zz"}, timeout=90)
    check("T606 a plugin that does not exist is refused", q.get("ok") is False, json.dumps(q)[:180])
    check("T606 and the refusal says it wants a NAME not a path",
          "NAME" in (q.get("error") or "") or "name" in (q.get("error") or ""),
          (q.get("error") or "")[:190])
    q = M.call("describe_game_feature_plugin", {}, timeout=60)
    check("T606 a missing name is refused", q.get("ok") is False, json.dumps(q)[:180])
    q = M.call("describe_game_feature_plugin", {"nameContains": "x"}, timeout=60)
    check("T606 'nameContains' is refused on describe", q.get("ok") is False, json.dumps(q)[:180])
    check("T606 and points at list_game_feature_plugins",
          "list_game_feature_plugins" in (q.get("error") or ""), (q.get("error") or "")[:190])
    q = M.call("list_game_feature_plugins", {"name": "x"}, timeout=60)
    check("T606 'name' is refused on the listing", q.get("ok") is False, json.dumps(q)[:180])
    check("T606 and points at describe_game_feature_plugin",
          "describe_game_feature_plugin" in (q.get("error") or ""), (q.get("error") or "")[:190])
    # The read-only boundary, stated in the contract rather than left implicit.
    q = M.call("list_game_feature_plugins", {"activate": True}, timeout=60)
    check("T606 'activate' is refused and says the bridge does not activate features",
          q.get("ok") is False and "read-only" in (q.get("error") or ""),
          (q.get("error") or "")[:200])
    check("T606 the bridge is still answering", M.bridge_responsive() is True, "bridge died")

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
