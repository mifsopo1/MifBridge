# Implementation log — full-scope expansion

_Started 2026-07-26. This is the 10-prompt's `audit/01_IMPLEMENTED.md` deliverable, numbered 06 to
fit the existing audit sequence. One section per batch: what landed, bucket, module, API cited,
live-test call and response. The build loop follows docs/10_FULL_SCOPE_EXPANSION_PROMPT.md._

Ground rules in force (from the 10-prompt + audit):
- Registry sync asserted after every batch: `MIF_DECL count == MIF_BIND count`, server.py updated.
- Bucket declared per endpoint; `self_audit.policyContradictions` must stay empty.
- Never block the game thread; async ⇒ request+poll; world-swap ⇒ deferred tick.
- Unrecognised parameter ⇒ error naming it. Every mutation live-proven with a numeric read-back.
- Tree never left unbuildable between batches. (Note: D:/DDS2SDK/Game is not a git repo — no
  commit checkpoints; each batch is instead closed by a build + live-proof + this log entry.)

## Batch A — rebuild to pick up in-source endpoints (no code changes)

**Goal**: validate the full build→launch→verify loop; deliver the 4 endpoints already in source but
absent from the running DLL: `set_viewport_camera`, `get_viewport_camera`, `focus_viewport`
(read-only bucket) and `spawn_actor_in_pie` (transacted).

- [x] Build: `Target is up to date` (5.7s) — the 01:49 DLL already contained all 160; the earlier
      editor session was running the stale 01:25 image. No compile was needed.
- [x] Editor relaunched; bridge answered self_audit in ~20s.
- [x] self_audit: endpointCount **160**, policyContradictions **[]**, buildDate Jul 26 2026 01:49:20.
- [x] `set_viewport_camera {location:{1234,-2345,3456}, rotation:{-30,45,0}, fov:75}` →
      `get_viewport_camera` returned the exact same numbers (+ `viewportCount:4`). PASS.
- [x] `spawn_actor_in_level {mesh:/Engine/BasicShapes/Cube, location:{10000,10000,100}}` →
      spawned WITH mesh (bounds min {9950,9950,50} max {10050,10050,150} — the PM mesh-drop fix
      holds) → `focus_viewport {actorPath:"AuditProofCube"}` framed 1 actor, camera moved to
      ~{9844,9844,227} aimed at the cube → `get_viewport_camera` confirms. PASS.
- [x] PIE lifecycle: `start_pie` (deferred, `state:"starting"`) → poll 1 → `state:"running"` with
      pieWorld/playerController/pawn/pieActorCount:19 → `spawn_actor_in_pie` placed
      StaticMeshActor_1 at z=300 into `/Temp/UEDPIE_0_Untitled_0` (hasAuthority:true) →
      `list_pie_actors` count 20 includes it → `stop_pie` (deferred, stopPending:true) →
      poll 1 → `state:"stopped"`. PASS.

**Finding**: pie_status now returns a rich state machine (running/startPending/sessionActive/
worldHasBegunPlay/stopPending/simulating/state + pieWorld + pawn) — MifBridgePIE.cpp was rewritten
at 01:47 alongside spawn_actor_in_pie. The Phase-4 "pie_status misreports" defect may already be
fixed in source; axis Q's root-cause pass runs against this current code and will confirm or refute
(multi-instance PIE reporting remains to check).

**Batch A verdict: COMPLETE — 4 endpoints delivered and live-proven, zero code changes needed.**

## Batch B — repairs (find_assets strictness, server.py drift, bucket hygiene)

Three audit-verified defects repaired (03_GAPS_AND_RISKS.md §7.1, §7.6, §7.7). No endpoints added
or removed: MIF_DECL 160 == MIF_BIND 160 (161 raw grep hits each — the extra is the `#define`
line); server.py @mcp.tool count 159 → **160**, closing the three-way registry drift. Build + live
proofs happen later this session.

### B1 — find_assets (and neighbours) reject unknown parameters (§7.1)

**Files**: `Source/MifBridge/Private/MifBridgeCooked.cpp`

- New file-local helper `RejectUnknownParams` (:59) — iterates `In->Values`, names EVERY
  unrecognised key, lists the accepted set, and takes per-key notes so a key that is
  *unimplemented* (not merely misspelled) says so explicitly. Key comparison is case-insensitive
  to match how JStr/JBool/JInt find fields, so a key that would be honoured is never rejected.
  Safe against transport noise: MifBridgeServer.cpp deserialises the POST body directly as the
  param object (token travels in the X-Mif-Token header), and server.py's `_post` drops unset
  (None) kwargs, so `In->Values` holds only what the caller actually sent.
- `H_find_assets` (:243) — rejects unknown keys; `recursive` gets the explicit note
  "not implemented - pathPrefix matching is ALWAYS recursive; recursiveClasses controls
  class-hierarchy matching". Class filter now resolves via
  `JStrAny({class, className, type})` (:254) — the PM-001 house pattern, so the audit's
  live-guessed `className` now *works* instead of silently matching nothing.
- Same guard added to every other handler in the file (all well under 10 lines each):
  `H_list_mounted_containers` (:131, accepts nothing), `H_describe_package` (:341,
  package/path), `H_diagnose_landscape` (:499, limit), `H_diagnose_landscape_draws`
  (:860, limit). Note: the audit sweep only named describe_package/list_mounted_containers;
  the two diagnose handlers live in the same file with the same hole, so they got the same
  one-guard fix rather than a TODO.

**Live proof (post-build)**:
- `find_assets {"recursive": false}` → must return `ok:false` with an error naming
  'recursive', stating it is not implemented, and listing the accepted keys. (Was: `ok:true`,
  param ignored.)
- `find_assets {"className": "DataTable", "limit": 3}` → must return DataTable assets
  (was: all 37,131 assets — className ignored).
- `find_assets {"class": "DataTable", "recursivClasses": true}` → `ok:false` naming
  'recursivClasses' (typo detection, generic text).
- `describe_package {"pkg": "/Game/X"}` → `ok:false` naming 'pkg', accepted "package (alias: path)".

### B2 — server.py missing diagnose_landscape_draws (§7.7)

**File**: `tools/ue5-mcp-bridge/server.py` (:907–910)

- Added `@mcp.tool() def diagnose_landscape_draws(limit: int = 40)` mirroring
  `H_diagnose_landscape_draws` (MifBridgeCooked.cpp — the handler lives there, NOT in
  MifBridgeLandscape.cpp as §7 context suggested; sole param `limit`, clamped 1–1000
  plugin-side). Docstring notes the rendering-thread flush the handler performs.
  `ast.parse` passes; tool count now 160 == endpoint count 160.

**Live proof (post-build)**: MCP tool `diagnose_landscape_draws(limit=5)` returns the
aggregate/histogram payload over MCP (previously only reachable via raw HTTP POST).

### B3 — describe_class / list_enum_values moved to the read-only bucket (§7.6)

**File**: `Source/MifBridge/Private/MifBridgeCommon.cpp` (`IsReadOnlyEndpoint`, :249–:255)

- Purity verified by reading both handlers before moving them:
  `H_describe_class` (MifBridgeIntrospect.cpp:315) walks `TFieldIterator` over a
  `ResolveClass`-resolved class — no `Modify()`, no `MarkBlueprintAsStructurallyModified`,
  no persistent object creation (ResolveClass may LoadObject; loading is not mutating for
  bucket purposes — find_assets/list_* already load under read-only).
  `H_list_enum_values` (MifBridgeNodes3.cpp:198) reads UEnum name tables only. Both genuinely
  pure — both moved; nothing left behind.
- Effect: calls no longer push an empty "Mif Bridge: describe_class" undo entry.

**Live proof (post-build)**: run `describe_class {"class": "Actor"}` then check the undo
stack is clean — `dumpundohistory` console cmd (or the upcoming list_transactions endpoint)
must show NO new "Mif Bridge: describe_class" / "Mif Bridge: list_enum_values" entries.
`self_audit` must report both under transactionBuckets.readOnly, endpointCount 160,
policyContradictions [].

### TODOs left

- None from this batch. (Deliberately NOT touched, still open in §7: add_foliage_instances
  impostor (§7.2), connect_pins K2-schema hardcode (§7.3), trigger_cook plan-only (§7.4),
  get_datatable_row O(table) (§7.5), and the repo-wide silent-param sweep beyond
  MifBridgeCooked.cpp — get_property already errors correctly and is the pattern reference.)

### Batch B+C build + live proofs — ALL PASS (2026-07-26 ~14:30 ET)

One compile fix was needed first (Batch C agent's FString→FName mismatch at MifBridgeUndo.cpp:91 —
`Package->GetName()` replaced with `Package->GetFName()`), plus a build-loop hardening: the cycle
script originally piped the build through `tail`, masking a failing exit code, and relaunched the
STALE DLL — caught immediately by the self_audit endpoint-count check (160 vs expected 165).
Exit code is now taken via the build's own status before any relaunch. Second cycle: build 19.5s
clean, bridge up in 10s.

- self_audit: endpointCount **165**, policyContradictions **[]**; buckets all as specified
  (list_transactions/list_dirty_packages/describe_class/list_enum_values read-only;
  undo/redo_transactions + save_dirty_packages self-managed).
- B1a `find_assets {recursive:false}` → ok:false naming 'recursive', explaining it is not
  implemented and why, listing accepted keys. B1b `{className:"DataTable",limit:3}` → 379 found,
  3 returned (alias works). B1c typo `recursivClasses` → named. B1d `describe_package {pkg}` →
  "accepted: package (alias: path)".
- C: spawn cube → list_transactions shows "Mif Bridge: spawn_actor_in_level" (index 0, 6 records,
  20,832 bytes) → undo_transactions {count:1} returns the exact title, cube verified GONE via
  list_level_actors (matched 0) → redo returns titlesRedone, cube BACK at {500,500,100} →
  describe_class then list_transactions: queue unchanged (bucket hygiene proven with the new
  endpoint) → list_dirty_packages: /Temp/Untitled_0 kind:world origin:"new" saveable:false →
  save_dirty_packages: skipped[] echoes "no on-disk destination (transient/untitled package) —
  use save_level_as for an untitled map".

**Batch B verdict: COMPLETE. Batch C verdict: COMPLETE. Live endpoint count: 160 → 165.**

## Batch C — undo introspection + dirty-package flows (5 endpoints)

Five endpoints from the Phase-2-verified specs: the transaction trio + save_dirty_packages
(docs/audit/work/A_editor_core.md) and list_dirty_packages (docs/audit/work/B_assets_registry.md —
B owns it per the ratified dedup). All five live in the NEW `Source/MifBridge/Private/MifBridgeUndo.cpp`:
they all operate on editor-SESSION state (the transaction buffer, the dirty-package set), not on any
one named asset, so they do not belong in MifBridgeAssetOps.cpp (whose contract is single-asset
/Game/-only lifecycle ops through AssetTools). Registry: MIF_DECL 160 → **165**, MIF_BIND 160 →
**165** (raw `grep -c "MIF_DECL("` / `"MIF_BIND("` = 166 each — the extra hit is the `#define`
line); server.py @mcp.tool 160 → **165**. Build + live proofs happen later this session.

**Shared-helper promotion**: `RejectUnknownParams` (Batch B, file-local static in
MifBridgeCooked.cpp:59) is now declared in `MifBridgeHandlers.h` and defined ONCE in
`MifBridgeCommon.cpp`; MifBridgeCooked.cpp's local copy was deleted (its call sites are unchanged)
and all five new handlers use the shared one. One implementation, no drifting copies.

**server.py latent-bug fix (found while adding the wrappers)**: the `spawn_actor_in_pie` block sat
AFTER the `if __name__ == "__main__": main()` guard, where `mcp.run()` blocks before the definition
executes — the tool was counted by grep but never actually registered at runtime. Relocated above
`main()`. Live proof: an MCP client must now list `spawn_actor_in_pie` among the tools.

### list_transactions — bucket: read-only

- **Engine API**: `UTransactor::GetQueueLength/GetUndoCount/GetTransaction/GetUndoContext/CanUndo/
  CanRedo/GetCurrentUndoBarrier` (Editor/UnrealEd/Classes/Editor/Transactor.h:555, :597, :573,
  :583, :539, :548, :626 — all method-level UNREALED_API on the MinimalAPI class, per the Phase-2
  verdict); per-entry detail via `FTransaction::GetTitle` (:398 inline), `GetId` (:386 inline),
  `GetContext` (:361 inline), `GetTransactionObjects` (:460), `GetRecordCount` (:461),
  `DataSize` (:383); reached through `GEditor->Trans` (EditorEngine.h:307). No UTransBuffer cast
  needed.
- **Files**: MifBridgeUndo.cpp (H_list_transactions), MifBridgeHandlers.h (MIF_DECL),
  MifBridgeCommon.cpp (MIF_BIND + IsReadOnlyEndpoint), server.py.
- **Params**: limit (count, max; default 20), offset (start; 0 = newest end), includeObjects
  (include_objects). Unknown params rejected by name. `offset >= queueLength` returns an empty
  page + queueLength (pollers must be able to drain), NOT an error.
- **Live proof**: `set_actor_transform` on a test actor → `list_transactions {}` shows queueLength
  +1, the new index-N entry titled "Mif Bridge: set_actor_transform" with recordCount >= 1 and
  dataSizeBytes > 0, currentIndex == queueLength-1.

### undo_transactions — bucket: SELF-MANAGED (per A-axis spec)

- **Engine API**: `UNREALED_API bool UndoTransaction(bool bCanRedo = true);`
  (Editor/UnrealEd/Classes/Editor/EditorEngine.h:934) — preferred over raw `UTransactor::Undo` so
  editor-side notification runs. Self-managed because beginning/running undo inside an open
  transaction violates `ensure(!GIsTransacting)` (UTransBuffer::BeginInternal, TransBuffer.h:74) —
  an undo inside RunEndpoint's blanket transaction is nonsense. Via IsCompileHeavyEndpoint this
  also bars it from batch (its PostUndo path reinstances Blueprints, EditorServer.cpp:1406).
- **Files**: MifBridgeUndo.cpp (H_undo_transactions), MifBridgeHandlers.h, MifBridgeCommon.cpp
  (MIF_BIND + IsSelfManagedEndpoint), server.py.
- **Params**: count (n, steps; 1..50) XOR toIndex (to_index; -1 = undo everything, 50-step cap per
  call), allowRedo (allow_redo, canRedo; default true). Pre-checks mirror the engine's silent-false
  guard (GIsSavingPackage/IsGarbageCollecting, EditorServer.cpp:1414) so refusals carry a reason;
  CanUndo-fails-during-PIE reports "blocked during PIE — stop_pie first". Partial progress (k>0)
  is success + stoppedEarly/reason; zero progress is an error (02_GOTCHAS: never silence a
  mutating call).
- **Live proof**: `set_actor_transform` cube to Z=500 (confirm via get_property) →
  `undo_transactions {"count": 1}` returns undone=1, titlesUndone=["Mif Bridge:
  set_actor_transform"], undoCount +1, queueLength unchanged → get_property shows the original Z.
  Also: spawn cube via `spawn_actor_in_level` → `list_transactions` shows the spawn entry at
  index N → `undo_transactions {"count": 1}` → `list_level_actors` no longer shows the cube.

### redo_transactions — bucket: SELF-MANAGED (per A-axis spec)

- **Engine API**: `UNREALED_API bool RedoTransaction();` (EditorEngine.h:935); titles read via
  `UTransactor::GetRedoContext()` (Transactor.h). Same GIsTransacting invariant as undo. The redo
  stack is wiped by ANY new transaction (UTransBuffer::BeginInternal, TransBuffer.h:80-90) —
  documented loudly in the server.py docstring.
- **Files**: MifBridgeUndo.cpp (H_redo_transactions), MifBridgeHandlers.h, MifBridgeCommon.cpp
  (MIF_BIND + IsSelfManagedEndpoint), server.py.
- **Params**: count (n, steps; 1..50) XOR toIndex (to_index; redo while currentIndex < toIndex,
  50-step cap). Returns redone, titlesRedone, stoppedEarly/reason, new queue position.
- **Live proof**: continuing the undo proof: `redo_transactions {"count": 1}` → redone=1,
  undoCount back down by 1 → get_property shows Z=500 again / `list_level_actors` shows the cube
  back.

### list_dirty_packages — bucket: read-only (B-axis owner)

- **Engine API**: `UNREALED_API static void GetDirtyWorldPackages(TArray<UPackage*>&, ...)` /
  `GetDirtyContentPackages(...)` — FEditorFileUtils, Editor/UnrealEd/Public/FileHelpers.h:402/:409
  (the Phase-2-corrected citation: the :144 overload belongs to UEditorLoadingAndSavingUtils at
  FileHelpers.h:38-39, and the FEditorFileUtils trio :402/:409/:417 is preferred). Both are plain
  TObjectIterator scans — no GC, no dialogs.
- **Files**: MifBridgeUndo.cpp (H_list_dirty_packages), MifBridgeHandlers.h, MifBridgeCommon.cpp
  (MIF_BIND + IsReadOnlyEndpoint), server.py.
- **Params**: kind = content | world | all (unknown values and unknown keys both error). Rows carry
  origin loose|container|new — deliberately NOT MifBridgeCooked.cpp's IsContainerOnlyPackage test,
  which would misreport a never-saved package as "container"; container rows are saveable:false
  (a dirty cooked base-game package can never be saved — the red flag this endpoint raises).
- **Live proof**: `set_actor_transform` on a placed actor dirties the map package →
  `list_dirty_packages {}` shows it under kind "world" (counts.world >= 1); `set_variable_default`
  on a /Game/ BP → its package appears under kind "content"; after `save_dirty_packages` both
  disappear (count 0).

### save_dirty_packages — bucket: SELF-MANAGED (per A-axis spec; asset-lifecycle precedent)

- **Engine API**: enumeration via the same FEditorFileUtils::GetDirtyWorldPackages/
  GetDirtyContentPackages (FileHelpers.h:402/:409); saving per-package via
  `FEditorFileUtils::SaveLevel` (FileHelpers.h:288, the save_level_as house path) for world
  packages and `UPackage::SavePackage` (the save_blueprint/save_package house pattern,
  .umap-vs-.uasset by `ContainsMap()`) for everything else. The handler deliberately does NOT call
  `FEditorFileUtils::SaveDirtyPackages(bPromptUserToSave=false, ..., bFastSave=true)`
  (FileHelpers.h:383) — the Phase-2 verdict's three CORRECTED hazards forbid it:
  1. its fast branch hardcodes `bUseDialog = true` (FileHelpers.cpp:3822-3828) and routes failures
     to `FMessageDialog::Open` (:3620-3640) — a blocking modal on the same game thread the bridge
     answers HTTP on (deadlock, not dialog);
  2. its pre-filter silently drops read-only and never-saved packages (:3703-3746) — every such
     package is instead ECHOED in the response (failed[] "file is read-only: <path>" / skipped[]
     with reason);
  3. its dirty enumeration runs `CollectGarbage(GARBAGE_COLLECTION_KEEPFLAGS)` when content is
     included (FileHelpers.cpp:3642-3647) — mid-frame GC that kills unrooted UObjects held across
     the call. The GetDirty* pair used instead performs no GC.
  Self-managed because saving is not undoable and a wrapping transaction would record package
  dirty-flag state into the undo stack (FTransaction::FPackageRecord, Transactor.h:240-254).
- **Files**: MifBridgeUndo.cpp (H_save_dirty_packages), MifBridgeHandlers.h, MifBridgeCommon.cpp
  (MIF_BIND + IsSelfManagedEndpoint), server.py.
- **Params**: maps (saveMaps, save_maps; default true), content (saveContent, save_content;
  default true), dryRun (dry_run; wouldSave[] instead of saved[]). Hard error during PIE when
  maps=true ("stop_pie first (or pass maps=false for a content-only save)").
- **Live proof**: dirty 2 content packages (`list_dirty_packages` shows counts.content == 2) →
  `save_dirty_packages {}` → saved[] length 2, failed[]/skipped[] empty → `list_dirty_packages`
  shows 0. Skip echo: `new_level` (untitled) + move an actor → `save_dirty_packages
  {"content": false}` → the /Temp/Untitled package appears under skipped[] with the
  "use save_level_as" reason, not silently dropped.

### Spec deviations (and why)

- **redo_transactions returns titlesRedone** — the A-axis return shape omitted titles for redo;
  the batch brief requires "undo/redo must return what was undone by title", so redo reports
  titles symmetrically (additive field, nothing removed).
- **list_dirty_packages response shape follows the B-axis owner** (flat packages[] with per-row
  kind/origin/saveable + counts{world,content}), not the A-axis duplicate's
  mapPackages/contentPackages split — B owns the endpoint per the ratified dedup; the world/content
  split the brief asks for is carried by the kind field and the counts object.
- **toIndex is capped at 50 steps per call** (stoppedEarly + "call again" reason) — the spec caps
  count at 50 to bound frame time; an uncapped toIndex would reintroduce the exact unbounded
  game-thread loop the cap exists to prevent.
- **save_dirty_packages adds a skipped[] array** beyond the spec's saved/failed/skippedCookedOrigin
  — required by the batch brief ("echo the engine's silent skips as explicit skipped entries with
  reasons") for the non-cooked silent-skip class (untitled/transient packages).

## Batch D — material graph authoring (10 endpoints)

The audit's flagship Tier-0 category (docs/audit/work/D_materials_rendering.md — all entries
Phase-2 adversarially verified; the CORRECTED verdict on recompile_material is implemented as
binding). All ten handlers live in the NEW `Source/MifBridge/Private/MifBridgeMaterials.cpp`.
Registry: MIF_DECL 165 -> **175**, MIF_BIND 165 -> **175** (raw `grep -c "MIF_DECL("` /
`"MIF_BIND("` = **176 each** — the extra hit is the `#define` line); server.py @mcp.tool
165 -> **175** (all ten wrappers sit ABOVE the `__main__` guard — the spawn_actor_in_pie
lesson). `ast.parse` passes. Build + live proofs happen later this session.

**New module dependency**: `MaterialEditor` added to MifBridge.Build.cs
PrivateDependencyModuleNames — the FIRST new module dep since the audit began.
UMaterialEditingLibrary is class-level MATERIALEDITOR_API (MaterialEditingLibrary.h:57);
editor-only, engine-core, no plugin gating (per the axis sweep's Build.cs re-read). Everything
else in the batch links against modules already in the dep list (Engine/UnrealEd).

**Shared-helper promotion**: `JIntAny` (Batch C's file-local `JIntAnyLocal` in
MifBridgeUndo.cpp, whose comment said "local until a second file needs it") is now declared in
MifBridgeHandlers.h and defined in MifBridgeCommon.cpp; add_material_expression is the second
caller and MifBridgeUndo.cpp's six call sites were switched to the shared one.

**Axis-wide cooked rule** (spec negative #3, enforced in every handler): UMaterialExpression is
`UCLASS(abstract, Optional, ...)` (MaterialExpression.h:183-184) and the expression collection
lives in `UMaterialEditorOnlyData` (`UCLASS(MinimalAPI, Optional)`, Material.h:309-310) —
Optional objects are STRIPPED from cooked packages, and `UMaterial::GetExpressions()` derefs
the editor-only data with no null check (Material.cpp:1426-1429), so a cooked material is a
CRASH, not an empty graph. Every mutating graph endpoint refuses on cooked/container-origin
packages with an error naming create_material + create_material_instance as the alternatives;
list_material_expressions degrades honestly (`numExpressions: 0, cooked: true`). Container
detection checks the IoDispatcher location EXPLICITLY (never "no loose file => cooked", which
would misreport brand-new unsaved assets — the DirtyPackageOrigin lesson from Batch C).

### create_material — bucket: SELF-MANAGED

- **Engine API**: `UMaterialFactoryNew::FactoryCreateNew`
  (Editor/UnrealEd/Classes/Factories/MaterialFactoryNew.h:14-26 — MinimalAPI class, NO
  method-level export, so the call goes through the factory POINTER via virtual dispatch,
  negative #5; a qualified `UMaterialFactoryNew::FactoryCreateNew` call would fail to link);
  domain/blendMode via direct UPROPERTY writes (`MaterialDomain` Material.h:449, `BlendMode`
  :453) + PreEditChange(nullptr)/PostEditChange. Domain list = non-hidden EMaterialDomain
  (MaterialDomain.h:12-30); blendMode list = non-Substrate EBlendMode (EngineTypes.h:249-263).
- Self-managed per the create_material_instance precedent (MifBridgeAuthoring.cpp:280-353):
  untransacted, explicit `FAssetRegistryModule::AssetCreated` + `MarkPackageDirty`. The initial
  shader compile is an async ENQUEUE — response carries {compiling, numRemainingJobs} + the
  poll hint. Refuses existing paths ("use a new path or delete_asset first").

### create_material_function — bucket: SELF-MANAGED

- **Engine API**: `UMaterialFunctionFactoryNew::FactoryCreateNew`
  (Editor/UnrealEd/Classes/Factories/MaterialFunctionFactoryNew.h:14-22, same
  MinimalAPI/virtual-dispatch route); optional `description` -> `UMaterialFunction::Description`
  (MaterialFunction.h:52) and `exposeToLibrary` -> `bExposeToLibrary` (:60) — both plain
  UPROPERTY data members per the Phase-2 resolution.
- `kind` (layer/layerBlend) is deliberately NOT accepted yet: RejectUnknownParams carries a
  KeyNote naming it as unimplemented-until-set_material_instance_layers rather than a typo.

### add_material_expression — bucket: transacted

- **Engine API**: `UMaterialEditingLibrary::CreateMaterialExpressionEx`
  (MaterialEditingLibrary.h:74-75; impl cpp:511-596 verified pure object-model — no editor
  window, no dialogs, no waits). Class resolution accepts short ("ScalarParameter"), full
  ("MaterialExpressionScalarParameter"), and path ("/Script/Engine...") spellings + the
  Lerp/TexCoord editor aliases; unknown class errors with the 10 nearest matches by edit
  distance over the live UClass census (abstract/deprecated excluded).
- `properties{}` applies via the set_property FProperty ImportText machinery (scratch-copy
  import — a rejected value never wipes the live property). Property NAMES and value kinds are
  pre-validated BEFORE the expression is created (the blanket transaction commits even on
  ok:false, so post-creation errors would leave a half-added node); a value that fails to
  parse deletes the just-created node again — failed calls add NOTHING. Unknown property name
  is a hard error naming property + class (silent-ignore is the audit's #1 bug class).
  `asset` -> CreateMaterialExpressionEx SelectedAsset (auto-wires TextureSample [+
  AutoSetSampleType], MaterialFunctionCall [via SetMaterialFunction], CollectionParameter —
  cpp:542-566). Both the asset AND its Optional editor-only-data subobject are Modify()'d
  (the expression array lives on the latter — undo would otherwise lose it).

### connect_material_expressions — bucket: transacted

- **Engine API**: `UMaterialEditingLibrary::ConnectMaterialExpressions`
  (MaterialEditingLibrary.h:158-159; impl cpp:677-692 — pure `Input->Connect` pointer wiring);
  pin discovery via `GetMaterialExpressionInputNames` (:203-204). Masked-output pin names
  R/G/B/A per the engine's GetExpressionOutputName (cpp:808-834), mirrored locally.
- A failed connect echoes BOTH pin lists (target inputs + source outputs) so the caller can
  self-correct in one round-trip. The TO node is Modify()'d (the FExpressionInput lives there).

### connect_material_property — bucket: transacted

- **Engine API**: `UMaterialEditingLibrary::ConnectMaterialProperty`
  (MaterialEditingLibrary.h:148-149; impl cpp:656-676 requires FromExpression's outer to BE the
  UMaterial — guaranteed by resolving the expression out of that material's own collection).
  EMaterialProperty verbatim from SceneTypes.h:159-200; accepted names per the spec's table
  (MP_ prefix optional, case-insensitive, ClearCoat->MP_CustomData0,
  ClearCoatRoughness->MP_CustomData1); deprecated/meta values get the dedicated "not
  connectable in 5.3" error. UMaterialFunction paths are steered to FunctionOutput expressions.
  Editor-only data Modify()'d (property inputs live there).

### delete_material_expression — bucket: transacted

- **Engine API**: `DeleteMaterialExpression` / `DeleteAllMaterialExpressions` /
  `DeleteMaterialExpressionInFunction` / `DeleteAllMaterialExpressionsInFunction`
  (MaterialEditingLibrary.h:97-102, :242-248 — all four verbatim per Phase-2; the library
  handles disconnection). `expression` XOR `all=true` (both => "ambiguous" error). Every
  expression is Modify()'d pre-delete (deletion rewires OTHER nodes' inputs too).

### list_material_expressions — bucket: read-only

- **Engine API**: `UMaterial::GetExpressions` (ENGINE_API, Material.h:1242) /
  `UMaterialFunction::GetExpressions` (MaterialFunction.h:183); per-node introspection via the
  MATERIALEDITOR_API statics (GetMaterialExpressionInputNames :203-204,
  GetMaterialExpressionNodePosition :219-220) + the ENGINE_API virtual
  `UMaterialExpression::GetInputsView` (MaterialExpression.h:351 — works uniformly for
  material AND function graphs, unlike GetInputsForMaterialExpression which null-gates on a
  UMaterial). Property bindings via `UMaterial::GetExpressionInputForProperty` (ENGINE_API,
  Material.h:1668) with our OWN null check — deliberately NOT the library's
  GetMaterialPropertyInputNode, which derefs the return unguarded (cpp:797-806, the Phase-2
  crash caution).
- Returns numbers an agent can assert on: numExpressions, per-node x/y + reflection-dumped
  configuration properties, per-node inputs[{input, from, fromOutput}], connectionCount,
  propertyBindings[]. Cooked: numExpressions 0 + cooked:true + the explanatory note.

### layout_material_expressions — bucket: transacted

- **Engine API**: `LayoutMaterialExpressions` / `LayoutMaterialFunctionExpressions`
  (MaterialEditingLibrary.h:170-171, :260-261; impl cpp:193-278 works on
  MaterialExpressionEditorX/Y directly — no GraphNode/editor-window requirement, per the
  Phase-2 resolution). Response carries the verdict's caveat: only nodes REACHABLE from
  property/function outputs are moved. Every expression Modify()'d (positions live on them).

### recompile_material — bucket: SELF-MANAGED (per the CORRECTED Phase-2 verdict)

- **Engine API**: dispatches on class. UMaterialFunction(Interface) ->
  `UMaterialEditingLibrary::UpdateMaterialFunction` (:254-255) and UMaterialInstanceConstant ->
  `UpdateMaterialInstance` (:327-328) — both branches verified clean (cpp:985-1032 /
  :1187-1202: enqueue only, no GC, no dialogs, no waits) and called directly.
- **UMaterial branch does NOT call `RecompileMaterial`** (:164-165): its hidden tail
  `FMaterialEditorUtilities::BuildTextureStreamingData` (cpp:731) (1) runs
  `CollectGarbage(GARBAGE_COLLECTION_KEEPFLAGS)` TWICE (MaterialEditorUtilities.cpp:789/:814),
  (2) opens `FScopedSlowTask` + `MakeDialog(true)` (:791-792) — modal UI pumped on the bridge's
  own game thread, and (3) busy-waits on debug-view-mode shader compiles
  (DebugViewModeHelpers.cpp:322-356). Per the verdict the handler replicates ONLY the
  non-blocking core (MaterialEditingLibrary.cpp:697-728): `FMaterialUpdateContext` +
  `AddMaterial` + `PreEditChange(nullptr)`/`PostEditChange()` + `MarkPackageDirty`
  (FMaterialUpdateContext ctor/dtor/AddMaterial all ENGINE_API, MaterialShared.h:2779+), plus
  the RefreshEditor/RedrawAllViewports broadcasts and the particle-view-relevance /
  child-instance UpdateParameterNames loops from the same range. RebuildMaterialInstanceEditors
  (editor-window refresh) and BuildTextureStreamingData are deliberately omitted.
- Response returns immediately with {compiling, numRemainingJobs} — shader compilation
  continues in the background; shader_compile_status is the poll. Refuses cooked assets
  ("shaders ship as fixed permutations").

### shader_compile_status — bucket: read-only

- **Engine API**: `extern ENGINE_API FShaderCompilingManager* GShaderCompilingManager`
  (ShaderCompiler.h:928); `GetNumPendingJobs`/`GetNumOutstandingJobs` (ENGINE_API :746-747);
  `IsCompiling` (:770-773) and `GetNumRemainingJobs` (:798-801) are header-inline and compile
  into MifBridge. Zero params (RejectUnknownParams with the empty accepted set). Shares one
  field-writer with recompile_material's response so the poll loop needs no translation.

### Live proof (post-build) — numeric all the way

1. `create_material {path:"/Game/Mods/AuditProofs/M_AuditProof"}` -> materialPath echoed,
   numExpressions 0.
2. `add_material_expression {path:..., class:"ScalarParameter", x:-400, y:0,
   properties:{ParameterName:"Roughness", DefaultValue:0.35}}` -> expressionName A;
   same for `VectorParameter` (ParameterName "Tint", y:-200) -> B; `Multiply` (x:-150) -> C.
   propertiesApplied echoes the counts.
3. `connect_material_expressions {from:B, to:C, toInput:"A"}` and `{from:A, to:C,
   toInput:"B"}` -> connected:true both.
4. `connect_material_property {from:C, property:"BaseColor"}` and `{from:A,
   property:"Roughness"}` -> connected:true both.
5. `list_material_expressions {path:...}` -> numExpressions **3**, connectionCount **2**,
   propertyBindings [{BaseColor <- C}, {Roughness <- A}]; A's properties block echoes
   ParameterName "Roughness" and DefaultValue **0.35** exactly.
6. `recompile_material {path:...}` -> recompiled:true, kind "material", compiling true (or
   numRemainingJobs > 0 on a non-trivial graph).
7. `shader_compile_status` polled until compiling:false, numRemainingJobs **0** (numbers
   strictly decrease).
8. EXISTING `create_material_instance {parent:"/Game/Mods/AuditProofs/M_AuditProof",
   path:"/Game/Mods/AuditProofs/MI_AuditProof"}` -> materialPath echoed (parent linkage
   proves the new master is a real material).
9. EXISTING `set_material_parameter {material:"/Game/Mods/AuditProofs/MI_AuditProof",
   scalars:{Roughness:0.5}}` -> applied **1**, unknownParameters [] (the parameter EXISTS
   because step 2 created it on the parent).
10. `get_property {objectPath:"/Game/Mods/AuditProofs/MI_AuditProof",
    property:"ScalarParameterValues"}` -> reads back Roughness = **0.5**.

### Spec deviations (and why)

- **get_material_stats and the other 15 axis-D proposals are NOT in this batch** — the batch
  brief scopes exactly the 10 graph-authoring-loop endpoints; get_material_stats' verdict
  (synchronous FinishCompilation wait needing an IsGameThreadShaderMapComplete guard) ships
  with a later batch.
- **create_material_function omits the `kind` param** the axis's Compositions table sketches
  (layer/layerBlend factory variants) — that design belongs to set_material_instance_layers
  (tier 3, unscheduled); the unknown-param KeyNote names it as unimplemented so callers get
  the truthful error today.
- **recompile_material's UMaterial branch also replicates the editor-refresh broadcasts and
  particle/child-instance loops** (cpp:709-726), not just the four calls the verdict's
  one-line summary names — the verdict's "replicate its non-blocking core (cpp:697-728)"
  range includes them, and each is an async flag write or delegate broadcast.
- **add_material_expression deletes the new node when a properties value fails to parse** —
  the spec's failure-mode table only demands the error; the deletion keeps the documented
  "failed call mutates nothing" contract inside RunEndpoint's commit-anyway blanket
  transaction (names are pre-validated, so this path only triggers on unparseable VALUES).
- **list_material_expressions reports property bindings via
  UMaterial::GetExpressionInputForProperty directly** instead of the spec's cited
  GetMaterialPropertyInputNode — the spec's OWN Phase-2 verdict flags that library function's
  null-deref; calling the ENGINE_API accessor with an explicit null check implements the
  verdict's guidance literally.

### Batch D build + live proofs — PASS, with two findings (2026-07-26 ~15:40 ET)

**Three build attempts.** Both failures were textbook instances of the audit's own verification rules,
worth recording because they generalise:

1. `error C2248: UMaterialInstance::UpdateParameterNames: cannot access protected member`
   (MifBridgeMaterials.cpp:1457). The symbol IS `ENGINE_API` (MaterialInstance.h:1007) — **exported
   is not the same as accessible**. The engine's own tail calls it legally only because
   `UMaterialEditingLibrary` is a declared `friend` (MaterialInstance.h:1064). Fixed by routing
   through that friend's public static `UpdateMaterialInstance` (MaterialEditingLibrary.cpp:1187,
   which itself calls UpdateParameterNames + UpdateStaticPermutation) over
   `TObjectIterator<UMaterialInstanceConstant>`. **Export-macro checks must be paired with an
   access-specifier check** — a rule the audit did not state and now should.
2. `LNK2019: unresolved external GMaxRHIShaderPlatform` — not referenced by our code at all: it is
   the **default argument** of `FMaterialUpdateContext`'s constructor (MaterialShared.h:2817), and
   default arguments are evaluated in the *caller's* translation unit. Fixed by adding `RHI`
   (RHIShaderPlatform.h:86) to Build.cs. **A default argument can impose a module dependency that
   no visible call names.**

**Live state**: endpointCount **175**, policyContradictions **[]**, all ten buckets exactly as
specified (list_material_expressions + shader_compile_status read-only; create_material,
create_material_function, recompile_material self-managed; the five graph mutations transacted).

**Proof chain** (`/Game/Mods/AuditProofs/M_AuditProof`): create_material → 3 expressions added
(ScalarParameter with ParameterName=Roughness/DefaultValue=0.35 and VectorParameter Tint both
confirmed by property read-back in list_material_expressions) → recompile_material → 
shader_compile_status 0 jobs → save_package wrote `Content/Mods/AuditProofs/M_AuditProof.uasset` →
create_material_instance parented to it → layout_material_expressions. **Cooked refusal proven**:
add_material_expression on `/Game/Landscape/Materials/DDS2_Landscape_MasterMat` returns the
designed message (graph stripped, UCLASS Optional, use create_material / create_material_instance).

**Finding D-1 (usability, open)**: expressions are addressable only by their UObject name
(`MaterialExpressionScalarParameter_0`), returned by add_material_expression. Addressing by
`ParameterName` ("Roughness"/"Tint") or by a unique class short name ("Multiply") fails. The error
is excellent (it lists every valid name), and the returned-handle workflow is sound — but the house
alias rule (accept the spellings a caller would reasonably use) says these should resolve. Queued
for Batch D.1.

**Finding D-2 (BUG in a pre-existing endpoint, §7-class)**: `set_material_parameter`
(MifBridgeAuthoring.cpp:359) accepts `{material, scalars:{}, vectors:{}}`. A call passing
`{path, parameter, value}` returned **`ok:true, applied:0, unknownParameters:[]`** — it silently
dropped `parameter` and `value`, reporting success while doing nothing. This is precisely the
failure mode PM-001/PM-005 exist to prevent, found by live-testing rather than by the source sweep.
Queued for Batch D.1 (add the shared RejectUnknownParams guard; `unknownParameters` currently only
reports parameters the *parent material* lacks, not unrecognised JSON keys).

**Batch D verdict: COMPLETE — 10 endpoints live-proven. Live count 165 → 175.**

---

## Batch D.1 — expression aliases + set_material_parameter silent-ignore repair

Closes the two findings Batch D's live testing raised (D-1 usability, D-2 bug). No new endpoints;
endpoint count stays **175**. Source-only — **not built, not live-proven yet**: the proofs at the
bottom are the exact calls to run after the next build.

### Batch D.1 — what changed, per file

**`Source/MifBridge/Private/MifBridgeMaterials.cpp`** (finding D-1)

- `FindExpressionByName` — the single resolver behind every expression-addressing endpoint — grew
  two alias rules and an ambiguity refusal. Precedence, first rule producing **exactly one**
  candidate wins:
  1. **exact UObject name** (`MaterialExpressionScalarParameter_0`) — unchanged, and never
     redirected: if it hits, no alias rule is consulted.
  2. **ParameterName** (`"Tint"`).
  3. **unique class short name** (`"Multiply"`) — accepted only when the graph holds exactly ONE
     node of that class. The `Lerp` -> `LinearInterpolate` / `TexCoord` -> `TextureCoordinate`
     editor aliases `add_material_expression`'s `class` already accepts are honoured here too, so a
     node added as `Lerp` is addressable as `Lerp`. A leading `MaterialExpression` is optional.
  - **Ambiguity is an error listing the candidates**, never a pick — two `Tint`s or two `Multiply`s
    return `expression 'X' is AMBIGUOUS in <asset> — N expressions …: <list>. Address one by its
    exact object name.` This is the pin-alias rule of `docs/02_GOTCHAS.md` §1 ("accepts the hit only
    if exactly one pin matches") applied to nodes.
- **No `ClassName#index` form** (the brief's optional rule 4) — deliberately. The UObject name IS
  the indexed form the engine already guarantees (`MaterialExpressionMultiply_0/_1`); a second index
  over the graph array would disagree with that suffix the moment a node is deleted, giving two
  spellings for one slot with one of them silently wrong.
- New helper `ExpressionParameterName()` finds the name in two steps:
  - `UMaterialExpression::HasAParameterName()` / `GetParameterName()` (MaterialExpression.h:551-561,
    public `#if WITH_EDITOR` inline virtuals — a vtable dispatch, so none of the export/access
    hazard that broke Batch D's first build). The engine's own comment there explains why they
    exist: "multiple class have ParameterName but are not UMaterialExpressionParameter due to class
    hierarchy". Verified overriders in 5.3: `UMaterialExpressionParameter`,
    `…TextureSampleParameter`, `…FontSampleParameter`, `…CollectionParameter`,
    `…RuntimeVirtualTextureSampleParameter`, `…SparseVolumeTextureSample` — **six families, not one
    base class**, which is exactly why `Cast<UMaterialExpressionParameter>` would have missed five.
  - Reflection fallback for classes carrying a `ParameterName` UPROPERTY **without** overriding the
    virtual — not hypothetical here: the Landscape expressions
    (`MaterialExpressionLandscapeLayerWeight/Switch/Sample.h`) do exactly that, and this project's
    landscape master material is full of them. (`MaterialExpressionLandscapeVisibilityMask`'s
    `static FName ParameterName` cannot be a UPROPERTY, so it correctly never matches.)
- The **not-found error now names the accepted forms**, and its catalogue rows carry the parameter
  name and the class SHORT name — the two spellings rules 2 and 3 accept:
  `MaterialExpressionScalarParameter_0 (ScalarParameter, ParameterName='Roughness')`.
- Applied to **every** endpoint in the file that takes an expression name — nothing else resolves
  expressions: `connect_material_expressions` (`from`, `to`), `connect_material_property` (`from`),
  `delete_material_expression` (`expression`). Each endpoint's `//   in:` comment and its
  "required" / "not found" errors were updated to state the three forms.

**`Source/MifBridge/Private/MifBridgeAuthoring.cpp`** (finding D-2 + the file-wide sweep)

- `set_material_parameter` rebuilt around four defences, in the order a bad call meets them:
  1. **`RejectUnknownParams`** — the shared guard declared in `MifBridgeHandlers.h` (promoted in
     Batch C) is CALLED, not redefined. Accepted: `material`/`materialPath`/`path`, `scalars`,
     `vectors`, `parameter`/`parameterName`/`name`, `value`. KeyNotes explain `textures`/`texture`
     and `switches` as unimplemented capabilities rather than typos.
  2. **`path` is an explicit alias** of `material` (with `materialPath`) via `JStrAny`. Verified
     against the D-2 live response: the endpoint DID already read `path` — that part of the report
     was the alias working; the drop was `parameter`/`value`. An empty/missing path now errors by
     name instead of falling into the "not found: " message with a blank path.
  3. **The singular `{parameter, value}` form is implemented** (see the decision note below).
  4. **Every no-op path is now an error**: no scalars and no vectors =>
     `nothing to apply — pass scalars:{...} and/or vectors:{...}, or the singular parameter + value`;
     a value that is neither scalar nor vector => error **naming the parameter**; a `scalars` entry
     that is not a number, or a `vectors` entry that is not `{r,g,b,a}` / `{x,y,z,w}` / `[r,g,b,a]`
     => error naming the key and the JSON type it got (these used to `continue` — a silent drop of
     the same family as D-2); and **applying zero parameters because the parent exposes none of
     them is now a failure**, not `ok:true, applied:0, unknownParameters:[…]`.
- Validation is complete **before the first write**, so a rejected call mutates nothing (the
  `add_material_expression` contract). The response gains `scalarsApplied` / `vectorsApplied`
  beside `applied`; `unknownParameters` + its note survive unchanged for the partial-success case
  (some names applied, others unknown => still `ok:true`).
- Vector values additionally accept `{x,y,z,w}` and `[r,g,b,a]` alongside `{r,g,b,a}` — one
  file-local `JsonToLinearColor` is the only thing that decides what a JSON value MEANS, so the
  maps and the sugar cannot drift apart.

**`tools/ue5-mcp-bridge/server.py`** — docstrings only, no new tools, no signature changes:
`connect_material_expressions`, `connect_material_property` and `delete_material_expression` now
state the three accepted spellings and the ambiguity refusal; `set_material_parameter` states the
new error cases (neither map given, no name recognised) and that texture/static-switch parameters
are not supported there.

### Batch D.1 — accepted parameter forms (the contract callers can rely on)

| Endpoint | Field | Accepted forms |
|---|---|---|
| `connect_material_expressions` | `from`, `to` | object name · ParameterName · unique class short name |
| `connect_material_property` | `from` | object name · ParameterName · unique class short name |
| `delete_material_expression` | `expression` | object name · ParameterName · unique class short name |
| `set_material_parameter` | material | `material` · `materialPath` · `path` |
| `set_material_parameter` | values | `scalars:{name:number}` · `vectors:{name:{r,g,b,a} or {x,y,z,w} or [r,g,b,a]}` · `parameter`/`parameterName`/`name` + `value` |

`value`'s family is **inferred from its JSON type**: number (or numeric string) => scalar;
object/array => vector; anything else (boolean, asset path) => error naming the parameter. A boolean
would mean a static switch and a string would mean a texture — neither is implemented here, so
neither is guessed at.

### Batch D.1 — the sweep of the rest of MifBridgeAuthoring.cpp

All five handlers in the file predate the strict-params rule. Every one now runs
`RejectUnknownParams` on its TOP-LEVEL keys:

| Handler | Guarded | Notes / deferred |
|---|---|---|
| `set_material_parameter` | yes | the D-2 fix itself |
| `spawn_many` | yes | **TODO(audit D.1)**: per-item objects inside `items[]` are still lenient — a typo'd item key defaults instead of erroring, and a non-object entry lands in `failed` with no reason |
| `duplicate_actors` | yes | its `in:` comment advertised **`rotationOffset`**, which no line has ever read (the code rotates by the scalar `yawOffset`) — comment corrected, key now refused by name with a KeyNote |
| `create_material_instance` | yes | its `in:` comment advertised **`textures?:{name:path}`**, never implemented — comment corrected, key now refused by name. **TODO(audit D.1)**: two drops remain INSIDE the handler (a non-numeric scalar / non-object vector is skipped silently; unlike `set_material_parameter` it never checks the parent exposes the name, so `parametersApplied` can count writes the material ignores). Deferred because it creates the asset before applying (self-managed bucket, no blanket transaction), so a mid-apply error would strand a half-configured asset — it needs the same pre-validate-then-write restructure |
| `add_foliage_instances` | yes | **TODO(audit D.1)**: `instances[]` entries share `spawn_many`'s per-item leniency, so `instanceCount` can be lower than the array length with no reason given. Deferred with it, and for the same reason: both need one per-item error shape |

Also left as a **TODO(audit D.1)** on `set_material_parameter`: it never calls `MIC->Modify()`, so
its writes are invisible to the blanket transaction and Ctrl-Z does not restore the previous values.
That is an undo-correctness bug, not the silent-ignore bug D-2 named, so it is recorded rather than
folded into this fix.

### Batch D.1 — live proofs to run after the next build

Against the Batch D proof assets (`M_AuditProof` holds ScalarParameter `Roughness`,
VectorParameter `Tint`, and one `Multiply`; `MI_AuditProof` is its instance).

```bash
B=http://127.0.0.1:8791/api
H=(-H "X-Mif-Token: dev" -H "Content-Type: application/json")

# D-1.1  ParameterName as `from` — the call that FAILED in Batch D must now succeed.
curl -s -X POST $B/connect_material_expressions "${H[@]}" \
  -d '{"path":"/Game/Mods/AuditProofs/M_AuditProof","from":"Tint","to":"Multiply","toInput":"A"}'
# expect: ok:true, connected:true, from:"MaterialExpressionVectorParameter_0",
#         to:"MaterialExpressionMultiply_0"  — from/to echo the RESOLVED object names, and that
#         echo IS the proof the alias resolved to the node you meant.

# D-1.2  unique class short name as `to`, ParameterName as `from`.
curl -s -X POST $B/connect_material_expressions "${H[@]}" \
  -d '{"path":"/Game/Mods/AuditProofs/M_AuditProof","from":"Roughness","to":"Multiply","toInput":"B"}'
# expect: ok:true, connected:true

# D-1.3  connect_material_property by ParameterName.
curl -s -X POST $B/connect_material_property "${H[@]}" \
  -d '{"path":"/Game/Mods/AuditProofs/M_AuditProof","from":"Roughness","property":"Roughness"}'
# expect: ok:true, connected:true, from:"MaterialExpressionScalarParameter_0"

# D-1.4  AMBIGUITY REFUSAL. Add a second Multiply, then address the class.
curl -s -X POST $B/add_material_expression "${H[@]}" \
  -d '{"path":"/Game/Mods/AuditProofs/M_AuditProof","class":"Multiply","x":-150,"y":200}'
curl -s -X POST $B/connect_material_expressions "${H[@]}" \
  -d '{"path":"/Game/Mods/AuditProofs/M_AuditProof","from":"Tint","to":"Multiply","toInput":"A"}'
# expect: ok:FALSE, error contains "AMBIGUOUS" and BOTH MaterialExpressionMultiply_0 and _1.
# The exact object name must still work while the class name does not:
curl -s -X POST $B/connect_material_expressions "${H[@]}" \
  -d '{"path":"/Game/Mods/AuditProofs/M_AuditProof","from":"Tint","to":"MaterialExpressionMultiply_1","toInput":"A"}'
# expect: ok:true  (rule 1 is never blocked by an ambiguity under rules 2/3)

# D-1.5  not-found error teaches the forms.
curl -s -X POST $B/delete_material_expression "${H[@]}" \
  -d '{"path":"/Game/Mods/AuditProofs/M_AuditProof","expression":"Nope"}'
# expect: ok:false; error names "the exact object name, a ParameterName, or a class short name",
#         and the catalogue rows read like  ..._0 (ScalarParameter, ParameterName='Roughness')

# D-1.6  cleanup, and the delete path resolving an object name.
curl -s -X POST $B/delete_material_expression "${H[@]}" \
  -d '{"path":"/Game/Mods/AuditProofs/M_AuditProof","expression":"MaterialExpressionMultiply_1"}'
# expect: ok:true, deleted:1

# D-2.1  THE BUG. This exact body returned ok:true, applied:0 in Batch D.
curl -s -X POST $B/set_material_parameter "${H[@]}" \
  -d '{"path":"/Game/Mods/AuditProofs/MI_AuditProof","parameter":"Roughness","value":0.75}'
# expect: ok:true, applied:1, scalarsApplied:1  (the sugar is implemented — see the decision note)
curl -s -X POST $B/get_property "${H[@]}" \
  -d '{"objectPath":"/Game/Mods/AuditProofs/MI_AuditProof","property":"ScalarParameterValues"}'
# expect: Roughness == 0.75  (read-back, not a self-report)

# D-2.2  singular vector form.
curl -s -X POST $B/set_material_parameter "${H[@]}" \
  -d '{"path":"/Game/Mods/AuditProofs/MI_AuditProof","parameterName":"Tint","value":{"r":0.2,"g":0.4,"b":0.9,"a":1}}'
# expect: ok:true, vectorsApplied:1

# D-2.3  NOTHING TO APPLY must fail.
curl -s -X POST $B/set_material_parameter "${H[@]}" \
  -d '{"material":"/Game/Mods/AuditProofs/MI_AuditProof"}'
# expect: ok:FALSE, error "nothing to apply — pass scalars:{...} and/or vectors:{...}, or the
#         singular parameter ... + value ..."   (previously ok:true, applied:0)

# D-2.4  unknown key must fail BY NAME.
curl -s -X POST $B/set_material_parameter "${H[@]}" \
  -d '{"material":"/Game/Mods/AuditProofs/MI_AuditProof","scalars":{"Roughness":0.5},"tiling":4}'
# expect: ok:FALSE, error names 'tiling' and lists the accepted set
curl -s -X POST $B/set_material_parameter "${H[@]}" \
  -d '{"material":"/Game/Mods/AuditProofs/MI_AuditProof","textures":{"Base":"/Game/T_Rock"}}'
# expect: ok:FALSE, error carries the KeyNote "texture parameters are NOT implemented on this endpoint"

# D-2.5  un-inferable value errors BY PARAMETER NAME.
curl -s -X POST $B/set_material_parameter "${H[@]}" \
  -d '{"material":"/Game/Mods/AuditProofs/MI_AuditProof","parameter":"Roughness","value":true}'
# expect: ok:FALSE, error contains "cannot tell whether parameter 'Roughness' is a scalar or a vector"

# D-2.6  no name recognised is a failure; a PARTIAL hit still succeeds.
curl -s -X POST $B/set_material_parameter "${H[@]}" \
  -d '{"material":"/Game/Mods/AuditProofs/MI_AuditProof","scalars":{"NoSuchParam":1}}'
# expect: ok:FALSE, error "nothing applied — ... exposes none of these parameters: NoSuchParam"
curl -s -X POST $B/set_material_parameter "${H[@]}" \
  -d '{"material":"/Game/Mods/AuditProofs/MI_AuditProof","scalars":{"Roughness":0.5,"NoSuchParam":1}}'
# expect: ok:true, applied:1, unknownParameters:["NoSuchParam"], + the note

# D-2.7  regression: the ORIGINAL Batch D step 9 body must behave exactly as before.
curl -s -X POST $B/set_material_parameter "${H[@]}" \
  -d '{"material":"/Game/Mods/AuditProofs/MI_AuditProof","scalars":{"Roughness":0.5}}'
# expect: ok:true, applied:1, unknownParameters:[]

# D-2.8  sweep regressions — documented keys still work, an advertised-but-unread key now refuses.
curl -s -X POST $B/spawn_many "${H[@]}" \
  -d '{"actorClass":"StaticMeshActor","labelPrefix":"D1Proof","items":[{"x":0,"y":0,"z":100}]}'
# expect: ok:true, spawned:1
curl -s -X POST $B/duplicate_actors "${H[@]}" \
  -d '{"labelPrefix":"D1Proof","offset":{"x":300,"y":0,"z":0},"rotationOffset":{"z":90}}'
# expect: ok:FALSE, KeyNote "not implemented — duplicate_actors rotates about Z only: pass yawOffset"
curl -s -X POST $B/duplicate_actors "${H[@]}" \
  -d '{"labelPrefix":"D1Proof","offset":{"x":300,"y":0,"z":0},"yawOffset":90}'
# expect: ok:true, duplicated:1
```

### Batch D.1 — decision: the `{parameter, value}` sugar SHIPS

The brief allowed dropping it for (a)+(b) plus a good error. It is implemented instead, because the
whole of D-2 is that `{path, parameter, value}` is what a caller actually writes — an error teaching
the map form still costs the round-trip the alias rule exists to save, and the endpoint would be
refusing an input whose meaning is unambiguous. The risk in sugar is guessing, and there is no
guessing here: the family is read off the JSON type, the ONE ambiguous case (a value that is neither
number nor object/array) is a hard error naming the parameter, and the sugar is folded into the same
maps before any write, so it cannot diverge from the map path.

**Batch D.1 verdict: source complete, UNBUILT.** Endpoint count unchanged (175). Follow-ups recorded
as TODO(audit D.1) in code and in the sweep table above: per-item validation for
`spawn_many` / `add_foliage_instances`, the pre-validate restructure for `create_material_instance`,
and `Modify()` for undo on `set_material_parameter`.

---

## Batch R phase 1 — external endpoint registration interface + kr_list_cooked_blueprints

Implements Batch 0 of `docs/audit/work/K_IMPL_PLAN.md` §C.1 — **the mechanism**, plus exactly ONE
provider endpoint to prove it end to end. Coupling model **(b)** (provider registration), ratified in
`work/K2_reconstructor_pipeline.md` §B: every `kr_*` handler lives in the **MifKismetReconstructor**
module, next to the Private code it calls, so no reconstructor symbol is exported and MifBridge gains
no dependency on its providers.

**Deviation from the plan, deliberate**: the plan's Batch 0 uses a throwaway `kr_ping`. This batch
registers the real Wave-1 #1 endpoint `kr_list_cooked_blueprints` instead — it exercises the identical
mechanism (registration, merge, bucket, route bind, provider attribution) *and* returns real data, so
there is no proof-only endpoint to delete later. `kr_ping` is not implemented and is not planned.

**Anchors re-verified live before editing** (K_IMPL_PLAN.md §0.1's rule; all matched the plan exactly,
no drift this time): `MifBridgeCommon.cpp` `Handlers()` :29, `GetEndpointNames()` :247,
`IsReadOnlyEndpoint` :256, `IsSelfManagedEndpoint` :309, `H_self_audit` :362,
`IsCompileHeavyEndpoint` :409, `RunEndpoint` :421; `MifBridgeServer.cpp` bind loop :88-108,
`StartAllListeners()` :110; `grep -c` = **176 / 176**.

### Files changed

| File | Change |
|---|---|
| `Source/MifBridge/Public/MifBridgeEndpointRegistry.h` | **NEW.** MifBridge's FIRST exported symbols (`grep -rn MIFBRIDGE_API Source/` was 0 hits, now exactly 2). `FExternalHandler`, `EEndpointBucket{ReadOnly,SelfManaged,Transacted}`, `FExternalEndpointDesc{Name,Bucket,Provider,Summary,Handler}`, `RegisterExternalEndpoint(Desc, FString* OutError)`, `UnregisterExternalEndpoints(Provider)`. **Forward-declares `class FJsonObject`; does NOT include `Dom/JsonObject.h`** — `Json` is a PRIVATE dep (MifBridge.Build.cs:39) and private deps do not propagate include paths to dependents (plan §0.3 C-1). |
| `Private/MifBridgeCommon.cpp` | 6 edits: registry include; `ExternalRegistry()` function-local static + `GbRouteTableLive`/`MarkRouteTableLive()` + both registrar definitions, inserted directly after `Handlers()`; `GetEndpointNames()` merges both maps; `IsReadOnlyEndpoint`/`IsSelfManagedEndpoint` fall back to the descriptor's bucket after their TSet miss; `RunEndpoint` consults the external map on the `Handlers().Find` miss and dispatches honouring the bucket; `H_self_audit` gains `endpointDetails[]`, `externalEndpointCount`, `externalProviders[]`. |
| `Private/MifBridgeHandlers.h` | 1 declaration: `void MarkRouteTableLive();` after `IsCompileHeavyEndpoint`. **No MIF_DECL added.** |
| `Private/MifBridgeServer.cpp` | 1 line: `MifBridge::MarkRouteTableLive();` after `Http.StartAllListeners();`. The route-binding loop is byte-unchanged — externals arrive through `GetEndpointNames()` at :88. |
| `MifBridge.Build.cs` | **UNCHANGED** (deliberate, plan §B.4): the header needs only `Core`, and the forward declaration removes any reason to promote `Json`. |
| `MifKismetReconstructor.Build.cs` | Optional `MifBridge` dep + `WITH_MIFBRIDGE=1/0`, guarded by `Directory.Exists(PluginDirectory/../MifBridge/Source/MifBridge)`. The define is ALWAYS emitted (1 or 0), so sources use a plain `#if`. |
| `MifKismetReconstructor.uplugin` | New `"Plugins": [{ "Name": "MifBridge", "Enabled": true, "Optional": true }]`. `Optional` is what preserves "the reconstructor works without the bridge". No `LoadingPhase` change: Default (KR) already precedes PostEngineInit (MB). |
| `Private/MifKrBridgeEndpoints.cpp` | **NEW**, whole file under `#if WITH_MIFBRIDGE`. Local param/JSON helpers, the registry enumeration, `H_kr_list_cooked_blueprints`, `MifKr_RegisterBridgeEndpoints()` / `MifKr_UnregisterBridgeEndpoints()`. |
| `Private/MifKismetReconstructorModule.cpp` | 3 edits: `extern` pair under `#if WITH_MIFBRIDGE` beside the existing verifier externs; `MifKr_RegisterBridgeEndpoints()` in `StartupModule` after `MifKr_BindFidelityVerifier()`; `MifKr_UnregisterBridgeEndpoints()` in `ShutdownModule` after `MifKr_UnbindFidelityVerifier()`. |

`server.py` is deliberately **untouched** in this phase — raw HTTP proves the mechanism; the
`@mcp.tool()` wrapper lands in phase 2 (plan §C.2 requires it in the same commit as the *tool* work).

### The mechanism's contract

- **Registry is three-way now.** Built-ins = `MIF_DECL` + `MIF_BIND` (+ `@mcp.tool`); externals = ONE
  `RegisterExternalEndpoint` call in the provider (+ `@mcp.tool`). External endpoints appear in
  **neither** MifBridge file, so the `grep -c "MIF_DECL(" == grep -c "MIF_BIND("` invariant is
  untouched: **176 / 176 before and after this batch.** `self_audit.endpoints`, built from the live
  merged map, remains the single source of truth — now with `provider` making drift attributable.
- **One merge point.** `GetEndpointNames()` unions both maps; that single change is what makes route
  binding (`MifBridgeServer.cpp:88-108`) and `self_audit` pick externals up with no further edits.
- **Buckets, single by construction.** A descriptor carries exactly one `EEndpointBucket`, so the
  twin-set `policyContradictions` class cannot exist for externals; `healthy` semantics are unchanged.
  `IsCompileHeavyEndpoint` needed **zero changes** — it derives from `IsSelfManagedEndpoint`
  (`return IsSelfManagedEndpoint(Endpoint) || compile || validate`), so an external `SelfManaged`
  endpoint is automatically fenced out of `batch`'s open transaction. Verified by reading it, not
  assumed.
- **Hard rule, stated in the header**: the registration API must never touch module-startup state.
  Linking MifBridge makes the OS loader map the MifBridge DLL when the PROVIDER DLL loads, and the
  provider loads at `Default` while MifBridge loads at `PostEngineInit` — so
  `RegisterExternalEndpoint` legally runs **before `FMifBridgeModule::StartupModule`**. The registry
  is a function-local static; reading the server/menus/token from here would turn a working provider
  into a startup crash.
- **Failures are loud, never silent.** Name collision with a built-in → `endpoint 'x' collides with a
  MifBridge built-in`; duplicate external → `endpoint 'x' already registered by provider 'y'`;
  post-route-binding registration → `endpoint 'x': route table already live — register from your
  module's StartupModule (routes bind once at server start)`. Empty name / empty handler / empty
  Provider / non-game-thread each get their own message. A `false` return means the endpoint does not
  exist, and the provider logs it at `Error`.
- **Shutdown symmetry.** `UnregisterExternalEndpoints(Provider)` returns the count removed. The HTTP
  route stays bound until the server restarts; `RunEndpoint` then answers `unknown endpoint`, which is
  the correct answer once the provider is gone.

### SHIP-SAFETY GATE (the plan's R3, enforced in code and in comments)

The reconstructor's existing resolution/analysis helpers (`MifKr_ResolveBPGC`, `MifKr_RegistryMatches`,
`MifKr_CoerceToBPGC`, `MifKr_FindBPGCByName`, `MifKr_AnalyzeUbergraph`) are **both file-static and
inside `#if MIF_KR_DEBUG`** (`MifReconstructorDebug.h:9-11`, whose own comment says *"Ship OFF (set to
0) before any release"*). Binding any of them would make the entire HTTP surface **vanish** the day the
gate flips — endpoints silently gone, `self_audit` count silently lower, nothing explaining why.
`MifKrBridgeEndpoints.cpp` therefore reimplements the ~20 lines of registry enumeration it needs,
ungated, and carries that rule as its file header. Review gate (must return zero, excluding the
comment block that necessarily names the symbols):

```bash
grep -n "MifKr_ResolveBPGC\|MifKr_RegistryMatches\|MifKr_CoerceToBPGC\|MifKr_FindBPGCByName\|MifKr_AnalyzeUbergraph\|MifKr_DumpBP\|MifKr_OpcodeName" \
  Source/MifKismetReconstructor/Private/MifKrBridgeEndpoints.cpp | grep -v "^[0-9]*://"
```

### `kr_list_cooked_blueprints` — provider `MifKismetReconstructor`, bucket **readOnly**

- **In**: `pathContains` (aliases `pathFilter`, `path`; default `/Game/`, `"*"` = every mounted root),
  `cookedOnly` (default true), `includeWidgets` (default true), `offset` (0), `limit` (200, max 2000).
  Any other key is rejected **by name** with the accepted list; `pathPrefix` and `nameContains` carry
  KeyNotes explaining they are not typos but different/absent capabilities.
- **Out** (structured numerics, not prose): `total` (every Blueprint package the registry knows,
  before filtering), `matched` (after filtering), `returned`, `offset`, `limit`, `truncated`,
  `blueprints[{objectPath, packageName, name, class, cooked, loaded, generatedClass}]`,
  `filter{...}`, `note`, and a `hint` when `matched == 0`.
- **Enumeration** mirrors `Analysis/MifUbergraphAnalyzer.cpp:84-102` as DATA (an `FARFilter` shape),
  not logic: `UBlueprintGeneratedClass` + `UBlueprint` class paths, `bRecursiveClasses = true` (which
  is how Widget/Anim BPGCs arrive without a UMG dependency), then `PKG_Cooked` + package dedup.
  Dedup **prefers the `*_C` generated-class row** (class-name suffix test, again to avoid a UMG dep),
  because that is the path every downstream `kr_*` resolver takes — and because a cooked Blueprint has
  no `UBlueprint` asset at all.
- **Paging is deterministic**: results are sorted by package name (`FName::LexicalLess`) *before*
  slicing, so consecutive pages neither overlap nor skip and their counts sum to `matched`. TMap
  iteration order is not an order.
- **Cooked behaviour, stated**: this endpoint exists FOR cooked content. Container-only BPGCs (IoStore
  packages the modkit premounts) appear exactly like loose assets; `cooked` reports `PKG_Cooked` per
  package; `cookedOnly:false` additionally lists uncooked Blueprints flagged `cooked:false`.
- **Loads nothing.** It reads the in-memory registry index and reports `loaded` from
  `FAssetData::IsAssetLoaded()` — it cannot fault a package in or run a construction script, which is
  what makes read-only the honest bucket. `IAssetRegistry::IsLoadingAssets()` (IAssetRegistry.h:719) is
  refused explicitly (`asset registry still scanning; retry after the initial scan completes`) rather
  than answering with a partial index and `ok:true`.
- **`limit` is refused, not clamped**, above 2000 (`limit 5000 exceeds maximum 2000; page with
  offset`): a silently clamped page would under-report the corpus with no cursor saying so.

**RejectUnknownParams duplication — decision (a), per plan §B.7.** `MifBridge::RejectUnknownParams`
and the `JStr*/JInt/JBool` accessors live in `Private/MifBridgeHandlers.h`, unreachable from the
provider module even though it links MifBridge. Rather than re-export them (which would grow
MifBridge's brand-new public surface from two functions into a JSON-accessor library that must then
stay ABI-stable for every future provider), `MifKrBridgeEndpoints.cpp` carries local
`KrRejectUnknownParams` / `KrJStrAny` / `KrJBool` / `KrJInt` / `KrFail` mirrors, with the duplication
and its eviction clause documented in the file header. Precedent: the helper itself was born
file-local in `MifBridgeCooked.cpp` and was only promoted when a second file needed it. **If a third
provider plugin appears, promote instead of copying a third time.**

### Live proof (REQUIRED — this batch is source-complete and UNBUILT)

No build and no editor launch were performed (the editor is running). Nothing below has been executed
yet; these are the exact gating calls, and this section must be updated with the responses.

```bash
BR=http://127.0.0.1:8791/api
H=(-H "X-Mif-Token: ${MIF_BRIDGE_TOKEN:-dev}" -H "Content-Type: application/json")

# 1) the mechanism
curl -s -X POST $BR/self_audit "${H[@]}" -d '{}' \
| python -c "import sys,json; d=json.load(sys.stdin); \
  r=[e for e in d['endpointDetails'] if e['name']=='kr_list_cooked_blueprints']; \
  print('count', d['endpointCount'], 'external', d.get('externalEndpointCount')); \
  print('providers', d.get('externalProviders')); print('row', r); \
  assert d['endpointCount']==177; \
  assert r and r[0]['provider']=='MifKismetReconstructor' and r[0]['bucket']=='readOnly'; \
  assert 'kr_list_cooked_blueprints' in d['transactionBuckets']['readOnly']; \
  assert 'kr_list_cooked_blueprints' not in d['transactionBuckets']['transacted']; \
  assert d['healthy'] and not d['policyContradictions']"

# 2) the endpoint, against this project's real containers
curl -s -X POST $BR/kr_list_cooked_blueprints "${H[@]}" -d '{"pathContains":"*","limit":1}'          # read total/matched
curl -s -X POST $BR/kr_list_cooked_blueprints "${H[@]}" -d '{"pathContains":"/Game/Blueprints/Pawns/NPC/","limit":50}'
curl -s -X POST $BR/kr_list_cooked_blueprints "${H[@]}" -d '{"pathContains":"/Game/","offset":0,"limit":100}'
curl -s -X POST $BR/kr_list_cooked_blueprints "${H[@]}" -d '{"pathContains":"/Game/","offset":100,"limit":100}'
curl -s -X POST $BR/kr_list_cooked_blueprints "${H[@]}" -d '{"bogusParam":1}'                        # MUST error, naming the key
curl -s -X POST $BR/kr_list_cooked_blueprints "${H[@]}" -d '{"limit":5000}'                          # MUST error with the paging message
```

Pass conditions, all six:
1. `self_audit.endpointCount` is **177** (176 built-ins + 1 external) — the merged map really is what
   `GetEndpointNames()` returns. `externalEndpointCount` is 1.
2. `endpointDetails` contains
   `{"name":"kr_list_cooked_blueprints","provider":"MifKismetReconstructor","bucket":"readOnly"}` —
   provider attribution works.
3. `externalProviders` is `[{"provider":"MifKismetReconstructor","endpointCount":1}]`.
4. `transactionBuckets.readOnly` contains the name (and `transacted` does not) — the descriptor's
   bucket really drives dispatch policy.
5. `POST /api/kr_list_cooked_blueprints` returns `ok:true` with **real** counts from this project's
   mounted containers — `pathContains:"*"` should land near the analyzer's measured **1277** cooked BP
   packages (allow drift for DLC mounts: `/ChristmasDlc`, `/DDS2Casino`), and `BP_BaseNPC` must appear
   with `cooked:true`. A 404 here means registration lost the race with `FMifBridgeServer::Start()`
   and everything else is moot.
6. `healthy:true`, `policyContradictions:[]`; the two negative calls error with their designed
   messages; the `offset:0` and `offset:100` pages do not overlap and their `returned` counts sum.

Then the **negative proof**: disable MifKismetReconstructor in the .uproject, restart, confirm
`endpointCount` is back to **176** and MifBridge still serves — the soft-coupling property
(`MifBridgeReconstruct.cpp:73-74`) is preserved, which is the entire reason model (b) was chosen.

**Known risk carried forward (plan R2, unresolved):** the `Directory.Exists` guard adds the MifBridge
dep whenever the *folder* is present, even if the plugin is *disabled in the .uproject* — UE 5.3
`ModuleRules` has no clean "is this plugin enabled" query. If that bites at link time, the fallback is
an unconditional dep with `WITH_MIFBRIDGE=1` always (both plugins ship together in this modkit); it is
a two-line change either way. Test at first build in BOTH configurations.

**Batch R phase 1 verdict: source complete, UNBUILT, live proof pending.** Endpoint count 176 → **177**
on the next successful build. MIF_DECL/MIF_BIND stay at **176 each** — externals never appear there,
and that is the invariant this design is built to preserve.

### Batch R phase 1 + D.1 — BUILD PASS, MECHANISM PROVEN (2026-07-26 ~16:40 ET)

Both DLLs linked (UnrealEditor-MifBridge.dll + UnrealEditor-MifKismetReconstructor.dll).

**The cross-plugin mechanism works.** Live `self_audit`:

```
endpointCount        176        (175 built-in + 1 external)
policyContradictions []
externalEndpointCount 1
externalProviders    [{ "provider": "MifKismetReconstructor", "endpointCount": 1 }]
endpointDetails[]    kr_list_cooked_blueprints -> provider "MifKismetReconstructor", bucket readOnly
                     self_audit                -> provider "MifBridge",              bucket readOnly
```

MIF_DECL == MIF_BIND == 175 real invocations, UNCHANGED — externals are structurally absent from
that invariant, which is the point of the design. MifBridge retains zero hard dependency on the
reconstructor: remove the plugin and the 175 built-ins keep serving; only the kr_* rows disappear.

`kr_list_cooked_blueprints {limit:5}` → `total:2395, matched:1277` cooked Blueprint packages,
registry-only (nothing loaded). **The 1277 independently matches the reconstructor's own recorded
corpus size** (work/K2_reconstructor_pipeline.md: "1256/1277 BPs"), which is a strong cross-check
that the census is reading the same population the reconstructor was built against.
Unknown param refused by name; `limit:0` refused with "pass 1..2000" (refused, not clamped, per
the K1 failure-mode spec).

**D.1 proofs (materials)**:
- ParameterName alias: `connect_material_expressions {from:"Tint"}` → resolved
  `MaterialExpressionVectorParameter_0`, connected. Unique class short name `"Multiply"` →
  `MaterialExpressionMultiply_0` wired to BaseColor. `list_material_expressions` shows
  `connectionCount:1` and the propertyBindings row.
- **D-2 repaired, numerically proven**: `{parameter:"Roughness", value:0.5}` → `applied:1,
  scalarsApplied:1` (was `ok:true, applied:0` silently); the map form still works; `get_property`
  read back `((ParameterInfo=(Name="Roughness"),ParameterValue=0.750000))`.
  Empty call → error showing both accepted forms (was a silent success). Unknown key `bogus` →
  refused with the accepted-key list. A parameter the PARENT does not expose → error naming it and
  pointing at the two endpoints that list real names (was silently accepted).
- `duplicate_actors {rotationOffset}` → refused, and the message names the parameter that actually
  exists: "duplicate_actors rotates about Z only: pass yawOffset:<degrees>".

**Environment quirk worth recording**: an asset created at `/Game/Mods/AuditProofs/...` resolves
back as `/Game/MODS/AuditProofs/...` — an existing uppercase directory on a case-insensitive
filesystem. Both spellings resolve, but any code doing case-sensitive path comparison against a
returned path would silently mismatch.

**Batch R phase 1 verdict: COMPLETE — live count 175 → 176, first external provider registered.**

---

## Batch R phase 2 — seven Wave-1 kr_* endpoints + MCP tools

Written 2026-07-26, immediately after phase 1's live proof. Phase 1 proved the *mechanism* with one
endpoint; phase 2 fills in the remaining seven Wave-1 endpoints and wires all eight into the MCP
server. **Source complete, UNBUILT** (the editor is running; the build is the next session's first
act). Every asset path in the curls below was verified live against the running editor this session
with `find_assets` — they exist, they are `origin:"container"` (pak-mounted, i.e. genuinely cooked),
and they are currently `loaded:false`.

**Wave 1 needed ZERO export promotions.** Everything the seven handlers call is either already
`MIFKISMETRECONSTRUCTOR_API` (`FKismetBytecodeDisassemblerJson`, `FPropertyTypeHelper`), already
`KISMET_API` (`CreateEditableBlueprintCopy`), or lives in the same module as the handlers
(`MifReconstructFunctionIntoGraph`, `namespace MifUber`) — which is the whole payoff of ratifying
coupling model (b). Not one declaration changed in either plugin's headers.

### Files changed (4 + 1 doc)

| File | New/Mod |
|---|---|
| `KR/Private/MifKrBridgeEndpoints.cpp` | MOD — 365 lines → 2440; the seven handlers + shared helpers |
| `KR/Private/MifKrJobManager.h` | **NEW** — the one-slot job record's contract |
| `KR/Private/MifKrJobManager.cpp` | **NEW** — its state machine (state only; the work lives with the handlers) |
| `KR/Private/MifKismetReconstructorModule.cpp` | MOD — 3 small edits: include the job header, and one `Notify*` call inside each of the two already-bound reconstruction delegate lambdas (both `#if WITH_MIFBRIDGE`) |
| `MifBridge/tools/ue5-mcp-bridge/server.py` | MOD — 8 `@mcp.tool()` wrappers (175 → **183**), all above the `if __name__` guard |

`MifBridgeCommon.cpp`, `MifBridgeHandlers.h` and `MifBridgeEndpointRegistry.h` are **byte-identical**
to phase 1. Nothing in phase 2 needed a bridge-side change — which is the property the registrar was
designed to have.

### The seven endpoints

| Endpoint | Bucket | Underlying call |
|---|---|---|
| `kr_dump_blueprint` | `ReadOnly` | `TFieldIterator<UFunction/FProperty>(BPGC, ExcludeSuper)` for structure; `FKismetBytecodeDisassemblerJson::SerializeFunction` **only for the returned page, only when `includeBytecode`** |
| `kr_disassemble_function` | `ReadOnly` | `UClass::FindFunctionByName(..., ExcludeSuper)` + `FKismetBytecodeDisassemblerJson::SerializeFunction` |
| `kr_list_events` | `ReadOnly` | `MifUber::RecoverEvent` / `ClassifyEvent` / `KindName` over the analyzer's thunk filter |
| `kr_analyze_ubergraph` | `ReadOnly` | `MifUber::BuildStatements` + `DetectPrologue` + one `WalkEvent` per event, over a single `SerializeFunction` of the ubergraph |
| `kr_pin_type_from_property` | `ReadOnly` | `UStruct::FindPropertyByName` → `FPropertyTypeHelper::ConvertPropertyToPinType` → `SerializeGraphPinType`, plus a new emitter for the bridge type grammar |
| `kr_reconstruct_request` | **`SelfManaged`** | deferred one tick via `GEditor->GetTimerManager()->SetTimerForNextTick`; then `CreateEditableBlueprintCopy` (mode `copy`) or `CreateBlueprint` + `CreateNewGraph` + `AddFunctionGraph<UFunction>` + `MifReconstructFunctionIntoGraph` + `CompileBlueprint` with an `FCompilerResultsLog` (mode `function`) |
| `kr_reconstruct_status` | `ReadOnly` | reads the `MifKr::Jobs` POD record; counters fed by the two engine delegates the module already binds |

`kr_reconstruct_request` is the only non-read endpoint and it is `SelfManaged` for the documented
reason: its deferred slice runs a full `CompileBlueprint` (and, in copy mode, a package save), and a
full compile captured by a blanket undo transaction means reinstancing in the undo buffer, a dead CDO
and a crash. `SelfManaged` also makes it compile-heavy, which fences it out of `batch`'s single open
transaction for free. `kr_reconstruct_status` is `ReadOnly` because it is *polled in a loop* — a
transacted poll would push one empty undo entry per call.

### Live proofs (run these after the build)

```bash
BR=http://127.0.0.1:8791/api
H=(-H "X-Mif-Token: ${MIF_BRIDGE_TOKEN:-dev}" -H "Content-Type: application/json")
ANIM=/Game/Animations/AnimClasses/NPC/PrisonerAnimBP.PrisonerAnimBP_C          # AnimBlueprintGeneratedClass
RAID=/Game/Audio/Music/ChaseAndFight/RaidAreaSphere.RaidAreaSphere_C
SMALL=/Game/Blueprints/Enviro/Markers/BP_SegmentedPathTaskMarker.BP_SegmentedPathTaskMarker_C
MED=/Game/Blueprints/Pawns/NPC/Oponents/Behaviour/BP_OponentPatrolRoute.BP_OponentPatrolRoute_C
```

**Gate first — `self_audit` must read 183 / 8:**
```bash
curl -s -X POST $BR/self_audit "${H[@]}" -d '{}' \
| python -c "import sys,json; d=json.load(sys.stdin); \
  kr=[e for e in d['endpointDetails'] if e['name'].startswith('kr_')]; \
  print('endpointCount', d['endpointCount'], 'external', d['externalEndpointCount']); \
  [print(' ', e['name'], e['bucket']) for e in sorted(kr, key=lambda x: x['name'])]; \
  assert d['endpointCount']==183 and d['externalEndpointCount']==8; \
  assert d['healthy'] and not d['policyContradictions']; \
  assert [e['name'] for e in kr if e['bucket']=='selfManaged']==['kr_reconstruct_request']"
```

**1. `kr_dump_blueprint`** — pass: `counts.functionsOwn` > 0; `parentChain[0].class` is
`AnimInstance`-derived for `$ANIM`; the second call disassembles exactly the filtered function and
`opcodeHistogramTotal` > 0; the third call reports `effectiveLimit:10` with the cap sentence in `note`.
```bash
curl -s -X POST $BR/kr_dump_blueprint "${H[@]}" -d "{\"asset\":\"$SMALL\"}"
curl -s -X POST $BR/kr_dump_blueprint "${H[@]}" -d "{\"asset\":\"$SMALL\",\"functionFilter\":\"SegmentOverlapp\",\"includeBytecode\":true}"
curl -s -X POST $BR/kr_dump_blueprint "${H[@]}" -d "{\"asset\":\"$ANIM\",\"includeBytecode\":true}"   # expect effectiveLimit 10 + note
curl -s -X POST $BR/kr_dump_blueprint "${H[@]}" -d "{\"asset\":\"$RAID\"}"
curl -s -X POST $BR/kr_dump_blueprint "${H[@]}" -d "{\"asset\":\"$SMALL\",\"bogusParam\":1}"          # MUST error, naming the key
```

**2. `kr_disassemble_function`** — pass: unpaginated `returned == totalStatements`; the two paged
calls concatenate to exactly that array and `totalStatements` is identical across all three; the
unknown-function call names near-miss own function names.
```bash
curl -s -X POST $BR/kr_disassemble_function "${H[@]}" -d "{\"asset\":\"$SMALL\",\"function\":\"SegmentOverlapp\"}"
curl -s -X POST $BR/kr_disassemble_function "${H[@]}" -d "{\"asset\":\"$SMALL\",\"function\":\"SegmentOverlapp\",\"statementOffset\":0,\"statementLimit\":5}"
curl -s -X POST $BR/kr_disassemble_function "${H[@]}" -d "{\"asset\":\"$SMALL\",\"function\":\"SegmentOverlapp\",\"statementOffset\":5,\"statementLimit\":5}"
curl -s -X POST $BR/kr_disassemble_function "${H[@]}" -d "{\"asset\":\"$MED\",\"function\":\"GetNextPatrolPoint\",\"includeRaw\":false}"
curl -s -X POST $BR/kr_disassemble_function "${H[@]}" -d "{\"asset\":\"$SMALL\",\"function\":\"NoSuchFunction\"}"   # MUST error with near-misses
```

**3. `kr_list_events`** — pass: every listed event has `recovered:true` and `entryOffset >= 0`
(measured corpus rate is 5871/5871); `counts.events == counts.recovered + counts.failed`; a BP with
no ubergraph returns `ok:true, events:[], status:"NO_UBERGRAPH"` — **an empty list, never an error**.
```bash
curl -s -X POST $BR/kr_list_events "${H[@]}" -d "{\"asset\":\"$SMALL\"}"
curl -s -X POST $BR/kr_list_events "${H[@]}" -d "{\"asset\":\"$RAID\"}"
curl -s -X POST $BR/kr_list_events "${H[@]}" -d "{\"asset\":\"$MED\",\"kind\":\"bndEvt\"}"
curl -s -X POST $BR/kr_list_events "${H[@]}" -d "{\"asset\":\"$ANIM\"}"           # AnimBPGC — closes K1 UNVERIFIED #1
curl -s -X POST $BR/kr_list_events "${H[@]}" -d "{\"asset\":\"$SMALL\",\"kind\":\"nope\"}"   # MUST error listing the enum
```

**4. `kr_analyze_ubergraph`** — pass: the `invariant` field ends in `holds`
(`analysedStmts == reached1 + shared + unreached`); `unreached == 0` and
`events.recovered == events.total` on all three (corpus measured 0 unreached / 100 % recovery);
`counts.sharedLatent <= counts.latentStmts`.
```bash
curl -s -X POST $BR/kr_analyze_ubergraph "${H[@]}" -d "{\"asset\":\"$SMALL\"}"
curl -s -X POST $BR/kr_analyze_ubergraph "${H[@]}" -d "{\"asset\":\"$MED\",\"includeOffsets\":true}"
curl -s -X POST $BR/kr_analyze_ubergraph "${H[@]}" -d "{\"asset\":\"$RAID\"}"
```

**5. `kr_pin_type_from_property`** — pass: `PathActive` → `bridgeType:"bool"`,
`pinType.PinCategory:"bool"`; `PathSpline` → `bridgeType:"object:/Script/Engine.SplineComponent"`
(a FULL path, never a short name) with `bridgeTypeUsable:true`; a native class resolves too.
```bash
curl -s -X POST $BR/kr_pin_type_from_property "${H[@]}" -d "{\"class\":\"$SMALL\",\"property\":\"PathActive\"}"
curl -s -X POST $BR/kr_pin_type_from_property "${H[@]}" -d "{\"class\":\"$SMALL\",\"property\":\"PathSpline\"}"
curl -s -X POST $BR/kr_pin_type_from_property "${H[@]}" -d '{"class":"/Script/Engine.Actor","property":"Tags"}'   # array container
curl -s -X POST $BR/kr_pin_type_from_property "${H[@]}" -d "{\"class\":\"$SMALL\",\"property\":\"NoSuchProp\"}"    # MUST error with near-misses
```

**6 + 7. `kr_reconstruct_request` / `kr_reconstruct_status`** — pass: the request returns
`state:"queued"` and a `jobId` **immediately**; a poll reports `state:"done"` with
`functionsDone == 1`, `result.graphNodes > 2` (a stub graph has only entry + result),
`compile.measured:true` and `compile.errors == 0`; the busy proof's second request **fails** naming
the running jobId; the ubergraph request is refused with the reason.
```bash
# function mode against a known-good function
curl -s -X POST $BR/kr_reconstruct_request "${H[@]}" \
  -d "{\"sourceAsset\":\"$MED\",\"mode\":\"function\",\"function\":\"GetNextPatrolPoint\"}"
curl -s -X POST $BR/kr_reconstruct_status "${H[@]}" -d '{}'          # poll to done
# cross-parameter rules — BOTH must error, naming the offending parameter
curl -s -X POST $BR/kr_reconstruct_request "${H[@]}" -d "{\"sourceAsset\":\"$MED\",\"mode\":\"function\",\"function\":\"SetupEnds\",\"variant\":\"child\"}"
curl -s -X POST $BR/kr_reconstruct_request "${H[@]}" -d "{\"sourceAsset\":\"$MED\",\"mode\":\"copy\",\"function\":\"SetupEnds\"}"
# ubergraph refusal
curl -s -X POST $BR/kr_reconstruct_request "${H[@]}" \
  -d "{\"sourceAsset\":\"$SMALL\",\"mode\":\"function\",\"function\":\"ExecuteUbergraph_BP_SegmentedPathTaskMarker\"}"
# busy-slot proof: fire two back to back, the SECOND must fail with runningJobId
curl -s -X POST $BR/kr_reconstruct_request "${H[@]}" -d "{\"sourceAsset\":\"$SMALL\",\"mode\":\"copy\",\"variant\":\"child\"}" &
curl -s -X POST $BR/kr_reconstruct_request "${H[@]}" -d "{\"sourceAsset\":\"$RAID\",\"mode\":\"copy\",\"variant\":\"child\"}"
# copy mode + the FIRST wall-clock timings that will exist anywhere (K2 UNVERIFIED #1) — read elapsedMs
curl -s -X POST $BR/kr_reconstruct_status "${H[@]}" -d '{}'
```

### Design decisions and spec deviations (each with its reason)

1. **`includeBytecode` is the flag name, `includeStatements` is accepted as an alias.** The mission
   named `includeBytecode`; K1 named `includeStatements`. Both are accepted so neither document is
   wrong at the wire; the response echoes `filter.includeBytecode`.
2. **Opcode histograms are keyed by HEX, not opcode name.** The only opcode-name table in the plugin
   is file-static inside the `MIF_KR_DEBUG` gate. Copying a ~100-case switch would be duplicating
   *logic*, which the ship-safety rule does not license (it licenses copying small *data*, such as an
   `FARFilter` shape). Nothing is lost: every statement's own `Inst` field carries the readable name,
   so the histogram counts and the statement stream reads.
3. **Bare-name asset resolution is EXACT, not substring.** The console resolver returns "the first
   asset whose name *contains* the needle". That is fine for a human watching a log and unacceptable
   for an agent acting on the answer, so an ambiguous bare name is an error listing the candidates.
4. **`kr_dump_blueprint` disassembles nothing by default**, and even with `includeBytecode` it
   disassembles only the functions on the returned page. Three brakes, because this runs on the game
   thread mid-frame: no-disassembly default, page-scoped disassembly, and a forced `limit` cap of 10
   when `includeBytecode` is set with no `functionFilter` (announced in `note` + `effectiveLimit` —
   an unannounced cap is a silent wrong answer about how much exists).
5. **`compile.measured` exists, and copy mode sets it false.** `CreateEditableBlueprintCopy` compiles
   internally and this endpoint does not own that `FCompilerResultsLog`. Reporting `errors:0` there
   would be a fabricated number, so the field says nothing measured it and points at `validate` on
   the returned `blueprintId`. Function mode owns its compile and reports real counts.
6. **`kr_dump_blueprint`'s `events[]` is the cheap `FUNC_Event` classification, not the authoritative
   one.** Proving a thunk really enters the ubergraph requires disassembling it — that is
   `kr_list_events`. Both numbers are reported so a disagreement is visible rather than hidden.
7. **No `open` parameter on `kr_reconstruct_request`** (K1 proposed one, defaulted false). An
   unattended agent must not be made to open editor tabs; the key is rejected with that reason.
8. **`kr_reconstruct_request` re-resolves the source class by PATH inside the deferred tick** rather
   than capturing a `UObject*` across it — holding an un-rooted raw pointer across a tick boundary is
   exactly the lifetime bug the pipeline's own `FGCScopeGuard` exists to prevent.
9. **Progress counters are fed by the two delegate lambdas the module already binds**, guarded to
   no-op unless a job is `Running`. Pressing F3 by hand, or running a console command, therefore
   moves nothing. Function mode calls the pipeline directly (not through the delegate), so its
   tallies are set explicitly and cannot double-count.

### Ship-safety gate — CLEAN

```
grep -n "MifKr_ResolveBPGC\|MifKr_RegistryMatches\|MifKr_CoerceToBPGC\|MifKr_FindBPGCByName\|
         MifKr_AnalyzeUbergraph\|MifKr_DumpBP\|MifKr_OpcodeName" \
     MifKrBridgeEndpoints.cpp MifKrJobManager.cpp MifKrJobManager.h | grep -v "^[0-9]*://"
  -> ZERO hits
grep -n "#if MIF_KR_DEBUG" MifKrBridgeEndpoints.cpp MifKrJobManager.{h,cpp}
  -> ZERO hits (every MIF_KR_DEBUG occurrence is prose in a comment explaining why nothing is gated)
```

No handler calls a debug-gated or file-static symbol from another TU. The resolution and enumeration
logic the handlers need is reimplemented here, ungated, exactly as phase 1 established.

**Batch R phase 2 verdict: source complete, UNBUILT, live proof pending.** Expected on the next
successful build: `self_audit.endpointCount` **176 → 183** (175 built-ins + 8 externals),
`externalEndpointCount` **1 → 8**, `externalProviders` still a single row
`{provider:"MifKismetReconstructor", endpointCount:8}`. `MIF_DECL` / `MIF_BIND` stay at **175 each** —
externals are structurally absent from that invariant, which is the point of the design.
`@mcp.tool()` count **175 → 183**.

---

## Wave 3 step 1 — KISMET_API RunTransientBlueprintReconstruct (ENGINE FORK)

_Implemented 2026-07-26 per `work/K_WAVE3_PLAN.md` §2.2 / §2.4 / §2.5. **Engine fork only — NOT built.**
No plugin file was touched (`MifKismetReconstructor` was being edited concurrently by another agent).
This is step 1 of 2: the engine export. The four plugin-side endpoints are step 2._

### The export (exactly one new symbol)

```cpp
KISMET_API bool RunTransientBlueprintReconstruct(
	UBlueprintGeneratedClass* SourceBPGC,
	UClass*                   ParentClass,
	bool                      bAsChild,
	FUncookedCopyStats&       OutStats,
	FCompilerResultsLog&      OutResults,
	TFunctionRef<void(UBlueprint* /*ReconBP*/)> OnCompiled);
```

Returns `true` iff `OnCompiled` ran. It is the RAW mint→populate→compile primitive, deliberately **not**
a composed "verify".

### Files changed (exhaustive — 2 files, `D:/UE532`, branch `BrandoCookedEditor-UE5.3.2`)

| # | File | Lines | Change |
|---|---|---|---|
| 1 | `Engine/Source/Editor/Kismet/Public/CompiledBlueprintReconstructor.h` (113 → 188 lines) | `:10` | `#include "Templates/Function.h"` for `TFunctionRef` |
| | | `:17` | `class UClass;` forward decl (first use of `UClass` in this header) |
| | | `:18-22` | `class FCompilerResultsLog;` forward decl + the 4-line comment explaining why it must never be `#include`d |
| | | `:121-147` | `FUncookedCopyStats` moved here from the `.cpp`, at global scope, **byte-identical** (verified by `diff` against `git show HEAD:` with one tab of de-indentation stripped) + a 6-line preamble on why it carries no API macro |
| | | `:149-188` | the `KISMET_API` declaration and its modkit-house-style contract comment |
| 2 | `Engine/Source/Editor/Kismet/Private/CompiledBlueprintCopyAction.cpp` (1752 → 1779 lines) | `:529-531` | the file-local `FUncookedCopyStats` (was `:529-548`, inside `namespace CompiledBlueprintCopyAction`) deleted, replaced by a 3-line breadcrumb pointing at the header |
| | | `:1526-1568` | the new definition, at global scope immediately after the `namespace CompiledBlueprintCopyAction` closing brace (`:1524`), beside its persistent twin `CreateAndSaveEditableCopy` |

Nothing else in `D:/UE532` was touched. No `.Build.cs`, no config, no third file, no plugin file.

`RunReconstructOnce` (`:1073-1108`), `PopulateUncookedCopy`, `CopyFunctionStubs` and every other helper
**stay `static`**. The new function is the only externally visible entry into that pipeline, so no caller
can enter it half-way and skip the skeleton refresh or the mint's GC rooting.

### Why ONE export suffices for all four Wave-3 endpoints

All four need the identical primitive — *mint a copy into the transient package, run the shared
`PopulateUncookedCopy` pipeline, compile it, look at the result* — and nothing more:

| Endpoint | What it adds on top | Extra engine surface |
|---|---|---|
| `kr_verify_fidelity` | calls the already-exported `GetBlueprintFidelityVerifier()` inside `OnCompiled` | **none** |
| `kr_classify_drift` | the same, plus a **plugin-side** per-function verdict sink in `MifFidelityVerifier.cpp` | **none** — the engine delegate is NOT widened (`CompiledBlueprintReconstructor.h:54-56` refuses arity changes) |
| `kr_drift_census` | the same ×N, plus `mif.kr.DriftCensus` forced on via `IConsoleVariable::Set` | **none** — that CVar is plugin-side |
| `kr_batch_reconstruct` | the same ×N with a **no-op** `OnCompiled`, plus tally + CSV | **none** — CSV is `IFileManager`, `CollectGarbage` is `COREUOBJECT_API` |

The shape is what makes one enough. Baking the fidelity verifier into the export (K2's proposed
`RunHeadlessFidelityVerify`) would have forced a **second** export for `kr_batch_reconstruct`, which
sweeps with verification OFF. Returning a rooted `UBlueprint*` instead of taking a callback (K1's
`RunThrowawayReconstruct`) would have been 2 exports *and* would have handed teardown to the caller.
`FUncookedCopyStats::AttemptedFunctions` — the fidelity denominator — needed no export of its own either:
it is reproducible plugin-side by widening the lambda already bound to
`FOnReconstructBlueprintFunctionGraph`, because each `AttemptedFunctions.Add` is paired 1:1 with an
invocation of that delegate (plan §1.1).

The teardown is **inside the engine function, not the caller's job**: `RemoveFromRoot()` +
`SetFlags(RF_Transient)` run unconditionally after `OnCompiled` returns. `RunReconstructOnce` roots the
copy across the compile (a raw local is not a GC root and `CompileBlueprint` can collect), and its own
contract hands that `RemoveFromRoot` to its caller. Across a **module boundary** that would mean one
forgotten line permanently roots a transient Blueprint per call — a leak a 1000+ iteration census
multiplies by the whole corpus. The `UBlueprint*` is therefore valid **only inside `OnCompiled`**; this is
stated in the header comment.

### Why `CreateEditableBlueprintCopy` could not be reused

It is a persistent asset factory by construction. Its implementation (`CreateAndSaveEditableCopy`)
unconditionally calls `FAssetRegistryModule::AssetCreated`, `UPackage::MarkPackageDirty` and
`UPackage::SavePackage` — one `.uasset` write, one registry add and one delete per call, which
disqualifies it for a census. It also compiles with **neither** an `FCompilerResultsLog` **nor**
`bSilentMode`, so compile errors are unreportable and every failure spams the Message Log. The new
function saves nothing, registers nothing, opens no editor tab and no modal, and collects no garbage
(GC cadence stays the caller's, between Blueprints — never inside one, while raw `UFunction*` are held).

### ABI safety

**Purely additive.**

- Adding an export **appends** to Kismet's export table. Every existing symbol keeps its name and
  signature; an already-built dependent DLL that never references `RunTransientBlueprintReconstruct`
  loads and resolves exactly as before. No existing signature, struct layout, vtable or enum is modified.
- **`FUncookedCopyStats` adds ZERO export-table entries.** It has no virtuals and no out-of-line member
  functions, so it emits nothing — identical to its neighbour `FBlueprintFidelityReport`, which has been
  crossing this DLL boundary macro-free since the fidelity delegate landed. Honest count of new exported
  symbols: **1, not 2.**
- Moving it from a `.cpp` to a `.h` changes layout for nobody: it had internal linkage, so no other TU in
  the process has ever had a definition of it to disagree with. Its only non-trivial member is a
  `TArray<UFunction*>` on UE's global allocator (`FMemory::Malloc/Free`, exported by Core) — the same
  mechanism by which `FBlueprintFidelityReport`'s three `FString`s already cross this boundary.
- The header contains no `UCLASS`/`USTRUCT`/`UENUM`/`UFUNCTION` and no `.generated.h`, so **UHT does not
  run** and there is no reflection data to version-mismatch.
- `FCompilerResultsLog` is **forward-declared, never included**: it lives in
  `Kismet2/CompilerResultsLog.h` in **UnrealEd**, and UnrealEd is a `PrivateDependencyModuleNames` entry
  of Kismet (`Engine/Source/Editor/Kismet/Kismet.Build.cs:38`; the module declares no
  `PublicDependencyModuleNames` at all). Private deps do not propagate include paths to dependents, so
  including that header from a Kismet **public** header would break the build of every consumer. Same
  defect class the Wave-1 planner caught with `Dom/JsonObject.h`. Callers include it themselves —
  `KR/Private/MifKrBridgeEndpoints.cpp` already does.
- Build cost when it is eventually built: 1 TU recompile + ~77 direct-dependent relinks, cascade stops
  one level deep. **Do not hand-copy a single new `UnrealEditor-Kismet.dll` onto an otherwise-stale
  binary set** — the plugin DLLs must be rebuilt against the new `.lib` in the same pass.

### Rollback

The engine repo has **4 unrelated, pre-existing modifications that must NOT be disturbed** — they predate
all audit work and none of them overlaps either Wave-3 file:

```
 M Engine/Build/BatchFiles/BuildUAT.bat
 M Engine/Build/BatchFiles/BuildUBT.bat
?? .github/
?? Engine/Config/DefaultEngine.ini
```

Roll back with a checkout of **exactly these two files** — never `git checkout -- .`, never `git add -A`:

```bash
cd /d/UE532
git status --porcelain                       # must show ONLY the 4 pre-existing entries + these 2
git diff --stat Engine/Source/Editor/Kismet/ # must be exactly 2 files
git checkout -- Engine/Source/Editor/Kismet/Public/CompiledBlueprintReconstructor.h \
                Engine/Source/Editor/Kismet/Private/CompiledBlueprintCopyAction.cpp
```

Then rebuild Kismet + dependents (a revert costs the same ~15-40 min as the change). If this is ever
committed, commit the two paths explicitly and on their own, so the four pre-existing modifications
cannot ride along.

### Deviations from the plan (and why)

1. **`VerifyFidelityCmd` was NOT refactored to call the new export.** The plan's §2.5 specifies replacing
   `:1421-1424` and `:1468-1469` with one call to the export. It was left alone, deliberately:
   (a) the plan's own batch order makes that change conditional on a regression gate — *"`mif.kr.VerifyFidelity`
   must print identical output to before the refactor"* — and this session was explicitly forbidden to
   build, so that gate cannot be run; (b) the plan's replacement snippet is not in fact behaviour-preserving:
   it collapses the mint-failure path (`"could not mint a copy of %s"`, `:1424`) into the compile-failure
   message (`"FAILED TO COMPILE (%d errors)"`), so a mint failure would newly log `"FAILED TO COMPILE
   (0 errors)"`. Consequence of leaving it: the ~6-line root/compile/unroot sequence now exists in two
   places. **The pipeline itself is NOT forked** — both paths still go through the single file-static
   `RunReconstructOnce`, which is what the "do not fork the pipeline" guarantee protects. Recommended as a
   follow-up in the same session that first builds the engine, so the regression gate can actually run.
2. **`#include "Templates/Function.h"` was added, but the plan's stated reason is wrong for this engine
   version.** The plan says `CoreMinimal.h` does not pull `TFunctionRef` in; it does, unconditionally, at
   `Engine/Source/Runtime/Core/Public/CoreMinimal.h:85`. The include was added anyway — it is IWYU-correct,
   it costs nothing, and relying on a transitive include for a type in a public signature is exactly the
   kind of coupling that breaks on an engine upgrade. The comment on that line records the correction.
3. **The duplicate `#include "CompiledBlueprintReconstructor.h"` at `.cpp:34` and `:50` was left in place**
   (the plan called collapsing it "cosmetic and optional"). Harmless — the header is `#pragma once`.

Nothing is guarded behind `MIF_KR_DEBUG` or any other debug flag: this is engine code that must exist in
every configuration.

**Wave 3 step 1 verdict: COMPLETE, NOT BUILT.** Step 2 (the four plugin-side endpoints in
`MifKismetReconstructor`) is a separate change and was not started here.

### Wave 1 (8 kr_* endpoints) + Wave 3 step 1 — BUILD PASS, ALL PROVEN (2026-07-26 ~16:10 ET)

Three build attempts. Both failures were the SAME two lines, and the root cause was only found by
reading the declaration — the first "fix" treated the symptom:
`UClass` **deliberately re-declares `IsA` as private** (Class.h:3488-3495, verbatim: *"This signature
intentionally hides the method declared in UObjectBaseUtility to make it private. Call IsChildOf
instead; Hidden because calling IsA on a class almost always indicates an error"*). So NO form of
`IsA` compiles on a `UClass*` — the first attempt's `IsA(T::StaticClass())` failed identically
(C2248 instead of C2275). Correct answer is `Cast<UBlueprintGeneratedClass>(X) != nullptr`, and
deliberately NOT the engine comment's suggested `IsChildOf`, which asks a different question
(does the class DERIVE from X, vs is the class OBJECT itself a BPGC).
**Rule for the postmortem log: a compile error explained by a plausible language rule is still a
guess until the declaration is read.**

**Live state**: endpointCount **183** (175 built-in + 8 external), policyContradictions **[]**,
externalProviders `[{MifKismetReconstructor, 8}]`. Buckets: 7 read-only + kr_reconstruct_request
self-managed, all as specified.

**Proofs (real cooked DDS2 assets):**
- `kr_disassemble_function` on `RaidAreaSphere_C::ExecuteUbergraph` → **94 statements** as
  structured JSON with resolved call targets (`Context` → `GlobalGetersLib.Default__GlobalGetersLib_C`
  → `LocalVirtualFunction GetDDSGameMode`), typed locals, object constants. **This closes
  02_GOTCHAS.md §3**, which still routes agents to `run_console` for exactly this.
- `kr_dump_blueprint` → parent chain, 6 functions with bytecode sizes + flag names, 5 properties,
  2 events, counts block, paging (offset/limit/truncated). Works on LOOSE assets too
  (`/Engine/EngineSky/BP_Sky_Sphere` → cooked:false), so the endpoint is not cooked-only.
- `kr_list_events` → recovered entry offsets and **frame-parameter maps**
  (`ReceiveTick` → frameProperty `K2Node_Event_DeltaSeconds` → eventPin `DeltaSeconds`).
- `kr_pin_type_from_property` on `BP_OponentPatrolRoute_C::PatrolDirections` → `TMap` resolved to
  `bridgeType:"object:/Script/Engine.Pawn"`, `bridgeContainer:"map"`, `bridgeValueType:"bool"`,
  plus a ready-to-paste `addVariableExample`. This is the composability win: cooked property →
  the exact grammar `add_variable`/`add_pin`/`set_pin_type` accept.
- **kr_reconstruct_request/status end-to-end**: job `krjob-1` deferred one tick, ran atomically,
  state `done` on the first poll, produced `/Game/Mif/RaidAreaSphere_Child` — and
  `list_graphs` on it reports **EventGraph with 68 nodes** + UserConstructionScript with 2.
  Cooked, graph-stripped source → editable copy with decompiled logic, over HTTP.
- House rules hold on every new endpoint: unknown key refused with the accepted-key list; missing
  mandatory param names it and names the endpoint that lists candidates; a wrong asset path returns
  the `.<Name>_C` convention plus "list candidates with kr_list_cooked_blueprints".

**Not exercised**: the busy-slot refusal. By design the job is atomic within a single tick and the
HTTP listener is a game-thread ticker, so a second request cannot physically arrive mid-job — the
refusal path is unreachable over HTTP and is a guard against future non-atomic job kinds.

**Wave 3 step 1 status**: the engine export `RunTransientBlueprintReconstruct` is built and linked
but **has no caller yet** — the four verify-family endpoints (kr_verify_fidelity, kr_drift_census,
kr_classify_drift, kr_batch_reconstruct) are Wave 3 step 2 and are NOT implemented. The export is
inert and harmless in this state; it is additive and changes no existing behaviour.

**Verdict: COMPLETE — live count 176 → 183. MifKismetReconstructor is reachable over HTTP.**
