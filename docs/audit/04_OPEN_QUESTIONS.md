# MifBridge endpoint audit — 04 OPEN QUESTIONS

- **Date:** 2026-07-26 (Phase 3 synthesis)
- **Audience:** Andre + the implementation session
- **Scope:** everything from the audit that needs a HUMAN DECISION or a LIVE EDITOR EXPERIMENT
  before (or during) implementation. Everything already settled lives in the axis files under
  [work/](work/) — those remain the single source of truth for full endpoint specs; this file only
  collects what is still open.

Sources: the 12 verdict-stamped axis files, [work/LIVE_PROBES.md](work/LIVE_PROBES.md),
[work/_BRIEF.md](work/_BRIEF.md), [00_BASELINE.md](00_BASELINE.md), [PROGRESS.md](PROGRESS.md).

> **RE-CHECKED 2026-08-29 - see [PROGRESS.md](PROGRESS.md) for the full staleness note.** Two items
> below are resolved and marked in place rather than left to read as open: §1.4 (bucket
> reclassification) was done long ago. §1.6 (connect_pins schema fix) was genuinely still open until
> today - found independently a different way this session, fixed, and this is the entry that shows
> it was flagged correctly a month before anyone acted on it.
>
> **RE-CHECKED AGAIN 2026-08-31.** Eight entries answerable by reading source or by a read-only
> probe were worked through; every one is marked in place with that date. The headline is not
> the six answers, it is that **four of the eight rested on a premise that had stopped being
> true**, and each would have misled whoever picked it up:
>
> - **E6** says "via the implemented `run_eqs_query`". There is no such endpoint, on any build.
> - **E4**'s acceptance test - "byte-matches the current implementation" - cannot pass, because
>   the only viable route uses a different serialiser.
> - **`UK2Node_MathExpression`** was to be "taken off the denylist". No denylist exists, and the
>   real constraint is a spawn ORDERING that the code already satisfies.
> - **USelection BSP-surface** was to be closed as "irrelevant to this game", which is explicitly
>   not an allowed reason here - MifBridge is a general UE5 tool.
>
> None of that was carelessness; each entry was true when written. The lesson for reading this
> file is narrower and worth carrying: **an entry states the world as of its writing date, and
> the cheapest half of any item here is confirming its premise still holds.** Two of the four
> took under a minute to disprove.

---

## 1. Policy decisions (need Andre's call)

Each item: the question, a recommendation, and the tradeoff of taking it.

### 1.1 `create_asset` generic allowlist vs dedicated validating creators

Axis B proposes a generic `create_asset` with a 9-type factory allowlist
(DataTable, CurveFloat/Vector/LinearColor, DataAsset, StringTable, MaterialParameterCollection,
PhysicalMaterial, AnimBlueprint — [work/B_assets_registry.md](work/B_assets_registry.md) §create_asset).
Four axes independently propose dedicated creators over the SAME factory surface, each adding
type-specific validation the generic path cannot do:

| Axis | Dedicated creators | Validation the generic path lacks |
|---|---|---|
| [H](work/H_data.md) | create_datatable, create_curve, create_curve_table, create_string_table | RowStruct resolution, curve-class choice, CurveTable rich/simple mode + the AddRichCurve `check()`-crash guard, string-table namespace |
| [D](work/D_materials_rendering.md) | create_material, create_material_function, create_material_parameter_collection, create_rvt_asset | domain/blendMode enums, layer/layerBlend function kinds, MPC parameter seeding, 5.3.2 RVT enum list + protected-member reflection writes |
| [G2](work/G2_sequencer_umg_input.md) | create_level_sequence, create_input_action / create_input_mapping_context | frame-rate/duration setup; EInputActionValueType whitelist (verified: Boolean/Axis1D/Axis2D/Axis3D) |
| [G3](work/G3_niagara_audio_physics.md) | create_niagara_system, create_sound_cue, create_metasound_source | template-system seeding, cue-graph root wiring, MetaSound builder flow |

**Question**: where is the boundary?
**Recommendation**: dedicated creators OWN their types; `create_asset` covers the residue only
(DataAsset, PhysicalMaterial, AnimBlueprint — plus future one-off types nobody writes a creator
for). `create_asset` must HARD-REFUSE types owned by a dedicated creator, with an error naming
that endpoint (`"use create_datatable — it validates rowStruct"`), so two lanes never mint the
same type with different validation.
**Tradeoff**: more endpoints and a discoverability cost (agents must learn which lane owns which
type — the refusal error text is the mitigation), versus a single generic lane that silently
skips the validation, modal-dialog pre-checks (CanCreateAsset's three FMessageDialogs,
AssetTools.cpp:4287–4337), and semantic seeding the dedicated creators encode. H's coverage log
already records this handshake as its recommendation; B's Phase-2 verdict flags the overlap
unresolved.

### 1.2 `map_check` routes through `UEditorEngine::Exec("MAP CHECK DONTDISPLAYDIALOG")` — does that violate the no-console-wrapper rule?

Phase 2 found `UEditorEngine::Map_Check` is a PRIVATE member (EditorEngine.h:2544–2571) — the
direct call does not compile. The only reachable route is the public exec dispatcher
([work/B_assets_registry.md](work/B_assets_registry.md) §map_check, CORRECTED verdict). Brief
rule 5 bans endpoints that merely wrap `run_console`.
**Recommendation**: ALLOWED. The endpoint's value is not the exec — it is the structured
MessageLog readback ("MapCheck" listing via `get_message_log`, current-page-only per the
bClearLog semantics) plus the message-count delta, in ONE structured return. The raw exec emits
nothing machine-readable to the output device, so `run_console` genuinely cannot deliver this.
Level-material assignment validation (a named Tier-0 gap) surfaces here.
**Tradeoff**: this softens rule 5 into "no wrapper UNLESS the endpoint adds structured readback
the console cannot produce" — write that refinement into the rule when ratifying, or the next
audit will relitigate it. Rejecting instead means losing map_check entirely (no exported
alternative exists in 5.3.2).

### 1.3 `trace_start` console-wrapper judgment (ratify axis I's argument)

`trace.start` the console command is a thin parser over the same `FTraceAuxiliary` API. Axis I
argues NOT-a-wrapper ([work/I_diagnostics.md](work/I_diagnostics.md) §trace_start, negative #7):
the endpoint (a) returns the resolved absolute .utrace path, (b) exposes session/trace GUIDs +
active channel list as structured fields, (c) validates channel string and target path with
named-parameter errors — none of which the console route returns. Phase 2 CONFIRMED the argument
and every export.
**Recommendation**: accept. Same rule-5 refinement as 1.2 covers it.
**Tradeoff**: none technical; purely whether the rule-5 boundary drawn in 1.2 is where Andre
wants it. Deciding 1.2 and 1.3 together keeps the precedent coherent.

### 1.4 Bucket reclassification of existing `describe_class` and `list_enum_values`

**DONE - verified 2026-08-29.** Both are in `IsReadOnlyEndpoint`'s set today
(MifBridgeCommon.cpp:606), with a comment citing "audit 03_GAPS_AND_RISKS.md §7.6" - this
recommendation, acted on.

Both are read-shaped but registered in the default TRANSACTED bucket
([00_BASELINE.md](00_BASELINE.md) — flagged `*`), so every call pushes an empty undo entry:
exactly the pollution the read-only bucket exists to prevent. Flagged since the baseline pass
(PROGRESS.md Step-1 notes).
**Recommendation**: move both to `IsReadOnlyEndpoint` in Batch 0 (repairs to existing endpoints).
**Tradeoff**: a behaviour change to two shipped endpoints — `self_audit`'s bucket report changes
(clients that assert bucket lists must update), and undo no longer records these calls (that is
the point; nothing legitimately relied on undoing a read). Risk is near zero, but it is still a
contract change to a live endpoint, so it needs the same regression note as any Batch-0 fix.

### 1.5 `add_foliage_instances`: deprecate/repair vs keep-and-document (the impostor finding)

Axis F found the existing endpoint is an impostor: it builds a detached HISM holder actor, NOT
`AInstancedFoliageActor` foliage (MifBridgeAuthoring.cpp:428–478), so its output is invisible to
foliage tools, foliage stat counts, and the procedural system
([work/F_world_level.md](work/F_world_level.md) §paint_foliage). F specifies the real route
(`FFoliageInfo::AddInstances`, exported) as a new `paint_foliage` endpoint.
**Recommendation**: KEEP `add_foliage_instances` unchanged, fix only its docstring to say
honestly what it makes ("one instanced-static-mesh actor holding N transforms — a draw-call
optimisation, not foliage-system foliage"), and ship `paint_foliage` alongside. Do NOT silently
reroute the old endpoint to the foliage system.
**Tradeoff**: keeping it preserves existing callers (the HISM-holder shape is genuinely useful
for prop scattering and survives cooked levels differently) at the cost of a confusing name
living forever next to `paint_foliage`. Repairing in place would be a silent behaviour change to
a shipped mutation — the exact class of surprise the contract bans — and would break anyone
depending on the single-actor output shape. Deprecation (refuse + redirect) is the middle option
if Andre prefers one lane; that is a user-visible break and should be a deliberate call.

### 1.6 `connect_pins` schema fix: behaviour change to a heavily-used endpoint

**DONE - 2026-08-29, over a month after this was filed.** The code had moved (the shared logic is
now `DoConnect` in MifBridgeNodes.cpp, not `ConnectPinsChecked` at the line cited below - a
refactor since this was written, not a sign the bug moved with it) but the bug was still exactly
this: `Schema` was resolved via a hardcoded `K2()` helper regardless of which graph the pins
belonged to. Found again independently this session via docs/06_CAPABILITY_ROADMAP.md rather than
via this file, fixed by resolving the schema from the pin's own owning graph instead, verified live
against a real AnimGraph (a pose output's fan-out is now correctly restricted the way
`UAnimationGraphSchema` requires, which K2's schema allowed through). See
FEATURE_PARITY_SPEC.md's dated entry and `tools/test_anim_nodes.py` T553-554 for the full account.
This item sat correctly identified and unactioned for over a month - worth remembering as a
concrete case for why re-reading old audit files periodically is worth the time.

`ConnectPinsChecked` hardcodes the K2 schema CDO (MifBridgeCommon.cpp:1494, plus BreakPinLinks
through the same CDO :1505–1506), so any graph whose schema overrides connection semantics
(anim graphs, state machines, transition schemas, widget graphs) silently gets K2 behaviour.
The fix — use `Graph->GetSchema()` — is verified viable and is a prerequisite for the whole
AnimBP authoring trio ([work/C_blueprints_graphs.md](work/C_blueprints_graphs.md)
§connect_pins behaviour change, CORRECTED verdict).
**Question**: rollout strategy for changing the most-used wiring endpoint in the registry.
**Recommendation**: ship as a silent fix in Batch 0, gated on a regression suite run BEFORE
merge: (a) every existing K2 recipe (`recipe_*`) compiles 0 errors / 0 warnings after rebuild,
(b) an existing-mod graph edit round-trip (connect/disconnect/reconnect on a real editable
child) byte-compares `get_node` output pre/post-fix, (c) the new positive case — connecting two
anim states spawns a transition node (count check). Rationale: for K2 graphs the graph's schema
IS `UEdGraphSchema_K2`, so behaviour is provably identical; a flag or v2 endpoint would fork the
API for a change that is invisible to every current caller.
**Tradeoff**: "provably identical" rests on the claim that no existing caller wires pins in a
non-K2 graph and DEPENDS on the wrong K2 semantics — if one does, the silent fix changes its
result. The regression suite is the insurance; a `legacySchema:true` escape-hatch parameter is
the cheap fallback if paranoia wins.

### 1.7 Naming/dedup ratifications (four cross-axis collisions)

All four were flagged by Phase 2; none were resolved in the axis files. The catalogue needs one
owner each — ratify (or override) these:

| Collision | Recommendation | Notes / tradeoff |
|---|---|---|
| `list_dirty_packages` — [A](work/A_editor_core.md) vs [B](work/B_assets_registry.md) | One endpoint. Merge: B's `kind` param (content/world/all) + A's `isCookedOrigin`/`saveable:false` row flags. Owner: **B** (registry axis owns package-state reads); A's `save_dirty_packages` cross-references it. | B carries the Phase-2 class-attribution fix (the :144 overload is UEditorLoadingAndSavingUtils; use the FEditorFileUtils trio :402/:409/:417). No functional tradeoff — pure editorial. |
| `build_reflection_captures` — [D](work/D_materials_rendering.md) vs [F](work/F_world_level.md) | One endpoint. Owner: **F** (level-side spec), folding D's hazard analysis. Both Phase-2 verdicts independently found the same two MANDATORY pre-checks: refuse while `FAssetCompilingManager` has remaining work (the impl calls FinishAllCompilation — unbounded stall) and refuse below SM5 feature level (`check()` = crash). Response warns MapBuildData persistence on cooked maps. | F's own verdict nominates itself as "the safer of the two to keep"; D's log claims the hazard analysis. They agree on substance — this is purely which file the catalogue points at. |
| `create_physics_asset` — [E](work/E_geometry_meshes.md) vs [G3](work/G3_niagara_audio_physics.md) | One endpoint. Owner: **E** (it pairs with skeletal_mesh_info / regenerate_skeletal_lods and the asset-compile poll), folding G3's PhysicsAssetUtils hazard notes (modals at PhysicsAssetUtils.cpp:343/:903 on the lower-level route). Encode E's finding: destination path is DERIVED (`<SkelMeshPackage>_PhysicsAsset`), no path param — response must return the derived path. | G3 keeps `set_physics_constraint` / `create_geometry_collection`; only the one name moves. |
| `get_asset_compilation_status` ([B](work/B_assets_registry.md)) vs `asset_compile_status` ([E](work/E_geometry_meshes.md)) | One endpoint over the same FAssetCompilingManager counters. **Name: `asset_compile_status`** (matches nav_status/pie_status precedent), folding B's per-manager breakdown (`GetAssetTypeName` — verified to exist, AssetCompilingManager.h:49) and E's static-mesh extras (remainingStaticMeshes, asyncEnabled). | Keep D's `shader_compile_status` SEPARATE — it polls GShaderCompilingManager (shader jobs), a different queue; folding them would conflate two "done" conditions. This one poll endpoint serves import_asset, build_static_mesh, commit_dynamic_mesh, set_static_mesh_lods, set_nanite_settings, regenerate_skeletal_lods. |

---

## 2. Live-editor experiments queued

Each: the exact probe, and what its outcome decides. The first two are the big ones — both were
explicitly deferred by the Phase-2 live-probe pass ([work/LIVE_PROBES.md](work/LIVE_PROBES.md)).

| # | Experiment | Exact probe | What it decides |
|---|---|---|---|
| E1 | **F world census on IslaSombra** (skipped by rule in LIVE_PROBES — editor had "Untitled" open; `load_level` is a mutation and was out of scope for the read-only pass) | `load_level /Game/Maps/IslaSombra/IslaSombra`, then `list_level_actors` with classFilter probes for `LevelInstance`, `LandscapeStreamingProxy`, `WaterBody` (any subclass), `LandscapeSpline`, plus `landscape_info` and a `list_data_layers`-shaped read once implemented | Whether the shipped world actually contains level instances / streaming proxies / water bodies / landscape splines — tiering for F's sublevel + water + landscape-spline endpoints, and whether `list_data_layers` has real targets. Presence is currently UNKNOWN, not "none" ([work/F_world_level.md](work/F_world_level.md) UNVERIFIED). Also closes the `AWorldSettings.WorldPartition` pointer read and `ULandscapeInfo::GetSplineActors` shape probes. |
| E2 | **RamaSave stub-vs-real behavioural test** (reflection probe done — all 3 classes reflect fully, 130/28/29 functions; reflection cannot prove function BODIES are non-stub) | `start_pie` → invoke `RamaSave_SaveToFile` via reflection (or trigger an in-game save) → assert the save file exists on disk with size > 0 → `RamaSave_LoadFromFile` → assert one known actor property round-trips | Whether the modkit editor loads the REAL RamaSaveSystem DLL or the stubbed SDK reconstruction ([work/H_data.md](work/H_data.md) UNVERIFIED). Gates `read_rama_savefile` and any bridge-side save/load orchestration. |
| E3 | ~~**find_assets param-strictness fix verification**~~ **CLOSED 2026-08-31 - VERIFIED FIXED.** `find_assets {"recursive": false}` is refused by name with a reason ("not implemented - pathPrefix matching is ALWAYS recursive"); `className` is accepted as a documented alias, not silently ignored; regression holds - `class`/`limit` returns 1709 assets. Original entry | After the Batch-0 fix: `find_assets {"recursive": false}` must return an error naming the unknown parameter; regression: `class`/`pathPrefix`/`nameContains`/`origin`/`recursiveClasses`/`limit` calls return byte-identical results to pre-fix | Closes the live-found defect: find_assets silently ignores unknown params (J probe — `recursive:false` accepted, used neither; G3 independently hit it with `className`). This is invariant-4's #1 bug class shipping in a read endpoint today ([work/J_dds2_project.md](work/J_dds2_project.md) implication #8). |
| E4 | **get_datatable_row per-row serialisation path** - **ANSWERED 2026-08-31: the proposed route is IMPOSSIBLE, and a different one works.** `TDataTableExporterJSON::WriteRow` exists (DataTableJSON.h:49) but that header is under Engine/**Private**/ and the method carries NO `ENGINE_API` - a plugin can neither include it by a public path nor link it. The only public DataTable JSON API is `UDataTable::GetTableAsJSON()` (DataTable.h:328 on 5.3, :300 on 5.7), whole-table on both engines. So the current O(whole table) implementation is FORCED by the engine's public surface, not a quality shortcut - which is worth knowing before anyone 'fixes' it. THE VIABLE ROUTE: `UDataTable::GetRowMap()` is public inline (:99) returning `TMap<FName, uint8*>`, and `FJsonObjectConverter::UStructToJsonObject` is `JSONUTILITIES_API` - so look the row pointer up by name and serialise that ONE row's struct directly, O(1) + one row. CAVEAT that changes the acceptance test: that is a DIFFERENT serialiser, so the entry's own 'byte-matches the current implementation' criterion will not hold and should be replaced by a field-level equivalence check. Original entry | Read `DataTableJSON.h` (FDataTableExporterJSON) for a per-row entry point + export macro; if exported, re-implement get_datatable_row to serialise ONE row instead of dumping the whole table and extracting; behavioural check: output on `/Game/DataTables/Databases/CurrencyDatabase` row `DOLAR` byte-matches the current implementation | Whether the quality fix is a clean swap ([work/J_dds2_project.md](work/J_dds2_project.md) UNVERIFIED). Matters at 379-table / 170-row scale; current path is O(whole table) per row read. |
| E5 | **Do shipped cooked maps contain navmesh tiles?** | With IslaSombra open (rides E1): `nav_status` → is tile count > 0 on load, without `build_navmesh`? | Whether DDS2 ships a cooked navmesh or builds at runtime — decides if bridge users must always `build_navmesh` first on base-game maps, and what `pie_move_pawn` docs promise ([work/G1_ai_navigation.md](work/G1_ai_navigation.md) Phase-2 pickup #5). |
| E6 | **EQS manager presence in the editor (non-PIE) world** - **THE PROBE ROUTE DOES NOT EXIST, checked 2026-08-31.** This entry says "via the implemented `run_eqs_query world:editor`" and "the endpoint already errors cleanly on a null manager". There is no `run_eqs_query` on this build - not in MifBridgeHandlers.h, not in the MCP server, not in self_audit's 446. The only AI-adjacent endpoints are `describe_behavior_tree`, `list_blackboard_keys` and `add_blackboard_key`. So the question stays OPEN but needs a different probe (a get_property read against the editor world's AISystem was the entry's own alternative), and whoever picks it up should not go looking for an endpoint that was never built. Original entry | One probe: does `UEnvQueryManager::GetCurrent(EditorWorld)` return non-null in this fork? (Via the implemented `run_eqs_query world:editor`, or a get_property probe on the editor world's AISystem) | Whether `run_eqs_query` can support `world:editor` or stays PIE-only. The endpoint already errors cleanly on a null manager, so it is safe either way — this only settles the docs and the default ([work/G1_ai_navigation.md](work/G1_ai_navigation.md) UNVERIFIED #1). |
| E7 | ~~**UBrainComponent PauseLogic/ResumeLogic export check**~~ **ANSWERED 2026-08-31 - FEASIBLE, but not through the base class.** The pair is ASYMMETRIC and identical on 5.3 and 5.7: `UBrainComponent::ResumeLogic` is `AIMODULE_API` (BrainComponent.h:163), but `UBrainComponent::PauseLogic` is an inline EMPTY body with no export macro (:157) - so a call through a UBrainComponent* would not link, and the base body does nothing anyway. `UBehaviorTreeComponent::PauseLogic` IS exported (`AIMODULE_API virtual void PauseLogic(...) override`, BehaviorTreeComponent.h:116). A pause/resume endpoint therefore works against the CONCRETE component, and must resolve to it rather than to the brain base. Original entry | Grep `AIModule/Classes/BrainComponent.h` for export macros on PauseLogic/ResumeLogic (source check, 5 minutes; queued here because it gates a live workflow question) | Feasibility of a pause/resume-brain endpoint — wanted only if bridge-driven `pie_move_pawn` moves fighting BehaviorTrees proves painful in practice ([work/G1_ai_navigation.md](work/G1_ai_navigation.md) pickup #6). |

---

## 3. UNVERIFIED remainders per axis

Everything still unverified after Phase 2 (items Phase 2 RESOLVED are excluded — each axis file's
verdicts and UNVERIFIED strikethroughs say which). One line each: item → the specific check that
closes it.

### [A — editor core](work/A_editor_core.md)
- `set_editor_mode` (write twin of get_editor_modes) — linkage verified; mid-frame safety is not: activating a mode spawns Slate toolkits from an HTTP handler → design pass with SetTimerForNextTick deferral + PIE guard, then one live activation test.
- Viewport bookmarks — **AUDITED 2026-08-31, the linkage blocker is resolved.** `IBookmarkTypeTools::Get()` is `static UNREALED_API` (IBookmarkTypeTools.h:19) and `CreateOrSetBookmark` / `JumpToBookmark` / `ClearBookmark` / `ClearAllBookmarks` are reachable through it, each taking an `FEditorViewportClient*` the bridge would have to resolve. So it is BUILDABLE; the decline now rests only on value (set/get_viewport_camera already covers save/restore), which is a legitimate basis where "unaudited signatures" was not.
- `UEditorActorSubsystem::ConvertActors` on Blueprint actors with cooked parents → one live conversion test on an editable child.
- Undo-barrier endpoints (SetUndoBarrier/RemoveUndoBarrier) — would a barrier strand RunEndpoint's blanket transactions? → design pass + transaction-stack experiment before exposing.
- USelection BSP-surface selection — **THE DECLINE REASON IS NOT ALLOWED HERE, flagged 2026-08-31.** "Irrelevant to this game" is explicitly not a valid reason to drop an item: MifBridge is a GENERAL UE5 tool, and DDS2 is one of two projects it is TESTED on, not the limit of who it is for. BSP brushes are a normal UE5 blockout workflow, and surface selection is how a material gets applied to one - that is a real use case for someone greyboxing a level, whatever DDS2 happens to ship. Re-judge on general value or leave it open; do not close it on relevance to this project. (No opinion offered here on whether it is worth building - only that the stated reason cannot be the one that closes it.)
- UEditorAssetSubsystem metadata tags — exported, deliberately unproposed → decision only, revisit if asset-tagging workflows appear.

### [B — assets/registry](work/B_assets_registry.md)
- FMigrationOptions fields (prompting behaviour?) → read PackageMigrationContext.h / the IAssetTools struct block before promising a dialog-free migrate.
- BeginAdvancedCopyPackages — prompts? async completion shape? → read the implementation for dialogs; tier-3 candidate only.
- USoundFactory constructor defaults — **READ 2026-08-31: SoundWave-only IS safe to promise.** `bAutoCreateCue = false` in the constructor on BOTH 5.3 (SoundFactory.cpp:158) and 5.7 (:164), with `SupportedClass = USoundWave::StaticClass()`. So an import produces a SoundWave and nothing else - no SoundCue appears beside it, which was the worry. `bIncludeAttenuationNode` is likewise false and `CueVolume` 0.75f, both inert while auto-cue is off. The flags are `UPROPERTY(EditAnywhere)` bitfields, so if cue generation is ever wanted it can be exposed reflectively rather than by linking AudioEditor - the same route MoviePipeline's settings take. Note the header lives in Editor/**AudioEditor**/Classes, not UnrealEd, which is where this entry's phrasing implies someone went looking.
- UTextureFactory `customconstructor` contract → eyeball once at implementation (NewObject works).
- FbxFactory no-dialog static-vs-skeletal forcing — **READ 2026-08-31, and it needs no import test to settle the mechanism.** Identical on 5.3 and 5.7. Three parts, and only one of them is the factory: (1) `FbxFactory::SetDetectImportTypeOnImport(bool)` is an INLINE setter (FbxFactory.h:59) so no export is involved; (2) `UFbxImportUI::MeshTypeToImport` (FbxImportUI.h:113) and `bAutomatedImportShouldDetectType` (:218) are both `UPROPERTY(BlueprintReadWrite)`, so the bridge sets them REFLECTIVELY rather than linking anything - and `SetMeshTypeToImport()` is just `MeshTypeToImport = bImportAsSkeletal ? FBXIT_SkeletalMesh : FBXIT_StaticMesh` (:232), so setting the enum directly is equivalent; (3) the DIALOG is suppressed by `UFactory::SetAutomatedAssetImportData(...)`, `UNREALED_API` (Factory.h:212 on 5.3, :252 on 5.7), which is what makes `IsAutomatedImport()` true. Forcing the type is `bAutomatedImportShouldDetectType = false` plus an explicit `MeshTypeToImport`. THE HAZARD IF THIS IS GOT WRONG is worth naming: a modal import dialog on an HTTP handler hangs the call and the editor waits for a click nobody is there to give.

### [C — blueprints/graphs](work/C_blueprints_graphs.md)
- `UK2Node_MathExpression` generic-spawn safety — **ANSWERED 2026-08-31: SAFE, and there is no denylist to take it off.** No MathExpression or Composite denial exists anywhere in MifBridge's source; `add_k2_node` is the generic spawn and refuses nothing by class. The real hazard is an ORDERING one, and the code already satisfies it. `UK2Node_Composite::PostPlacedNewNode` (K2Node_Composite.cpp:308) creates the inner graph via `CreateNewGraph(this, NAME_None, ..., GetGraph()->Schema)` and then `check(BoundGraph)` - so the node must ALREADY be in a graph when that runs, or `GetGraph()` is null and the editor dies on a check, not an error. `PlaceAndInit` (MifBridgeCommon.cpp:4684) does `Graph->AddNode(...)` and only then `Node->PostPlacedNewNode()`, which is exactly the precondition the engine needs. MathExpression's own PostPlacedNewNode then renames the bound graph, which is harmless. WHAT WOULD BREAK IT: any future spawn path that calls PostPlacedNewNode before AddNode - the ordering is the contract, not the node class.
- AnimGraph module load timing (startup vs first-AnimBP-use) → handlers call LoadModuleChecked defensively; one live check settles it.
- Exact node set from `CreateDefaultNodesForGraph` for the two anim schemas → empirical count via list_nodes in the verify step (no code change).
- Conduit/alias state nodes (UAnimStateConduitNode/UAnimStateAliasNode) → read headers; plausible follow-on endpoints, not proposed.
- UK2Node_AsyncAction factory DISCOVERY (enumerating UBlueprintAsyncActionBase factories for a list endpoint) → mechanism investigation; add_async_action requires the caller to name the factory meanwhile.
- EnhancedInput/GameplayAbilities module loaded-state for spawning their K2 nodes via add_node_by_class → live probe; fails with a clean error naming the module if unloaded.
- FBakedStateExitTransition field list → reflection serialisation in describe_anim_class covers it; optional header read.

### [D — materials/rendering](work/D_materials_rendering.md)
- None remaining — all six Phase-1 UNVERIFIED items were resolved with citations in Phase 2 (lighting quality plumbing, RTF enum, RVT enum, MaterialFunction props, LayoutMaterialExpressions, texture-build poll).

### [E — geometry/meshes](work/E_geometry_meshes.md)
- FSkeletalMeshCompilingManager methods/exports unread → read SkinnedAssetCompiler.h only if per-type skeletal counts are wanted; the FAssetCompilingManager-totals design stands.
- MeshLODToolset "AutoLOD" (UGenerateStaticMeshLODProcess — one-call LOD chain + collision) → header read; tier-3 candidate.
- ADynamicMeshActor/UDynamicMeshComponent as LEVEL objects — lifetime/serialization/cook/RamaSave interplay unexamined → design read + one spawn/save test.
- UGeometryScriptLibrary_MeshBakeFunctions (normal/AO baking, 14 UFUNCTIONs located) → signature read; pairs with future texture work.

### [F — world/level](work/F_world_level.md)
- `create_level_instance` — DEMOTED (CreateLevelInstanceFrom hard-codes a modal SaveAs, LevelInstanceSubsystem.cpp:999–1000) → human decision: invest in a bridge-side reimplementation of the move+save sequence, or park until an engine change. Full Phase-1 entry preserved in the file for a revisit.
- AWaterZone prerequisite — do spawned AWaterBody* actors render without a pre-existing zone? → open WaterZoneActor.h + one spawn test before set_water_body_profile's verify step.
- IslaSombra census probes → experiment E1 above.
- `AWorldSettings.WorldPartition` pointer read → one get_property probe with a real map open (rides E1).
- FInterpCurveFloat addressability via set_property (`Points[i].OutVal`) → one live probe; if set_property CAN reach it, set_water_body_profile shrinks toward a thin UpdateAll wrapper (composition question, not viability).
- `ULandscapeInfo::GetSplineActors` return shape on a plain landscape → one live call (rides E1).

### [G1 — AI/navigation](work/G1_ai_navigation.md)
- EQS manager in editor world → experiment E6.
- Direct `UBehaviorTree::RootNode` composition (bypassing the editor graph) — does the BT editor/runtime tolerate an asset whose graph never existed? → one experiment on a scratch asset; tier-3 curiosity, recorded to prevent rediscovery.
- set_property on UNavigationSystemV1 CDO `SupportedAgents` — config-class persistence semantics (writes DefaultEngine.ini? live nav system re-reads?) → live set_property + nav_status re-read; `OverrideSupportedAgents` is the programmatic alternative, also untested against an initialized editor world.
- `BP_TaskMoveToCustom` semantics (why the game wraps stock BTTask_MoveTo) — cooked graph, unreadable by design; only matters if bridge moves must imitate game moves exactly.
- DDS2 nav agent count (>1 supported agent? water routing looks spline-based, not navmesh) → list_object_properties on project nav settings or the placed ARecastNavMesh; affects whether find_path grows an `agent` param.
- UBrainComponent pause/resume export → experiment E7.

### [G2 — sequencer/UMG/input](work/G2_sequencer_umg_input.md)
- UMoviePipelinePrimaryConfig setting classes — **READ 2026-08-31, and the answer removes the linkage question entirely.** `UMoviePipelineOutputSetting` is `MOVIERENDERPIPELINECORE_API` in a **Public/** header on both 5.3 and 5.7, with the same fields: `FDirectoryPath OutputDirectory`, `FString FileNameFormat`, `FIntPoint OutputResolution`, `bUseCustomFrameRate` + `FFrameRate OutputFrameRate`. But every one is a `UPROPERTY(EditAnywhere, BlueprintReadWrite)`, so the config-building step needs NO C++ against the class at all - the bridge's existing reflective property write reaches them, the same way it reaches any other UObject. That is a smaller job than the entry assumed, and it is version-proof: a field renamed in a later engine surfaces as a property-not-found refusal instead of a link error.
- Sequencer event tracks (UMovieSceneEventTrack + director-BP payload binding) → object-model walk; parked for phase-2 design.
- UWidgetBlueprintGeneratedClass widget-tree ARCHETYPE accessor (name/export in 5.3.2) → header read before implementing list_widget_tree's cooked-degraded branch.

### [G3 — Niagara/audio/physics](work/G3_niagara_audio_physics.md)
- `CreatePhysicsAsset` on container-origin skeletal meshes — render-data fallback or failure? → one live test on a cooked mesh before the endpoint promises cooked behaviour.
- MetaSound `Build()` package/save flow — registration beyond AssetCreated + SavePackage (document version stamp, frontend registry)? → prototype build+save; builder signatures verified.
- Wave Player node's exact FMetasoundFrontendClassName strings → read MetasoundStandardNodes/MetasoundEngine sources at implementation time.
- `USoundFactory::SuppressImportDialogs` coverage of OGG/FLAC (only WAV header region read) → read the cpp.
- `UNiagaraComponent::SetVariable*` on `_GEN_VARIABLE` SCS templates — override-store propagation to instances untraced → one live template-set + PIE-spawn check; placed-actor and PIE paths are verified.

### [H — data](work/H_data.md)
- FSimpleCurve key-struct export surface (SimpleCurve.h unopened) → read the header; until then read_curve stays eval-only for simple-curve CurveTable rows. (Note: project CurveTable count is 0 — low urgency.)
- `set_editor_culture` — signature unread AND dubious value (globally changes editor UI language) → decision only; recommend leaving it out.
- RamaSave DLL stub-vs-real → experiment E2.
- MirrorDataTableFactory (anim mirroring tables) → open only if anim-mirroring demand appears; niche.
- FStringTableRegistry global-registry route → unswept; not needed for the asset-based endpoints.
- EDataTableExportFlags option set → enumerate before exposing exporter options; v1 ships defaults.

### [I — diagnostics](work/I_diagnostics.md)
- `FApp::GetSessionId` (controller route) — deliberately unverified; the chosen framework-direct automation design does not need it. Only revisit if multi-device automation ever ships.

### [J — DDS2 project](work/J_dds2_project.md)
- FDataTableExporterJSON per-row path → experiment E4.
- Can retoc emit an AssetRegistry.bin alongside to-zen output? → consult retoc docs / one cook experiment; upgrades refresh_asset_registry's registryBlob lane from theory to practice.
- `ScanModifiedAssetFiles` vs full ScanPathsSynchronous speed on loose /Game/MODS edits → timed probe after implementation (behavioural claim only).
- PakOrder constants used by MountModKitGameContainers → read the order value in the engine fork before picking mount_pak's default (500).
- BPModLoaderMod's exact in-GAME pak-load mechanism → inferred from UE4SS.log shape only; affects mount_pak documentation, not the endpoint.

---

## 4. Operational prerequisites (do these before/at implementation start)

1. **Editor rebuild + restart** to pick up the 4 in-source-but-not-live endpoints:
   `set_viewport_camera`, `get_viewport_camera`, `focus_viewport` (read-only) and
   `spawn_actor_in_pie` (transacted). Live DLL serves 156 of 160
   ([00_BASELINE.md](00_BASELINE.md) registry health). After restart: `self_audit` must report
   160, and the Tier-0 "editor camera control" gap is formally CLOSED (verify only — do not
   re-propose).
2. **server.py drift**: `diagnose_landscape_draws` has no MCP tool (server.py exposes 159 of
   160). Add the missing `@mcp.tool()` wrapper in the same change that adds new tools, or the
   three-way registry rule is violated from day one.
3. **Keep [work/_BRIEF.md](work/_BRIEF.md)'s covered-set list in sync**: if ANY audit endpoints
   land before an implementation session reads this audit, update the 160-name list (and
   00_BASELINE counts) first — otherwise later agents will diff against a stale covered set and
   re-propose or mis-classify work. This already bit once (the brief circulated 159 and dropped
   spawn_actor_in_pie; corrected in place).

---

## 5. Deferred-by-design (decisions already made — listed so nobody re-opens them by accident)

| Item | Why deferred | Where recorded |
|---|---|---|
| Skin-weight editing pipeline | UGeometryScriptLibrary_MeshBoneWeightFunctions is exported and callable, but a correct end-to-end story needs CopyMeshToSkeletalMesh + skeleton compatibility + rebind + rebuild; high blast radius, no mission driver. Tier 3. | [E](work/E_geometry_meshes.md) negative #9 |
| Niagara module-stack authoring | One-way street in 5.3.2: only the `UNiagaraScript*` overload of AddScriptModuleToStack is exported; ALL RemoveModuleFromStack overloads are unexported — module addition without removal. Needs a removal story + output-node lookup design. Asset-create / param-set / spawn / counts ship instead. | [G3](work/G3_niagara_audio_physics.md) negative #4 |
| MetaSound graph-authoring depth | Builder API is exported end-to-end but handle-based and deep; node-class discovery needs the frontend registry; 5.3 lacks BuildToAsset (grep-confirmed absent). One recipe endpoint proposed; a general metasound_* family is a phase-2 DESIGN item, not an engine-surface gap. | [G3](work/G3_niagara_audio_physics.md) negative #8 |
| Chaos fracture | PlanarCut and Fracture plugins both `EnabledByDefault:false` and not project-enabled; editor fracture tools are Private/UI-locked. The exported PLANARCUT_API functions become callable IF the plugins are enabled — recorded as a future unlock; game has zero destruction content. | [G3](work/G3_niagara_audio_physics.md) negative #5 |
| World Partition authoring on cooked worlds | IslaSombra is cooked WP: editing/saving cooked maps is a documented impossible; data-layer MUTATION excluded on value (only WP world is unsaveable); WP conversion is commandlet-only. Read-only `list_data_layers` is the whole WP surface. Mod maps stay non-WP; sublevels are their streaming story. | [F](work/F_world_level.md) negatives #6/#7, world probe |
| PCG | Plugin `EnabledByDefault:false`, experimental VersionName 0.1 in 5.3, heavy API churn before 5.4; enabling it solely for bridge endpoints is cost without a mission driver. Revisit if the project adopts PCG content. | [E](work/E_geometry_meshes.md) negative #2 |
| Localization gather commandlet | Gather-text runs as commandlets (separate process over loose packages) — cannot run in-process mid-frame; inventory taken (7 Gather* commandlets), request+poll design only if localization demand materialises. | [H](work/H_data.md) coverage log / negative |

Related standing negatives (same "do not re-open" status, different shape): GAS four-way negative
([G3](work/G3_niagara_audio_physics.md) #1), Landmass scripting dead end
([F](work/F_world_level.md) #3), BT/EQS graph authoring UI-lock
([G1](work/G1_ai_navigation.md) negatives), SequencerScripting no-export findings
([G2](work/G2_sequencer_umg_input.md) #1 — plugin-enabled claim overturned, export findings stand).
