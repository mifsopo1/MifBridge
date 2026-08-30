"""add_socket - and proving the other two socket verbs did not need building.

SCOPE, CUT DOWN AFTER CHECKING. The survey asked for three endpoints: add_socket, remove_socket and
set_socket_transform. Two of them already existed under other names, because the property walker
crosses object boundaries:

    move    set_property   {objectPath: <owner>, propertyPath: "Sockets[3].RelativeLocation"}
    delete  edit_container {objectPath: <owner>, propertyPath: "Sockets", operation: "remove", index: 3}

What they lacked was the INDEX - list_sockets did not emit one, so neither call could be aimed. So
this commit adds `index`, `owner` and `objectPath` to list_sockets and ONE endpoint rather than three.
T3103 is the test that makes that a claim rather than an assumption: it takes an index straight out of
list_sockets, moves the socket with set_property, deletes it with edit_container, and reads both back.
If that test ever fails, the two endpoints are needed after all.

AddSocket CANNOT REPORT FAILURE. It returns void and silently does nothing in three cases - the outer
is not the mesh (SkeletalMesh.cpp:3703), the name is already taken (:3708), or the bone is not in the
reference skeleton (:3714) - logging to LogPCG-style UE_LOG that no HTTP caller ever sees. All three
are therefore checked here first, and the socket is confirmed by searching for it afterwards. T3102
covers each refusal.

NOT DONE, and said out loud because it looks like an omission: RebuildSocketMap() is NOT called.
USkeletalMesh::SocketMap is a PostLoad-built cache and it is tempting to think an add leaves it
stale - but in an EDITOR build every read of it (FindSocketAndIndex, SkeletalMesh.cpp:3799 and :3846)
sits inside `#if !WITH_EDITOR`, the editor paths linear-scan the Sockets array instead, and
RebuildSocketMap's whole body is `#if !WITH_EDITOR` as well, so the call would compile to nothing.
A call that looks like a safety measure and does nothing is worse than no call.

WORKS ON DUPLICATES. Real content here keeps sockets on ONE shared rig -
/Game/SkeletalMeshes/Character/DDS2_CharacterSkeleton, used by every character - so adding a test
socket to it would touch every character in the project. This suite duplicates both the mesh and the
skeleton into /Game/_Mif and repoints the copy at the copy, so nothing it does can reach the real rig.

CLEANS UP: both duplicates are deleted at the end. Nothing is saved.
"""
import json
import sys
import time

import mifaudit as M
import scratch_confirm as SC

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
    DSK = "/Game/_MifSock/SK_%d" % st
    DME = "/Game/_MifSock/Mesh_%d" % st
    OWNER = DME + "." + DME.rsplit("/", 1)[-1]
    made = []

    try:
        # ------------------------------------------------------------------ setup
        print("=== setup: scratch copies, so the shared rig is never touched ===")
        mesh = None
        for a in (M.call("find_assets", {"class": "SkeletalMesh", "limit": 25}).get("assets") or []):
            if a["path"].startswith("/Game/_Mif"):
                continue
            s0 = M.call("list_sockets", {"path": a["path"]})
            if s0.get("ok") and s0.get("skeleton"):
                mesh = a["path"]
                skel = s0["skeleton"]
                break
        check("(setup) a real SkeletalMesh with a skeleton exists", bool(mesh), mesh)
        if not mesh:
            print("SKIPPED - no rigged SkeletalMesh in this project.")
            return 0

        d1 = M.raw_post("duplicate_asset", {"path": skel, "newPath": DSK})
        d2 = M.raw_post("duplicate_asset", {"path": mesh, "newPath": DME})
        check("(setup) the skeleton and mesh both duplicate into scratch",
              d1.get("ok") is True and d2.get("ok") is True,
              "%s / %s" % (json.dumps(d1)[:100], json.dumps(d2)[:100]))
        if not (d1.get("ok") and d2.get("ok")):
            return 1
        made = [DSK, DME]

        rp = M.raw_post("set_property", {"objectPath": OWNER, "propertyPath": "Skeleton",
                                         "value": DSK + "." + DSK.rsplit("/", 1)[-1]})
        check("(setup) the copy is repointed at the COPY of the skeleton", rp.get("ok") is True,
              json.dumps(rp)[:200])
        s = M.call("list_sockets", {"path": DME})
        check("(setup) and really reports the scratch skeleton, not the shared rig",
              s.get("skeleton", "").startswith("/Game/_MifSock"), s.get("skeleton"))
        if not s.get("skeleton", "").startswith("/Game/_MifSock"):
            print("REFUSING TO CONTINUE - would have written to the shared rig.")
            return 1
        base_mesh = s.get("meshSocketCount")
        base_skel = s.get("skeletonSocketCount")

        # ------------------------------------------------------------------ T3100 the read fields
        print("\n=== T3100: list_sockets emits what the reflective write verbs need ===")
        rows = s.get("sockets") or []
        check("T3100 every socket reports an index", len(rows) > 0
              and all(isinstance(r.get("index"), (int, float)) for r in rows),
              json.dumps(rows[:1])[:250])
        check("T3100 and an objectPath", all(r.get("objectPath") for r in rows),
              json.dumps(rows[:1])[:250])
        # The index must be per-LIST, since the two lists are separate arrays on separate objects.
        for src in ("mesh", "skeleton"):
            idx = [r["index"] for r in rows if r.get("source") == src]
            check("T3100 %s indices are 0..n-1 into THAT list, which is what set_property takes"
                  % src, idx == list(range(len(idx))), idx[:10])

        # ------------------------------------------------------------------ T3101 creation
        print("\n=== T3101: creating sockets, on each target ===")
        a = M.raw_post("add_socket", {"path": DME, "name": "MifTestSkel", "bone": "head",
                                      "location": {"x": 1, "y": 2, "z": 3}})
        check("T3101 add_socket succeeds with no target given", a.get("ok") is True,
              json.dumps(a)[:250])
        # The default matters: real content keeps sockets on the skeleton, so defaulting to the mesh
        # would put every new socket where nothing looks for it.
        check("T3101 and defaults to the SKELETON, which is where real content keeps them",
              (a.get("socket") or {}).get("source") == "skeleton", json.dumps(a)[:250])
        check("T3101 the transform it was given survives",
              (a.get("socket") or {}).get("relativeLocation") == {"x": 1, "y": 2, "z": 3},
              json.dumps((a.get("socket") or {}).get("relativeLocation")))

        m = M.raw_post("add_socket", {"path": DME, "name": "MifTestMesh", "bone": "head",
                                      "target": "mesh"})
        check("T3101 target:mesh puts it on the mesh instead",
              m.get("ok") is True and (m.get("socket") or {}).get("source") == "mesh",
              json.dumps(m)[:250])
        b = M.raw_post("add_socket", {"path": DME, "name": "MifTestBoth", "bone": "head",
                                      "target": "both"})
        check("T3101 target:both succeeds", b.get("ok") is True, json.dumps(b)[:250])
        s2 = M.call("list_sockets", {"path": DME})
        # THE postcondition for "both" - it must land in BOTH lists, which only a read-back shows.
        check("T3101 and it really landed in both lists, +1 mesh and +2 skeleton overall",
              s2.get("meshSocketCount") == base_mesh + 2
              and s2.get("skeletonSocketCount") == base_skel + 2,
              "mesh %s->%s, skel %s->%s" % (base_mesh, s2.get("meshSocketCount"),
                                            base_skel, s2.get("skeletonSocketCount")))

        # ------------------------------------------------------------------ T3102 the silent drops
        print("\n=== T3102: the three cases AddSocket drops silently ===")
        badbone = M.raw_post("add_socket", {"path": DME, "name": "MifBad", "bone": "nosuchbone"})
        check("T3102 an unknown bone is refused", badbone.get("ok") is False,
              json.dumps(badbone)[:250])
        check("T3102 and the refusal says AddSocket would have done nothing quietly",
              "silently does nothing" in (badbone.get("error") or ""),
              (badbone.get("error") or "")[:220])
        check("T3102 and reports how many bones there actually are",
              "bones)" in (badbone.get("error") or ""), (badbone.get("error") or "")[:150])

        dup = M.raw_post("add_socket", {"path": DME, "name": "MifTestSkel", "bone": "head"})
        check("T3102 a duplicate socket name is refused", dup.get("ok") is False,
              json.dumps(dup)[:250])
        check("T3102 and says why uniqueness matters - attach-by-name",
              "ambiguous" in (dup.get("error") or ""), (dup.get("error") or "")[:200])

        blank = M.raw_post("add_socket", {"path": DME, "name": "   ", "bone": "head"})
        check("T3102 a whitespace-only name is refused - AddSocket TRIMS and then drops it",
              blank.get("ok") is False and "trims" in (blank.get("error") or ""),
              (blank.get("error") or "")[:200])

        nobone = M.raw_post("add_socket", {"path": DME, "name": "MifNoBone"})
        check("T3102 a missing bone is refused", nobone.get("ok") is False,
              (nobone.get("error") or "")[:180])
        badtarget = M.raw_post("add_socket", {"path": DME, "name": "MifX", "bone": "head",
                                              "target": "somewhere"})
        check("T3102 an unknown target is refused with the three real ones",
              badtarget.get("ok") is False and "skeleton" in (badtarget.get("error") or ""),
              (badtarget.get("error") or "")[:180])

        s3 = M.call("list_sockets", {"path": DME})
        check("T3102 none of the five refusals changed anything",
              s3.get("meshSocketCount") == s2.get("meshSocketCount")
              and s3.get("skeletonSocketCount") == s2.get("skeletonSocketCount"),
              "mesh=%s skel=%s" % (s3.get("meshSocketCount"), s3.get("skeletonSocketCount")))

        # ------------------------------------------------------------------ T3103 THE scope claim
        print("\n=== T3103: move and delete need no endpoint - proving it, not assuming it ===")
        row = next(r for r in s3["sockets"]
                   if r["source"] == "mesh" and r["name"] == "MifTestMesh")
        mv = M.raw_post("set_property", {
            "objectPath": OWNER,
            "propertyPath": "Sockets[%d].RelativeLocation" % row["index"],
            "value": {"x": 10, "y": 20, "z": 30}})
        check("T3103 set_property MOVES a socket, aimed by list_sockets' index",
              mv.get("ok") is True, json.dumps(mv)[:250])
        moved = next(r for r in M.call("list_sockets", {"path": DME})["sockets"]
                     if r["name"] == "MifTestMesh")
        check("T3103 and the new location reads back through list_sockets",
              moved["relativeLocation"] == {"x": 10, "y": 20, "z": 30},
              json.dumps(moved["relativeLocation"]))

        before = M.call("list_sockets", {"path": DME}).get("meshSocketCount")
        rm = M.raw_post("edit_container", {"objectPath": OWNER, "propertyPath": "Sockets",
                                           "operation": "remove", "index": row["index"]})
        check("T3103 edit_container DELETES a socket, aimed the same way", rm.get("ok") is True,
              json.dumps(rm)[:250])
        after = M.call("list_sockets", {"path": DME})
        check("T3103 and the count dropped, with that name gone",
              after.get("meshSocketCount") == before - 1
              and not any(r["name"] == "MifTestMesh" for r in after["sockets"]),
              "%s -> %s" % (before, after.get("meshSocketCount")))
        print("        Both worked, so remove_socket and set_socket_transform stay unbuilt.")
    finally:
        # REVERSE ORDER, and this cost a failing cleanup before it was right. The mesh REFERENCES
        # the skeleton, so deleting the skeleton first is refused - correctly - by delete_asset,
        # which reports "0 assets removed ... the holder is an in-memory handle". `made` is built
        # skeleton-then-mesh, so cleanup walks it backwards: dependents before dependencies.
        for path in reversed(made):
            r = SC.confirm_call("delete_asset", {"path": path})
            if not r.get("ok"):
                print("        cleanup: %s -> %s" % (path, (r.get("error") or "")[:160]))
        # SCOPED TO WHAT THIS RUN MADE, not to the folder. /Game/_MifSock is shared, and an
        # earlier run's assets can still be listed there: delete_asset unregisters an asset while
        # the UObject stays resident, and the registry can re-discover it later (docs/06 #28). A
        # folder-wide assertion fails on someone else's leftovers, which is not this suite's result.
        left = [a["path"] for a in (M.call("find_assets", {"pathPrefix": "/Game/_MifSock"})
                                    .get("assets") or [])
                if any(a["path"].startswith(m) for m in made)]
        check("T3104 (cleanup) the mesh and skeleton THIS run made are gone", not left, left)

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
