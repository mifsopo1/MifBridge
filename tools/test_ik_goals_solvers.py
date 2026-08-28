"""IK Rig goals and solvers - the half of an IK Rig that actually solves IK.

The retargeting endpoints author a root bone and named chains. That is all retargeting needs and none
of what IK needs: a rig with chains but no solvers cannot place a foot on uneven ground.

Three editor-killing asserts sit on this path and the design avoids them rather than guarding after
the fact, so the tests here check the AVOIDANCE rather than the guards:

  SetGoalCurrentTransform does check(Goal) on an unknown name, so no endpoint exposes it. T264 asserts
  that a transform parameter is refused with an explanation rather than quietly accepted.

  GetSolverUniqueName has checkNoEntry() on a bad index AND calls GetNiceName(), whose base is also
  checkNoEntry(). Solvers are therefore reported by CLASS NAME. T260 asserts the reported names are
  real class names, because a "nice name" here would mean the unsafe call is being made.

  AddNewGoal neither sanitises nor uniquifies and returns the same empty answer for "name taken" and
  "unknown bone". T263 asserts those two produce DIFFERENT, specific errors.

T262 is the test with teeth and it caught a real bug. A rig has two independent halves and needs only
the one it is used for. The validator originally demanded retarget chains and a retarget root from
every rig, which called a perfectly good IK-only rig invalid - and because a failed structural check
gates the engine probe, the one answer that would have settled it never ran. It asserts all three
shapes now: IK-only, retargeting-only, and a rig that does neither.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []
HUMAN = "/Game/Characters/Mannequins/Meshes/SKM_Manny.SKM_Manny"


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def new_rig(tag, st, mesh=HUMAN):
    p = M.call("create_asset", {"path": "/Game/_MifIK/GS_%s_%d" % (tag, st),
                                "class": "IKRigDefinition"}).get("assetPath")
    if mesh:
        M.call("set_ik_rig_mesh", {"path": p, "mesh": mesh})
    return p


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    probe = M.call("list_ik_solver_types", {})
    if "unavailable" in (probe.get("error") or ""):
        print("IK Rig is unavailable on this engine build; the endpoints say so. Nothing to test.")
        return 0
    if not M.call("list_bones", {"path": HUMAN}).get("ok"):
        print("fixture mesh missing: %s" % HUMAN)
        return 3

    # ------------------------------------------------------------------ T260 solver types
    print("\n=== T260: what solvers does this engine have ===")
    t = M.call("list_ik_solver_types", {})
    names = [x.get("solverClass") for x in (t.get("types") or [])]
    check("T260 it lists solver classes", len(names) >= 3, str(names))
    # The names are not guessable - this one is IKRigFBIKSolver while its siblings use an underscore.
    check("T260 including the full-body solver, whose name breaks the pattern",
          "IKRigFBIKSolver" in names, str(names))
    check("T260 and the limb solver", "IKRig_LimbSolver" in names, str(names))
    # A "nice name" appearing here would mean GetNiceName() is being called, which asserts on any
    # solver class that does not override it.
    check("T260 the names are CLASS names, not friendly labels",
          all(n.startswith("IKRig") and " " not in n for n in names), str(names))
    check("T260 every entry carries a resolvable path",
          all((x.get("path") or "").startswith("/Script/") for x in (t.get("types") or [])),
          json.dumps(t.get("types"))[:170])
    check("T260 and the note explains why the labels are absent",
          "asserts" in (t.get("note") or ""), (t.get("note") or "")[:160])

    # ------------------------------------------------------------------ T261 solvers
    print("\n=== T261: adding and configuring a solver ===")
    rig = new_rig("T261", st)
    a = M.call("add_ik_solver", {"path": rig, "solverClass": "IKRig_LimbSolver"})
    check("T261 a solver is added", a.get("ok") is True and a.get("index") == 0, json.dumps(a)[:170])
    check("T261 and the rig reports one", a.get("solverCount") == 1, a.get("solverCount"))
    check("T261 the response warns that indices shift",
          "shift" in (a.get("note") or ""), (a.get("note") or "")[:150])

    s = M.call("set_ik_solver", {"path": rig, "index": 0, "rootBone": "thigh_r"})
    check("T261 its root bone is set", s.get("ok") is True and s.get("rootBone") == "thigh_r",
          json.dumps(s)[:170])
    # Read back off the solver, not echoed: a LimbSolver derives its end from the goal and declines an
    # explicit end bone, and silence about that is how a rig ends up not doing what it was told.
    e = M.call("set_ik_solver", {"path": rig, "index": 0, "endBone": "foot_r"})
    check("T261 a field the solver does not use is reported, not silently dropped",
          e.get("ok") is True and (e.get("endBone") == "None" or "refusedNote" in e),
          json.dumps({k: v for k, v in e.items() if k != "note"})[:200])

    d = M.call("set_ik_solver", {"path": rig, "index": 0, "enabled": False})
    check("T261 it can be disabled", d.get("enabled") is False, d.get("enabled"))
    M.call("set_ik_solver", {"path": rig, "index": 0, "enabled": True})

    for label, payload, expect in (
        ("bad index", {"path": rig, "index": 9, "rootBone": "thigh_r"}, "out of range"),
        ("nothing to change", {"path": rig, "index": 0}, "nothing to change"),
        ("absent bone", {"path": rig, "index": 0, "rootBone": "nope_zz"}, "is not a bone"),
    ):
        q = M.call("set_ik_solver", payload)
        check("T261 %s refused" % label, q.get("ok") is False, json.dumps(q)[:140])
        check("T261 %s explains" % label, expect in (q.get("error") or ""), (q.get("error") or "")[:160])
    # An out-of-range refusal must say how many there actually are.
    q = M.call("remove_ik_solver", {"path": rig, "index": 9})
    check("T261 an out-of-range remove reports the real count",
          "has 1 solver" in (q.get("error") or ""), (q.get("error") or "")[:170])
    q = M.call("add_ik_solver", {"path": rig, "solverClass": "IKRig_NotASolver"})
    check("T261 an unknown solver class is refused with guidance",
          q.get("ok") is False and "list_ik_solver_types" in (q.get("error") or ""),
          (q.get("error") or "")[:180])

    # ------------------------------------------------------------------ T262 the two halves
    print("\n=== T262 [teeth]: a rig needs only the half it is used for ===")
    ik = new_rig("T262ik", st)
    i = M.call("add_ik_solver", {"path": ik, "solverClass": "IKRig_LimbSolver"}).get("index")
    M.call("set_ik_solver", {"path": ik, "index": i, "rootBone": "thigh_r"})
    M.call("add_ik_goal", {"path": ik, "name": "Foot_R", "bone": "foot_r"})
    M.call("set_ik_goal_solver_connection", {"path": ik, "name": "Foot_R", "solverIndex": i})
    v = M.call("list_ik_rig", {"path": ik})
    check("T262 an IK-only rig knows what it is for", v.get("purpose") == "IK", v.get("purpose"))
    # The bug this caught: demanding chains and a root marked this valid rig invalid.
    check("T262 and is VALID without retarget chains or a root", v.get("valid") is True,
          json.dumps(v.get("problems")))
    # And because a failed structural check gates the engine probe, the wrong verdict also suppressed
    # the one answer that would have settled it.
    check("T262 so the engine probe actually runs and agrees",
          v.get("runtimeInitialized") is True,
          "runtimeInitialized=%s note=%s" % (v.get("runtimeInitialized"),
                                             (v.get("runtimeNote") or "")[:110]))

    rt = new_rig("T262rt", st)
    M.call("set_ik_rig_retarget_root", {"path": rt, "bone": "pelvis"})
    M.call("add_ik_retarget_chain", {"path": rt, "name": "Spine",
                                     "startBone": "spine_01", "endBone": "spine_05"})
    v = M.call("list_ik_rig", {"path": rt})
    check("T262 a retargeting-only rig is judged on its own terms",
          v.get("purpose") == "retargeting" and v.get("valid") is True,
          "purpose=%s problems=%s" % (v.get("purpose"), json.dumps(v.get("problems"))[:140]))

    # ---------------------------------------------------------------- T262b runtimeWarnings, for real
    # REGRESSION LOCK for the fix in 3406cff: on UE 5.6+, UIKRigProcessor is a deprecated thin wrapper
    # around FIKRigProcessor, and its Log member is a documented DECOY ("the deprecated logging system
    # will no longer function... here to avoid compilation issues" - IKRigProcessor.h). Reading it, as
    # this code used to unconditionally, made runtimeWarnings/runtimeErrors silently empty on every
    # 5.6+ engine even when the real processor genuinely warned - and nothing here would have caught
    # it, because every check above only ever asserts runtimeInitialized, never the warning/error text
    # itself. A rig with a goal deliberately left unconnected from any solver is the engine's own
    # documented "warns but still initialises" case (see the runtimeNote every list_ik_rig response
    # already carries), which makes it the natural fixture for this: bInit stays True either way, so
    # only reading the REAL log - not a decoy - can tell the two states apart.
    print("\n=== T262b: an unconnected goal's warning survives to runtimeWarnings, not a decoy log ===")
    warn = new_rig("T262warn", st)
    M.call("set_ik_rig_retarget_root", {"path": warn, "bone": "pelvis"})
    M.call("add_ik_retarget_chain", {"path": warn, "name": "Spine",
                                     "startBone": "spine_01", "endBone": "spine_05"})
    M.call("add_ik_solver", {"path": warn, "solverClass": "IKRig_LimbSolver"})
    M.call("add_ik_goal", {"path": warn, "name": "OrphanGoal", "bone": "foot_r"})
    # Deliberately no set_ik_goal_solver_connection - that is the whole point of this fixture.
    v = M.call("list_ik_rig", {"path": warn})
    check("T262b the structural/engine verdict still says initialised (warnings are not fatal)",
          v.get("runtimeInitialized") is True,
          "runtimeInitialized=%s note=%s" % (v.get("runtimeInitialized"),
                                             (v.get("runtimeNote") or "")[:140]))
    warnings = v.get("runtimeWarnings") or []
    check("T262b and runtimeWarnings is non-empty, not the decoy log's permanent []",
          len(warnings) > 0, json.dumps(v.get("runtimeWarnings"))[:200])
    check("T262b naming the actual orphaned goal, not a generic message",
          any("OrphanGoal" in str(w) for w in warnings), json.dumps(warnings)[:220])

    empty = new_rig("T262empty", st)
    v = M.call("list_ik_rig", {"path": empty})
    check("T262 but a rig that does NEITHER is still called out",
          v.get("purpose") == "nothing yet" and v.get("valid") is False, v.get("purpose"))
    check("T262 and told what to add",
          any("add_ik_solver" in p for p in (v.get("problems") or [])),
          json.dumps(v.get("problems"))[:200])
    # An unset root is not a BAD root - that conflation is what marked IK-only rigs invalid.
    check("T262 an unset retarget root is not reported as an invalid one",
          not any("is not a bone in this rig" in p for p in (v.get("problems") or [])),
          json.dumps(v.get("problems"))[:180])

    # ------------------------------------------------------------------ T263 goals
    print("\n=== T263: goals, and two failures the engine cannot tell apart ===")
    g = M.call("add_ik_goal", {"path": rig, "name": "Foot_R", "bone": "foot_r"})
    check("T263 a goal is added", g.get("ok") is True and g.get("name") == "Foot_R", json.dumps(g)[:170])
    check("T263 it warns that an unconnected goal does nothing",
          "does NOTHING" in (g.get("note") or ""), (g.get("note") or "")[:150])

    # AddNewGoal returns the same empty answer for both of these. They must not read alike here.
    dup = M.call("add_ik_goal", {"path": rig, "name": "Foot_R", "bone": "foot_l"})
    bad = M.call("add_ik_goal", {"path": rig, "name": "Other", "bone": "nope_zz"})
    check("T263 a duplicate name is refused", dup.get("ok") is False, json.dumps(dup)[:140])
    check("T263 and says the NAME is the problem",
          "already has a goal" in (dup.get("error") or ""), (dup.get("error") or "")[:170])
    check("T263 an unknown bone is refused", bad.get("ok") is False, json.dumps(bad)[:140])
    check("T263 and says the BONE is the problem",
          "is not a bone" in (bad.get("error") or ""), (bad.get("error") or "")[:170])
    check("T263 the two errors are genuinely different",
          (dup.get("error") or "") != (bad.get("error") or ""), "the engine cannot tell them apart")

    mv = M.call("set_ik_goal_bone", {"path": rig, "name": "Foot_R", "bone": "foot_l"})
    check("T263 a goal can be moved to another bone",
          mv.get("ok") is True and mv.get("bone") == "foot_l", json.dumps(mv)[:160])
    check("T263 and it reports where it was", mv.get("previousBone") == "foot_r", mv.get("previousBone"))
    M.call("set_ik_goal_bone", {"path": rig, "name": "Foot_R", "bone": "foot_r"})

    q = M.call("remove_ik_goal", {"path": rig, "name": "NotAGoal_zz"})
    check("T263 removing an unknown goal lists the real ones",
          q.get("ok") is False and "It has:" in (q.get("error") or ""), (q.get("error") or "")[:170])

    # ------------------------------------------------------------------ T264 connections
    print("\n=== T264: connecting a goal is what makes it do anything ===")
    v = M.call("list_ik_rig", {"path": rig})
    goal = next((x for x in (v.get("goals") or []) if x.get("name") == "Foot_R"), {})
    check("T264 a fresh goal reaches no solver", goal.get("connected") is False, json.dumps(goal)[:160])
    # Inert goals are only a warning to the engine, so this note is the only thing that reports them.
    check("T264 and list_ik_rig says so",
          "do nothing" in (v.get("goalNote") or ""), (v.get("goalNote") or "")[:160])

    c = M.call("set_ik_goal_solver_connection", {"path": rig, "name": "Foot_R", "solverIndex": 0})
    check("T264 it connects", c.get("ok") is True and c.get("connected") is True, json.dumps(c)[:160])
    check("T264 and reports reaching a solver at all",
          c.get("connectedToAnySolver") is True, c.get("connectedToAnySolver"))
    v = M.call("list_ik_rig", {"path": rig})
    goal = next((x for x in (v.get("goals") or []) if x.get("name") == "Foot_R"), {})
    check("T264 the rig agrees", goal.get("connectedSolvers") == [0], json.dumps(goal)[:160])
    check("T264 and the inert-goal note is gone", "goalNote" not in v, (v.get("goalNote") or "")[:120])

    u = M.call("set_ik_goal_solver_connection", {"path": rig, "name": "Foot_R",
                                                 "solverIndex": 0, "connected": False})
    check("T264 it disconnects again", u.get("connected") is False, json.dumps(u)[:150])

    for label, payload, expect in (
        ("unknown goal", {"path": rig, "name": "Nope_zz", "solverIndex": 0}, "no goal called"),
        ("bad solver index", {"path": rig, "name": "Foot_R", "solverIndex": 9}, "out of range"),
    ):
        q = M.call("set_ik_goal_solver_connection", payload)
        check("T264 %s refused" % label, q.get("ok") is False, json.dumps(q)[:140])
        check("T264 %s explains" % label, expect in (q.get("error") or ""), (q.get("error") or "")[:160])

    # The assert that is avoided rather than guarded: no endpoint sets a goal transform.
    q = M.call("add_ik_goal", {"path": rig, "name": "T", "bone": "foot_r", "transform": {}})
    check("T264 a goal transform is refused with the reason it is absent",
          q.get("ok") is False and "asserts" in (q.get("error") or ""), (q.get("error") or "")[:190])

    # ------------------------------------------------------------------ T265 removal shifts indices
    print("\n=== T265: removing a solver shifts every later index ===")
    multi = new_rig("T265", st)
    for cls in ("IKRig_LimbSolver", "IKRig_BodyMover", "IKRig_SetTransform"):
        M.call("add_ik_solver", {"path": multi, "solverClass": cls})
    before = [x.get("solverClass") for x in (M.call("list_ik_rig", {"path": multi}).get("solvers") or [])]
    check("T265 three solvers were added", len(before) == 3, str(before))
    r = M.call("remove_ik_solver", {"path": multi, "index": 0})
    check("T265 the first is removed", r.get("ok") is True and r.get("solverCount") == 2,
          json.dumps(r)[:150])
    after = [x.get("solverClass") for x in (M.call("list_ik_rig", {"path": multi}).get("solvers") or [])]
    # The consequence a caller has to know about: index 0 is now a different solver.
    check("T265 and the remaining ones shifted down", after == before[1:], "%s -> %s" % (before, after))
    check("T265 the response warns about the shift",
          "shifted DOWN" in (r.get("note") or ""), (r.get("note") or "")[:160])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    print("NOTE: scratch rigs left under /Game/_MifIK/. Nothing was saved.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
