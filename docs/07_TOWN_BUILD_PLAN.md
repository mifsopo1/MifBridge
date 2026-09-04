<!-- MIFBRIDGE-DEV-ONLY -- excluded from release zips by tools/make_release.py.
     A plan for a demo that has not been built. Internal until it is, at which point the DEMO
     ships and the plan still does not.
     Still version-controlled: kept in git, kept out of the zip. -->
# Small-town build — plan

Build a 7-building town entirely through MifBridge, then play it in PIE. This is both a deliverable
and the largest end-to-end test of the bridge so far (~600–900 actors vs the 109 of the room demo).

---

## 0. Decisions made up front

| Decision | Choice | Why |
|---|---|---|
| **Building construction** | Pre-merged building meshes, not modular panel assembly | `ShantyTown/Models/MergedBuildings/SM_MERGED_SM_Building_*` and `FishingVillage/SM_House0*` are single meshes. One actor per building instead of ~40 panels — ~10× fewer actors, no seam alignment, and it survives a level reload. Modular panels stay for the one building we cut open for interior props. |
| **Ground** | Scaled plane + landscape material | A real `ALandscape` needs heightmap import, which the bridge has no endpoint for. Flat ground suits a shanty town. Stated as a limitation, not hidden. |
| **Graffiti** | `ADecalActor` + `MI_Graffiti_Pattern_*` | Decals project onto whatever wall they touch, so they don't need per-building UV work. |
| **Interiors** | Props placed in **2 of 7** buildings only | Every building would be ~300 extra actors for detail nobody sees through a doorway. Two "open" buildings get dressed; the rest are exterior-only. Deliberate, not laziness. |
| **NPCs** | Static, no AI controller | "Static NPCs" as asked. Avoids nav-mesh generation, which the bridge cannot trigger. |
| **Save cadence** | `save_package` after **every** stage | The room demo was lost when the level reset. Never again. |

---

## 1. Confirmed asset inventory

Everything below was verified present via `find_assets` before writing this plan.

| Role | Asset |
|---|---|
| Houses ×2 | `/Game/StaticMeshes/AssetPacks/FishingVillage/Meshes/SM_House01`, `SM_House02` |
| Shops ×2 | `/Game/StaticMeshes/AssetPacks/ShantyTown/Models/MergedBuildings/SM_MERGED_SM_Building_01`, `_02` |
| Shacks ×2 | ShantyTown merged buildings (smaller variants — needs one more survey pass to pick) |
| 7th building | TBD from ShantyTown — likely a dock/warehouse structure |
| Ground material | `/Game/StaticMeshes/AssetPacks/Tropical_Jungle_Pack/Materials/Instances/GroundCover/MI_Grass_01_Nowind` |
| Grass | `STF/Pack03-LandscapePro/.../SM_GrassGroup01` |
| Trees | `ShantyTown/Models/Foliage/Trees/SM_Palm_Tree_02`, `SM_Palm_Tree_Small_01` |
| Fences | `ShantyTown/Models/Fences/Palm/SM_Fence_Palm_01` |
| Props | `FishingVillage/Meshes/SM_Barrel01`, `SM_Bag01–04`; `/Game/StaticMeshes/Props/SM_Boombox`, `SM_BunkerLamp`, `SM_EnviroCeilingLamp` |
| Graffiti | `/Game/StaticMeshes/Decals/Graffiti/GraffitiPack/Materials/MI_Graffiti_Pattern_4`, `_5` (+59 more) |
| NPCs | `/Game/Blueprints/NPC/**` — 453 available; pick 4–5 visually distinct |
| Sky | `Ultra_Dynamic_Sky_C` + `Ultra_Dynamic_Weather_C` |
| Player | `DDS2_GameMode_C` → `BP_PlayerCharacter` + `MainPlayerHUD` |

**Still to survey before building:** exact shack/7th-building meshes, and each building's bounds
(needed for spacing — the room demo showed pivots are *not* centred, so spacing must come from
`ExtendedBounds`, not guesses).

---

## 2. Town layout

A single main street running north–south, buildings facing inward, ~4000×3000 units total.

```
        N
   [H1]   [S1]        H  = house      (FishingVillage)
   [K1]   [SH1]       S  = shop       (ShantyTown merged)
        ▲             K  = shack
   [H2]  street  [S2] SH = 7th building
   [K2]   [SH1]
        S            player start mid-street
```

- Buildings set back ~600u from the street centreline, alternating sides.
- Fences and palms fill the gaps between buildings so the edge of the world isn't visible.
- Graffiti decals on street-facing walls of the two shops and one shack.

---

## 3. Build stages

Each stage ends with `save_package` and an actor-count assertion.

| # | Stage | Approx actors |
|---|---|---|
| 1 | Ground plane + material, UDS sky + weather, realistic sky settings | 3 |
| 2 | 7 buildings placed and rotated to face the street | 7 |
| 3 | Street dressing — fences, crates, barrels, a dock walkway as pavement | ~60 |
| 4 | Foliage — palms, grass clumps, dead leaves, scattered with jittered rotation/scale | ~250 |
| 5 | Graffiti decals on street-facing walls | ~8 |
| 6 | Interiors for 2 buildings — shelving, boxes, lamps, boombox | ~60 |
| 7 | 5 static NPCs at doorways and the street | 5 |
| 8 | GameMode, PlayerStart, save, PIE | 2 |

**Total ≈ 400 actors.** At the room demo's observed rate (~2 HTTP calls/actor, ~0.35 s each) that's
roughly **4–5 minutes** of continuous run time. Acceptable for one take; worth a progress line per stage.

---

## 4. Verification

Per stage: assert the returned actor count matches expectation, and that no `spawn` returned an
error. The room demo's failure — a hardcoded `✓` printed outside its guard — is the exact bug to
avoid: **every success line must be conditional on the parsed response**, and the run must end with
a failure tally, not a cheerful summary.

Final checks: `compile` on any authored Blueprint returns 0/0; `pie_status` reports the pawn class is
`BP_PlayerCharacter_C`, not `DefaultPawn`.

---

## 5. Known risks

1. **`GlobalDefaultGameMode` is currently overridden project-wide** from the room demo and must be
   reverted or deliberately kept.
2. **`pie_status` reports `running` too early** — it uses `IsPlayingSessionInEditor()`, which goes
   true before the world exists. The correct test is `PlayWorld->HasBegunPlay()`. Until that is
   fixed, the script must sleep after "running" before querying the pawn. *(Fix pending, plugin-side.)*
3. **`list_object_properties` returned zero bytes on `Ultra_Dynamic_Sky`** (~545 properties) — a
   probable size/timeout bug. Use `describe_class` for large objects instead. *(RE-CHECKED
   2026-08-28 against a real DDS2 Ultra_Dynamic_Sky instance and it does NOT reproduce: 634
   properties, both with an explicit high limit and with the default limit=200 (honestly reported
   as truncated, matched:634). Whatever caused the zero-byte response before is not present now -
   left as "does not currently reproduce" rather than claimed fixed, since the original root cause
   was never identified. If it recurs, get the exact engine build and property count at the time.)*
4. **The level is `Untitled` and unsaved.** Save it to a real `/Game/Maps/` path as stage 0 or the
   whole build is one crash from gone — as already happened once.
5. **Cooked-content buildings** (`/DDS2Casino/`) cannot be modified, only placed. Fine for meshes.

---

## 6. Out of scope

Nav mesh (no endpoint), real terrain sculpting (no endpoint), lightmap builds (no endpoint — rely on
UDS's dynamic lighting), and building interiors for 5 of the 7 buildings.
