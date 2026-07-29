# MifBridge full-scope audit and expansion — consolidated report

- **Date:** 2026-07-26 (single-day run: audit, adversarial verification, synthesis, delta research, first four implementation batches)
- **Engine:** `5.3.2-0+++UE5+Release-5.3-CookedEditorModKit` (source fork at `D:/UE532`)
- **Project:** Drug Dealer Simulator 2 modkit (`D:/DDS2SDK/Game`), plugin `D:/DDS2SDK/Game/Plugins/MifBridge`
- **Mission:** [../10_FULL_SCOPE_EXPANSION_PROMPT.md](../10_FULL_SCOPE_EXPANSION_PROMPT.md)
- **Audience:** Andre. This is the master narrative — read it top to bottom once; everything else in
  `docs/audit/` is reference material this report links to.

**Data ownership.** This report mirrors nothing. Full ten-field endpoint entries live in the 18 axis
files under [work/](work/); the generated index is [01_CATALOGUE.md](01_CATALOGUE.md); the ranking and
batch plan is [02_RANKED.md](02_RANKED.md); the risk digest is [03_GAPS_AND_RISKS.md](03_GAPS_AND_RISKS.md);
open decisions are [04_OPEN_QUESTIONS.md](04_OPEN_QUESTIONS.md); the implementation log is
[06_IMPLEMENTED.md](06_IMPLEMENTED.md); the run log is [PROGRESS.md](PROGRESS.md). Where this report and
an axis file disagree, the axis file wins.

---

## 1. Executive summary

The working hypothesis — *the existing 160 endpoints cover a small fraction of what this engine build
can actually be driven to do* — is **proven**, with numbers:

- **250 verified entries** came out of the full sweep: 242 new endpoints plus 8 behaviour changes to
  existing ones. After adversarial verification and cross-axis dedup, **241 remain active** —
  roughly **1.4× the existing surface**, every one carrying a cited, export-checked engine entry point,
  a transaction bucket, cooked-content behaviour, and a numeric verification method
  ([01_CATALOGUE.md](01_CATALOGUE.md)).
- **36 are Tier-0** (each closes a known, named gap), 132 Tier-1, 68 Tier-2, 5 Tier-3.
- **18 axes** were swept: 12 engine axes (A–J), 2 reconstructor axes (K1/K2), 3 plugin axes (P1–P3),
  and 1 defect-forensics axis (Q).
- **~162 cited negative results** were banked alongside the positives — dead ends nobody has to
  re-tread, each with file:line evidence ([03_GAPS_AND_RISKS.md](03_GAPS_AND_RISKS.md)).
- Every entry survived a second, adversarial pass: **189 CONFIRMED, 60 CORRECTED, 1 DEMOTED** across
  both verification waves. The corrections were not cosmetic — the dominant correction class was
  *hidden modal dialogs and game-thread blocking waits* on otherwise-viable engine paths, exactly the
  class that deadlocks a mid-frame HTTP handler (21 modal traps and 13 blocking hazards are now
  inventoried in [03_GAPS_AND_RISKS.md](03_GAPS_AND_RISKS.md) §2–§3).

Whole categories that sat at **zero** coverage came back implementable: material graph authoring,
Sequencer, Niagara, audio, physics assets, undo introspection, automation tests, Enhanced Input,
procedural geometry, savegames, localization-adjacent text.

**Already implemented and live-proven this session** ([06_IMPLEMENTED.md](06_IMPLEMENTED.md)):

| Batch | What landed | Proof state |
|---|---|---|
| A | The 4 in-source endpoints picked up by rebuild/relaunch: `set_viewport_camera`, `get_viewport_camera`, `focus_viewport`, `spawn_actor_in_pie` | ALL PASS — exact camera round-trip, framed proof cube, full PIE lifecycle incl. in-PIE spawn |
| B | Repairs: `find_assets` (+4 siblings) unknown-param rejection with `className`/`type` aliases; `server.py` drift closed (`diagnose_landscape_draws` tool added, 160==160==160); `describe_class`/`list_enum_values` moved to the read-only bucket | ALL PASS — typo'd params now error by name; alias returns 379 DataTables; undo queue proven clean after reads |
| C | 5 new endpoints in new `MifBridgeUndo.cpp`: `list_transactions`, `undo_transactions`, `redo_transactions`, `list_dirty_packages`, `save_dirty_packages` | ALL PASS — spawn→undo→gone→redo→back cycle verified numerically; skip-echo proven on an untitled map |
| D | Next zero-dependency slice — in flight as this report is written | Landing entry belongs to [06_IMPLEMENTED.md](06_IMPLEMENTED.md) when it closes |

Live endpoint count moved **156 → 160 → 165** today, with `self_audit` reporting zero policy
contradictions at every step.

**The three biggest unlocks ahead:**

1. **The materials loop** (Batch 2, [02_RANKED.md](02_RANKED.md)): one editor-only module
   (`MaterialEditor`) buys 9 endpoints, 5 of them Tier-0 — create/author/wire/recompile/read-back for
   real material graphs. Materials are the single densest Tier-0 cluster in the catalogue (10 of 36).
2. **The reconstructor over HTTP** (Batch R): 10 `kr_*` endpoints expose MifKismetReconstructor's
   working cooked-Blueprint decompiler to agents. Two ship with zero export promotions; the rest ride
   a new registration interface that also makes MifBridge extensible by any future `Mif*` plugin
   (§5 below, [work/K2_reconstructor_pipeline.md](work/K2_reconstructor_pipeline.md)).
3. **The zero-dependency core** (Batch 1): **145 endpoints with no Build.cs change at all**, including
   21 Tier-0 — undo/dirty flows (5 already landed as Batch C), the AnimBP authoring trio, PIE NPC
   movement, landscape splines, foliage, sublevels, datatable/curve authoring, diagnostics.

---

## 2. How the audit was run

The run had two waves. The first executed [../09_ENDPOINT_DISCOVERY_PROMPT.md](../09_ENDPOINT_DISCOVERY_PROMPT.md)'s
successor design as a 3-phase engine sweep; the second was a delta against
[../10_FULL_SCOPE_EXPANSION_PROMPT.md](../10_FULL_SCOPE_EXPANSION_PROMPT.md) covering what the first wave
had not: the reconstructor (K), the other project plugins (P), and root causes for the two known
defects (Q). Full timeline with incidents: [PROGRESS.md](PROGRESS.md).

**Phase 0 — baseline.** Environment verified (engine source, plugin, live editor answering on
127.0.0.1:8791), registry state measured three ways (source 160 / live DLL 156 / server.py 159 —
already a finding), and [work/_BRIEF.md](work/_BRIEF.md) written: the shared contract every sweep agent
worked under (invariants, the covered set, the ten verification fields, output format). An early sed
extraction said 159 source endpoints; grep -o said 160 (`spawn_actor_in_pie` had been dropped) — the
brief was corrected in place, and that correction later prevented every downstream agent from diffing
against a stale covered set.

**Phase 1 — breadth sweep.** 12 axis agents (workflow `mifbridge-endpoint-sweep`) each swept one
engine domain against `D:/UE532/Engine/Source` — headers read from disk, never recalled — and wrote
`work/<axis>.md` with ten fields per proposal: verbatim signature with file:line, export macro, module
dep, WITH_EDITOR guard, bucket + justification, async design, parameter spec with aliases, failure
modes, cooked behaviour, numeric verification method. Result: 232 proposals, ~107 cited negatives,
66 UNVERIFIED items. The bridge went down mid-sweep (G1/G2/H/I lost live probes); queued probes moved
to Phase 2 rather than being guessed at.

**Phase 2 — adversarial verification.** 12 fresh verifiers (workflow `mifbridge-audit-verify`) re-opened
*every citation* in every axis file: signature matched verbatim, export macro re-checked, bucket and
async design challenged, and — the decisive step — the cited **.cpp implementations** greped for
`FMessageDialog`, `EditorAddModalWindow`, `ShowModal`, `FScopedSlowTask`, GC calls and busy-waits.
Negatives were spot-verified too (a wrong negative kills a capability as surely as a wrong positive).
A 13th read-only agent ran the queued live probes ([work/LIVE_PROBES.md](work/LIVE_PROBES.md)).
Wave-1 verdicts: **220 entries — 168 CONFIRMED, 51 CORRECTED, 1 DEMOTED; 3 negatives OVERTURNED.**
The delta wave (3 more verifiers over K/P/Q) brought the totals to **250 — 189 / 60 / 1**.

Two integrity events prove the discipline was not theatre:

- **The H catch:** one axis file was found carrying a completion stamp while only 2 of 25 entries had
  verdicts — a falsely-claimed verification pass. The entire axis was re-run from scratch
  ([work/H_data.md](work/H_data.md) header).
- **The J control:** the one verifier that finished before the first usage limit confirmed 8/8 entries
  verbatim with zero corrections — including both load-bearing `mount_pak` claims down to
  `AssetRegistry.cpp:186-192` and `IPlatformFilePak.cpp:8112-8124` — showing the Phase-1 agents could
  be right, which makes the 60 corrections elsewhere meaningful rather than noise.

**Why the corrections matter.** The single dominant correction class was hidden modal/blocking
behaviour: the modal-trap inventory grew from 2 known traps to **21** catalogued ones spanning 6 of 12
engine axes, plus **13** blocking/synchronous hazards (unbounded shader waits, double GC inside
`RecompileMaterial`, `FinishAllCompilation` drains, a V-HACD dialog loop). A modal opened from a
mid-frame HTTP handler deadlocks the editor — the HTTP pump never runs again — so each of these was a
shipped-crash-in-waiting had Phase 1 gone straight to implementation. The blanket rule distilled into
[03_GAPS_AND_RISKS.md](03_GAPS_AND_RISKS.md) §2: *pre-validate everything; never let the engine prompt.*
Other correction families: silent-ignore gates (the `editcondition` class — e.g. `MinLOD` needs
`bOverrideMinLOD=true`), private members behind exported-looking classes (`UEditorEngine::Map_Check`),
and export-macro misattributions. The 1 DEMOTED entry (`create_level_instance`) died on a hard-coded
modal Save-As with no dialog-free path in 5.3.2 (`LevelInstanceSubsystem.cpp:999-1000`).

**Phase 3 — synthesis.** 12 low-effort extractors emitted machine rows (`work/index/*.rows.json`),
two writers compiled [03_GAPS_AND_RISKS.md](03_GAPS_AND_RISKS.md) and
[04_OPEN_QUESTIONS.md](04_OPEN_QUESTIONS.md), and the main session assembled
[01_CATALOGUE.md](01_CATALOGUE.md) (generated index — never hand-edit) and
[02_RANKED.md](02_RANKED.md) (tier sort by U+E+R, batches grouped by shared module cost).

**The delta** (workflow `mifbridge-fullscope-delta`): 6 research agents → 3 adversarial verifiers →
6 extractors over the three territories the 10-prompt demanded beyond the engine: MifKismetReconstructor
(K1 toolkit, K2 pipeline/coupling), the other project plugins (P1 graph layout, P2 world/vehicle,
P3 sessions/GameFeatures/misc), and the two defect root-causes (Q). The catalogue and ranking were then
regenerated with all 18 axes.

---

## 3. The baseline — what the 160 already covered

[00_BASELINE.md](00_BASELINE.md) is the full per-file inventory (29 handler files, buckets from the
live `self_audit`: 48 read-only / 15 self-managed / 93 transacted, 17 compile-heavy). The shape that
motivated the audit is its domain table:

**Dense** — Blueprint *graph* authoring (40 endpoints: every node spawner, pin op, wiring op),
functions/events/dispatchers/interfaces (14), level/world authoring (15). An agent could already build
K2 logic and place actors well.

**Thin** — materials (2: instantiate + edit a MaterialInstanceConstant — no material or expression
authoring at all), animation (introspection only), pipeline (read/plan only — `trigger_cook` executes
nothing), viewport camera (in source, not yet live at audit start).

**Zero** — Sequencer, Niagara, audio, physics, Enhanced Input, localization, source control,
automation tests, undo introspection, dependency graphs, procedural geometry, savegames.

The audit also measured the environment those endpoints operate in: 37,131 registry assets, of which
25,285 are container-only (cooked, mounted from 3 IoStore containers) and 11,846 loose — the
cooked-content constraint that shapes roughly a third of all endpoint specs (§8).

---

## 4. Domain findings, axis by axis (A–J)

One subsection per engine axis. Format: what the sweep established, the axis's Tier-0 items, and its
killer negatives. Full entries, parameter tables and verdicts live in the linked axis file — always
read the axis entry before implementing.

### A — Editor core ([work/A_editor_core.md](work/A_editor_core.md))

All 54 `UEditorSubsystem` subclasses in the build were enumerated and dispositioned; the transaction
buffer, dirty-package flows, selection, folders, layers, cvars and viewport state all came back
reachable through method-level `UNREALED_API` exports (this fork exports at method level on
`MinimalAPI` classes — every claim was checked at the line, not the class). Undo/redo introspection —
a named roadmap gap — is fully viable via `GEditor->Trans` + `UEditorEngine::UndoTransaction/
RedoTransaction`, and shipped this session as Batch C. Checkout-free saving exists
(`FEditorFileUtils::SaveDirtyPackages(bFastSave=true)`) but its fast path hides a modal on any failed
save — the shipped `save_dirty_packages` deliberately enumerates and saves per-package instead.
20 entries: 18 CONFIRMED, 2 CORRECTED.
**Tier-0:** `list_transactions` ✅, `undo_transactions` ✅, `redo_transactions` ✅ (all live).
**Killer negatives:** `UTransactor::Undo/Redo` unexported (go through GEditor); `ULightEditorSubsystem`
lives in a Private header; `UEditorLoadingAndSavingUtils::SaveDirtyPackages` is not checkout-free;
no `GetActiveModes()` enumeration exists in 5.3.2.

### B — Assets and the registry ([work/B_assets_registry.md](work/B_assets_registry.md))

Dependency/referencer graphs, class hierarchies, tag queries, redirector chasing and package disk data
are all registry-level reads that work without loading assets — with a designed self-diagnosis for
cooked stripping (container packages return `dependencyDataAvailable:false`, never a silent empty
list). ~296 `UFactory` subclasses were enumerated to ground a generic `create_asset` with a 9-type
allowlist (boundary policy ratified: dedicated creators own their types, `create_asset` hard-refuses
them — [04_OPEN_QUESTIONS.md](04_OPEN_QUESTIONS.md) §1.1). Import/export/validate/consolidate all
exist but are the axis with the heaviest modal-trap density: `CanCreateAsset` alone hides three
`FMessageDialog`s. 18 entries: 12 CONFIRMED, 6 CORRECTED.
**Tier-0:** none — this axis is the Tier-1 backbone (`get_asset_dependencies`, `get_asset_referencers`,
`get_class_hierarchy`, `create_asset`, `import_asset`, `map_check`...). `list_dirty_packages` ✅ landed
via Batch C under B's ownership.
**Killer negatives:** `UEditorEngine::Map_Check` is **private** despite its export macro — rerouted
through the public Exec dispatcher `"MAP CHECK DONTDISPLAYDIALOG"` + MessageLog readback;
`ObjectTools::ConsolidateObjects` has *unsuppressable* end-of-run failure modals; the project has no
source-control provider (whole family skipped).

### C — Blueprints and graphs ([work/C_blueprints_graphs.md](work/C_blueprints_graphs.md))

The deepest-covered axis still yielded 8 Tier-0 items, mostly *behaviour changes* to shipped endpoints:
the `connect_pins` schema fix (it hardcodes the K2 schema CDO, silently giving anim/state-machine/widget
graphs K2 semantics — one-line-class fix, prerequisite for AnimBP authoring), inherited/native parent
resolution in `add_component`/`list_components`, local-variable scope, and in-place variable retyping.
The UK2Node census (111 BlueprintGraph declarations + 80 AnimGraph nodes + 17 state-machine classes)
grounds a generic `add_node_by_class` with a PM-004-derived denylist — zero new dependencies. AnimBP
state-machine authoring was verified end-to-end (state machine → states → transitions → rule graphs),
and `FKismetBytecodeDisassembler` turns out to be `SCRIPTDISASSEMBLER_API` and works on cooked classes —
read-only bytecode access to base-game logic for one small module dep. 18 entries: 14 CONFIRMED,
4 CORRECTED (export attributions only).
**Tier-0:** `list_components`°, `connect_pins`°, `add_component`°, `list_variables`° (behaviour
changes), `add_create_delegate`, `create_macro`, `set_variable_type`, `add_node_by_class`.
**Killer negatives:** `USubobjectDataSubsystem::ReparentSubobjects` is UI-locked (requires an
SCS-editor preview actor) — direct ENGINE_API SCS calls instead; collapse-to-function/macro and
cross-Blueprint graph moves remain impossible (engine does them via copy/paste — a documented non-goal).

### D — Materials and rendering ([work/D_materials_rendering.md](work/D_materials_rendering.md))

The headline unlock: **material graph authoring is fully unblocked** through `UMaterialEditingLibrary`
(`MATERIALEDITOR_API`, ~50 UFUNCTIONs — the module is the axis's one new dependency). 277
`UMaterialExpression` subclasses were censused and a 34-class key-property catalogue written for
`add_material_expression`. The whole authoring loop closes: create material → add expressions →
wire → connect to output pins → recompile → read back stats and the graph. Phase 2's corrections were
the heaviest here: `RecompileMaterial`'s tail runs GC **twice**, pumps a cancellable slow-task dialog
and busy-waits on debug shaders — the endpoint replicates its non-blocking core instead;
`GetStatistics` synchronously blocks until that material's shaders compile (an overturned negative —
not stale numbers, a blocking wait). 25 entries: 18 CONFIRMED, 7 CORRECTED.
**Tier-0 (10 — the densest cluster):** `recompile_material`, `add_material_expression`,
`connect_material_expressions`, `connect_material_property`, `create_material`,
`validate_level_materials`, `list_material_expressions`, `set_actor_render_overrides`,
`get_actor_render_info`, `shader_compile_status`.
**Killer negatives:** cooked materials have **no graph** — `UMaterialExpression` is `UCLASS(Optional)`
and stripped from cooked packages, so every graph endpoint refuses on `PKG_Cooked` (create-new and
instance-derivation are the only lanes against base-game materials); MPC default-value setters are
unexported (write the UPROPERTY arrays + Pre/PostEditChange); `BuildReflectionCaptures` hard-crashes
(`check()`) below SM5 and drains all editor-wide compilation.

### E — Geometry and meshes ([work/E_geometry_meshes.md](work/E_geometry_meshes.md))

GeometryScript is the biggest untapped surface, as the mission predicted: 39 library classes,
**~478 UFUNCTIONs**, reachable through a dynamic-mesh session model (create/list/release pooled
meshes + a single allowlisted `mesh_op` dispatcher + numeric `mesh_query` verification). Cost: the
plugin is not project-enabled — one-time `.uplugin` reference from MifBridge. Separately, LODs,
collision (primitive and V-HACD), Nanite, sockets, UV channels, physics assets and actor merging all
live in engine-source editor modules (`StaticMeshEditor`/`SkeletalMeshEditor`) with **no**
EditorScriptingUtilities plugin needed. 23 entries: 17 CONFIRMED, 6 CORRECTED.
**Tier-0:** none — 17 Tier-1 entries make it the largest Tier-1 block after F/H.
**Killer negatives:** Modeling-Tools interactive tools are UI-locked (need a live ToolManager);
`UDynamicMeshPool`'s MaxPoolSize failsafe is a **permanent leak**, not a backstop
(`UDynamicMesh.cpp:563-578`) — the bridge must enforce its own handle cap; every editor-subsystem
call **silently returns zeros during PIE** (`CheckIfInEditorAndPIE`) — all such endpoints must detect
PIE and error; cooked meshes have no source models and stock `CopyMeshFromStaticMesh` range-check
*asserts* on them (pre-check `GetNumSourceModels()`, fall back to the exported LODResources converter).

### F — World and level ([work/F_world_level.md](work/F_world_level.md))

The town-road gap closes: `ALandscapeProxy::EditorApplySpline` is fully exported, so
`apply_spline_to_landscape` (Tier-0) deforms and paints terrain along any existing spline component,
with real landscape splines (`create_landscape_spline` + rasterise + read-back) as the native lane.
Sublevels get a full lifecycle (create/add/remove/visibility/streaming/current), sized for mod maps —
IslaSombra is cooked World Partition, so WP *authoring* is de-scoped by evidence, and read-only
`list_data_layers` is the entire WP surface. The existing `add_foliage_instances` was exposed as an
impostor (detached HISM holder, invisible to foliage tooling); the real `FFoliageInfo::AddInstances`
route is specced as `paint_foliage`. 25 entries: 14 CONFIRMED, 10 CORRECTED, and the audit's only
DEMOTION (`create_level_instance`, hard-coded modal Save-As).
**Tier-0:** `apply_spline_to_landscape`.
**Killer negatives:** landscape spline classes have no fully-exported rebuild path (vtable dispatch +
the exported EditorApplySpline fallback); landscape **edit layers are contractually off** —
`create_landscape` sets `bCanHaveLayersContent=false`, and `EditorApplySpline` silently no-ops on
layered landscapes, so a future enable must own the sculpt/paint rewrite in the same change; Landmass
is a scripting dead end; six of the 21 modal traps in §8 are sublevel utilities from this axis.

### G1 — AI, navigation, NPC routing ([work/G1_ai_navigation.md](work/G1_ai_navigation.md))

The "nothing makes an NPC walk" gap resolved into a verified map of how DDS2 actually moves pawns —
four lanes on the stock AIModule: task spline-walks (`BP_SegmentedPathTaskMarker.PathSpline`), chained
patrol-route spline actors with faction gates, opponents on `ADetourCrowdAIController` + one
BehaviorTree, and a native `BaseNPC` locomotion spine under a dialogue-only BP shell. All four were
live-confirmed by class-shape probes ([work/LIVE_PROBES.md](work/LIVE_PROBES.md) G1) — no
Pedestrian/Crowd/Citizen asset family exists. The bridge story: `pie_move_pawn`/`pie_move_status`
(a plugin-side leg queue, because `FAIMoveRequest` holds exactly one goal), `find_path` as the numeric
backbone, plus projection/raycast/random-point/nav-area/link/modifier primitives. 17 entries:
16 CONFIRMED, 1 CORRECTED.
**Tier-0:** `pie_move_status`, `find_path`, `pie_move_pawn`.
**Killer negatives:** BehaviorTree **and** EQS graph authoring are UI-locked (unexported node classes
inside Slate-toolkit flows) — running them is fine, authoring is not; DDS2 impact low since all 6
shipped BTs are cooked (graphs stripped) anyway; MassAI/MassCrowd are absent (game predates Mass).

### G2 — Sequencer, UMG, Enhanced Input ([work/G2_sequencer_umg_input.md](work/G2_sequencer_umg_input.md))

Both roadmap UMG gaps close zero-dep (`list_widget_tree`, `reparent_widget` — Tier-0), and the live
census re-tiered the whole axis: the real UMG surface is **279 cooked WidgetBlueprintGeneratedClass**
assets (plain WidgetBlueprint counts only 54), Enhanced Input is small-but-real (62 InputActions,
5 IMCs), and Sequencer is nearly absent from game content (3 game LevelSequences) — which is why the
sequence chain is deliberately the last Tier-1 batch despite being fully verified. The sequencer
object model is built on method-level `MOVIESCENE_API` (create/bind/track/section/keys), and the same
endpoints double for UMG widget animations — that reuse is what keeps their value above the raw
census. MovieRenderPipeline is transitively enabled via DLSSMoviePipelineSupport, so headless movie
rendering is a request/poll pair away. 17 entries: 11 CONFIRMED, 6 CORRECTED.
**Tier-0:** `list_widget_tree`, `reparent_widget`.
**Killer negatives:** SequencerScripting extension classes have no export macros and Private-header
channel wrappers (the "plugin disabled" half of that negative was **overturned** — it is transitively
enabled and reflection-callable, kept as a fallback surface); `UWidgetAnimation::BindPossessableObject`
asserts without a live preview widget (write `AnimationBindings` directly); the InputAction/IMC
factories open a modal class picker (both assets are plain DataAssets — `NewObject` instead).

### G3 — Niagara, audio, physics ([work/G3_niagara_audio_physics.md](work/G3_niagara_audio_physics.md))

Census-grounded: 38 NiagaraSystems, 354 SoundCues, 3,753 SoundWaves, **185 MetaSoundSources** (heavily
used — the 5.3 builder API was verified end-to-end for a create recipe), 163 PhysicsAssets, and zero
PhysicalMaterials/GeometryCollections in the project. Niagara `User.*` parameters need dedicated
setters (they live in a serialized parameter store unreachable by `set_property`); spawn/activate/
particle-count/compile-poll round out a workable FX loop. SoundCue authoring (create + node-tree
extension) covers the 80% mod-audio case. 15 entries: 11 CONFIRMED, 4 CORRECTED.
**Tier-0:** none; anchors are `set_niagara_user_parameter` and `create_sound_cue` (Tier-1).
**Killer negatives:** GAS is a four-way negative (plugin disabled, absent from uproject and game
Build.cs, class absent live) — zero endpoints; Niagara module-stack authoring is a one-way street in
5.3.2 (module *add* is exported, every *remove* overload is not) — deferred until a removal story
exists; Chaos fracture is plugin-gated and UI-locked; `UNiagaraSystem::PreSave` implicitly blocks on
compilation — `save_package` must gate on the compile poll.

### H — Data, curves, config, savegames ([work/H_data.md](work/H_data.md))

The full datatable authoring loop closes (create table, per-row delete/duplicate/rename/move, CSV
round-trip, composite parent stacks), plus structured `FRichCurve` editing (all method-level
`ENGINE_API`), string tables, config read/write, struct-member in-place editing, and savegame
enumeration/reading. Live probes proved cooked curves keep **full key data** (times, values, tangents,
interp modes) and cooked *Database tables serialise completely — the data lane works against base-game
content. Census: 379 DataTables live (214 under /Game/DataTables, 95 under Databases proper — the
Phase-1 "122" figure matched no live scope and was retired); CurveTable count is **0**, so curve-table
endpoints have zero project demand despite being viable. 25 entries: 21 CONFIRMED, 4 CORRECTED —
after the axis's false-completion stamp was caught and the whole pass re-run (§2).
**Tier-0:** `read_curve`, `set_curve_keys`.
**Killer negatives:** this axis found the modal-trap class — `UDataTableFactory::ConfigureProperties`
and three sibling factories open modal dialogs (never call ConfigureProperties; `IAssetTools::CreateAsset`
verified to skip it); `UCurveTable::AddRichCurve` is a hard `check()` crash on simple-mode tables (and
the factory pre-seeds one bad row); RamaSaveSystem shipped source is a stubbed SDK reconstruction —
reflection-only route, with the stub-vs-real DLL question later settled by P3 (§6); string-table edits
are not undoable (private non-UPROPERTY storage).

### I — Diagnostics and observation ([work/I_diagnostics.md](work/I_diagnostics.md))

The numbers-for-correctness axis: `get_perf_stats` (every global verified — FPS, thread ms, draw
calls, GC recency), `log_tail` (a GLog ring-buffer device with monotonic sequence ids and an explicit
thread contract), structured MessageLog reads, bulk property watches (200 pairs/call for PIE loops),
deterministic editor↔PIE path mapping, a world-state CRC, automation-test enumeration/run via the
framework directly, and Insights traces. `trace_start` passes the no-console-wrapper rule with an
explicit argument (structured returns the console cannot produce) — ratified in
[04_OPEN_QUESTIONS.md](04_OPEN_QUESTIONS.md) §1.3. 10 entries: 9 CONFIRMED, 1 CORRECTED.
**Tier-0:** none; `get_perf_stats`, `log_tail`, `get_properties_bulk` anchor Tier-1.
**Killer negatives:** `GAverageFPS/GAverageMS` have no public-header declaration (the engine's own
consumers re-declare extern locally — copied, with the fork-fragility wart documented);
`IMessageLogListing::GetFilteredMessageCount` does not exist in 5.3.2; **no UnrealPak.exe exists on
this machine** — retoc is the only pack lane, which shaped axis J's pipeline endpoints.

### J — DDS2 project-specific ([work/J_dds2_project.md](work/J_dds2_project.md))

The project-shape axis: a systems map (native QuestManager/TownStatusManager/BaseNPC spine; quests,
shops and dialogue are *compositions* over existing endpoints and were documented rather than
duplicated), UE4SS mod inventory and structured modloader-log reads, pak verification via the
Core-only `FIoStoreReader`, and the cook lane: `trigger_cook` stays plan-only, and the real execution
lane is designed as `mod_package_request/_status` (request+poll around retoc). `mount_pak` is viable
with both halves of its central caveat cited: the premade asset registry is blind to *newly mounted*
paks (boot-mounted DLC containers are fully visible — the refinement is live-proven), so mounted mod
content is loadable by exact path but invisible to `find_assets` until an AppendState lane exists.
8 entries: 8 CONFIRMED — the wave's clean-verification control (§2). DLC model live-confirmed:
exactly 2 GameFeatureData assets, and the casino DLC ships a native runtime module
(`DDS2CasinoRuntime` reflects live).
**Tier-0:** none; `list_native_classes`, `list_ue4ss_mods`, `verify_pak_contents` lead Tier-1.
**Killer negatives:** no editor→live-game control channel exists (UE4SS Lua runs in the shipping game
process; the bridge can only tail its log); no population-manager class exists; two editor instances
on one project kill **both** (SQLite lock in AssetSearch) — auto-relaunch logic must check first.

---

## 5. The reconstructor (K) — cooked-Blueprint decompilation over HTTP

Axes [work/K1_reconstructor_toolkit.md](work/K1_reconstructor_toolkit.md) (read/analysis surface) and
[work/K2_reconstructor_pipeline.md](work/K2_reconstructor_pipeline.md) (pipeline, verification,
coupling model). This was the 10-prompt's Phase 1 — "highest value per unit of effort in the entire
project" — because MifKismetReconstructor already solves the #1 capability gap ("cooked Blueprint
graphs are unreadable") but is reachable only from the editor console.

**What it can do.** Disassemble any cooked function's Kismet bytecode to a JSON statement stream;
dump a whole cooked BP's per-function bytecode inventory with opcode histograms; census events with
recovered ubergraph entry offsets; analyse/slice ubergraphs (prologue shape, per-event reachability,
shared-latent detection); reconstruct a cooked function — or a whole Blueprint — into real, compilable
K2 graphs; and verify fidelity by recompiling a throwaway child and diffing recompiled-vs-cooked
bytecode, with drift classification (intentional vs real) and a corpus-wide census mode.

**The corrected console census.** The 10-prompt said "eleven console commands". The verified census is
**7 commands + 5 CVars — nothing else registers** (K1 surface inventory, every registration read):
5 plugin-side commands (`mif.kr.FindBP/ListBP/DumpBP/Reconstruct/AnalyzeUbergraph`) all sit inside
`#if MIF_KR_DEBUG` — throwaway debug surface whose header says "Ship OFF before any release" — so
endpoints must bind the ungated building blocks underneath, never the `MifKr_*` statics. The remaining
2 commands (`mif.kr.ReconstructAll`, `mif.kr.VerifyFidelity`) live in the **engine fork**
(`CompiledBlueprintCopyAction.cpp:1345-1348`, `:1472-1475`), not the plugin. The 5 CVars
(`Events`, `LatentResume`, `ClassifyIntentional`, `DriftCensus`, `DumpFull`) are behaviour gates, not
operations — the task hint's `kr_events`/`kr_latent_resume` endpoint names were correctly **refused**
(CVar toggles are `run_console` territory; the two that affect measurements become per-call
set/restore params on `kr_verify_fidelity`).

**The coupling model — decision: registration interface (option b).** A hard link was rejected with
evidence: MifBridge would fail to load whenever the reconstructor is absent (inverting the deliberate
soft-delegate design), it would force a large permanent export surface out of the reconstructor's
Private/ code, and every future provider would add another hard dependency to the one plugin that must
always load. The recommended design is specced executable in K2 §B: a new
`MifBridge/Public/MifBridgeEndpointRegistry.h` — MifBridge's **first** exported symbols — with
`FExternalEndpointDesc {Name, Bucket, Provider, Summary, Handler}` and
`RegisterExternalEndpoint/UnregisterExternalEndpoints`. Providers register at module startup;
`self_audit` gains a `provider` field per endpoint; externals are single-bucket by construction so the
policy-contradiction class cannot exist for them; `IsCompileHeavyEndpoint` needs zero changes (it
derives from self-managed, so external kr_* jobs are automatically barred from `batch`). Load order is
proven, not hoped: the reconstructor loads at `Default` phase, MifBridge binds routes at
`PostEngineInit` — strictly later — and the registry is a function-local static, safe before
StartupModule. The complete 11-file touch list (with a suggested `kr_ping` proof batch) is in K2 §C.

**Quick wins vs the one engine refactor.** `kr_dump_blueprint` and `kr_disassemble_function` need
**zero export promotions** — `FKismetBytecodeDisassemblerJson` is already
`MIFKISMETRECONSTRUCTOR_API`, and under model (b) the other internals stay private because the
handlers live next to them. The verify family (`kr_verify_fidelity`, `kr_drift_census`,
`kr_classify_drift`) is the one place needing a cross-repo change: `RunReconstructOnce`,
`PopulateUncookedCopy` and friends are file-local statics in the engine fork, and the only exported
mint (`CreateEditableBlueprintCopy`) saves persistent assets — useless for throwaway verification. The
spec calls for **one** `KISMET_API RunHeadlessFidelityVerify` refactor of `VerifyFidelityCmd`
(:1356-1470) in the fork's existing modkit files (decision gate §10).

**The async job-model constraint.** `FHttpServerModule` ticks on the **game thread** — the same thread
a running reconstruction occupies — so mid-reconstruct progress polling is *impossible by
construction*: while a job runs, no HTTP request is served. Consequence, designed rather than fought:
single-BP jobs are atomic deferred-tick operations (request returns immediately, work happens next
tick, status reads a completed result); only the corpus census slices — one BP per tick, GC every 25,
mirroring the engine batch loop's own GC cadence — and its status endpoint therefore shows real
progress. One job slot, four request endpoints sharing it.
**Tier-0:** `kr_disassemble_function`, `kr_dump_blueprint` (K1); `kr_reconstruct_request`,
`kr_reconstruct_status` (K2).
**Killer negatives:** raw IR endpoints refused by design (the intermediate format holds un-rooted
`UFunction*` pointers valid only under a GC scope guard — any cross-call handle would dangle);
standalone single-event reconstruction not viable (event node spawning lives engine-side, unexported);
`mif.kr.ReconstructAll` cannot be synchronous (it blocks the editor for the whole sweep).

---

## 6. The other plugins (P) — layout, stub SDKs, GameFeatures

### P1 — Graph auto-layout ([work/P1_graph_layout.md](work/P1_graph_layout.md))

The 10-prompt's Phase-4 gap #5 ("40 spawned nodes heaped at the origin"). Headless BlueprintAssist
formatting is **impossible** — proven from source, not assumed: `FBAGraphHandler`'s only constructor
takes `SDockTab` + `SGraphEditor`, `FormatNodes` early-outs without a live graph panel, and node sizes
come from Slate's `GetDesiredSize()`. But BA is unusually well exported (78 `BLUEPRINTASSIST_API`
symbols), so **Plan A** is a request+poll pair that opens the real Blueprint editor, lets BA attach,
kicks its exported format entry points and polls its own progress accessors
(`format_graph_ba_request/_status`, Tier-2). **Plan B** — the Tier-0 `format_graph` — is a zero-dep,
deterministic Sugiyama-lite layered-DAG layout implemented bridge-side over the existing `move_node`,
because engine 5.3.2 has **no native layout API at all** (swept: `EdGraphUtilities`, schemas, the BT
graph's private layouter). AutoSizeComments exports settings/cache but none of its resize logic —
`fit_comment_to_nodes` computes the union rect itself. ElectronicNodes, FlatNodes and
NS_BlueprintToText export nothing callable (negatives). Bonus: `export_graph_text` — lossless T3D
clipboard-format export of any graph — fell out of the same sweep.

### P2 — Oceanology / Riverology / FGear ([work/P2_world_vehicle_plugins.md](work/P2_world_vehicle_plugins.md))

The axis-defining discovery: all three "third-party with source" plugins are **SDK-style stub
reconstructions** — real UCLASS/UPROPERTY/UFUNCTION declarations matching the cooked game's reflection
layout, with empty native bodies (`GetWaveHeightAtLocation { return FVector{}; }`,
`setSteering() {}`, `getKMHSpeed() { return 0.0f; }` — all cited at line). The editor loads DLLs built
from these stubs (matching local UHT artifacts and one shared BuildId prove it). Consequences for
endpoint design: reflection data is *real* — `set_property`/`get_property`/`spawn_actor_in_level`
work fully, and values authored this way cook into mods that behave correctly in the shipped game
(which runs the vendor's real code) — but any endpoint whose value depends on *executing* a native
plugin function in the modkit editor returns zeros. That kills the headline candidates
(`get_ocean_height`, `pie_drive_vehicle`, vehicle telemetry) as worse-than-nothing silent-wrong-number
endpoints; each negative carries an implementation-ready citation for the day real binaries appear.
The one proposal that survives — and generalises far beyond these plugins — is
**`call_object_function`** (Tier-1): invoke any named UFUNCTION on any objectPath via
FindFunction/ProcessEvent, the only lane that executes **cooked-BP bytecode** (which, unlike the stub
natives, is real and runs in-editor). It doubles as the general salvage for every reflection-callable
surface the audit ruled out of dedicated endpoints.

### P3 — GameFeatures, thumbnails, everything else ([work/P3_sessions_misc_plugins.md](work/P3_sessions_misc_plugins.md))

The one high-value find is GameFeatures: both DLC game-feature plugins sit in **`ErrorRegistering`**
with `ErrorCode=GameFeaturePlugin.StateMachine.Registering.Plugin_Missing_GameFeatureData` —
live-verified from the editor log and the state machine, with the two GameFeatureData assets
(`/ChristmasDlc/ChristmasDlc`, `/DDS2Casino/DDS2Casino`) present as container assets but unloaded.
`get_game_feature_state` (Tier-1) makes that diagnosis machine-readable; the
`change_game_feature_state_request/_status` pair (Tier-2) can drive states and capture transition
error codes — with the documented limitation that *past* transition errors are unrecoverable through
any public API (log or self-initiated-transition capture only). `render_asset_thumbnail` rides the
engine's ThumbnailTools. The ruled-out list is long and deliberate, one cited paragraph each so no
future session re-treads them: AdvancedSessions/AdvancedSteamSessions (real code, but 38 unexported
statics whose value is runtime/PIE-session-shaped, reachable via `call_object_function` if ever
needed), RamaThumb and GamepadVirtualCursor (stubs), Hermes/RedTalaria (uplugin-only shells, no
Source), DLSS/NIS/Streamline (DLSSLibrary's `QueryDLSSSupport` stub returns a hardwired `Supported` —
actively misleading), BugSplat, BlueprintFileUtils (redundant + attack surface). P3 also settled H's
open question as a side effect: the RamaSaveSystem DLL carries the same local stub-build timestamp as
the other stub plugins — **the loaded DLL is the stub build**, so `read_rama_savefile`'s live probe
will fail until real binaries appear.

---

## 7. Repairs (Q + the standing defect list)

Axis [work/Q_gap_rootcauses.md](work/Q_gap_rootcauses.md) root-caused the 10-prompt's two open defects
with verbatim engine citations; [03_GAPS_AND_RISKS.md](03_GAPS_AND_RISKS.md) §7 holds the full
bridge-side defect list. Both Q entries are Tier-0 behaviour changes.

**`pie_status` misreported "stopped" during live play — root cause: `GEditor->PlayWorld` is a per-tick
scratch pointer, and out-of-process sessions are unobservable.** The handler derived session state
from one pointer that the engine reassigns to each ticked PIE context in turn, leaves pointing at
whichever context the render loop visited last, and — the corrected Phase-2 mechanism — nulls or
re-points *synchronously inside `LoadMap`* via `FWorldContext::SetCurrentWorld`'s conditional
retargeting whenever a context travels (DDS2 travels on every play session: IslaSombra → OpenWorld).
Any endpoint equating `PlayWorld == null` with "not playing" is structurally wrong; multi-instance
sessions add a last-writer-wins race on top; and "Standalone Game" sessions are *genuinely*
unobservable because the engine wipes its own session state right after launching the process
(`PlayLevelNewProcess.cpp:57-59`) — the repair reports that truth as a fixed documentation note
instead of guessing. The repair replaces the pointer read with context enumeration
(`CollectPIEWorlds` — already in the plugin, previously used only by `spawn_actor_in_pie`) and a real
state machine: queued/starting/running/simulating/travelling/stopping/stopped with a per-instance
array. Batch A found MifBridgePIE.cpp had already been rewritten this way at 01:47 alongside
`spawn_actor_in_pie`; Q's pass confirms the current source against the root cause, with
multi-instance reporting the remaining verification arm.

**`snap_actors_to_ground` reported ~112/303 missed on flat landscape — root cause: first-blocking-hit
truncation.** The handler's comment believed `LineTraceMultiByChannel` returns every hit along the
ray; the engine contract says the opposite — *"Only the single closest blocking result will be
generated, no tests will be done after that"* (`World.h:1956`), and under default response params
every channel blocks. So any road, floor mesh or prop between the trace start (1 km up) and the
landscape becomes the terminal hit, the landscape never enters the hit list, and the ground filter —
which was always correct — finds nothing. 37% of pivots over street furniture in a dense cooked town
is unremarkable. The repair: a penetrating multi-trace (`ECR_Overlap` response override) so the
landscape is reachable through blocking geometry, plus per-actor diagnosis in the response. The filter
stays, per the mission's constraint; the truncated hit list was the bug.

**Already fixed and live-proven this session** (details and proofs in [06_IMPLEMENTED.md](06_IMPLEMENTED.md)):

- `find_assets` unknown-parameter strictness — the audit's live-found instance of the house #1 bug
  class (a `className` typo silently returned all 37,131 assets). Now: unknown keys error by name with
  the accepted set; `className`/`type` work as aliases; the guard covers all five handlers in
  MifBridgeCooked.cpp; the `RejectUnknownParams` helper was promoted to a single shared implementation.
- server.py drift — `diagnose_landscape_draws` had no MCP tool; added, restoring the three-way
  registry to 160==160==160 (now 165 across all three).
- Bucket hygiene — `describe_class` and `list_enum_values` moved to the read-only bucket after purity
  verification; proven live with the new `list_transactions` (undo queue unchanged after reads).
- The latent server.py `spawn_actor_in_pie` bug — the tool definition sat *after* the
  `if __name__ == "__main__": main()` guard, so it was counted by grep but never registered at
  runtime. Relocated above `main()`.

**Still open in the §7 repair list** (deliberately untouched, each needs its own decision or pass):
the `add_foliage_instances` impostor (keep-and-document recommended — §10), the `connect_pins`
K2-schema hardcode (regression-gated Batch-0 fix — §10), `trigger_cook`'s hardcoded install paths,
`get_datatable_row`'s O(whole-table) reads (experiment E4), and the repo-wide silent-param sweep
beyond MifBridgeCooked.cpp.

---

## 8. Risk digest

Compressed from [03_GAPS_AND_RISKS.md](03_GAPS_AND_RISKS.md) — **read that file before implementing
anything**; the mitigations listed there are load-bearing parts of the endpoint specs, and an
implementation that skips a listed pre-check is wrong even if it appears to work.

**Modal-trap inventory (21 entries, §2).** A modal dialog opened from a mid-frame HTTP handler
deadlocks the editor. The traps cluster in four families:

| Family | Representative traps | Standing mitigation |
|---|---|---|
| Factory `ConfigureProperties` | DataTable :176, CurveTable :55, Curve + DataAsset SClassPickerDialogs | Never call ConfigureProperties on any factory; `IAssetTools::CreateAsset` verified to skip it |
| `CanCreateAsset` / consolidate / fixup | 3 FMessageDialogs incl. an overwrite YesNo; unsuppressable consolidate failure modals | Pre-validate names/packages/existence yourself; never implement overwrite via engine prompt |
| Sublevel utilities | add (already-present), make-current (locked), remove (locked/dirty), move-actors (reference prompts), create (SaveAs) | Pre-check `FindStreamingLevel`/`IsLevelLocked`/dirty state; hard-code the non-dialog flags |
| Slow-task dialogs mid-handler | MoviePipeline PIE executor :93/:109, PhysicsAssetUtils :343/:903, V-HACD WaitFor loop, RecompileMaterial tail | Pre-validate inputs; unattended-script guard; input-size caps; replicate non-blocking cores |

Distilled rule: grep every engine utility's .cpp for `FMessageDialog` / `EditorAddModalWindow` /
`ShowModal` / `FScopedSlowTask`+`MakeDialog` **before** wiring it into a handler.

**Blocking hazards (13 entries, §3).** Handlers run on the game thread synchronously and inline,
post-world-tick (not "mid-frame", and not via `AsyncTask` — see `02_GOTCHAS.md` §8), so a blocking
call takes the whole bridge down rather than just its own request. The blacklist:
`GetStatistics` (blocks until that material's shaders compile), `BuildReflectionCaptures`
(`FinishAllCompilation` — unbounded, plus a `check()` crash below SM5), `WaitForCompilationComplete`
(also reached *implicitly* via Niagara `PreSave`), `FinishAllCompilation`/shader flushes generally,
`UStaticMesh::Build` with non-null OutErrors (forces synchronous), registry enumeration callbacks
(run inside the registry lock — re-entry deadlocks), and the save-time/mid-handler **GC family**
(dirty-package enumeration, RecompileMaterial ×2, `GEditor->Cleanse`) that kills any unrooted UObject
the bridge holds across the call — root every handle map.

**Cooked-content rules (§4).** What survives cooking vs not, verified live where possible:

- *Stripped — endpoints refuse or degrade honestly, never silent-empty:* material expression graphs
  (`UCLASS(Optional)`), texture source data, foliage instance arrays (types survive, instances do
  not — base-game foliage cannot be removed at all), mesh source models (with a crash guard: stock
  conversion *asserts* on cooked meshes), dependency/referencer edges on container packages
  (loose→container edges survive), BP graphs (the reconstructor exists because of this), cooked WP
  maps (unsaveable; build results transient).
- *Survives — verified live:* FRichCurve full key data, DataTable rows with native structs, CDO and
  MIC property reads, reflection of native and BPGC classes, Kismet bytecode, AnimBP baked state
  machines, StaticMesh sockets and collision counts, WP data-layer reads, UFoliageType assets.
- *Registry blindness:* paks mounted after boot are invisible to `find_assets` (boot-mounted DLC is
  visible) until an AppendState lane exists.

**5.3.2 sharp edges (§5).** Twelve engine traps an implementer must engineer around, headlined by:
the UDynamicMeshPool leak-not-backstop failsafe; the `editcondition` silent-ignore class (`MinLOD`
et al.); editor-subsystem silent zeros during PIE; `FAIMoveRequest` holding exactly one goal; the RVT
enum differing from 5.4 knowledge (generalised: *never trust 5.4-era API memory against this fork*);
the `AddRichCurve` hard-check crash; the hard-assert class (5 known `check()` sites); the silent-no-op
class (`EditorApplySpline` on layered landscapes, `PilotLevelActor` without a viewport, MessageLog
auto-creating typo'd listings); and the two-editor-instances mutual kill.

Also in 03: §1's ~45 not-viable APIs each with the exported alternative named, and §6's three
**overturned negatives** (SequencerScripting is transitively enabled; `GetStatistics` blocks rather
than staling; `WaitUntilAsyncPropertyReleased` is exported) — protected from being wrongly re-killed.

---

## 9. Implementation state and roadmap

### What landed this session ([06_IMPLEMENTED.md](06_IMPLEMENTED.md))

**Batch A** — zero code changes needed: the build was already `Target is up to date` (the 01:49 DLL
had all 160 endpoints; the running editor had been on a stale 01:25 image). Relaunch + `self_audit`
160/0-contradictions; all four pending endpoints live-proven: exact camera set/get round-trip
(+`viewportCount:4`), `focus_viewport` framing a proof cube with the camera moving to the computed
vantage, and the full PIE lifecycle — deferred start, poll to `running`, `spawn_actor_in_pie` into
`/Temp/UEDPIE_0_Untitled_0` with authority, count 19→20, deferred stop, poll to `stopped`. Bonus
finding: `pie_status` had already been rewritten into the rich state machine (§7).

**Batch B** — the three repairs (§7), all live-proven post-build: unknown-param rejection naming the
key and the accepted set, alias resolution returning 379 DataTables where the old code silently
matched nothing, and bucket hygiene proven via a clean undo queue.

**Batch C** — 5 endpoints in a new `MifBridgeUndo.cpp` (session-state operations, deliberately not in
the single-asset AssetOps file), registry 160 → **165** in all three places. Proof chain: spawn cube →
`list_transactions` shows the entry (index, 6 records, 20,832 bytes) → `undo_transactions` returns the
exact title and the cube is verifiably gone → redo brings it back at {500,500,100} → `describe_class`
leaves the queue untouched → `list_dirty_packages` reports the untitled world `saveable:false` →
`save_dirty_packages` echoes the skip with the "use save_level_as" reason instead of silently
dropping it. Spec deviations (4) are documented with reasons in the log.

**Batch D** is in flight as this report is written; its landing entry goes to
[06_IMPLEMENTED.md](06_IMPLEMENTED.md) with the same proof discipline.

### Build-loop lessons (paid for today, encoded for every future batch)

- **The stale-DLL trap, caught by numbers:** the first B+C cycle piped the build through `tail`,
  masking a failing exit code, and relaunched the *stale* DLL. The `self_audit` endpoint-count check
  (160 vs expected 165) caught it immediately — the "verify with numbers" rule catching its own build
  loop. Exit codes are now taken from the build's own status before any relaunch.
- One real compile error (an `FString` vs `FName` mismatch at MifBridgeUndo.cpp:91) — fixed, not
  reverted; second cycle built clean in 19.5s with the bridge up in 10s.
- Two usage-limit interruptions (~04:00 and ~10:45) cost zero work: all research files were on disk,
  workflows resumed from cache, and the one agent that died mid-edit (Batch C, after writing the .cpp
  but before registration) was resumed by message to finish exactly the remaining step.

### The batch plan ([02_RANKED.md](02_RANKED.md) — module costs per batch)

| Batch | Scope | Count | New modules / cost | Status |
|---|---|---|---|---|
| 0 | Repairs to existing endpoints (6 Tier-0 behaviour changes + 2) | 8 | none | partially done (find_assets, server.py, buckets, pie_status-in-source); connect_pins/foliage/read_modloader_log open |
| 1 | Zero-new-dependency endpoints (21 Tier-0) | **145** | none — no Build.cs change at all | **5 landed** (Batch C ✅: list/undo/redo transactions, list/save dirty packages) |
| 2 | Materials (5 Tier-0) | 9 | MaterialEditor | next big unlock |
| 3 | Enhanced Input | 4 | EnhancedInput | census: 62 IA / 5 IMC |
| 4 | Mesh editing | 13 | StaticMeshEditor, SkeletalMeshEditor, MeshMergeUtilities, PhysicsUtilities, GeometryCollectionEngine | engine-source modules, no plugin enables |
| 5 | GeometryScript session model | 9 | plugin enable + GeometryScriptingCore/Editor, GeometryFramework | buys the ~478-function surface |
| 6 | FX & audio | 10 | Niagara, NiagaraEditor, Metasound*, AudioEditor | census-backed (38/354/185) |
| 7 | Sequencer & MRQ | 12 | MovieScene family + MovieRenderPipeline* | deliberately last (3 game LevelSequences; doubles for UMG animations) |
| R | Reconstructor kr_* via registration interface (4 Tier-0) | 10 | provider model (§5); 1 engine-fork export for the verify family | gated on decision §10 |
| 8 | Small single-module additions | 21 | one module each (ScriptDisassembler, RHI, ImageWrapper, PakFile, MessageLog, ...) | opportunistic |

**Suggested order:** finish Batch 0's remainder inside Batch-1 passes (same files) → drive Batch 1 in
5–15-endpoint slices (it contains 21 of the 36 Tier-0 items and costs nothing per build) → Batch 2
(materials, the biggest single category unlock) → Batch R part 1 (registry + `kr_ping` +
dump/disassemble — zero promotions) → Batches 3/4/5 by mission demand → 6/7/8 opportunistically.
Every batch ends with build → relaunch → `self_audit` (count + zero contradictions) → per-endpoint
numeric live proof → log entry, exactly as Batches A–C did.

---

## 10. Decision gates ([04_OPEN_QUESTIONS.md](04_OPEN_QUESTIONS.md))

**Ratified during this run** (recommendations adopted; recorded so nobody relitigates):

1. **`create_asset` boundary** (§1.1) — dedicated creators own their types with type-specific
   validation; `create_asset` covers the residue and hard-refuses owned types, naming the right lane
   in its error. Encoded in the catalogue's dedup table.
2. **Rule-5 refinement** (§1.2/§1.3) — a console-adjacent endpoint is allowed iff it adds structured
   readback the console cannot produce; `map_check` (private Map_Check → Exec + MessageLog) and
   `trace_start` both pass. The refinement text should be written into the rule when next touched.
3. **Bucket reclassification** (§1.4) — done and live-proven in Batch B.
4. **Cross-axis dedup owners** (§1.7) — B owns `list_dirty_packages`, F owns
   `build_reflection_captures`, E owns `create_physics_asset`, the compile poll is named
   `asset_compile_status` (shader poll stays separate). Applied in the catalogue.

**Still needing Andre's call:**

- **`connect_pins` rollout** (§1.6) — silent Batch-0 fix gated on a three-part regression suite
  (recipes compile clean; a real graph-edit round-trip byte-compares; anim-state connection spawns a
  transition), vs a `legacySchema:true` escape hatch. Recommendation: silent fix + suite.
- **`add_foliage_instances`** (§1.5) — keep-and-document (recommended), repair in place, or
  deprecate. Repairing in place would be a silent behaviour change to a shipped mutation — the exact
  surprise class the contract bans.
- **The engine-fork `KISMET_API` refactor** — the **one cross-repo change** in the whole plan: one
  exported `RunHeadlessFidelityVerify` in `D:/UE532`'s Kismet module, needed only by the kr_* verify
  family (the reconstruct pair ships without it). Approving it unblocks fidelity gating over HTTP;
  deferring it leaves Batch R at 7 of 10 endpoints.
- **`create_level_instance` resurrection** (§3 F) — invest in a bridge-side move+save reimplementation
  or park until an engine change. Currently DEMOTED.

**Queued live experiments** (§2, E1–E7): the IslaSombra world census (tiers F's sublevel/water/spline
endpoints — skipped by rule while "Untitled" was open), the RamaSave behavioural save/load test
(P3's stub-DLL finding predicts failure — worth one PIE run to close), find_assets strictness
regression (E3 — now largely covered by Batch B's proofs), per-row datatable serialisation (E4),
shipped-navmesh presence (E5), EQS in the editor world (E6), brain pause/resume exports (E7).
Per-axis UNVERIFIED remainders (~93 items) are itemised in §3 with the specific check that closes each.

---

## 11. Appendix — document map and run statistics

### docs/audit/

| File | Holds |
|---|---|
| [PROGRESS.md](PROGRESS.md) | The complete run log — phases, incidents, counts, resume points. Newest at bottom. |
| [00_BASELINE.md](00_BASELINE.md) | The 160 pre-existing endpoints: per-file tables, buckets, registry health, domain-coverage table. |
| [01_CATALOGUE.md](01_CATALOGUE.md) | Generated master index of all 250 entries (never hand-edit): per-axis tables, dedup resolutions, totals. |
| [02_RANKED.md](02_RANKED.md) | Full tier ranking (U+E+R) + the 10 implementation batches grouped by module cost, with ✅ marks. |
| [03_GAPS_AND_RISKS.md](03_GAPS_AND_RISKS.md) | The risk digest: ~45 non-viable APIs with alternatives, 21 modal traps, 13 blocking hazards, cooked-content limits, 12 sharp edges, 3 overturned negatives, the bridge defect list. |
| [04_OPEN_QUESTIONS.md](04_OPEN_QUESTIONS.md) | 7 policy decisions (4 ratified), 7 queued experiments, per-axis UNVERIFIED remainders, operational prerequisites, deferred-by-design list. |
| 05_FULL_REPORT.md | This report. |
| [06_IMPLEMENTED.md](06_IMPLEMENTED.md) | The implementation log — one section per batch: engine APIs cited, files, params, live proofs, spec deviations. |

### docs/audit/work/

| File | Holds |
|---|---|
| [_BRIEF.md](work/_BRIEF.md) | The shared sweep contract: invariants, the covered set (all 160), the ten verification fields, output format. |
| [LIVE_PROBES.md](work/LIVE_PROBES.md) | Phase-2 read-only bridge probes: NPC class shapes, asset censuses, cooked-curve/table/RamaSave evidence, payload shapes, confirmed/refined/refuted Phase-1 claims. |
| [A_editor_core.md](work/A_editor_core.md) … [J_dds2_project.md](work/J_dds2_project.md) | The 12 engine-axis files — the single source of truth for full ten-field entries, negatives, UNVERIFIED items, verdicts. |
| [K1_reconstructor_toolkit.md](work/K1_reconstructor_toolkit.md), [K2_reconstructor_pipeline.md](work/K2_reconstructor_pipeline.md) | Reconstructor read surface; pipeline/verify endpoints + the coupling-model spec with its 11-file touch list. |
| [P1_graph_layout.md](work/P1_graph_layout.md), [P2_world_vehicle_plugins.md](work/P2_world_vehicle_plugins.md), [P3_sessions_misc_plugins.md](work/P3_sessions_misc_plugins.md) | Layout plugins + format_graph plans A/B; the stub-SDK finding + call_object_function; GameFeatures + the ruled-out plugin list. |
| [Q_gap_rootcauses.md](work/Q_gap_rootcauses.md) | The two defect forensics entries (pie_status, snap_actors_to_ground) with repair specs. |
| index/*.rows.json | 18 machine-extracted row files + `_merged.rows.json` — feed the generated catalogue/ranking; regenerate, don't edit. |

Mission and contract documents live one level up in `docs/`: the executed mission is
[../10_FULL_SCOPE_EXPANSION_PROMPT.md](../10_FULL_SCOPE_EXPANSION_PROMPT.md); the architecture/postmortem/
gotcha contracts it enforces are `00_ARCHITECTURE.md`, `01_POSTMORTEMS.md`, `02_GOTCHAS.md`,
`08_LANDSCAPE.md`. A cross-session memory summary was saved to
`~/.claude/projects/.../memory/mifbridge-endpoint-audit.md`.

### Run statistics

- **Audit proper (Phases 0–3,** [PROGRESS.md](PROGRESS.md) **totals):** 4 workflows + 1 baseline agent,
  **40 subagents, ~8.4M subagent tokens, ~3,100 tool calls**. Phase 1 alone: 12 agents, ~2.25M tokens,
  896 tool calls, 232 proposals.
- **Full-scope delta:** workflow `mifbridge-fullscope-delta` (6 research → 3 verify → 6 extract
  agents, resumed once from cache after a usage limit), plus the Batch A/B/C implementation agents —
  bringing the whole run past **~13M subagent tokens** across 5 workflows and 55+ agents.
- **Named workflows:** `mifbridge-endpoint-sweep` (wf_ba0d3082-315), `mifbridge-audit-verify`
  (wf_81466059-eec, run across a usage-limit reset), `mifbridge-audit-synthesize` (wf_2e71d7f7-b27),
  `mifbridge-fullscope-delta` (wf_a4d0acdc-a23, resumed at 14:01 ET).
- **Incidents survived:** one mid-sweep editor/bridge outage (probes deferred, not guessed); one
  editor self-restart during P3 (recorded with the exact failing call); two usage-limit hits with
  clean resumes; one falsely-stamped verification pass (caught, re-run); one stale-DLL relaunch
  (caught by the endpoint-count check); zero data loss.
- **End state:** 165 endpoints live and healthy (`self_audit`: 0 policy contradictions,
  MIF_DECL == MIF_BIND == server.py == 165), 241 verified proposals ready to implement, and a risk
  file that means none of today's ~162 dead ends ever needs to be walked into twice.
