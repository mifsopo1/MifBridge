# Endpoint audit — progress log

> **RE-DISCOVERED 2026-08-29, A MONTH AFTER THE LAST ENTRY - READ THIS FIRST.** This whole folder
> (`docs/audit/`) documents the 2026-07-26 to -29 audit run: 40 subagents, ~8.4M tokens, the source
> material `FEATURE_PARITY_SPEC.md` and `docs/06_CAPABILITY_ROADMAP.md`'s own "13-agent audit"/
> "8-domain fan-out audit" references point back to. It had gone completely unexamined this session
> until a routine doc-hygiene pass found it. Two things worth knowing before reading further:
> - **It is NOT dead weight.** `04_OPEN_QUESTIONS.md` §1.6 independently described connect_pins
>   hardcoding the K2 pin-connection schema - a real bug, over a month before this session
>   independently re-found and fixed the same thing a different way. `07_SELF_AUDIT_FINDINGS.md`'s
>   sampled CRITICAL/HIGH findings (#2-#9) were almost all already fixed, several with a comment at
>   the fix site quoting the finding's own language - meaning this archive genuinely got acted on,
>   just never marked as such in its own files.
> - **`FEATURE_PARITY_SPEC.md` is the current, actively-maintained source of truth** for what is
>   built vs still open. This log and the axis files under `work/` are frozen at 2026-07-26 - the
>   endpoint count alone has grown from 241 (Phase-3 close, below) to 363 since. Treat every
>   remaining `[ ]` in this file and its siblings as "was true a month ago," not "is still open" -
>   check FEATURE_PARITY_SPEC.md or the live source before trusting either direction.

Purpose: allow an interrupted run to resume without re-deriving anything. Newest entries at the bottom.

## 2026-07-26 — Step 0 complete (baseline established)

- Environment verified: engine source at D:/UE532/Engine/Source, plugin at D:/DDS2SDK/Game/Plugins/MifBridge, editor RUNNING (self_audit answered on 127.0.0.1:8791).
- Contract docs read: 00_ARCHITECTURE, 01_POSTMORTEMS, 02_GOTCHAS, 06_CAPABILITY_ROADMAP, 08_LANDSCAPE.
- Registry state measured (corrected by the baseline agent 2026-07-26):
  - Source: **160** endpoints, MIF_DECL ≡ MIF_BIND (no drift). (An early sed extraction said 159 — it dropped `spawn_actor_in_pie`; grep -o confirmed 160.)
  - Live editor: 156 — `set_viewport_camera`, `get_viewport_camera`, `focus_viewport`, `spawn_actor_in_pie` are in source but not the running DLL (pending rebuild). **Editor-camera control (a named Tier-0 gap) is already implemented in source.**
  - server.py: 159 tools — missing only `diagnose_landscape_draws`.
  - Live buckets captured from self_audit (policyContradictions: 0). Build: Jul 26 2026, engine 5.3.2 CookedEditorModKit fork.
- Handler→file map extracted to scratchpad (handler_files.txt).
- Project plugins recorded (uproject): ModelingToolsEditorMode, Water, Landmass, GameFeatures, AssetSearch, Oceanology, Riverology, FGear, RamaSaveSystem, DLSS-family, etc.
- `docs/audit/work/_BRIEF.md` written — the shared contract for all sweep agents (invariants, covered set, 10 verification fields, output format).

## 2026-07-26 — Step 1 launched (breadth sweep)

- 00_BASELINE.md: being written by a dedicated agent (from handler_files.txt + live self_audit buckets + handler source).
- Phase-1 workflow `mifbridge-endpoint-sweep` (run wf_ba0d3082-315) launched: 12 axis agents
  (A editor core, B assets/registry, C blueprints/graphs, D materials/rendering, E geometry/meshes,
  F world/level, G1 AI/nav/NPC-routing, G2 sequencer/UMG/input, G3 niagara/audio/physics,
  H data, I diagnostics, J DDS2-specific), each writing `docs/audit/work/<axis>.md` with the ten
  verification fields per proposal. Read-only live-bridge curls permitted to F/G1/G2/G3/H/I/J.

## 2026-07-26 — 00_BASELINE.md written

- Baseline agent completed: per-file tables for all 29 handler files, buckets from live self_audit
  (48 read-only / 15 self-managed / 93 transacted, 17 compile-heavy †), descriptions sourced from
  handler code, registry-health section, domain-coverage table.
- Cleanup candidates it flagged: `describe_class` and `list_enum_values` are read-shaped but sit in
  the transacted bucket (undo-stack pollution) — carried into Phase-3 open questions.
- Coverage density (from its domain table): graph authoring (40), functions/events/interfaces (14),
  level/world (15) dense; materials (2), animation (read-only), pipeline thin; Sequencer, physics,
  audio, Niagara, localization, source control at zero.
- `_BRIEF.md` corrected in place: covered set now lists all 160 (added `spawn_actor_in_pie`).

## 2026-07-26 — Phase 1 complete (breadth sweep)

All 12 axes returned (run wf_ba0d3082-315, ~2.25M subagent tokens, 896 tool calls). **232 proposed
endpoints**, ~107 cited negative results, 66 UNVERIFIED items. Per-axis files in `work/`, machine
summary in scratchpad `phase1_summary.json`. Headline findings:

- **A (21)**: undo/redo viable (UTransactor virtuals method-level UNREALED_API); 54 UEditorSubsystems enumerated; checkout-free SaveDirtyPackages(bFastSave) verified.
- **B (18)**: dependency/referencer graphs designed with cooked-stripping self-diagnosis; ~296 UFactory subclasses enumerated; Map_Check IS exported; generic create_asset with 9-type cited allowlist.
- **C (18)**: generic add_node_by_class with PM-004-derived denylist, zero new deps; AnimBP state-machine authoring verified end-to-end; FKismetBytecodeDisassembler is SCRIPTDISASSEMBLER_API and works on cooked classes; SCS attach-to-native via USCS_Node::SetParent (SubobjectDataSubsystem route is UI-locked — negative).
- **D (25)**: material graph authoring fully unblocked (UMaterialEditingLibrary MATERIALEDITOR_API); 277 UMaterialExpression subclasses censused; cooked materials have NO graph (UCLASS Optional) — refuse-on-cooked rule.
- **E (24)**: dynamic-mesh session model on engine's own UDynamicMeshPool; GeometryScripting NOT enabled (one-time uplugin ref cost); StaticMesh/SkeletalMesh editor subsystems are ENGINE-source modules (no EditorScriptingUtilities plugin needed).
- **F (25)**: EditorApplySpline closes the town-road gap (tier 0); IslaSombra is cooked World Partition → WP authoring de-scoped, sublevel surface targets mod maps; existing add_foliage_instances is a detached-HISM impostor — real FFoliageInfo::AddInstances route specified.
- **G1 (18)**: DDS2 NPC movement mapped (stock AIModule: spline markers + opponent BT stack + quest walk-paths); pie_move_pawn/status with plugin-side leg queue; BT/EQS graph authoring UI-locked (negatives); find_path is the numeric backbone.
- **G2 (18)**: SequencerScripting plugin is a dead end (cited) — sequence chain built on method-level MOVIESCENE_API instead; UWidgetAnimation reuses the same endpoints; MovieRenderPipeline transitively enabled via DLSSMoviePipelineSupport; both roadmap UMG gaps closed zero-dep.
- **G3 (17)**: Niagara User.* params need dedicated setters (not UPROPERTYs); live census: MetaSounds heavily used (185) → 5.3 builder API verified; GAS four-way verified absent (no endpoints).
- **H (25)**: full datatable authoring loop; FRichCurve all method-level ENGINE_API; TWO MODAL-DIALOG TRAPS found on asset-creation paths (CurveTableFactory::ConfigureProperties, AssetToolsImpl::CanCreateAsset overwrite dialog); RamaSaveSystem shipped source is stubbed SDK reconstruction — real format reflection-only.
- **I (14)**: get_perf_stats (all globals verified); log_tail ring-buffer design with thread contract; automation framework-direct route; trace_start passes no-console-wrapper rule with explicit argument.
- **J (9)**: DDS2 systems map (native QuestManager/TownStatusManager/BaseNPC spine + 122 *Database tables — quests/shops/dialogue are compositions, documented not duplicated); trigger_cook is PLAN-ONLY → mod_package_request/_status lane designed; mount_pak viable but premade asset registry leaves mounts invisible to find_assets (both halves cited).
- Incident: editor/bridge went DOWN mid-sweep (G1/G2/H/I got connection refused; it restarted later). Queued live probes moved to Phase 2.
- Known cross-axis dupes to merge in Phase 3: list_dirty_packages (A,B), build_reflection_captures (D,F), create_physics_asset (E,G3), asset_compile_status (B,E), create_* creators (B generic vs D/H specific).

## 2026-07-26 — Phase 2 launched (adversarial verification)

Workflow `mifbridge-audit-verify` (run wf_81466059-eec): 12 adversarial verifiers (one per axis
file; re-open every citation, verify verbatim signature + export + module + bucket + async design,
hunt modal/blocking/GC hazards in cited implementations, verdict-stamp every entry in place, also
spot-verify the negative results) + 1 read-only live-probe agent for the queued probes (bridge is
back up; results → work/LIVE_PROBES.md).

## 2026-07-26 ~04:00 — Phase 2 first run hit the usage limit

- Only the J verifier completed before the session limit: **8/8 entries CONFIRMED verbatim, 0
  corrections, 0 negatives overturned** — including both load-bearing mount_pak claims
  (premade-registry blindness AssetRegistry.cpp:186-192; sibling-utoc co-mount
  IPlatformFilePak.cpp:8112-8124) and verify_pak_contents' Core-only FIoStoreReader route.
  Its verdicts are already stamped into work/J_dds2_project.md.
- 11 verifiers + the live-probe agent failed on the limit ("resets 5:30am America/New_York").

## 2026-07-26 09:10 EDT — Phase 2 resumed after reset

- User confirmed: keep going, editor left open.
- Workflow resumed with resumeFromRunId wf_81466059-eec (J replays from cache; 11 verifiers +
  live probes re-run).

## 2026-07-26 ~10:00 EDT — Phase 2 complete (all 13 agents)

**220 entries verified: 168 CONFIRMED, 51 CORRECTED, 1 DEMOTED** (create_level_instance — hard-coded
modal Save-As, LevelInstanceSubsystem.cpp:999-1000). 3 negatives OVERTURNED. Verdicts stamped into
every axis file; LIVE_PROBES.md written (8 sections). Load-bearing corrections:

- Modal-trap inventory grew 2 → 5+ (DataTableFactory:176, CurveTableFactory:55, UCurveFactory +
  UDataAssetFactory SClassPickerDialogs, CanCreateAsset's three FMessageDialogs; plus F's sublevel
  trio, G2's MoviePipelinePIEExecutor:93/:109, G3's PhysicsAssetUtils:343/:903, B's consolidate/
  fixup dialogs). Blanket rule for implementers: pre-validate, never rely on engine prompts.
- Hidden blocking tails: recompile_material (double CollectGarbage + slow-task + shader busy-wait),
  GetStatistics (FinishCompilation sync wait — negative overturned: not stale, but blocking),
  build_reflection_captures (FinishAllCompilation unbounded + SM5 check() crash), Niagara PreSave
  WaitForCompilationComplete, convex decomposition WaitFor(33ms) dialog loop.
- Silent-ignore gates found: MinLOD needs bOverrideMinLOD=true (StaticMeshComponent.h:115) — the
  exact banned bug class, now specified; EditorScriptingHelpers returns silent zeros during PIE.
- B's map_check rerouted: UEditorEngine::Map_Check is PRIVATE — public Exec "MAP CHECK
  DONTDISPLAYDIALOG" + MessageLog readback instead.
- C's add_node_by_class export claims corrected (MinimalAPI + reflection + ENGINE_API virtuals —
  still fully viable); G2's "SequencerScripting dead end" OVERTURNED (transitively enabled via
  LevelSequenceEditor.uplugin) though the MOVIESCENE_API route stands as designed.
- E's UDynamicMeshPool failsafe = permanent leak past MaxPoolSize (UDynamicMesh.cpp:563-578) —
  bridge must enforce its own handle cap.
- EXISTING-bridge defects found live: find_assets silently ignores unknown params (param is
  'class', not 'className'); H integrity catch — one axis file falsely claimed a verification pass
  and was fully re-run.
- Live probes: G1's DDS2 movement map CONFIRMED (BP_OponentAIController_C parents
  ADetourCrowdAIController; spline task markers + patrol routes verified; no Pedestrian/Crowd
  asset family exists). Census: WBP-GeneratedClass 279, InputAction 62, IMC 5, LevelSequence 4
  (sequencer → low tier). RamaSave reflects fully (130/28/29 functions). Cooked curves keep full
  key data. F's world census SKIPPED (Untitled world open — queued as open question).

## 2026-07-26 — Phase 3 launched (synthesis)

Workflow `mifbridge-audit-synthesize` (run wf_2e71d7f7-b27): 12 low-effort extractors emit
work/index/<axis>.rows.json (name/kind/tier/u/e/r/bucket/async/modules/plugin/verdict/cooked/
hazards/overlaps per entry) + 2 writers compile 03_GAPS_AND_RISKS.md and 04_OPEN_QUESTIONS.md
from the verdict-stamped axis files. Main session then assembles 01_CATALOGUE.md (master index —
axis work files stay the single source of truth for full entries) and 02_RANKED.md (tier sort by
U+E+R, batches grouped by shared new-module deps, Batch 0 = repairs to existing endpoints).

## 2026-07-26 ~10:00 EDT — Phase 3 complete. AUDIT DONE.

- Extraction: 223 index rows across 12 axes → work/index/*.rows.json (+ _merged.rows.json).
  Verdict distribution: 170 CONFIRMED / 52 CORRECTED / 1 DEMOTED. Tier distribution (active,
  post-dedup): 29 / 124 / 61 / 4 (tiers 0–3), 218 active proposals total (223 − 1 demoted −
  4 merged duplicates).
- 03_GAPS_AND_RISKS.md written: ~45 non-viable APIs (each with the exported alternative where one
  exists), 21 modal-dialog traps with mitigations, 13 game-thread blocking hazards, merged
  cooked-content limits, 5.3.2 sharp edges, 3 overturned negatives, bridge-side defect list.
- 04_OPEN_QUESTIONS.md written: 7 ratified policy recommendations (create_asset boundary,
  map_check/trace_start console-rule refinement, bucket reclassification, add_foliage_instances
  keep-and-document, connect_pins rollout, dedup owners), 7 queued live experiments, per-axis
  UNVERIFIED remainders, operational prerequisites, deferred-by-design list.
- 01_CATALOGUE.md generated (master index; work/<axis>.md files remain the single source of truth
  for full ten-field entries — the index is generated, do not hand-edit).
- 02_RANKED.md generated: full tier ranking sorted by U+E+R + 9 implementation batches grouped by
  new-module cost (Batch 0 repairs = 6 behaviour changes; Batch 1 zero-new-dependency = 140
  endpoints; then materials 9 / input 4 / mesh-editing 13 / GeometryScript 9 / FX-audio 10 /
  sequencer 12 / small-singles 15).
- Memory saved for future sessions: ~/.claude/projects/.../memory/mifbridge-endpoint-audit.md.

**Run totals: 4 workflows + 1 baseline agent, 40 subagents, ~8.4M subagent tokens, ~3,100 tool
calls.** Success criterion met: every Tier-0/1 entry carries verbatim cited signatures, export
macros, module deps, guards, bucket, params, failure modes, cooked behaviour and a numeric
verification method — implementable without opening engine source.

## 2026-07-26 — Scope extended: full-scope expansion delta (docs/10_FULL_SCOPE_EXPANSION_PROMPT.md)

User directed: consolidate everything into one large report, and execute the not-yet-done parts of
the 10-prompt (the reconstructor/plugins/gaps research). Diff of the 10-prompt against completed work:

| 10-prompt phase | Status |
|---|---|
| Phase 0 baseline | DONE (00_BASELINE.md; note: D:/DDS2SDK/Game is NOT a git repo — commit checkpoints inapplicable) |
| Phase 1 MifKismetReconstructor over HTTP | research launched (K1 toolkit/commands, K2 pipeline/verify/coupling-model) |
| Phase 2 other 20 plugins | research launched (P1 graph-layout family, P2 Oceanology/Riverology/FGear, P3 sessions/GameFeatures/misc/no-source) |
| Phase 3 engine surface | DONE (the 223-entry catalogue) |
| Phase 4 gaps | material authoring + NPC routing catalogued; pie_status + snap_actors_to_ground root-causes launched (Q axis); graph auto-layout in P1 |
| Implementation (build loop) | NOT started — requires closing the user's editor; gated on user go-ahead |

Workflow `mifbridge-fullscope-delta` (run wf_a4d0acdc-a23): 6 research agents → 3 adversarial
verifiers → 6 row extractors. New axis files: work/K1_*, K2_*, P1_*, P2_*, P3_*, Q_*.md.
After it lands: regenerate 01_CATALOGUE/02_RANKED with the new axes, then write 05_FULL_REPORT.md
(the consolidated large report).

## 2026-07-26 ~10:45 — Delta research complete; implementation started (user granted full autonomy)

User closed the editor and authorized full autonomous implementation (open/close editor as needed,
handle dialogs). Implementation log: 06_IMPLEMENTED.md.

- **Batch A COMPLETE**: build was `Target is up to date` (the 01:49 DLL already had 160 — the old
  editor ran a stale 01:25 image). Relaunched, self_audit 160/0 contradictions, all 4 pending
  endpoints live-proven (camera round-trip exact, focus framed the proof cube, full PIE lifecycle
  with spawn_actor_in_pie). Bonus: pie_status was already rewritten (01:47) into a rich state
  machine — Phase-4 defect #1 likely fixed; Q confirms against current source.
- **Batch B COMPLETE (code)**: find_assets + 4 siblings now reject unknown params
  (RejectUnknownParams helper, MifBridgeCooked.cpp:59), className/type aliases work,
  diagnose_landscape_draws MCP tool added (drift closed, 160==160==160), describe_class +
  list_enum_values moved to read-only after purity verification. Build + live proofs pending.
- **K/P/Q research all complete** (files in work/). Headlines:
  - K1: console census CORRECTS the 10-prompt — 7 commands + 5 CVars, not 11 commands; 5 are
    MIF_KR_DEBUG-gated throwaways; mif.kr.ReconstructAll lives in the ENGINE FORK
    (CompiledBlueprintCopyAction.cpp:1345). kr_dump_blueprint + kr_disassemble_function need ZERO
    export promotions (FKismetBytecodeDisassemblerJson already MIFKISMETRECONSTRUCTOR_API).
    kr_events/kr_latent_resume are CVar toggles — correctly refused as endpoints.
  - K2: coupling model (b) spec'd executable — MifBridgeEndpointRegistry.h (FExternalEndpointDesc:
    name+bucket+provider+handler), load order proven (reconstructor Default phase < MifBridge
    PostEngineInit route binding), self_audit gains provider. Job-model constraint: FHttpServerModule
    ticks on the game thread → mid-reconstruct progress polling impossible; single-BP jobs are
    atomic deferred-tick; only the census slices (1 BP/tick, GC every 25). kr_verify_fidelity needs
    ONE KISMET_API export refactor in the fork (RunReconstructOnce et al are file-local statics).
  - P1: BlueprintAssist headless formatting IMPOSSIBLE (ctor needs SDockTab+SGraphEditor; FormatNodes
    early-outs without a panel) but request+poll via an open editor is viable (78 BLUEPRINTASSIST_API
    exports); Plan B zero-dep layered-DAG layout over move_node; engine 5.3.2 has NO native layout.
  - P2: Oceanology/Riverology/FGear are SDK STUB reconstructions (empty native bodies) — native
    endpoints would return zeros; only proposal: generic call_object_function (FindFunction/
    ProcessEvent) which unlocks cooked-BP callable functions generally.
  - P3/Q: files written (P3_sessions_misc_plugins.md, Q_gap_rootcauses.md) — summaries arrive with
    the verify resume.
- **Usage limit hit again (~10:45, resets 2pm ET)**: 3 verifiers + 6 extractors + the Batch C agent
  died. All research files were already safe on disk. Batch C agent died AFTER writing
  MifBridgeUndo.cpp (694 lines, 5 handlers) but BEFORE registration (DECL/BIND/mcp unchanged).
- **14:01 ET**: limit reset. Batch C agent resumed via SendMessage (finish registration);
  delta workflow resumed (research replays from cache, verify+extract re-run live).

## 2026-07-26 ~15:00 ET — Delta verified; B+C built and proven; catalogue regenerated

- **Delta verify+extract complete** (15/15 agents). K axes: 13 CONFIRMED / 2 CORRECTED (the two
  kr_verify_fidelity variants reconciled — K2's async form wins; all export-promotion targets
  re-verified against current declarations). P axes: 5/4 (format_graph export claims tightened;
  export_graph_text hidden check() crash found — same-outer validation is load-bearing;
  change_game_feature_state LexFromString crash trap + hidden StreamableManager blocking).
  Q: 1 CONFIRMED (snap_actors first-blocking-hit truncation, end-to-end) / 1 CORRECTED
  (pie_status: real mechanism is PlayWorld being a per-tick scratch UPROPERTY, GC-nulled across
  in-PIE travel; out-of-process sessions PROVABLY unobservable — PlayLevelNewProcess.cpp:57-59
  cancels the session info at launch). 27 new index rows.
- **Batch B+C built and live-proven** (second cycle; first failed on an FString/FName mismatch at
  MifBridgeUndo.cpp:91 AND exposed a build-script bug — piped exit code masked the failure and
  relaunched the stale DLL; caught by the self_audit count check. Both fixed.) Live: **165
  endpoints, 0 contradictions**, all bucket assignments correct, full undo/redo round-trip proven,
  find_assets strictness proven, save_dirty_packages echoes engine-silent skips.
- **Catalogue regenerated across all 18 axes**: 250 entries, **241 active** (250 − 8 merged
  duplicates − 1 demoted; an earlier log line said 245 — arithmetic error, corrected), **36
  Tier-0**, 10 implementation batches incl. Batch R (reconstructor via registration interface).
  02_RANKED marks live-proven endpoints ✅.
- **Batch D in flight** (agent implementing the 10-endpoint material loop; MaterialEditor module).
- **05_FULL_REPORT.md writer launched** (reads everything; the consolidated large report).

### Remaining

- [ ] Batch D: finish code → build cycle → live proofs (M_AuditProof chain).
- [ ] 05_FULL_REPORT.md lands → user can switch the session off Fable.
- [ ] Later batches (specs all verified, implementable on any model): zero-dep core (145),
      input (4), mesh-editing (13), GeometryScript (9, plugin enable), fx-audio (10),
      sequencer (12), small-singles (21), reconstructor (10, needs registration interface +
      one KISMET_API fork refactor for the verify family).
- [ ] Phase 3: merge dupes, assemble 01_CATALOGUE.md, 02_RANKED.md (scores + module-dep batches), 03_GAPS_AND_RISKS.md (from negatives), 04_OPEN_QUESTIONS.md (incl. describe_class/list_enum_values bucket cleanup, server.py diagnose_landscape_draws drift, pending editor rebuild for the 4 new endpoints).

## 2026-07-26 ~16:00 ET — Batches D + D.1 landed; reconstructor work started

- **Batch D (materials) COMPLETE and live-proven**: 175 endpoints live, 0 contradictions. Full
  authoring loop proven (create → 3 expressions with property read-back → recompile → shader poll →
  save .uasset → instance). Cooked refusal proven against the shipped landscape master material.
  Two build failures, both generalisable rules now in 06_IMPLEMENTED.md:
  **(1) exported ≠ accessible** — UMaterialInstance::UpdateParameterNames is ENGINE_API but
  `protected`; the engine calls it only because UMaterialEditingLibrary is a declared `friend`
  (MaterialInstance.h:1064). **(2) a default argument can impose a module dep no visible call
  names** — GMaxRHIShaderPlatform is the default arg of FMaterialUpdateContext's ctor
  (MaterialShared.h:2817), evaluated in the caller's TU ⇒ MifBridge needed `RHI`.
- **Batch D.1 (polish + silent-ignore sweep) code complete** (unbuilt): expression addressing now
  accepts UObject name → ParameterName → unique class short name, ambiguity always errors with
  candidates. Engine finding: **ParameterName spans SIX expression families in 5.3**, and the
  Landscape expressions carry it as a UPROPERTY *without* overriding the virtual — a
  `Cast<UMaterialExpressionParameter>` resolver would have missed five families including the ones
  this project's landscape material is full of; a reflection fallback covers them.
  set_material_parameter repaired (D-2): accepts the `{parameter,value}` sugar the live caller
  actually wrote, errors when nothing would be applied (was `ok:true, applied:0`), errors on
  wrong-typed map entries (was a silent `continue`). Five handlers in MifBridgeAuthoring.cpp gained
  the RejectUnknownParams guard.
  **Two more documented-but-never-read parameters found** (the PM-005 class):
  `create_material_instance`'s `textures` and `duplicate_actors`' `rotationOffset` — advertised in
  their `in:` comments, read by no line of code. Now refused by name.
  **New bug logged, not yet fixed**: set_material_parameter never calls `MIC->Modify()`, so Ctrl-Z
  does not restore parameter values.
- **Batch R phase 1 IN FLIGHT** — the first MifKismetReconstructor changes of this session.
  Plan: work/K_IMPL_PLAN.md (1071 lines, line-anchored). It caught two spec defects: the spec's own
  registry header would not compile (it includes Dom/JsonObject.h, but `Json` is a PRIVATE MifBridge
  dep — forward-declare instead, which also keeps Build.cs unchanged), and the reconstructor's
  helper functions are `static` AND inside `#if MIF_KR_DEBUG` ("ship OFF before release"), so
  handlers must reimplement resolution or the endpoints vanish in a shipping build.
  **Wave 1 = 8 endpoints needing ZERO export promotions; Wave 2 = EMPTY; Wave 3 = 4 verify-family
  endpoints gated on one KISMET_API refactor in the engine fork** (a cross-repo change — user
  decision).
  Phase 1 scope: MifBridgeEndpointRegistry.h (MifBridge's first exported symbol), the
  ExternalRegistry merge in MifBridgeCommon.cpp, self_audit `provider` field, reconstructor-side
  optional dep + MifKrBridgeEndpoints.cpp, and ONE endpoint (kr_list_cooked_blueprints) to prove
  the mechanism before bulk-adding the rest. Adversarial pre-build review included.

### Remaining

- [ ] Build cycle for D.1 + Batch R phase 1 together; prove the registration mechanism
      (self_audit shows kr_list_cooked_blueprints with provider "MifKismetReconstructor").
- [ ] Batch R phase 2: the remaining 7 Wave-1 kr_* endpoints + their server.py tools.
- [ ] Decision gate: the KISMET_API engine-fork refactor that unblocks the 4 verify endpoints.
- [ ] Optional later batches from 02_RANKED (zero-dep core 145, input 4, mesh-editing 13,
      GeometryScript 9, fx-audio 10, sequencer 12, small-singles 21).
