"""import_landscape_heightmap / export_landscape_heightmap - bulk terrain in one call.

WHY THESE EXIST, measured by the session that asked for them rather than assumed. sculpt_landscape
costs ~435 ms per CALL and the cost does not move with brush size - 37 vertices and 40,363 vertices
both took ~435 ms on 5.7.4. So the price of a shape is the number of CALLS: a 1450x1450 coastline
rastered sensibly is ~23,000 of them, 2.7 hours. An adaptive quadtree got it to 1,647 calls and
11.2 minutes, which is near the floor for a brush and still eleven minutes per attempt.

And a disc cannot draw a coastline at any price. flatten with falloff 0 gives vertical walls, so
every water body is a pit with sheer sides; discs stamped along a boundary leave scalloped
crescents. The geometry came out CORRECT - a transect agreed with the source classifier on 47 of 49
samples - and the result was still "very poor, and unlike natural terrain". That is not something a
better parameter fixes.

T8000 IS THE WHOLE ARGUMENT: a 2017x2017 landscape - 4,068,289 samples - exported and re-imported
with every sample identical, export 0.10s and import 1.83s. Against 11.2 minutes for the same
terrain via the brush, and that was the OPTIMISED brush path.

THE ROUND-TRIP IS EXACT BY DESIGN, and T8001 is what proves the design rather than the luck. Height
is stored as uint16 natively - 32768 is the actor's own Z - so with no minZ/maxZ the samples pass
through untouched in both directions. Nothing is normalised, so nothing is lost. minZ/maxZ exist
for the other case, a 0..65535 image being mapped onto a world Z range, and they are required
TOGETHER because half a mapping silently rescales the terrain.

T8002 IS THE ONE THAT WOULD HURT MOST IF IT REGRESSED. Heightfield collision is cooked separately
from the render surface. A write that skips RecreateCollisionComponents leaves terrain that renders
as hills and traces as dead flat - so anything placed by line trace goes to the wrong height, and
nothing about the visible result says so. sculpt_landscape documents this at its own write; these
endpoints run the same tail, and this asserts a trace agrees with the heightmap afterwards.
"""
import base64
import json
import math
import os
import struct
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
    if M.write_mode() == "read":
        print("SKIPPED - read mode")
        return 0

    # A LANDSCAPE THESE ENDPOINTS CAN ACTUALLY WRITE TO, chosen rather than assumed.
    #
    # Both writers edit the MERGED heightmap with no FScopedSetLandscapeEditingLayer, so on a
    # landscape with sculpt edit layers the next composite regenerates it from the layers and
    # discards the write. They refuse that landscape now. Before they did, this suite ran against
    # whatever terrain the level happened to hold - here, one with two edit layers - and its
    # round-trip assertions passed only because they re-exported inside the window before the
    # composite ran. Vacuous, and invisible from inside the suite.
    #
    # create_landscape leaves edit layers OFF deliberately, so it produces the one kind of
    # landscape these endpoints support. Prefer an existing suitable one; build a scratch one only
    # if the level has none.
    made_landscape = None
    target = None
    for row in (M.call("landscape_info", {}).get("landscapes") or []):
        if not row.get("editLayers"):
            target = row.get("actorPath")
            print("using the level's own non-layered landscape: %s" % row.get("label"))
            break
    if target is None:
        st = int(time.time()) % 100000
        mk = M.raw_post("create_landscape", {"location": {"x": 300000, "y": 300000, "z": 0},
                                             "componentsX": 2, "componentsY": 2,
                                             "quadsPerSection": 63, "sectionsPerComponent": 1,
                                             "heightMode": "rolling", "amplitude": 2000,
                                             "label": "MifHeightmapFixture%d" % st})
        check("T7999 (setup) a scratch landscape is created, because every landscape in this "
              "level has edit layers and these endpoints cannot write to one",
              mk.get("ok") is True, json.dumps(mk)[:250])
        target = mk.get("actorPath") or ((mk.get("landscape") or {}) or {}).get("actorPath")
        made_landscape = target
        if not target:
            print("SKIPPED - could not build a landscape to test against.")
            return 2

    # THE PRECONDITION, ASSERTED. If this fixture ever gains edit layers the writers will refuse
    # and every round-trip below would fail confusingly; better to say why here.
    chosen = next((r for r in (M.call("landscape_info", {}).get("landscapes") or [])
                   if r.get("actorPath") == target), {})
    check("T7999 (setup) the landscape under test has NO sculpt edit layers, which is the only "
          "kind these endpoints can write to without the composite discarding it",
          not chosen.get("editLayers"), json.dumps(chosen.get("editLayers"))[:200])

    def lcall(endpoint, payload):
        """Every call names its landscape - there are now two in the level."""
        body = dict(payload)
        body.setdefault("landscape", target)
        return M.raw_post(endpoint, body)

    probe = lcall("export_landscape_heightmap", {})
    if probe.get("ok") is not True:
        print("SKIPPED - no landscape in the open level, so nothing was verified.")
        print("  (%s)" % (probe.get("error") or "")[:160])
        return 2

    W = probe["area"]["width"]
    H = probe["area"]["height"]
    print("landscape %dx%d = %d samples" % (W, H, W * H))

    # ------------------------------------------------------------------ T8000 the point
    print("\n=== T8000: the whole map in one call, and fast enough to iterate ===")
    t0 = time.time()
    e = lcall("export_landscape_heightmap", {"asData": True})
    dt_export = time.time() - t0
    check("T8000 export succeeds", e.get("ok") is True, json.dumps(e)[:220])
    check("T8000 it returns every sample, not a subset",
          e.get("samples") == W * H, "%s vs %d" % (e.get("samples"), W * H))
    check("T8000 and writes a file that is on disk at the size the samples imply",
          bool(e.get("file")) and os.path.exists(e["file"])
          and os.path.getsize(e["file"]) == W * H * 2,
          "%s -> %s" % (e.get("file"), e.get("bytes")))
    # The speed IS the feature. A brush needed 11.2 minutes for this; anything in seconds is a
    # different kind of tool, so it is asserted rather than merely mentioned.
    check("T8000 export of %d samples in under 10s (was 11.2 MINUTES via the brush)" % (W * H),
          dt_export < 10.0, "%.2fs" % dt_export)

    # ------------------------------------------------------------------ T8001 exact round-trip
    print("\n=== T8001: a round-trip changes nothing, because nothing is normalised ===")
    sent = base64.b64decode(e["data"])
    check("T8001 the base64 carries two bytes per sample",
          len(sent) == W * H * 2, "%d bytes for %d samples" % (len(sent), W * H))
    r = lcall("import_landscape_heightmap", {"file": e["file"]})
    check("T8001 re-importing the exported file succeeds", r.get("ok") is True,
          json.dumps(r)[:250])
    # THE assertion, and it is the endpoint's own postcondition rather than the suite's opinion:
    # every sample was read back off the landscape and compared.
    check("T8001 and the endpoint verified every sample against the landscape itself",
          "matches what was sent" in (r.get("verified") or ""), r.get("verified"))
    check("T8001 with no remap, since the native storage is already uint16",
          r.get("remapped") is False, r.get("remapped"))

    back = lcall("export_landscape_heightmap", {"asData": True})
    check("T8001 a SECOND export is byte-identical to the first - the proof the round-trip is "
          "lossless rather than merely reported as such",
          base64.b64decode(back.get("data") or "") == sent,
          "second export differs from the first")

    # ------------------------------------------------------------------ T8002 collision
    print("\n=== T8002: the walkable surface follows the visible one ===")
    # Write a shape a disc brush cannot make: a radial island with ridged undulation.
    cx, cy, R = W / 2.0, H / 2.0, min(W, H) * 0.42
    buf = bytearray(W * H * 2)
    for y in range(H):
        for x in range(W):
            d = math.hypot(x - cx, y - cy) / R
            h = max(0.0, 1.0 - d * d) + 0.06 * math.sin(x * 0.05) * math.cos(y * 0.047)
            struct.pack_into("<H", buf, (y * W + x) * 2,
                             int(max(0.0, min(1.0, 0.35 + 0.45 * h)) * 65535))
    t0 = time.time()
    w = lcall("import_landscape_heightmap",
                   {"data": base64.b64encode(bytes(buf)).decode(), "width": W, "height": H})
    dt_import = time.time() - t0
    check("T8002 a generated heightmap imports", w.get("ok") is True, json.dumps(w)[:250])
    # THE THRESHOLD IS THE ALTERNATIVE, NOT A STOPWATCH READING. The first version asserted 30s
    # from a single warm measurement of 1.83s - and a later run took 55.6s, a 30x spread, because
    # RecreateCollisionComponents on a 64-proxy World Partition landscape varies enormously with
    # editor state. Asserting a number measured once is the same brittleness as a test that depends
    # on which asset find_assets returns first.
    #
    # What is actually being claimed is that this beats the brush by orders of magnitude: the same
    # terrain took 11.2 MINUTES via an optimised quadtree of 1,647 sculpt calls. 120s keeps a wide
    # margin over the observed 1.8-55.6s range and still fails loudly if this ever regresses into
    # brush territory.
    check("T8002 import of %d samples well inside the 11.2 MINUTES the brush needed "
          "(observed 1.8-55.6s; collision rebuild dominates and varies with editor state)" % (W * H),
          dt_import < 120.0, "%.2fs" % dt_import)

    got = base64.b64decode(
        (lcall("export_landscape_heightmap", {"asData": True}) or {}).get("data") or "")
    check("T8002 and a fresh export returns exactly what was sent - independent of the endpoint's "
          "own verification",
          got == bytes(buf), "re-export differs from the data sent")

    # THE COLLISION ASSERTION, and it is the reason this suite exists as much as the speed is.
    # Heightfield collision is cooked separately from the render surface, so a write that skips
    # RecreateCollisionComponents leaves terrain that renders as hills and traces as dead flat -
    # and nothing visible betrays it. Anything placed by tracing then goes to the wrong height.
    #
    # BOTH SIDES ARE MEASUREMENTS. The trace is compared against a FRESH EXPORT, not against the
    # buffer this suite sent: if an import silently no-oped, the sent buffer would still describe
    # the terrain the test expected, and the check would pass. Sampling the export closes that.
    # THE LANDSCAPE UNDER TEST, not "the first one". This read [0] and there is now more than one
    # in the level - it took the other landscape's bounds, mapped them onto THIS landscape's
    # heightmap indices, and traced world points that land on the other terrain entirely. Worst
    # delta 15707uu, which reads as a collision bug and was a fixture bug.
    li = next((r for r in (M.call("landscape_info", {}).get("landscapes") or [])
               if r.get("actorPath") == target), {})
    wmin, wmax = li.get("worldMin") or {}, li.get("worldMax") or {}
    fresh = lcall("export_landscape_heightmap", {"asData": True})
    zdata = base64.b64decode(fresh.get("data") or "")
    z0 = fresh.get("worldZAtZero")
    span = (fresh.get("worldZAtMax") or 0) - (z0 or 0)

    # PROBE POINTS FROM THE LANDSCAPE'S OWN EXTENT. These were hardcoded world coordinates -
    # (0,0), (50000,0), (-80000,-80000) - which only land on terrain centred near the world origin
    # at roughly the size of this project's own landscape. Any other landscape, in any other
    # project, and every probe falls outside it. Quarter/half/three-quarter across works anywhere.
    # ON VERTICES, not at arbitrary fractions. The trace returns the INTERPOLATED surface; the
    # heightmap sample is a vertex. A probe that lands between vertices compares two different
    # things and is off by however much the terrain slopes across one quad - 533uu here on rolling
    # terrain, which looks exactly like a collision bug. The old hardcoded coordinates happened to
    # sit on vertices of this project's landscape, so the difference never showed. Deriving the
    # world point FROM the vertex index keeps them aligned on any landscape.
    probe_pts = []
    if wmin and wmax and wmax.get("x") != wmin.get("x"):
        for vi, vj in ((W // 4, H // 4), (W // 2, H // 2), (3 * W // 4, 3 * H // 4)):
            probe_pts.append((wmin["x"] + (wmax["x"] - wmin["x"]) * vi / float(W - 1),
                              wmin["y"] + (wmax["y"] - wmin["y"]) * vj / float(H - 1)))
    agreed, tried, worst = 0, 0, 0.0
    for wx, wy in probe_pts:
        if not wmin or wmax.get("x") == wmin.get("x"):
            break
        vx = int(round((wx - wmin["x"]) / (wmax["x"] - wmin["x"]) * (W - 1)))
        vy = int(round((wy - wmin["y"]) / (wmax["y"] - wmin["y"]) * (H - 1)))
        if not (0 <= vx < W and 0 <= vy < H):
            continue
        sample = struct.unpack_from("<H", zdata, (vy * W + vx) * 2)[0]
        want_z = z0 + span * (sample / 65535.0)
        tr = M.raw_post("trace_ground", {"x": wx, "y": wy})
        # A MISSING ENDPOINT MUST NOT LOOK LIKE A MISS. The first version of this called
        # `line_trace`, which does not exist here, and its else branch printed "did not hit" - so a
        # typo'd endpoint name read as an unexercised arm and the whole check silently never ran.
        if tr.get("ok") is False and "not an endpoint" in (tr.get("error") or ""):
            check("T8002 the trace endpoint exists", False, tr.get("error"))
            break
        tried += 1
        if tr.get("hit"):
            delta = abs((tr.get("z") or 0) - want_z)
            worst = max(worst, delta)
            if delta < 1.0:
                agreed += 1
    if tried:
        check("T8002 the collision surface agrees with the heightmap at every probed point "
              "(worst delta %.2fuu) - collision was rebuilt, not left behind" % worst,
              agreed == tried, "%d of %d points agreed, worst delta %.2f" % (agreed, tried, worst))
        # A landscape that is flat everywhere would pass the above trivially, so require that at
        # least one probe sat at a real elevation.
        check("T8002 and at least one probe was at a non-zero height, so the agreement is not "
              "the trivial one on flat ground",
              worst >= 0.0 and any(
                  struct.unpack_from("<H", zdata,
                                     (int(round((wy - wmin["y"]) / (wmax["y"] - wmin["y"]) * (H - 1))) * W
                                      + int(round((wx - wmin["x"]) / (wmax["x"] - wmin["x"]) * (W - 1)))) * 2)[0]
                  not in (0, 32768)
                  for wx, wy in probe_pts),
              "every probed sample sat at the zero height")
    else:
        check("T8002 at least one trace probe landed on the landscape", False,
              "no probe hit - collision cannot be verified, which is not the same as it being fine")

    # ------------------------------------------------------------------ T8003 the refusals
    print("\n=== T8003: what it refuses, and whether the reason is usable ===")
    tiny = base64.b64encode(bytes(8)).decode()
    cases = [
        ("both file and data", {"file": "x.r16", "data": "AA=="}, "exactly one"),
        ("neither", {}, "exactly one"),
        ("data with no dimensions", {"data": tiny}, "width and height are required"),
        ("minZ without maxZ", {"data": tiny, "width": 2, "height": 2, "minZ": 0},
         "must be given together"),
        ("a region outside the landscape", {"data": tiny, "width": 2, "height": 2, "x0": 999999},
         "falls outside"),
        ("a byte count that does not match", {"data": base64.b64encode(bytes(6)).decode(),
                                              "width": 2, "height": 2}, "must be exactly"),
    ]
    for label, payload, want in cases:
        rr = lcall("import_landscape_heightmap", payload)
        check("T8003 %s is refused, saying why" % label,
              rr.get("ok") is False and want in (rr.get("error") or ""),
              (rr.get("error") or "")[:200])

    # Named refusals: the two shapes a caller is most likely to reach for.
    lay = lcall("import_landscape_heightmap", {"data": "AA==", "width": 1, "height": 1,
                                                    "layer": "L"})
    check("T8003 an edit layer is refused BY NAME rather than silently written to the merged result",
          lay.get("ok") is False and "edit layers" in (lay.get("error") or ""),
          (lay.get("error") or "")[:220])
    fl = lcall("import_landscape_heightmap", {"heights": [1.0, 2.0]})
    check("T8003 a float array is refused by name, with the size that makes it a bad idea",
          fl.get("ok") is False and "25 MB" in (fl.get("error") or ""),
          (fl.get("error") or "")[:220])

    check("T8003 - the editor is still alive", M.call("self_audit", {}).get("ok") is True,
          "a bad stride or an over-long write into landscape height data is not a survivable error")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
