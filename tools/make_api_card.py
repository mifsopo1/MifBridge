"""Render the listing's API images from output captured LIVE, never from text I typed.

WHY THIS EXISTS AND WHY capture_camera COULD NOT DO IT. The gallery plan said the images would be
made by the plugin doing its job - capture_viewport is an endpoint, so no screenshot tool is pointed
at anybody's window. That reasoning holds for a 3D scene and does not survive contact with the other
four planned images: the panel, the compiler output and describe_endpoint's answer are UI and TEXT,
and no capture endpoint can photograph those. A render of the level shows Curfew; it does not show
MifBridge.

WHAT THE PRODUCT ACTUALLY SELLS ON is in this file's subject. Every UE automation tool can spawn an
actor. What a buyer is choosing between is what happens when the call is WRONG, because that is where
an agent spends its time - and this plugin answers with the accepted parameter list, the alias, and
the mistake it thinks you made. A picture of that is a more honest advertisement than a picture of
somebody's greybox city.

=============================================================================
EVERY CHARACTER ON THE CARD IS CAPTURED FROM A RUNNING EDITOR
=============================================================================
The probes are sent when this runs and the responses are rendered verbatim. Nothing is hardcoded,
which is a marketing decision as much as a technical one: an image of output I typed by hand would be
a fabrication even if every word happened to be right, and it would drift the first time an error
message improved. If the bridge is not reachable this REFUSES rather than falling back to a canned
string.

THE PROBES ARE READ-ONLY AND DELIBERATELY WRONG. Each sends a parameter a read-only endpoint does not
accept, so the refusal is real and obtaining it mutates nothing - no asset, no actor, no package. The
one thing a demo generator must not do is dirty the project it is advertising.

AND IT IS NOT A FAKE SCREENSHOT. This draws a transcript, styled as a terminal, and does not imitate
the Unreal editor's UI. An image that looked like a panel I never photographed would be a fabricated
screenshot regardless of the text being genuine.

Usage:
    python tools/make_api_card.py --out <dir>
"""
import argparse
import io
import json
import os
import sys
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# THE PROBES. Read-only endpoints, each sent one parameter it does not accept. The comment on each
# says what the resulting refusal demonstrates, because that is the reason it is on the card.
PROBES = [
    # An alias that does not exist, on the endpoint an agent reaches for first. The refusal names
    # the real key AND explains the semantic difference, which is the part a bare "unknown param"
    # error cannot give you.
    ("list_level_actors", {"class": "StaticMeshActor"}),
    # Two endpoints in one family that do NOT share a spelling - the exact mistake that cost three
    # wrong calls in this repo's own history.
    ("capture_camera", {"path": "shot.png"}),
    # A parameter that sounds obviously right and is not: the refusal lists what find_assets can
    # actually filter on, including the tag syntax, so the next call is correct rather than a guess.
    ("find_assets", {"cooked": True, "limit": 1}),
]

BG = (24, 26, 32)
PANEL = (17, 19, 24)
FRAME = (48, 52, 62)
DIM = (128, 136, 152)
CALL = (126, 190, 255)
KEY = (224, 108, 117)
OK = (140, 200, 140)
TEXT = (214, 219, 228)
HEAD = (245, 247, 250)


def _font(size, bold=False):
    """Consolas for the transcript, Segoe for the headings; fall back rather than crash."""
    from PIL import ImageFont
    for path in (r"C:\Windows\Fonts\consolab.ttf" if bold else r"C:\Windows\Fonts\consola.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _head_font(size):
    from PIL import ImageFont
    for path in (r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\seguisb.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def capture(probes):
    """Send each probe and keep the REFUSAL verbatim. Returns [] if the bridge cannot answer.

    A probe that is ACCEPTED is dropped rather than drawn. That is not defensiveness for its own
    sake: this card's whole claim is "the refusal tells you what to do instead", and a probe the
    plugin started accepting would put a success on a card captioned as a refusal. If an endpoint
    gains one of these parameters, the card loses that row and says so - which is the honest
    failure, and the visible one.
    """
    import mifaudit as M
    ok, why = M.require_sdk_bridge()
    if not ok:
        print("no usable editor: %s" % why)
        return []
    print("  %s" % why)
    rows = []
    for ep, payload in probes:
        r = M.raw_post(ep, payload, timeout=60)
        if not isinstance(r, dict):
            print("  %-20s no response - dropped" % ep)
            continue
        if r.get("ok") is not False or not r.get("error"):
            print("  %-20s WAS ACCEPTED - dropped, this card only shows refusals" % ep)
            continue
        rows.append({"endpoint": ep, "payload": payload, "error": str(r.get("error"))})
        print("  %-20s refused (%d chars)" % (ep, len(str(r.get("error")))))
    return rows


def draw_card(rows, path, width=1280):
    """Draw the transcript. Height is computed from the wrapped text, never guessed."""
    from PIL import Image, ImageDraw

    mono, monob, head = _font(19), _font(19, bold=True), _head_font(30)
    sub = _head_font(17)
    pad, lh, wrap_at = 46, 27, 96

    # LAY OUT FIRST, MEASURE, THEN ALLOCATE. A fixed canvas height silently clips the last refusal,
    # and the last one is the longest.
    blocks = []
    for r in rows:
        call = "POST /api/%s  %s" % (r["endpoint"], json.dumps(r["payload"]))
        err = textwrap.wrap(r["error"], wrap_at) or [r["error"]]
        blocks.append((textwrap.wrap(call, wrap_at) or [call], err))

    # HEIGHT FROM THE SAME ARITHMETIC THE DRAW LOOP USES, not an approximation of it. The first
    # version added a per-block constant that did not match the one below, and over-allocated by
    # ~110px - so the footer floated in dead space. Two expressions for one layout drift the moment
    # either is touched; this is the loop's own advance, summed.
    advance = [(len(c) + len(e)) * lh + 40 for c, e in blocks]
    height = pad + 96 + sum(advance) + 52
    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)

    d.text((pad, pad - 6), "Every call is checked, and a wrong one says why", font=head, fill=HEAD)
    d.text((pad, pad + 34),
           "Captured live from a running Unreal editor. Nothing below was typed by hand.",
           font=sub, fill=DIM)

    y = pad + 96
    for call_lines, err_lines in blocks:
        top = y - 10
        block_h = (len(call_lines) + len(err_lines)) * lh + 26
        d.rectangle([pad - 14, top, width - pad + 14, top + block_h], fill=PANEL, outline=FRAME)
        for ln in call_lines:
            d.text((pad, y), ln, font=monob, fill=CALL)
            y += lh
        y += 6
        for i, ln in enumerate(err_lines):
            # The first line carries the verdict, so it gets the colour; the continuation is body
            # text. Colouring every line red reads as a stack trace, which is the opposite of the
            # point being made.
            d.text((pad, y), ln, font=mono, fill=KEY if i == 0 else TEXT)
            y += lh
        y += 34

    d.text((pad, height - pad - 6),
           "MifBridge - refusals name the accepted parameters, the aliases, and the likely mistake",
           font=sub, fill=OK)
    img.save(path)
    return width, height


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, help="directory to write the image into")
    args = ap.parse_args()
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("needs Pillow: python -m pip install pillow")
        print("")
        print("This is a MARKETING asset generator, not part of the plugin or the --fab package, so")
        print("the dependency costs a buyer nothing - nothing they run imports it.")
        return 2

    os.makedirs(args.out, exist_ok=True)
    print("MifBridge API card -> %s" % args.out)
    rows = capture(PROBES)
    if len(rows) < 2:
        print("")
        print("REFUSING - only %d refusal(s) captured. The card is not drawn from fewer, because a" % len(rows))
        print("thin card would be padded with text I wrote, and every character on it is supposed to")
        print("have come off the wire.")
        return 1

    path = os.path.join(args.out, "MifCard_Refusals.png")
    w, h = draw_card(rows, path)

    # CHECK THE OUTPUT, same standard as make_ue_demo. An image generator that reports success
    # without opening what it wrote is the failure that gets published.
    size = os.path.getsize(path) if os.path.isfile(path) else 0
    if size < 8000:
        print("REJECTED - wrote only %d bytes, which is not a rendered card" % size)
        return 1
    print("")
    print("OK  %dx%d, %d bytes, %d refusal(s), all captured this run" % (w, h, size, len(rows)))
    io.open(os.path.join(args.out, "api_card_facts.json"), "w", encoding="utf-8").write(
        json.dumps({"path": path, "rows": rows}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
