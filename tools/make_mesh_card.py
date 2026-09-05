"""The Blender half of the gallery, as a card - because the two RENDERS are identical, on purpose.

WHY THE BEFORE/AFTER PAIR DOES NOT WORK ALONE. make_demo produces exactly what it promises: an asset
with real defects, the findings, the recipe, and a render on each side. Then it reports
`silhouetteIoU 1.0` and says why in its own output - "applying a transform is a visual no-op by
design - the render is identical and the export problem is gone, which is the whole point".

It is right, and that makes the image pair actively misleading as a gallery entry. A buyer sees two
identical pictures captioned before and after and concludes the tool did nothing. The renders are
187,240 and 187,032 bytes of the same shape. Everything that CHANGED - two ngons gone, a
non-uniform scale of [1.8, 1.0, 1.0] baked into the mesh data - is invisible in a picture and
perfectly legible as text.

SO THE TEXT IS THE SUBJECT AND THE RENDERS ARE EVIDENCE, which is the honest arrangement rather than
a rescue: the claim being made is "your asset still looks exactly the same", and two identical
thumbnails are the proof of it. The weakness becomes the point.

THE UV STEP IS ON THE CARD DELIBERATELY, and it is the most persuasive line here. recipe_game_ready
DECLINED to unwrap, because a UV layer already existed:

    "skipped - a UV layer already exists and forceUnwrap is false. Re-unwrapping a layout somebody
     made by hand is destructive and silent, so it is opt-in."

A pipeline tool that knows when NOT to touch your work is a different product from one that runs
every step. Showing a step that did nothing, and why, says more than three that succeeded.

EVERY NUMBER COMES FROM make_demo's OWN RUN. This reads demo_facts.json and the two PNGs it wrote in
the same pass, so a figure on the card cannot drift from the picture beside it - which is the
property make_demo's note already claims and this preserves. Nothing is typed by hand; if the facts
file is missing or stale-shaped, this refuses rather than inventing a plausible number.

The drawing helpers are imported from make_api_card rather than copied. One card renderer.

Usage:
    python tools/make_demo.py --out <dir>      # first: generates the images and the facts
    python tools/make_mesh_card.py --out <dir> # then: renders the card from them
"""
import argparse
import io
import json
import os
import sys
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ONE CARD RENDERER, NOT TWO. The palette and font resolution live in make_api_card; importing them
# means the two gallery cards cannot drift into looking like different products, and a change to the
# house style lands in both.
from make_api_card import _font, _head_font, BG, PANEL, FRAME, DIM, CALL, KEY, OK, TEXT, HEAD


def load_facts(out_dir):
    """(facts, before_png, after_png) or (None, why, None). Refuses rather than inventing."""
    fp = os.path.join(out_dir, "demo_facts.json")
    if not os.path.isfile(fp):
        return None, "no demo_facts.json in %s - run tools/make_demo.py --out %s first" % (out_dir, out_dir), None
    try:
        facts = json.load(io.open(fp, encoding="utf-8"))
    except ValueError as exc:
        return None, "demo_facts.json will not parse: %s" % exc, None
    before = os.path.join(out_dir, "01-before.png")
    after = os.path.join(out_dir, "02-after.png")
    for p in (before, after):
        if not os.path.isfile(p):
            return None, "missing %s - the card shows the renders make_demo made, not stand-ins" % p, None
    # The two fields the card's whole claim rests on. A facts file without them is a different shape
    # than this was written against, and drawing from it would be guessing.
    for key in ("concernsBefore", "concernsAfter", "concernsFixed", "recipeSteps"):
        if key not in facts:
            return None, "demo_facts.json has no %r - refusing to draw a card from a shape I do not recognise" % key, None
    return facts, before, after


def draw(facts, before, after, path, width=1280):
    from PIL import Image, ImageDraw

    mono, monob, head = _font(19), _font(19, bold=True), _head_font(30)
    sub, small = _head_font(17), _font(16)
    pad, lh = 46, 27

    fixed = list(facts.get("concernsFixed") or [])
    steps = list(facts.get("recipeSteps") or [])

    # Wrap first, measure, then allocate - the same rule as the refusal card, learned there by
    # over-allocating 110px with an expression that did not match the draw loop.
    fixed_lines = []
    for f in fixed:
        fixed_lines.extend(textwrap.wrap("- " + str(f), 92) or [str(f)])
    step_lines = []
    for s in steps:
        name = str(s.get("step", "?"))
        changed = s.get("changed")
        head_line = "%-16s %s" % (name, "CHANGED" if changed else "did nothing, on purpose")
        step_lines.append(("head", head_line, bool(changed)))
        for w in textwrap.wrap(str(s.get("detail") or ""), 88):
            step_lines.append(("body", "    " + w, bool(changed)))

    thumb_h = 250
    body_h = (len(fixed_lines) + 2) * lh + (len(step_lines) + 2) * lh
    height = pad + 96 + thumb_h + 40 + body_h + 70
    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)

    d.text((pad, pad - 6), "It finds what a store rejects - and the fix does not touch the art",
           font=head, fill=HEAD)
    d.text((pad, pad + 34),
           "Measured and fixed by the plugin in one pass. Every number below came off that run.",
           font=sub, fill=DIM)

    # THE TWO RENDERS, SIDE BY SIDE, LABELLED AS IDENTICAL. This is the claim, not a failure to
    # illustrate one: the geometry was repaired and the silhouette is untouched.
    y = pad + 96
    side = thumb_h - 34
    shots = []
    for p in (before, after):
        im = Image.open(p).convert("RGB")
        im.thumbnail((side, side))
        shots.append(im)
    # ADJACENT, AND LEFT-ALIGNED TO THE SAME MARGIN AS EVERY OTHER LINE. Two earlier attempts were
    # wrong in opposite directions: one render centred in each half-width column left a hole between
    # them that read as a missing image, and centring the pair read as misplaced on a card that is
    # otherwise strictly left-aligned. They are being COMPARED, so they sit next to each other, on
    # the margin everything else uses.
    gap = 40
    x = pad
    labels = ("BEFORE  -  %d concerns" % facts.get("concernsBefore", 0),
              "AFTER  -  %d concern(s)" % facts.get("concernsAfter", 0))
    for i, im in enumerate(shots):
        # A border, because both renders are near-black on a near-black card and without one they
        # bleed into the background - which is the same failure as not showing them at all.
        d.rectangle([x - 2, y - 2, x + im.width + 1, y + im.height + 1], outline=FRAME)
        img.paste(im, (x, y))
        d.text((x, y + im.height + 10), labels[i], font=small, fill=KEY if i == 0 else OK)
        x += im.width + gap
    y += side + 40

    iou = facts.get("silhouetteIoU")
    if iou is not None:
        d.text((pad, y),
               "The two renders are identical, and that is the result: silhouette IoU %s." % iou,
               font=monob, fill=CALL)
        y += lh
        d.text((pad, y), "Baking a transform is a visual no-op by design - the art is unchanged and "
                         "the export problem is gone.", font=mono, fill=TEXT)
        y += lh + 20

    d.text((pad, y), "WHAT IT FOUND, and a store would have rejected the asset for both:",
           font=monob, fill=HEAD)
    y += lh + 4
    for ln in fixed_lines:
        d.text((pad, y), ln, font=mono, fill=KEY)
        y += lh
    y += 18

    d.text((pad, y), "WHAT THE RECIPE DID:", font=monob, fill=HEAD)
    y += lh + 4
    top = y - 8
    d.rectangle([pad - 14, top, width - pad + 14, top + len(step_lines) * lh + 18],
                fill=PANEL, outline=FRAME)
    for kind, ln, changed in step_lines:
        d.text((pad, y), ln, font=monob if kind == "head" else mono,
               fill=(OK if changed else DIM) if kind == "head" else TEXT)
        y += lh

    d.text((pad, height - pad - 6),
           "MifBridge - it declined to re-unwrap UVs somebody had already made. That is the feature.",
           font=sub, fill=OK)
    img.save(path)
    return width, height


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, help="the directory make_demo.py wrote into")
    args = ap.parse_args()
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("needs Pillow: python -m pip install pillow")
        return 2

    facts, before, after = load_facts(args.out)
    if facts is None:
        print("REFUSING - %s" % before)
        return 2

    path = os.path.join(args.out, "MifCard_Mesh.png")
    w, h = draw(facts, before, after, path)
    size = os.path.getsize(path) if os.path.isfile(path) else 0
    if size < 8000:
        print("REJECTED - wrote only %d bytes, which is not a rendered card" % size)
        return 1
    print("OK  %dx%d, %d bytes" % (w, h, size))
    print("    %d concern(s) before -> %d after, %d recipe step(s), all from demo_facts.json"
          % (facts.get("concernsBefore", -1), facts.get("concernsAfter", -1),
             len(facts.get("recipeSteps") or [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
