"""Generate the UE half of the listing gallery, read-only, from a live editor.

WHY THE UE HALF WAS MISSING. make_demo.py produces the Blender before/after and says of itself that
a grey cylinder is not listing art; the images that would actually sell this are on the Unreal side.
Those needed a live editor and a scene worth photographing, and until 2026-09-05 there was no
project that was BOTH. The DDS2 fork has content but it is another studio's, and using it in
marketing is the same 3(g)(i) problem as shipping its name in an error string. A probe project is
ours and has nothing in it.

Curfew is both: Andre's own uncooked 5.7 game, 35,725 assets. Shots from it are ours to publish, and
they show MifBridge against a REAL project on a STOCK engine, which is the listing's whole argument -
a general UE5 tool, not a DDS2 mod utility.

=============================================================================
IT WRITES NOTHING. NOT ONE ASSET, NOT ONE ACTOR.
=============================================================================
capture_camera takes an explicit location and lookAt and renders from there, so no viewport is moved,
no actor is spawned, no level is dirtied and no package is saved. The only thing that lands on disk
is a PNG in the output directory. That matters beyond tidiness: a gallery generator that dirties
somebody's level to take its picture is one nobody will run twice, and this repo has already lost
somebody's unsaved materials once.

capture_viewport is deliberately NOT used - it photographs the CURRENT viewport, so it depends on
where the editor happens to be pointed and would need set_viewport_camera first, which is a write.

=============================================================================
IT CHECKS ITS OWN OUTPUT
=============================================================================
A generator that writes a black frame and reports success is worse than none, because nobody looks
at marketing images until they are already published. Every capture is verified as a real file with
real bytes, and rejected if it is almost entirely one colour - which is what an empty scene, a camera
inside geometry, or a failed render all produce.

Usage:
    python tools/make_ue_demo.py --out <dir> [--shots N]
"""
import argparse
import collections
import io
import json
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def png_is_interesting(path, min_bytes=20000, max_dominant=0.985):
    """(ok, why). Rejects the frames a broken capture actually produces.

    NOT a quality judgement - it cannot tell a good composition from a bad one. It answers the one
    question that can be answered mechanically: is this a picture of anything? A camera inside a
    wall, an unloaded level and a failed render all come back as a near-uniform field, and all three
    look like success to a caller that only checks the file exists.

    THE THRESHOLD IS DELIBERATELY LOOSE, and 0.92 was too strict. At that value the first run
    rejected one of make_demo's own renders - a barrel on a plain backdrop, which is legitimately
    92% one colour and is exactly the kind of image a store gallery wants. A checker that refuses
    good product shots gets switched off, and then it is not catching black frames either. What is
    caught at 0.985 is a frame that is ALL one thing, which is the failure that actually happens.
    """
    try:
        raw = io.open(path, "rb").read()
    except OSError as exc:
        return False, "could not read it back: %s" % exc
    if len(raw) < min_bytes:
        return False, "only %d bytes - too small to be a real frame" % len(raw)
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return False, "not a PNG (magic bytes are %r)" % raw[:8]

    # Decode enough to sample colours: concatenate IDAT, inflate, and read the scanlines. Done by
    # hand because this tool must not need Pillow - a gallery generator that cannot run without an
    # extra dependency is one that stops being run.
    pos, w, h, bitdepth, colortype, idat = 8, 0, 0, 0, 0, b""
    while pos + 8 <= len(raw):
        ln = struct.unpack(">I", raw[pos:pos + 4])[0]
        typ = raw[pos + 4:pos + 8]
        body = raw[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, bitdepth, colortype = struct.unpack(">IIBB", body[:10])
        elif typ == b"IDAT":
            idat += body
        elif typ == b"IEND":
            break
        pos += 12 + ln
    if not idat or bitdepth != 8 or colortype not in (2, 6):
        return True, "%dx%d, %d bytes (not sampled: unusual PNG form)" % (w, h, len(raw))

    stride = 3 if colortype == 2 else 4
    try:
        flat = zlib.decompress(idat)
    except zlib.error as exc:
        return False, "IDAT would not inflate: %s" % exc
    counts = collections.Counter()
    rowlen = w * stride + 1
    for y in range(0, h, max(1, h // 40)):          # ~40 rows is plenty to catch a flat frame
        off = y * rowlen + 1
        for x in range(0, w, max(1, w // 40)):
            i = off + x * stride
            if i + 2 < len(flat):
                # 32 LEVELS PER CHANNEL, NOT 16. At >>4 a smooth studio background collapses into
                # one bucket, and the first run of this rejected a REAL render at 92% - a product
                # shot on a plain backdrop legitimately is that uniform. >>3 keeps a gradient
                # distinguishable from a flat fill, which is the difference that matters.
                counts[(flat[i] >> 3, flat[i + 1] >> 3, flat[i + 2] >> 3)] += 1
    if not counts:
        return False, "no pixels sampled"
    top, n = counts.most_common(1)[0][1], sum(counts.values())
    frac = top / float(n)
    if frac > max_dominant:
        return False, ("%.0f%% of sampled pixels are one colour - an empty scene, a camera inside "
                       "geometry or a failed render all look like this" % (frac * 100))
    return True, "%dx%d, %d bytes, most common colour %.0f%% of samples" % (w, h, len(raw), frac * 100)


def frame_the_content(rows):
    """(target xyz, spread, kept) - where the publishable content actually is.

    THREE THINGS THIS HAS TO GET RIGHT, and the first version got none of them because it aimed at
    world origin and assumed.

    SCRATCH ACTORS ARE EXCLUDED, and that is not tidiness. A sweep spawns test fixtures into
    whatever level is open - this session left 141 of them in Curfew's map - and a store gallery
    with MifScratchCube in shot is worse than no gallery. The rule has an owner:
    mifaudit.is_scratch_fixture reads the LABEL as well as the path, which a hand-rolled
    startswith("/Game/_Mif") cannot do for a level actor, and level actors are exactly what this
    filters.

    THE MEDIAN, NOT THE MEAN OR THE BOUNDING BOX. A level's extremes are its outliers: the sky
    actor and WorldDataLayers sit at (0,0,0), a stray probe sits 50km away, and a Landscape reports
    world-partition bounds of +-2^42 - which is what made the first attempt at a bounding box come
    back as the whole float range. The median lands where the actors ARE, and a handful of far
    outliers cannot move it.

    THE SPREAD IS AN INTERQUARTILE DISTANCE for the same reason, floored so a tight cluster does not
    put the camera inside the geometry it is photographing.
    """
    import mifaudit as M
    pts = []
    for a in rows:
        if not isinstance(a, dict):
            continue
        if M.is_scratch_fixture(a):
            continue                          # this session's own fixtures are not marketing art
        loc = a.get("location") or {}
        try:
            x, y, z = float(loc["x"]), float(loc["y"]), float(loc["z"])
        except (KeyError, TypeError, ValueError):
            continue
        if max(abs(x), abs(y), abs(z)) > 1e7:
            continue                          # world-partition sentinel bounds, not a place
        pts.append((x, y, z))
    if not pts:
        return None, 0.0, 0

    def med(vals):
        s = sorted(vals)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0

    target = tuple(med([p[i] for p in pts]) for i in range(3))
    # Interquartile spread across x and y, which is how wide the content is without letting one
    # distant actor decide how far back the camera stands.
    def iqr(vals):
        s = sorted(vals)
        n = len(s)
        return s[min(n - 1, (3 * n) // 4)] - s[n // 4]
    spread = max(iqr([p[0] for p in pts]), iqr([p[1] for p in pts]), 600.0)
    return target, min(spread, 12000.0), len(pts)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, help="directory to write the images into")
    ap.add_argument("--shots", type=int, default=3, help="how many angles to try (default 3)")
    args = ap.parse_args()

    import mifaudit as M
    ok, why = M.require_sdk_bridge()
    if not ok:
        print("no usable editor: %s" % why)
        print("")
        print("This needs a live editor with content worth photographing. Curfew is the one this")
        print("was written for - MIF_PROJECT_MARKER=Curfew.uproject names it.")
        return 2

    os.makedirs(args.out, exist_ok=True)
    print("MifBridge UE gallery -> %s" % args.out)
    print("  %s" % why)

    # READ-ONLY FROM HERE. Nothing below writes an asset, spawns an actor or dirties a package.
    #
    # limit 5000 AND `matched`, NOT limit 1 and `count`. The first version asked for one row and
    # read `count`, which is the number of rows RETURNED - so it could only ever be 0 or 1, and it
    # was about to decide whether a 183-actor level was empty from a field that cannot answer that.
    # describe_endpoint says this outright ("the response reports count, matched and truncated"),
    # which is where it should have been read from the first time.
    lvl = M.raw_post("list_level_actors", {"limit": 5000}, timeout=180)
    rows = (lvl.get("actors") or []) if isinstance(lvl, dict) else []
    n_actors = lvl.get("matched") if isinstance(lvl, dict) else None
    print("  actors in the open level: %s (%d row(s) read, truncated=%s)"
          % (n_actors, len(rows), lvl.get("truncated") if isinstance(lvl, dict) else "?"))
    if not rows:
        print("")
        print("SKIPPED - the open level has no actors, so there is nothing to photograph. This is")
        print("not a failure of the capture path: point the editor at a level with content.")
        return 2

    target, spread, kept = frame_the_content(rows)
    if target is None:
        print("")
        print("SKIPPED - no non-scratch actor has a usable location, so there is nothing this can")
        print("aim at. A level holding only test fixtures is not a level worth photographing.")
        return 2
    print("  framing %d publishable actor(s) around (%.0f, %.0f, %.0f), spread %.0f uu"
          % (kept, target[0], target[1], target[2], spread))

    # Three angles rather than one, because a single camera position is a coin flip on whether it is
    # inside geometry - and the checker below can only reject a bad frame, not compose a good one.
    #
    # DERIVED FROM THE CONTENT, NOT HARDCODED. The first version aimed all three at world origin,
    # which is where a UE level's content is only by convention. Curfew's is at (95173, 99311) and
    # its origin holds nothing but the sky actor and WorldDataLayers, so every shot was a picture of
    # empty space - and the frame checker below correctly rejected all of them, which is the only
    # reason this was noticed rather than published.
    d = spread
    ANGLES = [
        {"location": {"x": target[0] + d,       "y": target[1] - d,       "z": target[2] + d * 0.6},
         "lookAt":   {"x": target[0], "y": target[1], "z": target[2]}},
        {"location": {"x": target[0] - d * 0.8, "y": target[1] - d * 0.8, "z": target[2] + d * 1.1},
         "lookAt":   {"x": target[0], "y": target[1], "z": target[2]}},
        {"location": {"x": target[0],           "y": target[1] - d * 1.4, "z": target[2] + d * 0.3},
         "lookAt":   {"x": target[0], "y": target[1], "z": target[2] + d * 0.15}},
    ][:max(1, args.shots)]

    written, rejected = [], []
    for i, angle in enumerate(ANGLES, 1):
        # `name` IS A FILENAME STEM, not a path. capture_camera writes to
        # <Project>/Saved/MifBridge/<name>.png and tells you where in the response - it does not
        # take a destination. Asked the endpoint rather than assuming a second time: the first
        # version sent a full path as `name` and then looked for the file where it had asked,
        # which is how three captures that SUCCEEDED were reported as rejected.
        want = "MifGallery_%02d" % i
        # `name`, NOT `path`. capture_camera's guard accepts x/y/z, location, rotation, lookAt,
        # useViewportCamera, fov, width, height and name - and nothing else. The first version of
        # this sent `path`, which capture_viewport takes as an alias and this endpoint does not;
        # it would have been refused by name on the first real run. Read the source rather than
        # assuming two endpoints in the same family share a spelling.
        r = M.raw_post("capture_camera", dict(angle, name=want), timeout=300)
        if not isinstance(r, dict) or r.get("ok") is False:
            print("  shot %d REFUSED: %s" % (i, str(r.get("error") if isinstance(r, dict) else r)[:150]))
            rejected.append((want, "refused"))
            continue
        # WHERE IT ACTUALLY LANDED, from the response. The field is `path`, and the response also
        # carries wroteFile and exists - so the endpoint answers "did this work" three ways and a
        # caller that trusts its own request instead is reporting on a file it never opened.
        path = r.get("path")
        if not path or r.get("wroteFile") is False or r.get("exists") is False:
            print("  shot %d REJECTED  the endpoint says it wrote nothing (path=%r wroteFile=%r)"
                  % (i, path, r.get("wroteFile")))
            rejected.append((path or want, "endpoint reported no file"))
            continue
        # AND THE ENDPOINT'S OWN VERDICT FIRST. It reports allBlack, so the plugin already answers
        # the crudest form of this question; png_is_interesting below is the wider net that also
        # catches a near-uniform frame - a camera inside geometry is not black, it is one colour.
        if r.get("allBlack"):
            print("  shot %d REJECTED  the endpoint reports allBlack" % i)
            rejected.append((path, "endpoint reported allBlack"))
            continue
        good, detail = png_is_interesting(path)
        print("  shot %d %-9s %s" % (i, "kept" if good else "REJECTED", detail))
        (written if good else rejected).append((path, detail))

    facts = {"actorsInLevel": n_actors, "kept": [p for p, _ in written],
             "rejected": [{"path": p, "why": d} for p, d in rejected]}
    io.open(os.path.join(args.out, "ue_demo_facts.json"), "w", encoding="utf-8").write(
        json.dumps(facts, indent=1))

    print("")
    if not written:
        print("NOTHING USABLE - %d shot(s) tried, all rejected. The images are not published and" % len(ANGLES))
        print("that is the point: a black frame reported as success is the failure this checks for.")
        return 1
    print("OK  %d of %d shot(s) kept. Nothing was written to the project - no actor spawned, no"
          % (len(written), len(ANGLES)))
    print("    package dirtied, no viewport moved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
