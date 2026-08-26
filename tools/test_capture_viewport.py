"""capture_viewport - the pixels the editor is ACTUALLY drawing.

The whole reason this exists alongside capture_camera is that they answer DIFFERENT questions, and
conflating them has already misled someone (docs/06_OPEN_ISSUES_FROM_USE.md #7). capture_camera
spawns a transient ASceneCapture2D: its own camera, its own show flags, its own view mode. This reads
the real viewport backbuffer - the user's camera, wireframe if they left it in wireframe, the real
show flags. T194 is therefore the test with teeth: it proves the capture follows the USER's camera
rather than a spawned one, and that the two endpoints genuinely disagree when pointed apart.

The other thing worth testing is that the reported numbers describe the FILE. An endpoint that hands
back a path plus a width and height it never verified is exactly the kind of thing that reads as
success while the PNG on disk is a different size - so T190 parses the PNG's own IHDR rather than
trusting the JSON.

A note on the blank-frame guard. The first version checked for ALL BLACK. Then the first real capture
came back pale and washed out and sailed straight past it, which is what prompted the rewrite: blank
is blank whatever colour it is, so the handler now measures UNIFORMITY - the fraction of the frame
that is the single most common colour - and reports it ALWAYS, not only when it decides something is
wrong. T191 tests the invariant in both directions so the flag can never disagree with the number it
is derived from.
"""
import json
import os
import re
import struct
import sys

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def png_header(path):
    """Width and height straight out of the PNG's own IHDR - not the endpoint's word for it."""
    with open(path, "rb") as fh:
        blob = fh.read(33)
    if blob[:8] != b"\x89PNG\r\n\x1a\n" or blob[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", blob[16:24])


def size_of(path):
    return os.path.getsize(path) if path and os.path.isfile(path) else -1


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ------------------------------------------------------------------ T190 the capture
    print("\n=== T190: the capture, and whether the JSON describes the FILE ===")
    r = M.call("capture_viewport", {})
    print("  ", json.dumps({k: v for k, v in r.items() if not k.endswith("Note")})[:260])
    if not r.get("ok"):
        # A headless or minimised editor legitimately cannot answer, and must say which it is rather
        # than handing over a picture of nothing.
        check("T190 a refusal explains itself",
              "minimis" in (r.get("error") or "") or "no active editor viewport" in (r.get("error") or ""),
              (r.get("error") or "")[:200])
        print("\nno drawable viewport - the rest of this needs one")
        return 1

    f = r.get("file") or ""
    check("T190 it returns a path", bool(f), f)
    check("T190 and the file is really there", os.path.isfile(f), f)
    check("T190 the reported byte count matches the file on disk",
          size_of(f) == r.get("bytes"), "reported=%s actual=%s" % (r.get("bytes"), size_of(f)))
    hdr = png_header(f) if os.path.isfile(f) else None
    check("T190 it is a real PNG, not a renamed buffer", hdr is not None, f)
    # The point: these dimensions come out of the FILE's own IHDR, not from the endpoint's claim. A
    # size that describes something other than what was written would otherwise read as success.
    check("T190 the PNG's own dimensions match the reported ones",
          hdr == (r.get("width"), r.get("height")),
          "IHDR=%s reported=%sx%s" % (hdr, r.get("width"), r.get("height")))
    # THE ONE THAT CAUGHT THE REAL BUG. FViewport::ReadPixels returns the backbuffer's alpha channel,
    # which in the editor is not coverage - it is leftover renderer state, and it was 0 on 99.97% of
    # pixels. PNGCompressImageArray wrote it out verbatim, so the file was a FULLY TRANSPARENT PNG:
    # correct RGB underneath, every JSON field reporting success, and a blank page in any viewer that
    # honours alpha. It was misread as "the scene is empty" twice before the channel was looked at.
    # capture_camera and the thumbnail path were checked and are opaque already; this was ours alone.
    try:
        from PIL import Image
    except ImportError:
        print("  SKIP  T190 opacity - PIL not installed, so the alpha channel cannot be read here")
    else:
        im = Image.open(f)
        if "A" not in im.mode:
            check("T190 the PNG is opaque", True, "no alpha channel at all, which is fine")
        else:
            zero = im.getchannel("A").histogram()[0]
            check("T190 the PNG is OPAQUE, not a transparent sheet over a correct render",
                  zero == 0, "%d of %d pixels are fully transparent" % (zero, im.size[0] * im.size[1]))

    check("T190 it says where the pixels came from",
          r.get("source") == "editor viewport backbuffer", r.get("source"))
    # The frame was drawn for THIS call. Without that, a non-realtime viewport hands back whatever
    # was last drawn while cameraLocation reports the current camera - see T194.
    check("T190 and that the frame was drawn for this call", r.get("forcedRedraw") is True,
          r.get("forcedRedraw"))
    check("T190 and whether the viewport is realtime",
          isinstance(r.get("realtime"), bool), r.get("realtime"))
    # A non-realtime viewport only redraws when something forces it, so the frame can be older than
    # the caller expects. That has to be said, not left to be discovered.
    if r.get("realtime") is False:
        check("T190 a non-realtime viewport warns the frame may be stale", "realtimeNote" in r,
              json.dumps(sorted(r.keys())))

    # ------------------------------------------------------------------ T191 blank-frame guard
    print("\n=== T191: the blank-frame guard reports its numbers ALWAYS ===")
    for field in ("distinctColours", "uniformity", "dominantColour"):
        check("T191 %s is always reported" % field, field in r, json.dumps(sorted(r.keys())))
    u = r.get("uniformity")
    check("T191 uniformity is a fraction", isinstance(u, (int, float)) and 0.0 < u <= 1.0, u)
    check("T191 distinctColours is at least 1", (r.get("distinctColours") or 0) >= 1,
          r.get("distinctColours"))
    check("T191 dominantColour is a hex colour",
          bool(re.match(r"^#[0-9A-F]{6}$", r.get("dominantColour") or "")), r.get("dominantColour"))
    # Both directions, so the flag can never disagree with the number it is derived from. This is the
    # regression the all-black version would have failed: a pale blank frame is still blank.
    check("T191 looksBlank is set exactly when the frame IS uniform",
          bool(r.get("looksBlank")) == (isinstance(u, (int, float)) and u > 0.98),
          "looksBlank=%s uniformity=%s" % (r.get("looksBlank"), u))
    if r.get("looksBlank"):
        check("T191 and a blank frame says so in words, naming the colour",
              (r.get("dominantColour") or "") in (r.get("blankNote") or ""),
              (r.get("blankNote") or "")[:180])

    # ------------------------------------------------------------------ T192 where it writes
    print("\n=== T192: where it writes ===")
    n = M.call("capture_viewport", {"path": "MifTestShot"})
    check("T192 a custom name is honoured",
          n.get("ok") is True and os.path.basename(n.get("file") or "") == "MifTestShot.png",
          n.get("file"))
    check("T192 and it is a different file from the default",
          (n.get("file") or "") != f, "%s vs %s" % (n.get("file"), f))
    # Teeth: the name comes from a caller, so it must not be able to steer the write out of the folder.
    esc = M.call("capture_viewport", {"path": "../../../../Windows/Temp/MifEscape"})
    if esc.get("ok"):
        norm = os.path.normpath(esc.get("file") or "").replace("\\", "/").lower()
        check("T192 a traversing name cannot escape Saved/MifBridge",
              "/saved/mifbridge/" in norm, esc.get("file"))
        check("T192 and nothing landed outside the project",
              not os.path.isfile("C:/Windows/Temp/MifEscape.png"), "C:/Windows/Temp/MifEscape.png")
    else:
        check("T192 a traversing name is refused rather than written", True, esc.get("error"))
    # A caller who types the extension should not get Shot.png.png.
    d = M.call("capture_viewport", {"path": "MifTestShot.png"})
    check("T192 a supplied .png is not doubled",
          os.path.basename(d.get("file") or "") == "MifTestShot.png", d.get("file"))

    # ------------------------------------------------------------------ T193 guards
    print("\n=== T193: unknown parameters point somewhere useful ===")
    for name, payload, expect in (
        ("location", {"location": {"x": 0, "y": 0, "z": 0}}, "set_viewport_camera"),
        ("resolution", {"resolution": "1920x1080"}, "viewport's own size"),
        ("showUI", {"showUI": True}, "backbuffer"),
    ):
        q = M.call("capture_viewport", payload)
        check("T193 %s refused" % name, q.get("ok") is False, json.dumps(q)[:150])
        check("T193 %s says what to do instead" % name, expect in (q.get("error") or ""),
              (q.get("error") or "")[:200])

    # ------------------------------------------------------------------ T194 the actual point
    print("\n=== T194 [the point]: this is the USER's camera, not a spawned one ===")
    keep = M.call("get_viewport_camera", {})
    here = {"x": 542.0, "y": 0.0, "z": 400.0}
    M.call("set_viewport_camera", {"location": here, "rotation": {"x": -20.0, "y": 180.0, "z": 0.0}})
    a = M.call("capture_viewport", {"path": "MifShotA"})
    cam = a.get("cameraLocation") or {}
    check("T194 the capture reports the camera it shot from",
          all(abs((cam.get(k) or 0) - v) < 1.0 for k, v in here.items()), json.dumps(cam))
    # Cross-checked against the endpoint whose whole job is reporting that camera, so the two cannot
    # quietly drift apart.
    g = M.call("get_viewport_camera", {}).get("location") or {}
    check("T194 and get_viewport_camera agrees with it",
          all(abs((g.get(k) or 0) - (cam.get(k) or 0)) < 0.01 for k in ("x", "y", "z")),
          "%s vs %s" % (json.dumps(g), json.dumps(cam)))

    # Move the viewport and shoot again. A genuine read of the live viewport must change.
    M.call("set_viewport_camera", {"location": {"x": 542.0, "y": 0.0, "z": 1800.0},
                                   "rotation": {"x": -80.0, "y": 180.0, "z": 0.0}})
    b = M.call("capture_viewport", {"path": "MifShotB"})
    sa, sb = size_of(a.get("file")), size_of(b.get("file"))
    check("T194 moving the viewport changes what is captured", sa != sb and sa > 0 and sb > 0,
          "%d bytes vs %d - identical would mean it is not reading the live viewport" % (sa, sb))
    check("T194 and the reported camera moved with it",
          abs(((b.get("cameraLocation") or {}).get("z") or 0) - 1800.0) < 1.0,
          json.dumps(b.get("cameraLocation")))

    # The split this endpoint exists for: capture_camera is a DIFFERENT camera. Pointed somewhere
    # else entirely it must not produce the viewport's picture - and must not drag the view with it.
    cc = M.call("capture_camera", {"x": -4000.0, "y": -4000.0, "z": 2000.0,
                                   "lookAt": {"x": 0, "y": 0, "z": 0}, "name": "MifShotC"})
    if cc.get("ok"):
        check("T194 capture_camera is a genuinely different camera", size_of(cc.get("file")) != sb,
              "capture_camera=%d capture_viewport=%d" % (size_of(cc.get("file")), sb))
        after = (M.call("get_viewport_camera", {}).get("location") or {}).get("z") or 0
        check("T194 and the user's view was not dragged along to serve it",
              abs(after - 1800.0) < 1.0, "viewport z is now %s, was 1800" % after)
    else:
        check("T194 capture_camera answered at all", False, json.dumps(cc)[:180])

    # Put the user's camera back where it was found.
    if keep.get("ok"):
        M.call("set_viewport_camera", {"location": keep.get("location"),
                                       "rotation": keep.get("rotation")})

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
