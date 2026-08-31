"""The thumbnail family - four endpoints named in no suite, and it comes back clean.

Recorded as a result rather than quietly not mentioned, the same way test_interfaces records its own
clean sweep. Two of the families hunted this session had real bugs; this one does not, and this suite
exists so that stays true.

WHY THIS FAMILY WAS WORTH HUNTING. It is rendering code, and rendering is where capture_viewport hid
TWO silent failures at once - it returned a byte-identical image after the camera moved (the backbuffer
was stale) and wrote a PNG whose every pixel was transparent (the alpha channel was leftover renderer
state, not coverage). Both reported ok:true with plausible-looking JSON. Mod icons run through this
family, so the same failures would be just as invisible and just as costly here.

The good news is that the lessons are already built in: render_thumbnail returns an ALPHA HISTOGRAM
with the render, and its note explains why a cut-out icon is not available from this path at all
(the engine thumbnail renderers clear to opaque black and draw a lit preview scene). That is the
capture_viewport bug being answered before it can be asked.

So the sharp test here is the OTHER half - the stale-frame half:

    T401 renders the same asset four ways and hashes the files. If orbitYaw, orbitPitch and orbitZoom
    do not each change the bytes, they are being accepted and ignored, which is exactly how the
    viewport capture failed.

and the one specific to writing a texture:

    T402 asks whether the written texture has real SOURCE pixels or is a header-only stub. A stub
    would create fine, report fine, and render black forever. set_texture_settings already refuses a
    source-less texture with an explicit message, so it doubles as the probe - if it refuses, the
    write produced a shell.

SAFETY - AND A CORRECTION. This used to say the texture 'lives in memory only'. It does not:
write_thumbnail_texture writes a real .uasset under /Game/_MifThumb, because writing a texture is what
it does. Stripping `save` does not help - the DENY list blocks endpoints NAMED like a save, and this
one has the effect without the name (issue Q). The suite now deletes what it creates, through
delete_asset so the running editor releases its references properly.
"""
import hashlib
import json
import os
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def sha_of(path):
    if not path or not os.path.isfile(path):
        return None
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]



def cleanup_scratch(prefix):
    """Delete every asset this suite wrote under `prefix`, through the editor.

    delete_asset rather than removing .uasset files from disk: the editor is running and holds
    references to them, and pulling files out from under it leaves a confused editor and a
    half-populated Asset Registry. Refuses to touch anything that is not a scratch path - the guard
    matters more than the tidiness.
    """
    import scratch_confirm as SC
    removed = 0
    for a in (M.call("find_assets", {"pathPrefix": prefix, "limit": 500}, timeout=120).get("assets") or []):
        path = a.get("path") or ""
        if not path.startswith("/Game/_Mif"):
            print("  cleanup REFUSED a non-scratch path: %s" % path)
            continue
        try:
            if SC.confirm_call("delete_asset", {"path": path}).get("ok"):
                removed += 1
        except Exception:
            pass
    print("  cleanup: removed %d asset(s) from %s" % (removed, prefix))

def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    # A MESH THAT CAN ACTUALLY SHOW A ROTATION, not whichever one find_assets returns first.
    #
    # Taking [0] drew /Game/UltraDynamicSky/Meshes/Rainbow on 2026-08-31 and failed both passes of
    # the sweep with "orbitYaw ... identical to the base render". The endpoint was fine - measured
    # side by side, that sky mesh gives yaw90 IDENTICAL / pitch60 DIFFERS / zoom1000 IDENTICAL while
    # PH_HumanGizmoLowPoly gives DIFFERS on all three. A rotationally symmetric mesh looks the same
    # from 90 degrees around, and orbitZoom is an absolute DISTANCE OFFSET (see the note at T401), so
    # 1000uu against something framed from tens of thousands away moves nothing visible.
    #
    # pick_system() in test_niagara_params already warned about exactly this - "whichever asset
    # find_assets happens to return first is a coin flip ... that already burned test_material_params"
    # - and the warning had not been applied here.
    #
    # SELECTED BY THE PROPERTY THE TEST NEEDS: a candidate is accepted only once a yaw render is
    # shown to differ from its base. The fixture cannot silently stop being suitable, because
    # suitability is what chooses it.
    candidates = [a.get("path") for a in
                  (M.call("find_assets", {"class": "StaticMesh", "pathPrefix": "/Game/",
                                          "limit": 40}).get("assets") or []) if a.get("path")]
    check("a real static mesh was found to render", bool(candidates), "no StaticMesh in /Game/")
    if not candidates:
        return 1

    mesh, sha_base, base = None, None, None
    for cand in candidates[:8]:               # bounded: each probe is a real render
        probe_base = M.call("render_thumbnail", {"asset": cand, "width": 128, "height": 128,
                                                 "name": "t401_pick_%d" % st}, timeout=180)
        b = sha_of(probe_base.get("pngPath"))
        probe_yaw = M.call("render_thumbnail", {"asset": cand, "width": 128, "height": 128,
                                                "name": "t401_picky_%d" % st, "orbitYaw": 90},
                           timeout=180)
        if b and sha_of(probe_yaw.get("pngPath")) not in (None, b):
            mesh, sha_base, base = cand, b, probe_base
            break
    if mesh:
        print("   using %s (yaw-sensitive, so the orbit assertions can mean something)" % mesh)
    else:
        mesh = candidates[0]
        print("   NOTE  no mesh among the first 8 changes under a 90-degree yaw. Using %s, and the"
              % mesh)
        print("         orbit assertions below will report UNEXERCISED rather than fail - a project")
        print("         of symmetric meshes is not a defect in render_thumbnail.")

    # ------------------------------------------------------------------ T400 capabilities
    print("")
    print("=== T400: can this editor render at all, and does it say so honestly? ===")
    cap = M.call("thumbnail_capabilities", {})
    check("T400 capabilities answers", cap.get("ok") is True, json.dumps(cap)[:170])
    check("T400 and reports whether the RHI is up", isinstance(cap.get("rhiInitialized"), bool),
          json.dumps(cap)[:170])
    # A size range that is reported is a size range that can be tested against; one that is not
    # leaves the caller guessing what a valid request looks like.
    check("T400 and states its size bounds", (cap.get("minSize") or 0) > 0 and (cap.get("maxSize") or 0) > 0,
          "min=%s max=%s" % (cap.get("minSize"), cap.get("maxSize")))

    # ------------------------------------------------------------------ T401 the stale-frame lens
    print("")
    print("=== T401 [the point]: do the orbit parameters actually change the image? ===")

    def render(label, **kw):
        payload = {"asset": mesh, "width": 128, "height": 128, "name": "t401_%s_%d" % (label, st)}
        payload.update(kw)
        r = M.call("render_thumbnail", payload, timeout=180)
        return r, sha_of(r.get("pngPath"))

    if base is None:                          # no yaw-sensitive candidate - render one anyway
        base, sha_base = render("base")
    check("T401 a thumbnail renders", base.get("ok") is True, json.dumps(base)[:200])
    check("T401 and the PNG really exists on disk", base.get("pngExists") is True and sha_base,
          "pngPath=%s pngExists=%s" % (base.get("pngPath"), base.get("pngExists")))
    check("T401 and it is not a trivially small file", (base.get("pngBytes") or 0) > 1000,
          "pngBytes=%s - a uniform image compresses to almost nothing" % base.get("pngBytes"))

    # THE assertion. capture_viewport returned a byte-identical image after the camera moved, and
    # reported the new camera position in the JSON while doing it. Hashing the files is the only thing
    # that would have caught it, so it is what is done here.
    # orbitZoom is 1000, not 2. USceneThumbnailInfo::OrbitZoom is an absolute DISTANCE OFFSET in world
    # units added to the computed camera distance - not a zoom factor - so on a mesh framed from
    # hundreds of units away a value of 2 moves the camera by a fraction of a pixel and the PNG comes
    # back byte-identical. This test used 2.0 and passed on a COLD editor for an unrelated reason
    # (first-render warm-up), then failed on the second pass in the same session. It was the test that
    # was wrong; the parameter works, at a magnitude that matches its units.
    for label, kw in (("orbitYaw", {"orbitYaw": 90}),
                      ("orbitPitch", {"orbitPitch": 60}),
                      ("orbitZoom", {"orbitZoom": 1000.0})):
        r, sha = render(label, **kw)
        if sha_base is None:
            print("  NOTE  %s is UNEXERCISED - no yaw-sensitive fixture was found." % label)
            continue
        check("T401 %s changes the rendered bytes" % label,
              bool(sha) and sha != sha_base,
              "identical to the base render. The fixture was chosen for yaw sensitivity, so this "
              "is the parameter being ignored rather than a symmetric mesh - %s" % mesh)

    # ------------------------------------------------------------------ T401b alpha, stated honestly
    print("")
    print("=== T401b: the alpha channel is measured, not assumed ===")
    alpha = base.get("alpha") or {}
    check("T401b the render reports an alpha histogram", bool(alpha), json.dumps(base)[:170])
    # The endpoint may legitimately be opaque - engine thumbnail renderers clear to opaque black. What
    # matters is that it MEASURED it, because the viewport capture's transparent PNG was invisible
    # until somebody looked at the alpha channel specifically.
    check("T401b with real counts rather than a flag",
          isinstance(alpha.get("fullyOpaquePixels"), (int, float))
          and isinstance(alpha.get("fullyTransparentPixels"), (int, float)),
          json.dumps(alpha)[:200])
    total = 128 * 128
    counted = (alpha.get("fullyOpaquePixels") or 0) + (alpha.get("fullyTransparentPixels") or 0)
    check("T401b and the counts are for the image that was asked for", counted <= total,
          "counted %s pixels for a %d-pixel image" % (counted, total))

    # ------------------------------------------------------------------ T402 a texture with pixels
    print("")
    print("=== T402: write_thumbnail_texture must produce pixels, not a header ===")
    out = "/Game/_MifThumb/T_Icon_%d" % st
    w = M.call("write_thumbnail_texture", {"asset": mesh, "outputPath": out,
                                           "width": 128, "height": 128}, timeout=180)
    check("T402 the texture is created", w.get("ok") is True, json.dumps(w)[:200])
    tpath = w.get("texturePath") or out
    check("T402 and it reports where it went", bool(tpath), json.dumps(w)[:170])

    # A texture with no SOURCE data creates fine, reports fine, and renders black forever.
    # set_texture_settings refuses exactly that case by name, so it is the probe.
    probe = M.call("set_texture_settings", {"path": tpath, "srgb": True})
    stub = "no texture source data" in (probe.get("error") or "")
    check("T402 the written texture has real source pixels", not stub,
          "set_texture_settings reports it as a header-only stub: %s" % (probe.get("error") or "")[:150])
    check("T402 and it is a Texture2D", probe.get("class") in (None, "Texture2D"),
          json.dumps(probe)[:170])

    # ------------------------------------------------------------------ T403 guards
    print("")
    print("=== T403: bad requests are refused, not rendered ===")
    q = M.call("render_thumbnail", {"asset": "/Game/NoSuchAsset_zz", "width": 64, "height": 64})
    check("T403 an asset that does not exist is refused", q.get("ok") is False, json.dumps(q)[:170])
    check("T403 and says something usable", len(q.get("error") or "") > 15, (q.get("error") or "")[:150])

    # An oversized request is CLAMPED rather than refused, which is the friendlier choice for a size -
    # the caller still gets an image. What matters is that it is not clamped SILENTLY, and it is not:
    # requestedWidth/requestedHeight and sizeNote all come back. Asserted here because the reporting
    # is the whole difference between this being helpful and being the add_component bug again.
    #
    # (This test first asserted a REFUSAL and failed. That was the test being wrong: an earlier probe
    # printed only ok/width/height/pngBytes and concluded the clamp was silent, when the response had
    # said so all along in fields it had not looked at.)
    maxs = cap.get("maxSize") or 2048
    big = maxs * 2
    q = M.call("render_thumbnail", {"asset": mesh, "width": big, "height": big,
                                    "name": "t403_big_%d" % st}, timeout=240)
    check("T403 an oversized request still renders", q.get("ok") is True, json.dumps(q)[:170])
    check("T403 clamped to the stated ceiling", q.get("width") == maxs and q.get("height") == maxs,
          "asked %d, got %sx%s, capabilities says maxSize=%s" % (big, q.get("width"), q.get("height"), maxs))
    check("T403 and the response echoes what was ASKED for", q.get("requestedWidth") == big,
          "requestedWidth=%s - without it a caller cannot tell the size was changed"
          % q.get("requestedWidth"))
    check("T403 and says plainly that it clamped", "clamp" in (q.get("sizeNote") or "").lower(),
          "sizeNote=%r" % (q.get("sizeNote") or ""))

    # The floor, for the same reason.
    mins = cap.get("minSize") or 8
    q = M.call("render_thumbnail", {"asset": mesh, "width": 1, "height": 1,
                                    "name": "t403_tiny_%d" % st}, timeout=120)
    check("T403 an undersized request is clamped up to the floor", q.get("width") == mins,
          "asked 1, got %s, capabilities says minSize=%s" % (q.get("width"), mins))
    check("T403 and says so too", "clamp" in (q.get("sizeNote") or "").lower(),
          "sizeNote=%r" % (q.get("sizeNote") or ""))
    check("T403 the editor survived all of it", M.bridge_responsive() is True,
          "the bridge stopped answering")

    cleanup_scratch("/Game/_MifThumb/")

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
