# The zombie-bunker showcase — architecture, before any of it is built

A staged build of a post-apocalyptic survival bunker, in Blender through MifBridge's typed ops,
then into Unreal. Modelled directly on `../lab/`, which works and is recorded; this is the same
shape at a larger scale with a UE half the lab never had.

**Read `../lab/README.md` first.** Every lesson in it applies here and is not repeated: lighting
settled by luminance histogram rather than taste, flicker patterns that start ON, CONSTANT
interpolation for a failing tube, the camera that must stand indoors, and the emergency lamp that
rendered black because it was behind the camera.

## The decision that shapes everything: this is RECORDED, not RENDERED

The lab's README says it in one line — *"It was built while being recorded"* — and it is the reason
that build is staged at all. The video is a screen recording of the viewport changing as the scripts
run. It is not `render_animation` output.

That matters more than it sounds, and it settles a question that would otherwise block the UE half:

> **The bridge cannot render a Level Sequence to video.** UE has `add_sequence`,
> `add_sequence_track`, `add_sequence_possessable` and `set_sequence_keys` — it can *author* a
> cinematic — and there is no Movie Render Queue binding, so nothing turns one into frames. Checked
> against all 453 endpoints on 2026-09-05, not assumed.

If the deliverable were a rendered film, that gap would have to be closed in C++ first. Because the
deliverable is a recording of the editor working, it does not: the Sequencer playing in the viewport
is exactly the thing worth filming. **Watching it get built is the product.** A finished room that
appears after four minutes of black screen demonstrates nothing about which capability did what.

So: every stage must produce a *visible change in the viewport*, and no stage should run so long
that a recording of it is boring.

## Scale, and being honest about it

The concept board says ~80 m × 40 m, multi-level. The lab is 18 × 11 × 3.6 m and stage 1 makes 33
objects in ~21 s. A bunker at eight times the floor area, on four levels, is not eight times the
work — it is eight times the *call volume*, and call volume is what makes a recording unwatchable.

**Therefore: one level, built well, with the vault hall as the hero.** The concept board's other
floors are a shot list for later, not a build target for the first pass. A cramped, dense, lit
single level films far better than a large empty one, and the lab's stage 2b already proved the
route to density is *scattering*, not hand-placing.

## The concept board is a checklist, not a target

Andre generated it with ChatGPT and asked whether it helps. It does, in one specific way: its
**build list** and **MCP integration points** columns are a shot list — armoury, medical bay,
hydroponics, workshop, power, mess hall. Those are rooms with recognisable silhouettes and different
lighting colours, which is what makes cuts between them read.

What it is **not** is a visual target. Those renders are concept art. No procedural script driving
typed ops produces them, and treating them as the bar would mean judging a working pipeline against
something it was never going to make. The bar is the lab: readable, lit, dense, obviously built by
something rather than hand-modelled.

## Stages

Each is one file, one visible change, and prints its own op census plus whether `run_python` was
among them. That last line is the whole integrity claim and it is measured, not asserted.

| stage | makes | why it earns its place in the video |
|---|---|---|
| `b1_shell` | vault hall, walls, floor, blast door, side-room shells | the room appears — the single most watchable moment |
| `b2_fixtures` | bunks, lockers, benches, shelving, tables, medical bay | it becomes a *place*, and each room reads differently |
| `b3_scatter` | crates, bottles, litter, cabling via particle systems | density without 400 hand calls — a modifier field, not a loop |
| `b4_light` | practicals: strip lights, hanging bulbs, purple grow lamps, red emergency | colour separates the rooms; this is where it stops looking like grey boxes |
| `b5_anim` | flicker, blast door, fan, generator | movement, and the flicker is the money shot |
| `b6_fx` | dust, steam, sparks | atmosphere the lab already proved out |
| `b7_cinematic` | the timeline: camera moves, beat by beat | the reason anyone watches to the end |
| `b8_unreal` | export, import, materials, blueprint, sequencer | the half the lab never had — and the actual product claim |

`b8` is the one that is genuinely new work rather than a scaled-up lab, and it is where the demo
stops being a Blender showcase and becomes a MifBridge one.

## What is shared, and what is NOT copied

`../lab/stage.py` is imported, not forked. It already owns `call` with the op census, `box`, `cyl`,
`cut`, `paint`, `mat` and `look` with its indoor bounds check — all of it learned the hard way, and
a second copy would drift from the original the first time either is fixed.

One thing had to change to make it reusable: `ROOM` was a module constant holding the *lab's*
interior. `look()` checks the eye against it, so importing it here would refuse every legitimate
bunker viewpoint while accepting eyes outside the bunker walls. It is now settable, defaulting to
exactly what it was.

## Status

Architecture only. Nothing is built yet, and this file exists before the code on purpose — the lab
was designed the same way and its README is why its numbers are defensible.
