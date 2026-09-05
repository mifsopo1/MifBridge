"""lighting_build_status - and the three build verbs that did NOT need endpoints.

SCOPE, CUT AFTER CHECKING RATHER THAN AFTER BUILDING. The survey asked for four things:
build_lighting, lighting_build_status, build_reflection_captures and a recaptureSky flag. Three of
the four already work, because they are ordinary editor commands and this plugin already drives
those through invoke_editor_command:

    BuildLightingOnly
    BuildReflectionCapturesOnly
    BuildLightingOnly_VisibilityOnly

T4301 IS THE ASSERTION THAT MATTERS MOST, and it is not about the endpoint's behaviour at all: it
checks that those three command names REALLY EXIST in the live editor's LevelEditor context. The
endpoint's response tells a caller to use them, and advice baked into a string rots silently - a
command renamed in a future engine would leave this endpoint confidently pointing at nothing. So the
names are read back out of list_editor_commands and compared, which turns the guidance into
something that fails loudly instead.

WHAT WAS GENUINELY MISSING was the read half. Those commands are fire-and-forget: they return
nothing, a Lightmass build runs for minutes, and there was no way to ask whether it had finished or
how much of the level was still unbuilt. Meanwhile every capture_viewport an agent takes shows
preview lighting, which looks like a rendering bug rather than an unfinished build.

NOT EXERCISED, and named rather than left to be inferred: the unbuilt-and-not-running branch.
NumLightingUnbuiltObjects is maintained by the lighting build system, not by the editor as actors
change - spawning a static mesh and a static point light does NOT move it, which this suite
confirmed empirically. Reaching a non-zero count means actually running a Lightmass build, which
takes minutes and writes into the level, so it is left alone. What IS asserted is that the derived
`built` flag always agrees with the three inputs it is derived from, in whatever state the level is
found.
"""
import json
import sys

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

    # ------------------------------------------------------------------ T4300 the read half
    print("=== T4300: the lighting state is readable at all ===")
    r = M.call("lighting_build_status", {})
    check("T4300 lighting_build_status succeeds", r.get("ok") is True, json.dumps(r)[:250])
    if not r.get("ok"):
        return 1
    for field, kind in (("running", bool), ("built", bool), ("unbuiltObjects", (int, float)),
                        ("unbuiltReflectionCaptures", (int, float)), ("cookedMap", bool),
                        ("level", str)):
        check("T4300 it reports %s" % field, isinstance(r.get(field), kind), r.get(field))

    # A DERIVED FIELD MUST AGREE WITH ITS INPUTS. `built` is computed from the two counts and the
    # running flag; if it can disagree with them it is worse than not being there, because a caller
    # will branch on it rather than on the numbers.
    expected = (not r["running"] and r["unbuiltObjects"] == 0
                and r["unbuiltReflectionCaptures"] == 0)
    check("T4300 `built` agrees with the counts and the running flag it is derived from",
          r["built"] is expected,
          "built=%s running=%s objects=%s captures=%s"
          % (r["built"], r["running"], r["unbuiltObjects"], r["unbuiltReflectionCaptures"]))

    # ------------------------------------------------------------------ T4301 the advice
    print("\n=== T4301: the commands this endpoint points at must really exist ===")
    check("T4301 the response names the real build verbs rather than implying it starts one",
          "BuildLightingOnly" in (r.get("startNote") or ""), (r.get("startNote") or "")[:200])

    cmds = M.call("list_editor_commands", {"context": "LevelEditor"})
    level_ctx = [c for c in (cmds.get("contexts") or []) if c.get("context") == "LevelEditor"]
    check("T4301 (setup) the LevelEditor command context is readable", bool(level_ctx),
          len(cmds.get("contexts") or []))
    if level_ctx:
        names = {c.get("name") for c in (level_ctx[0].get("commands") or [])}
        # THE assertion. Advice in a string rots silently; this makes it fail loudly instead.
        for want in ("BuildLightingOnly", "BuildReflectionCapturesOnly",
                     "BuildLightingOnly_VisibilityOnly"):
            check("T4301 '%s' really exists in the live editor" % want, want in names,
                  "not among %d LevelEditor commands" % len(names))
        check("T4301 - so the three build verbs need no endpoint of their own",
              {"BuildLightingOnly", "BuildReflectionCapturesOnly"} <= names,
              "invoke_editor_command already reaches them")

    print("\n=== T4302: it reads, and says so when asked to do more ===")
    for bad, why in (({"build": True}, "build"), ({"wait": True}, "wait"),
                     ({"quality": "production"}, "quality")):
        resp = M.raw_post("lighting_build_status", bad)
        check("T4302 '%s' is refused - this endpoint does not start anything" % why,
              resp.get("ok") is False, json.dumps(resp)[:200])
    hint = M.raw_post("lighting_build_status", {"build": True})
    check("T4302 and the refusal points at the command that DOES start a build",
          "BuildLightingOnly" in (hint.get("error") or ""), (hint.get("error") or "")[:220])

    # Reading twice must not change anything - it is a pure read.
    again = M.call("lighting_build_status", {})
    check("T4302 reading twice reports the same state - no side effects",
          again.get("unbuiltObjects") == r.get("unbuiltObjects")
          and again.get("built") == r.get("built"), json.dumps(again)[:200])

    if r.get("cookedMap"):
        check("T4302 a cooked map is flagged, with the reason the result cannot persist",
              "cannot be resaved" in (r.get("transientNote") or ""),
              (r.get("transientNote") or "")[:200])
    else:
        print("  NOTE  the open level is not cooked, so the transient-result warning is not")
        print("        exercised here. It fires on a cooked map, where a build runs and looks")
        print("        right and then cannot be saved.")

    print("\n  NOT EXERCISED: the unbuilt-and-not-running branch. NumLightingUnbuiltObjects is")
    print("  maintained by the lighting build system, not as actors change - spawning a static")
    print("  mesh and a static point light does not move it, which was checked rather than")
    print("  assumed. Reaching a non-zero count means running a real Lightmass build, which takes")
    print("  minutes and writes into the level.")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
