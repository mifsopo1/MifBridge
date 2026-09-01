# The abandoned-lab showcase

Six staged scripts that build a lit, animated, rendered interior in Blender **entirely through
MifBridge's typed ops**. No `run_python` — each stage prints which ops it used and whether the
escape hatch was among them, so the claim is measured rather than asserted.

```
python tools/blender-showcase/lab/s1_shell.py     # 33 objects   ~20s
python tools/blender-showcase/lab/s2_props.py     # 101 props    ~20s
python tools/blender-showcase/lab/s3_scatter.py   # node tree    ~2s
python tools/blender-showcase/lab/s4_light.py     # 8 lights     ~7s
python tools/blender-showcase/lab/s5_anim.py      # 27 keys      ~4s
python tools/blender-showcase/lab/s6_render.py    # dust + PNG   ~1s
```

Needs a Blender with the MifBlender addon listening (see `tools/run_blender_suites.py`, or launch
one and `import MifBlender; MifBlender.register()`). Run them in order; stage 1 clears the scene.

## Why it is staged rather than one script

It was built while being recorded. A single script that runs for a minute and then reveals a
finished room shows nothing about which capability did what; six stages that each visibly change
the viewport show the bridge working. It is also how you find out *which* stage broke something,
which one script never tells you.

## The numbers that were settled by measurement, not taste

**Lighting.** The first attempt was world strength 0.015 with 60 W tubes in an 18 × 11 × 3.6 m room.
Rendering it and computing a luminance histogram off the PNG gave **95 % of pixels near black** — a
correct scene nobody could see. Three passes settled it:

| world | tubes | mean | near black | p90 | |
|---|---|---|---|---|---|
| 0.030 | 420 W | 0.28 | 9 % | 0.66 | a lit room, not an abandoned one |
| **0.018** | **320 W** | **0.17** | **14 %** | **0.41** | pools of light against darkness |
| 0.012 | 240 W | 0.11 | 19 % | 0.27 | dark *without* highlights — flat |

Sixty watts is a desk lamp lighting a warehouse. Contrast comes from making the practicals bright
against a dark room, not from raising the ambient.

**Every flicker pattern starts ON.** Two of three fixtures were originally keyed to 0 at frame 1, so
the still frame anyone sees before pressing play showed one tube and black. A flicker is a *change*
from lit; keyed the other way it just reads as a broken scene.

**Interpolation is CONSTANT for the flicker.** Blender's default is BEZIER, which eases — a light
keyed 320/0/320 on BEZIER fades up and down and reads as a pulsing lamp, not a failing tube.

**The camera stays indoors.** `look()` takes an eye position and *refuses* one outside the room
bounds. The first version took a focus point plus an orbit distance — which is how you inspect an
object from outside it, and exactly wrong for standing in a room: focus (9, 5.5, 1.2) at distance 26
puts the eye near (−4.4, −14.1, 11.8), fourteen metres behind the south wall and eight above the
ceiling, watching a building get built from a field.

**The light fittings hang below the beams.** They were first placed at z 3.44–3.56 while the ceiling
beams occupy 3.26–3.60 — inside each other in both axes. They now hang on drop rods in the bays
*between* beams.

## What stage 3 is actually demonstrating

Everything before it is modelling, which any bridge can do given enough calls. Stage 3 **authors a
geometry-node tree**: creates the group, adds nodes, wires them, exposes `Density` and `Scale` as
group inputs, then drives those from the modifier. That capability did not exist before 0.8.0 —
attaching a nodes modifier already worked, building the tree did not.

Select `Debris_Surface`, find its **GeometryNodes** modifier, and drag **Density**. The floor's
rubble regenerates live. It is a real node graph the bridge wrote, not a bake.
