# Changelog

Every number here was **measured from the tagged tree**, not remembered — UE endpoints by counting
`MIF_DECL` in `MifBridgeHandlers.h` at that commit, Blender ops by parsing the `OPS` dicts. The one
number in this repo that was ever hand-typed read "20 ops" long after it was 68, which is why none
of these are.

| version | date | UE endpoints | Blender ops |
|---|---|---|---|
| [0.8.0](#080) | 2026-09-01 | 441 | 68 |
| [0.7.0](#070) | 2026-08-30 | 422 | 42 |
| [0.6.0](#060) | 2026-08-27 | 321 | 20 |
| [0.5.0](#050) | 2026-08-27 | 321 | 18 |
| [0.4.1](#041) | 2026-08-21 | 228 | 17 |
| [0.4.0](#040) | 2026-08-21 | 228 | 17 |
| [0.3.0](#030) | 2026-08-10 | 219 | 12 |

---

## 0.8.0

**The Blender arm becomes a full DCC, and the 5.7 gate earns its keep.**

The Blender half went **45 ops → 68**, closing every capability family that had no typed op at all.
Before this it could model, boolean, transform and shade; it could not light, aim a camera,
animate, simulate or render — so everything past modelling had to leave the typed path for
`run_python`.

Nine families landed: **lights**, **cameras**, **keyframes**, **geometry-node tree authoring**,
**particles**, **physics**, **rendering**, **world**, and **viewport control**.

That ninth one was not on the original gap list, and the reason is worth keeping: the list had been
written by asking what the *engine* can do. Viewport control is about what the person watching can
**see** — a bridge that can light a scene and cannot show it has not finished the job. It surfaced
only because a lighting stage was reported as "all grey, couldn't see any of it" while every light
was being created correctly into a SOLID viewport.

**The 5.7 release gate refused to package, and was right.** Four C++ files had changed since the
recorded engine probe; re-running it found the plugin no longer compiled on 5.7 at all. Three
breaks, every one of a shape a symbol-presence check cannot see because all the names still exist
in both trees:

- `EAutomationTestFlags` changed **three ways at once** — namespaced `enum Type` became an
  `enum class`, the accessor moved namespace *and* was renamed, and `GetTestFlags()`'s return type
  changed.
- Comparing the resulting enum class against a bare `0` is a hard error, where the old plain enum
  converted silently.
- 5.7 escalates the `FInputKeyEventArgs` deprecation from a warning to a **build failure**. The
  code already documented that the 6-arg form was deprecated and chose it deliberately; what
  changed was the consequence, not the API.

This is why the probe **compiles** rather than greps. Reading finds symbols that were *deleted* and
reliably misses ones that *changed shape*.

Also: the spec backlog reached zero (358 done, 45 declined); two new Blender suites
(`test_blender_anim`, `test_blender_scene`) were written *because* a 44-run sweep went green over
all the new code while calling none of it; and `audit_dead_params` plus its Blender twin stopped
letting one handler vouch for every other.

## 0.7.0

**+101 endpoints, and the first regression sweep that ever finished.**

The endpoint count went 321 → 422 and the Blender arm doubled, 20 → 42. More importantly it is the
first release where a full sweep ran to completion rather than dying partway — which is what made
every count after it trustworthy.

## 0.6.0

Blender 18 → 20 ops. UE endpoints unchanged from 0.5.0.

## 0.5.0

**228 → 321 endpoints.** The largest single jump in the project's history.

## 0.4.1

**Fixes an `add_pin` editor crash when a default is supplied.** A patch release for one crash, which
is the right size for a crash.

## 0.4.0

**Animation Blueprints, widget tree topology, and a crash that had been documented instead of
fixed.** The title is the lesson: a known crash written down in a doc is still a crash.

## 0.3.0

**The Blender round trip.** The first release where the two halves worked as one tool — a mesh could
leave Unreal, be edited in Blender, and come back.

---

Releases with attached zips: <https://github.com/mifsopo1/MifBridge/releases>
