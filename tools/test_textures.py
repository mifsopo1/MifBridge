"""import_texture and set_texture_settings - the endpoints a retexture mod is made of.

Named in no suite, and worth covering for what they are used FOR: replacing an image in a cooked game
is the most ordinary thing a DDS2 modder does, and both halves of it fail quietly when they fail.

THE FAILURE THIS FAMILY IS SHAPED AROUND. A Texture2D can exist, load, report its class, answer every
question you ask it, and have NO SOURCE PIXELS - a header-only stub. It renders black forever. Worse,
changing its settings rebuilds platform data FROM the source, so a settings call on a stub does
nothing and says ok. set_texture_settings already refuses exactly that case by name, which is why it
doubles as the probe here: if it refuses, the import produced a shell rather than an image.

What each test asks:

  T410  does an imported texture really carry the pixels it was given - and are the DECODED dimensions
        the ones the source actually had, rather than the ones that were requested?
  T411  does set_texture_settings distinguish "applied" from "was already that value"? A flag that is
        always true carries no information, and this endpoint reports `changed` as a LIST, which is
        only worth having if it is ever empty.
  T412  the stub refusal itself, which is the guard the other two lean on.
  T413  guards - a missing file, an unreadable payload, a path that is not a texture.

SAFETY - AND A CORRECTION. This block used to claim every texture here 'lives in memory only under
/Game/_MifTex and vanishes when the editor restarts'. THAT WAS FALSE, and believing it is why nobody
looked: import_texture WRITES A .uasset TO DISK, because that is what importing a texture IS. There is
no in-memory mode to ask for. Stripping `save` stops nothing here - the DENY list blocks endpoints
NAMED like a save, and this one has the effect without the name. An overnight run left 98 real assets
in the project's Content tree before anyone noticed (issue Q).

So the suite now DELETES WHAT IT CREATES, at the end, through delete_asset - which lets the running
editor release its references rather than having files pulled out from under it. That does leave the
overwrite/refill path - the one that refills an EXISTING texture object so references survive -
untested, and it is named at the end rather than left as a silent gap.
"""
import json
import os
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def is_stub(texture_path):
    """True when the texture has no source pixels. set_texture_settings names that case explicitly."""
    r = M.call("set_texture_settings", {"path": texture_path, "srgb": True})
    return "no texture source data" in (r.get("error") or ""), r



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

    # A source image of a size chosen HERE, so the decoded dimensions can be checked against something
    # known rather than against whatever the endpoint reports about itself.
    mesh = (M.call("find_assets", {"class": "StaticMesh", "pathPrefix": "/Game/", "limit": 1})
            .get("assets") or [{}])[0].get("path")
    check("a mesh exists to make a source image from", bool(mesh), "no StaticMesh in /Game/")
    if not mesh:
        return 1
    src = M.call("render_thumbnail", {"asset": mesh, "width": 64, "height": 64,
                                      "name": "tex_src_%d" % st}, timeout=180).get("pngPath")
    check("a 64x64 source PNG exists on disk", bool(src) and os.path.isfile(src), str(src))
    if not src or not os.path.isfile(src):
        return 1

    # ------------------------------------------------------------------ T410 the import
    print("")
    print("=== T410: an imported texture must carry the pixels it was given ===")
    dest = "/Game/_MifTex/T_Imp_%d" % st
    r = M.call("import_texture", {"sourcePath": src, "destPath": dest}, timeout=180)
    check("T410 the import succeeds", r.get("ok") is True, json.dumps(r)[:220])
    tpath = r.get("texturePath") or r.get("objectPath") or dest
    check("T410 and it says where the texture went", bool(tpath), json.dumps(r)[:170])

    # The endpoint DECODES the payload and reports what it found. Checked against the size the source
    # was actually made at - a decode that reports the requested size rather than the real one would
    # be exactly the kind of self-agreeing answer that proves nothing.
    check("T410 the decoded width matches the real source", r.get("decodedWidth") == 64,
          "decodedWidth=%s for a 64x64 source" % r.get("decodedWidth"))
    check("T410 the decoded height matches the real source", r.get("decodedHeight") == 64,
          "decodedHeight=%s for a 64x64 source" % r.get("decodedHeight"))
    check("T410 and it identifies the format it decoded", (r.get("imageFormat") or "").upper() == "PNG",
          "imageFormat=%s" % r.get("imageFormat"))
    check("T410 and reports how many bytes it actually read",
          (r.get("payloadBytes") or 0) == os.path.getsize(src),
          "payloadBytes=%s, file is %d bytes" % (r.get("payloadBytes"), os.path.getsize(src)))

    # THE assertion. Everything above could be true of a texture with no pixels in it.
    stub, probe = is_stub(tpath)
    check("T410 the imported texture has REAL source pixels, not a header", not stub,
          "set_texture_settings reports a stub: %s" % (probe.get("error") or "")[:150])
    check("T410 and it is a Texture2D", probe.get("class") in (None, "Texture2D"), json.dumps(probe)[:150])

    # ------------------------------------------------------------------ T411 applied vs already-set
    print("")
    print("=== T411: 'applied' and 'was already that value' are different answers ===")
    # compressionSettings:"UserInterface2D" is the DETAILS PANEL name for TC_EditorIcon, and it is what
    # this endpoint's own help text tells callers to use for icons. The reflection parser matched only
    # AUTHORED names, so following that advice was an error - found by this test on its first run.
    # It now takes display names as a second pass, and T411b below pins that down.
    a = M.call("set_texture_settings", {"path": tpath, "lodGroup": "UI",
                                        "compressionSettings": "UserInterface2D",
                                        "neverStream": True})
    check("T411 a settings change succeeds", a.get("ok") is True, json.dumps(a)[:200])
    check("T411 and names what it changed", isinstance(a.get("changed"), list),
          "changed=%s" % json.dumps(a.get("changed"))[:120])
    first = list(a.get("changed") or [])
    check("T411 the first call changed something", len(first) > 0,
          "changed=[] on a texture that was freshly imported with defaults")

    # The same call again. `changed` must now be EMPTY - a list that is always populated tells the
    # caller nothing, which is the same defect as an alwaysTrue flag.
    b = M.call("set_texture_settings", {"path": tpath, "lodGroup": "UI",
                                        "compressionSettings": "UserInterface2D",
                                        "neverStream": True})
    check("T411 the identical second call still succeeds", b.get("ok") is True, json.dumps(b)[:170])
    check("T411 and reports that it changed NOTHING", (b.get("changed") or []) == [],
          "changed=%s on a repeat of the same settings - the field carries no information if it is "
          "never empty" % json.dumps(b.get("changed"))[:120])

    # ------------------------------------------------------------------ T411b names from the UI
    print("")
    print("=== T411b: the name shown in the Details panel is accepted, not just the authored one ===")
    # All four of these are TC_EditorIcon. The authored name is what reflection stores; the display
    # name is what a person reading the editor UI will type. Both have to work, or the help text is a
    # trap - and write_thumbnail_texture already accepted the alias, so the two parsers disagreed
    # about the same concept.
    for spelling in ("TC_EditorIcon", "EditorIcon", "UserInterface2D", "User Interface 2D"):
        q = M.call("set_texture_settings", {"path": tpath, "compressionSettings": spelling})
        check("T411b %r is accepted" % spelling, q.get("ok") is True, json.dumps(q)[:170])
    # They must all mean the SAME thing. Setting it four ways in a row leaves `changed` empty after
    # the first, which is the evidence they resolved to one value rather than four.
    q = M.call("set_texture_settings", {"path": tpath, "compressionSettings": "EditorIcon"})
    check("T411b and they all resolve to the same value", (q.get("changed") or []) == [],
          "changed=%s after setting the same compression four different ways - they are not the same "
          "value" % json.dumps(q.get("changed"))[:110])
    q = M.call("set_texture_settings", {"path": tpath, "compressionSettings": "NotARealCompression_zz"})
    check("T411b a genuinely unknown value is still refused", q.get("ok") is False, json.dumps(q)[:150])
    check("T411b and the refusal mentions the Details-panel spelling",
          "Details panel" in (q.get("error") or ""), (q.get("error") or "")[:190])

    # ------------------------------------------------------------------ T412 the stub guard
    print("")
    print("=== T412: the guard the rest of this leans on ===")
    # create_asset can mint a Texture2D with no source; if it cannot, the guard is exercised in T410
    # by its ABSENCE instead, and that is said out loud rather than passed silently.
    made = M.call("create_asset", {"path": "/Game/_MifTex/T_Stub_%d" % st, "class": "Texture2D"})
    if made.get("ok"):
        stub, probe = is_stub(made.get("objectPath") or made.get("assetPath")
                              or "/Game/_MifTex/T_Stub_%d" % st)
        check("T412 a source-less texture is refused rather than silently 'configured'", stub,
              "set_texture_settings answered %s - a stub would have been reconfigured to no effect"
              % json.dumps(probe)[:150])
        if stub:
            check("T412 and the refusal says how to give it pixels",
                  "import_texture" in (probe.get("error") or ""), (probe.get("error") or "")[:180])
    else:
        check("T412 (not exercised: create_asset would not mint a bare Texture2D here)", True,
              json.dumps(made)[:120])

    # ------------------------------------------------------------------ T413 guards
    print("")
    print("=== T413: bad input is refused, not imported ===")
    q = M.call("import_texture", {"sourcePath": "D:/definitely/not/here_zz.png",
                                  "destPath": "/Game/_MifTex/T_Nope_%d" % st})
    check("T413 a missing source file is refused", q.get("ok") is False, json.dumps(q)[:170])
    check("T413 and says something usable", len(q.get("error") or "") > 15, (q.get("error") or "")[:150])

    q = M.call("import_texture", {"base64": "bm90IGFuIGltYWdl",   # "not an image"
                                  "destPath": "/Game/_MifTex/T_Junk_%d" % st})
    check("T413 a payload that is not an image is refused", q.get("ok") is False, json.dumps(q)[:170])

    q = M.call("set_texture_settings", {"path": "/Game/_MifTex/NoSuchTexture_zz", "srgb": True})
    check("T413 settings on a missing texture are refused", q.get("ok") is False, json.dumps(q)[:170])
    check("T413 the editor survived all of it", M.bridge_responsive() is True, "bridge stopped answering")

    # CLEAN UP WHAT THIS SUITE PUT ON DISK. See the SAFETY note at the top: these are real .uasset
    # files, not in-memory objects, and leaving them accumulates real assets in the project.
    cleanup_scratch("/Game/_MifTex/")

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    print("NOT COVERED, and named rather than left silent: the overwrite/refill path, which refills an")
    print("EXISTING texture object so assets referencing it keep working. It needs overwrite=true, and")
    print("mifaudit strips that alongside save - scratch_confirm gives it no exemption either, because")
    print("neither flag is about a single asset the way confirm is.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
