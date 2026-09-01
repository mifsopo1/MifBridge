# The abandoned-lab showcase

9 staged scripts that build a lit, animated, rendered interior in Blender **entirely through
MifBridge's typed ops**. No `run_python` — each stage prints which ops it used and whether the
escape hatch was among them, so the claim is measured rather than asserted.

```
python tools/blender-showcase/lab/s1_shell.py      # 33 objects           ~21s
python tools/blender-showcase/lab/s2_props.py      # 101 props            ~21s
python tools/blender-showcase/lab/s2b_clutter.py   # 354 instances        ~3s
python tools/blender-showcase/lab/s3_scatter.py    # node tree            ~2s
python tools/blender-showcase/lab/s4_light.py      # 8 lights             ~6s
python tools/blender-showcase/lab/s5_anim.py       # 27 keys              ~4s
python tools/blender-showcase/lab/s6_render.py     # dust + PNG           ~1s
python tools/blender-showcase/lab/s7_cinematic.py  # 55s, 125 keys        ~9s
```

`s5` and `s7` are alternatives: `s5` is a short loop for looking at the room, `s7` is the full
55-second timeline and replaces `s5`'s camera when it finds one.

Needs a Blender with the MifBlender addon listening (see `tools/run_blender_suites.py`, or launch
one and `import MifBlender; MifBlender.register()`). Run them in order; stage 1 clears the scene.

## Why it is staged rather than one script

It was built while being recorded. A single script that runs for a minute and then reveals a
finished room shows nothing about which capability did what; stages that each visibly change
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

## Stage 2b — density, and why it is scattered

The reference frames are *full*: bottles on every shelf, litter across the whole floor. 101
hand-placed props does not get there, and doing it by hand means several hundred more calls in a
script nobody can adjust afterwards — change your mind about how full a shelf is and you rewrite a
loop.

So it scatters. **354 instances across 6 HAIR particle systems**, each count a *field on the
modifier*. Ten bottles or eighty is one number.

It is also the more honest demonstration: hand-placing 400 props proves the bridge can call
`create_primitive` 400 times; scattering them proves it drives Blender's instancing system, which
is what did not exist before 0.8.0.

## Stage 7 — the 55-second cinematic

The benchmark's timeline, verbatim: lights up, fan, computer boot, flicker, steam, camera move,
warning lamp, something falls off a shelf, arrive at the workstation, monitor change, blackout,
emergency lighting, pull back. **1345 frames, 125 keyframes**, six capability families at once —
lights, keyframes, physics, particles, cameras, world.

Beats are declared in **seconds** and converted in one place, and the conversion is *asserted*
against the scene's real fps. Writing `20` where `480` was meant is the easiest mistake to make
silently — the render just looks wrong.

Measured per beat rather than eyeballed:

| beat | mean luminance | lit pixels |
|---|---|---|
| 00:18 running | 0.075 | 32 % |
| 00:31 warning lamp | 0.043 | 14 % (peak 0.68 — the red punches through) |
| **00:51 blackout** | **0.0004** | **0 %** |
| **00:53 emergency** | **0.109** | **36 %** |

A 270× jump in mean is the beat working.

### The detour worth knowing about

The emergency beat first rendered **pure black**, and it was chased through the keyframe (right
datablock, right frame), the evaluated depsgraph (energy confirmed), the scene camera and colour
management. **100× the wattage changed the frame not at all, to four decimal places.**

The lamp was at (9, 5.5, 3.2) — directly *behind* the camera at that beat. The same frame from a
static camera showed peak luminance **0.63**. The light was blazing; nobody was looking at it.

Every op reported correctly the whole time. A light nobody is looking at is not a lighting bug, and
no amount of instrumentation on the bridge would have said so.
