"""attach_actor / detach_actor - actor hierarchy, and the reflective route that would corrupt it.

WHAT WAS MISSING. An agent could spawn a door, a handle and a sign and place all three, and had no
way to make them one movable object - moving the parent left the children behind. The READ half was
missing too: SerializeActor reported transform and folder and nothing about hierarchy, so even an
attachment made by hand in the World Outliner was invisible over the bridge.

THE READ HALF IS ON SerializeActor, not on one endpoint, and that is why T1801 checks it through
get_level_actor rather than through attach_actor's own response: SerializeActor is the shared body
of get_level_actor, list_level_actors and four other responses, so all six gained attachParent /
attachSocket / attachedChildren at once. Asking a DIFFERENT endpoint whether the attachment landed
is the point - attach_actor reporting on itself is the shape this project has already shipped a bug
behind.

T1800: attach succeeds and reports which transform rule it used.
T1801: BOTH SIDES of the relationship read back - the child names its parent AND the parent lists
       the child. One-sided is exactly what the set_property route produces, so checking only one
       would pass over a corrupted graph.
T1802: self-parent, cycle and unknown-socket are refused, each for its own reason. A socket that
       does not exist is the interesting one: the engine silently falls back to the component
       origin, so an unchecked bad name looks like it worked.
T1803: detach, and detaching something already detached is detached:false rather than an error.
T1804: set_property REFUSES the attachment properties. They are ordinary UPROPERTYs on
       USceneComponent and ResolvePropertyPathEx crosses object boundaries, so
       set_property{propertyPath:"RootComponent.AttachParent"} resolved and assigned before this -
       setting ONE side, with no OnAttachmentChanged and no transform re-base. The capability was
       missing AND the reflective route to it was actively harmful.

CLEANS UP AFTER ITSELF. Both actors are spawned into the persistent EDITOR world, which PIE
stopping does not tear down - see mifaudit.cleanup_level_actor for the T1606 breakage an uncleaned
one already caused in an unrelated suite.
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


def spawn(label, x, y):
    r = M.call("spawn_actor_in_level", {
        "class": "/Script/Engine.StaticMeshActor",
        "location": {"x": x, "y": y, "z": 50000},
        "label": label})
    return ((r.get("actor") or {}).get("actorPath")) or r.get("actorPath"), r


def actor_of(resp):
    return resp.get("actor") or resp


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    st = int(time.time() % 100000)
    base = 900000 + st
    parent, pr = spawn("MifAttachParent%d" % st, base, base)
    child, cr = spawn("MifAttachChild%d" % st, base + 500, base)
    check("T1800 (setup) two scratch actors are spawned far from real content",
          bool(parent) and bool(child), json.dumps(pr)[:200])
    if not (parent and child):
        return 1

    try:
        # ------------------------------------------------------------------ T1800
        print("\n=== T1800: attach_actor ===")
        a = M.call("attach_actor", {"child": child, "parent": parent})
        check("T1800 attach_actor succeeds", a.get("ok") is True, json.dumps(a)[:300])
        check("T1800 it reports attached:true, not just ok", a.get("attached") is True,
              json.dumps(a)[:250])
        check("T1800 and which transform rule it used",
              a.get("keptWorldTransform") is True, json.dumps(a)[:250])
        check("T1800 it reports the level is dirty and nothing was saved",
              bool(a.get("levelNote")), json.dumps(a)[:250])

        # ------------------------------------------------------------------ T1801
        print("\n=== T1801: BOTH sides read back, through a different endpoint ===")
        kid = actor_of(M.call("get_level_actor", {"actorPath": child}))
        dad = actor_of(M.call("get_level_actor", {"actorPath": parent}))
        check("T1801 the child names its parent - read through get_level_actor, not attach_actor",
              kid.get("attachParent") == parent,
              "attachParent=%s expected=%s" % (kid.get("attachParent"), parent))
        # THE assertion that a one-sided write would fail. set_property could set the child's
        # AttachParent and leave this empty, which is exactly the corruption T1804 refuses.
        check("T1801 and the PARENT lists the child - the relationship is two-sided",
              child in (dad.get("attachedChildren") or []),
              json.dumps(dad.get("attachedChildren"))[:250])

        # ------------------------------------------------------------------ T1802
        print("\n=== T1802: refusals, each for its own reason ===")
        selfp = M.call("attach_actor", {"child": parent, "parent": parent})
        check("T1802 a self-parent is refused", selfp.get("ok") is False, json.dumps(selfp)[:250])
        check("T1802 and says NOTHING was changed",
              "NOTHING was changed" in (selfp.get("error") or ""), selfp.get("error"))

        cycle = M.call("attach_actor", {"child": parent, "parent": child})
        check("T1802 a cycle is refused", cycle.get("ok") is False, json.dumps(cycle)[:250])
        check("T1802 and the refusal names the cycle rather than failing generically",
              "cycle" in (cycle.get("error") or "").lower(), cycle.get("error"))

        # The engine falls back to the component origin for an unknown socket, so this looks like
        # success unless it is checked.
        sock = M.call("attach_actor", {"child": child, "parent": parent, "socket": "NoSuchSocket"})
        check("T1802 a socket that does not exist is refused, not silently ignored",
              sock.get("ok") is False, json.dumps(sock)[:250])
        check("T1802 and the refusal explains the silent-fallback hazard",
              "socket" in (sock.get("error") or "").lower(), sock.get("error"))

        # ------------------------------------------------------------------ T1803
        print("\n=== T1803: detach_actor ===")
        d = M.call("detach_actor", {"actorPath": child})
        check("T1803 detach_actor succeeds", d.get("ok") is True, json.dumps(d)[:300])
        check("T1803 it reports detached:true and names what it detached from",
              d.get("detached") is True and d.get("detachedFrom") == parent, json.dumps(d)[:250])
        after = actor_of(M.call("get_level_actor", {"actorPath": child}))
        check("T1803 and the child really has no parent now - read back independently",
              not after.get("attachParent"), json.dumps(after)[:250])

        again = M.call("detach_actor", {"actorPath": child})
        check("T1803 detaching an already-detached actor is NOT an error",
              again.get("ok") is True, json.dumps(again)[:250])
        check("T1803 it reports detached:false - 'it is detached' stays distinguishable from "
              "'I detached it'",
              again.get("detached") is False and again.get("wasAttached") is False,
              json.dumps(again)[:250])

        # ------------------------------------------------------------------ T1804
        print("\n=== T1804: set_property refuses the attachment properties ===")
        for prop in ("RootComponent.AttachParent", "RootComponent.AttachSocketName"):
            sp = M.call("set_property", {"objectPath": child, "propertyPath": prop,
                                         "value": parent})
            check("T1804 set_property on %s is refused" % prop, sp.get("ok") is False,
                  json.dumps(sp)[:250])
            check("T1804 %s - and the refusal points at attach_actor" % prop,
                  "attach_actor" in (sp.get("error") or ""), sp.get("error"))
    finally:
        # CLEANUP. These spawn into the persistent EDITOR world, so PIE stopping does not remove
        # them and they would pollute every later session.
        for p in (child, parent):
            if p:
                c = M.cleanup_level_actor(p, "scratch attach-test actor")
                check("T1805 (cleanup) %s is removed from the level" % p.split(".")[-1],
                      c.get("ok") is True, c.get("error"))

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
