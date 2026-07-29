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

---

## Batch E — DataTable FText readability (user-reported)

**Report (verbatim, from a collaborator):** *"For the DataTable stuff, it was double wrapping the
inputs, like the descriptions on my DataTable after it did edits included the NSLOC shit or whatever
it is. Anyway I did a edit on my side to fix it, I'll review the code when I get home to see if it
was a actual bug or just the AI using it wrong."*

**No endpoints added.** MIF_DECL = MIF_BIND = **175** before and after; server.py tools **183**
before and after (175 built-in + 8 external `kr_*`). This batch is parameters, response fields and
docs only.

### Diagnosis (verified live + against engine source)

1. **READ path.** `H_read_datatable` / `H_get_datatable_row` called `Table->GetTableAsJSON()` with
   DEFAULT flags. `UDataTable::GetTableAsJSON(const EDataTableExportFlags InDTExportFlags =
   EDataTableExportFlags::None)` — `Engine/Classes/Engine/DataTable.h:328`. With `None`, every
   `FText` exports via `ExportText_Direct`, i.e. the full lossless form
   `NSLOCTEXT("ns","key","source")`. The readable branch is gated on
   `EDataTableExportFlags::UseSimpleText` — `DataTableUtils.cpp:213-219`, flag documented at
   `DataTableUtils.h:21` as *"Export text properties as their display string, rather than their
   complex lossless form"*.
2. **WRITE merge mode** (`replace:false`, the default) uses
   `FJsonObjectConverter::JsonObjectToUStruct`, which parses the NSLOCTEXT export form correctly.
   Round-trip safe. Verified live.
3. **WRITE replace mode** (`replace:true`) uses `Table->CreateTableFromJSONString` →
   `DataTableUtils::AssignStringToProperty` (`DataTableJSON.cpp:753/772`). A **plain** string
   assigned to an `FText` there gets a **generated** namespace (`"<TableName> [<guid>]"`) and key
   (`"<RowName>_<ColumnName>"`). So plain text written by replace becomes a *localized* `FText`,
   which then READS BACK as `NSLOCTEXT(...)`. Verified live: a field holding
   `"Plain description here"` came back as
   `NSLOCTEXT("TestDT_Roundtrip [4819EC...]", "DOLAR_CurrencyDescription", "Plain description here")`.
4. **It wraps ONCE and is stable.** Cycles 2 and 3 were byte-identical, which proves the stored
   `FText` is genuinely localized rather than being re-wrapped each pass. The display string is
   still correct and **the data is not corrupted**. What the user saw is the bridge's *read output*,
   not damaged content — so the collaborator's local edit was treating a presentation problem as a
   data problem.

**Therefore the real defects are (a)** the read format is hostile and misleading, and **(b)** merge
and replace have DIFFERENT `FText` semantics one flag apart, undocumented. Both are now addressed.

### Fix

`Source/MifBridge/Private/MifBridgeDataTables.cpp`

- New file-local helpers in the anonymous namespace: `ResolveTextFormat`, `ContainsNsLocText`
  (value + array overloads), `RowStructHasTextProperty`, plus the `kTextNote` / `kReplaceTextNote` /
  `kTextFormatAccepted` strings. New includes: `DataTableUtils.h` (the flag enum),
  `UObject/UnrealType.h` (`TFieldIterator<FTextProperty>`).
- **`textFormat` on `read_datatable` and `get_datatable_row`.** Values `export` (DEFAULT — current
  lossless NSLOCTEXT behaviour, round-trip safe) and `simple` (passes
  `EDataTableExportFlags::UseSimpleText` to `GetTableAsJSON`). Alias spellings `textMode` and
  `simpleText:true`. Values are matched case-insensitively after trim; an unrecognised **value** is
  an error naming the accepted set (house rule — never a silent default). `textFormat` and
  `simpleText` both present but disagreeing is also an error rather than a silent winner. The
  effective value is echoed back as `textFormat`, in every branch.
- **`textNote`** is added only when the mode is `export` AND the **emitted** JSON actually contains
  `NSLOCTEXT(`. `read_datatable` scans post-truncation rows; `get_datatable_row` scans only the row
  it returns. Clean tables and clean rows stay quiet.
- **`textLocalizationNote`** on `write_datatable_rows` after a **successful** replace, gated on
  `RowStructHasTextProperty(Table->GetRowStruct())` (`TFieldIterator<FTextProperty>`). It states the
  generated namespace/key convention, that those fields will read back as `NSLOCTEXT(...)` in export
  mode, and that merge mode does not do this. This is the undocumented asymmetry that produced the
  report.
- **`RejectUnknownParams`** added to all three handlers (none had it):
  - `read_datatable` — `path, maxRows, textFormat, textMode, simpleText, op`
  - `get_datatable_row` — `path, rowName, textFormat, textMode, simpleText, op`
  - `write_datatable_rows` — `path, rows, replace, confirm, op`

  `op` is in every list **deliberately**: `H_batch` dispatches by passing the whole op object
  straight to the handler (`MifBridgeNodes.cpp:1278`), so a guard without `op` would reject every
  batched call. This is a latent collision in the existing guarded endpoints too — see the note
  below.
- The `textFormat` parse runs **before** `LoadDataTable`, so a bad value never loads an asset.

`tools/ue5-mcp-bridge/server.py` — `read_datatable` and `get_datatable_row` gain
`text_format: str = "export"` (forwarded as `textFormat=text_format or None`, so an empty string
means "omitted" per the `list_datatables` precedent); `write_datatable_rows` keeps its signature and
documents the merge-vs-replace `FText` asymmetry in its docstring. All defs remain above the
`if __name__` guard (verified: `ast.parse` clean, last top-level def line 1555, guard line 1565,
zero defs below it).

`docs/02_GOTCHAS.md` — new **§5e "FText in DataTables — NSLOCTEXT in reads is not corruption"**
between §5d and §6, citing `DataTableUtils.cpp:213` and `DataTableUtils.h:21`, with the `textFormat`
table, the merge-vs-replace table, and the rule *prefer merge (`replace:false`) unless you intend a
full-table overwrite*. One cross-reference bullet added to §7 ("Behaviours that are not bugs"),
which is the list a reader scans for exactly this symptom.

### Live proof to run after the next build

The editor was running during this batch, so nothing here is built yet. Run these against a
DataTable whose row struct has an `FText` column (`TestDT_Roundtrip` was used for the diagnosis):

1. **Simple mode returns the plain display string** —
   `read_datatable {path:"<DT>", textFormat:"simple"}` → the `FText` column reads
   `"Plain description here"`, response carries `textFormat:"simple"` and **no** `textNote`.
2. **Default read is unchanged and now self-explaining** —
   `read_datatable {path:"<DT>"}` → same column reads `NSLOCTEXT("<DT> [guid]", "<Row>_<Col>",
   "Plain description here")`, response carries `textFormat:"export"` **and** `textNote`.
3. **Aliases agree** — `read_datatable {path:"<DT>", textMode:"simple"}` and
   `read_datatable {path:"<DT>", simpleText:true}` must both equal call 1 byte-for-byte.
4. **Bad value is refused** — `read_datatable {path:"<DT>", textFormat:"plain"}` → `ok:false`,
   error naming `export` and `simple`. **Not** a silent default.
5. **Conflict is refused** — `read_datatable {path:"<DT>", textFormat:"export", simpleText:true}`
   → `ok:false`, "conflicting text format".
6. **Unknown key is refused** — `read_datatable {path:"<DT>", textFormatt:"simple"}` → `ok:false`
   listing the accepted keys.
7. **Row endpoint matches** — `get_datatable_row {path:"<DT>", rowName:"<Row>", textFormat:"simple"}`
   vs the default call: same two outcomes as 1 and 2, `textNote` present only in export mode.
8. **Replace warns** — `write_datatable_rows {path:"<DT>", rows:[...], replace:true, confirm:true}`
   on a row struct with an `FText` → `replaced:true` **and** `textLocalizationNote`. The same call
   against a table whose row struct has no `FText` must **not** carry it.
9. **Merge still round-trips** — feed step 2's `NSLOCTEXT(...)` string straight back through
   `write_datatable_rows {replace:false, confirm:true}` and re-read: byte-identical, and **no**
   `textLocalizationNote` (merge never emits it).
10. **Batch still works** — `batch {ops:[{op:"read_datatable", path:"<DT>"}]}` must succeed. If this
    fails with "unrecognised parameter 'op'", the guard lists are wrong.

### Finding worth a separate fix: `op` vs `RejectUnknownParams`

Every previously guarded endpoint (`MifBridgeAuthoring.cpp`, `MifBridgeCooked.cpp`,
`MifBridgeMaterials.cpp`, `MifBridgeUndo.cpp` — 20 call sites) omits `op` from its accepted-key
list, while `H_batch` hands the handler the op object verbatim including its `op` key
(`MifBridgeNodes.cpp:1278`). Any of those endpoints invoked **inside `batch`** therefore fails with
"unrecognised parameter 'op'". Batch E works around it locally by accepting `op`; the systematic fix
is to strip `op` in `H_batch` before dispatch, or to make `RejectUnknownParams` always tolerate it.
Not done here — out of scope for a user-reported DataTable bug, and it touches the batch dispatcher.

**Verdict: COMPLETE (source only, unbuilt). Endpoint counts unchanged — MIF_DECL 175, MIF_BIND 175,
MCP tools 183.**

### Batch E + `op` regression — BUILD PASS, ALL PROVEN (2026-07-27 ~08:00 ET)

**Live: 183 endpoints, 8 external, 0 contradictions.**

**Regression I introduced this session and fixed here.** The unknown-parameter guards added in
Batches B/C/D reject any key not on their accepted list — but `H_batch` passes each op object to the
handler VERBATIM, `op` field included (MifBridgeNodes.cpp:1277). So ~20 guarded endpoints had begun
failing with "unrecognised parameter 'op'" the moment they were called inside `batch`: the
strictness fix silently broke composition. Fixed centrally in `RejectUnknownParams`
(MifBridgeCommon.cpp) by always tolerating `op`, with the dispatcher line cited, so no call site has
to remember it. **Proven**: `batch {ops:[find_assets, get_datatable_row]}` now returns ok for both.
Worth noting how it escaped — every proof this session exercised endpoints STANDALONE. Composition
had no test. That is the gap to close next.

**Batch E — DataTable FText readability (reported by Brando).** Diagnosis: not corruption.
`GetTableAsJSON()` defaults to `EDataTableExportFlags::None`, which the engine documents as the
"complex lossless form" (DataTableUtils.h:21, branch at DataTableUtils.cpp:213), so every FText
reads back as `NSLOCTEXT("ns","key","source")`. Merge-mode writes parse that correctly (round-trip
verified byte-identical); `replace:true` goes through a different importer
(DataTableUtils::AssignStringToProperty via DataTableJSON.cpp:753/772) which gives a PLAIN string a
generated localization id — so plain text becomes localized and thereafter reads as NSLOCTEXT.
It wraps ONCE and is stable across further cycles (verified 3 cycles, byte-identical), which proves
the stored FText is properly localized and its display string intact.
Fix, all proven live:
- `textFormat: "export" (default) | "simple"` on `read_datatable`/`get_datatable_row`
  (aliases `textMode`, `simpleText`). Proven: simple → `"CurrencyName":"Dolary"`.
- `textNote` attached only when NSLOCTEXT is actually present, explaining it is the lossless form,
  not corruption, and that merge mode accepts it verbatim.
- An unknown VALUE errors naming the accepted set (never a silent default). Proven.
- `textLocalizationNote` on successful `replace` when the row struct has any FTextProperty,
  documenting the merge/replace asymmetry. Proven.

**Operational finding — a modal dialog takes the whole bridge down.** After this build the editor
launched but every call returned nothing; the process was alive with
`MainWindowTitle: "BA Welcome Screen"` (BlueprintAssist's launch popup). Because
`FHttpServerModule` is a GAME-THREAD ticker, ANY modal window stops the bridge answering — the
symptom looks like a crashed bridge but is a blocked game thread, and any plugin can cause it.
Suppressed durably via `bShowWelcomeScreenOnLaunch=False` under
`[/Script/BlueprintAssist.BASettings_EditorFeatures]` in
`Saved/Config/WindowsEditor/EditorPerProjectUserSettings.ini` (gitignored; not part of any commit).
This is a live instance of the hazard class 03_GAPS_AND_RISKS.md §2 catalogues for OUR endpoints —
the same rule applies to third-party plugins we do not control.

**Stale doc flagged, not fixed**: 00_ARCHITECTURE.md still says server.py lives in a separate repo
(`GitHub/Eddie_v2/tools/ue5-mcp-bridge/`). That path does not exist; there is exactly one server.py,
inside this plugin. Its "known sync hazard" section and 82-of-102 endpoint figures are obsolete.


## Batch F - set_property array writes + get_property type fidelity

Two user-reported defects in the generic reflection endpoints. **GAP 1 was a silent no-op that was
actually a silent WIPE** — `ok:true` on four CDO array writes that wrote nothing (and would have
cleared them had they held anything). **GAP 3** is round-trip hostility in the read path: bools come
back as the strings `"True"`/`"False"`, arrays as one export-text blob.

File owned and changed this batch: `Source/MifBridge/Private/MifBridgeNodes5.cpp` only.
`get_property` / `list_object_properties` live in `MifBridgeNodes6.cpp`, which this batch does not
own — see **"What is NOT done"** at the end. `docs/00_ARCHITECTURE.md`'s source-layout table still
claims Nodes5 owns "get/set"; it owns **set** only. That row is stale and should be corrected by
whoever next edits that file.

### Diagnosis - verified against UE 5.3 source at `D:/UE532`, not inferred

The user report was *"set_property silently no-ops on array properties … got ok:true on all four and
nothing was written"*. The mechanism is worse than a no-op and it is not specific to arrays.

**1. `value` was read with `JStr`.**

```cpp
FString JStr(const TSharedRef<FJsonObject>& In, const TCHAR* Field, const FString& Default)
{
    FString Value;
    return In->TryGetStringField(Field, Value) ? Value : Default;   // MifBridgeCommon.cpp:576
}
```

`TryGetStringField` fails for **every** JSON value that is not a string, so `value:["A","B","C"]`
became `""`. The handler then fed `""` to `ImportText_Direct`.

**2. An empty buffer is not a failure for `FArrayProperty` — it is an instruction to EMPTY the
array, reported as success.**

```cpp
// Engine/Source/Runtime/CoreUObject/Private/UObject/PropertyArray.cpp:612-621
// If we export an empty array we export an empty string, so ensure that if we're passed an empty
// string we interpret it as an empty array.
if (*Buffer == TCHAR('\0') || *Buffer == TCHAR(')') || *Buffer == TCHAR(','))
{
    if (ArrayHelper) { ArrayHelper->EmptyValues(); }
    return Buffer;          // <-- NON-NULL == success
}
```

`H_set_property` treats a non-null return as applied, so the response said `applied:true`. The four
CDO arrays were empty before and empty after; had they been populated the call would have **deleted
their contents** and still reported success. This is a data-loss path, not just a no-op.

**3. The same shape exists for floats, so it was never an array-only bug.**
`FNumericProperty::ImportText_Internal` (PropertyNumeric.cpp:125-137) has a
`if (Start == Buffer) return NULL;` guard on the **integer** branch and **none** on the
floating-point branch. It falls through to `SetNumericPropertyValueFromString(ptr, Start)` with
`Start == ""` → `Atof("") == 0.0` → returns non-null. So `value: 0.5` sent as a **JSON number**
silently wrote **0.0** and reported success. Same class, different type, never reported.

For contrast, the types that already failed loudly on `""`:
`FBoolProperty` (PropertyBool.cpp:384-397, unmatched token → `NULL`), `FSetProperty`
(PropertySet.cpp:718) and `FMapProperty` (PropertyMap.cpp:807) — both require a leading `(` — and
integer numerics. That uneven behaviour is exactly why the bug read as "arrays are special".

**4. One more silent path found while fixing this.** `FObjectPropertyBase::ImportText_Internal`
computes `bOk` from `ParseObjectPropertyValue` and then **discards it**, returning the advanced
buffer regardless (PropertyBaseObject.cpp:388/422). An unresolvable asset path therefore imported
"successfully" as null. Now refused.

**PM-003 held throughout.** The scratch-buffer discipline (import into a copy seeded from the
current value, publish only on success) was already correct and is preserved — it is now a type,
`FScratchValue`, so every new import site inherits it instead of re-deriving it.

### Fix - GAP 1, `set_property` accepts JSON containers

**Route (a) was taken in full: JSON arrays/sets/objects are converted, not refused.**

- `value` is now read as a **`TSharedPtr<FJsonValue>`**, not a string.
- **A JSON string short-circuits to the old code path byte-for-byte** (including bool
  normalisation). Every pre-Batch-F caller is untouched by construction — this is the compatibility
  guarantee, and it is a single `if (ValueJson->Type == EJson::String)` at the top so it cannot rot.
- Anything else goes through `JsonToPropertyText`, which emits the engine's export-text grammar:

  | Property | JSON accepted | Export text emitted |
  |---|---|---|
  | `FArrayProperty` | array | `(elem,elem,elem)` — `()` when empty |
  | `FSetProperty` | array | `(elem,elem)` |
  | `FMapProperty` | object | `((Key,Value),(Key,Value))` — PropertyMap.cpp:843-877 |
  | `FStructProperty` | object | `(Member=Value,…)` — partial literals leave other members alone |
  | `FBoolProperty` | bool / number / string | `True` / `False` |
  | integer numerics | whole number / string | decimal digits; a **fractional** number is REFUSED, never truncated |
  | float numerics | number / string | `FString::SanitizeFloat` (`%f`-based, String.cpp:1172) — never an exponent, which UE's float parser cannot read |
  | enums (`FEnumProperty`, `FByteProperty` with `Enum`) | string (entry name) / whole number | authored entry name, or the integer |
  | `FStr` / `FName` / `FText` | **string only** | quoted + escaped by the engine |
  | object / soft object / class | string path, or `null` | `Class'/Game/Path.Name'`, or `None` |

- **Quoting and escaping are not hand-rolled.** Every scalar element is built as *raw undelimited*
  text, handed to **that element property's own `ImportText_Direct`**, and then exported back with
  `ExportTextItem_Direct(..., PPF_Delimited)`. The engine produces its own quoting
  (`"%s"` + `ReplaceCharWithEscapedChar` for `FString`/`FName`, PropertyStr.cpp:59 /
  PropertyName.cpp:36; `Class'path'` for objects; authored names for enums), so an element
  containing `"`, `,`, `(`, `)`, a newline or a backslash round-trips correctly with no per-type
  quoting table to get wrong. It also **validates each element in place**: a bad enum name fails
  naming `Tags[2]`, not the whole string.
- **Route (b) is still there for everything that cannot be converted faithfully.** Every refusal
  names the location (`MyArray[3]`, `Slot.Anchors.Minimum.X`, `MyMap{key}`), the property type, and
  the accepted form — e.g. a JSON array aimed at an `FStructProperty` returns
  *"'Anchors' (FStructProperty FAnchors): cannot convert JSON array. Accepts a JSON object of
  Anchors members (e.g. {"X":1,"Y":2}) or UE export text as a string (X=1,Y=2)."*
  Refused shapes: fixed-size C-array members inside a struct literal, non-string JSON into
  `FStr`/`FName`/`FText`, fractional numbers into integer properties, JSON containers into
  delegates/interfaces, and nesting past 12 levels.
- Unknown **struct members** are refused naming the member and listing the struct's members,
  including the authored name for Blueprint user structs whose reflected names are mangled
  (`Speed_2_A1B2…`). Both spellings are accepted on input.

**There is now no path where a JSON container produces no write.** It is converted, or it is
refused with an error.

### Fix - the verify-the-write guard (mandatory, type-agnostic)

This is the part that matters more than the array conversion, because it closes the *class* of bug
rather than the instance.

**Mechanism: three exports of the same leaf through the same exporter, compared as strings.**

1. `valueBefore` — `ExportText_Direct` of the live leaf **before** the import.
2. `valueStaged` — `ExportText_Direct` of the **scratch buffer** after a successful parse. This is
   the canonical text of what is about to be written. Because it comes from the same exporter as
   `valueBefore`, the two are directly comparable — "did the caller ask for a change" becomes a
   string compare rather than a guess about how the caller spelled their input (`0.5` vs
   `0.500000`).
3. `valueAfter` — `ExportText_Direct` of the live leaf **after** publish **and after
   `PostEditChangeProperty` has run**. `PostEditChangeProperty` is itself a place a value can be
   silently rejected or clamped, so it must be inside what gets verified.

Then:

| Condition | Result |
|---|---|
| `valueStaged != valueBefore` and `valueAfter == valueBefore` | **`ok:false`** — "set_property did NOT write …" with all three strings in the response |
| `valueStaged != valueBefore` and `valueAfter == valueStaged` | `ok:true, applied:true, verified:true, changed:true` |
| `valueAfter != valueStaged` but `valueAfter != valueBefore` | `ok:true` + **`coerced:true`** + `valueStaged` — the write landed but something clamped/normalised it |
| `valueStaged == valueBefore` | `ok:true, changed:false` + `note` — a genuine idempotent write, reported as a number instead of a bare `applied:true` |

Containers additionally report `elementsBefore` / `elementsAfter` (via `FScriptArrayHelper` /
`FScriptSetHelper` / `FScriptMapHelper::Num()`), so "did it land" is answerable by comparing two
integers without parsing any export text.

Why comparing *staged vs before* rather than *input vs after*: the caller's input text is not
comparable to an export (`"0.5"` exports as `0.500000`; `["A"]` exports as `("A")`). Canonicalising
both sides through the property's own exporter is the only comparison that is correct for every
type. That is what makes the guard work for property kinds nobody has exercised yet.

Note: if the guard trips, the asset is left marked dirty holding its **original** value — nothing
was written, so there is nothing to roll back. The spurious dirty flag is the deliberate price of
verifying *after* the edit notification instead of before it.

`RejectUnknownParams` was also added — `set_property` had none. Accepted:
`objectPath, blueprintId, path, widgetName, propertyPath, value` (plus the batch dispatcher's `op`,
tolerated centrally in `RejectUnknownParams`). `actorPath`, `format` and `verify` carry explanatory
KeyNotes rather than a bare "unrecognised".

### Fix - GAP 3, typed JSON output

**Design decision: an additive `typed` field. No `format` parameter, no change to `value`.**

Justification:

1. `value` (export text) is the **round-trip-safe** form and callers already parse it. Changing it
   under them is the same mistake as the DataTable `FText` read format — the fix there was an
   explicit opt-in, not a silent reshaping (docs/02_GOTCHAS.md §5e).
2. A `format` parameter would need every caller updated to opt in, would double the response shapes
   to test, and would leave the dangerous default in place — a caller who never heard of the flag
   keeps reading `"False"` as truthy. An additive field fixes the default reader on the next
   deploy without a flag day.
3. **The presence of `typed` is itself the version signal** — no capability negotiation needed.
4. `typed` is exactly the shape `set_property` now accepts, so `get_property → set_property` becomes
   a closed loop with no string surgery.

`MifBridge::PropertyValueToTypedJson(const FProperty*, const void*, UObject*)` lives in
`MifBridgeNodes5.cpp` with **external linkage on purpose**, so the read endpoints share one
implementation instead of growing a second one that drifts. Mapping:

| Property | `typed` emits |
|---|---|
| `FBoolProperty` | JSON `true` / `false` — **not** `"True"` |
| numerics | JSON number |
| enums / byte-with-enum | JSON string (authored entry name) — a raw byte would be the same loss as `"True"` |
| `FStr` / `FName` / `FText` | JSON string (`FText` → display string; the lossless `NSLOCTEXT` form stays in `value`) |
| object / class | JSON string path; `null` only when the engine itself exports `None` (an **unloaded soft ref** reports its path, not null) |
| array / set | JSON array (sets iterate sparse storage with `IsValidIndex`) |
| map | JSON object, keys exported undelimited so an `FName` key reads `Foo` not `"Foo"` |
| struct | JSON object keyed by the **reflected** member name (the name the struct-literal writer emits) |
| C-array `UPROPERTY` (`int Foo[4]`) | JSON array of all `ArrayDim` elements, not element 0 |
| delegates / anything unmodelled | export-text string (never wrong) |

`set_property` already emits `typed` (the post-write readback), so the guard and the typed output
share one code path and one set of bugs.

**Consistency rule for `list_object_properties`** (to be applied by whoever owns Nodes6.cpp): emit
`typed` per property **only when `valueClipped` is false**, and set `typedOmitted:true` otherwise.
A typed value alongside a clipped string would be a lie, and emitting full typed values for all
~545 properties of `Ultra_Dynamic_Sky` reintroduces the response-size failure `maxValueChars`
exists to prevent.

### Round-trip argument (logical — nothing is built this session)

`get_property.typed → set_property.value` is lossless for the three reported cases:

- **Bools.** `typed` emits JSON `true`/`false`. The writer's `FBoolProperty` branch maps JSON
  `true`→`True`, `false`→`False`, which is what `FBoolProperty::ImportText_Internal` matches
  (PropertyBool.cpp:384-397). The old string form `"True"`/`"False"` still works through the
  unchanged string path, so both directions are covered.
- **Arrays.** `typed` emits a JSON array whose elements are themselves typed. The writer converts a
  JSON array to `(e,e,e)` with each element canonicalised **through the same property object** that
  produced it on the read side — so read-export and write-import are inverse operations by
  construction, not by matching format strings. Empty array → `()`, which
  PropertyArray.cpp:636-644 imports as an empty array.
- **Numbers.** Integers export as decimal digits and import through the integer branch, which
  accepts `[+-]` then digits. Floats go through `SanitizeFloat`, which is `%f`-based and therefore
  never emits the exponent form the float parser cannot read. Known bound, stated rather than
  hidden: `int64` beyond 2^53 is not exactly representable as a JSON number — which is precisely
  why `value` (export text) is kept alongside `typed` rather than replaced by it.
- **The guard closes the loop empirically.** Even where a type's round trip is imperfect, feeding
  `typed` back returns `changed:false` if it was already equal, or `ok:false` if the write did not
  land. It cannot return `ok:true` having done nothing.

Not claimed: `FText` (`typed` gives the display string; use `value`), fixed-size C-array members
inside struct literals (refused), and unresolvable object paths (refused).

### Response shape - `set_property`

Unchanged: `target`, `propertyPath`, `leafProperty`, `applied`, `recompiled` (widget path only).
New: `leafType`, `verified`, `changed`, `valueForm` (`"string"` | `"json"`), `importText` (the
export text actually handed to `ImportText_Direct` — this is what makes a JSON-array call
debuggable), `valueBefore`, `valueAfter`, `typed`, and conditionally `elementsBefore` /
`elementsAfter` / `coerced` / `valueStaged` / `note`.

### Live proof to run after the next build

The editor was running during this batch, so **nothing here is built**. `<BP>` is any Actor
Blueprint; `Tags` is `TArray<FName>` on `AActor`, so every actor BP has it. `Default__<Class>_C` is
the CDO route from docs/02_GOTCHAS.md §5d.

```bash
B=http://127.0.0.1:8791/api
H=(-H "X-Mif-Token: dev" -H "Content-Type: application/json")
CDO='/Game/BP/BP_Scooter.Default__BP_Scooter_C'      # <-- any Actor BP CDO

# F-1  THE REPORTED FAILING CASE: a CDO array written as a JSON list.
#      Before this batch: ok:true, array emptied, nothing reported.
curl -s -X POST $B/set_property "${H[@]}" \
  -d "{\"objectPath\":\"$CDO\",\"propertyPath\":\"Tags\",\"value\":[\"Alpha\",\"Beta\",\"Gamma\"]}"
# expect: ok:true, applied:true, verified:true, changed:true, valueForm:"json",
#         importText:"(\"Alpha\",\"Beta\",\"Gamma\")",
#         elementsBefore:0, elementsAfter:3,
#         typed:["Alpha","Beta","Gamma"]        <-- a JSON ARRAY, not one string
# THE NUMBER THAT PROVES THE BUG IS DEAD: elementsAfter == 3.

# F-2  Read it back and confirm the write is real (get_property still returns export text today;
#      it gains "typed" when Nodes6.cpp adopts PropertyValueToTypedJson — see "What is NOT done").
curl -s -X POST $B/get_property "${H[@]}" \
  -d "{\"objectPath\":\"$CDO\",\"propertyPath\":\"Tags\"}"
# expect: value:"(\"Alpha\",\"Beta\",\"Gamma\")"

# F-3  ROUND TRIP: feed F-1's typed array straight back. Must be a clean no-op, not a wipe.
curl -s -X POST $B/set_property "${H[@]}" \
  -d "{\"objectPath\":\"$CDO\",\"propertyPath\":\"Tags\",\"value\":[\"Alpha\",\"Beta\",\"Gamma\"]}"
# expect: ok:true, changed:FALSE, elementsAfter:3, note about the value already being set.
# If elementsAfter is 0 here, the wipe is back.

# F-4  Export text still works, byte-for-byte as before. Compatibility check.
curl -s -X POST $B/set_property "${H[@]}" \
  -d "{\"objectPath\":\"$CDO\",\"propertyPath\":\"Tags\",\"value\":\"(\\\"Solo\\\")\"}"
# expect: ok:true, valueForm:"string", elementsAfter:1

# F-5  Explicit empty must still be possible, and must be honest about it.
curl -s -X POST $B/set_property "${H[@]}" \
  -d "{\"objectPath\":\"$CDO\",\"propertyPath\":\"Tags\",\"value\":[]}"
# expect: ok:true, changed:true, elementsBefore:1, elementsAfter:0, importText:"()"

# F-6  Quoting/escaping is the ENGINE's, not ours: commas, quotes and backslashes survive.
curl -s -X POST $B/set_property "${H[@]}" \
  -d "{\"objectPath\":\"$CDO\",\"propertyPath\":\"Tags\",\"value\":[\"a,b\",\"say \\\"hi\\\"\",\"back\\\\slash\"]}"
# expect: ok:true, elementsAfter:3 (NOT 5 - the commas must not split into extra elements),
#         and typed must echo the three strings back unchanged.

# F-7  BOOL TYPE FIDELITY on the write side (JSON true, not the string "true").
curl -s -X POST $B/set_property "${H[@]}" \
  -d "{\"objectPath\":\"$CDO\",\"propertyPath\":\"bReplicates\",\"value\":true}"
# expect: ok:true, valueAfter:"True", typed:true   <-- JSON true, not "True"

# F-8  FLOAT AS A JSON NUMBER - the second silent-corruption path.
#      Before this batch this wrote 0.0 and reported success.
curl -s -X POST $B/set_property "${H[@]}" \
  -d "{\"objectPath\":\"$CDO\",\"propertyPath\":\"NetUpdateFrequency\",\"value\":37.5}"
# expect: ok:true, changed:true, valueAfter:"37.500000", typed:37.5   <-- NOT 0.0

# F-9  A struct as a JSON object (widget slot layout, docs/02_GOTCHAS.md §5d).
curl -s -X POST $B/set_property "${H[@]}" \
  -d '{"blueprintId":"/Game/UI/WBP_HUD.WBP_HUD","widgetName":"HealthBar",
       "propertyPath":"Slot.Anchors.Minimum","value":{"X":0.5,"Y":0.0}}'
# expect: ok:true, importText:"(X=0.500000,Y=0.000000)", typed:{"X":0.5,"Y":0.0}, recompiled:true

# F-10 REFUSAL, not a silent drop: JSON array into a struct property.
curl -s -X POST $B/set_property "${H[@]}" \
  -d '{"blueprintId":"/Game/UI/WBP_HUD.WBP_HUD","widgetName":"HealthBar",
       "propertyPath":"Slot.Anchors.Minimum","value":[0.5,0.0]}'
# expect: ok:FALSE, error names the property, says FStructProperty, and SHOWS (X=1,Y=2)

# F-11 REFUSAL: unknown struct member, with the member list.
curl -s -X POST $B/set_property "${H[@]}" \
  -d '{"blueprintId":"/Game/UI/WBP_HUD.WBP_HUD","widgetName":"HealthBar",
       "propertyPath":"Slot.Anchors.Minimum","value":{"Z":0.5}}'
# expect: ok:FALSE, "struct Vector2D has no member 'Z'. Members: X, Y"

# F-12 REFUSAL: fractional number into an integer property (no silent truncation).
curl -s -X POST $B/set_property "${H[@]}" \
  -d "{\"objectPath\":\"$CDO\",\"propertyPath\":\"NetPriority\",\"value\":2.7}"
# (any integer UPROPERTY on the CDO) expect: ok:FALSE naming the fractional number.

# F-13 REFUSAL: unknown parameter, listing the accepted keys.
curl -s -X POST $B/set_property "${H[@]}" \
  -d "{\"objectPath\":\"$CDO\",\"propertyPath\":\"Tags\",\"value\":[],\"overwrite\":true}"
# expect: ok:FALSE naming 'overwrite' AND listing objectPath/blueprintId/path/widgetName/
#         propertyPath/value.

# F-14 REFUSAL: an object path that does not resolve is no longer written as None.
curl -s -X POST $B/set_property "${H[@]}" \
  -d '{"objectPath":"/Game/BP/BP_Scooter.BP_Scooter_C:Mesh_GEN_VARIABLE",
       "propertyPath":"StaticMesh","value":"/Game/Nope/SM_DoesNotExist.SM_DoesNotExist"}'
# expect: ok:FALSE. Before this batch: ok:true with StaticMesh silently set to null.

# F-15 BATCH COMPOSITION - the new RejectUnknownParams must tolerate the dispatcher's "op" key.
curl -s -X POST $B/batch "${H[@]}" \
  -d "{\"ops\":[{\"op\":\"set_property\",\"objectPath\":\"$CDO\",\"propertyPath\":\"Tags\",\"value\":[\"Batched\"]}]}"
# expect: ok:true. If this fails with "unrecognised parameter 'op'", the guard list is wrong.
```

### What `server.py` needs (NOT touched here - a later agent owns it)

1. **`set_property(value: str = "")` must become `value: Any`.** This is the blocker: the MCP tool
   currently types `value` as `str`, so a JSON array cannot even reach the endpoint through the MCP
   path. The HTTP path already works. Keep passing it through unchanged — do **not** `json.dumps`
   it, and do **not** apply the `or None` idiom, because `value=[]`, `value=0`, `value=False` and
   `value=""` are all legitimate writes that `or None` would erase (that would recreate the exact
   bug this batch fixes, one layer up).
2. **Docstring**: state that `value` accepts UE export text as a string **or** typed JSON
   (array/object/number/bool/null), that the response now carries `changed`, `verified`,
   `elementsBefore`/`elementsAfter` and `typed`, and that **`applied:true` alone is no longer the
   success test — check `changed`**.
3. **`get_property` / `list_object_properties`**: no signature change for the additive `typed`
   field, but their docstrings should say `value` is the round-trip-safe export text and `typed` is
   the typed form (bools as booleans, arrays as lists) — and that `typed` appears only once
   Nodes6.cpp adopts it.
4. No new endpoints; `MIF_DECL` / `MIF_BIND` counts are unchanged.

### What is NOT done, and why

**`get_property` and `list_object_properties` were not changed.** This batch's brief scoped it to
`MifBridgeNodes5.cpp` ("generic reflection property get/set" — the wording copied from
00_ARCHITECTURE.md's stale source-layout table), but both read handlers actually live in
`MifBridgeNodes6.cpp`. A handler can only be defined once, so emitting `typed` from `get_property`
cannot be done from this file; editing Nodes6.cpp would have crossed an explicit file boundary in a
multi-agent batch.

The emitter is therefore already written, tested against the write path, and **externally linked**
so adopting it is three lines with no header change:

```cpp
// MifBridgeNodes6.cpp - near the top, inside namespace MifBridge
TSharedPtr<FJsonValue> PropertyValueToTypedJson(const FProperty* Prop, const void* ValueAddr, UObject* Owner);

// H_get_property, after the existing ExportText_Direct call:
Out->SetField(TEXT("typed"), PropertyValueToTypedJson(Leaf, LeafAddr, LeafOwner));

// H_list_object_properties, inside the property loop - gated, per the consistency rule above:
if (!bValueClipped) { PropJson->SetField(TEXT("typed"), PropertyValueToTypedJson(Prop, Prop->ContainerPtrToValuePtr<void>(Target), Target)); }
else                { PropJson->SetBoolField(TEXT("typedOmitted"), true); }
```

Promote the declaration into `MifBridgeHandlers.h` when that file is next edited, so the two files
stop relying on a local extern.

**Verdict: GAP 1 COMPLETE (source only, unbuilt). GAP 3 emitter COMPLETE and shared; its two read
call sites are pending an owner of MifBridgeNodes6.cpp. Endpoint counts unchanged.**

## Wave 3 step 2 — the verify family (4 endpoints, source only, UNBUILT)

_2026-07-28. Plugin side only: `MifKismetReconstructor/Private/MifKrBridgeEndpoints.cpp`. The engine
half (`RunTransientBlueprintReconstruct`, engine commit `7926ab12`) was already committed and built by
step 1; nothing under `MifBridge/` and nothing in `server.py` was touched. **No build was run and the
editor was not launched or restarted — the running DLL does not contain these endpoints yet.** Every
curl below is therefore a live proof **to run after the next build**, not a recorded result._

### What landed

| Endpoint | Bucket | Underlying call | Shape |
|---|---|---|---|
| `kr_verify_fidelity` | **SelfManaged** | `RunTransientBlueprintReconstruct(SourceBPGC, ParentClass=SourceBPGC, bAsChild=true, Stats, Results, cb)` → inside `cb`: `GetBlueprintFidelityVerifier().Execute(SourceBPGC, ReconBP, Stats.AttemptedFunctions, Report)` | request + poll, `kind:"verify"`, deferred ONE tick, atomic |
| `kr_classify_drift` | **SelfManaged** | same, plus one extra `Verifier.Execute(..., {OneFunc}, Single)` per attempted function | request + poll, `kind:"classify"`, deferred ONE tick, atomic |
| `kr_drift_census` | **SelfManaged** | same as verify, ×N, with `mif.kr.DriftCensus` forced to 1 for the job | request + poll, `kind:"census"`, **ONE BP PER TICK**, GC every 25 + once at the end |
| `kr_batch_reconstruct` | **SelfManaged** | same export with `bAsChild` from `mode`, verifier optional | request + poll, `kind:"batch"`, **ONE BP PER TICK**, same GC cadence |

All four are polled by the existing `kr_reconstruct_status` (ReadOnly) — Wave 3 adds **no** new status
endpoint. Endpoint count for this provider goes 8 → 12.

**Why SelfManaged and not ReadOnly**, per endpoint and not by convention: every one of them runs a full
`FKismetEditorUtilities::CompileBlueprint` on the throwaway copy (once, or once per slice). A full
compile inside a blanket undo transaction means reinstancing captured by an undo step — a dead CDO and
a crash. `SelfManaged` also makes them compile-heavy, which fences them out of `batch`'s single open
transaction for free. They persist nothing (no save, no `AssetCreated`, no editor tab), but calling
that "read-only" would be a lie about the compile.

### Design decisions worth recording

- **One export, four endpoints.** The engine call is the raw mint→populate→compile primitive; the
  verifier is the caller's business. That is what lets `kr_batch_reconstruct` (verify OFF by default)
  share the same export instead of needing a second one.
- **Nothing escapes the callback.** The engine unroots the copy and marks it `RF_Transient` the moment
  `OnCompiled` returns, and `Stats.AttemptedFunctions` holds raw cooked `UFunction*`. Every callback in
  this file copies out ints and `FString`s only. The sweep additionally holds only package/object
  **paths** across ticks and re-resolves per slice.
- **Per-function verdicts with ZERO change to `MifFidelityVerifier.cpp`.** `kr_classify_drift` re-runs
  the same bound verifier once per attempted function with a **one-element denominator**; the verifier's
  loop is independent per function, so a single-element call yields exactly that function's verdict in
  the report's own counters. This replaces the planned module-static capture sink — no new global, no
  arm/disarm lifecycle, no second copy of the verdict logic. It costs a second pass, which is what makes
  `result.consistent` (per-function rows vs the independent aggregate) a real cross-check.
- **The anim trap is refused, not silently degraded.** `IsCompiledBlueprintAsset` uses exact class-path
  equality and excludes `UAnimBlueprintGeneratedClass`, and `GetBlueprintClassTypesForSource` special-cases
  only Widget — so an anim source mints a plain `UBlueprint` and loses its whole AnimGraph. Census and
  batch never enumerate one (the mirrored gate is exact-equality **on purpose**, and the count of
  excluded anim packages is reported as `animExcluded`, never silently dropped). `kr_verify_fidelity` and
  `kr_classify_drift` **refuse** an anim source with the full explanation and an explicit `allowAnim:true`
  opt-in that flags the result `degraded:true`.
- **`score`/`adjustedScore` are JSON `null` when nothing was scored** — never `1.000`, never the `-1`
  sentinel — with `compared`/`scored` beside every percentage. Raw and adjusted are always printed
  together.
- **CVar overrides are read back.** A CVar `Set` is refused silently when something wrote it at a higher
  priority, so `classifyIntentional` / the census instrument try `ECVF_SetByCode`, verify, escalate to
  `ECVF_SetByConsole` if the polite attempt lost, and report `classifyIntentionalApplied` /
  `driftCensusApplied`. A run whose requested flag did not take says so.
- **Fixed in passing: the `op` regression, this provider's copy.** `KrRejectUnknownParams` (the local
  mirror of MifBridge's helper) did not tolerate the batch dispatcher's `op` key, so **every** `kr_*`
  endpoint failed with `unrecognised parameter 'op'` when called inside `batch` — the same regression
  MifBridge fixed centrally in Batch E, never mirrored here. One line, cited.

### Live proofs — run after the next build

```bash
B=http://127.0.0.1:8791/api
H=(-H "X-Mif-Token: dev" -H "Content-Type: application/json")

# W3-0  the endpoints exist at all (12 kr_* now, 0 policy contradictions).
curl -s -X POST $B/self_audit "${H[@]}" -d '{}'
# expect: externalProviders includes MifKismetReconstructor with 12 endpoints;
#         kr_verify_fidelity / kr_classify_drift / kr_drift_census / kr_batch_reconstruct all
#         bucket "SelfManaged"; policyContradictions [].

# ---------------------------------------------------------------- kr_verify_fidelity

# W3-1  POSITIVE fidelity proof — 7 own functions, the daily driver.
curl -s -X POST $B/kr_verify_fidelity "${H[@]}" \
  -d '{"sourceAsset":"/Game/Blueprints/Pawns/NPC/Oponents/Behaviour/BP_OponentPatrolRoute.BP_OponentPatrolRoute_C"}'
# expect: ok:true, jobId:"krjob-N", kind:"verify", state:"queued", deferred:true,
#         mode:"child", functionsTotalEstimate ~7.
curl -s -X POST $B/kr_reconstruct_status "${H[@]}" -d '{}'
# expect: state:"done", progressObservable:false (this kind is atomic),
#         result.fidelity.scored > 0, result.fidelity.score a number in a plausible band around the
#         corpus 54.65%, and BOTH machine-checkable invariants true:
#           result.invariants.scoredEqualsSum == true
#           result.invariants.adjustedGeScore == true
#           result.invariants.comparedEqualsScoredMinusMissing == true
#         result.stats.functionsAttempted == the estimate above (or the response says why not).
#         RECORD result.elapsedMs — no wall-clock number for a single-BP verify exists anywhere in
#         source, and the async design rests on that gap.

# W3-2  CLASSIFIER CONTAINMENT — the audit baseline. Same BP, classifier off.
curl -s -X POST $B/kr_verify_fidelity "${H[@]}" \
  -d '{"sourceAsset":"/Game/Blueprints/Pawns/NPC/Oponents/Behaviour/BP_OponentPatrolRoute.BP_OponentPatrolRoute_C","classifyIntentional":false}'
curl -s -X POST $B/kr_reconstruct_status "${H[@]}" -d '{}'
# expect: result.classifyIntentionalApplied == true, result.fidelity.intentional == 0, and
#         drift(off) == intentional(on) + drift(on) from W3-1  — the containment invariant.
#         identical and equivalent must be UNCHANGED between the two runs.

# W3-3  THE "NEVER 1.000" PROOF. A small event-only trigger actor scores nothing.
curl -s -X POST $B/kr_verify_fidelity "${H[@]}" \
  -d '{"sourceAsset":"/Game/Audio/Music/ChaseAndFight/RaidAreaSphere.RaidAreaSphere_C"}'
curl -s -X POST $B/kr_reconstruct_status "${H[@]}" -d '{}'
# expect: state:"done", result.fidelity.scored == 0, result.fidelity.score == null (JSON null,
#         NOT 1.0 and NOT -1), adjustedScore == null, and result.fidelity.scoreNote present.
#         THIS IS A PASS, not a failure — record it as the honesty proof.

# W3-4  THE ANIM TRAP — must be REFUSED, with the reason.
curl -s -X POST $B/kr_verify_fidelity "${H[@]}" \
  -d '{"sourceAsset":"/Game/Animations/AnimClasses/NPC/PrisonerAnimBP.PrisonerAnimBP_C"}'
# expect: ok:FALSE, animBlueprint:true, error names the AnimGraph loss, the plain-UBlueprint mint,
#         and allowAnim:true as the explicit opt-in. NO job is started (the slot stays free).
curl -s -X POST $B/kr_verify_fidelity "${H[@]}" \
  -d '{"sourceAsset":"/Game/Animations/AnimClasses/NPC/PrisonerAnimBP.PrisonerAnimBP_C","allowAnim":true}'
curl -s -X POST $B/kr_reconstruct_status "${H[@]}" -d '{}'
# expect: the job runs, and result.animBlueprint:true, result.degraded:true,
#         result.degradedReason present. Numbers exist but are flagged uncomparable to the corpus.

# W3-5  PARAMETER DISCIPLINE. Unknown key => named, with the accepted set.
curl -s -X POST $B/kr_verify_fidelity "${H[@]}" \
  -d '{"sourceAsset":"/Game/Audio/Music/ChaseAndFight/RaidAreaSphere.RaidAreaSphere_C","variant":"sibling"}'
# expect: ok:FALSE, error: unrecognised parameter 'variant' (... CHILD-ONLY ... FALSE drift ...)
#         - accepted: sourceAsset (aliases: blueprint, bpName, path), classifyIntentional (alias:
#         classify), allowAnim
curl -s -X POST $B/kr_verify_fidelity "${H[@]}" -d '{}'
# expect: ok:FALSE, error names sourceAsset as required.

# W3-6  ONE SLOT, NO QUEUE. Fire two in a row.
curl -s -X POST $B/kr_verify_fidelity "${H[@]}" -d '{"sourceAsset":"BP_OponentPatrolRoute"}' &
curl -s -X POST $B/kr_drift_census "${H[@]}" -d '{"maxCount":3}'
# expect: the SECOND is REFUSED naming the running jobId/kind/state — never silently queued.

# ---------------------------------------------------------------- kr_classify_drift

# W3-7  PER-FUNCTION VERDICTS + the independent cross-check.
curl -s -X POST $B/kr_classify_drift "${H[@]}" \
  -d '{"sourceAsset":"/Game/Blueprints/Pawns/NPC/Oponents/Behaviour/BP_OponentPatrolRoute.BP_OponentPatrolRoute_C"}'
curl -s -X POST $B/kr_reconstruct_status "${H[@]}" -d '{}'
# expect: result.consistent == true  (per-function rows sum to the aggregate produced by an
#         INDEPENDENT whole-BP verifier run — the assert the plan asked for, carried in code)
#         result.verdictCounts.identical == result.fidelity.identical  (and so on for every class)
#         result.reasonTally reproduces result.fidelity.intentTally exactly
#         every functions[] row has name + verdict + reasons[]; drift rows carry `detail`, and a row
#         whose detail lacks "ROOT:" carries reasons ["classifier-declined-or-off"].

# W3-8  `function` FILTERS THE REPORT, and a miss is never a silent empty row.
curl -s -X POST $B/kr_classify_drift "${H[@]}" \
  -d '{"sourceAsset":"BP_OponentPatrolRoute","function":"NoSuchFunction"}'
curl -s -X POST $B/kr_reconstruct_status "${H[@]}" -d '{}'
# expect: result.functionFound == false, result.attemptedFunctions[] lists what WAS attempted,
#         result.functionNote explains inherited / no-bytecode / ubergraph. The whole-BP verify
#         still ran: result.fidelity is fully populated.

# W3-9  includeWindow is REFUSED with the reason and the alternative.
curl -s -X POST $B/kr_classify_drift "${H[@]}" \
  -d '{"sourceAsset":"BP_OponentPatrolRoute","includeWindow":true}'
# expect: ok:FALSE, "unrecognised parameter 'includeWindow' (not implemented - the +/-2 statement
#         window needs the verifier's canonical cooked/recon streams ... use kr_disassemble_function)"

# ---------------------------------------------------------------- kr_drift_census

# W3-10  SLICING IS REAL — bpDone must ADVANCE across polls.
curl -s -X POST $B/kr_drift_census "${H[@]}" \
  -d '{"pathFilter":"/Game/Blueprints/","maxCount":5}'
# expect: ok:true, kind:"census", bpTotal:5, matched:<N>, animExcluded:<N>, csvPath set, deferred:true
curl -s -X POST $B/kr_reconstruct_status "${H[@]}" -d '{}'   # poll repeatedly
# expect: progressObservable == true, and result.bpDone STRICTLY INCREASES between polls while
#         state == "running" (this is the proof of one-BP-per-tick slicing; the atomic kinds cannot
#         do this). On completion:
#           result.pass + result.fail + result.skip == result.bpDone == result.bpTotal == 5
#           result.skipTaxonomy.resolve + .parent + .mint == result.skip
#           result.corpusFidelity and result.corpusAdjusted both present (or both null)
#           result.filter.driftCensusApplied == true
#           result.censusCsvPath exists on disk; result.censusCsvRows is its row count
#           result.corpusBaseline echoes 1277 / 1228-of-1256 / 54.65% / 85.9% for comparison
#           result.gcRuns >= 1

# W3-11  CENSUS == SUM OF INDIVIDUAL VERIFIES (the summation invariant).
# Take the five package names from the CSV at result.csvPath, run kr_verify_fidelity on each with the
# same classifyIntentional, and sum identical/equivalent/intentional/drift/missing/uncomparable.
# expect: bit-identical to result.totals from W3-10.

# W3-12  EMPTY MATCH is `done` with bpTotal:0 and the filter echoed — never a hung queued job.
curl -s -X POST $B/kr_drift_census "${H[@]}" -d '{"pathFilter":"/Game/NoSuchFolderAnywhere/"}'
curl -s -X POST $B/kr_reconstruct_status "${H[@]}" -d '{}'
# expect: request carries hint:"zero cooked Blueprints match ..."; status reaches state:"done"
#         with result.bpTotal == 0 and result.filter.pathFilter echoed back.

# ---------------------------------------------------------------- kr_batch_reconstruct

# W3-13  PASS/FAIL SWEEP with the engine's CSV.
curl -s -X POST $B/kr_batch_reconstruct "${H[@]}" \
  -d '{"pathFilter":"/Game/Blueprints/Enviro/","mode":"child","maxBlueprints":10}'
curl -s -X POST $B/kr_reconstruct_status "${H[@]}" -d '{}'
# expect: result.pass + result.fail + result.skip == result.bpTotal
#         result.csvRows == result.bpDone, and the CSV at result.csvPath has the engine harness's
#         9-column header (no verify) with one flushed row per Blueprint plus a "# TOTAL" line.
#         Spot-check one row's RealFuncs against kr_dump_blueprint's own-functions-with-bytecode
#         count for the same Blueprint — they must match.

# W3-14  verify REQUIRES child — the sibling refusal, verbatim reasoning.
curl -s -X POST $B/kr_batch_reconstruct "${H[@]}" \
  -d '{"pathFilter":"/Game/Blueprints/Enviro/","mode":"sibling","verify":true,"maxBlueprints":3}'
# expect: ok:FALSE, "verify requires mode:'child' - a SIBLING copy mints its components into the
#         transient package, so every component reference differs by object path and the drift would
#         be an artefact of the mode, not of the decompiler."

# W3-15  verify:true + child produces the 17-column CSV and the totals block.
curl -s -X POST $B/kr_batch_reconstruct "${H[@]}" \
  -d '{"pathFilter":"/Game/Blueprints/Enviro/","mode":"child","verify":true,"maxBlueprints":3}'
curl -s -X POST $B/kr_reconstruct_status "${H[@]}" -d '{}'
# expect: CSV header is the 17-column verify form; a Blueprint that scored nothing has EMPTY
#         Fidelity/AdjFidelity cells (never 0.000, never 1.000); result.totals present.

# W3-16  COMPOSITION — the `op` fix. A kr_* read inside batch must not fail on 'op'.
curl -s -X POST $B/batch "${H[@]}" \
  -d '{"ops":[{"op":"kr_list_cooked_blueprints","pathContains":"/Game/Blueprints/","limit":2}]}'
# expect: ok:true for the inner op. Before this change it returned
#         "unrecognised parameter 'op'" — that is the regression this fixes.
```

### What `server.py` needs (owned by a later agent — NOT touched here)

Four `@mcp.tool()` wrappers, all POST to `/api/<name>`, all thin pass-throughs. No new status tool —
they are polled by the existing `kr_reconstruct_status`.

| Tool | Params (name: type = default) |
|---|---|
| `kr_verify_fidelity` | `source_asset: str` (**required**; wire key `sourceAsset`), `classify_intentional: bool = True` (`classifyIntentional`), `allow_anim: bool = False` (`allowAnim`) |
| `kr_classify_drift` | `source_asset: str` (**required**), `function: str = None`, `classify_intentional: bool = True`, `allow_anim: bool = False` |
| `kr_drift_census` | `path_filter: str = "/Game/"` (`pathFilter`), `start_index: int = 0` (`startIndex`), `max_count: int = 50` (`maxCount`, 0 = unbounded), `classify_intentional: bool = True` |
| `kr_batch_reconstruct` | `path_filter: str = "/Game/"`, `mode: str = "sibling"` (`sibling`\|`child`), `verify: bool = False` (requires `mode="child"`), `start_index: int = 0`, `max_blueprints: int = 0` (`maxBlueprints`, 0 = all), `classify_intentional: bool = True` |

Omit any parameter the caller did not set — every one of these endpoints rejects unknown keys by name,
and sending a `None` would be sent as a key. The docstrings should carry three facts: (1) the call
returns a `jobId` and does NOT do the work — poll `kr_reconstruct_status`; (2) `census`/`batch` are the
only kinds whose progress advances mid-job (`progressObservable` says so per record); (3)
`result.fidelity.score` is `null`, never `1.0`, when nothing was scored.

### Deviations from the plan, with reasons

1. **No per-function capture sink in `MifFidelityVerifier.cpp`** (plan §5.3 / K2 file 8). Replaced by
   the one-element-denominator technique described above: same data, zero changes to the verifier, no
   module-static to arm and disarm, and it yields an independent cross-check instead of a tautology.
   Cost: the classify endpoint pays ~2× a verify.
2. **`includeWindow` is not implemented** (rejected by name, with the reason and the alternative). The
   ±2-statement window needs the verifier's canonical cooked/recon streams, which exist only inside its
   per-function loop and never cross the delegate boundary. Emitting a fabricated window would be worse
   than refusing.
3. **`rootCookedOrdinal` / `rootReconOrdinal` / `cookedStmts` / `reconStmts` are not emitted as separate
   numeric fields.** They live in `FVerdict`, which never crosses the verifier boundary. What is emitted
   instead is the verifier's own formatted root line, verbatim, as `detail` — the same information,
   unparsed and unfabricated.
4. **No changes to `MifKrJobManager.h/.cpp`** (plan file 4). The census/batch counters are written into
   the existing `FJobRecord::Result` payload each slice, which `kr_reconstruct_status` already emits
   whenever it is valid — so live progress works with zero new record fields and zero new files to keep
   in sync. `Kind` is already a free-form string, so `verify`/`classify`/`census`/`batch` needed nothing.
5. **No `WITH_KR_TRANSIENT_RECONSTRUCT` define** (plan §6.3 item 4). The engine export is committed and
   built; adding a compile gate for a rollback that has not happened would be dead configuration. If the
   engine change is ever backed out, the gate is a 6-line addition at that point.
6. **`.cpp` written with LF, not CRLF.** `MifKrBridgeEndpoints.cpp` and every sibling source file in
   both plugins are 100% LF on disk today; appending CRLF would have produced a mixed-ending file, which
   is worse than either convention. This doc section is CRLF, matching the tail of this file.
7. **`allowAnim` added** (not in the plan's parameter tables). The plan required the anim case to be
   detected and refused rather than silently degraded; a flat refusal with no escape hatch would have
   made the negative-path proof unrunnable. Default `false` (refuse), and the opt-in path flags
   `degraded:true`.
8. **`kr_batch_reconstruct` keeps the plan's `maxBlueprints` default of 0 (= every match)** even though
   `kr_drift_census` defaults to 50 for accident-avoidance. The plan states it explicitly and it mirrors
   `mif.kr.ReconstructAll`; the sweep is sliced and observable, and `bpTotal` is echoed in the queued
   response before any work happens.

**Verdict: COMPLETE (source only, UNBUILT, UNPROVEN LIVE).** Provider endpoint count 8 → 12. Nothing in
`MifBridge/` changed, so `MIF_DECL count == MIF_BIND count` is untouched. `server.py` unchanged — the
four tools above are owed by its owner. Ship-safety gate grep (the one this file's header documents):
**zero hits**, and there is no `#if MIF_KR_DEBUG` anywhere in the file.

## Batch H — asset field naming + audit_unused excludeReferencers

**Status: SOURCE-COMPLETE, UNBUILT.** The editor was running throughout; no build, no launch, no
kill. Everything under "live BEFORE" below was measured against the *running* (pre-change) DLL and is
what the fix is measured against. Everything under "live proof" is gating and must be run after the
next build.

**Two user-reported gaps, both scoped to asset endpoints:**

- **GAP 8 (trivial)** — *"find_assets field naming — returns package vs path inconsistently, so
  callers guess."*
- **GAP 4 (low)** — *"audit_unused could use an excludeReferencers param so a dev-only test level
  does not mask everything."*

### Files changed

| File | Why |
|---|---|
| `Source/MifBridge/Private/MifBridgeCooked.cpp` | `find_assets`, `describe_package`, `list_mounted_containers` row naming |
| `Source/MifBridge/Private/MifBridgeAssetOps.cpp` | **where `H_get_referencers` / `H_get_dependencies` / `H_audit_unused` live** — plus `delete/rename/duplicate_asset`. `excludeReferencers` lands here |
| `docs/audit/06_IMPLEMENTED.md` | this section |

**No endpoint added or removed**, so `MifBridgeHandlers.h` (`MIF_DECL`) and `MifBridgeCommon.cpp`
(`MIF_BIND`) are untouched and the registry cannot have drifted: live `self_audit.endpointCount` was
**188** before the change and must still be 188 after. `tools/ue5-mcp-bridge/server.py` deliberately
untouched — see "what server.py needs".

---

### GAP 8 — one meaning per key

The report understated it. The keys were not merely inconsistent, the **same key meant different
things on different endpoints**, with nothing in the response to say which:

- `find_assets.assets[].path` -> **object** path `/Game/X/Foo.Foo_C`
- `audit_unused.assets[].path` -> **package** path `/Game/X/Foo`
- `rename_asset.newPath` -> **package** path · `duplicate_asset.newPath` -> **object** path
- `list_mounted_containers.containers[].path` -> a **filesystem** path (`...\pakchunk0.utoc`)

So piping one endpoint's `path` into the next is a silent "asset not found" — and there was no key a
caller could read blind and trust. The fix is **purely additive**: every legacy key keeps its exact
previous value, and every asset row gains two keys that mean the same thing everywhere.

```
objectPath  = /Game/X/Foo.Foo_C   the object inside the package
                                  -> set_property, get_property, open_blueprint, describe_class
packageName = /Game/X/Foo         the package that holds it
                                  -> get_referencers, get_dependencies, describe_package, delete_asset
```

Both are written by ONE helper, `EmitAssetIdentity(Row, ObjectPath, PackageName)`, so no emitter can
spell them differently. It is duplicated in the two files with an explicit **eviction clause** —
promote it to `MifBridgeHandlers.h` next time that header is edited (exactly the route
`RejectUnknownParams` took in Batch C); a third consumer must promote, not copy.

#### Field-naming table — before -> after

Additions in **bold**. Nothing was renamed, removed, or given a different value.

| Endpoint | Row / object | Before | After |
|---|---|---|---|
| `find_assets` | `assets[]` | `path`(obj) `name` `class` `package`(pkg) `origin` `loaded` | **`objectPath`** **`packageName`** + all of the above unchanged |
| `describe_package` | top level | `package` `origin` `existsOnDisk` `inRegistry` `loaded` `flags` | **`packageName`** + unchanged |
| `describe_package` | `registryAssets[]` | `path`(obj) `name` `class` `loaded` | **`objectPath`** **`packageName`** **`package`** **`origin`** — now byte-for-byte the same eight keys `find_assets` emits |
| `describe_package` | `exports[]` | `name` `class` | **`objectPath`** (= `UObject::GetPathName()`, keeps any `:Subobject`) **`packageName`** |
| `list_mounted_containers` | `containers[]` | `file` `path`(filesystem!) `sizeBytes` | **`filePath`** (same value, says which kind of path). Not an asset row — `objectPath`/`packageName` deliberately absent |
| `audit_unused` | `assets[]` | `path`(**pkg**) `name` `class` `folder` `refs` `extRefs` | **`objectPath`** **`packageName`** **`package`** + the GAP-4 fields below |
| `get_referencers` | top level | `package` `count` `referencers[]` | **`packageName`** (`referencers[]` entries are package paths — the registry graph is package-to-package, now stated in the `out:` block) |
| `get_dependencies` | top level | `package` `count` `dependencies[]` | **`packageName`** |
| `delete_asset` | top level | `path`(pkg) `numDeleted` `deleted` | **`packageName`** |
| `rename_asset` | top level | `oldPath`(pkg) `newPath`(**pkg**) `renamed` | **`oldPackageName`** **`newPackageName`** **`newObjectPath`** (read back off the renamed `UObject`, not reassembled) |
| `duplicate_asset` | top level | `sourcePath`(pkg) `newPath`(**obj**) `duplicated` | **`sourcePackageName`** **`newPackageName`** **`newObjectPath`** (both read back off the created object — `DuplicateAsset` uniquifies on collision) |
| `diagnose_landscape`, `diagnose_landscape_draws` | `proxies[]`, `contrastProxies[]`, `sample[]` | — | **unchanged, on purpose.** These are component rows, not asset rows; nothing is keyed `path`/`package`, and `material` holds an object path under a name that already says what it is. GAP 8 is about keys whose *meaning* varied — none of these do |

Every `in:`/`out:` comment block on those endpoints now states the distinction, plus three comments
that were simply *wrong* are corrected in passing: `find_assets` never documented `returned`,
`list_mounted_containers` never documented `containerCount` or `path`, and `describe_package` never
documented its `path` alias.

---

### GAP 4 — `excludeReferencers`

`audit_unused` extended, not rewritten: same handler, same style, same response shape plus new keys.

```
excludeReferencers?: string | string[]        aliases: excludeReferencer, ignoreReferencers
```

Package paths and/or folder prefixes whose references **do not count toward "used"**.

| Rule | Behaviour |
|---|---|
| Exact form | `/Game/DevTest/L_Scratch` — that one package |
| Prefix form | `/Game/DevTest/` — everything beneath |
| Trailing slash | **optional** — both forms also match beneath. Guessing intent from one character would silently under-exclude, which is the failure this parameter removes |
| Object path in | accepted and reduced to its package, so a value pasted out of `find_assets` works unedited |
| Single string | accepted as well as an array |
| Malformed entry | **error naming the entry**, never a skip — a dropped pattern reads as "nothing was excluded", i.e. exactly the masking the caller asked us to stop |
| Unknown key | still rejected by name by the shared `RejectUnknownParams`, whose accepted list now includes the three spellings |
| Matching | case-insensitive, same as every other package comparison in the plugin |

**Per-result echo — nothing is dropped silently.** Every row carries which referencers stopped
counting and which pattern did it:

```json
{ "objectPath": "/Game/X/SM_Foo.SM_Foo", "packageName": "/Game/X/SM_Foo",
  "path": "/Game/X/SM_Foo", "package": "/Game/X/SM_Foo", "name": "SM_Foo",
  "class": "/Script/Engine.StaticMesh", "folder": "/Game/X",
  "refs": 0, "refsTotal": 1, "extRefs": 0, "excludedRefs": 1,
  "excludedReferencers": [
    { "packageName": "/Game/DevTest/L_Scratch", "matchedPattern": "/Game/DevTest" } ] }
```

Summary additions:

| Key | Meaning |
|---|---|
| `excludedReferencerCount` | total excluded referencer edges across the whole scan |
| `unusedOnlyDueToExclusions` | assets that were masked **purely** by excluded referencers — the number the parameter exists to reveal |
| `excludeReferencers[]` | echo of the **effective** patterns (object paths reduced, trailing slash dropped) — what was applied, not what was typed |
| `excludeReferencerMatches{}` | per-pattern hit count. A mistyped pattern is otherwise indistinguishable from one that legitimately matched nothing; a `0` here names the typo |

**Invariants, checkable per row:** `refsTotal - refs == excludedRefs == excludedReferencers.length`.
`refs` deliberately counts only referencers that COUNT, so `refs == 0` keeps meaning "unused" and no
existing caller-side test changes. An excluded referencer is removed from `extRefs` too — a dev level
is external to every folder, so leaving it there would keep the mask in place under a different
number. **With `excludeReferencers` omitted, every new key is `0`/`[]` and `refs == refsTotal`: the
response is the previous one plus additions.**

#### Also fixed here: truncation under-reported `unusedCount`

The row cap used `break`, which stopped the *scan*, so the summary numbers depended on the page size.
Proven live against the unchanged DLL, same folder, same registry, seconds apart:

```
audit_unused {pathPrefix:"/Game/MODS/BotanistExpansion_p", limit:2}    -> unusedCount 3,  truncated true
audit_unused {pathPrefix:"/Game/MODS/BotanistExpansion_p", limit:4000} -> unusedCount 43, truncated false
```

Same question, two answers. Changed to `continue` (the cap now bounds the array, not the scan) —
matching `find_assets`, which has always done it that way. `excludedReferencerCount` would have been
born with the same defect. This is a behaviour change beyond what was requested: a truncated
`audit_unused` now reports the *true* `unusedCount` instead of a number that grows with `limit`.

---

### Live BEFORE evidence (running editor, pre-build, 2026-07-28)

1. `find_assets` rows carry `path`(object) + `package`(package) and **no** `objectPath`/`packageName`:
   `{"path":"/Game/MODS/.../MI_Dryer_3_2.MI_Dryer_3_2", "package":"/Game/MODS/.../MI_Dryer_3_2", ...}`
2. `describe_package.registryAssets[]` rows carry `path`,`name`,`class`,`loaded` and **no**
   `package`/`origin` — the two row shapes really were different.
3. The parameter did not exist and was correctly refused by name:
   `{"ok":false,"error":"unrecognised parameter 'excludeReferencers' - accepted: pathPrefix, class, includeAll, limit, rescan"}`
4. **The masking is real and large in this project.** `/Game/MODS/BotanistExpansion_p/Levels/MIF_TestGrounds`
   is a dev test level holding **1009** dependencies (973 under the mod folder). Walking
   `get_referencers` over all of them: **406 assets under `/Game/MODS/BotanistExpansion_p` are
   referenced by nothing except that one test level.** `audit_unused` today reports **43** unused out
   of **1641** scanned. Discounting the test level, the honest number is **449**.
5. Negative control: `/Game/MODS/BotanistExpansion_p/Blueprints/Equipment/BE_LABEQ_Cauldron` has 3
   referencers — `.../Levels/MIF_TestGrounds`, `/Game/Maps/Untitled`,
   `.../source_tables/MIF_HideoutEquipment` — so excluding the test level must leave it **used**
   (`refs` 3 -> 2), not swept up.

### Live proof (REQUIRED — run after the next build)

```bash
B=http://127.0.0.1:8791/api
H=(-H "X-Mif-Token: ${MIF_BRIDGE_TOKEN:-dev}" -H "Content-Type: application/json")

# H-1  registry unchanged: no endpoint added or removed.
curl -s -X POST $B/self_audit "${H[@]}" -d '{}' \
| python -c "import sys,json; d=json.load(sys.stdin); print(d['endpointCount'], d['policyContradictions']); \
  assert d['endpointCount']==188 and not d['policyContradictions'] and d['healthy']"

# H-2  GAP 8: every find_assets row carries BOTH names, and the legacy keys are unchanged.
curl -s -X POST $B/find_assets "${H[@]}" -d '{"pathPrefix":"/Game/MODS","limit":3}' \
| python -c "import sys,json; a=json.load(sys.stdin)['assets']; print(a[0]); \
  assert all(r['objectPath']==r['path'] and r['packageName']==r['package'] for r in a); \
  assert all(r['objectPath'].startswith(r['packageName']+'.') for r in a)"

# H-3  GAP 8: describe_package rows now have the SAME shape as find_assets rows, and exports are addressable.
curl -s -X POST $B/describe_package "${H[@]}" \
  -d '{"package":"/Game/MODS/BotanistExpansion_p/Blueprints/Equipment/BE_LABEQ_Cauldron"}' \
| python -c "import sys,json; d=json.load(sys.stdin); r=d['registryAssets'][0]; print(r); print(d['exports'][1]); \
  assert d['packageName']==d['package']; \
  assert set(r)=={'objectPath','packageName','path','package','name','class','origin','loaded'}; \
  assert all(e['objectPath'].startswith(e['packageName']) for e in d['exports'])"

# H-4  GAP 8: audit_unused row `path` is the PACKAGE path (unlike find_assets) — now stated by the row itself.
#      AND: omitting the new parameter must reproduce the OLD numbers exactly.
curl -s -X POST $B/audit_unused "${H[@]}" -d '{"pathPrefix":"/Game/MODS/BotanistExpansion_p","limit":4000}' \
| python -c "import sys,json; d=json.load(sys.stdin); r=d['assets'][0]; print(d['scanned'], d['unusedCount'], r); \
  assert r['packageName']==r['path']==r['package'] and r['objectPath'].startswith(r['packageName']+'.'); \
  assert d['unusedCount']==43 and d['scanned']==1641; \
  assert d['excludedReferencerCount']==0 and d['excludeReferencers']==[] and d['unusedOnlyDueToExclusions']==0; \
  assert all(x['refs']==x['refsTotal'] and x['excludedRefs']==0 for x in d['assets'])"

# H-5  the truncation fix: the summary no longer depends on the page size.
curl -s -X POST $B/audit_unused "${H[@]}" -d '{"pathPrefix":"/Game/MODS/BotanistExpansion_p","limit":2}' \
| python -c "import sys,json; d=json.load(sys.stdin); print(d['unusedCount'], d['truncated'], len(d['assets'])); \
  assert d['unusedCount']==43 and d['truncated'] and len(d['assets'])==2"
# BEFORE this batch the same call returned unusedCount 3.

# H-6  GAP 4, the headline: one dev test level was masking 406 assets.
curl -s -X POST $B/audit_unused "${H[@]}" \
  -d '{"pathPrefix":"/Game/MODS/BotanistExpansion_p","limit":4000,
       "excludeReferencers":"/Game/MODS/BotanistExpansion_p/Levels/MIF_TestGrounds"}' \
| python -c "import sys,json; d=json.load(sys.stdin); \
  print(d['scanned'], d['unusedCount'], d['unusedOnlyDueToExclusions'], d['excludedReferencerCount'], d['excludeReferencerMatches']); \
  assert d['scanned']==1641; \
  assert d['unusedOnlyDueToExclusions']==406 and d['unusedCount']==449; \
  assert d['excludeReferencers']==['/Game/MODS/BotanistExpansion_p/Levels/MIF_TestGrounds']; \
  assert sum(d['excludeReferencerMatches'].values())==d['excludedReferencerCount']; \
  rows=[r for r in d['assets'] if r['excludedRefs']]; print(rows[0]); \
  assert all(r['refsTotal']-r['refs']==r['excludedRefs']==len(r['excludedReferencers']) for r in d['assets'])"
# ^ a SINGLE STRING is accepted, not just an array. Every row must satisfy the arithmetic invariant.

# H-7  GAP 4: the per-result echo names WHO was excluded and WHICH pattern did it.
curl -s -X POST $B/audit_unused "${H[@]}" \
  -d '{"pathPrefix":"/Game/MODS/BotanistExpansion_p/Blueprints/Equipment","includeAll":true,"limit":200,
       "excludeReferencers":["/Game/MODS/BotanistExpansion_p/Levels/"]}' \
| python -c "import sys,json; d=json.load(sys.stdin); \
  rk={r['name']:r for r in d['assets']}; c=rk['BE_LABEQ_Cauldron']; r1=rk['BE_LABEQ_Rack01']; print(c); print(r1); \
  assert c['refsTotal']==3 and c['refs']==2 and c['excludedRefs']==1 and c['refs']>0; \
  assert c['excludedReferencers'][0]['packageName']=='/Game/MODS/BotanistExpansion_p/Levels/MIF_TestGrounds'; \
  assert c['excludedReferencers'][0]['matchedPattern']=='/Game/MODS/BotanistExpansion_p/Levels'; \
  assert r1['refs']==0 and r1['refsTotal']==1 and r1['excludedRefs']==1"
# ^ PREFIX form, and the negative control: Cauldron keeps 2 real referencers and is NOT swept up.
# The echoed pattern is the NORMALIZED one (trailing slash dropped) — that is what was applied.

# H-8  GAP 4: trailing slash is optional, and an object path is reduced to its package.
curl -s -X POST $B/audit_unused "${H[@]}" \
  -d '{"pathPrefix":"/Game/MODS/BotanistExpansion_p","limit":1,
       "excludeReferencers":["/Game/MODS/BotanistExpansion_p/Levels/MIF_TestGrounds.MIF_TestGrounds",
                             "/Game/MODS/BotanistExpansion_p/Levels/MIF_TestGrounds/"]}' \
| python -c "import sys,json; d=json.load(sys.stdin); print(d['excludeReferencers'], d['unusedOnlyDueToExclusions']); \
  assert d['excludeReferencers']==['/Game/MODS/BotanistExpansion_p/Levels/MIF_TestGrounds']; \
  assert d['unusedOnlyDueToExclusions']==406"
# ^ three spellings of the same level collapse to ONE canonical pattern; result identical to H-6.

# H-9  a typo'd pattern is visible, not silent.
curl -s -X POST $B/audit_unused "${H[@]}" \
  -d '{"pathPrefix":"/Game/MODS/BotanistExpansion_p","limit":1,"excludeReferencers":"/Game/DoesNotExist/"}' \
| python -c "import sys,json; d=json.load(sys.stdin); print(d['excludeReferencerMatches']); \
  assert d['excludeReferencerMatches']=={'/Game/DoesNotExist':0} and d['excludedReferencerCount']==0 \
     and d['unusedOnlyDueToExclusions']==0"

# H-10  malformed input ERRORS naming the parameter — never a silent skip, never ok:true having done nothing.
curl -s -X POST $B/audit_unused "${H[@]}" -d '{"pathPrefix":"/Game/MODS","excludeReferencers":["Game/NoLeadingSlash"]}'
# expect ok:false, error names excludeReferencers AND the offending entry
curl -s -X POST $B/audit_unused "${H[@]}" -d '{"pathPrefix":"/Game/MODS","excludeReferencers":[123]}'
# expect ok:false, "entries must be strings"
curl -s -X POST $B/audit_unused "${H[@]}" -d '{"pathPrefix":"/Game/MODS","excludeReferencers":[""]}'
# expect ok:false, "contains an empty entry"
curl -s -X POST $B/audit_unused "${H[@]}" -d '{"pathPrefix":"/Game/MODS","excludeReferencer":"/Game/DevTest"}'
# expect ok:TRUE — alias accepted
curl -s -X POST $B/audit_unused "${H[@]}" -d '{"pathPrefix":"/Game/MODS","excludeRefs":"/Game/DevTest"}'
# expect ok:false, "unrecognised parameter 'excludeRefs'" + the accepted list INCLUDING excludeReferencers

# H-11  composition: the guard must still tolerate the batch dispatcher's `op` key.
curl -s -X POST $B/batch "${H[@]}" \
  -d '{"ops":[{"op":"audit_unused","pathPrefix":"/Game/MODS/BotanistExpansion_p","limit":1,
               "excludeReferencers":"/Game/MODS/BotanistExpansion_p/Levels/MIF_TestGrounds"},
              {"op":"find_assets","pathPrefix":"/Game/MODS","limit":1}]}'
# expect ok for both — Batch E made RejectUnknownParams always tolerate `op`.

# H-12  the rest of the additive naming, on throwaway assets.
curl -s -X POST $B/duplicate_asset "${H[@]}" \
  -d '{"path":"/Game/MODS/BotanistExpansion_p/Label_BotanistExpansion","newPath":"/Game/Mods/AuditProofs/H_DupProof"}' \
| python -c "import sys,json; d=json.load(sys.stdin); print(d); \
  assert d['newPath']==d['newObjectPath']=='/Game/Mods/AuditProofs/H_DupProof.H_DupProof'; \
  assert d['newPackageName']=='/Game/Mods/AuditProofs/H_DupProof'"
curl -s -X POST $B/rename_asset "${H[@]}" \
  -d '{"path":"/Game/Mods/AuditProofs/H_DupProof","newPath":"/Game/Mods/AuditProofs/H_RenProof","confirm":true}' \
| python -c "import sys,json; d=json.load(sys.stdin); print(d); \
  assert d['newPath']==d['newPackageName']=='/Game/Mods/AuditProofs/H_RenProof'; \
  assert d['newObjectPath']=='/Game/Mods/AuditProofs/H_RenProof.H_RenProof'"
# ^ THE GAP-8 BUG IN ONE PAIR: duplicate_asset.newPath is an OBJECT path, rename_asset.newPath is a
#   PACKAGE path. Both legacy values unchanged; newObjectPath/newPackageName are unambiguous.
curl -s -X POST $B/delete_asset "${H[@]}" -d '{"path":"/Game/Mods/AuditProofs/H_RenProof","confirm":true}'
curl -s -X POST $B/get_referencers "${H[@]}" \
  -d '{"path":"/Game/MODS/BotanistExpansion_p/Blueprints/Equipment/BE_LABEQ_Cauldron.BE_LABEQ_Cauldron"}' \
| python -c "import sys,json; d=json.load(sys.stdin); print(d['packageName'], d['count']); \
  assert d['packageName']==d['package'] and d['count']==3"
curl -s -X POST $B/list_mounted_containers "${H[@]}" -d '{}' \
| python -c "import sys,json; c=json.load(sys.stdin)['containers']; print(c[:1]); \
  assert all(x['filePath']==x['path'] for x in c)"
```

Pass conditions, one line each: **H-1** registry unchanged at 188 · **H-2/H-3** both row shapes
identical and self-describing · **H-4** omitting the new parameter reproduces the old numbers exactly
· **H-5** 3 -> 43, the page size no longer changes the answer · **H-6** 406 masked assets surfaced,
`unusedCount` 43 -> 449 · **H-7** prefix form works and the still-referenced control survives ·
**H-8** three spellings normalize to one · **H-9** a typo is visible as a 0 hit count · **H-10** every
malformed input errors naming the parameter · **H-11** still composes inside `batch` · **H-12** the
`newPath` ambiguity is now resolvable from the response alone.

> The BEFORE numbers (1641 / 43 / 406 / 3-referencer Cauldron) were measured on the live registry on
> 2026-07-28. If `/Game/MODS/BotanistExpansion_p` is edited before the proof runs, re-measure with
> the H-4 call first and rebase H-5/H-6/H-7 on the new baseline — the *invariants* (arithmetic per
> row, page-size independence, echo completeness) hold regardless of the numbers.

---

### What `server.py` needs (NOT touched — a later dedicated agent owns it)

No new endpoint, so no new `@mcp.tool` and no registry drift. One signature change and five docstring
changes:

1. **`audit_unused` — the only functional change.** Currently
   `audit_unused(path_prefix, cls="", include_all=False, limit=4000, rescan=False)`
   (`tools/ue5-mcp-bridge/server.py:1345`). It needs
   `exclude_referencers: list[str] | str | None = None` forwarded as
   `excludeReferencers=exclude_referencers` — and it must be dropped when `None` (`_post` already
   drops `None` kwargs, so pass `exclude_referencers or None`; an empty list must not be sent as
   `[]`, which is accepted but is noise). Docstring should carry the exact/prefix rule, the
   single-string form, and the four new summary keys.
   **Until that lands, `excludeReferencers` is reachable over raw HTTP only.**
2. **`find_assets` (:893)**, **`describe_package` (:902)**, **`get_referencers` (:1333)**,
   **`get_dependencies` (:1339)** — docstrings only: state that rows now carry `objectPath` and
   `packageName`, and that new code should read those two rather than `path`/`package`.
3. Anything downstream parsing `duplicate_asset.newPath` vs `rename_asset.newPath` should move to
   `newObjectPath`/`newPackageName`.

### Findings NOT fixed here (outside the two files I own)

- **`get_referencers`, `get_dependencies` and `audit_unused` are in the `transacted` bucket.**
  Confirmed live: `self_audit.transactionBuckets` lists all three under `transacted`, while
  `find_assets` / `describe_package` are `readOnly`. They are pure registry queries that never call
  `Modify()`, so each call opens an `FScopedTransaction` for nothing. Measured impact is smaller than
  the comment at `MifBridgeCommon.cpp:348` implies — `list_transactions` was byte-identical before and
  after a `get_referencers` call (still index 13, `"Mif Bridge: connect_pins"`), so the engine
  discards the empty transaction and the undo stack is *not* polluted. It is still a policy
  inconsistency that `self_audit.policyContradictions` cannot catch (it only detects a name in two
  buckets). One-line fix: add the three names to `IsReadOnlyEndpoint` in `MifBridgeCommon.cpp`
  alongside the existing cooked-introspection block. **Not done — that file is not mine this session.**
- **`EmitAssetIdentity` is duplicated in two files** with an eviction clause in both. Promote to
  `MifBridgeHandlers.h` next time that header is opened.

## Batch I — level streaming control (8 endpoints)

**Reported gap (user, verbatim):** *"No level streaming control. I can't load or unload a level
instance from the bridge, which is why in-game test setup needs a Lua command instead."*

Two halves, one shared read endpoint. Spec: `docs/audit/work/F_world_level.md`, entries
`list_sublevels` / `add_sublevel` / `remove_sublevel` / `set_sublevel_visibility` /
`set_current_sublevel` / `set_sublevel_streaming` — **every Phase-2 verdict line implemented as
binding**, including all three hidden modal dialogs and the one hard assert. The runtime half
(`pie_load_level_instance` / `pie_unload_level_instance`) is new: it is not in the F-axis entry list,
and it is the half the user actually asked for.

All eight handlers live in the NEW `Source/MifBridge/Private/MifBridgeStreaming.cpp` (1404 lines).
**No Build.cs change** — `UnrealEd` and `Engine` are already dependencies, and every symbol used is
exported from one of them.

**Registry**: MIF_DECL 180 -> **188**, MIF_BIND 180 -> **188** (unique endpoint names, verified
identical by set-diff — both `comm` directions empty). Raw `grep -c MIF_DECL MifBridgeHandlers.h`
= **190** (188 + the `#define` + the `#undef`); raw `grep -c MIF_BIND MifBridgeCommon.cpp` = **192**
(188 + `#define` + `#undef` + 2 prose mentions of "MIF_BIND" in comments at :438 and :446). The raw
counts differ from each other for that reason and always have — the checkable invariant is the
**unique-name set**, which is 188 == 188.

> Note on the brief's stated starting figure: it said "181 raw each". Actual on-disk state when this
> batch started was raw DECL 182 / raw BIND 184, unique 180 / 180 and in sync. The unique sets were
> the same number, which is the invariant that matters.

**Source only, UNBUILT.** Per instruction the editor was not launched, killed, or built against. The
live-proof curls below are written to be run after the next build; none has been executed yet.

### Endpoints, buckets, and cited engine API

| Endpoint | Bucket | Engine API (verified in D:/UE532) | Export |
|---|---|---|---|
| `list_sublevels` | **read-only** | `UWorld::GetStreamingLevels()` World.h:1037 (inline); `ULevelStreaming::GetWorldAssetPackageFName()` LevelStreaming.h:486; `GetLoadedLevel()` :523 (inline); `IsLevelVisible()` :556; `IsStreamingStatePending()` :564; `GetLevelStreamingState()` :351 (inline); `EnumToString(ELevelStreamingState)` :119; `UWorld::IsPartitionedWorld()` World.h:2715 (inline) | `class ENGINE_API UWorld` (World.h:953 — whole class); `ENGINE_API` per-method on MinimalAPI `ULevelStreaming` (:135) |
| `add_sublevel` | **self-managed** (+deferred) | `UEditorLevelUtils::AddLevelToWorld(UWorld*, const TCHAR*, TSubclassOf<ULevelStreaming>, const FTransform&)` EditorLevelUtils.h:223 | `static UNREALED_API` |
| `remove_sublevel` | **self-managed** (+deferred) | `UEditorLevelUtils::RemoveLevelFromWorld(ULevel*, bool, bool)` EditorLevelUtils.h:247 | `static UNREALED_API` |
| `set_sublevel_visibility` | transacted | `UEditorLevelUtils::SetLevelVisibility(...)` EditorLevelUtils.h:282; `ULevelStreaming::SetShouldBeLoaded` LevelStreaming.h:427; `SetShouldBeVisible` :414; `ULevel::SetLightingScenario` Level.h:1090 | `UNREALED_API` / `ENGINE_API` |
| `set_current_sublevel` | transacted | `UEditorLevelUtils::MakeLevelCurrent(ULevel*, bool bEvenIfLocked)` EditorLevelUtils.h:86 | `static UNREALED_API` |
| `set_sublevel_streaming` | **self-managed** (+deferred) | `UEditorLevelUtils::SetStreamingClassForLevel(ULevelStreaming*, TSubclassOf<ULevelStreaming>)` EditorLevelUtils.h:238 | `static UNREALED_API` |
| `pie_load_level_instance` | **self-managed** | `ULevelStreamingDynamic::LoadLevelInstance(UObject* WorldContext, FString LevelName, FVector, FRotator, bool& bOutSuccess, const FString& OptionalLevelNameOverride, TSubclassOf<ULevelStreamingDynamic>, bool bLoadAsTempPackage)` LevelStreamingDynamic.h:80 | `static ENGINE_API` — **verified myself, as instructed** |
| `pie_unload_level_instance` | **self-managed** | `ULevelStreaming::SetIsRequestingUnloadAndRemoval(bool)` LevelStreaming.h:458 + `SetShouldBeLoaded(false)` :427 | `ENGINE_API` |

Pre-check / resolution helpers, all `static ENGINE_API` on `FLevelUtils`:
`FindStreamingLevel(UWorld*, const TCHAR*)` LevelUtils.h:44, `IsLevelLocked(ULevel*)` LevelUtils.h:91.

Streaming classes: `ULevelStreamingAlwaysLoaded` (LevelStreamingAlwaysLoaded.h:17, `UCLASS(MinimalAPI)`)
and `ULevelStreamingDynamic` (LevelStreamingDynamic.h:19, `UCLASS(BlueprintType, MinimalAPI)`) —
MinimalAPI exports the class *type information*, which is all `StaticClass()` needs.

### The modal-dialog pre-validation, per hazardous call

`FHttpServerModule` is a **game-thread** `FTSTickerObjectBase`. A modal window spins its own loop, the
tick stops, and the bridge stops answering entirely — indistinguishable from a crash, and unrecoverable
in an unattended run (`docs/02_GOTCHAS.md` §8; it happened live on 2026-07-27). So these are
correctness guards, not polish. Four reachable hazards, each made **unreachable**:

1. **`AddLevelToWorld_Internal` — `FSuppressableWarningDialog::ShowModal()`, EditorLevelUtils.cpp:450**
   (branch opens :441). Fires when the package is already a streaming level **or** is the persistent
   level. `add_sublevel` pre-checks **both**, using the engine's own two tests verbatim so the two can
   never disagree: the persistent-level string compare
   (`World->PersistentLevel->GetOutermost()->GetName() == PackageName`) and
   `FLevelUtils::FindStreamingLevel(World, *PackageName)`. Persistent → structured error. Already
   present → `alreadyPresent:true, changed:false` **with no engine call at all** (the spec asks for
   `alreadyPresent` rather than an error; `changed:false` keeps it from reading as work done).

2. **`MakeLevelCurrent(ULevel*, bEvenIfLocked=false)` — `FMessageDialog::Open`, EditorLevelUtils.cpp:588**
   (guard at :555). `set_current_sublevel` pre-checks `FLevelUtils::IsLevelLocked(Target)` and returns
   a structured error naming the Levels panel. `bEvenIfLocked` is deliberately **not** exposed —
   making a read-only level current silently swallows every subsequent spawn.

3. **`RemoveLevelsFromWorld` — two `FMessageDialog::Open`s.**
   - Locked level, **EditorLevelUtils.cpp:832** (test at :830). `remove_sublevel` pre-checks
     `FLevelUtils::IsLevelLocked(Level)` and refuses.
   - Failed package unload, **EditorLevelUtils.cpp:896**. Traced to its only cause: `UnloadPackages`
     writes `OutErrorMessage` in exactly one place — the **dirty-package** branch,
     `PackageTools.cpp:390` (collected at :362-372). So `remove_sublevel` refuses a dirty sublevel by
     default (`discardUnsaved:false`), which also protects the user: `PrivateDestroyLevel` force-clears
     the dirty flag (EditorLevelUtils.cpp:1043-1046), i.e. the engine **silently discards unsaved
     sublevel edits**. With `discardUnsaved:true` the handler clears the flag *itself* before calling,
     so the dirty branch — and therefore the dialog — is provably dead either way.

4. **`SetStreamingClassForLevel` — `check(Level)` on `InLevel->GetLoadedLevel()`,
   EditorLevelUtils.cpp:525.** Not a dialog: a hard assert that takes the editor down.
   `set_sublevel_streaming` pre-checks `GetLoadedLevel() != nullptr` and errors with the remedy
   (`set_sublevel_visibility {shouldBeLoaded:true}` → poll → retry). (`check(InLevel)` at :516 is
   satisfied by construction — the level was resolved.)

**Every one of these guards is re-run inside the deferred lambda**, because state can change between
the HTTP call and the next tick and "we checked a frame ago" is not a guarantee.

### Deferral, and why the deferred results are not lost

`add_sublevel`, `remove_sublevel` and `set_sublevel_streaming` validate synchronously and run the
engine call on the **next tick** via `GEditor->GetTimerManager()->SetTimerForNextTick` — the
`new_level`/`load_level` precedent (`MifBridgeWorld.cpp:144` and `:204`). `remove_sublevel` has the
strongest case of the three, and it is worse than the world-swap hazard those two cite:

- `RemoveLevelsFromWorld` **resets the transaction buffer itself** (EditorLevelUtils.cpp:886-889) —
  it would destroy `RunEndpoint`'s own `FScopedTransaction` under its feet. That alone forces the
  self-managed bucket.
- then `GEditor->Cleanse` — a **forced GC** (:909),
- then a stale-reference sweep that is **`EPrintStaleReferencesOptions::Fatal`** when the buffer was
  reset (:929-937). Running that with our HTTP call frame still on the stack is precisely the
  situation that sweep exists to kill the editor over.

A deferred mutation cannot put its result in its own HTTP response, and dropping it would reproduce
the failure `docs/02_GOTCHAS.md` warns about ("Never silence a mutating call"). So each deferred verb
returns an **`opId`**, and `list_sublevels` reports every op in `ops[]` with
`{opId, endpoint, path, completed, ok, error, detail}` plus a `pendingOps` count. **Poll until your
opId has `completed:true`, then read its `ok`.** For `add_sublevel` and `set_sublevel_streaming` the
new `ULevelStreaming` object path arrives in `detail` (that endpoint *replaces* the object, so the
old `objectPath` dies — EditorLevelUtils.cpp:514-548).

### One silent no-op found and closed

`ULevelStreaming::SetShouldBeLoaded` has an **empty body** in the base class (LevelStreaming.cpp), and
`ULevelStreamingAlwaysLoaded::ShouldBeLoaded()` is hardcoded `return true`
(LevelStreamingAlwaysLoaded.h:27). Only `ULevelStreamingDynamic` honours the flag
(LevelStreamingDynamic.h:97). Since `add_sublevel` defaults to **alwaysloaded**, a naive
`set_sublevel_visibility {shouldBeLoaded:false}` would have returned `ok:true` having done literally
nothing. `set_sublevel_visibility` therefore **reads back every write** and compares:

- fields whose read-back matches the request go in `changed` (with the read-back value, never the echo),
- fields that did not take go in `ignored[{field, requested, actual, reason}]` — the reason names
  `set_sublevel_streaming {streamingClass:"dynamic"}` as the fix,
- and if **nothing** took, the call is an **error**, not `ok:true`.

### Spec deviations (and why)

- **`list_sublevels` accepts `world: "editor" | "pie"`** (plus `netMode` for PIE). The F-axis entry
  reserves `world` as editor-only. Extended instead of adding a second read endpoint, so there is
  exactly ONE poll endpoint for streaming state across both worlds — `00_ARCHITECTURE.md`'s
  one-source-of-truth rule. Unknown values error naming the accepted set.
- **`remove_sublevel` does NOT call `MakeLevelCurrent(persistent)` first**, which the spec's failure-mode
  list suggests. Following that advice would **add** hazard #2: `MakeLevelCurrent(ULevel*, false)` is
  itself a modal on a locked level, and `RemoveLevelsFromWorld` already does exactly this internally
  with `bEvenIfLocked=**true**` (EditorLevelUtils.cpp:869-873). The response reports `wasCurrent`.
- **`remove_sublevel` gains `discardUnsaved`** (default false) — see hazard #3.
- **`set_sublevel_visibility` gains `lightingScenario`**, exactly as the F-axis "Compositions" note
  asks (`ULevel::SetLightingScenario`, ENGINE_API Level.h:1090, bit at Level.h:547) rather than a new
  endpoint.
- **`create_sublevel` and `move_actors_to_sublevel` are NOT in this batch** — both are in the F-axis
  spec but outside this batch's stated scope. `create_sublevel`'s modal rule (hard-code
  `bInUseSaveAs=false`) is still unimplemented and still correct; whoever builds it should read the
  Phase-2 verdict first.
- **The deferred `add_sublevel` cannot report `alreadyPresent` from the tick** — but it never needs
  to: the already-present case is answered synchronously, without an engine call.

### Async contract (house rule: request + poll, never block)

| Endpoint | Blocks? | Poll with | Done when |
|---|---|---|---|
| `add_sublevel` | no (deferred) | `list_sublevels` | `ops[]` entry for your `opId` has `completed:true` |
| `remove_sublevel` | no (deferred) | `list_sublevels` | same; also `count` decremented by 1 |
| `set_sublevel_streaming` | no (deferred) | `list_sublevels` | same; new `objectPath` in the op's `detail` |
| `set_sublevel_visibility` | no | `list_sublevels` | that level's `pending:false` |
| `pie_load_level_instance` | no | `list_sublevels {"world":"pie"}` | entry whose `packageName` == `instanceName` has `loaded:true` |
| `pie_unload_level_instance` | no | `list_sublevels {"world":"pie"}` | no entry has `packageName` == `instanceName` |

`list_sublevels` also reports `loadedCount` / `visibleCount` / `pendingCount` and a single
`ready` boolean (`pendingCount == 0`) so a caller does not have to recombine counts.

### Live-proof curls (to run after the next build — NOT yet executed)

```bash
API() { curl -s -X POST "http://127.0.0.1:8791/api/$1" \
  -H "X-Mif-Token: ${MIF_BRIDGE_TOKEN:-dev}" -H "Content-Type: application/json" -d "$2"; }

# 0. registry is live and buckets are as declared (188 endpoints)
API self_audit '{}' | python -c "import sys,json; d=json.load(sys.stdin); b=d['transactionBuckets']; \
  print('count',d['endpointCount'],'contradictions',d['policyContradictions']); \
  print('readOnly   ', [e for e in b['readOnly']    if 'sublevel' in e or 'level_instance' in e]); \
  print('selfManaged', [e for e in b['selfManaged'] if 'sublevel' in e or 'level_instance' in e]); \
  print('transacted ', [e for e in b['transacted']  if 'sublevel' in e or 'level_instance' in e])"
# EXPECT contradictions [] ; readOnly ['list_sublevels'] ;
#        selfManaged ['add_sublevel','remove_sublevel','set_sublevel_streaming',
#                     'pie_load_level_instance','pie_unload_level_instance'] ;
#        transacted ['set_current_sublevel','set_sublevel_visibility']

# 1. baseline read on a SAVEABLE map (never a cooked base-game map)
API new_level '{}' ; sleep 2
API save_level_as '{"path":"/Game/Maps/MifStreamTest"}'
API list_sublevels '{}'
# EXPECT ok:true, count:0, ready:true, isPartitioned:false, persistent.packageName /Game/Maps/MifStreamTest

# 2. make a district map to attach, then go back to the host map
API new_level '{}' ; sleep 2
API save_level_as '{"path":"/Game/Maps/MifStreamDistrict"}'
API load_level    '{"path":"/Game/Maps/MifStreamTest"}' ; sleep 2

# 3. add_sublevel — deferred, then proved through ops[]
API add_sublevel '{"path":"/Game/Maps/MifStreamDistrict","streamingClass":"alwaysloaded"}'
# EXPECT ok:true, requested:true, deferred:true, opId:N, streamingClass:"alwaysloaded"
sleep 1; API list_sublevels '{}'
# EXPECT count:1 ; sublevels[0].packageName == /Game/Maps/MifStreamDistrict ;
#        sublevels[0].loaded true ; ops[] has {opId:N, completed:true, ok:true, detail:<objectPath>}

# 4. the modal guard, proven: re-adding must NOT call the engine
API add_sublevel '{"path":"/Game/Maps/MifStreamDistrict"}'
# EXPECT ok:true, alreadyPresent:true, changed:false — and the bridge STILL ANSWERS the next call
API list_sublevels '{}'   # EXPECT ok:true, count still 1  (proves no modal is up)

# 5. persistent-level guard (the other half of the same dialog)
API add_sublevel '{"path":"/Game/Maps/MifStreamTest"}'
# EXPECT ok:false, error contains "IS the persistent level"

# 6. unknown-parameter and unknown-value strictness
API add_sublevel '{"path":"/Game/Maps/MifStreamDistrict","streamingclass":"dynamic","wobble":1}'
# EXPECT ok:false, error names 'wobble' AND lists accepted keys (streamingclass IS accepted — case-insensitive)
API add_sublevel '{"path":"/Game/Maps/MifStreamDistrict","streamingClass":"lazy"}'
# EXPECT ok:false, error "unknown streamingClass 'lazy' — accepted: alwaysloaded, dynamic"
API set_sublevel_visibility '{"path":"/Game/Maps/MifStreamDistrict"}'
# EXPECT ok:false, "nothing to change — pass at least one of visible, shouldBeLoaded, shouldBeVisible, lightingScenario"
API remove_sublevel '{}'
# EXPECT ok:false, "path is required, e.g. \"/Game/Maps/TownDistrict\" (aliases: packagePath, level)"

# 7. set_current_sublevel — proved by where a spawned actor LANDS, not by the echo
API set_current_sublevel '{"path":"/Game/Maps/MifStreamDistrict"}'
# EXPECT ok:true, changed:true, currentLevel /Game/Maps/MifStreamDistrict
API spawn_actor_in_level '{"actorClass":"StaticMeshActor","label":"MifStreamProbe"}'
# EXPECT actor.actorPath rooted in ...MifStreamDistrict.MifStreamDistrict:PersistentLevel...
API set_current_sublevel '{"path":"persistent"}'   # EXPECT changed:true
API set_current_sublevel '{"path":"persistent"}'   # EXPECT ok:true, changed:false, "already the current level"

# 8. visibility — and the silent-no-op guard firing on an ALWAYSLOADED level
API set_sublevel_visibility '{"path":"/Game/Maps/MifStreamDistrict","visible":false}'
# EXPECT ok:true, changed.visible:false, sublevel.visible:false, ignored:[]
API list_level_actors '{"nameContains":"MifStreamProbe"}'
# EXPECT matched:1 — visibility is NOT existence
API set_sublevel_visibility '{"path":"/Game/Maps/MifStreamDistrict","shouldBeLoaded":false}'
# EXPECT ok:FALSE — "nothing was changed ... streaming class 'alwaysloaded' hardcodes ShouldBeLoaded()"
#        with ignored[0].field "shouldBeLoaded", requested false, actual true
API set_sublevel_visibility '{"path":"/Game/Maps/MifStreamDistrict","visible":true}'   # restore

# 9. set_sublevel_streaming — deferred, object identity CHANGES
API list_sublevels '{}' | python -c "import sys,json; print(json.load(sys.stdin)['sublevels'][0]['objectPath'])"
API set_sublevel_streaming '{"path":"/Game/Maps/MifStreamDistrict","streamingClass":"dynamic"}'
# EXPECT ok:true, deferred:true, opId:M, fromClass "alwaysloaded", toClass "dynamic", oldObjectPath <A>
sleep 1; API list_sublevels '{}'
# EXPECT sublevels[0].streamingClass "dynamic" ; objectPath != <A> ; ops[] {opId:M, completed:true, ok:true, detail:<B>}
API set_sublevel_streaming '{"path":"/Game/Maps/MifStreamDistrict","streamingClass":"dynamic"}'
# EXPECT ok:true, changed:false, "already this streaming class — no engine call was made"

# 9b. now that it is DYNAMIC, shouldBeLoaded actually takes
API set_sublevel_visibility '{"path":"/Game/Maps/MifStreamDistrict","shouldBeLoaded":false}'
# EXPECT ok:true, changed.shouldBeLoaded:false, ignored:[]
sleep 1; API list_sublevels '{}'    # EXPECT that level loaded:false
# 9c. and the crash guard is now reachable-but-refused
API set_sublevel_streaming '{"path":"/Game/Maps/MifStreamDistrict","streamingClass":"alwaysloaded"}'
# EXPECT ok:false, error cites check(Level), EditorLevelUtils.cpp:525 — and the EDITOR IS STILL ALIVE
API set_sublevel_visibility '{"path":"/Game/Maps/MifStreamDistrict","shouldBeLoaded":true}'; sleep 1

# 10. remove_sublevel — dirty guard, then the removal
API spawn_actor_in_level '{"actorClass":"StaticMeshActor","label":"MakesItDirty"}'   # after set_current_sublevel
API remove_sublevel '{"path":"/Game/Maps/MifStreamDistrict"}'
# EXPECT ok:false, "has UNSAVED changes ... or pass discardUnsaved:true"
API save_package '{"path":"/Game/Maps/MifStreamDistrict"}'
API remove_sublevel '{"path":"/Game/Maps/MifStreamDistrict"}'
# EXPECT ok:true, requested:true, deferred:true, opId:K, undoBufferReset:true, wasCurrent:<bool>
sleep 2; API list_sublevels '{}'
# EXPECT count:0 ; ops[] {opId:K, completed:true, ok:true, detail:"undo buffer was reset"}
API describe_package '{"path":"/Game/Maps/MifStreamDistrict"}'
# EXPECT existsOnDisk:true — detaching must NOT delete the asset
API remove_sublevel '{"path":"/Game/Maps/MifStreamTest"}'
# EXPECT ok:false, "cannot remove the persistent level"

# 11. THE REPORTED GAP — load/unload a level at RUNTIME during PIE, no Lua
API start_pie '{}'
until API pie_status '{}' | grep -q '"state":"running"'; do sleep 1; done
API list_sublevels '{"world":"pie"}'
# EXPECT ok:true, world:"pie", worldName != the editor world name
API pie_load_level_instance '{"path":"/Game/Maps/MifStreamDistrict","location":{"x":0,"y":0,"z":500}}'
# EXPECT ok:true, requested:true, instanceName:"<PIE-prefixed name>", sourcePath /Game/Maps/MifStreamDistrict,
#        objectPath:<...>, loaded:false (async!), pollWith list_sublevels {"world":"pie"}
sleep 2; API list_sublevels '{"world":"pie"}'
# EXPECT one entry whose packageName == instanceName, loaded:true, visible:true, pending:false, ready:true
API list_pie_actors '{"nameContains":"MifStreamProbe"}'
# EXPECT matched >= 1 — the streamed level's actors are LIVE in the running world
API pie_unload_level_instance '{"instanceName":"<instanceName from above>"}'
# EXPECT ok:true, requested:true, changed:true
sleep 2; API list_sublevels '{"world":"pie"}'
# EXPECT no entry with that packageName
API pie_unload_level_instance '{"instanceName":"NoSuchThing"}'
# EXPECT ok:false, "no streaming level 'NoSuchThing' in the PIE world"
API stop_pie '{}'

# 12. PIE endpoints refuse cleanly with no PIE session
API pie_load_level_instance '{"path":"/Game/Maps/MifStreamDistrict"}'
# EXPECT ok:false, "no PIE world — not playing. start_pie, then poll pie_status until state=='running'."

# 13. batch composition (the 'op' key must be tolerated by every guard here)
API batch '{"ops":[{"op":"list_sublevels"},{"op":"list_sublevels","world":"editor"}]}'
# EXPECT ok:true for both — proves RejectUnknownParams' central 'op' tolerance covers the new guards
```

### What `server.py` needs (owned by a later agent — NOT touched here)

Eight new `@mcp.tool` wrappers, all `_post`, **all above the `__main__` guard** (the
`spawn_actor_in_pie` lesson). Suggested signatures:

| Tool | Params |
|---|---|
| `list_sublevels` | `world: str = "editor"`, `netMode: str = "server"` |
| `add_sublevel` | `path: str`, `streamingClass: str = "alwaysloaded"`, `location: dict = None`, `rotation: dict = None` |
| `remove_sublevel` | `path: str`, `discardUnsaved: bool = False` |
| `set_sublevel_visibility` | `path: str`, `visible: bool = None`, `shouldBeLoaded: bool = None`, `shouldBeVisible: bool = None`, `lightingScenario: bool = None` |
| `set_current_sublevel` | `path: str` (`"persistent"` accepted) |
| `set_sublevel_streaming` | `path: str`, `streamingClass: str` |
| `pie_load_level_instance` | `path: str`, `location: dict = None`, `rotation: dict = None`, `visible: bool = True`, `netMode: str = "server"`, `nameOverride: str = ""`, `tempPackage: bool = False` |
| `pie_unload_level_instance` | `instanceName: str = ""`, `objectPath: str = ""`, `path: str = ""`, `netMode: str = "server"` |

Three things the wrappers **must** get right, or the tools will look broken:

1. **Optional booleans must be omitted, not defaulted.** `set_sublevel_visibility` distinguishes
   "caller passed false" from "caller omitted it" (`JHasAny`). A wrapper that sends
   `shouldBeLoaded: false` because the Python default is `False` turns every call into an
   unintended write. Send only the keys the caller actually supplied — `None` means drop the key.
2. **Do not send unknown keys.** Every endpoint here rejects them by name (`RejectUnknownParams`).
3. **Document the poll loop in the docstring.** `add_sublevel` / `remove_sublevel` /
   `set_sublevel_streaming` return `opId` and are NOT finished on return; `pie_load_level_instance` /
   `pie_unload_level_instance` are async streaming requests. The docstring should say "poll
   `list_sublevels` until `ops[]` shows your `opId` `completed:true`" (or, for PIE,
   `list_sublevels(world="pie")` until the instance appears/disappears), or agents will read
   `ok:true` as "done" and act on a world that has not changed yet.

**Verdict: COMPLETE (source only, unbuilt, no live proofs yet). MIF_DECL 188, MIF_BIND 188, sets
identical. Files changed: 1 new (MifBridgeStreaming.cpp), 2 edited (MifBridgeHandlers.h,
MifBridgeCommon.cpp), 1 appended (this file). No Build.cs change. server.py: 8 tools owed.**

## Batch G - foreign property get + parameter aliases

**Scope: `MifBridgeNodes.cpp` only.** `add_function_call`, `add_variable_get` and `add_variable_set`
all live in that file, so `MifBridgeNodes2/3/4/6.cpp` were checked and left untouched, and
`MifBridgeNodes5.cpp` (another owner) was not opened. No registry header changed - no endpoint was
added or removed, so `MIF_DECL` / `MIF_BIND` are untouched at 188.

---

### GAP 2 - "no way to add a property-get node targeting another object"

#### The root cause is NOT what the report says, and the difference matters

The report says *"add_variable_get only handles the blueprint's own variables"*. It does not, and it
has not for some time. The plugin already resolved a foreign member through `targetClass`, and
`FindPropertyByName` walks the `PropertyLink` chain, which includes every inherited **native**
`UPROPERTY`. Proven live against the running editor **before any change in this batch**:

```bash
curl -s -X POST http://127.0.0.1:8791/api/create_blueprint -H "X-Mif-Token: dev" \
  -d '{"path":"/Game/MifScratch/BP_MifGapG","parentClass":"Actor"}'

curl -s -X POST http://127.0.0.1:8791/api/add_variable_get -H "X-Mif-Token: dev" -d '{
  "graphId":"/Game/MifScratch/BP_MifGapG.BP_MifGapG::EventGraph",
  "var":"ChildActorClass","targetClass":"ChildActorComponent","x":300,"y":200}'
```

```json
{"ok":true,"nodeGuid":"FDEE3AA0...","node":{"class":"K2Node_VariableGet","title":"Get ChildActorClass",
 "pins":[{"name":"ChildActorClass","direction":"output","type":{"category":"class","subObject":"Actor"}},
         {"name":"self","direction":"input","type":{"category":"object","subObject":"ChildActorComponent"}}]}}
```

That is a correct non-self member reference with a visible `self` (Target) pin of type
`ChildActorComponent`. The feature existed. **Four separate things hid it**, and only the last is a
plugin bug:

1. **`server.py` never exposed it.** `server.py:203` is
   `def add_variable_get(graph_id: str, var: str, x: int = 0, y: int = 0)` and its docstring reads
   *"Add a 'get variable' node for a **self member variable**."* `server.py:209` is the same for
   `add_variable_set`. There is no `target_class` parameter, so an MCP client is *told* the
   capability does not exist. This is the whole of the reported gap for anyone driving the bridge
   through MCP, and it is not fixable from the plugin - see "what server.py needs" below.
2. **The C++ class name did not resolve.** `ResolveClass` (MifBridgeCommon.cpp:1230) looks a bare
   name up with `FindFirstObject<UClass>`, which matches the *UObject* name (`ChildActorComponent`),
   never the C++ spelling. Proven live:
   `{"targetClass":"UChildActorComponent"}` -> `{"ok":false,"error":"targetClass not found: 'UChildActorComponent' ..."}`.
   The natural key for a native property owner is exactly the C++ name.
3. **A wrong parameter name failed silently.** `{"property":"ChildActorClass","targetClass":...}`
   returned only `{"ok":false,"error":"var is required"}` - no list of accepted spellings, and the
   `property` key was dropped without comment.
4. **`add_variable_set` accepted properties Blueprints may not write.** This one is a real defect,
   found while verifying the above, and it is the reason the "change guard" work kept producing
   graphs that would not compile.

#### The real defect: `add_variable_set` on a BlueprintReadOnly property

`UChildActorComponent::ChildActorClass` is `UPROPERTY(EditAnywhere, BlueprintReadOnly, ...)`
(ChildActorComponent.h:116). The old guard in `PointAtExternalMember` tested only
`CPF_BlueprintVisible`, which a BlueprintReadOnly property passes. So a **Set** node on it was
created and reported `ok:true` with a full pin list. Proven live, pre-change:

```bash
curl -s -X POST http://127.0.0.1:8791/api/add_variable_set -H "X-Mif-Token: dev" -d '{
  "graphId":"/Game/MifScratch/BP_MifGapG.BP_MifGapG::EventGraph",
  "var":"ChildActorClass","targetClass":"ChildActorComponent","x":700,"y":200}'
# -> {"ok":true,"nodeGuid":"F0D5D8B4...","node":{"class":"K2Node_VariableSet", ... 5 pins ... }}
```

Then, after wiring its exec pin to `Event BeginPlay` and compiling:

```json
{"ok":false,"numErrors":3,"messages":[
 {"severity":"error","text":"ChildActorComponent.ChildActorClass is not blueprint writable.  Set ChildActorClass"},
 {"severity":"error","text":"This blueprint (self) is not a ChildActorComponent, therefore ' Target ' must have a connection."},
 {"severity":"error","text":"Variable node  Set ChildActorClass  uses an invalid target. ..."}]}
```

**The trap is that the error is deferred.** Compiling with the Set node *unwired* returned
`numErrors: 0` - the Kismet compiler prunes isolated nodes before `ValidateNodeDuringCompilation`
runs. So the bad node sits in the graph reporting healthy until someone finally wires it, by which
time the `ok:true` that created it is long gone. A response that says `ok` and produces a node that
cannot compile is the same failure class as `ok:true` having done nothing.

#### The fix

All of it in `MifBridgeNodes.cpp`.

- **Any accessible `FProperty` resolves, native included.** `FindAnyProperty` (`FindPropertyByName`,
  falling back to `FindFProperty`) against the skeleton-preferred class. `FName` comparison is
  case-insensitive, so `childactorclass` resolves too.
- **The member reference is now built by the engine's own API.** `UK2Node_Variable::SetFromProperty`
  (K2Node_Variable.cpp:87-91) replaces the hand-rolled `SetExternalMember` + GUID lookup. It does two
  things the hand-rolled path did not do together:
  - `FMemberReference::SetFromField<FProperty>(Property, /*bSelfContext*/ false, OwnerClass)`
    (MemberReference.h:108-142) records the owning class as the member parent, clears `bSelfContext`,
    and resolves the Blueprint member GUID itself - so a rename on a target *Blueprint* does not
    break the reference, while a *native* property (no GUID) stays a stable name-only reference.
  - it sets `SelfContextInfo = ESelfContextInfo::NotSelfContext`, which the old path left at its
    default. That field is half of the self-pin decision: `UK2Node_Variable::CreatePinForSelf`
    (K2Node_Variable.cpp:142-213) computes
    `bSelfTarget = IsSelfContext() && (NotSelfContext != SelfContextInfo)` at :151, then at :200-206
    creates the `self` pin (friendly name "Target") and hides it **only** when `bSelfTarget`. A
    non-self reference therefore always exposes a visible Target pin - that pin is what makes this a
    foreign-property access at all. The pin's class is normalised to the property's owning class at
    :162-165, so passing a derived `targetClass` still yields a correctly-typed Target.
- **Accessibility is checked against the compiler's own predicates**, so "accepted here" == "compiles
  there":
  - read -> `FBlueprintEditorUtils::IsPropertyReadableInBlueprint` (BlueprintEditorUtils.cpp:8810),
    the same call `UK2Node_VariableGet::ValidateNodeDuringCompilation` makes (K2Node_VariableGet.cpp:425-457)
  - write -> `FBlueprintEditorUtils::IsPropertyWritableInBlueprint` (BlueprintEditorUtils.cpp:8786),
    the same call `UK2Node_VariableSet::ValidateNodeDuringCompilation` makes (K2Node_VariableSet.cpp:421-457)

  Not re-implemented as `CPF_` arithmetic on purpose: the *Private* verdict depends on the `MD_Private`
  metadata **and** on whether the owning class was generated by *this* blueprint, which no flag test
  can see. The engine's palette filter (`UEdGraphSchema_K2::CanUserKismetAccessVariable`,
  EdGraphSchema_K2.cpp:1228) additionally hides category-hidden properties; that one is deliberately
  **not** used, because a category-hidden property still compiles and refusing it would refuse
  something that works.
- **The refusal says WHY and names the way forward.** A write refusal looks for a BlueprintCallable
  `Set<Prop>` and names it - which for the motivating case is exactly right, since
  `SetChildActorClass` is `UFUNCTION(BlueprintCallable)` (ChildActorComponent.h:92-95):

  > `property 'ChildActorClass' on 'ChildActorComponent' is BlueprintReadOnly - graphs may READ it but never write it: a Set node here compiles to the error "ChildActorComponent.ChildActorClass is not blueprint writable". Use add_variable_get for the read. 'SetChildActorClass' IS BlueprintCallable though - add_function_call {class:"ChildActorComponent", function:"SetChildActorClass"} does what you want.`

  A "property not found" now lists near-miss names on the class, or the count of Blueprint-visible
  properties when nothing overlaps - which distinguishes "wrong name" from "wrong class" without a
  second round-trip.
- **The same gate now covers the SELF path.** A self *member* can be an inherited native property, so
  `add_variable_set {var:"SomeInheritedReadOnlyProp"}` hit the identical trap with no `targetClass`
  in sight. Gated only when the property actually resolves on the skeleton - a variable added moments
  ago that is not on the skeleton yet keeps its existing warning rather than becoming a hard refusal.
  Locals are exempt (they are always writable and do not live on a UClass).
  **Behaviour change worth knowing:** `add_variable_set` on a component variable, or on the
  blueprint's own BlueprintReadOnly variable, is now refused. Those calls previously returned
  `ok:true` and produced a graph that errored at compile.
- **The C++ class prefix resolves.** `ResolveClassAllowingCppPrefix` retries once with a leading
  `U`/`A` stripped, but only after the exact name has already failed - so it cannot change any
  currently-succeeding resolution. Used by `add_variable_get/set`, `add_function_call`,
  `add_parent_call`, `add_override_event`. Kept file-local with an eviction clause: if a second file
  needs it, promote it into `ResolveClass` rather than copying it.
- **One body for get and set.** `DoAddVariableNode(In, Out, EMemberAccess)` replaces two
  near-identical handlers that differed only in node class, direction and the word "get"/"set". That
  duplication is exactly how the read-only check would have landed on the getter and not the setter.
- **Nothing mutates until every check has passed.** All resolution and validation happens before the
  first `Modify()`/`NewObject`, so a refused call leaves the blueprint and the undo stack untouched.
- **Structured, checkable output** added to both verbs:
  `scope` (`self`|`local`|`external`), `access` (`read`|`write`), `var`, `pinCount`, `hasTargetPin`,
  `targetPin`, and for external references `memberClass`, `memberProperty`, `native`,
  `blueprintReadOnly`. `hasTargetPin:true` + `scope:"external"` is the numeric assertion that the
  node really targets another object.

---

### GAP 5 - parameter-name aliases + strict unknown-key rejection

No parameter was renamed. Every existing spelling still works; the aliases are additive
(`JStrAny` / `JBoolAny` house pattern). Every verb in this file now also rejects unknown keys through
the shared `RejectUnknownParams`, which lists the accepted set in the error and tolerates batch's
`op` routing key centrally (MifBridgeCommon.cpp:669). 18 guard sites covering 20 endpoints; before
this batch `MifBridgeNodes.cpp` had none.

`ResolveNodeField` (MifBridgeCommon.cpp:1036) already accepts `nodeGuid` / `node` / `guid` / `nodeId`
for a single-node field; those are now on the accepted lists so the guard cannot reject a spelling the
resolver would have honoured. Endpoints with **two** node parameters keep distinct names on purpose
(docs/02_GOTCHAS.md:18) - aliasing there would let one key satisfy both roles - so those get a
`KeyNote` naming the right key instead of an alias.

| Endpoint | Canonical | Aliases added | KeyNotes (rejected, with the reason) |
|---|---|---|---|
| `add_function_call` | `class` | `cls`, `className`, `targetClass`, `ownerClass` | `graph`, `target`, `args`, `pure` |
| | `function` | `functionName`, `func`, `method` | |
| | `asMessage` | `message` (pre-existing) | |
| `add_variable_get` / `add_variable_set` | `var` | `name`, `variable`, `varName`, `property`, `propertyName`, `member` | `graph`, `target`, `value`, `scope` |
| | `targetClass` | `class`, `cls`, `className`, `ownerClass`, `objectClass` | |
| `add_macro_instance` | `macroGraph` | `macro`, `macroName`, `name` | `graph` |
| | `macroPath` | `macroLibrary`, `library`, `path` | |
| `add_override_event` | `event` | `eventName`, `name`, `function`, `functionName` | `graphId` (an override always lands in the event graph) |
| | `interfaceOrParent` | `class`, `cls`, `className`, `parentClass`, `interface`, `ownerClass`, `targetClass` | |
| | `callParent` | `addParentCall`, `withParentCall` | |
| | `blueprintId` | `path` (pre-existing via `ResolveBlueprintField`) | |
| `add_parent_call` | `parentClass` | `class`, `cls`, `className`, `parent`, `ownerClass`, `targetClass` | `graph` |
| | `function` | `functionName`, `func`, `method`, `name` | |
| `add_cast` | `targetClass` | `cls`, `className` (adds to existing `class`, `castTo`, `to`, `targetType`) | `graph`, `pure`, `object` |
| `add_pin` | `name` | `pin`, `pinName` (pre-existing) | `confirm` (add_pin is additive) |
| | `type` | `pinType` | |
| | `direction` | `dir` | |
| | `default` | `defaultValue`, `value` | |
| `remove_pin` | `pin` | `pinName`, `name` (pre-existing) | |
| | `direction` | `dir` | |
| `disconnect_pin` | `pin` | `pinName`, `name` | |
| `set_pin_default` | `pin` | `pinName`, `name` | |
| | `value` | `default`, `defaultValue` | |
| `connect_pins` / `reconnect_pin` | `srcPin` | `sourcePin`, `fromPin` | `from`, `fromNode`, `sourceNode` -> `srcNode`; `to`, `toNode`, `destNode`, `targetNode` -> `dstNode` |
| | `dstPin` | `destPin`, `toPin` | |
| `splice_into_exec` | `afterPin` | `afterExecOut` | `beforeNode`, `node` |
| | `insertExecIn` | `insertIn`, `execIn` | |
| | `insertExecOut` | `insertOut`, `execOut` | |
| `batch` | `blueprintId` | `path` | `operations` -> `ops`; `graphId` belongs on each op |
| `move_node` / `remove_node` / `refresh_node` | `nodeGuid` | `node`, `guid`, `nodeId` (pre-existing) | |
| `add_branch` | - | - | `graph`, `condition` |
| `add_get_array_item` | - | - | `graph`, `index`, `array` |

Every key `server.py` currently posts to these 20 endpoints was cross-checked against the accepted
lists before the guards went in - all present, so no existing MCP tool call can start failing. The
recipe that forwards its own payload into one of them
(`H_recipe_override_and_call_parent` -> `H_add_override_event`, MifBridgeRecipes.cpp:309) passes only
accepted keys.

---

### Live proof

The pre-change captures above are real - they are why the root cause reads the way it does. The
edits are **source only, unbuilt** (the editor is running; building would require closing it), so
these are the exact calls that verify the batch once the DLL is rebuilt. Scratch asset
`/Game/MifScratch/BP_MifGapG` already exists, compiles 0/0, and holds the Get node from the
pre-change proof.

```bash
T='-H "X-Mif-Token: dev" -H "Content-Type: application/json"'
G='/Game/MifScratch/BP_MifGapG.BP_MifGapG::EventGraph'

# 1. GAP 2 core - foreign NATIVE property get. Expect ok:true and, new in Batch G,
#    scope:"external", access:"read", hasTargetPin:true, targetPin:"self",
#    memberClass:"/Script/Engine.ChildActorComponent", native:true, blueprintReadOnly:true
curl -s -X POST http://127.0.0.1:8791/api/add_variable_get -H "X-Mif-Token: dev" \
  -d "{\"graphId\":\"$G\",\"var\":\"ChildActorClass\",\"targetClass\":\"ChildActorComponent\",\"x\":300,\"y\":260}"

# 2. Same thing with the C++ class name. Was "targetClass not found"; now resolves identically.
curl -s -X POST http://127.0.0.1:8791/api/add_variable_get -H "X-Mif-Token: dev" \
  -d "{\"graphId\":\"$G\",\"var\":\"ChildActorClass\",\"targetClass\":\"UChildActorComponent\",\"x\":300,\"y\":330}"

# 3. Same thing with the alias keys. property/cls both honoured.
curl -s -X POST http://127.0.0.1:8791/api/add_variable_get -H "X-Mif-Token: dev" \
  -d "{\"graphId\":\"$G\",\"property\":\"ChildActorClass\",\"cls\":\"ChildActorComponent\",\"x\":300,\"y\":400}"

# 4. The defect. Was ok:true + a deferred compile error; now a refusal naming the reason
#    AND naming SetChildActorClass.
curl -s -X POST http://127.0.0.1:8791/api/add_variable_set -H "X-Mif-Token: dev" \
  -d "{\"graphId\":\"$G\",\"var\":\"ChildActorClass\",\"targetClass\":\"ChildActorComponent\"}"

# 5. The refusal's own suggestion must work.
curl -s -X POST http://127.0.0.1:8791/api/add_function_call -H "X-Mif-Token: dev" \
  -d "{\"graphId\":\"$G\",\"class\":\"ChildActorComponent\",\"function\":\"SetChildActorClass\",\"x\":700,\"y\":260}"

# 6. GAP 5 - cls is now an alias, not a silent miss. Was
#    "function 'PrintString' not found on class 'SKEL_BP_MifGapG_C'".
curl -s -X POST http://127.0.0.1:8791/api/add_function_call -H "X-Mif-Token: dev" \
  -d "{\"graphId\":\"$G\",\"cls\":\"KismetSystemLibrary\",\"function\":\"PrintString\",\"x\":100,\"y\":600}"

# 7. An unknown key must name itself AND list the accepted set.
curl -s -X POST http://127.0.0.1:8791/api/add_variable_get -H "X-Mif-Token: dev" \
  -d "{\"graphId\":\"$G\",\"varName2\":\"X\",\"targetClass\":\"ChildActorComponent\"}"
# expect: unrecognised parameter 'varName2' - accepted: graphId, var (aliases: name, variable,
#         varName, property, propertyName, member), targetClass (aliases: class, cls, className,
#         ownerClass, objectClass), x, y

# 8. Not-found now names near misses instead of only pointing at describe_class.
curl -s -X POST http://127.0.0.1:8791/api/add_variable_get -H "X-Mif-Token: dev" \
  -d "{\"graphId\":\"$G\",\"var\":\"ChildActor\",\"targetClass\":\"ChildActorComponent\"}"

# 9. Composition still works (the 'op' key must stay tolerated inside batch).
curl -s -X POST http://127.0.0.1:8791/api/batch -H "X-Mif-Token: dev" -d "{\"ops\":[
  {\"op\":\"add_variable_get\",\"graphId\":\"$G\",\"var\":\"ChildActorClass\",\"targetClass\":\"ChildActorComponent\",\"x\":300,\"y\":470},
  {\"op\":\"add_branch\",\"graphId\":\"$G\",\"x\":700,\"y\":470}],\"compileAtEnd\":true}"

# 10. Nothing left behind by a refusal, and the asset still compiles clean.
curl -s -X POST http://127.0.0.1:8791/api/compile -H "X-Mif-Token: dev" \
  -d '{"blueprintId":"/Game/MifScratch/BP_MifGapG"}'
```

---

### What `server.py` needs (not touched - a later agent owns it)

1. **`add_variable_get` (server.py:203) and `add_variable_set` (server.py:209) need `target_class`.**
   This is the actual reported gap. Both signatures are
   `(graph_id: str, var: str, x: int = 0, y: int = 0)` and both docstrings say *"for a self member
   variable"*, so an MCP client is told a capability that exists does not. Needed:
   `target_class: str = None` -> `_post(..., targetClass=target_class)` (`_post` drops `None`, so
   omitting it keeps the current self/local behaviour byte-for-byte). The docstring should say that
   with `target_class` the node reads/writes a property on **another object**, that this works for
   **native** UPROPERTYs (`ChildActorComponent` / `ChildActorClass`), that the node grows a `self`
   ("Target") input pin which must be wired with `connect_pins`, and that `add_variable_set` refuses
   BlueprintReadOnly properties by design.
2. **Return the new fields.** `scope`, `access`, `hasTargetPin`, `targetPin`, `memberClass`,
   `memberProperty`, `native`, `blueprintReadOnly`, `pinCount` are pass-through, but the docstrings
   should tell an agent to assert `hasTargetPin` before trying to wire the Target pin.
3. **The class parameter naming.** `add_function_call` already takes `cls` and maps it to `class` on
   the wire (server.py:198); that mapping is now redundant but harmless - `cls` is accepted directly.
   No change required.
4. **Do not send unknown keys.** Every endpoint in `MifBridgeNodes.cpp` now rejects them by name.
   Keep using `_post`'s `None`-dropping rather than sending defaulted keys.
5. **No new endpoints**, so no new `@mcp.tool()` is required for this batch.

**Verdict: COMPLETE (source only, unbuilt; the pre-change captures above are live, the post-build
verification script is section "Live proof"). Files changed: 1 edited
(`Source/MifBridge/Private/MifBridgeNodes.cpp`), 1 appended (this file). No registry change - MIF_DECL
188, MIF_BIND 188, unchanged. No Build.cs change. server.py: 2 tools owed (`add_variable_get`,
`add_variable_set` need `target_class`).**

---

## Batch J — inherited component overrides (the Details-panel write path)

**The gap.** The bridge could edit a Blueprint's OWN component templates
(`list_components` → `templatePath` → `set_property`, docs/02_GOTCHAS.md §5d) and it could edit
Class Defaults on the CDO. It had **no route at all** for the most common real edit in a child
Blueprint: select a component that came from the PARENT Blueprint, change a value. That is the
single most-wanted gap in the audit, and it is what the Details panel does through
`UInheritableComponentHandler`.

**Source file:** `Source/MifBridge/Private/MifBridgeInherited.cpp` (new, CRLF, 1089 lines).
Nothing else was touched — the registry wiring and MCP tools are listed below for the main session.

### Mechanism

A child Blueprint does not own the inherited component's template; the parent's SCS does. Editing
it stores a **delta**, not a copy:

* `UInheritableComponentHandler` (ICH) holds one `FComponentOverrideRecord` per overridden
  component — `{ComponentKey, ComponentClass, ComponentTemplate}` —
  `InheritableComponentHandler.h:68-89`, array at `:169-170`.
* `FComponentKey` identifies the **parent's** `USCS_Node` by `{OwnerClass, SCSVariableName,
  AssociatedGuid}` (`InheritableComponentHandler.h:57-65`); the guid is the node's `VariableGuid`
  (`FComponentKey::FComponentKey(const USCS_Node*)`, `InheritableComponentHandler.cpp:561-570`).
* `CreateOverridenComponentTemplate` (`InheritableComponentHandler.cpp:104-198`) `NewObject`s a
  template into the child's BPGC **with the parent's template as its archetype** and flags
  `RF_ArchetypeObject | RF_Public | RF_InheritableComponentTemplate`. Only the properties you then
  change diverge from the parent.
* **No compile is needed for it to take effect.** `USCS_Node::GetActualComponentTemplate`
  (`SCS_Node.cpp:29-54`) walks the child's ICH chain at *instancing* time and returns the override
  when a record matches, falling back to `ComponentTemplate` otherwise.

Canonical editor path, read verbatim from
`Engine/Source/Editor/SubobjectDataInterface/Private/SubobjectData.cpp:145-170`
(`FSubobjectData::GetObjectForBlueprint`) and duplicated in `SSCSEditor.cpp:1544-1569`:

```cpp
if (IsComponent() && bCanEdit && !IsNativeComponent() && IsInheritedSCSNode())
{
    FComponentKey Key(GetSCSNode());
    const bool bBlueprintCanOverrideComponentFromKey = Key.IsValid()
        && Blueprint && Blueprint->ParentClass
        && Blueprint->ParentClass->IsChildOf(Key.GetComponentOwner());
    if (bBlueprintCanOverrideComponentFromKey) {
        UInheritableComponentHandler* ICH = Blueprint->GetInheritableComponentHandler(true);
        OverriddenComponent = ICH->GetOverridenComponentTemplate(Key);
        if (!OverriddenComponent) OverriddenComponent = ICH->CreateOverridenComponentTemplate(Key);
    }
}
```

**Config gate.** `UBlueprint::GetInheritableComponentHandler` returns **null outright** when
`[Kismet] bEnableInheritableComponents` is false (`Blueprint.cpp:2062-2068`; `BaseEngine.ini:1947`
ships it `true`), and `USCS_Node::GetActualComponentTemplate` consults the same helper — so with it
off, an override would be written and then *ignored at instancing*. Both mutators report that as a
named failure rather than dereferencing null.

### The three corrections this batch encodes

A proposal circulating before this work got three things wrong. Each is now a comment in the source
with its citation, because each would have produced a silent or destructive failure.

**1. There is no `FComponentKey(FName)`.** The struct has exactly three constructors — default,
`FComponentKey(const USCS_Node*)` and `FComponentKey(UBlueprint*, const FUCSComponentId&)`
(`InheritableComponentHandler.h:23-30`). A first-time override must therefore be keyed off the
parent's real `USCS_Node`, found by walking `Blueprint->ParentClass` up the
`UBlueprintGeneratedClass` chain and asking each class's `SimpleConstructionScript->FindSCSNode`.
That walk is the engine's own — `Editor.cpp:1260-1272`. `UInheritableComponentHandler::FindKey(FName)`
(`InheritableComponentHandler.cpp:509-519`) iterates `Records`, so it can only ever find a key for
an override that **already exists**; it is used here strictly as a fast path in the read verb, never
as the way to create one.

Starting the walk at `ParentClass` (not `GeneratedClass`) is what keeps the child's own SCS out of
the search. Each level is a `UBlueprintGeneratedClass`, which still carries its
`SimpleConstructionScript` even when the class is **cooked and has no `UBlueprint` behind it** — the
normal case in this project, where mod blueprints derive from cooked game blueprints. `FindSCSNode`
matches on either the variable name or the template's `FName`
(`SimpleConstructionScript.cpp:988-1003`), so both `Influence` and `Influence_GEN_VARIABLE` resolve.

**2. Never `ImportText` straight into the live property address** — docs/01_POSTMORTEMS.md PM-003.
`ImportText_Direct` parses **in place** and can consume/zero the destination before deciding the
text is invalid, so a failed write **destroys** the value it failed to set. Every write in this file
imports into a scratch buffer seeded from the current value (`InitializeValue` →
`CopyCompleteValue` from the live address → `ImportText_Direct` into the scratch) and publishes with
`CopyCompleteValue` only after the parse succeeded. Seeding from the current value preserves
partial-struct-literal semantics (`(X=5)` leaves Y and Z alone), matching the Details panel;
`GetSize()` spans `ArrayDim`, so C-array UPROPERTYs round-trip. `PostEditChangeProperty` fires only
on a write that actually happened.

This is the same bracket `set_property` uses in `MifBridgeNodes5.cpp`. It is **duplicated, not
shared**, because that file and `MifBridgeHandlers.h` were owned by another workstream this session.
**Eviction clause** (on the `JIntAny` precedent, `MifBridgeHandlers.h:53-56`): the moment that fence
lifts, promote ONE copy of `ResolvePropertyPath` + `NormalizeBoolLiteral` to
`MifBridgeHandlers.h` / `MifBridgeCommon.cpp` and delete both local sets. Two copies of a PM-003-safe
write bracket is exactly the drift this codebase fixes on sight.

**3. Native components are explicitly NOT an ICH case.** The editor's own guard is
`!IsNativeComponent()` (`SubobjectData.cpp:148`; the predicate is `SubobjectData.cpp:814-822` —
`CreationMethod == EComponentCreationMethod::Native && GetSCSNode() == nullptr`). A C++ component
declared on a native parent (`ACharacter::Mesh`, `::CharacterMovement`, `::CapsuleComponent`)
already exists as the **child Blueprint's own CDO subobject**, so the child edits it directly and
there is nothing to delta. Feeding one to the ICH would silently write the wrong object.

The endpoints detect this and emit the exact alternative path. The path form matters and is *not*
guessable:

```
/Game/<Pkg>/<Asset>.Default__<Class>_C:<SUBOBJECT NAME>
```

The trailing name is the **subobject's** name from the C++ constructor, **not the property name**.
Verified live on the bridge (2026-07-28, `list_object_properties` on the child CDO):

| property (Details panel name) | actual CDO subobject path |
|---|---|
| `Mesh` | `…NPC_MifAmbient.Default__NPC_MifAmbient_C:CharacterMesh0` |
| `CharacterMovement` | `…Default__NPC_MifAmbient_C:CharMoveComp` |
| `CapsuleComponent` | `…Default__NPC_MifAmbient_C:CollisionCylinder` |
| `ArrowComponent` | `…Default__NPC_MifAmbient_C:Arrow` |

Nobody guesses `CharacterMesh0` from `Mesh`. So the handler **resolves the object and emits its real
`GetPathName()`** rather than composing a path from the name the caller passed — it looks the
component up twice, first by property name (what `describe_class` and the Details panel show), then
by subobject name (what appears in the path), and reports which match hit via `nativeMatchedBy`.
This is the same class of fix as `list_components` emitting `templatePath` instead of leaving
callers to guess `_GEN_VARIABLE`.

### Endpoints

#### 1. `get_inherited_component` — READ-ONLY

`{ blueprint (blueprintId|path|asset), component (componentName|name) }`

The discovery verb: call it first to learn which of four routes applies. Read-only in the *strong*
sense — it asks for the handler with `bCreateIfNecessary=false`
(`UBlueprint::GetInheritableComponentHandler`, `Blueprint.h:1025`), so merely asking "is this
overridden?" never mints an ICH on the asset. **That is the whole reason it exists separately from
the mutator**: the engine's accessor is get-or-*create*, so any naive "just checking" call written
against the raw API dirties the Blueprint.

Returns `origin` ∈ `parentBlueprintSCS | native | ownSCS | notFound`, plus `parentClass`,
`ownerClass`, `componentClass`, `canOverride` + `canOverrideReason`, `overrideExists`,
`existingOverrideCount`, `overrideTemplatePath`, `parentTemplatePath`, `nativeCdoPath` +
`nativeMatchedBy` + `creationMethod` (native case), `ownTemplatePath` (own-SCS case),
`inheritableComponentHandlerPath`, and a `route` + `hint` naming the exact next call. On `notFound`
it emits `availableComponents[]` (name + origin + class across own SCS, the whole parent chain, and
the CDO's native subobjects) — "component 'X' not found" with no list is the error that costs an
agent three round trips guessing spellings.

Cited API: `USimpleConstructionScript::FindSCSNode` (`SimpleConstructionScript.h:107`),
`GetAllNodes` (`:77`, `ENGINE_API`, `WITH_EDITOR`), `USCS_Node::GetVariableName`
(`SCS_Node.h:150`, `FORCEINLINE` — no export needed), `UInheritableComponentHandler::FindKey(FName)`
(`:138`), `GetOverridenComponentTemplate` (`:140`), `GetAllTemplates` (`:148`, inline const),
`UObject::GetDefaultSubobjectByName` / `GetDefaultSubobjects` (`Object.h:199` / `:193`).

#### 2. `override_inherited_component` — TRANSACTED

`{ blueprint, component, properties?: {name → value}, confirm? }`

Get-or-creates the override template via the canonical path, optionally applies a `properties`
object with the PM-003 scratch bracket, and returns `overrideTemplatePath` plus per-property
`{applied, changed, before, after, wanted, reason}`. Omit `properties` and it just mints the
override and hands back the path for the existing `set_property`.

* **Refuses the native case** with the CDO subobject path in the error text, and the own-SCS case
  with that template's path.
* **Anti-silence**, the single most important invariant in this codebase right now: `applied` is
  true **only when the object, re-exported after the publish, holds exactly what the import
  produced**. `ExportTextItem_Direct` is called before the write and again after
  `PostEditChangeProperty`, because a component can clamp, snap or reject a value in its
  `PostEditChangeProperty` — trusting `ImportText`'s non-null return would report success for a
  write that never landed. `changed` is reported **separately**, so writing a value the property
  already had shows up as `applied:true, changed:false` with an explicit note, rather than being
  mislabelled a failure. If any property fails, the whole call returns `ok:false` naming them, with
  the full per-property rows still in the payload (`Fail` only sets `ok`/`error`).
* `EditConst` properties are rejected **up front**, by name, instead of after a write that silently
  does nothing.
* JSON numbers are converted to integer text when integral — a JSON `250` must not reach an int
  property as `"250.000000"`, which `FIntProperty::ImportText` rejects for a reason invisible in the
  caller's request. Nested JSON objects/arrays are rejected with the UE literal form to use instead.
* `confirm` is **accepted but not required** (minting an override is reversible). It is *honoured*
  rather than ignored: `confirm:false` is an explicit refusal. A parameter the handler silently
  drops is the #1 bug class here (03_GAPS_AND_RISKS.md §7.1).
* Writing `bEditableWhenInherited` attaches a `warning`:
  `FBlueprintEditorUtils::HandleDisableEditableWhenInherited` (`BlueprintEditorUtils.cpp:9917-9933`)
  **removes ICH override records on derived classes** when it is turned off, and
  `UActorComponent::CanEditChange` (`ActorComponent.cpp:2203-2207`) then locks the component in
  every child. Surfaced, not blocked — it is a legitimate thing to set.

Cited API: `UBlueprint::GetInheritableComponentHandler(bool)` (`Blueprint.h:1025`),
`CreateOverridenComponentTemplate` (`InheritableComponentHandler.h:108`),
`GetOverridenComponentTemplate` (`:140`), `FComponentKey(const USCS_Node*)` (`:27`),
`IsValid()` (`:44-47`), `GetComponentOwner()` (`:53`),
`FProperty::ImportText_Direct` / `ExportTextItem_Direct` (`UnrealType.h:455`).

#### 3. `revert_inherited_component` — TRANSACTED, `confirm:true` required

`{ blueprint, component, confirm:true }`

`RemoveOverridenComponentTemplate` (`InheritableComponentHandler.h:109`) so the child falls back to
the parent's value. Confirm-gated because it discards **every** property overridden on that
component in one step — there is no per-property undo inside the record. Uses
`bCreateIfNecessary=false`, so reverting when nothing is overridden does not mint a handler; it
fails naming the parent template it already reads from.

**Known caveat, stated rather than hidden.** `RemoveOverridenComponentTemplate`
(`InheritableComponentHandler.cpp:200-211`) calls `MarkAsGarbage()` on the template, and
`MarkAsGarbage` is an object-flag operation (`UObjectBaseUtility.h:263`) that the transaction buffer
does **not** record. A Ctrl-Z restores the `Records` array but the restored record points at a
garbage-flagged template. The engine anticipates exactly this —
`CreateOverridenComponentTemplate`'s `if (!::IsValid(NewComponentTemplate))` branch
(`InheritableComponentHandler.cpp:161-169`, commented *"HACK … we mark them pending kill so we can
identify that situation here"*) clears the flag and re-copies from the archetype — so the reliable
recovery is to call `override_inherited_component` again rather than to rely on undo alone. The
response says so in a `note` field.

### Buckets, and why

| endpoint | bucket |
|---|---|
| `get_inherited_component` | **read-only** |
| `override_inherited_component` | **transacted** (RunEndpoint's blanket transaction) |
| `revert_inherited_component` | **transacted** |

Neither mutator is self-managed. "It edits a Blueprint asset" is not by itself a reason to be
self-managed in this codebase — that bucket exists for handlers that run a full
`FKismetEditorUtilities::CompileBlueprint` (`MifBridgeCommon.cpp:374-378`), because class
reinstancing captured by an undo step restores a dead CDO and crashes. Neither of these compiles;
every step is `Modify()`-able (Blueprint, ICH, template), so the blanket transaction gives the caller
a correct Ctrl-Z for free — which self-managed would throw away.

**Dirty path: `MarkBlueprintAsModified`, not `MarkBlueprintAsStructurallyModified`.** Read
`FBlueprintEditorUtils` to decide:

* `MarkBlueprintAsStructurallyModified` (`BlueprintEditorUtils.cpp:1802-1828`) runs
  `FBlueprintCompilationManager::CompileSynchronously(FBPCompileRequest(BP,
  EBlueprintCompileOptions::RegenerateSkeletonOnly, nullptr))` and then calls
  `MarkBlueprintAsModified` anyway. A skeleton regen rebuilds the class's **variable set**.
* An ICH override creates **no variable** — the component variable is already inherited from the
  parent — so the skeleton regen buys a synchronous compile for nothing.
* `MarkBlueprintAsModified` (`:1831-1895`) does exactly what a template *value* change needs:
  `BS_Dirty`, `MarkPackageDirty`, `PostEditChangeProperty`, and
  `UpdateCustomPropertyListForPostConstruction` on the BPGC **and its derived classes** — that is
  the cached list consulted when instancing components.
* And the override needs no compile at all to take effect: `USCS_Node::GetActualComponentTemplate`
  (`SCS_Node.cpp:29-54`) reads the ICH chain live at instancing time.

This is the same split the existing component endpoints already use: `add_component` uses
structural (it mints a variable), `set_component_transform` uses plain modified (it changes a
template value). This is the second kind.

`MarkBlueprintAsModified` only **dirties** — call `save_blueprint` (or `save_dirty_packages`) to
persist.

### Registry lines the main session must add

`MifBridgeHandlers.h`, beside the other component declarations:

```cpp
	// INHERITED components (MifBridgeInherited.cpp) — the Details-panel write path for a component
	// that came from a PARENT Blueprint's SCS. Delta storage via UInheritableComponentHandler; no
	// compile, so both mutators are ordinary transacted endpoints. get_inherited_component is the
	// read-only discovery verb: call it FIRST to learn which of the four routes
	// (parentBlueprintSCS / native / ownSCS / notFound) applies.
	MIF_DECL(get_inherited_component);
	MIF_DECL(override_inherited_component);
	MIF_DECL(revert_inherited_component);
```

`MifBridgeCommon.cpp`, in the `MIF_BIND` block beside the component binds:

```cpp
			// Inherited component overrides (Batch J)
			MIF_BIND(get_inherited_component);
			MIF_BIND(override_inherited_component);
			MIF_BIND(revert_inherited_component);
```

`MifBridgeCommon.cpp`, `IsReadOnlyEndpoint()`'s `TSet` — **one** new entry:

```cpp
			// Pure discovery: resolves a name across the parent chain and reports the route. Creates
			// NOTHING — it deliberately calls GetInheritableComponentHandler(false), so it cannot
			// mint an ICH just by being asked a question, and must not push an empty undo entry.
			TEXT("get_inherited_component"),
```

`IsSelfManagedEndpoint()` — **no entries**. Both mutators ride RunEndpoint's blanket transaction.

Expected after wiring: `self_audit` → `endpointCount` +3, `transactionBuckets.readOnly` contains
`get_inherited_component`, `transactionBuckets.transacted` contains the other two,
`policyContradictions` still `[]`.

### What server.py needs

Three `@mcp.tool()` wrappers in `tools/ue5-mcp-bridge/server.py`, in the components section next to
`list_components`:

```python
@mcp.tool()
def get_inherited_component(blueprint: str, component: str) -> dict:
    "Discovery verb for a component in a CHILD Blueprint. Reports origin (parentBlueprintSCS | native | ownSCS | notFound), whether an override already exists, the override template path, the parent's original template path, and for a NATIVE component the CDO-subobject path to use with set_property instead. Creates nothing - call this before override_inherited_component."
    return _post("get_inherited_component", blueprint=blueprint, component=component)


@mcp.tool()
def override_inherited_component(blueprint: str, component: str,
                                 properties: dict = None, confirm: bool = None) -> dict:
    "Override a component INHERITED from a parent Blueprint's SCS - what the Details panel does when you edit an inherited component. Mints the UInheritableComponentHandler override template (a delta; the parent's value is untouched) and optionally applies properties {name: value}. Every write is verified by re-export: a property that did not land is reported failed with a reason, never as success. Omit properties to just mint the override and use set_property against the returned overrideTemplatePath. Refuses NATIVE components (C++ parent) and names the CDO path to use instead."
    return _post("override_inherited_component", blueprint=blueprint, component=component,
                 properties=properties or None, confirm=confirm)


@mcp.tool()
def revert_inherited_component(blueprint: str, component: str, confirm: bool = False) -> dict:
    "Discard a child Blueprint's override of an inherited component so it falls back to the parent's values. Requires confirm=True - it drops EVERY overridden property on that component at once."
    return _post("revert_inherited_component", blueprint=blueprint, component=component, confirm=confirm)
```

Note `confirm: bool = None` on the override tool — the default must be *absent*, not `False`, or the
wrapper would turn every call into the explicit-refusal path.

### Live proof — the asset chosen, and the exact curls

Picked by walking the live bridge read-only (`find_assets` → `open_blueprint` → `list_object_properties`
→ `get_property`) on 2026-07-28:

| role | asset |
|---|---|
| child Blueprint (loose, editable) | `/Game/MODS/BotanistExpansion_p/Blueprints/NPCs/NPC_MifAmbient.NPC_MifAmbient` |
| parent (Blueprint class, **cooked**, container origin — BPGC only, no `UBlueprint`) | `/Game/Blueprints/Pawns/NPC/BP_BaseNPC.BP_BaseNPC_C` |
| inherited **parent-SCS** components on it | `Influence` (`USphereComponent`), `LookForward` (`UArrowComponent`), `NPCBody` (`UChildActorComponent`) |
| inherited **native** component (from `ACharacter`) | `Mesh` → subobject `CharacterMesh0` |

Why this one: the child's own SCS is empty (`list_components` → `count: 0`), it has **no ICH yet**
(`list_object_properties` → `InheritableComponentHandler: None`), and the parent-SCS templates are
directly addressable (`…BP_BaseNPC_C:Influence_GEN_VARIABLE` resolves, `SphereRadius = 32.000000`),
so every assertion below has a clean baseline. It also exercises the **cooked-parent** path, which is
the normal shape in this project.

All calls: `POST http://127.0.0.1:8791/api/<endpoint>`, header `X-Mif-Token: dev`.

```bash
BP=/Game/MODS/BotanistExpansion_p/Blueprints/NPCs/NPC_MifAmbient.NPC_MifAmbient
H='-H "X-Mif-Token: dev" -H "Content-Type: application/json"'

# 1. discovery, parent-BP SCS component. EXPECT origin=parentBlueprintSCS, canOverride=true,
#    overrideExists=false, existingOverrideCount=0,
#    parentTemplatePath=/Game/Blueprints/Pawns/NPC/BP_BaseNPC.BP_BaseNPC_C:Influence_GEN_VARIABLE
curl -s -X POST http://127.0.0.1:8791/api/get_inherited_component \
  -H "X-Mif-Token: dev" -H "Content-Type: application/json" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Influence\"}"

# 2. discovery, NATIVE component. EXPECT origin=native, canOverride=false, creationMethod=Native,
#    nativeMatchedBy=property,
#    nativeCdoPath=/Game/MODS/BotanistExpansion_p/Blueprints/NPCs/NPC_MifAmbient.Default__NPC_MifAmbient_C:CharacterMesh0
curl -s -X POST http://127.0.0.1:8791/api/get_inherited_component \
  -H "X-Mif-Token: dev" -H "Content-Type: application/json" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Mesh\"}"

# 3. the write. EXPECT ok=true, created=true, propertiesApplied=1, propertiesFailed=0,
#    properties[0] = {name:SphereRadius, applied:true, changed:true,
#                     before:"32.000000", after:"250.000000"},
#    overrideTemplatePath=/Game/MODS/.../NPC_MifAmbient.NPC_MifAmbient_C:Influence_GEN_VARIABLE
curl -s -X POST http://127.0.0.1:8791/api/override_inherited_component \
  -H "X-Mif-Token: dev" -H "Content-Type: application/json" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Influence\",\"properties\":{\"SphereRadius\":250}}"

# 4. independent read-back through the EXISTING set_property machinery. EXPECT "250.000000"
curl -s -X POST http://127.0.0.1:8791/api/get_property \
  -H "X-Mif-Token: dev" -H "Content-Type: application/json" \
  -d '{"objectPath":"/Game/MODS/BotanistExpansion_p/Blueprints/NPCs/NPC_MifAmbient.NPC_MifAmbient_C:Influence_GEN_VARIABLE","propertyPath":"SphereRadius"}'

# 5. IT IS A DELTA, NOT A COPY — the PARENT must be untouched. EXPECT "32.000000"
curl -s -X POST http://127.0.0.1:8791/api/get_property \
  -H "X-Mif-Token: dev" -H "Content-Type: application/json" \
  -d '{"objectPath":"/Game/Blueprints/Pawns/NPC/BP_BaseNPC.BP_BaseNPC_C:Influence_GEN_VARIABLE","propertyPath":"SphereRadius"}'

# 6. second discovery. EXPECT overrideExists=true, existingOverrideCount=1, route=set_property
curl -s -X POST http://127.0.0.1:8791/api/get_inherited_component \
  -H "X-Mif-Token: dev" -H "Content-Type: application/json" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Influence\"}"

# 7. NATIVE REFUSAL. EXPECT ok=false and the error text containing
#    ":CharacterMesh0" and "set_property {objectPath:"
curl -s -X POST http://127.0.0.1:8791/api/override_inherited_component \
  -H "X-Mif-Token: dev" -H "Content-Type: application/json" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Mesh\",\"properties\":{\"bAutoActivate\":false}}"

# 8. PM-003 PROOF — a value that cannot parse must leave the property intact.
#    EXPECT ok=false, properties[0].applied=false, before == after == "250.000000"
curl -s -X POST http://127.0.0.1:8791/api/override_inherited_component \
  -H "X-Mif-Token: dev" -H "Content-Type: application/json" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Influence\",\"properties\":{\"SphereRadius\":\"not-a-number\"}}"
curl -s -X POST http://127.0.0.1:8791/api/get_property \
  -H "X-Mif-Token: dev" -H "Content-Type: application/json" \
  -d '{"objectPath":"/Game/MODS/BotanistExpansion_p/Blueprints/NPCs/NPC_MifAmbient.NPC_MifAmbient_C:Influence_GEN_VARIABLE","propertyPath":"SphereRadius"}'

# 9. NO-OP HONESTY — re-writing the value it already has.
#    EXPECT ok=true, properties[0] = {applied:true, changed:false, note:"value was already this..."}
curl -s -X POST http://127.0.0.1:8791/api/override_inherited_component \
  -H "X-Mif-Token: dev" -H "Content-Type: application/json" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Influence\",\"properties\":{\"SphereRadius\":250}}"

# 10. STRICT PARAMS. EXPECT ok=false naming 'radius' and listing the accepted set
curl -s -X POST http://127.0.0.1:8791/api/override_inherited_component \
  -H "X-Mif-Token: dev" -H "Content-Type: application/json" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Influence\",\"radius\":250}"

# 11. UNKNOWN COMPONENT. EXPECT ok=false + availableComponents[] listing Influence/LookForward/
#     NPCBody as parentBlueprintSCS and CharacterMesh0/CharMoveComp/CollisionCylinder as native
curl -s -X POST http://127.0.0.1:8791/api/get_inherited_component \
  -H "X-Mif-Token: dev" -H "Content-Type: application/json" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"NoSuchThing\"}"

# 12. CONFIRM GATE. EXPECT ok=false, "requires confirm=true"
curl -s -X POST http://127.0.0.1:8791/api/revert_inherited_component \
  -H "X-Mif-Token: dev" -H "Content-Type: application/json" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Influence\"}"

# 13. REVERT. EXPECT ok=true, reverted=true, remainingOverrideCount=0,
#     fallsBackTo=/Game/Blueprints/Pawns/NPC/BP_BaseNPC.BP_BaseNPC_C:Influence_GEN_VARIABLE
curl -s -X POST http://127.0.0.1:8791/api/revert_inherited_component \
  -H "X-Mif-Token: dev" -H "Content-Type: application/json" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Influence\",\"confirm\":true}"

# 14. back to baseline. EXPECT overrideExists=false, existingOverrideCount=0
curl -s -X POST http://127.0.0.1:8791/api/get_inherited_component \
  -H "X-Mif-Token: dev" -H "Content-Type: application/json" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Influence\"}"

# 15. double revert. EXPECT ok=false, "has no override ... nothing to revert"
curl -s -X POST http://127.0.0.1:8791/api/revert_inherited_component \
  -H "X-Mif-Token: dev" -H "Content-Type: application/json" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Influence\",\"confirm\":true}"

# 16. buckets. EXPECT endpointCount +3, get_inherited_component in readOnly,
#     override_/revert_ in transacted, policyContradictions []
curl -s -X POST http://127.0.0.1:8791/api/self_audit \
  -H "X-Mif-Token: dev" -H "Content-Type: application/json" -d '{}'

# 17. the transaction is real. EXPECT the newest titles to read
#     "Mif Bridge: override_inherited_component" / "... revert_inherited_component"
curl -s -X POST http://127.0.0.1:8791/api/list_transactions \
  -H "X-Mif-Token: dev" -H "Content-Type: application/json" -d '{"limit":5}'
```

To keep an override, follow step 3 with
`save_blueprint {"blueprintId": "<BP>"}` — `MarkBlueprintAsModified` only dirties the package.

### Not verified

* **Nothing here is compiled.** The file is source-only; per instruction the editor was not built or
  restarted. The live-proof curls above are written against the running bridge but cannot execute
  until the DLL contains the new endpoints.
* `FBlueprintEditorUtils::HandleDisableEditableWhenInherited`'s call sites could not be enumerated
  inside the time budget (a full-engine grep timed out). The declaration
  (`BlueprintEditorUtils.h:1921`) and the record-removal body (`:9917-9933`) are read and cited; the
  endpoint therefore *warns* on a `bEditableWhenInherited` write rather than claiming to know
  exactly when the removal fires.
* The undo-after-revert path (record restored, template still garbage-flagged) is reasoned from
  `MarkAsGarbage` not being transaction-recorded (`UObjectBaseUtility.h:263`) plus the engine's own
  `!::IsValid()` recycling branch. It is documented and reported in the response `note`, not
  measured.
* This document appends with **LF** line endings to match the rest of `06_IMPLEMENTED.md` (the file
  is LF throughout); the new **source** file is CRLF, matching every other `.cpp` in the plugin.

**Verdict: COMPLETE (source only, unbuilt).** Files: 1 created
(`Source/MifBridge/Private/MifBridgeInherited.cpp`), 1 appended (this file). **No Build.cs change** -
`Engine` (PublicDependencyModuleNames) and `UnrealEd` (PrivateDependencyModuleNames) are already
declared and cover every include used (`Engine/InheritableComponentHandler.h`, `Engine/SCS_Node.h`,
`Engine/SimpleConstructionScript.h`, `Engine/BlueprintGeneratedClass.h`,
`Kismet2/BlueprintEditorUtils.h`), and UBT globs `Private/*.cpp` so the new file needs no listing.
Registry owed: **3 MIF_DECL + 3 MIF_BIND + 1 IsReadOnlyEndpoint entry** (exact text above), taking
MIF_DECL/MIF_BIND from 188 to 191. server.py owed: **3 tools**.


---

## Batch K — self-audit CRITICAL/HIGH fixes

Source pass over `docs/audit/07_SELF_AUDIT_FINDINGS.md`, all six dimensions. Scope was widened
mid-batch from CRITICAL/HIGH to **every finding**; this section is written in finding order per
dimension and states explicitly which findings were deliberately not fixed and why.

**Ground rule for this batch (it caused two collisions last round):** there are no per-file fences.
Where two places needed the same logic it was **promoted** to `Private/MifBridgeHandlers.h`
(declaration) + `Private/MifBridgeCommon.cpp` (definition) and the copies deleted. No helper was
duplicated. Line endings match each file's existing convention — the repo is genuinely mixed on disk
(e.g. `MifBridgeCommon.cpp`/`MifBridgeIntrospect.cpp` are LF, `MifBridgeHandlers.h`/`MifBridgeNodes.cpp`
are CRLF); every edit was applied through a helper that detects and re-emits the file's own ending,
and no file is mixed.

> A note on measurement: `grep -c $'\r$'` under Git Bash on this machine reports every line as
> CRLF regardless of the file's real content. It is not a reliable ending check. Python
> (`open(..., newline='')` then counting `\r\n`) is, and is what the numbers above come from.

---

### Dimension: anti-silence

**1. CRITICAL — `RunEndpoint` committed the transaction on `ok:false`. ALREADY FIXED before this
batch** (`MifBridgeCommon.cpp`, `Transaction.Cancel()` when `!IsOk(Out)`). Not re-done. It changes
the disposition of several findings below, which is noted at each one.

> **CORRECTED BY BATCH M — read this before trusting any "finding 1's rollback" claim below.**
> `Transaction.Cancel()` **discards the undo entry; it does not roll anything back.**
> `UTransBuffer::Cancel` pops the transaction off `UndoBuffer` and never calls `FTransaction::Apply`
> (`EditorTransaction.cpp:1387-1437`; `Apply` is called only from `Undo` `:1624` and `Redo` `:1688`).
> Every sentence in this batch that reasons from "the cancel takes the partial edit with it" is
> wrong. The affected findings are 13, 14, 15 and 16; see the *Batch M* section at the end of this
> file for what was actually done about each. PM-007 has the full argument.

**2. CRITICAL — `set_variable_default` wiped the default and echoed the request.** Rewritten,
`MifBridgeIntrospect.cpp`.
* The value key must now be **present**. `value`, `default` and `defaultValue` are all accepted (the
  `default`/`value` split between `add_variable` and this endpoint was itself the trap); supplying
  two of them at once is refused rather than silently preferring one.
* The value is converted through the **shared** `JsonToPropertyText` — the same converter
  `set_property` uses — against the variable's real `FProperty` (skeleton class, falling back to the
  generated class). So `{value:["a","b"]}` on an array variable now works, and `{value:"banana"}` on
  an int is refused naming the property and the form it wants, instead of being stored verbatim.
* `value:null` is the one deliberate way to clear a default.
* If the blueprint has no reflection property yet, a string is still stored (unchanged behaviour) but
  the response carries `typeValidated:false` and a warning; a non-string in that state is refused
  rather than guessed.
* The response is a **read-back**: `valueBefore`, `valueAfter`, `changed`, `typeValidated`. The legacy
  `default` field is still emitted but now carries `valueAfter`, not the request.
* `RejectUnknownParams` added.

**3. CRITICAL — `batch`'s inline backup was a degraded copy of `backup_blueprint`.** Extracted
`bool BackupPackage(UPackage*, FString& OutPath, FString& OutError)` into
`MifBridgeHandlers.h` + `MifBridgeCommon.cpp`; `H_backup_blueprint` (`MifBridgeIntrospect.cpp`) and
`H_batch` (`MifBridgeNodes.cpp`) both call it. It branches on `UPackage::ContainsMap()` (so a `.umap`
gets a real backup instead of silently none), checks `IFileManager::Copy` against `COPY_OK`, and
refuses when the asset has never been saved. In `batch`, `backup:true` that cannot be honoured — no
`blueprintId`, unresolvable `blueprintId`, or a failed copy — is now a **hard Fail before any op
runs**, not a silent proceed.

**4. HIGH — `rename_variable` / `remove_variable` reported success for no-ops; `rename_variable`
could hang the bridge on a modal.** Both rewritten, `MifBridgeIntrospect.cpp`.
* Existence is checked with `FBlueprintEditorUtils::FindNewVariableIndex` first; a miss Fails with
  **near-miss suggestions** (new shared `NearMissSuggestion` helper).
* A name that resolves on the **parent class** gets its own message naming the class it is inherited
  from — `RemoveMemberVariable`/`RenameMemberVariable` only search `NewVariables`, so an inherited
  name was a guaranteed no-op reported as success.
* `newName == oldName` is refused (FName compares case-insensitively, so this also means "fix the
  casing" is not something this endpoint can do — said out loud rather than silently no-op'd), and so
  is a `newName` already in use.
* **The modal is now unreachable from HTTP:** a variable with `RepNotifyFunc != NAME_None` is refused
  with a message pointing at `set_variable_flags {repNotify:false}`. `RenameMemberVariable` calls
  `VerifyUserWantsRepNotifyVariableNameChanged`, which pops an `FSuppressableWarningDialog` on the
  game thread — the thread the HTTP ticker runs on — so the whole bridge would stall until a human
  clicked, and clicking *No* made the engine revert the rename while the handler still answered
  `ok:true`.
* Both **read back** afterwards (`renamed`, `removedVerified`) instead of asserting the engine's void
  call worked.
* `RejectUnknownParams` added to both.

**5. HIGH — three splice paths broke the exec chain, discarded `TryCreateConnection`, and reported a
count of links they may not have made.** Two shared helpers added
(`MifBridgeHandlers.h` + `MifBridgeCommon.cpp`):
* `SpliceExecAfter(SourceOut, InsertIn, InsertOut, OutMoved, OutError)`
* `SpliceExecBefore(TargetIn, EntryIn, ExitOut, OutMovedUpstreams, OutError)`

Both validate **every** connection the new shape needs with `CanCreateConnection` *before* the first
`BreakPinLinks`, and both report a tally of connections actually made. Call sites converted:
`splice_into_exec` (`MifBridgeNodes.cpp`), `SpliceAfter` and `recipe_splice_before_parent`
(`MifBridgeRecipes.cpp`). `recipe_add_debug_print` no longer answers `ok:true` + a warning when the
splice fails — a print node that is never executed is not what was asked for.

Also fixed in the same class, as the finding notes: **`DoConnect`'s `bBreakFirst` branch** ran
`BreakPinLinks` on both pins *before* `CanCreateConnection`, so `reconnect_pin` on a disallowed pair
destroyed the existing wiring and then returned `ok:false`. The query is hoisted above the break.

**6. HIGH — `paint_landscape` never checked the layer belongs to the landscape.** Added the check its
own error message already promised: `Info->GetLayerInfoIndex(LayerInfo) == INDEX_NONE` Fails and lists
the landscape's real layers. Painting an unregistered layer does not no-op — `SetAlphaData` allocates
a new weightmap channel and the weight-adjust normalisation pushes the real layers down, then a later
`FixupWeightmaps` deletes it — so the old behaviour was `ok:true, verticesTouched:N` for a paint that
dimmed the layers in use and then vanished.

**7. HIGH — `set_spline_points` cleared the spline before validating any point.** All points are now
parsed into a `TArray<FVector>` **before** `ClearSplinePoints`, and a non-object entry Fails naming
its index. `points:[[0,0,0],[100,0,0]]` used to return `ok:true, pointCount:0` with the existing
patrol route destroyed. `snapToGround` combined with `space:"local"` is refused instead of ignored,
`pointCount` is read back from the component, and points whose downward trace hit nothing are
reported rather than counted as snapped.

**8. HIGH — `batch` silently discarded non-object `ops[]` entries.** Each one now produces an
`ok:false` row in `results[]` carrying its `index`, and sets `bAllOk=false`; an op with no `op` key
gets the same treatment. An **empty** `ops` array is a hard Fail. Every result row now carries
`index`, so a caller can align `results[i]` with `ops[i]`.

**9. HIGH — `spawn_actor_in_pie` was the unfixed sibling of the `spawn_actor_in_level` postmortem.**
`mesh`/`staticMesh` support ported across (with a read-back that destroys the actor rather than
leaving an empty one if the mesh does not take), and `RejectUnknownParams` added to **both**
endpoints with a `KeyNote` for `material`. `spawn_actor_in_level`'s `in:` contract line was corrected
— it still advertised `{ actorClass, location?, rotation?, scale?, label?, folder? }` long after the
code began reading `mesh`, `staticMesh` and the `class` alias.

**10. HIGH — 132 of 203 handlers have no `RejectUnknownParams`. PARTIALLY fixed; the architectural
half deliberately not done.** Guards were added to every endpoint this batch touched
(`describe_class`, `list_enum_values`, `rename_variable`, `remove_variable`, `set_variable_default`,
`spawn_actor_in_level`, `spawn_actor_in_pie`, and the others listed below). The finding's actual
recommendation — change `MIF_BIND` to carry an accepted-key list and have `RunEndpoint` apply the
guard centrally — is **not** done here: it rewrites the signature of all 191 binds plus the external
descriptor struct in a *public* header, which is a batch of its own and cannot be verified without a
build. It remains the right fix and is the single highest-leverage item left in the document.

### Dimension: anti-silence (MEDIUM / LOW)

**11. `create_material_instance` — two silent drops + a dead `Unknown` array.** Rewritten to
gather-and-validate before the first write (the bracket `set_material_parameter` already had), so a
`scalars` entry that is not a number and a `vectors` entry that is not a colour are named errors
instead of skipped-and-uncounted. It now also checks the **parent** exposes each name and emits the
`unknownParameters[]` array that was declared at the top of the handler and never written. Reported,
not fatal — the instance is the endpoint's product and destroying it over a bad parameter name would
be worse. Vector values gained `{x,y,z,w}` and `[r,g,b,a]` forms via the shared `JsonToLinearColor`.
The TODO block that deferred all of this is gone, including its false claim about the bucket.

**12. Unresolved `actorPaths[]` entries silently dropped.** `snap_actors_to_ground`
(`MifBridgeWorld.cpp`) emits `notFound[]` and **Fails** when nothing resolved — with every path
bogus it used to return `ok:true, considered:0, snapped:0, missed:0`, a total no-op reported as
success — and also Fails when a folder/label selector matches nothing. `duplicate_actors`
(`MifBridgeAuthoring.cpp`) emits `notFound[]` **and** `failed[]`, so `duplicated + failed.length ==
sourceCount x count` is checkable rather than trusted.

**13. `add_component` dropped `location`/`rotation`/`scale` for non-scene components.** The transform
block lived inside `Cast<USceneComponent>`, so a `UAudioComponent` with a transform returned
`ok:true` having ignored all three. Now Fails with the same message `set_component_transform` uses —
the two endpoints no longer disagree about the same impossible request.

**14. `add_timeline` discarded its entire configuration on a null template.** `length`, `autoPlay`,
`loop` and `floatTracks[]` all lived inside `if (Template)`. A null template now Fails ~~(finding 1's
`Transaction.Cancel()` takes the half-made node with it)~~ — **struck by Batch M: the cancel does not
take the node with it.** Batch M moved the `floatTracks[]` check above the node creation, and the
null-template branch now names the node it left in the graph. A non-string/empty `floatTracks[]`
entry Fails naming its index instead of being skipped.

**15. `TrySetDefaultValue`'s result discarded.** New shared `SetPinDefaultChecked`
(`MifBridgeHandlers.h` + `MifBridgeCommon.cpp`) snapshots `DefaultValue` **and** `DefaultObject`
**and** `DefaultTextValue` around the call — comparing only `DefaultValue` would miss an object-pin
write entirely. `set_pin_default` now Fails when the schema refused the literal and emits
`defaultBefore`/`defaultAfter`/`changed`. `add_pin` reports `defaultApplied`/`defaultError` but does
**not** Fail: the pin was created successfully and failing would report failure over a pin that stays
(Batch M: *"would roll that back too"* was the wrong reason for the right behaviour). A default
supplied for an output pin is now reported rather than silently ignored.

**16. `add_tree_widget` dropped `x`/`y`/`autoSize` for non-canvas parents and orphaned a widget.**
Placement keys are checked *before* `ConstructWidget` for the root case, and against the real slot
class afterwards; either way it Fails instead of ignoring them. On an `AddChild` failure the
already-constructed widget gets `MarkAsGarbage()` rather than being left in the tree's outer
(`MarkAsGarbage` is not transaction-recorded, so finding 1's rollback would not have removed it —
and per Batch M, finding 1's rollback would not have removed the widget either, so this handler was
right for a slightly better reason than it knew).

**17. DataTables.** The `replace` path returned `replaced:false` with **no Fail** — `ok:true` for a
destructive call that changed nothing; it now Fails with the reasons. A non-object `rows[]` entry now
warns (the missing-`Name` case immediately below it always did — two adjacent failures, one visible).
`delete_datatable_rows` gained the `RejectUnknownParams` its sibling in the same file has had since
Batch B, refuses an empty `rowNames`, and Fails on a non-string entry instead of skipping it on a
confirm-gated destructive endpoint.

**18. `create_blueprint`'s `overwrite` did nothing.** Removed from the `in:` line and from
`server.py`'s signature; `blueprintType` (which the handler reads, and whose absence from the
contract caused PM-002) added. A `RejectUnknownParams` with a `KeyNote` now answers `overwrite` by
name — "NOT supported … `delete_asset` the old one first" — instead of dropping it and letting the
caller stare at "a Blueprint already exists" wondering why their flag did nothing.

**19. `set_actor_transform`'s `relative` ignored scale.** `relative:true` deltas location and
rotation and left scale absolute, undocumented. Combining them is now refused: additive and
multiplicative "relative scale" are both defensible and mean opposite things at 0, so there is no
honest default.

**20. `describe_animation` documented `numKeys?`, emitted by no line of the plugin.** Corrected to
`numSampledKeys?` (what it really emits, for `UAnimSequence` only) and `notifyCount` added — it was
emitted and undocumented.

**21. `sculpt_landscape`'s unknown-`mode` refusal was inside the per-vertex loop.** A brush smaller
than one quad never reached it, so an invalid mode answered `ok:true, verticesTouched:0`. Validated
before the loop, and the silently-ignored argument combinations are now refusals: `amount` applies to
raise/lower only, `targetZ` to flatten only, and raise/lower with `amount:0` is refused rather than
writing every vertex back unchanged and counting it as touched.

**22. `add_switch_string` dropped bad `cases[]` entries and had no doc block.** Both fixed; duplicate
cases are also refused, since they collapse into one pin and undercount the same way.

**23. `set_material_parameter` never called `MIC->Modify()`.** One line, added before the first
write. Without it the handler recorded nothing into the blanket transaction, `UTransBuffer::End` saw
`FTransaction::IsTransient()`, popped the entry and restored `UndoCount` — so the material edit was
**not undoable and the next Ctrl-Z reverted whatever the user did before it**, while
`undo_transactions` reported success over an edit it had not reverted.

---

### Dimension: registry-buckets

**1 / 2. `describe_class` and `list_enum_values` were 100% broken over MCP.** `server.py` posts
`className` / `enumName`; the handlers read only `class` / `enum`, and neither had a guard, so every
MCP call answered "class is required" / "enum is required" to a caller that had plainly supplied one.
Both handlers now accept both spellings via `JStrAny` and carry a guard. Aliased in the **handler**
rather than changed in the wrapper because `enumName` is the plugin's usual spelling —
`add_switch_enum` and `add_enum_literal` in the same file already read it, so `list_enum_values` was
the odd one out, not `server.py`.

**3. `batch` could not dispatch any external (`kr_*`) endpoint.** `H_batch` consulted only
`Handlers()` while `RunEndpoint`, `IsReadOnlyEndpoint` and `IsSelfManagedEndpoint` had all been
taught about the external registry — a half-integration in which the compile-heavy *ban* honoured
external descriptors and the *dispatcher* below it did not. All 12 `kr_*` endpoints answered
`unknown op: 'kr_list_events'` for endpoints `self_audit` lists as live. New
`FindExternalHandler(const FString&)` in `MifBridgeHandlers.h` (defined in `MifBridgeCommon.cpp`,
where `ExternalRegistry()` is file-static) lets `H_batch` mirror RunEndpoint's order exactly. This
also un-deadens `MifKrBridgeEndpoints.cpp`'s batch-specific `op` handling.

**4. `create_material_instance` was transacted while two source comments asserted it was not.** Added
to the `SelfManaged` set beside `create_material`, and both comments corrected. It does exactly what
its siblings do (`CreatePackage` → `FactoryCreateNew` → `PostEditChange` → `AssetCreated` →
`MarkPackageDirty`), and `UMaterialInstance::PostEditChangeProperty` runs `InitResources()` +
`UpdateStaticPermutation()` — the same shader-state family cited as the reason `recompile_material`
is self-managed.

**5 / 7 / regressions 8. Pure reads in the transacted bucket.** `get_referencers`,
`get_dependencies`, `audit_unused` and `build_navmesh` added to `IsReadOnlyEndpoint`. Each pushed an
empty undo entry per call. The comment records *why* it mattered even though the engine discards a
record-free transaction: that only holds while they record nothing, and the moment one gains a
`Modify()` the redo stack starts dying silently — which is the exact A/B loop `redo_transactions`'
docstring tells callers to rely on.

**8. `create_struct` / `create_enum` mint assets inside the blanket transaction.** Left as-is,
**deliberately**, with the reason now written at the bucket site instead of reading as an oversight:
`FStructureEditorUtils::AddVariable`/`RemoveVariable` open their **own** `FScopedTransaction` around
the reinstance-and-recompile, so struct editing inside a transaction is engine-sanctioned and is not
the dead-CDO hazard; only their package-creation half is out of line, and moving them would make them
compile-heavy and therefore unbatchable — a behaviour change with no defect behind it.

**9. Stale counts, paths and line references.** See the doc-truth section below.

---

### Dimension: unity-and-dupes

The rule at the heart of this dimension is now written down in `MifBridgeHandlers.h` and in
**PM-005** (new, `docs/01_POSTMORTEMS.md`): a unity build merges all unnamed namespaces in a
translation unit into one, `static` collapses identically, and blob membership follows file **sizes**
and moves on its own — so "they're in different blobs" is never a fence.

| # | helper | was | now |
|---|---|---|---|
| CRITICAL 1 | `EditorWorld()` | 3 byte-identical copies (`Streaming`, `World`, and `GetEditorWorld` in `PIE`), ~8 KB of source growth from a hard C2084 | 1, `MifBridgeCommon.cpp` |
| HIGH 2 | `JsonTypeName(EJson)` | 2, **already diverged in caller-visible text** (`"bool"` vs `"boolean"`) | 1; `"boolean"` wins, so `set_property` refusals now say "JSON boolean" |
| HIGH 3 | `NormalizeBoolLiteral` | 2, under an eviction clause with no trigger | 1 |
| HIGH 4 | `Vec3` | 2, **already sharing unity blob 2**, compiling only because the arities differed | 1 (both overloads) |
| MED 5 | `ValidateNewAssetPath` | 2, same name, different signature, different failure convention | renamed apart: `ValidateNewMaterialAssetPath` / `ValidateNewUserTypePath` |
| MED 6a | the actor finder | **5** byte-identical copies under 5 names | 1, `FindActorInWorld` |
| MED 6b | the property dot-walk | 3 (`ResolvePropertyPath`, `ResolveReadPropertyPath`, `ResolvePropertyPathLocal`) — PM-003's write bracket in triplicate | 1, `ResolvePropertyPath`, now with near-miss suggestions for every caller |
| MED 6c | the pin-spec parser | 2 (`ParsePinSpecs`, `ParseDispatcherParams`) | 1, `ParsePinSpecs`; a non-object entry is now an error, not a silent skip |
| MED 6e | "current world" | 5 helpers, **2 different policies** (Spatial/Nav preferred PIE; Streaming/World/PIE did not) | 2 named intents: `EditorWorld()` and `ActiveWorld()`, each call site picking deliberately |

Also promoted, because more than one place needed them: `JsonToPropertyText` and
`PropertyValueToTypedJson` (declared in the shared header; definitions stay in `MifBridgeNodes5.cpp`
next to the cluster of conversion helpers they depend on — one definition, one file). **`MifBridgeNodes6.cpp`
had been calling `PropertyValueToTypedJson` with no declaration in scope at all**, compiling only
because the unity build happened to put it in the same TU as `MifBridgeNodes5.cpp`.

**6d** (`NormalizeLevelPackagePath` vs `PackagePathToMapFilename`, 0.83 similar) was **not** merged:
only the first ~20 lines coincide and the tails do genuinely different things. Left as two functions
with two names — which is the correct end state, not a duplicate.

**7.** `MIF_DECL(set_component_transform)` moved out from under the `MifBridgeInherited.cpp` heading
in `MifBridgeHandlers.h` — it is defined in `MifBridgeComponents.cpp`, and mis-signalled ownership is
precisely the input that manufactures a second copy.

**8.** Every load-bearing line citation in the **public** header `MifBridgeEndpointRegistry.h` was
wrong (7 of them, plus "176 built-ins"). Rather than re-deriving numbers that will drift again, the
citations are **removed** and replaced with symbol names plus a note saying why: a wrong citation is
the *mechanism* of this whole finding set — the next reader jumps to the cited line, finds nothing,
and writes a local copy.

**Verification.** A module-wide scan for free functions defined in more than one `.cpp` now returns
**zero**. Before this batch it returned five same-name collisions and roughly a dozen
body-identical-under-different-names.

---

### Dimension: hazards

**1. HIGH — `audit_unused` blocked the game thread unboundedly.** Three separate stalls, all inline
on the thread the HTTP ticker runs on, all now **hard Fails rather than waits** (a caller can retry
an error; nobody can cancel a stall):
* `rescan:true` is refused for a `pathPrefix` of fewer than two segments — `ScanPathsSynchronous`
  on `/Game` is minutes on this project, and the only prior validation was "non-empty, starts with `/`";
* the unconditional `WaitForCompletion()` — which blocked even when `rescan` was *not* asked for — is
  replaced by "the asset registry is still scanning, retry once it settles";
* a prefix matching more than 20000 assets is refused, because `GetReferencers` runs per asset
  regardless of `limit` (`limit` caps the reported rows, not the work).

All three are documented in the endpoint's comment block, which previously declared no hazards at all
despite `02_GOTCHAS.md` requiring it.

**2. MEDIUM — `set_sublevel_visibility` / `set_current_sublevel` ran a synchronous level-streaming
flush inside the blanket transaction.** Both moved to `SelfManaged`. `SetLevelVisibility` opens its
**own** `FScopedTransaction` and runs `FlushLevelStreaming()` → `FlushAsyncLoading()` inside a
`while (bLevelsPendingVisibility)` loop — a nested engine transaction plus a blocking async-loading
flush captured as one undo step. That was the correctness bug and it is fixed. Routing them through
the deferred op log the way `add_sublevel` does was **deliberately not done**: it converts a working
synchronous verb into a poll-based one, which is a contract change rather than a bug fix, and the
flush is bounded in an editor world. The stall is declared in the file's hazard header, in both
endpoint comments, and in `02_GOTCHAS.md` §8.

**3. MEDIUM — the "FOUR reachable hazards, all made unreachable" claim was not exhaustive.** Three
more, verified against `D:/UE532`: `AddLevelToWorld`'s unconditional `FScopedSlowTask::MakeDialog()`
(declared — Slate ticks, `FTSTicker` does not, so concurrent requests stall); `check(Level->OwningWorld)`
at `EditorLevelUtils.cpp:527` (**closed** — the deferred lambda now tests it, alongside the two checks
it already covered); and `check(Level->bIsVisible == …)` (not a live crash in an editor world, but it
means the graceful "SetLevelVisibility did not take" branch is **dead code** — marked as such at the
site so nobody treats it as the safety net).

**4. MEDIUM — `pie_load_level_instance` reported the wrong cause for a name collision.**
`LoadLevelInstance` sets `bOutSuccess=false` on **entry** and the already-exists branch returns
`nullptr` **without** setting it, so `bFound` was always false when `Instance` was null and the
"already exists" arm was unreachable — a colliding `nameOverride` was told the package is missing
from the asset registry. Now detected **before** the call, replicating `GetLevelInstancePackageName`
(`LevelStreaming.cpp:2585-2619`) and the uniqueness test (`:2538-2553`) exactly — `[/Temp] +
GetLongPackagePath + "/" + nameOverride`, `ConvertToPIEPackageName` with the world context's
`PIEInstance`, compared against `GetWorldAssetPackageFName()` — and only when a `nameOverride` was
supplied, which is the same condition the engine gates its own test on. **This was read out of the
engine source, not inferred**; a first attempt used an `EndsWith` heuristic and was replaced because a
false positive would refuse a legal call.

**5. LOW — the deferred-op ring could drop a mutating call's result.** `BeginOp` evicted the oldest
entry unconditionally, so more than 16 deferred verbs before the next tick silently discarded
outcomes and the caller polled forever — the "never silence a mutating call" failure inside the
mechanism built to prevent it. Only **completed** entries are evictable now; if 16 are all pending
the ring grows. `FinishOp` finding no entry logs an error instead of returning in silence.

**6 / 7. LOW — undeclared blocking.** `diagnose_landscape_draws`' `FlushRenderingCommands()` and
`save_dirty_packages`' per-map `FScopedSlowTask … MakeDialog(true)` are now declared in their comment
blocks (and in the `02_GOTCHAS.md` §8 table). The `diagnose_landscape_draws` note also records *why*
its captured `FPrimitiveSceneProxy*` pointers are safe — nothing runs on the game thread between the
gather and the flush — so a future edit does not introduce a yield there.

---

### Dimension: regressions

**1. `batch` vs external endpoints** — fixed, see registry-buckets #3.
**2. `set_material_parameter` turned a partial success into all-or-nothing** — the behaviour is
deliberate (a partially-applied material edit is indistinguishable from a complete one at the call
site) but was undocumented. Now stated in the handler comment **and** in the `server.py` docstring,
which explicitly separates the two failure modes: an *unknown* name is reported in
`unknownParameters[]` and is not fatal alone; a *malformed value* aborts the whole call before any
write.
**3. `set_material_parameter` recorded nothing into its transaction** — fixed, see anti-silence #23.
**4. `set_property` was banned from `batch` while GOTCHAS §5d told callers to batch it** — the ban
was over-broad: only the `widgetName` branch compiles. New `IsBatchTransactionOpen()` +
`FBatchTransactionScope` (RAII, declared beside `batch`'s `FScopedTransaction`) let the **widget
branch refuse itself** inside a batch, and `set_property` is subtracted from
`IsCompileHeavyEndpoint`. The `objectPath` branch — CDO edits, component templates, node properties,
placed actors — is batchable again, as the docs always said.
**5.** `create_material_instance`'s bucket and both false comments — fixed, see registry-buckets #4.
**6. `batch`'s `compileAtEnd` skipped ops addressed through the `path` alias** — `Touched` now uses
`JHasAny`/`JStrAny` over `{blueprintId, path}`, the same spelling set the handlers' own guards
advertise. Before this, an op that mutated through `path` left `compiles:[]` and a structurally
modified, uncompiled blueprint under `ok:true`.
**7. `add_variable_get`'s new refusal was undocumented** — the readability sentence added to its
`server.py` docstring, matching `add_variable_set`'s.
**9. `add_function_call` could not honour `asMessage:false`** — `JHasAny` now distinguishes "omitted"
from "explicitly false", so an explicit `false` suppresses the interface auto-Message path. The
response reports `message` either way, and a deliberate non-Message interface call carries a `note`
saying it will hard-fail at runtime on a target that does not implement the interface (which is the
reason the auto-path exists).
**10. A `set_property` that failed its own verification left the package dirty** — the dirty flag is
snapshotted before the write and restored on the verification-failure path (`packageDirtyRestored` in
the response). It only ever *clears* a flag this call raised. Without it, `list_dirty_packages` and
`save_dirty_packages` would report and then **save** a package whose only "change" provably did not
land.
**11. `RejectUnknownParams` tolerated `op` on every endpoint** — now only while
`IsBatchTransactionOpen()`, so `find_assets {"op":"typo"}` over raw HTTP is a named error again
instead of being silently accepted.

---

### Dimension: doc-truth

`06_IMPLEMENTED.md` is **append-only** by standing instruction, so its false verdicts are corrected
here as errata rather than edited in place. Everything outside this file was fixed at the source.

| finding | disposition |
|---|---|
| 1, 2 (CRITICAL) — "Batch D verdict: COMPLETE — 10 endpoints live-proven" (`:545`) is false for 4 of the 10; "Wave 1 … BUILD PASS, ALL PROVEN" (`:1384`) is contradicted by its own body | **ERRATUM, recorded here.** Both verdicts are hereby struck: they claim live proof that the sections' own bodies do not contain. Treat any "PROVEN" in this document as unverified unless a transcript accompanies it. |
| 4 (HIGH) — every gating `self_audit` assertion in the file is wrong | **ERRATUM.** They were written against pre-Batch-I counts. The live figures after this batch are **203 endpoints / 191 built-in + 12 external**, and `self_audit` is the authority — no assertion in this document should be used as a gate. |
| 3, 5, 6, 7, 13, 14, 15, 16 | **NOT FIXED** — these are structural repairs to `06_IMPLEMENTED.md`, `01_CATALOGUE.md` and `02_RANKED.md` (missing sections, unstruck plans, implemented-status columns). Append-only forbids the first; the other two are a bulk status re-derivation that is its own task and would be wrong again after the next batch. Named here so they are not lost. |
| 8, 12 | **ERRATUM.** `save_dirty_packages` was never proven to save, and the W3-6 proof cannot fire. |
| 9 (`00_ARCHITECTURE.md` false in four places) | **FIXED.** The "Known sync hazard" section named `C:\Users\andre\Documents\GitHub\Eddie_v2\tools\ue5-mcp-bridge\server.py` — **a path that does not exist** — and claimed 82 of 102 endpoints exposed with 20 having no MCP tool. All wrong. Replaced with the real path, the real counts (203/203), a corrected parity command, and an explanation of why the reverse diff is *not* empty (the 12 `kr_*` externals never appear in `MIF_BIND`). The source-layout table, which stopped at `Nodes6.cpp`, gained the 16 missing files. |
| 10, 11 (`02_GOTCHAS.md` §3 and §7) | **FIXED.** §7's compile-heavy list named 8 of ~25 and had drifted; it now points at `self_audit.transactionBuckets.compileHeavy` — computed from the same predicate `batch` refuses with — rather than repeating a literal list that will drift again. §5d's "Batch what you can" now says which branch is batchable and which is not. |
| README counts (`:46`, `:127`) | **FIXED** — 102 → 203. |
| `Public/MifBridgeEndpointRegistry.h` citations | **FIXED** — see unity-and-dupes #8. |
| unity-and-dupes 9 — no postmortem for the C2084 | **FIXED** — **PM-005** added to `docs/01_POSTMORTEMS.md`, covering per-TU unnamed-namespace merging, why `static` does not help, why a differently-*named* copy is worse (no build error at all), why blob membership is not a fence, and why eviction clauses without a trigger produce permanent duplicates. |

---

### Live proofs

The bridge was **not** restarted for this batch (the editor is running an older DLL and must not be
killed), so these are the exact commands that demonstrate each fix once the plugin is rebuilt. Every
one of them **failed to demonstrate anything before this batch** because the old code answered
`ok:true`.

```bash
B=http://127.0.0.1:8791/api
H=(-H "X-Mif-Token: dev" -H "Content-Type: application/json")
BP=/Game/MifTestbed/BP_Foo

# --- Finding 2: set_variable_default must FAIL rather than wipe -------------------------
# BEFORE: ok:true, default:""  and Health's default destroyed.  AFTER: ok:false, nothing touched.
curl -s -X POST $B/set_variable_default "${H[@]}" -d "{\"blueprintId\":\"$BP\",\"name\":\"Health\"}"
#   expect ok:false, error mentions "value is required (aliases: default, defaultValue)"
curl -s -X POST $B/set_variable_default "${H[@]}" -d "{\"blueprintId\":\"$BP\",\"name\":\"Health\",\"value\":\"banana\"}"
#   expect ok:false on an int variable, naming the property and the accepted form
curl -s -X POST $B/set_variable_default "${H[@]}" -d "{\"blueprintId\":\"$BP\",\"name\":\"Health\",\"value\":100}"
#   expect ok:true with valueBefore/valueAfter/changed/typeValidated:true  (a READ-BACK, not an echo)
curl -s -X POST $B/set_variable_default "${H[@]}" -d "{\"blueprintId\":\"$BP\",\"name\":\"Items\",\"value\":[\"a\",\"b\"]}"
#   expect ok:true on an array variable — the JSON array is CONVERTED, not silently blanked

# --- Finding 4: rename/remove must FAIL on a no-op, and never open a modal ---------------
curl -s -X POST $B/rename_variable "${H[@]}" -d "{\"blueprintId\":\"$BP\",\"oldName\":\"NoSuchVar\",\"newName\":\"X\",\"confirm\":true}"
#   expect ok:false + near-miss suggestions.  BEFORE: ok:true, name:"X", nothing renamed.
curl -s -X POST $B/rename_variable "${H[@]}" -d "{\"blueprintId\":\"$BP\",\"oldName\":\"Health\",\"newName\":\"Health\",\"confirm\":true}"
#   expect ok:false ("same variable name … nothing to rename")
# with a RepNotify variable — this is the one that used to HANG THE WHOLE BRIDGE on a modal:
curl -s -X POST $B/set_variable_flags   "${H[@]}" -d "{\"blueprintId\":\"$BP\",\"name\":\"Health\",\"replicated\":true,\"repNotify\":true}"
curl -s -X POST $B/rename_variable      "${H[@]}" -d "{\"blueprintId\":\"$BP\",\"oldName\":\"Health\",\"newName\":\"HP\",\"confirm\":true}"
#   expect an IMMEDIATE ok:false naming the RepNotify function and set_variable_flags — no dialog,
#   and the next curl still answers.  Confirm the bridge is alive:
curl -s -X POST $B/self_audit "${H[@]}" -d '{}' | head -c 80
curl -s -X POST $B/remove_variable "${H[@]}" -d "{\"blueprintId\":\"$BP\",\"name\":\"Typo\",\"confirm\":true}"
#   expect ok:false.  BEFORE: ok:true, removed:"Typo", nothing removed.
# an INHERITED name gets its own answer rather than a bare not-found:
curl -s -X POST $B/remove_variable "${H[@]}" -d "{\"blueprintId\":\"$BP\",\"name\":\"bHidden\",\"confirm\":true}"
#   expect ok:false, "is INHERITED from Actor … cannot remove a variable it does not own"

# --- Finding 3: batch backup on a MAP package must produce a real .bak or FAIL -----------
MAP=/Game/Maps/TestRoom
curl -s -X POST $B/batch "${H[@]}" -d "{\"blueprintId\":\"$MAP\",\"backup\":true,\"compileAtEnd\":false,\"ops\":[]}"
#   expect ok:false ("'ops' is empty") — and note it fails BEFORE running anything
curl -s -X POST $B/batch "${H[@]}" -d "{\"blueprintId\":\"$MAP\",\"backup\":true,\"compileAtEnd\":false,\"ops\":[{\"op\":\"list_variables\",\"blueprintId\":\"$BP\"}]}"
#   expect ok:true AND backup:"…/TestRoom.umap.bak"; verify the file EXISTS:
ls -l "D:/DDS2SDK/Game/Content/Maps/TestRoom.umap.bak"
#   BEFORE: the handler probed TestRoom.uasset, found nothing, emitted NO backup key, and ran anyway.
curl -s -X POST $B/batch "${H[@]}" -d "{\"blueprintId\":\"/Game/Nope/Missing\",\"backup\":true,\"ops\":[{\"op\":\"list_variables\",\"blueprintId\":\"$BP\"}]}"
#   expect ok:false, "backup:true was requested but blueprintId … did not resolve … Nothing was run."

# --- Finding 8 + registry-buckets 3: batch honesty and external dispatch -----------------
curl -s -X POST $B/batch "${H[@]}" -d '{"compileAtEnd":false,"ops":["add_branch"]}'
#   expect ok:false, opCount:1, results[0].error "ops[0] is not an object".  BEFORE: ok:true, opCount:0, results:[]
curl -s -X POST $B/batch "${H[@]}" -d '{"compileAtEnd":false,"ops":[{"op":"kr_list_events","asset":"X"}]}'
#   expect the reconstructor's own answer.  BEFORE: "unknown op: 'kr_list_events'" for an endpoint self_audit lists.

# --- Finding 5: a splice that cannot complete must not sever the chain -------------------
curl -s -X POST $B/splice_into_exec "${H[@]}" -d "{\"afterNode\":\"$G1\",\"insertNode\":\"$G2\",\"insertExecIn\":\"NotAnExecPin\"}"
#   expect ok:false, "insertExecIn not found" — and list_nodes shows the ORIGINAL exec chain intact
#   BEFORE: the chain was broken first and reconnectedTargets reported links that were never made.

# --- Buckets: the four moved endpoints, and no policy contradictions ---------------------
curl -s -X POST $B/self_audit "${H[@]}" -d '{}' \
  | python -c "import json,sys; d=json.load(sys.stdin); b=d['transactionBuckets']; \
print('endpointCount', d['endpointCount']); \
print('contradictions', d['policyContradictions']); \
print({n: ('readOnly' if n in b['readOnly'] else 'selfManaged' if n in b['selfManaged'] else 'transacted') \
 for n in ['get_referencers','get_dependencies','audit_unused','build_navmesh','create_material_instance', \
           'set_sublevel_visibility','set_current_sublevel','set_property']}); \
print('set_property compileHeavy?', 'set_property' in b['compileHeavy'])"
#   expect endpointCount 203, contradictions [], the first four readOnly, the next three selfManaged,
#   set_property selfManaged, and set_property NOT in compileHeavy.

# --- regressions 4: the objectPath branch of set_property is batchable again -------------
curl -s -X POST $B/batch "${H[@]}" -d '{"compileAtEnd":false,"ops":[{"op":"set_property",
  "objectPath":"/Engine/EngineMaterials/DefaultMaterial","propertyPath":"TwoSided","value":"True"}]}'
#   expect the op to RUN.  BEFORE: "op 'set_property' is not allowed inside batch".
#   The widget branch is still refused, now by name:
curl -s -X POST $B/batch "${H[@]}" -d "{\"compileAtEnd\":false,\"ops\":[{\"op\":\"set_property\",
  \"blueprintId\":\"/Game/UI/WBP_Test\",\"widgetName\":\"Title\",\"propertyPath\":\"ColorAndOpacity.A\",\"value\":1}]}"
#   expect ok:false naming widgetName, and stating the objectPath branch IS batchable

# --- regressions 11: 'op' is only tolerated where batch injects it -----------------------
curl -s -X POST $B/find_assets "${H[@]}" -d '{"pathPrefix":"/Game","op":"typo"}'
#   expect ok:false, "unrecognised parameter 'op'".  BEFORE: silently accepted.

# --- hazards 1: audit_unused refuses instead of freezing the editor ----------------------
curl -s -X POST $B/audit_unused "${H[@]}" -d '{"pathPrefix":"/Game","rescan":true}'
#   expect an IMMEDIATE ok:false naming the two-segment rule — and the bridge still answers:
curl -s -X POST $B/self_audit "${H[@]}" -d '{}' | head -c 40
#   BEFORE: a full synchronous re-scan of /Game with the ticker stopped; every other call timed out.

# --- anti-silence 7: the spline is not cleared until every point parses ------------------
curl -s -X POST $B/get_spline_points "${H[@]}" -d '{"actorPath":"BP_PatrolRoute"}'   # note pointCount
curl -s -X POST $B/set_spline_points "${H[@]}" -d '{"actorPath":"BP_PatrolRoute","points":[[0,0,0],[100,0,0]]}'
#   expect ok:false, "points[0] is not an object … The existing spline was NOT modified."
curl -s -X POST $B/get_spline_points "${H[@]}" -d '{"actorPath":"BP_PatrolRoute"}'   # SAME pointCount
#   BEFORE: ok:true, pointCount:0, and the patrol route destroyed.

# --- anti-silence 6: paint_landscape enforces the check its error text promised ----------
curl -s -X POST $B/paint_landscape "${H[@]}" -d '{"layerInfo":"/Game/Unrelated/LI_NotOnThisLandscape","center":{"x":0,"y":0},"radius":500}'
#   expect ok:false listing the landscape's real layers.
#   BEFORE: ok:true, verticesTouched:N — and it dimmed the layers actually in use.

# --- registry-buckets 1/2: the two broken MCP tools --------------------------------------
curl -s -X POST $B/describe_class    "${H[@]}" -d '{"className":"Actor"}'   # the spelling server.py sends
curl -s -X POST $B/list_enum_values  "${H[@]}" -d '{"enumName":"ECollisionChannel"}'
#   both expect ok:true.  BEFORE: "class is required" / "enum is required" — 100% failure over MCP.
```

---

**Verdict: source complete, UNBUILT and UNPROVEN.** The editor was running throughout and was
deliberately not restarted, so nothing in this section has executed. Every claim above is a source
change plus the command that will demonstrate it; treat the proofs as *owed* until a build lands.

**Registry counts after this batch:** `MIF_DECL` **191** = `MIF_BIND` **191** = `H_*` definitions
**191**, name-sets identical in both directions, zero duplicates. External `kr_*` **12**. Endpoints
**203**. `server.py` `@mcp.tool()` **203**, every one above the `if __name__` guard, every `_post`
target resolving to a real endpoint and every endpoint served by exactly one tool. **No endpoint was
added, removed or renamed in this batch** — the counts are unchanged from the pre-batch state by
design; only parameters, guards and buckets moved.

**Line endings:** every source file was written back in its own existing convention (the repo is
genuinely mixed: 20 LF `.cpp`, 16 CRLF `.cpp`). Verified after the batch: **no file in
`Source/MifBridge/` is internally mixed.** One pre-existing exception outside that tree —
`docs/02_GOTCHAS.md` was **already** mixed before this batch (lines 1-490 CRLF, 491-513 LF, from an
earlier session); its edits were applied byte-precisely against whichever ending each region uses, so
no line changed ending and the mixed state did not spread. It is worth normalising deliberately at
some point, which is a decision for the repo owner rather than a side effect of this batch.

---

## Batch L — property-write integrity (validate before import, verify the right object)

Three defects, all found by LIVE testing against the running editor, all in the same family: **a
write that reported success about something other than what the caller asked for.** No endpoint was
added, removed or renamed.

| # | Endpoint(s) | What the bridge answered | What was true |
|---|---|---|---|
| 1 | `set_actor_transform` (and every vector/rotator/scalar reader) | `ok:true`, and echoed a location | one component was silently discarded and its OLD value kept |
| 2 | `override_inherited_component` (and the shared property-text import path) | `ok:true, applied:true, wanted:"0.000000"` | the value was never understood; 0 was written and 0 was verified |
| 3 | `set_property` on a placed actor's component | `verified:true` | the verification read a `TRASH_*` object the construction-script rerun had already destroyed |

---

### Defect 1 — a non-numeric value was ignored PER FIELD

**Live evidence.**

```
set_actor_transform {actorPath:"RollbackProbe", location:{"x":"not-a-number","y":123,"z":456}}
  -> ok:true
  -> actor at {x:700, y:123, z:456}
```

`y` and `z` applied, `x` silently kept its previous value (700), and the response echoed that mixed
location, so it read as intentional. **The caller got a transform it never asked for and was told it
was the one requested.**

**Root cause, confirmed.** `MifBridgeLevel.cpp`'s `ReadVector` did
`JNum(Obj, TEXT("x"), OutVec.X)`, and `JNum` returns its `Default` for BOTH "the field is absent"
(correct — that is what makes "move only" work) and "the field is present but of the wrong JSON
type" (silent-ignore). Those are different facts and had one answer.

UE's own coercions make it worse than it looks, and they are why "TryGetNumber succeeded" is not the
same question as "the caller sent a number":

| engine | behaviour |
|---|---|
| `FJsonValueString::TryGetNumber(int32&)` — `JsonValue.h:135` | **always returns true**; `LexFromString` yields 0 for garbage. `JInt` on `"abc"` reported SUCCESS with 0. |
| `FJsonValueString::TryGetNumber(double&)` — `JsonValue.h:134` | accepts anything `FString::IsNumeric()` likes |
| `FJsonValueBoolean::TryGetNumber(double&)` — `JsonValue.h:201` | turns `true` into `1.0` |
| `FJsonValue::AsNumber()` | returns `0.0` for a string and cannot report that it did |

**Audit result — four duplicate readers and ~20 open-coded ones.** The same shape existed as
`ReadVector` (`MifBridgeLevel.cpp`), `ReadVec` (`MifBridgeWorld.cpp`), `ReadVec3`
(`MifBridgeComponents.cpp`, array-only, via `AsNumber()`), and `JNumFrom`+`ReadTransform`
(`MifBridgeAuthoring.cpp` — a private re-implementation of `JNum`), plus roughly twenty
`FVector(JNum(O,"x"),JNum(O,"y"),JNum(O,"z"))` sites in `MifBridgeLandscape.cpp`,
`MifBridgeNavigation.cpp`, `MifBridgePIE.cpp`, `MifBridgeSpatial.cpp`, `MifBridgeStreaming.cpp`,
`MifBridgeViewport.cpp`. That is PM-005's pattern again: four copies of one rule, so a fix to any one
of them reaches a quarter of the surface.

**Fix — two layers, one implementation each.**

*Layer 1: strict shared readers.* `MifBridgeHandlers.h` declares and `MifBridgeCommon.cpp` defines
**one** family, and the four duplicates are deleted:

```
bool      ParseWholeNumber(Text, OutValue)                 // the WHOLE string, never a prefix
bool      JsonValueAsNumber(Value, Where, Out, OutError)   // Number, or a string that parses whole
enum class EJsonRead { Absent, Read, Invalid }             // three states, not two
EJsonRead ReadNumberField (In, Field, Where, InOut, OutError)
EJsonRead ReadVectorField (In, Field, InOutVec, OutError)  // {x,y,z} | [x,y,z]
EJsonRead ReadRotatorField(In, Field, InOutRot, OutError)  // + {pitch,yaw,roll}
EJsonRead ReadScaleField  (In, Field, InOutVec, OutError)  // + a bare number = uniform
bool      ReadVectorObject(Obj, Where, InOutVec, OutError) // a points[] entry IS the vector
```

`Absent` keeps the incoming value (partial vectors stay legal). `Invalid` is a hard error naming the
field path (`location.x`), the offending value and the expected type. An unrecognised key **inside**
the vector object is refused too — `{"x":1,"y":2,"zz":3}` is a typo, not a 2D vector.

`ParseWholeNumber` is hand-scanned on purpose: Core exposes no strtod-with-end-pointer (`FCString`
has `Atod`, not `Strtod`), and every parser it does expose stops at the first character it cannot
read and reports success for the prefix it managed. `"12abc"` becomes 12 and `"not-a-float"` becomes
0, silently. It is the same function defect 2 uses, so the JSON side and the property-text side agree
on what a number is.

*Layer 2: a central backstop for everything not individually rewritten.* `JNum` / `JInt` / `JIntAny`
/ `JBool` / `JBoolAny` now RECORD a violation when a field is present with the wrong JSON type, and
`RunEndpoint` fails the response and cancels the transaction if anything was recorded. One place;
every endpoint inherits it, which is how the ~20 open-coded readers are covered without twenty edits.
`batch` attributes the violation to the op that caused it, by delta, so `ops[7]`'s bad parameter is
not reported against `ops[0]`.

Numeric **strings** stay accepted when they are entirely numeric (`"12.5"`) — callers legitimately
send those — but only entirely. `"12abc"` is refused on purpose.

**A second bug found while fixing the first.** `set_actor_transform`'s `relative:true` seeded its
deltas from the actor's CURRENT transform and then added the current transform again, so
`{relative:true, location:{"x":100}}` moved x correctly and **doubled y and z**. Relative mode now
seeds from zero. Same family: a transform the caller never asked for, echoed back as if they had.

`set_actor_transform` also had **no `RejectUnknownParams` guard at all**; it has one now, and the
response carries `locationApplied` / `rotationApplied` / `scaleApplied` so the echoed transform can
never be read as "all three applied as requested".

---

### Defect 2 — garbage coerced to a default and PASSED the post-write verification

**Live evidence.**

```
override_inherited_component {component:"Influence", properties:{"SphereRadius":"not-a-float"}}
  -> ok:true, applied:true, wanted:"0.000000"
```

**Root cause.** Nothing in the write bracket was broken. `ImportText_Direct` really did report
success, the publish really did happen, and the read-back really did equal what was staged. UE's
float importer (`PropertyNumeric.cpp:125-137`) accepts only `[+-.0-9]`, stops at the first character
it dislikes, and has **no "nothing consumed" guard** — so `"not-a-float"` parsed as `0.0`. The
verification then compared `after(0)` with `wanted(0)` and passed.

> **Verifying that the write LANDED does not verify that the VALUE WAS UNDERSTOOD.**
> The anti-silence guard added in Batch F cannot catch this class *by construction*: both sides of
> its comparison are derived from the same misparse, so they agree. The only place to catch it is
> BEFORE the import, against the destination property's type.

**Fix.** One shared validator, `ValidatePropertyText(Prop, Text, Where, OutError, bOutValidated)`,
declared in `MifBridgeHandlers.h` and defined once in `MifBridgeNodes5.cpp` beside `AcceptedFormHint`
(whose text its errors quote):

| property kind | rule |
|---|---|
| numeric | the WHOLE string must parse (`ParseWholeNumber`); a prefix like `"12abc"` is refused; **exponent form is refused** because UE's importer accepts only `[+-.0-9]` and would store just the mantissa (`1e5` -> 1); a trailing `f` is allowed on floats because the engine allows it; a fractional value on an integer property is refused (the importer stops at the `.`) |
| bool | a recognised literal (`True/False/Yes/No/On/Off/1/0`) — `FBoolProperty::ImportText` is word-based (`PropertyBool.cpp:384-397`) and takes an unrecognised word as **False** without reporting anything |
| enum / byte-enum | a real entry (bare or `Enum::`-qualified, authored display names included for Blueprint user enums) or a whole integer; the valid entries are LISTED in the error. An unrecognised entry name imports as 0 — the FIRST entry — which looks deliberate |
| hard object / class ref | a resolvable path or explicit `None`/null — checked after import in `CanonicaliseLeaf`, which is where the engine's own reference spellings are understood; UE stores null for an unresolvable path and reports success (`PropertyBaseObject.cpp:388/422`). **Soft** refs are deliberately not checked: a soft reference legitimately names an unloaded asset |
| struct / container as export text | **cannot be pre-validated reliably.** Returns true, sets `typeValidated:false` and a `typeValidationNote` saying the value was imported unchecked and recommending the typed-JSON form, which IS checked leaf by leaf. A stated non-guarantee, never a silent guess |

**Every path that turns caller input into property text now goes through it**, which was the point:

| call site | file | why it needed hooking separately |
|---|---|---|
| `CanonicaliseLeaf` | `MifBridgeNodes5.cpp` | the typed-JSON path — covers `set_property`, `override_inherited_component`, `set_variable_default`, and every nested container/struct leaf |
| `H_set_property` string fast-path | `MifBridgeNodes5.cpp` | **bypasses `JsonToPropertyText` entirely** — a string reaches `ImportText_Direct` byte-for-byte, so `set_property {value:"not-a-float"}` had exactly the defect this batch is named after |
| `ApplyOneProperty` string fast-path | `MifBridgeInherited.cpp` | the same bypass — and the actual endpoint the defect was caught on, because `"not-a-float"` is a JSON string |
| `ImportExpressionProperty` | `MifBridgeMaterials.cpp` | a sibling converter that emits text from the JSON value's SHAPE rather than the destination property's TYPE |

`set_property` reports `typeValidated` (and `typeValidationNote` where it could not check);
`override_inherited_component` reports both per property row. PM-003's scratch-buffer discipline is
untouched — validation happens before the scratch import, and the scratch import is still the only
thing that touches a value.

---

### Defect 3 — `set_property`'s verification could read a TRASHED object

From `docs/audit/work/R1_DETAILS_PANEL_PARITY.md` gaps **G4** and **G5** (task #8).

**The mechanism, in engine source.** On a PLACED ACTOR's component the bridge's own
`PreEditChange`/`PostEditChange` pair triggers the rerun itself:

```
ActorComponent.cpp:806-822   UActorComponent::PreEditChange
    if (IsRegistered()) { EditReregisterContexts.Add(this, new FComponentReregisterContext(this)); }
ActorComponent.cpp:927-941   UActorComponent::ConsolidatedPostEditChange
    if (MyOwner && !MyOwner->IsTemplate() && ChangeType != Interactive) { MyOwner->RerunConstructionScripts(); }
ActorConstruction.cpp:167-210
    Component->DestroyComponent();
    Component->Rename(*MakeUniqueObjectName(this, GetClass(), FName(*FString::Printf(TEXT("TRASH_%s"), ...))).ToString(), ...);
```

A placed actor's components are registered and the bridge sends `ValueSet`, not `Interactive`, so
**every** `set_property` on a placed actor's component reruns that actor's construction scripts. The
component is destroyed and renamed `TRASH_<Class>_N`; a NEW component of the same name replaces it.
The verification re-read at `MifBridgeNodes5.cpp` (~`:863`) then read the trashed object, found the
value it had just written there, and reported `applied:true, verified:true` **about an object that is
no longer part of the actor** — and a use-after-free once GC runs.

**Fix — re-resolve before verifying.** After the notification returns, if `LeafOwner` or `Target` is
invalid or named `TRASH_*`, the original object path is re-resolved with `StaticFindObject` (never
`StaticLoadObject`: the package is already loaded and nothing here may resurrect anything), the
property path is re-walked on the NEW object, and the read-back is taken from that. If it cannot be
re-resolved the call **FAILS as unverified**, naming the reconstruction — it never falls back to the
trashed pointer. New response fields: `reconstructed`, `retargetedTo`, `verifiedOn`.

A consequence worth stating: an instance edit of a property `FComponentInstanceDataCache` skips
(transient, no `EditAnywhere`/`Interp`, multicast delegate, or written by the construction script —
`ComponentInstanceDataCache.cpp:54-66,171`) will now HONESTLY fail the anti-silence guard, with the
skip rules and the `…_GEN_VARIABLE` template route named in the error. It used to report success
about the trashed copy.

**Fix — the notification, and the false comment.** The code said:

```cpp
LeafOwner->PostEditChangeProperty(Evt);       // propagates to instances/archetype
```

**It does not.** `UObject::PostEditChangeProperty` is a delegate broadcast plus an interactive
snapshot and nothing else (`Obj.cpp:433-444`). `PostEditChangeChainProperty` is the one that walks
`GetArchetypeInstances` (`Obj.cpp:501-509`) — and it ends by calling `PostEditChangeProperty` anyway
(`Obj.cpp:541`), so **switching is a strict superset**. The comment is corrected in place and the
call switched. Also reached now: the 40 `PostEditChangeChainProperty` overrides in
`Runtime/Engine/Private` that never fired, among them `UMeshComponent::CleanUpOverrideMaterials`
(`MeshComponent.cpp:155-166`).

Building the chain needed the walker to stop discarding what it knew: `ResolvePropertyPathChain`
(new, in `MifBridgeCommon.cpp`) returns every segment's `FProperty`, and `ResolvePropertyPath`
forwards to it — one walker, per PM-005. The chain RESTARTS when the path crosses an
`FObjectProperty`, because the chain must be relative to the object the notification fires on;
`PropagatePostEditChange` `check()`s the active member node (`Obj.cpp:660`), so the chain is either
built in full or not used at all, never handed over half-built. `MemberProperty` is set to the
OUTERMOST member as the Details panel does (`PropertyNode.cpp:3081-3083`) — `AActor::PostEditChangeProperty`
switches on it (`ActorEditor.cpp:134-135`), so member-keyed handlers never fired for a dotted path
like `Settings.BloomIntensity`. New response fields: `notification` (`chain`|`plain`),
`memberProperty`, `chainDepth`.

---

### Files changed

| file | change |
|---|---|
| `Source/MifBridge/Private/MifBridgeHandlers.h` | declares the strict-reader family, the violation backstop, `ValidatePropertyText`, `ResolvePropertyPathChain` |
| `Source/MifBridge/Private/MifBridgeCommon.cpp` | defines all of the above; `JNum`/`JInt`/`JIntAny`/`JBool`/`JBoolAny` record violations; `RunEndpoint` resets and reports them on BOTH exit paths; `ResolvePropertyPathChain` |
| `Source/MifBridge/Private/MifBridgeNodes5.cpp` | `ValidatePropertyText` + `ListEnumEntries`; `CanonicaliseLeaf` and `set_property`'s string path validate first; chain notification; re-resolve-before-verify |
| `Source/MifBridge/Private/MifBridgeInherited.cpp` | `ApplyOneProperty` validates the string path; `typeValidated`/`typeValidationNote` per row |
| `Source/MifBridge/Private/MifBridgeMaterials.cpp` | `ImportExpressionProperty` validates before importing |
| `Source/MifBridge/Private/MifBridgeLevel.cpp` | local `ReadVector` deleted; `set_actor_transform` strict + guarded + relative-seeding fixed; `spawn_actor_in_level` strict |
| `Source/MifBridge/Private/MifBridgeWorld.cpp` | local `ReadVec` deleted; `set_spline_points` validates every point BEFORE clearing the spline |
| `Source/MifBridge/Private/MifBridgeComponents.cpp` | local `ReadVec3` deleted; `add_component` + `set_component_transform` strict, and both accept `{x,y,z}` as well as `[x,y,z]` |
| `Source/MifBridge/Private/MifBridgeAuthoring.cpp` | `JNumFrom` deleted; `ReadTransform` strict and per-item, naming `items[N]` / `instances[N]`; `duplicate_actors` offset strict |
| `Source/MifBridge/Private/MifBridgeNodes.cpp` | `batch` attributes an ignored parameter to the op that caused it |
| `tools/ue5-mcp-bridge/server.py` | 10 tool docstrings restated (no signature changed) |

**Registry counts after this batch:** `MIF_DECL` **191** = `MIF_BIND` **191** = `H_*` definitions
**191**. External `kr_*` **12**. Endpoints **203**. `server.py` `@mcp.tool()` **203**; parity diff
empty in the endpoint→tool direction and exactly the 12 `kr_*` in the other. **No endpoint was added,
removed or renamed** — only readers, validators, guards and response fields changed.

**Line endings:** every file was written back in its own existing convention, verified with Python
and `newline=''` (`grep -c $'\r$'` under Git Bash on this machine misreports, so it was not used). No
file under `Source/MifBridge/` or `tools/` is internally mixed.

---

### Proof calls

**Status: source complete, UNBUILT and UNPROVEN.** The editor was running throughout and was
deliberately not restarted. Every command below is owed, not observed.

```bash
B=http://127.0.0.1:8791/api
H=(-H "X-Mif-Token: ${MIF_BRIDGE_TOKEN:-dev}" -H "Content-Type: application/json")

# --- defect 1: the exact call that failed ------------------------------------------------
curl -s -X POST $B/set_actor_transform "${H[@]}" \
  -d '{"actorPath":"RollbackProbe","location":{"x":"not-a-number","y":123,"z":456}}'
#   expect ok:false naming 'location.x', the string "not-a-number", and "a number".
#   BEFORE: ok:true with the actor at {700,123,456}.
curl -s -X POST $B/list_level_actors "${H[@]}" -d '{"nameContains":"RollbackProbe"}'
#   expect the location UNCHANGED — a rejected component must not half-apply.

# --- defect 1: the backstop, on an endpoint with no strict reader -------------------------
curl -s -X POST $B/list_level_actors "${H[@]}" -d '{"limit":"abc"}'
#   expect ok:false, ignoredParameters naming 'limit'.
#   BEFORE: FJsonValueString::TryGetNumber(int32&) returned TRUE with 0, so limit became 0.

# --- defect 1: a partly-numeric string is refused, a wholly-numeric one is accepted -------
curl -s -X POST $B/set_actor_transform "${H[@]}" -d '{"actorPath":"RollbackProbe","location":{"x":"12abc"}}'
#   expect ok:false — UE's parsers would take the 12 and discard the rest.
curl -s -X POST $B/set_actor_transform "${H[@]}" -d '{"actorPath":"RollbackProbe","location":{"x":"12"}}'
#   expect ok:true, x=12.

# --- defect 1: the relative double-application ------------------------------------------
curl -s -X POST $B/set_actor_transform "${H[@]}" -d '{"actorPath":"RollbackProbe","location":{"x":0,"y":500,"z":500}}'
curl -s -X POST $B/set_actor_transform "${H[@]}" -d '{"actorPath":"RollbackProbe","relative":true,"location":{"x":100}}'
#   expect {100,500,500}.  BEFORE: {100,1000,1000} — y and z doubled.

# --- defect 1: an unknown component key inside the vector --------------------------------
curl -s -X POST $B/set_actor_transform "${H[@]}" -d '{"actorPath":"RollbackProbe","location":{"x":1,"y":2,"zz":3}}'
#   expect ok:false naming 'zz'.  BEFORE: silently a 2D move with z kept.

# --- defect 2: the exact call that failed ------------------------------------------------
curl -s -X POST $B/override_inherited_component "${H[@]}" \
  -d '{"blueprint":"/Game/BP/BP_Child","component":"Influence","properties":{"SphereRadius":"not-a-float"}}'
#   expect ok:false, propertiesApplied:0, the row's reason naming SphereRadius, "not-a-float",
#   PropertyNumeric.cpp's missing "nothing consumed" guard, and the accepted form.
#   BEFORE: ok:true, applied:true, wanted:"0.000000".

# --- defect 2: the same class through set_property's STRING fast path ---------------------
curl -s -X POST $B/set_property "${H[@]}" \
  -d '{"objectPath":"/Game/BP/BP_Child.BP_Child_C:Influence_GEN_VARIABLE","propertyPath":"SphereRadius","value":"not-a-float"}'
#   expect ok:false.  BEFORE: ok:true, applied:true, verified:true, valueAfter "0.000000".

# --- defect 2: bool, enum and exponent --------------------------------------------------
curl -s -X POST $B/set_property "${H[@]}" -d '{"objectPath":"/Game/BP/BP_Child.Default__BP_Child_C","propertyPath":"bReplicates","value":"banana"}'
#   expect ok:false — FBoolProperty::ImportText would have taken "banana" as False.
curl -s -X POST $B/set_property "${H[@]}" -d '{"objectPath":"/Game/BP/BP_Child.BP_Child_C:Influence_GEN_VARIABLE","propertyPath":"Mobility","value":"Movabel"}'
#   expect ok:false LISTING the EComponentMobility entries. BEFORE: imported as 0 = Static.
curl -s -X POST $B/set_property "${H[@]}" -d '{"objectPath":"/Game/BP/BP_Child.BP_Child_C:Influence_GEN_VARIABLE","propertyPath":"SphereRadius","value":"1e3"}'
#   expect ok:false naming exponent notation.  BEFORE: stored 1.0.
curl -s -X POST $B/set_property "${H[@]}" -d '{"objectPath":"/Game/BP/BP_Child.BP_Child_C:Influence_GEN_VARIABLE","propertyPath":"SphereRadius","value":"250.0"}'
#   expect ok:true, typeValidated:true — the control, so the guard is not just refusing everything.

# --- defect 3: the verification reads the LIVE object, not the trashed one ----------------
curl -s -X POST $B/list_level_actors "${H[@]}" -d '{"nameContains":"BP_Lamp"}'      # take an actorPath
curl -s -X POST $B/set_property "${H[@]}" \
  -d '{"objectPath":"<actorPath>.LightComponent0","propertyPath":"Intensity","value":"5000.0"}'
#   expect reconstructed:true, retargetedTo:"<actorPath>.LightComponent0", verifiedOn == retargetedTo,
#   notification:"chain", memberProperty:"Intensity", chainDepth:1.
#   Then prove the object read is the LIVE one, not a TRASH_* twin:
curl -s -X POST $B/get_property "${H[@]}" -d '{"objectPath":"<actorPath>.LightComponent0","propertyPath":"Intensity"}'
#   expect 5000 — the same number set_property reported, read through a FRESH resolve.
#   BEFORE: set_property reported verified:true off the trashed pointer, and this second call
#   could disagree with it.

# --- defect 3: a property the instance cache cannot carry now fails HONESTLY --------------
curl -s -X POST $B/set_property "${H[@]}" \
  -d '{"objectPath":"<actorPath>.LightComponent0","propertyPath":"<a transient or non-Edit UPROPERTY>","value":"1"}'
#   expect ok:false naming ComponentInstanceDataCache.cpp:54-66 and the …_GEN_VARIABLE route.
#   BEFORE: verified:true, about a destroyed object.
```

---

### STILL UNPROVEN, and the test that would prove it: transaction rollback on failure

> **RESOLVED, 2026-07-29 — THE TEST BELOW WAS RUN AND IT FAILED AT STEP 2.** `overrideExists` came
> back **`true`**, with `queueLength` unchanged. The rollback guarantee is **FALSE**, and not only for
> creations: `UTransBuffer::Cancel` never calls `FTransaction::Apply` at all. The corrected test, the
> fix and the per-handler audit are in the *Batch M* section at the end of this file; the general
> rule is PM-007. Everything below this box is preserved as it was written, including the sentence
> "`Cancel()` applies", which is the assumption the run disproved.

`RunEndpoint` calls `Transaction.Cancel()` when a handler fails
(`MifBridgeCommon.cpp`, "FAILURE ROLLS BACK"). **This has never been demonstrated**, because no call
had been found that MUTATES and then GENUINELY FAILS — every candidate either failed before mutating
or succeeded. It is asserted, not tested, and until the test below runs it should be treated as
unverified.

**Defect 2's fix creates the missing call.** `override_inherited_component` mints the ICH override
record FIRST (`Blueprint->Modify()`, `ICH->Modify()`, `CreateOverridenComponentTemplate` —
`MifBridgeInherited.cpp:818-833`) and applies `properties` AFTERWARDS; a property failure ends in
`Fail()` at the tail of the handler. Before this batch, `"not-a-float"` did not fail — it imported as
0 and reported success — so the mutate-then-fail path was unreachable. **It is reachable now.** The
endpoint is in RunEndpoint's default bucket (not read-only, not self-managed — confirmed against
`IsSelfManagedEndpoint`), so it rides the blanket transaction and `Cancel()` applies.

```bash
# 0. BASELINE — the override must not exist yet, and note the transaction count.
curl -s -X POST $B/get_inherited_component "${H[@]}" \
  -d '{"blueprint":"/Game/BP/BP_Child","component":"Influence"}'
#   record: overrideExists (expect false) and overrideTemplatePath (expect absent)
curl -s -X POST $B/list_transactions "${H[@]}" -d '{"limit":5}'
#   record queueLength -> CALL THIS N, and transactions[0].id / .title

# 1. THE MUTATE-THEN-FAIL CALL. It mints the override (a real, transacted mutation) and THEN
#    fails on the property, which is exactly the shape that had never been exercised.
curl -s -X POST $B/override_inherited_component "${H[@]}" \
  -d '{"blueprint":"/Game/BP/BP_Child","component":"Influence","properties":{"SphereRadius":"not-a-float"}}'
#   expect ok:false, created:true in the body, propertiesApplied:0, propertiesFailed:1

# 2. THE ASSERTION THAT MAKES IT A TEST — the mutation must be GONE.
curl -s -X POST $B/get_inherited_component "${H[@]}" \
  -d '{"blueprint":"/Game/BP/BP_Child","component":"Influence"}'
#   expect overrideExists:false — IDENTICAL to step 0.
#   If it is true, Transaction.Cancel() did not roll back the ICH record and the guarantee is FALSE.

# 3. AND NO UNDO STEP MAY BE LEFT BEHIND. A cancelled transaction is discarded, not recorded;
#    a rollback that leaves a stack entry would make the NEXT Ctrl-Z undo the user's own work.
curl -s -X POST $B/list_transactions "${H[@]}" -d '{"limit":5}'
#   expect queueLength == N and transactions[0].id UNCHANGED from step 0.

# 4. CONTROL — the same call with a VALID value must mint the override and keep it, so step 2
#    is proving a rollback rather than an endpoint that never worked.
curl -s -X POST $B/override_inherited_component "${H[@]}" \
  -d '{"blueprint":"/Game/BP/BP_Child","component":"Influence","properties":{"SphereRadius":"250.0"}}'
#   expect ok:true, created:true, propertiesApplied:1
curl -s -X POST $B/get_inherited_component "${H[@]}" \
  -d '{"blueprint":"/Game/BP/BP_Child","component":"Influence"}'
#   expect overrideExists:true, and list_transactions queueLength == N+1 with
#   transactions[0].title "Mif Bridge: override_inherited_component".

# 5. CLEAN UP so the probe is repeatable.
curl -s -X POST $B/revert_inherited_component "${H[@]}" \
  -d '{"blueprint":"/Game/BP/BP_Child","component":"Influence","confirm":true}'
```

**Why this call and not another.** The test needs all four of: (a) a mutation that is transacted and
externally observable, (b) a failure that happens strictly AFTER it, (c) an endpoint in the blanket-
transaction bucket, and (d) a read-back verb that can see the mutation from outside. `add_component`
plus a bad transform satisfies (a)-(c) and is a good second case, but `list_components` on a rolled-back
SCS node is a weaker observation than `get_inherited_component`'s explicit `overrideExists`. Steps 3 and
4 are what make it a test rather than a demonstration: without the control, an endpoint that silently
does nothing passes step 2.

**Second case, for a different bucket boundary.** `add_component` with
`{"location":{"x":"not-a-number"}}` now fails AFTER the SCS node is created, so `list_components`
must not show the new component afterwards. Worth running because it exercises a different handler
and a different mutation kind (SCS node vs ICH record) through the same central guarantee.

**Known limitation, stated rather than discovered later.** The rollback guarantee covers the
default bucket only. Read-only and **self-managed** endpoints run outside the blanket transaction by
design, so for those the violation report says so verbatim: *"This endpoint manages its own
transactions (it compiles, or it is batch), so any write it completed before this check still stands
— re-read the target before retrying."* `batch` is in that bucket, so a type violation inside a batch
op fails the envelope and names the op, but batch's own single transaction still commits. Closing
that would mean cancelling batch's transaction on any op failure, which is a behaviour change to
batch's partial-success contract and is deliberately NOT made here.

---

## Batch M — orphaned creations on failure paths

**One sentence.** A cancelled transaction does not undo object creation — it does not undo anything —
so every handler that creates first and validates second leaves its creation behind on a call that
reported failure; the fix is order, and this batch reorders the ones that can be reordered and states
what the rest leave behind.

### The live evidence

Batch L's *STILL UNPROVEN* test (above) was run against
`/Game/MODS/BotanistExpansion_p/Blueprints/NPCs/NPC_MifAmbient`. **It failed at step 2.**

```
0. get_inherited_component {blueprint:"…/NPC_MifAmbient", component:"Influence"}
   -> overrideExists: false                       (baseline, as required)
   list_transactions -> queueLength: 0            (call this N)

1. override_inherited_component {blueprint:"…/NPC_MifAmbient", component:"Influence",
                                 properties:{"SphereRadius":"not-a-float"}}
   -> ok: false                                   CORRECT — Batch L's validator fired

2. get_inherited_component {…}                    THE ASSERTION
   -> overrideExists: TRUE                        *** FAILED. Expected false. ***

3. list_transactions -> queueLength: 0            unchanged: the cancel DID fire and left no entry
```

So `Transaction.Cancel()` ran, no undo step was created — **and the override was still on the asset.**
A failed call had permanently added an ICH override to the user's Blueprint. The child now shadows the
parent for that component: a silent behaviour change to their asset, from a call that told them it had
failed. This is the mirror image of the `ok:true`-did-nothing class this codebase keeps paying for,
and it is worse, because the caller is *told* it failed and therefore never goes looking.

### Why the cancel could not help — two engine facts, either one sufficient

Both read directly out of `D:/UE532`, not inferred:

| # | Fact | Where |
|---|---|---|
| 1 | `SaveToTransactionBuffer` stores an object only if it has `RF_Transactional` | `Runtime/CoreUObject/Private/UObject/UObjectGlobals.cpp:3131-3134` |
| 1a | The ICH is `NewObject<UInheritableComponentHandler>(this, FName(...))` — **no flags** | `Runtime/Engine/Private/BlueprintGeneratedClass.cpp:1202` |
| 1b | The override template is `NewObject<UActorComponent>(..., RF_ArchetypeObject \| RF_Public \| RF_InheritableComponentTemplate, BestArchetype)` — **not transactional** | `Runtime/Engine/Private/InheritableComponentHandler.cpp:159-160` |
| 2 | `UTransBuffer::Cancel` broadcasts `TransactionCanceled`, calls `GUndo->EndOperation()`, nulls `GUndo`, `UndoBuffer.Pop(false)`, restores `RemovedTransactions`, resets `ActiveCount`. **It never calls `FTransaction::Apply()`.** | `Editor/UnrealEd/Private/EditorTransaction.cpp:1387-1437` |
| 2a | `FTransaction::Apply()` has exactly two callers in the transaction system: `UTransBuffer::Undo` and `UTransBuffer::Redo` | `EditorTransaction.cpp:1624`, `:1688` |
| 2b | The engine's own doc for the virtual: *"Cancels the current transaction, no longer capture actions to be placed in the undo buffer"* — nothing about reverting | `Editor/UnrealEd/Classes/Editor/Transactor.h:514-519` |

Fact 1 alone means `ICH->Modify()` recorded nothing. Fact 2 means it would not have mattered if it
had. **`Cancel` = throw the record away. It has never meant "roll back".**

`Cancel()` is still called, and should be: a failed call must not leave an entry on the undo stack, or
the user's next Ctrl-Z undoes a *failed* bridge action instead of their own last edit. That is the
whole benefit, and it is real. It is just not atomicity.

### The reorder

`H_override_inherited_component` (`Source/MifBridge/Private/MifBridgeInherited.cpp`) now runs
**guards → preflight → create → apply**:

1. **Guards with nothing created.** `GetHandlerForOverride(..., bCreateIfNecessary=false)` runs the
   key-validity / parentage / has-a-generated-class checks. `IsOk(Out)` separates "a guard failed"
   from "this blueprint simply has no handler yet", exactly as `revert_inherited_component` does.
2. **Preflight.** Every entry in `properties` is resolved and type-checked against a **probe object**
   — the existing override when there is one, otherwise the parent's `ComponentTemplate`, which is
   the archetype `CreateOverridenComponentTemplate` duplicates
   (`InheritableComponentHandler.cpp:159-160`). Same class, same struct layout, same array lengths,
   so every question the preflight asks has the same answer on the probe as on the template that
   would be minted. It runs through `PrepareOneProperty` — the front half of `ApplyOneProperty`,
   split out so **the preflight and the writer are literally the same code** and cannot drift. It
   writes nothing: `ResolvePropertyPath` only walks (`MifBridgeCommon.cpp:1340-1423`) and both
   validators work on a scratch buffer.
   A refusal returns `ok:false` with `created:false`, `nothingModified:true`,
   `outcome:"preflight-rejected-nothing-created"`, `validatedAgainst:<probe path>` and one
   `properties[]` row per requested name (`stage:"preflight"`, `validated:true|false`, plus the same
   `reason` / `typeValidated` / `typeValidationNote` an applied call reports).
3. **Create**, then **apply**, unchanged.
4. **Belt and braces.** If an apply still fails for something no type check can predict — a
   `ClampMin`, a component's own `PostEditChangeProperty`, a `CanEditChange` refusal — the handler
   calls `RemoveOverridenComponentTemplate` itself and reports
   `outcome:"created-then-removed-on-failure"`, `overrideRemovedOnFailure:true`,
   `removedTemplatePath`. **Only when `created` was true in this call.** A pre-existing override is
   never removed (`outcome:"pre-existing-override-kept"`, `overrideRemovedOnFailure:false`), because
   deleting overrides the caller already had would be a worse bug than the one being fixed — it is
   why `revert_inherited_component` is confirm-gated.

One residue is stated rather than hidden: if the call also minted the blueprint's *first*
`UInheritableComponentHandler` and then failed, that empty handler stays (zero records, no
behavioural effect — the same object the editor creates the first time you override anything). There
is no engine API to unassign it. The failure text says so.

`outcome` is emitted on every path: `created`, `updated-existing`,
`preflight-rejected-nothing-created`, `created-then-removed-on-failure`,
`pre-existing-override-kept`, `create-returned-null`.

### The audit — every handler that creates a UObject/asset/component/template

Method: grep the module for `NewObject<`, `FactoryCreateNew`, `SpawnActor*`, `CreatePackage`,
`CreateNewGraph`, `AddFunctionGraph`, `Graph->AddNode`/`PlaceAndInit`,
`CreateOverridenComponentTemplate`, `CreateMaterialExpression`, `AddRow`, `AddInstance`,
`DuplicateObject`, `AssetCreated`, then for each hit ask whether a **fallible step follows in the
same handler**. 54 handlers create something; 26 of them have a `Fail()` after the creation.

| Handler | Creates | Fallible step after? | Verdict |
|---|---|---|---|
| `override_inherited_component` | ICH + override template | property apply | **DEFECT — FIXED.** Reorder (a) + cleanup (b). The one proved live. |
| `add_component` | `USCS_Node` + component template | `location`/`rotation`/`scale` read | **DEFECT — FIXED (a).** The scene-component test is a property of the CLASS and the seed values are the class default, which is what `SCS->CreateNode` initialises the template from — so both moved above `CreateNode`. Its old comment explicitly claimed the cancel rolled the node back. |
| `add_foliage_instances` | holder `AActor` + HISM + N instances | per-instance transform read | **DEFECT — FIXED (a).** The whole `instances[]` array is parsed into `TArray<FTransform>` before the actor is spawned. Old comment claimed the rollback. |
| `add_timeline` | `UK2Node_Timeline` + `UTimelineTemplate` + `UCurveFloat` per track | `floatTracks[]` entries; null template | **DEFECT — FIXED (a) + (c).** Track names validated before the node exists. The null-`UTimelineTemplate` branch cannot be predicted and unwinding a timeline is not something the bridge has a safe API for, so it now names the node it left in the graph and tells you to `delete_node` it. Emits `leftBehind`. |
| `create_material_instance` | `UPackage` + `UMaterialInstanceConstant` | `scalars`/`vectors` validation | **DEFECT — FIXED (a).** The validation block was already "before a single write" — but the asset is a write. Hoisted above `CreatePackage`. Self-managed, so there was not even a transaction to cancel. |
| `add_pin` | `UK2Node_FunctionResult` + user pins on N Return nodes | `CreateUserDefinedPin` on a sibling | **DEFECT — (c).** Unwinding means deleting user pins that may already be wired — that is `remove_pin`'s confirm-gated job. Both failure texts now name the count of siblings already updated, the Result node if this call made one, and `remove_pin {confirm:true}` as the repair. |
| `recipe_add_debug_print` | `UK2Node_CallFunction` | `afterNode` resolve, `SpliceAfter` | **DEFECT — (c).** Self-managed: it opens its own `FScopedTransaction`, which **commits** on return, so there was never even a cancel here. Both failure texts now say the Print String node is in the graph, unwired, and name `delete_node`. |
| `create_struct` | `UPackage` + `UUserDefinedStruct` | member name / `MakePinType` / `AddStructMemberNamed` | **DEFECT — (c).** Never registered (`AssetCreated`/`MarkPackageDirty` are at the tail) so nothing reaches the content browser or disk, but the package path is taken for the rest of the session. The failure text says so and says to use a different path. |
| `set_variable_flags` | `UEdGraph` (OnRep function) | `replicationCondition` parse | **DEFECT — (c).** Already emitted `createdRepNotifyGraph`; the error string now points at it. |
| `add_material_expression` | `UMaterialExpression` | property import | **ALREADY CORRECT.** Calls `DeleteMaterialExpression`/`…InFunction` on failure. The pattern to copy. |
| `add_tree_widget` | `UWidget` via `ConstructWidget` | `AddChild`, slot class | **ALREADY CORRECT.** `MarkAsGarbage()` on failure, and its comment already knew the transaction would not do it. |
| `add_event_dispatcher` | member variable + signature `UEdGraph` | `CreateNewGraph` | **ALREADY CORRECT.** `RemoveMemberVariable` on failure. |
| `spawn_actor_in_level` | `AActor` | `mesh` resolve | **ALREADY CORRECT.** Transform validated first; `Actor->Destroy()` on both mesh failures. |
| `spawn_actor_in_pie` | `AActor` | `mesh` resolve + read-back | **ALREADY CORRECT.** `Destroy()` on all three, including the "the mesh did not take" read-back. |
| `create_landscape` | `ALandscape` | `material` load | **ALREADY CORRECT.** `Landscape->Destroy()` on failure. |
| `add_switch_string` | `UK2Node_SwitchString` | `cases[]` entries | **ALREADY CORRECT — by construction.** `NewObject` produces a dangling node; `PlaceAndInit` (which calls `Graph->AddNode`) runs *after* the `cases` loop, so a refusal never puts it in the graph. "Nothing was kept" is literally true here. |
| `capture_camera` | render target + `ASceneCapture2D` | capture component missing | **ALREADY CORRECT.** `Cap->Destroy()`; the actor is `RF_Transient` and the RT lives in the transient package. Read-only bucket, dirties nothing. |
| `create_material` | `UPackage` + `UMaterial` | — | OK. Only creation-null failures after the creation. |
| `create_material_function` | `UPackage` + `UMaterialFunction` | — | OK. Same shape. |
| `create_blueprint` | `UPackage` + `UBlueprint` | — | OK. Nothing fallible between `CreateBlueprint` and `AssetCreated`. |
| `create_enum` | `UPackage` + `UUserDefinedEnum` | — | OK. The `values[]` loop emits warnings, never a `Fail`. |
| `implement_interface_function` | `UEdGraph` + terminators | — | OK. The only failure is `CreateNewGraph` returning null. |
| `add_nav_volume` | `ANavMeshBoundsVolume` | — | OK. Only the spawn-null failure. |
| `bind_landscape_rvt` | `ARuntimeVirtualTextureVolume` ×N | — | OK. No failure after the spawns. (Its *semantic* hazard is a separate postmortem.) |
| `spawn_many` | `AActor` ×N | — | OK. A null spawn is reported per-item; partial success is this endpoint's stated contract, not a failure path. |
| `duplicate_actors` | `AActor` ×N | — | OK. Same contract. |
| `write_datatable_rows` | rows via `FDataTableEditorUtils::AddRow` | — | OK. Per-row problems are `warnings[]`, never a `Fail`; the only `Fail` after the loop is the non-editor `#else` branch. |
| `create_function` | Entry/Result terminators | — | OK. Self-managed; no `Fail` after the terminators exist. |
| `add_function_call`, `add_branch`, `add_macro_instance`, `add_get_array_item`, `add_override_event`, `add_parent_call`, `add_cast`, `add_self`, `add_custom_event`, `add_make_struct`, `add_break_struct`, `add_literal`, `add_class_cast`, `add_switch_enum`, `add_switch_int`, `add_enum_literal`, `add_sequence`, `add_spawn_actor`, `add_create_widget`, `add_get_subsystem`, `add_make_array`, `add_make_map`, `add_format_text`, `add_get_data_table_row`, `add_comment`, `add_enhanced_input_action` | one `UK2Node` each | — | OK, all 26. Every one of them resolves and validates its inputs *before* `NewObject`, and has no `Fail()` after the node is placed. This is the house style and it is the reason the defect showed up in the ICH endpoint rather than in the node adders. |
| `recipe_reset_and_loop`, `recipe_argmax_over_components` | several `UK2Node`s | — | OK. No `Fail()` after the first creation; wiring problems are reported as counters, not failures. |
| `start_pie` | `DuplicateObject<ULevelEditorPlaySettings>` | — | OK. Transient settings object, never an asset. |
| `add_sublevel`, `remove_sublevel`, `set_sublevel_streaming`, `pie_load_level_instance`, `pie_unload_level_instance` | `ULevel` / `ULevelStreaming` | deferred | OK. All defer their engine call to the next tick and report through an op log, so there is no in-handler create-then-fail window at all. |
| `rename_variable`, `remove_variable` | — (mutate, not create) | read-back | **Comment corrected only.** Both Fail when the engine call did not take, so there is nothing to undo — but both comments cited the cancel as a general guarantee. Reworded. |

### The re-run that must now pass

Same five steps as the original test, with the assertions corrected for what `created` now means.
`$B` = `http://127.0.0.1:8791/api`, `${H[@]}` = the token header.

```bash
BP=/Game/MODS/BotanistExpansion_p/Blueprints/NPCs/NPC_MifAmbient

# 0. BASELINE. The override must not exist, and note the transaction count.
curl -s -X POST $B/get_inherited_component "${H[@]}" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Influence\"}"
#   REQUIRE overrideExists: false          <- if true, run step 5 first and start again
curl -s -X POST $B/list_transactions "${H[@]}" -d '{"limit":5}'
#   record queueLength -> N, and transactions[0].id

# 1. THE FAILING CALL.
curl -s -X POST $B/override_inherited_component "${H[@]}" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Influence\",\"properties\":{\"SphereRadius\":\"not-a-float\"}}"
#   REQUIRE ok:false
#   REQUIRE created:false                  <- WAS created:true. This is the fix.
#   REQUIRE nothingModified:true
#   REQUIRE outcome:"preflight-rejected-nothing-created"
#   REQUIRE propertiesFailed:1, propertiesApplied:0
#   REQUIRE properties[0].stage:"preflight", properties[0].validated:false
#   REQUIRE validatedAgainst names the PARENT's template, not a new override

# 2. THE ASSERTION. This is the step that failed before.
curl -s -X POST $B/get_inherited_component "${H[@]}" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Influence\"}"
#   REQUIRE overrideExists: false          <- IDENTICAL to step 0
#   REQUIRE existingOverrideCount unchanged from step 0
#   REQUIRE no overrideTemplatePath field

# 3. NO UNDO STEP LEFT BEHIND.
curl -s -X POST $B/list_transactions "${H[@]}" -d '{"limit":5}'
#   REQUIRE queueLength == N and transactions[0].id UNCHANGED from step 0

# 4. CONTROL — the same call with a VALID value must still work, or step 2 is proving
#    an endpoint that does nothing rather than a fix.
curl -s -X POST $B/override_inherited_component "${H[@]}" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Influence\",\"properties\":{\"SphereRadius\":\"250.0\"}}"
#   REQUIRE ok:true, created:true, outcome:"created", propertiesApplied:1
curl -s -X POST $B/get_inherited_component "${H[@]}" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Influence\"}"
#   REQUIRE overrideExists:true
curl -s -X POST $B/list_transactions "${H[@]}" -d '{"limit":5}'
#   REQUIRE queueLength == N+1, transactions[0].title "Mif Bridge: override_inherited_component"

# 4b. THE NEW ASSERTION — a pre-existing override is NOT destroyed by a failing call.
curl -s -X POST $B/override_inherited_component "${H[@]}" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Influence\",\"properties\":{\"SphereRadius\":\"still-not-a-float\"}}"
#   REQUIRE ok:false, created:false, nothingModified:true, overrideExisted:true
curl -s -X POST $B/get_inherited_component "${H[@]}" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Influence\"}"
#   REQUIRE overrideExists:true AND the SphereRadius written in step 4 is still 250.0
#          (get_property on overrideTemplatePath)

# 5. CLEAN UP so the probe is repeatable.
curl -s -X POST $B/revert_inherited_component "${H[@]}" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Influence\",\"confirm\":true}"
```

**Second case, different handler, same guarantee** — `add_component`, which the original test named:

```bash
curl -s -X POST $B/add_component "${H[@]}" \
  -d '{"blueprintId":"<child BP>","componentClass":"SphereComponent","name":"MifProbe",
       "location":{"x":"not-a-number","y":0,"z":0}}'
#   REQUIRE ok:false, and the error says "The component was NOT added"
curl -s -X POST $B/list_components "${H[@]}" -d '{"blueprintId":"<child BP>"}'
#   REQUIRE no component named MifProbe        <- this is what previously survived
```

**Third case, a level actor** — `add_foliage_instances`:

```bash
curl -s -X POST $B/add_foliage_instances "${H[@]}" \
  -d '{"mesh":"<a real static mesh>","label":"MifFoliageProbe",
       "instances":[{"x":0,"y":0,"z":0},{"x":"nope","y":0,"z":0}]}'
#   REQUIRE ok:false
curl -s -X POST $B/list_level_actors "${H[@]}" -d '{}'
#   REQUIRE no actor labelled MifFoliageProbe  <- previously a half-populated holder survived
```

### Files changed

| File | Change |
|---|---|
| `Source/MifBridge/Private/MifBridgeInherited.cpp` | `PrepareOneProperty` split out of `ApplyOneProperty`; `SerializePreflightOutcome` + `PreflightProperties` added; `H_override_inherited_component` reordered to guards → preflight → create → apply, with `RemoveOverridenComponentTemplate` cleanup scoped to `created:true`; fourth correction added to the file header |
| `Source/MifBridge/Private/MifBridgeCommon.cpp` | `RunEndpoint`'s "FAILURE ROLLS BACK" comment replaced with what `Cancel` actually does; `ReportParamTypeViolations` no longer tells callers "nothing was written" |
| `Source/MifBridge/Private/MifBridgeHandlers.h` | silent-ignore backstop note no longer claims the rollback |
| `Source/MifBridge/Private/MifBridgeComponents.cpp` | `add_component` validates the transform against the component CLASS before `SCS->CreateNode` |
| `Source/MifBridge/Private/MifBridgeAuthoring.cpp` | `add_foliage_instances` parses every instance before spawning; `create_material_instance` validates `scalars`/`vectors` before `CreatePackage` |
| `Source/MifBridge/Private/MifBridgeNodes3.cpp` | `add_timeline` validates `floatTracks[]` before the node exists; null-template branch names what it left behind |
| `Source/MifBridge/Private/MifBridgeNodes.cpp` | `add_pin` failure texts name what is left behind; default-value comment corrected |
| `Source/MifBridge/Private/MifBridgeRecipes.cpp` | `recipe_add_debug_print` failure texts name the node left in the graph |
| `Source/MifBridge/Private/MifBridgeUserTypes.cpp` | `create_struct` member-name failure names the in-memory asset left behind |
| `Source/MifBridge/Private/MifBridgeIntrospect.cpp` | `set_variable_flags` `replicationCondition` failure points at `createdRepNotifyGraph`; two `rename_variable`/`remove_variable` comments corrected |
| `tools/ue5-mcp-bridge/server.py` | `override_inherited_component` docstring: validation happens before minting; `created:false`/`nothingModified:true` on refusal; the cleanup rule |
| `docs/01_POSTMORTEMS.md` | **PM-007** added at the head |
| `docs/00_ARCHITECTURE.md` | transaction-policy section states what `Cancel` does and does not do |
| `docs/audit/06_IMPLEMENTED.md` | Batch K findings 1/14/15/16 corrected; *STILL UNPROVEN* section marked resolved; this section |

Registry parity is untouched — no endpoint was added or removed. `MIF_DECL` **191** = `MIF_BIND`
**191** (name-set diff empty both ways); `server.py` `_post` targets **203** = 191 built-ins + the 12
`kr_*` externals.

### Not done, and why

* **`RunEndpoint` was NOT changed to actually roll back.** Doing it means letting the transaction end
  normally and then calling `GEditor->UndoTransaction()`, which replays an undo *inside* a request:
  `PostUndo` triggers Blueprint reinstancing (`EditorServer.cpp:1406`) — the exact dead-CDO hazard
  `IsSelfManagedEndpoint` exists to fence off — and it does nothing at all for the objects that were
  never `RF_Transactional` in the first place, which is the case that started this. Order at the
  handler is the mechanism; a central undo would be a second, less reliable one layered on top.
* **The four "(c)" handlers were not restructured.** `add_pin` and `recipe_add_debug_print` would
  need to delete user-visible graph objects that may already be wired; `create_struct` and
  `set_variable_flags` would need their engine helpers to be reordered around state they create.
  Each now says precisely what it left and which endpoint removes it, which is the honest option and
  is what the audit asked for when neither (a) nor (b) is safe.
* **Nothing was built.** The editor is running and this batch is source-only. Every claim above about
  engine behaviour is a source citation with a file and line, not an observation of a running DLL;
  the *behaviour* claims about the bridge are what the re-run above is for.

---

## Batch N — component discovery + Details-panel parity

_Source-only. Nothing was built and the editor was not launched; every engine claim below is a
`file:line` citation against `D:/UE532`, and every bridge claim is a curl the main session runs after
it builds. Specs: `docs/audit/work/R3_REMAINING_WORK.md` §4.1 (`list_components`) and
`docs/audit/work/R1_DETAILS_PANEL_PARITY.md` (G1/G2/G3/G6/G8)._

### The three counts

| thing | before | after |
|---|---|---|
| `MIF_DECL` in `Source/MifBridge/Private/MifBridgeHandlers.h` | 191 | **195** |
| `MIF_BIND` in `Source/MifBridge/Private/MifBridgeCommon.cpp` | 191 | **195** |
| `H_*` handler definitions across `Private/*.cpp` | 191 | **195** |
| `@mcp.tool()` in `tools/ue5-mcp-bridge/server.py` | 203 | **207** |
| `_post("…")` targets in `server.py` | 203 | **207** |

`MIF_DECL` set ≡ `MIF_BIND` set ≡ `H_*` set (diff empty both ways, all three directions).
`server.py` − `MIF_DECL` = the 12 `kr_*` externals and nothing else, unchanged.

### New endpoints — registry lines, verbatim

```cpp
// MifBridgeHandlers.h, beside MIF_DECL(list_object_properties):
MIF_DECL(describe_property);
MIF_DECL(diff_properties_vs_default);
MIF_DECL(edit_container);
MIF_DECL(reset_property_to_default);

// MifBridgeCommon.cpp, in Handlers(), beside MIF_BIND(list_object_properties):
// Details-panel parity (Batch N) - MifBridgeDetails.cpp
MIF_BIND(describe_property);
MIF_BIND(diff_properties_vs_default);
MIF_BIND(edit_container);
MIF_BIND(reset_property_to_default);

// MifBridgeCommon.cpp, IsReadOnlyEndpoint()'s TSet, beside get_property / list_object_properties:
TEXT("describe_property"), TEXT("diff_properties_vs_default"),

// IsSelfManagedEndpoint() — NO entries. See the bucket table below.
```

| endpoint | bucket | why that bucket |
|---|---|---|
| `describe_property` | **read-only** | FField metadata + `CPF_*` flags + `UObject::CanEditChange`. No `Modify()`, no object creation, and `GetInheritableComponentHandler` is never reached. Outside `IsReadOnlyEndpoint` every call pushes an empty entry onto the stack `list_transactions` exists to report. |
| `diff_properties_vs_default` | **read-only** | `UObject::GetArchetype` + `FProperty::Identical`. Same reasoning. |
| `edit_container` | **default (transacted)** | Runs no `FKismetEditorUtilities::CompileBlueprint`, and that is the only thing `IsSelfManagedEndpoint` is for (`00_ARCHITECTURE.md` §Transaction policy). Every mutation is `Modify()`-able, so `RunEndpoint`'s blanket transaction gives correct Ctrl-Z for free — which self-managed would throw away. |
| `reset_property_to_default` | **default (transacted)** | Same. |

**The widget-template form (`blueprintId` + `widgetName`) is REFUSED on both mutators**, naming
`set_property` as the route. R1 §2.5 field 5 offered exactly two options — refuse the widget form, or
promote the endpoint to self-managed and mirror `set_property`'s tight inner transaction — and asked
for one to be picked and stated. Refusing is the one that keeps both verbs batchable and undoable for
the cases that have containers and archetypes at all; a widget slot has neither. Both also refuse a
**cooked / container-only** package outright, on the shared
`MifBridge::IsCookedOrContainerPackage` test.

---

### PART 1 — `list_components` now enumerates INHERITED and NATIVE components

**The gap, restated in one sentence.** Batch J shipped the WRITE path for inherited components
(`get_/override_/revert_inherited_component`) and shipped no way to discover what they are called:
`get_inherited_component` resolves ONE component BY NAME, and `list_components` walked the child
Blueprint's own SCS and nothing else. An agent editing a child saw a near-empty list and had no name
to pass to the three endpoints the session had just built.

**What it reports now.** Every component from all three origins, each row tagged:

| `origin` | source | `templatePath` on that row |
|---|---|---|
| `ownSCS` | this Blueprint's `SimpleConstructionScript` | the SCS `ComponentTemplate` (`<Class>:<Name>_GEN_VARIABLE`) |
| `parentBlueprintSCS` | a parent BLUEPRINT's SCS, anywhere up the `UBlueprintGeneratedClass` chain | the child's OVERRIDE template when one exists, and **deliberately absent** when it does not |
| `native` | a C++ component on the parent class chain, read off the CDO | the CHILD CDO's own subobject — under its REAL subobject name |

Plus, per row: `owningClass`, `classPath`, `inherited`, `overrideExists`, `canOverride` +
`canOverrideReason`, `editableWhenInherited`, `subobjectName` (native), `parentTemplatePath`
(inherited), `creationMethod` (native), and `route` / `endpoint` / `hint` — the exact next call.
Top level: `blueprint`, `parentClass`, `ownSCSCount`, `parentBlueprintSCSCount`, `nativeCount`,
`matched`, `truncated`, `inheritableComponentHandlerPath`, `existingOverrideCount`.

**ADDITIVE, and the new origins are ON BY DEFAULT.** Every field the old response carried (`name`,
`class`, `isRoot`, `templatePath`, `parent`, `attachSocket`, `count`, `components`) is emitted
unchanged and with unchanged meaning, so every existing caller keeps working; the new origins arrive
as EXTRA rows and the new facts as EXTRA fields. `includeInherited` / `includeNative` both default
**true** and exist so a caller can ask for exactly the old own-SCS-only shape back — not as an opt-in
for the new one. Discoverability is the entire point of the change, and a default-off discovery
feature is the same gap wearing a parameter.

**`templatePath` means ONE thing on every row**: the `objectPath` to pass to `set_property` to change
that component's defaults FOR THIS BLUEPRINT. That is why an inherited row with no override has no
`templatePath`: the only template that exists is the PARENT asset's, and writing there would change
every other child of that parent. `parentTemplatePath` shows it read-only and `route` says
`override_inherited_component`.

**Native rows carry the REAL subobject path, resolved from the object.** The property name and the
subobject name differ — `Mesh` → `CharacterMesh0`, `CharacterMovement` → `CharMoveComp`,
`CapsuleComponent` → `CollisionCylinder` — and nobody guesses those. `MifBridgeInherited.cpp` already
resolved this correctly, so that logic was **PROMOTED, not copied**: `FindNativeComponentOnCDO` now
lives once in `MifBridgeCommon.cpp` (declared in `MifBridgeHandlers.h`) and
`MifBridgeInherited.cpp`'s copy is gone.

**Engine API, cited.**

| API | Verbatim | Export | Access |
|---|---|---|---|
| `USimpleConstructionScript::GetAllNodes` | `ENGINE_API const TArray<USCS_Node*>& GetAllNodes() const;` — `SimpleConstructionScript.h:77` (inside `#if WITH_EDITOR`) | method-level `ENGINE_API`; class is `UCLASS(MinimalAPI)` at `:16` | **public** — implicit after `GENERATED_UCLASS_BODY()` at `:19`. Already called at `MifBridgeComponents.cpp` before this batch |
| `USimpleConstructionScript::GetRootNodes` / `FindParentNode` | same header, same section | `ENGINE_API` | public |
| `UBlueprintGeneratedClass::SimpleConstructionScript` | `TObjectPtr<class USimpleConstructionScript> SimpleConstructionScript;` — `BlueprintGeneratedClass.h:685` | data member, no symbol to export | **public @660** |
| `UObject::GetDefaultSubobjects` / `GetDefaultSubobjectByName` | `COREUOBJECT_API void GetDefaultSubobjects(TArray<UObject*>& OutDefaultSubobjects);` — `Object.h` | **COREUOBJECT_API** | public |
| `UBlueprint::GetInheritableComponentHandler` | `UInheritableComponentHandler* GetInheritableComponentHandler(bool bCreateIfNecessary);` — `Blueprint.h`; returns null when `[Kismet] bEnableInheritableComponents=false` (`Blueprint.cpp:2062-2068`) | `ENGINE_API` | public — called here with **`false`**, which is what keeps `list_components` read-only |
| `UInheritableComponentHandler::GetOverridenComponentTemplate` / `GetAllTemplates` | `InheritableComponentHandler.h` | `ENGINE_API` | public |
| `FComponentKey(const USCS_Node*)` | `InheritableComponentHandler.h:23-30` — the ONLY constructor that can mint a new key; there is no `FComponentKey(FName)` | struct | public |
| `UActorComponent::IsEditableWhenInherited` | `ENGINE_API bool IsEditableWhenInherited() const;` — `ActorComponent.h:356` | **ENGINE_API** | public @`:336` |
| `UActorComponent::CreationMethod` | `EComponentCreationMethod CreationMethod;` — `ActorComponent.h:315` | `UPROPERTY()`, no export needed | public @`:311` |
| `AActor::GetRootComponent` | `Actor.h` | `ENGINE_API`/inline | public — used to mark the native row that IS the root |

No new module: `Engine` + `UnrealEd` were already `Build.cs` dependencies. **`Build.cs` was not
touched by this batch at all.**

**Spec correction applied.** R3 §3/S4 required `origin`, not `source`, with the four values Batch J
already shipped. The four words now have exactly ONE definition — `MifBridge::kComponentOrigin*` in
`MifBridgeCommon.cpp` — and `MifBridgeInherited.cpp`'s four local literals were deleted rather than
aliased (a namespace-scope alias initialised from another translation unit's constant is a
static-initialisation-order bet for no benefit).

---

### PART 2 — Details-panel parity

#### G2 — EditCondition / companion override flags (a DEFECT, not a gap)

Writing `UStaticMeshComponent::MinLOD` without `bOverrideMinLOD` is **silently ignored by the
engine**: the renderer reads the flag, not the value —
`int32 EffectiveMinLOD = InComponent->bOverrideMinLOD ? InComponent->MinLOD : SMCurrentMinLOD;`
(`StaticMeshRender.cpp:248`, again at `:2661`, and `NaniteResources.cpp:838`).
`FPostProcessSettings` is the same shape 423 times over (`SceneView.cpp:1440-1442`'s `LERP_PP` /
`SET_PP` / `IF_PP` macros all test `Src.bOverride_##NAME` first). `set_property` returned
`applied:true, verified:true, changed:true` for such a write and the value was never read. The
existing verification bracket cannot catch it by construction — the value genuinely changed.

**The mechanism is NOT the `bOverride_` naming convention.** That is only `FPostProcessSettings`'
house style. It is `UPROPERTY meta`, read at `PropertyNode.cpp:230`
(`MyProperty->GetMetaData(TEXT("EditCondition"))`), with the companion flag found as a sibling
`FBoolProperty` on the gated property's own owner struct —
`BoolProperty = FindFProperty<FBoolProperty>(Property->GetOwnerStruct(), *PropertyToken->PropertyName);`
(`EditConditionContext.cpp:55`). `UStaticMeshComponent` proves the point without the prefix:
`meta=(editcondition = "bOverrideMinLOD")` on `MinLOD` (`StaticMeshComponent.h:115-116`) against
`uint8 bOverrideMinLOD:1;` (`:226`). Case does not matter: metadata is keyed by `FName`
(`Field.cpp:749-757`), so one `static const FName` lookup finds `editcondition` and `EditCondition`
alike.

`FEditConditionParser` **cannot be linked** — `EditConditionParser.h:100` has no export macro, lives
in `Editor/PropertyEditor/**Private**/`, and `PropertyEditor` is not a dependency of this module at
all (`UnrealEd.Build.cs` names it only under `DynamicallyLoadedModuleNames` /
`PrivateIncludePathModuleNames` / `PublicIncludePathModuleNames` at `:125, 270, 291`). So
`MifBridge::InspectEditCondition` (`MifBridgeCommon.cpp`, declared in `MifBridgeHandlers.h`)
implements a **restricted** evaluator: a single identifier or its negation, which the spec measured
at **713 / 837 = 85.2 %** of gated properties in `Runtime/**.h`. The other 122 come back
`editConditionKind:"unevaluated"` with the raw meta string and are **never guessed**.

`set_property` behaviour, per the new `overrideFlag` parameter (aliases `editCondition`, `override`;
an unrecognised value is a hard error naming the accepted set, PM-002):

| `overrideFlag` | behaviour when the gate is unmet |
|---|---|
| `"set"` (**default**) | sets the companion bool to the satisfying value **inside the same `Modify`/`PreEditChange`…`PostEditChange` bracket and the same transaction**, writes the value, and reports `overrideFlagWritten:{name, valueBefore, valueAfter}` — `valueAfter` is a MEASURED readback, not an echo — plus a `warnings[]` line |
| `"refuse"` | writes nothing. Fails naming the property, the meta string, the flag and its current value, and both fixes |
| `"ignore"` | writes anyway, sets `overrideFlagUnmet:true` and warns that the engine will ignore the value |

Default `"set"` because it is what the panel does when a human types into the field: the value row is
edit-const until the inline toggle is ticked, so a human physically cannot produce the "value written,
flag off" state the bridge produced. The flag write deliberately fires **no separate notification** —
on a placed actor's component that would rerun the construction scripts mid-write and leave the leaf
address dangling before the value is even published.

**Whatever the mode, the response states the gate.** `editCondition` (raw meta string or `null`),
`editConditionKind` ∈ `none|bool|negatedBool|unevaluated`, `editConditionMet` (bool or `null`),
`editConditionHides`. Silently writing a value the engine ignores is the banned bug class; silently
*fixing* it without saying so is the same failure wearing a hat.

#### G1 — element-level addressing, in the SHARED walker

`MifBridge::ResolvePropertyPathEx` (`MifBridgeCommon.cpp`, declared in `MifBridgeHandlers.h`) is now
the ONE walker; `ResolvePropertyPath` and `ResolvePropertyPathChain` forward to it. Grammar:

```
segment  := name accessor*
accessor := '[' index ']'            TArray | TSet | ArrayDim>1   (integer)
          | '[' member '=' text ']'  TArray of struct  -> linear find, first match
          | '{' keytext '}'          TMap -> the KEY
          | '[' keytext ']'          TMap -> alias for {keytext}
```

Disambiguation is by CONTAINER TYPE, never by the text. Path splitting is bracket-depth aware, so
`ScalarParameterValues[ParameterInfo.Name=Roughness].ParameterValue` is not cut at the wrong dot.

* **Out of range names the index AND the actual length**, always:
  `'OverrideMaterials[7]': index 7 is out of range - the array has 2 elements (valid 0..1). Use edit_container {operation:"add"} to grow it.`
* **`ArrayDim > 1` C-arrays are handled**, and were the real reason `UCurveVector::FloatCurves`
  (`FRichCurve FloatCurves[3]`, `Curves/CurveVector.h:36`) and `UBlendSpace::BlendParameters`
  (`Animation/BlendSpace.h:862`) were unreachable — neither walker handled them, and they are not
  `TArray`s. The scratch buffer now spans the whole `ArrayDim` and the import targets element N
  inside it, so addressing `FloatCurves[2]` neither reads nor writes past the allocation;
  `PropertyValueToTypedJsonElement` was added for the same reason (the whole-property emitter loops
  `ArrayDim` from whatever address it is given).
* **Set indexing is sparse-aware and by ITERATION POSITION** — `FScriptSetHelper::FindNthElementPtr`
  (`UnrealType.h:5344`) skips the holes — and the response says `elementOrdering:"iteration"`,
  because that order is not stable across a rehash.
* **Map lookup is a linear compare of exported key text**, not a hash probe, so a key type with no
  `GetTypeHash` is still READABLE and only the mutating path has to refuse it. A miss lists the
  existing keys.
* **Editing a set element through `set_property` checks for a duplicate first** (the panel refuses it
  outright: `"Duplicate elements are not allowed in Set properties"`, `PropertyHandleImpl.cpp:389`)
  and **rehashes** afterwards (`PropertyHandleImpl.cpp:522-534`), reporting `rehashed:true`.

**Container mutation** is the new `edit_container` endpoint —
`operation` ∈ `add|insert|remove|clear|swap|resize|setKey`. The verb is `operation` (alias `action`)
and **not `op`**, because `op` is `batch`'s routing key and is tolerated centrally by
`RejectUnknownParams`; an endpoint using it would be un-diagnosable inside `batch`. `op` is refused
by name with that explanation. Engine API: `FScriptArrayHelper::AddValue/AddValues/InsertValues/
RemoveValues/SwapValues/EmptyValues/Resize` (`UnrealType.h:4162-4277`, all inline public),
`FScriptMapHelper::AddPair/RemovePair/RemoveAt/EmptyValues/Rehash` (`:4680-5005`; `Rehash` at `:4764`
is the only `COREUOBJECT_API` one), `FScriptSetHelper::AddElement/RemoveElement/RemoveAt/
EmptyElements/FindElementIndex/FindInternalIndex/Rehash` (`:5344-5608`; `Rehash` at `:5454`).
Module: `CoreUObject`, already a public dependency. **No new module.**

The five hazards R1 §2.4 said must be *encoded, not discovered*, and where each is:

1. **Rehash after any key/element mutation** — after every map/set add/remove/setKey, reported as
   `rehashed`.
2. **Pointer invalidation** — the helper is re-resolved after every structural op before the element
   is written (`AddValues`/`InsertValues` reallocate, `UnrealType.h:4099-4110`).
3. **PM-003 at element granularity** — no `ImportText_Direct` ever sees a live address.
   `MifBridge::ImportPropertyTextSafely` (defined in `MifBridgeNodes5.cpp` beside `FScratchValue`) is
   the one implementation.
4. **Duplicate refusal** — `AddPair` **overwrites silently**, so without the check `add` becomes
   `replace` with no notice; refused by name, quoting the panel's own `"Duplicate keys are not
   allowed in Map properties"` (`PropertyHandleImpl.cpp:446`). Same for set elements.
5. **`CPF_EditFixedSize`** (`ObjectMacros.h:403`) refuses every size-changing op and names the flag.
   Keyed off the **flag**, never the metadata string: the flag survives a cook, the meta does not.

Plus one the spec did not ask for: **an operation-specific parameter that this operation cannot act
on is REFUSED, not dropped** (`count` on a swap, `newSize` on an add, `key` on an array). An ignored
parameter is worse than a rejected one.

`PerformOperationWithSetter` (`UPROPERTY(Setter=...)` containers, 288 of them in `Runtime/**.h`) is
**not** used — see *Not done, and why*.

#### G6 — `reset_property_to_default` + `diff_properties_vs_default`

Both compute "default" the way `FPropertyNode` does: the archetype, with a `UClass` → CDO hop first
(`PropertyNode.cpp:1651-1654` then `:1669`), compared with `FProperty::Identical` plus
`PPF_DeepComparison` when `ContainsInstancedObjectProperty()` and an `ArrayDim` loop
(`PropertyNode.cpp:2275-2308`).

`reset_property_to_default` applies the two refusals the panel applies and a naive reset does not —
`CPF_Config` has **no** reset arrow and `CPF_EditFixedSize` has none either
(`FPropertyHandleBase::CanResetToDefault`, `PropertyHandleImpl.cpp:3421-3433`) — imports the default
text with `PPF_InstanceSubobjects` (`PropertyHandleImpl.cpp:490-492, 992-1008`), and **asserts the
invariant**: after a successful reset `valueAfter` must equal `defaultValue` byte-for-byte under the
same exporter, or the call fails. A property that already equals its default is *reported*
(`changed:false`), not failed. When the archetype does not carry the property at all — a variable a
child Blueprint added — it falls back to a freshly constructed value and says
`defaultSource:"constructed"` (`PropertyNode.cpp:2432-2443` is the engine's own version of that fork).
The default text is parsed into a staging buffer **before** `Modify`/`PreEditChange` is called, so a
failed reset neither touches the live value (PM-003) nor leaves an unconsumed
`FComponentReregisterContext` behind (`ActorComponent.cpp:806-822` is matched only by
`ConsolidatedPostEditChange` at `:927-941`).

`diff_properties_vs_default` emits the checkable invariant
`inspected == differing + matching + skippedTransient` as `countsConsistent`. Transients are skipped
by default (they always differ and drown the signal). An object whose archetype is itself is a stated
RESULT with `differing:0`, not an error.

#### G3 — `describe_property`, the discovery layer

Reports, for one property or a filtered survey (or for a bare class with no instance): the AUTHORED
specifier recovered from the flags (`UhtPropertyMemberSpecifiers.cs:21-88` run backwards, so
`VisibleAnywhere` shows up as exactly `CPF_Edit | CPF_EditConst` — a property a human **cannot** edit
and this bridge will happily write), the raw `CPF_*` names, every metadata key, `Category` /
`DisplayName` / `ToolTip`, the EditCondition block above, `ClampMin`/`ClampMax`/`UIMin`/`UIMax`/
`Multiple`/`ArrayClamp`, `EditFixedSize`, `Instanced` + `AllowedClasses`/`DisallowedClasses`,
`GetOptions`, `Units`/`ForceUnits`, `BitmaskEnum`, `ArrayDim`, the container shape (kind, inner/key/
value type, element count, key hashability), `persistence` ∈ `saved|transient|duplicateTransient|
notSerialized` — three DIFFERENT lies, from `FProperty::ShouldSerializeValue`'s own rules
(`Property.cpp:1167-1225`): gone on reload, gone on copy/paste, and **not undoable** — plus
`editableByHuman` (the panel's own predicate recomputed, including `UObject::CanEditChange`,
`Obj.cpp:507-511`) with a `notEditableReason`, and `differsFromDefault` / `defaultValue` /
`defaultSource`.

**Cooked behaviour is stated, not faked.** `GetMetaDataMap()` is null on a cooked package, so
`metadataAvailable:false` and every meta field is **absent** — never emitted as an empty string,
which would read as "no clamp, no gate" when the truth is "unknown". `CPF_*` flags are cooked and
stay accurate.

#### G8 — clamps: reported, never silently exceeded, coerced only on request

`ClampMin`/`ClampMax` are applied **only** by the panel's typed numeric setters
(`ClampValueFromMetaData` / `ClampIntegerValueFromMetaData`, `PropertyHandleImpl.cpp:870-931`, reached
from `FPropertyHandleInt/Float/Double::SetValue`). The **text** path does not clamp:
`FPropertyValueImpl::SetValueAsString` goes straight to `ImportText` (`:818-853`), and a grep of
`Runtime/CoreUObject` for `ClampMin` returns only `UPROPERTY` declarations in
`NoExportTypes.h:1352-1361` — no consuming code. So `set_property` mirrors the panel's *copy/paste*
path, which is genuinely unclamped, and both of these are "the Details panel".

Policy, as the spec asked: **do not silently clamp** (that would be a silent value change, the same
bug class in the other direction) and do not silently exceed. A write outside `ClampMin..ClampMax`
now carries `clampViolation:{meta, limit, written}` and a `warnings[]` line naming the metadata and
the fix; `enforceClamps:true` (aliases `clamp`, `respectClamps`) switches to the panel's typed-setter
behaviour and reports the coercion as `clampApplied:{meta, requested, written}` **and** sets the
existing `coerced:true`, so a caller has ONE field to check regardless of who did the clamping.
`UIMin`/`UIMax` are slider bounds, are enforced by nothing including the panel, and are reported in
`uiRange` and never acted on.

---

### PM-005 — what was PROMOTED rather than copied

Batch N needed seven things that already existed once. Every one of them was moved to
`MifBridgeCommon.cpp` (or `MifBridgeNodes5.cpp` where it belongs beside `FScratchValue`) and declared
in `MifBridgeHandlers.h`, and the original copy was **deleted**. No file gained a second
implementation of anything.

| helper | was | now |
|---|---|---|
| `FindNativeComponentOnCDO` | file-local in `MifBridgeInherited.cpp` | `MifBridgeCommon.cpp`, used by `ResolveComponentOrigin` and the enumerator |
| `CreationMethodToString` | file-local in `MifBridgeInherited.cpp` | `MifBridge::ComponentCreationMethodString(const UActorComponent*)`, `MifBridgeCommon.cpp` |
| the four `kOrigin*` literals | file-local in `MifBridgeInherited.cpp` | `MifBridge::kComponentOrigin*`, `MifBridgeCommon.cpp` — literals, but two files spelling the same state is the same failure |
| `GatherAvailableComponents`' three-origin walk | a second enumerator in `MifBridgeInherited.cpp` | calls `MifBridge::EnumerateBlueprintComponents` |
| `ResolveGenericTarget` | file-local in `MifBridgeNodes6.cpp`, plus an inline copy in `set_property` | `MifBridge::ResolvePropertyTarget`, `MifBridgeCommon.cpp` |
| `IsCookedOrContainerPackage` | file-local in `MifBridgeMaterials.cpp` | `MifBridgeCommon.cpp` |
| the `value` → import-text dispatch | inline in `set_property` | `MifBridge::PropertyImportTextFromJson`, `MifBridgeNodes5.cpp` |
| the PM-003 scratch import | inline in `set_property` | `MifBridge::ImportPropertyTextSafely`, `MifBridgeNodes5.cpp` (still using the same `FScratchValue`) |

`MifBridgeNodes6.cpp`'s deleted copy carried a comment saying the duplication was deliberate —
*"duplicated here rather than shared so this read-only file can't perturb the existing write path"*.
That reasoning does not survive contact with PM-005: the two copies were already free to drift about
what `objectPath` accepts, and a read that resolves its target differently from the write that
follows it is a worse failure than the one the fence was guarding against.

---

### Live proof — run this after the build

Real assets. The child `/Game/MODS/BotanistExpansion_p/Blueprints/NPCs/NPC_MifAmbient` inherits the
SCS component **`Influence`** from `/Game/Blueprints/Pawns/NPC/BP_BaseNPC.BP_BaseNPC_C` and native
components from `ACharacter`.

```bash
B=http://127.0.0.1:8791/api
H=(-H "X-Mif-Token: ${MIF_BRIDGE_TOKEN:-dev}" -H "Content-Type: application/json")
BP=/Game/MODS/BotanistExpansion_p/Blueprints/NPCs/NPC_MifAmbient
CDO=/Game/MODS/BotanistExpansion_p/Blueprints/NPCs/NPC_MifAmbient.Default__NPC_MifAmbient_C
SMC=/Script/Engine.Default__StaticMeshComponent

# ---------------------------------------------------------------- PART 1: discovery
# 0. THE REGRESSION THIS BATCH EXISTS FOR. Before: this listed the child's own SCS only.
curl -s -X POST $B/list_components "${H[@]}" -d "{\"blueprintId\":\"$BP\"}"
#   REQUIRE parentBlueprintSCSCount >= 1
#   REQUIRE nativeCount            >= 3
#   REQUIRE a row {name:"Influence", origin:"parentBlueprintSCS"} with
#           owningClass:"/Game/Blueprints/Pawns/NPC/BP_BaseNPC.BP_BaseNPC_C",
#           canOverride:true, overrideExists:false,
#           NO templatePath, parentTemplatePath present,
#           route:"override_inherited_component"
#   REQUIRE a row {name:"Mesh", origin:"native", subobjectName:"CharacterMesh0"} whose
#           templatePath ENDS IN ":CharacterMesh0"   <- the property name is NOT the subobject name
#   REQUIRE a row {name:"CharacterMovement", origin:"native", subobjectName:"CharMoveComp"}
#   REQUIRE a row {name:"CapsuleComponent",  origin:"native", subobjectName:"CollisionCylinder"}
#   REQUIRE every native row's owningClass names a C++ class (ACharacter / APawn / AActor),
#           NOT the child blueprint
#   REQUIRE existingOverrideCount == 0 and inheritableComponentHandlerPath == ""
#           (listing must NOT mint an ICH: bCreateIfNecessary=false)

# 0b. THE ROUND TRIP THAT WAS IMPOSSIBLE BEFORE — take a name straight out of the list.
curl -s -X POST $B/get_inherited_component "${H[@]}" \
  -d "{\"blueprint\":\"$BP\",\"component\":\"Influence\"}"
#   REQUIRE origin:"parentBlueprintSCS", canOverride:true, editableWhenInherited:true

# 0c. THE OLD SHAPE IS STILL REACHABLE, and the additive fields are still there.
curl -s -X POST $B/list_components "${H[@]}" \
  -d "{\"blueprintId\":\"$BP\",\"includeInherited\":false,\"includeNative\":false}"
#   REQUIRE parentBlueprintSCSCount == 0 and nativeCount == 0
#   REQUIRE every remaining row has origin:"ownSCS" and a templatePath

# 0d. A NATIVE templatePath must actually WORK as set_property's objectPath.
#     (read-only proof: get_property against the path list_components handed back)
curl -s -X POST $B/get_property "${H[@]}" \
  -d "{\"objectPath\":\"$CDO:CharacterMesh0\",\"propertyPath\":\"CastShadow\"}"
#   REQUIRE ok:true    <- if the subobject name had been guessed from "Mesh", this 404s

# ---------------------------------------------------------------- PART 2: EditCondition (G2)
# 1. THE SILENT-IGNORE, PROVED READ-ONLY FIRST.
curl -s -X POST $B/describe_property "${H[@]}" \
  -d "{\"objectPath\":\"$SMC\",\"propertyPath\":\"MinLOD\"}"
#   REQUIRE property.editCondition:"bOverrideMinLOD"
#   REQUIRE property.editConditionKind:"bool"
#   REQUIRE property.editConditionMet:false
#   REQUIRE property.editConditionFlag:"bOverrideMinLOD"
#   REQUIRE property.editableByHuman:false with notEditableReason naming the EditCondition
#   REQUIRE property.specifier:"EditAnywhere", property.persistence:"saved"

# 2. REFUSE MODE — nothing is written, and the response names the flag.
curl -s -X POST $B/set_property "${H[@]}" \
  -d "{\"objectPath\":\"$SMC\",\"propertyPath\":\"MinLOD\",\"value\":\"2\",\"overrideFlag\":\"refuse\"}"
#   REQUIRE ok:false
#   REQUIRE the error names bOverrideMinLOD AND its current value AND StaticMeshRender's behaviour
curl -s -X POST $B/get_property "${H[@]}" -d "{\"objectPath\":\"$SMC\",\"propertyPath\":\"MinLOD\"}"
#   REQUIRE value:"0"        <- unchanged

# 3. DEFAULT MODE — the flag is written WITH the value, and REPORTED.
curl -s -X POST $B/set_property "${H[@]}" \
  -d "{\"objectPath\":\"$SMC\",\"propertyPath\":\"MinLOD\",\"value\":\"2\"}"
#   REQUIRE ok:true, applied:true, verified:true, changed:true
#   REQUIRE overrideFlagWritten:{name:"bOverrideMinLOD",valueBefore:false,valueAfter:true}
#   REQUIRE warnings[] contains a line naming bOverrideMinLOD
curl -s -X POST $B/get_property "${H[@]}" -d "{\"objectPath\":\"$SMC\",\"propertyPath\":\"bOverrideMinLOD\"}"
#   REQUIRE typed:true       <- valueAfter above was a MEASURED readback, and this confirms it

# 4. IGNORE MODE — written, and honestly labelled as dead data.
curl -s -X POST $B/set_property "${H[@]}" \
  -d "{\"objectPath\":\"$SMC\",\"propertyPath\":\"bOverrideMinLOD\",\"value\":\"False\"}"
curl -s -X POST $B/set_property "${H[@]}" \
  -d "{\"objectPath\":\"$SMC\",\"propertyPath\":\"MinLOD\",\"value\":\"3\",\"overrideFlag\":\"ignore\"}"
#   REQUIRE ok:true, overrideFlagUnmet:true, warnings[] says WRITTEN BUT IGNORED BY THE ENGINE

# 5. A BAD MODE IS A NAMED ERROR, NOT A SILENT DEFAULT (PM-002).
curl -s -X POST $B/set_property "${H[@]}" \
  -d "{\"objectPath\":\"$SMC\",\"propertyPath\":\"MinLOD\",\"value\":\"2\",\"overrideFlag\":\"yes-please\"}"
#   REQUIRE ok:false and the error lists set | refuse | ignore

# 6. CLEAN UP the CDO so the probe is repeatable.
curl -s -X POST $B/reset_property_to_default "${H[@]}" \
  -d "{\"objectPath\":\"$SMC\",\"propertyPath\":\"MinLOD\"}"
curl -s -X POST $B/reset_property_to_default "${H[@]}" \
  -d "{\"objectPath\":\"$SMC\",\"propertyPath\":\"bOverrideMinLOD\"}"

# ---------------------------------------------------------------- PART 2: elements (G1)
# 7. THE ERROR THAT USED TO BE A LIE. "OverrideMaterials[0] not found" for a property that exists.
curl -s -X POST $B/get_property "${H[@]}" \
  -d "{\"objectPath\":\"$SMC\",\"propertyPath\":\"OverrideMaterials[0]\"}"
#   REQUIRE ok:false, and the error says "index 0 is out of range - the array has 0 elements"
#           (NOT "property 'OverrideMaterials[0]' not found")

# 8. GROW IT, ADDRESS IT, READ IT BACK.
curl -s -X POST $B/edit_container "${H[@]}" \
  -d "{\"objectPath\":\"$SMC\",\"propertyPath\":\"OverrideMaterials\",\"operation\":\"add\"}"
#   REQUIRE ok:true, elementsBefore:0, elementsAfter:1, index:0, changed:true
curl -s -X POST $B/get_property "${H[@]}" \
  -d "{\"objectPath\":\"$SMC\",\"propertyPath\":\"OverrideMaterials[0]\"}"
#   REQUIRE ok:true, isElement:true, elementIndex:0
curl -s -X POST $B/set_property "${H[@]}" \
  -d "{\"objectPath\":\"$SMC\",\"propertyPath\":\"OverrideMaterials[0]\",\"value\":\"/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial\"}"
#   REQUIRE ok:true, isElement:true, elementPath:"OverrideMaterials[0]", changed:true
curl -s -X POST $B/edit_container "${H[@]}" \
  -d "{\"objectPath\":\"$SMC\",\"propertyPath\":\"OverrideMaterials\",\"operation\":\"remove\",\"index\":0}"
#   REQUIRE ok:true, elementsBefore:1, elementsAfter:0

# 9. OUT OF RANGE NAMES THE INDEX AND THE LENGTH.
curl -s -X POST $B/edit_container "${H[@]}" \
  -d "{\"objectPath\":\"$SMC\",\"propertyPath\":\"OverrideMaterials\",\"operation\":\"remove\",\"index\":7}"
#   REQUIRE ok:false and the error contains BOTH "7" and "0 elements"

# 10. A PARAMETER THIS OPERATION CANNOT USE IS REFUSED, NOT DROPPED.
curl -s -X POST $B/edit_container "${H[@]}" \
  -d "{\"objectPath\":\"$SMC\",\"propertyPath\":\"OverrideMaterials\",\"operation\":\"clear\",\"newSize\":4}"
#   REQUIRE ok:false naming newSize and saying it is for resize

# 11. A C-ARRAY UPROPERTY (FRichCurve FloatCurves[3]) — NOT a TArray.
#     Pick any UCurveVector in the project, or /Engine content, as $CV.
curl -s -X POST $B/get_property "${H[@]}" -d "{\"objectPath\":\"$CV\",\"propertyPath\":\"FloatCurves[1]\"}"
#   REQUIRE ok:true, isElement:true
curl -s -X POST $B/get_property "${H[@]}" -d "{\"objectPath\":\"$CV\",\"propertyPath\":\"FloatCurves[3]\"}"
#   REQUIRE ok:false and the error says "fixed-size C-array of 3 elements (valid 0..2)"
curl -s -X POST $B/edit_container "${H[@]}" \
  -d "{\"objectPath\":\"$CV\",\"propertyPath\":\"FloatCurves\",\"operation\":\"add\"}"
#   REQUIRE ok:false and the error says its size is part of the C++ declaration

# ---------------------------------------------------------------- PART 2: default diff (G6)
# 12. WHAT DOES THE CHILD ACTUALLY OVERRIDE?
curl -s -X POST $B/diff_properties_vs_default "${H[@]}" -d "{\"objectPath\":\"$CDO\"}"
#   REQUIRE countsConsistent:true
#   REQUIRE inspected == differing + matching + skippedTransient

# 13. RESET IS IDEMPOTENT AND SAYS SO.
curl -s -X POST $B/reset_property_to_default "${H[@]}" \
  -d "{\"objectPath\":\"$SMC\",\"propertyPath\":\"MinLOD\"}"
#   REQUIRE ok:true, changed:false, note says it already equals its default

# 14. THE WIDGET FORM IS REFUSED BY NAME, not silently mishandled.
curl -s -X POST $B/edit_container "${H[@]}" \
  -d "{\"blueprintId\":\"/Game/UI/WBP_Anything\",\"widgetName\":\"Anything\",\"propertyPath\":\"X\",\"operation\":\"clear\"}"
#   REQUIRE ok:false naming set_property as the route

# 15. REGISTRY PARITY, from the running DLL.
curl -s -X POST $B/self_audit "${H[@]}" -d '{}'
#   REQUIRE endpointCount 207 (195 built-in + 12 external)
#   REQUIRE transactionBuckets.readOnly grew by exactly 2
#   REQUIRE policyContradictions == []
```

### Files changed

| File | Change |
|---|---|
| `Source/MifBridge/Private/MifBridgeDetails.cpp` | **NEW.** `describe_property`, `diff_properties_vs_default`, `edit_container`, `reset_property_to_default` |
| `Source/MifBridge/Private/MifBridgeHandlers.h` | 4 `MIF_DECL`s; `FComponentOriginRow` + `EnumerateBlueprintComponents` + `FindNativeComponentOnCDO` + `ComponentCreationMethodString` + `kComponentOrigin*`; `FPropertyPathResolution` + `ResolvePropertyPathEx`; `ResolvePropertyTarget`; `FEditConditionInfo` + `InspectEditCondition`; `FPropertyClampInfo` + `InspectClamps` + `DescribeClampViolation`; `AddWarning`; `ExportPropertyTextForMatch` / `FindMapEntryByKeyText` / `SampleMapKeyText`; `IsCookedOrContainerPackage`; `PropertyImportTextFromJson`; `ImportPropertyTextSafely`; `PropertyValueToTypedJsonElement`; five forward declarations |
| `Source/MifBridge/Private/MifBridgeCommon.cpp` | 4 `MIF_BIND`s + 2 read-only bucket entries; the walker rewritten as `ResolvePropertyPathEx` with the accessor grammar (the two old entry points now forward to it); the component enumerator; the promoted target resolver, cooked test, map-key matcher, EditCondition inspector and clamp reader; 10 includes |
| `Source/MifBridge/Private/MifBridgeComponents.cpp` | `list_components` rewritten over the shared enumerator: three origins, per-row route/hint, `RejectUnknownParams`, origin counts |
| `Source/MifBridge/Private/MifBridgeInherited.cpp` | local `FindNativeComponentOnCDO`, `CreationMethodToString` and the four origin literals deleted; `GatherAvailableComponents` now calls the shared enumerator; `get_inherited_component` reports `editableWhenInherited` |
| `Source/MifBridge/Private/MifBridgeNodes5.cpp` | `set_property`: shared target resolver + accessor-aware walk + element-aware scratch/publish, EditCondition handling (`overrideFlag`), clamp reporting and `enforceClamps`, set-element duplicate check + rehash, new response fields; `PropertyImportTextFromJson` / `ImportPropertyTextSafely` / `PropertyValueToTypedJsonElement` defined |
| `Source/MifBridge/Private/MifBridgeNodes6.cpp` | local `ResolveGenericTarget` deleted; `get_property` / `list_object_properties` use the shared resolver, gained `RejectUnknownParams`, and `get_property` reports element addressing |
| `Source/MifBridge/Private/MifBridgeMaterials.cpp` | local `IsCookedOrContainerPackage` deleted; the three call sites now use the shared one |
| `tools/ue5-mcp-bridge/server.py` | 4 new `@mcp.tool()`s; `list_components` and `set_property` signatures + docstrings updated |
| `docs/audit/06_IMPLEMENTED.md` | this section |

`Build.cs` was **not** touched: every API above is `Core` / `CoreUObject` / `Engine` / `UnrealEd`,
all already dependencies. R1 §3.3's warning is honoured — `PropertyEditor` is **not** added.

### Not done, and why

* **G4 (the notification bracket) was already shipped in Batch L** and was not re-done.
  `set_property` already builds an `FEditPropertyChain`, fires `PostEditChangeChainProperty` (a
  strict superset of the plain form — `Obj.cpp:541` calls it at the end) and sets `MemberProperty` to
  the outermost member. What remains open from that spec is `SetArrayIndexPerObject`: R1's own
  §UNVERIFIED item 6 says the key format for a nested path was never read out of
  `GenerateArrayIndexMapToObjectNode` (`PropertyHandleImpl.cpp:206`, `Private/`, unexported).
  Guessing a key format into a `check()`-guarded engine path is exactly the kind of plausible default
  PM-001/PM-002 are about, so `GetArrayIndex(Name)` still returns -1 in downstream handlers.
* **G5 (instance vs template) is partly shipped and was not extended.** The dangling-read defect is
  closed — `set_property` re-resolves after a construction-script rerun and fails as UNVERIFIED
  rather than reading a `TRASH_*` object — and `describe_property` / `edit_container` /
  `reset_property_to_default` all report `targetKind`-class facts (`isTemplate`, `archetype`,
  `creationMethod`, `owningActor`, `editableWhenInherited`, `cooked`). The spec's
  `survivesRerun` / `survivesRerunReason` / `templateEquivalent` fields on `set_property` are NOT
  emitted: computing them means predicting `FComponentInstanceDataCache::ShouldSkipProperty` and
  `GetUCSModifiedProperties` per write, and the honest failure text for the case that actually breaks
  is already in the endpoint.
* **G7 (`create_instanced_subobject`) not built.** It constructs a `UObject` with a flag mask copied
  from `SPropertyEditorEditInline.cpp:290-341` and renames the previous subobject into the transient
  package. It is a creation verb with no read verb to validate it against yet, and PM-007 says a
  creation path needs its validation ordered in front of it; `describe_property` now reports
  `instanced` and `allowedClasses`, which is the discovery half it would need. Deferred whole.
* **G9 (multi-target writes) not built.** R1 ranks it last itself: "convenience, not capability —
  everything it does is already expressible as N calls or one `batch`."
* **`FProperty::PerformOperationWithSetter` is not used by `edit_container`.** The panel routes
  container add/insert/remove through it (`PropertyHandleImpl.cpp:1132/1148/1166) so a
  `UPROPERTY(Setter=...)` container is written via its setter. Wiring it means handing a
  `TFunctionRef` a *direct* address the setter may relocate, which is the pointer-invalidation hazard
  R1 §2.4 lists, and the 288 `Setter=` declarations in `Runtime/**.h` are overwhelmingly scalars, not
  containers. Refusing to guess: the containers this endpoint edits are written directly, exactly as
  `set_property` already writes scalar values.
* **`ClampMin` on a value that is not whole-number-parseable is not clamped.** `enforceClamps` acts
  only on `FNumericProperty` leaves whose text parses WHOLE through `ParseWholeNumber`; a struct-typed
  or expression-typed value falls through to the reporting path. Stated rather than silently skipped.
* **Nothing was built and the editor was not launched.** Every engine claim is a source citation with
  a file and line; every bridge claim is one of the curls above, which the main session runs.

---

## Batch O — editor UI invocation (actions, not pixels)

_Source-only. Nothing was built and the editor was not launched; every engine claim below is a
`file:line` citation against `D:/UE532` with its export macro and access specifier, and every bridge
claim is a curl the main session runs after it builds. Spec:
`docs/audit/work/R2_UI_AUTOMATION.md`, implemented in its §8 ranked order._

The question this batch answers is the one the user asked in plain words — *"assigning anything in
the details panel or clicking the UI"* — and the answer the research reached is that **you invoke the
bound ACTION; you do not click the pixel**. Clicking is not "the escape hatch with extra steps": for
the two motivating cases (a third-party plugin's toolbar button, a custom editor window) invoking the
action reaches them *better*, and pixel-clicking mostly cannot reach them at all. `ui_click` is
therefore **deliberately not built**, and the whole decision — the design it would need, the §7
guardrails, and the §6 list of what cannot be made safe — is written down at the end of this section
so a future session can decide it deliberately rather than rediscover it.

### The three counts

| thing | before | after |
|---|---|---|
| `MIF_DECL` in `Source/MifBridge/Private/MifBridgeHandlers.h` | 195 | **199** |
| `MIF_BIND` in `Source/MifBridge/Private/MifBridgeCommon.cpp` | 195 | **199** |
| `H_*` handler definitions across `Private/*.cpp` | 195 | **199** |
| `@mcp.tool()` in `tools/ue5-mcp-bridge/server.py` | 207 | **211** |
| `_post("…")` targets in `server.py` | 207 | **211** |

`MIF_DECL` set ≡ `MIF_BIND` set ≡ `H_*` set — diff empty in all six directions, verified by script.
`server.py` − `MIF_BIND` = the 12 `kr_*` externals and nothing else, unchanged.
**`MifBridge.Build.cs` was NOT touched.** No new module dependency was added; see *The command-list
problem* below for the route that made `LevelEditor` / `MainFrame` unnecessary.

### New endpoints — registry lines, verbatim

```cpp
// MifBridgeHandlers.h, at the end of the MIF_DECL block:
MIF_DECL(list_editor_commands);
MIF_DECL(invoke_editor_command);
MIF_DECL(invoke_editor_tab);
MIF_DECL(send_editor_key);

// MifBridgeCommon.cpp, in Handlers(), after MIF_BIND(pie_unload_level_instance):
// Editor UI invocation (Batch O) - MifBridgeUI.cpp
MIF_BIND(list_editor_commands);
MIF_BIND(invoke_editor_command);
MIF_BIND(invoke_editor_tab);
MIF_BIND(send_editor_key);

// MifBridgeCommon.cpp, IsReadOnlyEndpoint()'s TSet, after TEXT("build_navmesh"):
TEXT("list_editor_commands")

// MifBridgeCommon.cpp, IsSelfManagedEndpoint()'s TSet, after the PIE level-instance pair:
TEXT("invoke_editor_command"), TEXT("invoke_editor_tab"), TEXT("send_editor_key")
```

New file: `Source/MifBridge/Private/MifBridgeUI.cpp` (LF line endings, matching its neighbours).

### Buckets, and why these are not the buckets the spec named

| endpoint | bucket | why that bucket |
|---|---|---|
| `list_editor_commands` | **read-only** | Enumerates registries and invokes nothing. The only third-party code it can reach is a command's `FCanExecuteAction`, and only under the opt-in `includeCanExecute`. Outside `IsReadOnlyEndpoint` every call would push an empty entry onto the stack `list_transactions` exists to report. |
| `invoke_editor_command` | **self-managed** | *(spec said "transacted")* |
| `invoke_editor_tab` | **self-managed** | *(spec said "transacted")* |
| `send_editor_key` | **self-managed** | *(spec said "transacted")* |

**The deviation is deliberate and it is the safer answer.** These three execute code MifBridge did
not write and cannot inspect: a bound `FUIAction`, a tab spawner, or whatever a keystroke happens to
be bound to. Any of it may open its own `FScopedTransaction` (most editor commands do), run a full
`FKismetEditorUtilities::CompileBlueprint`, or **be** undo/redo. Beginning an undo inside an open
transaction violates the engine's own `ensure(!GIsTransacting)`
(`Editor/UnrealEd/Classes/Editor/TransBuffer.h:74`), and a compile captured by an undo step restores
a dead CDO and crashes — the two hazards `IsSelfManagedEndpoint` exists for. There is no way to know
in advance which of those an arbitrary third-party action is, so the only honest bucket is the one
that opens **nothing**: the invoked action then behaves exactly as it does when a human clicks it,
including owning its own undo step, which is also the undo the user expects to see.

A wrapping transaction would also have bought nothing real. Ctrl-Z does not close a tab, does not
un-press a key, and does not un-run a third-party command that manages its own undo.

Being self-managed additionally makes all three **compile-heavy**
(`IsCompileHeavyEndpoint` derives from that set), so `batch` refuses them. Correct, and for the same
reason: firing an editor action inside batch's single open transaction is the identical hazard.

---

### The command-list problem, and the route that solved it with no new module dependency

This is the one genuinely new mechanism in the batch, and it closes
`R2_UI_AUTOMATION.md` §9 **UNVERIFIED item 3**.

`FUICommandList::TryExecuteAction` needs a **live command list**. Three facts about where those come
from, all verified:

1. **`FInputBindingManager` stores none.** `RegisterCommandList` is a pure broadcast that keeps
   nothing —
   `D:/UE532/Engine/Source/Runtime/Slate/Private/Framework/Commands/InputBindingManager.cpp:561-569`:
   ```cpp
   bool FInputBindingManager::RegisterCommandList(const FName InBindingContext, const TSharedRef<FUICommandList> CommandList) const
   {
       if (ContextMap.Contains(InBindingContext) && OnRegisterCommandList.IsBound())
       {
           OnRegisterCommandList.Broadcast(InBindingContext, CommandList);
           return true;
       }
       return false;
   }
   ```
2. **The two global lists are in modules MifBridge cannot reach.**
   `FLevelEditorModule::GetGlobalLevelEditorActions()` (`Editor/LevelEditor/Public/LevelEditor.h:167`)
   and `IMainFrameModule::GetMainFrameCommandBindings()` need the `LevelEditor` / `MainFrame` modules.
   Both are **`PrivateDependencyModuleNames`/`DynamicallyLoadedModuleNames` of UnrealEd**
   (`Editor/UnrealEd/UnrealEd.Build.cs:147` inside the `PrivateDependencyModuleNames` block that runs
   `:98-184`; `:206` and `:215` inside `DynamicallyLoadedModuleNames` from `:187`), so they are **not**
   transitively available and reaching them would be a `Build.cs` change — which this batch, unable to
   build or test, deliberately did not make.
3. **`FInputBindingManager::OnRegisterCommandList` is a PUBLIC multicast member** and five engine
   sites broadcast onto it:

   | broadcaster | file:line |
   |---|---|
   | `FLevelEditorModule::StartupModule` | `Editor/LevelEditor/Private/LevelEditor.cpp:281` |
   | `FMainFrameModule::StartupModule` | `Editor/MainFrame/Private/MainFrameModule.cpp:600` |
   | `SLevelViewport` (per viewport widget) | `Editor/LevelEditor/Private/SLevelViewport.cpp:1381` |
   | `SContentBrowser` (per browser widget) | `Editor/ContentBrowser/Private/SContentBrowser.cpp:678` |
   | `FSequencer` (per sequencer instance) | `Editor/Sequencer/Private/Sequencer.cpp:668-669` |

**The timing is verified, not assumed** — and it has to be, because a broadcast that happens before
we subscribe is lost forever (nothing stores it). PostEngineInit plugin modules load *inside*
`FEngineLoop::Init` (`Runtime/Launch/Private/LaunchEngineLoop.cpp:4838-4840`), and `EditorInit` calls
`EngineLoop.Init()` at `Editor/UnrealEd/Private/UnrealEdGlobals.cpp:111` **before** loading MainFrame
and building the editor UI at `:171`. MifBridge is `"LoadingPhase": "PostEngineInit"`
(`MifBridge.uplugin`), so `FMifBridgeModule::StartupModule` runs before all five.

Implementation: `MifBridge::SubscribeCommandListObserver()` / `UnsubscribeCommandListObserver()` /
`AreCommandListsObserved()` / `GetCachedCommandListContexts()` / `GetCachedCommandLists()`, declared
in `MifBridgeHandlers.h` (TWO translation units need them — `MifBridge.cpp` subscribes,
`MifBridgeUI.cpp` reads — so a second copy would be the PM-005 bug class) and defined ONCE in
`MifBridgeUI.cpp`. The cache is keyed by context and holds `TWeakPtr<FUICommandList>`: a closed
viewport's list must not be kept alive by us, and a dead entry must be reported as gone rather than
invoked. Subscription is skipped under `IsRunningCommandlet()`, same as the server autostart.

**What this does NOT give you, stated so nobody discovers it in the field:** a list registered before
MifBridge subscribed, or never broadcast at all, is invisible. `list_editor_commands` reports
`commandListSource.observed` and `commandListSource.contextsWithLists`, and
`invoke_editor_command`'s refusal names both plus the two routes forward. The response never implies
the cache is complete.

---

### 1. `invoke_editor_tab` — rank 1 (high usefulness, high safety)

```
in : { tabId?|tab?, manager? = "global", majorTab?, asset?, probe? = false,
       probeIds?: [string], includeKnownIds? = true, asInactive? = false }
out: { ok, manager, managerResolved, tabId, hasSpawner, alreadyOpen, invoked, tabLabel,
       tabActive, tabForeground, probes:[{tabId, hasSpawner, open}], probed, availableCount,
       workspaceMenuTabIds:[], enumerable:false, enumerationNote, openAssetEditors?:[] }
```

| API | verbatim | export | access |
|---|---|---|---|
| `FTabManager::TryInvokeTab` | `SLATE_API virtual TSharedPtr<SDockTab> TryInvokeTab(const FTabId& TabId, bool bInvokeAsInactive = false);` — `Runtime/Slate/Public/Framework/Docking/TabManager.h:912` | `SLATE_API` | **public** (`public:` `:786`, next specifier `protected:` `:1000`) |
| `FTabManager::FindExistingLiveTab` | `:920` | `SLATE_API` | public |
| `FTabManager::HasTabSpawner` | `SLATE_API bool HasTabSpawner(FName TabId) const;` — `:981` | `SLATE_API` | public |
| `FTabManager::GetLocalWorkspaceMenuRoot` | `:969` | `SLATE_API` | public |
| `FGlobalTabmanager::Get` | `:1203` | `SLATE_API` | public (`public:` `:1201`) |
| `FGlobalTabmanager::GetTabManagerForMajorTab` | `SLATE_API TSharedPtr<FTabManager> GetTabManagerForMajorTab(const TSharedPtr<SDockTab> DockTab) const;` — `:1257` | `SLATE_API` | public |
| `FTabId(const FName)` | `:66-70` | struct | public |
| `FWorkspaceItem::GetChildItems` / `AsSpawnerEntry` | `Runtime/Slate/Public/Framework/Docking/WorkspaceItem.h:73`, `:108` | header-inline / virtual | public |
| `FTabSpawnerEntry::GetTabType` | `TabManager.h:290` | inline | public (`private:` starts `:300`) |
| `IAssetEditorInstance::GetAssociatedTabManager` | `virtual TSharedPtr<class FTabManager> GetAssociatedTabManager() = 0;` — `Editor/UnrealEd/Public/Subsystems/AssetEditorSubsystem.h:77` | pure virtual | public |
| `UAssetEditorSubsystem::FindEditorForAsset` / `GetAllEditedAssets` | `:138`, `:161` | `UNREALED_API` | public |

**This is the route BlueprintAssist itself uses** to open all three of its own windows —
`BlueprintAssistGlobalActions.cpp:147`, `BlueprintAssistModule.cpp:117`,
`BlueprintAssistToolbar.cpp:533/546/557` all call `FGlobalTabmanager::Get()->TryInvokeTab(...)`. So
"open a custom editor window", the second motivating case, is one public call and no pixels.

**Three tab managers, no new module dependency.** `manager` selects:

* `"global"` — `FGlobalTabmanager::Get()`. Nomad/global tabs: `OutputLog`, `ReferenceViewer`,
  `PluginsEditor`, `BADebugMenu`, …
* `"majorTab"` + `majorTab:"LevelEditor"` — `FindExistingLiveTab` on the global manager, then
  `GetTabManagerForMajorTab`. **This is what makes the LEVEL EDITOR's own tab manager reachable
  without the `LevelEditor` module**: the major tab is a global nomad tab, and the global manager
  knows which child manager was created for it. `LevelEditorSelectionDetails` — the level Details
  panel — lives here.
* `"assetEditor"` + `asset:<path>` — `UAssetEditorSubsystem::FindEditorForAsset(…, /*bFocusIfOpen*/ false)`
  → `GetAssociatedTabManager()`. Blueprint-editor tabs (`Inspector`, `MyBlueprint`, `Palette`,
  `BlueprintDefaults`) — **the Blueprint Details panel** — live here. `bFocusIfOpen:false` matters: a
  discovery call must not steal the user's focus.

**The discovery half, designed honestly around the trap.** Tab ids **cannot be enumerated** from a
plugin: `FTabSpawner TabSpawner;` (`TabManager.h:1114`) and
`SLATE_API bool HasTabSpawnerFor(FName) const;` (`:1117`) are both under `protected:` (`:1113`)
**despite carrying the export macro** — the same exported-but-inaccessible shape this project has hit
before. What *is* public is `HasTabSpawner` (`:981`), which probes **one** id. So the endpoint:

1. probes a curated seed of ~45 ids drawn from the engine's own tab-id constants and from
   BlueprintAssist — which hardcodes the same kind of list for the same reason, with the comment
   *"Nomad unlisted tabs - search for '->RegisterNomadTabSpawner('"*
   (`BlueprintAssist/Private/BlueprintAssistWidgets/BAOpenWindowMenu.cpp:529`), independent
   third-party confirmation that probe-not-enumerate is the honest primitive;
2. plus anything in `probeIds[]`;
3. plus a walk of `GetLocalWorkspaceMenuRoot()` → `GetChildItems()` → `AsSpawnerEntry()->GetTabType()`,
   reported separately as `workspaceMenuTabIds[]` and labelled **partial**, because a spawner appears
   there only if it was given a group (`FTabSpawnerEntry::SetGroup`) and the engine's nomad spawners
   are grouped into the `WorkspaceMenuStructure` module's own root, not this manager's local root.

Every `hasSpawner` in `probes[]` is a **live answer from this editor**, never a claim from the seed
list, and ids that answered false are omitted rather than listed as if they existed. The response
carries `enumerable:false` and an `enumerationNote` citing `TabManager.h:1113-1117`.

**Modal disposition.** A tab spawner is third-party code and can show a dialog while constructing its
widget — that is exactly how a BlueprintAssist popup took this bridge down once
(`docs/02_GOTCHAS.md` §8). What CAN be pre-validated is: `HasTabSpawner` refuses an unknown id before
anything is constructed, and a bare/`probe:true` call constructs nothing at all. Opening a tab is
reversible by closing it, which is why this endpoint is **not** confirm-gated the way the other two
are. A `TryInvokeTab` that returns null is reported as `ok:false` naming the likely causes, never as
a silent success.

**Not implemented on purpose:** closing a tab. `SDockTab::RequestCloseTab` runs a third-party
`OnCanCloseTab` that is free to show a dialog, and there is no pre-check for that. The parameter is
refused by name with that reason rather than ignored.

---

### 2. `list_editor_commands` + `invoke_editor_command` — rank 2

```
list_editor_commands                                        [read-only]
in : { context?, command?, filter?, includeUnbound? = true, includeCanExecute? = false,
       includeConsole? = false, consolePrefix?, menu?, section?, limit? = 400 }
out: { ok, contexts:[{context, description, commandCount, cachedCommandLists,
                      commands:[{name, label, description, chord{key,ctrl,alt,shift,cmd,text,valid},
                                 altChord, bound, inputText, mappedInLists,
                                 canExecuteKnown, canExecute|null, modalDenied?}]}],
       contextCount, knownContextCount, matchedCommands, emittedCommands, truncated,
       commandListSource:{observed, contextsWithLists, mechanism, limitation},
       console?:{prefix, matched, emitted, truncated, objects:[{name, help, kind}]},
       menu?:{name, registered, enumerable:false, enumerationNote, entryCount,
              sections:[{name, ownerMenu, entryCount,
                         entries:[{name, label, type, isSubMenu, invokeKind, hasCommandList}]}]} }
```

Three halves, each labelled with what it can and cannot see:

**(a) Binding contexts — genuinely ENUMERABLE.**

| API | verbatim | export | access |
|---|---|---|---|
| `FInputBindingManager::Get` | `static SLATE_API FInputBindingManager& Get();` — `Runtime/Slate/Public/Framework/Commands/InputBindingManager.h:32` | `SLATE_API` (the **class** has no export macro; every method needed carries its own) | **public** `:27` (`private:` at `:198`) |
| `GetKnownInputContexts` | `:45` | `SLATE_API` | public |
| `GetCommandInfosFromContext` | `:126` | `SLATE_API` | public |
| `FindCommandInContext(FName, FName)` | `:101` | `SLATE_API` | public |
| `OnRegisterCommandList` / `OnUnregisterCommandList` | plain multicast members, declared just above `private:` | none needed | **public** |
| `FUICommandInfo::GetLabel/GetDescription/GetCommandName/GetBindingContext` | `UICommandInfo.h:241/244/253/256` | inline | public (`public:` `:188`, `private:` `:280`) |
| `FUICommandInfo::GetActiveChord` | `:207` | inline | public |
| `FUICommandInfo::GetInputText` | `:202` | `SLATE_API` | public |
| `FBindingContext::GetContextName/GetContextDesc` | `:117`, `:132` | inline | public |
| `FInputChord::Key/bCtrl/bAlt/bShift/bCmd`, `GetInputText`, `IsValidChord` | `InputChord.h:29-45`, `:181`, `:213` | member / `SLATE_API` / inline | public |

**This reaches third-party plugins with zero coupling**, which is the whole point:
BlueprintAssist registers `TCommands<FBACommandsImpl>(TEXT("BlueprintAssistCommands"), …)`
(`BlueprintAssist/Public/BlueprintAssistCommands.h:13-21`), so
`GetCommandInfosFromContext("BlueprintAssistCommands", …)` lists every one of its ~150 commands with
label, description and current chord **without MifBridge linking against BlueprintAssist at all**.

`canExecute` is `null` unless `includeCanExecute:true` — calling `FCanExecuteAction` runs third-party
code, so it is opt-in, and an unknown answer is reported as `null` with `canExecuteKnown:false`
rather than guessed.

**(b) Console objects — enumerable, opt-in.** `IConsoleManager::ForEachConsoleObjectThatStartsWith`
(`Runtime/Core/Public/HAL/IConsoleManager.h:984`, pure virtual on the public interface),
`IConsoleObject::GetHelp` (`:346`) / `AsCommand` (`:419`). This is the discovery half the spec wanted
paired with an exec endpoint; it lives here rather than in a new endpoint because the exec endpoint
itself was folded into `run_console` (below).

**(c) ONE named menu — probe-only, opt-in.**

> **The access trap, honoured.** `UToolMenu::FindEntry` is **PRIVATE** —
> `Developer/ToolMenus/Public/ToolMenu.h:102-106`, under `private:` at `:98`, reachable only by
> `friend class UToolMenus;` — even though the whole class carries `TOOLMENUS_API`. Exported and
> unusable. This code therefore walks the **public** `UPROPERTY() TArray<FToolMenuSection> Sections;`
> (`ToolMenu.h:161-162`) and uses `FToolMenuSection::FindEntry`, which **is** public
> (`ToolMenuSection.h:59-60`, `public:` at `:29`, `private:` at `:62`) over the public
> `UPROPERTY() TArray<FToolMenuEntry> Blocks;` (`:88-89`).

`UToolMenus::IsMenuRegistered` (`ToolMenus.h:140`) gates it, and `CollectHierarchy` (`:216`) lists it.
**`CollectHierarchy`, never `GenerateMenu`**: `GenerateMenu` allocates a `UToolMenu` and runs
third-party dynamic-section construct delegates (`Developer/ToolMenus/Private/ToolMenus.cpp:1881-1901`)
— listing must not have side effects, invoking may. Menu **names** are not enumerated: `UToolMenus`
keeps its registry in the private member `TMap<FName, TObjectPtr<UToolMenu>> Menus;`
(`ToolMenus.h:390-391`) and exposes no enumerator; reflecting into another module's private state to
produce a *listing* was judged the wrong trade and the response says `enumerable:false` with the
citation instead of pretending.

`invokeKind` per entry is computed from the only public probe there is —
`FToolMenuEntry::GetActionForCommand(EmptyContext, OutList)` (`ToolMenuEntry.h:138`) returns non-null
exactly when the entry is command-backed **and** its command list is reachable. Entries built from a
raw `FUIAction` or an `FToolMenuStringCommand` keep both in private, non-`UPROPERTY` members
(`ToolMenuEntry.h:214`, `:216`) and are labelled `"unreachableOrToolUIAction"` rather than advertised
as invokable and then failing.

```
invoke_editor_command                                       [self-managed]
in : { context, command, menu?, section?, entry?,
       dryRun? = false, confirm? = false, allowKnownModal? = false }
out: { ok, context, command, label, description, chord, resolvedVia, actionFound,
       canExecuteChecked, canExecute, invoked, dryRun, cachedListsForContext,
       modalHazard, modalDenied?, modalDeniedReason?, note }
```

| API | verbatim | export | access |
|---|---|---|---|
| `FUICommandList::IsActionMapped` | `UICommandList.h:125` | `SLATE_API` | public (`public:` `:17`, `protected:` `:207`) |
| `FUICommandList::CanExecuteAction` | `:140` | `SLATE_API` | public |
| `FUICommandList::TryExecuteAction` | `:148` | `SLATE_API` | public |
| `FToolMenuEntry::GetActionForCommand` | `ToolMenuEntry.h:138` | struct-wide `TOOLMENUS_API` | public (`private:` `:153`) |
| `FToolMenuEntry::TryExecuteToolUIAction` | `:149` | struct-wide | public |
| `FToolMenuSection::FindEntry` | `ToolMenuSection.h:59` | struct-wide | public |

`TryExecuteAction`, not `ExecuteAction` — the latter's own comment at `UICommandList.h:129` says
*"It is assumed at this point that CanExecuteAction was already checked"*.

**Resolution order, each step failing closed with a distinct error:**

1. `FindCommandInContext(context, command)`. Unknown context → the full known-context list plus
   `NearMissSuggestion`. Unknown command → the context's command names plus near misses.
2. The verified modal deny-list (below), checked **before** anything else so a caller who passed
   `confirm:true` by reflex still cannot trip one of the known-unconditional cases.
3. A live `FUICommandList`: `menu`+`entry` → `CollectHierarchy` → `Sections` →
   `FToolMenuSection::FindEntry` → `GetActionForCommand`; otherwise the `OnRegisterCommandList` cache.
   `menu` without `entry` (or vice versa) is a named error, not a silent narrowing.
4. `CanExecuteAction`. **False refuses the call** — the editor draws that entry greyed out, and
   `TryExecuteAction` would do nothing while reporting nothing.
5. `confirm:true`. Missing → `ok:false` naming the parameter and stating that everything else checked
   out. It never answers `ok:true` having done nothing.
6. `TryExecuteAction`. A `false` return after `CanExecute` said true is `ok:false` naming the two
   causes (mapping removed between the calls; unbound `FExecuteAction`, `UIAction.h:165`).
7. If there is no command list but the named entry exists, `TryExecuteToolUIAction` is the documented
   fallback — and its `false` return is reported as the §2.3 limitation *by name*, not as a generic
   failure.

**`dryRun:true` runs steps 1–4 and fires nothing.** The MCP tool description tells an agent to start
there.

**Modal disposition — the whole risk, and it cannot be closed.** The action is arbitrary third-party
code. If it opens a modal the game-thread ticker stops, this HTTP server stops reading its socket,
and the call **never returns** (`docs/02_GOTCHAS.md` §8). What is done about it:

* `confirm:true` required; `dryRun:true` available; `CanExecute` gate — the three that carry real
  weight.
* A **verified** deny-list, exact-matched on `Context.Command`, refused unless
  `allowKnownModal:true`. It is a **seed**, and the docs and the code say so in those words. Every
  entry cites the line that opens the dialog:

  | context.command | mechanism | citation | conditional? |
  |---|---|---|---|
  | `MainFrame.AboutUnrealEd` | `FSlateApplication::AddModalWindow` | `Editor/MainFrame/Private/Frame/MainFrameActions.cpp:725` | no — unconditional |
  | `MainFrame.CreditsUnrealEd` | `FSlateApplication::AddModalWindow` | `:753` | no — unconditional |
  | `MainFrame.ZipUpProject` | `IDesktopPlatform::SaveFileDialog` | `:470` | no — unconditional |
  | `MainFrame.OpenIDE` | `FMessageDialog::Open` on failure | `:443` | yes, but the failure branch is the normal case in an agent session |

  Command-to-callback mapping verified at `MainFrameActions.cpp:224`, `:227`, `:139`, `:136`; context
  name `"MainFrame"` at `Editor/MainFrame/Private/Frame/MainFrameActions.cpp:75-78`.
  `list_editor_commands` reports `modalDenied` per command so the list is visible, not hidden.
* Every response carries `modalHazard` with the out-of-process diagnostic:
  `Get-Process UnrealEditor | Select-Object Id,MainWindowTitle`. **A call that never returns IS the
  symptom.**

There is no way to make this safe in general from inside the process (R2 §6 item 1), and the tool
description says exactly that rather than implying a guarantee.

---

### 3. `run_editor_exec` — FOLDED INTO `run_console`, and here is the justification

The spec ranked a separate `run_editor_exec` third. **It was not built as a separate endpoint**,
because it would have been a *third* copy of "call `UEngine::Exec` and describe the result" —
`run_console` and `run_console_captured` are the first two — and a third copy of a shared behaviour is
precisely the bug class PM-005 exists for. The brief allowed either justification or folding; folding
is the answer the house rules force.

Everything the new endpoint was supposed to ADD is now in `run_console`, additively:

| the spec wanted | what `run_console` gained |
|---|---|
| structured result (`FStringOutputDevice`) | `execOutput` (string) + `execOutputLines` (array) + `outputCaptured` |
| editor-target routing | `world`: `editor` (default, unchanged) / `pie` (refused when not playing) / `active` |
| — | `RejectUnknownParams`, which this endpoint **never had**: `run_console {command:"x", target:"editor"}` used to answer `ok:true` having silently ignored `target` |
| — | `cmd` as an additive alias for `command` |

**Nothing was renamed.** `command` and `executed` mean exactly what they always meant, and
`captureOutput:false` reproduces the old call byte for byte (`Ar = *GLog`).

The single Exec call site is now `MifBridge::RunEngineExec(UWorld*, const FString&, FString* OutText)`
— declared in `MifBridgeHandlers.h`, defined in `MifBridgeCommon.cpp`, called by **both**
`H_run_console` and `H_run_console_captured` (whose direct `GEngine->Exec` was deleted, not
duplicated). It records the overload trap once instead of in two comment blocks:

```
ENGINE_API virtual bool UEngine::Exec(UWorld*, const TCHAR*, FOutputDevice& = *GLog)
     Runtime/Engine/Classes/Engine/Engine.h:2224, under `public:` at :2222   <- THIS ONE
ENGINE_API virtual bool UEngine::Exec_Editor(...)          Engine.h:2229, `protected:` at :2227
UNREALED_API virtual bool UEditorEngine::Exec_Editor(...)  Editor/UnrealEd/Classes/Editor/EditorEngine.h:817, `protected:` at :816
```

Both `Exec_Editor` overloads carry export macros and are still inaccessible — the same shape as the
`UClass::IsA` incident. `UEngine::Exec` dispatches into them anyway.

**The output device TEES; it does not replace.** `run_console`'s documented workflow is *"run it, then
tail `<Saved>/Logs/`"* and every `mif.kr.*` command relies on it. A capture that swapped `*GLog` for a
plain string device would silently delete from the log exactly the output the caller was told to go
and read. `RunEngineExec`'s device forwards every `Serialize` to `GLog` **and** keeps a copy, capped
at 256 KB with an explicit truncation marker so a cap never looks like completeness. Passing
`OutText == nullptr` takes the old path unchanged.

`run_console_captured` therefore keeps its `output[]` (log lines bracketed by `FScopedLogCapture`)
byte-identical, and gains `execOutput` with the same meaning it has on `run_console`. **One field
name, one meaning, across both endpoints** — the drift this project keeps paying for, closed by
construction.

`run_console`'s modal disposition is stated in its own comment block and in the tool description: an
exec command is arbitrary registered code, it runs inline on the game thread, and there is no
deny-list because the console surface is open-ended and a name-based list there would be theatre.
`list_editor_commands {includeConsole:true, consolePrefix:"…"}` is the pre-check.

---

### 4. `send_editor_key` — rank 4

```
in : { key, confirm? = false, dryRun? = false, modifiers?{ctrl,alt,shift,cmd},
       userIndex? = 0, isRepeat? = false, characterCode? = 0, keyCode? = 0, sendKeyUp? = true }
out: { ok, key, keyValid, sent, downHandled, upHandled, keyLeftDown,
       modifiersRequested, modifiersReal, modifiersSatisfiedByRealKeyboard,
       focusedWidget{type, readableLocation}, activeWindow, activeWindowMinimized, note }
```

| API | verbatim | export | access |
|---|---|---|---|
| `FSlateApplication::ProcessKeyDownEvent` | `SlateApplication.h:1219` | `SLATE_API` | public (`public:` `:1159`) |
| `FSlateApplication::ProcessKeyUpEvent` | `:1227` | `SLATE_API` | public |
| `FSlateApplication::GetModifierKeys` | `:661` | `SLATE_API` | public (`public:` `:226`, next specifier `protected:` `:1056`) |
| `FSlateApplication::GetKeyboardFocusedWidget` | `:1483` | `SLATE_API` | public (`public:` `:1437`, `protected:` `:1490`) |
| `FSlateApplication::GetActiveTopLevelWindow` | `:1468` | `SLATE_API` | public |
| `FKeyEvent(FKey, FModifierKeysState, uint32, bool, uint32, uint32)` | `Runtime/SlateCore/Public/Input/Events.h:429-436` | struct | public `:410` |
| `FKey::IsValid` / `EKeys::GetAllKeys` | `Runtime/InputCore/Classes/InputCoreTypes.h:72`, `:706` | `INPUTCORE_API` | public |
| `SWidget::GetTypeAsString` / `GetReadableLocation` | `SlateCore/Public/Widgets/SWidget.h:1466`, `:1472` | `SLATECORE_API` | public `:1458` |

Pre-processors get first refusal —
`Runtime/Slate/Private/Framework/Application/SlateApplication.cpp:4645`,
`if (InputPreProcessors.HandleKeyDownEvent(*this, InKeyEvent)) { return true; }` — which is the
**only** route to commands a plugin dispatches from its own `IInputProcessor` rather than from a
reachable `FUICommandList`. BlueprintAssist is exactly that shape:
`FBAInputProcessor::ProcessCommandBindings` (`BlueprintAssistInputProcessor.cpp:1111`) runs against
command lists that are private members of BA singletons (`:145-359`), so `invoke_editor_command`
cannot reach them and this can. That is the gap this endpoint exists to close.

**THE MODIFIER REFUSAL — the design point the brief asked for, honoured literally.** The
`FModifierKeysState` carried in an `FKeyEvent` is **not** what consumers read.
`FSlateApplication::GetModifierKeys()` goes straight to the platform
(`SlateApplication.cpp:3034-3037`, `return PlatformApplication->GetModifierKeys();`) and
BlueprintAssist builds its `FInputChord` from that live state, not from the event
(`BlueprintAssistInputProcessor.cpp:1118-1123`). A synthetic `Ctrl+H` is therefore evaluated as bare
`H` — it would fire **the wrong command, silently**.

So: if `modifiers` are requested, the endpoint compares them against
`FSlateApplication::Get().GetModifierKeys()` and **REFUSES** unless the real platform keyboard
already has them down (a human physically holding them, which is the one case where it genuinely
works and is then reported as `modifiersSatisfiedByRealKeyboard:true`). The refusal quotes the real
state, names the bare key that WOULD have been delivered, and offers three concrete options. It never
downgrades a chord to its unmodified key. The AutomationDriver does not fix this either: its fake
modifier state is consulted only while pass-through is OFF (`AutomatedApplication.cpp:278-286`) and
`Enable()` turns pass-through ON (`AutomationDriverModule.cpp:66`).

**Inline, not deferred — a stated deviation from spec §5.2.** The spec deferred by one tick "out of
caution", and its own §9 UNVERIFIED item 5 says inline was never proved unsafe. Deferring would have
required an op ring plus a new poll endpoint, and it does **not** avoid the modal hazard — it only
moves it off our stack, at the cost of the response no longer being able to say what happened.
Running inline means `downHandled`/`upHandled` are real answers in the real response, consistent with
`invoke_editor_command` and `invoke_editor_tab`, which are inline for the same reason.

**Down and up in the same call.** Command dispatch happens on key DOWN for both Slate's binding path
and BlueprintAssist's processor, so pairing them immediately costs nothing and cannot strand a key
down if the bridge or the editor stops between the halves. `sendKeyUp:false` exists for the rare
consumer that wants the down alone and sets `keyLeftDown:true` plus a `warning` saying so.

**Also validated before anything is sent:** Slate initialised at all (refused in a commandlet); the
key name resolves to a registered `FKey`, with near misses from `EKeys::GetAllKeys` when it does not
(`"SpaceBar"` not `"Space"`); and the focused widget and active window are reported, because a key no
pre-processor claims goes to the focused widget — which is the difference between "it worked" and "it
went into a text box".

**Not implemented on purpose:** typing a string (`text`). `ProcessKeyCharEvent` per character goes
into whatever currently has focus, which is unbounded, and R2 §7 item 8 asks for a second explicit
flag around exactly that. The parameter is refused by name with the reason.

---

### Live proof (post-build) — run these in order

```bash
B=http://127.0.0.1:8791/api
H=(-H "X-Mif-Token: dev" -H "Content-Type: application/json")

# O-0  The bridge sees the four new endpoints and puts them in the right buckets.
curl -s -X POST $B/self_audit "${H[@]}" -d '{}'
# expect: endpointCount 211 (199 built-in + 12 kr_*), healthy:true, policyContradictions [],
#         transactionBuckets.readOnly contains "list_editor_commands",
#         transactionBuckets.selfManaged contains invoke_editor_command / invoke_editor_tab /
#         send_editor_key, and all three also appear in transactionBuckets.compileHeavy.

# O-1  Discovery half (a): third-party commands enumerate with ZERO coupling.
curl -s -X POST $B/list_editor_commands "${H[@]}" \
  -d '{"context":"BlueprintAssistCommands","limit":500}'
# expect: ok:true, contextCount 1, matchedCommands ~150, and each command carries name/label/
#         chord.key. This is the proof that a plugin MifBridge does not link against is fully
#         enumerable. If BlueprintAssist is disabled, use "LevelEditor" instead.

# O-2  The command-list mechanism actually caught the startup broadcasts.
curl -s -X POST $B/list_editor_commands "${H[@]}" -d '{"limit":1}'
# expect: commandListSource.observed:true and contextsWithLists containing at least
#         "LevelEditor" and "MainFrame" (LevelEditor.cpp:281, MainFrameModule.cpp:600).
#         An EMPTY contextsWithLists with observed:true means the subscription happened too late —
#         that is the one thing in this batch whose timing argument is worth re-checking live.

# O-3  Discovery half (c): a named ToolMenu lists, with no side effects.
curl -s -X POST $B/list_editor_commands "${H[@]}" \
  -d '{"menu":"LevelEditor.MainMenu.Tools","limit":50}'
# expect: menu.registered:true, menu.sections[] non-empty, and an entry named "MifBridgeToggle"
#         in section "MifBridge" (MifBridge.cpp's own menu) — proof CollectHierarchy read the real
#         live registry. Each entry carries invokeKind.

# O-4  Discovery half (b): console objects.
curl -s -X POST $B/list_editor_commands "${H[@]}" \
  -d '{"includeConsole":true,"consolePrefix":"mif.","limit":100}'
# expect: console.objects[] containing mif.BridgeAutoStart and mif.BridgeDebug (MifBridge.cpp:18/24)
#         and the mif.kr.* commands if MifKismetReconstructor is loaded.

# O-5  invoke_editor_tab DISCOVERY — probes only, opens nothing.
curl -s -X POST $B/invoke_editor_tab "${H[@]}" -d '{"probe":true}'
# expect: ok:true, invoked:false, enumerable:false, probes[] with hasSpawner:true for at least
#         "OutputLog" and (if BlueprintAssist is on) "BADebugMenu". availableCount > 0.

# O-6  invoke_editor_tab OPENS a real window — the "custom editor window" case.
curl -s -X POST $B/invoke_editor_tab "${H[@]}" -d '{"tabId":"OutputLog"}'
# expect: ok:true, hasSpawner:true, invoked:true, tabLabel "Output Log".
#         Re-run it: alreadyOpen:true, invoked:true (TryInvokeTab draws attention to a live tab).

# O-7  invoke_editor_tab reaches the LEVEL DETAILS PANEL without the LevelEditor module.
curl -s -X POST $B/invoke_editor_tab "${H[@]}" \
  -d '{"manager":"majorTab","majorTab":"LevelEditor","tabId":"LevelEditorSelectionDetails"}'
# expect: ok:true, managerResolved "child tab manager of major tab 'LevelEditor'",
#         hasSpawner:true, invoked:true. THIS is the GetTabManagerForMajorTab escape hatch.

# O-8  invoke_editor_tab reaches a BLUEPRINT DETAILS PANEL. Open a Blueprint first.
curl -s -X POST $B/open_blueprint "${H[@]}" -d '{"blueprintId":"<some /Game/... BP>"}'
curl -s -X POST $B/invoke_editor_tab "${H[@]}" -d '{"manager":"assetEditor"}'
# expect: ok:false naming `asset`, WITH openAssetEditors[] listing what is open — the failure IS
#         the discovery. Then:
curl -s -X POST $B/invoke_editor_tab "${H[@]}" \
  -d '{"manager":"assetEditor","asset":"<that path>","tabId":"Inspector"}'
# expect: ok:true, managerResolved "tab manager of BlueprintEditor editing <path>", invoked:true.

# O-9  An unknown tab id fails CLOSED with near misses, and opens nothing.
curl -s -X POST $B/invoke_editor_tab "${H[@]}" -d '{"tabId":"OutputLogg"}'
# expect: ok:false, "no tab spawner registered for 'OutputLogg' ... (did you mean 'OutputLog'?)",
#         invoked absent/false.

# O-10 invoke_editor_command DRY RUN — resolves, fires nothing.
#      SelectNone is real and verified: UI_COMMAND( SelectNone, "Unselect All", ... ) at
#      Editor/LevelEditor/Private/LevelEditorActions.cpp:3769, context "LevelEditor" (:3550).
curl -s -X POST $B/invoke_editor_command "${H[@]}" \
  -d '{"context":"LevelEditor","command":"SelectNone","dryRun":true}'
# expect: ok:true, invoked:false, actionFound:true, canExecuteChecked:true, resolvedVia
#         "registeredCommandListCache", and a note saying whether it is executable.

# O-11 Missing confirm is an ERROR naming the parameter — NOT ok:true having done nothing.
curl -s -X POST $B/invoke_editor_command "${H[@]}" \
  -d '{"context":"LevelEditor","command":"SelectNone"}'
# expect: ok:false, "...requires confirm=true ... (or dryRun=true)", invoked:false.

# O-12 The modal deny-list refuses BEFORE invoking, even with confirm.
curl -s -X POST $B/invoke_editor_command "${H[@]}" \
  -d '{"context":"MainFrame","command":"AboutUnrealEd","confirm":true}'
# expect: ok:false, modalDenied:true, modalDeniedReason citing MainFrameActions.cpp:725,
#         invoked:false — AND THE BRIDGE STILL ANSWERS THE NEXT CALL. That last part is the proof.

# O-13 A real invoke, with no dialog and an effect a DIFFERENT endpoint can see.
#      Select something first (select_level_actors) so "unselect all" has work to do.
curl -s -X POST $B/invoke_editor_command "${H[@]}" \
  -d '{"context":"LevelEditor","command":"SelectNone","confirm":true}'
# expect: ok:true, invoked:true. Verify independently:
curl -s -X POST $B/list_level_actors "${H[@]}" -d '{"selectedOnly":true,"limit":5}'
# expect: count 0 — the ACTION had the effect, proven by a different endpoint reading the state.

# O-14 Unknown context / command fail closed with near misses.
curl -s -X POST $B/invoke_editor_command "${H[@]}" \
  -d '{"context":"LevelEditor","command":"SelectNonee","dryRun":true}'
# expect: ok:false, "(did you mean 'SelectNone'?)"

# O-15 send_editor_key DRY RUN reports the focus context and sends nothing.
curl -s -X POST $B/send_editor_key "${H[@]}" -d '{"key":"Tab","dryRun":true}'
# expect: ok:true, keyValid:true, sent:false, focusedWidget{type, readableLocation},
#         activeWindow, activeWindowMinimized.

# O-16 THE MODIFIER REFUSAL — the headline safety behaviour.
curl -s -X POST $B/send_editor_key "${H[@]}" \
  -d '{"key":"H","modifiers":{"ctrl":true},"confirm":true}'
# expect (with nobody touching the keyboard): ok:false, sent:false,
#         modifiersSatisfiedByRealKeyboard:false, and an error quoting
#         SlateApplication.cpp:3034-3037 and BlueprintAssistInputProcessor.cpp:1118-1123.
#         It must NOT have sent bare H.

# O-17 An invalid key name fails with near misses, sends nothing.
curl -s -X POST $B/send_editor_key "${H[@]}" -d '{"key":"Space","dryRun":true}'
# expect: ok:false, keyValid:false, "(did you mean 'SpaceBar'?)"

# O-18 A real key. Focus a Blueprint graph first if BlueprintAssist is on (Tab = its creation menu).
curl -s -X POST $B/send_editor_key "${H[@]}" -d '{"key":"Tab","confirm":true}'
# expect: ok:true, sent:true, downHandled true or false (false only means nothing claimed it),
#         keyLeftDown:false.

# O-19 run_console gained a structured result and kept its old shape.
curl -s -X POST $B/run_console "${H[@]}" -d '{"command":"stat unit"}'
# expect: ok:true, command echoed, executed:true, worldTarget "editor", world "<map name>",
#         outputCaptured:true, execOutput/execOutputLines present.
curl -s -X POST $B/run_console "${H[@]}" -d '{"command":"stat unit","captureOutput":false}'
# expect: identical minus execOutput* — the byte-for-byte old behaviour.

# O-20 run_console rejects an unknown parameter instead of ignoring it (this used to answer ok:true).
curl -s -X POST $B/run_console "${H[@]}" -d '{"command":"stat unit","target":"editor"}'
# expect: ok:false naming `target` and listing the accepted keys.

# O-21 run_console world routing refuses honestly rather than silently using the editor world.
curl -s -X POST $B/run_console "${H[@]}" -d '{"command":"stat fps","world":"pie"}'
# expect (not playing): ok:false, "no PIE world exists — nothing was executed".
curl -s -X POST $B/run_console "${H[@]}" -d '{"command":"stat fps","world":"editorr"}'
# expect: ok:false listing editor|pie|active — an unrecognised value is never a silent default.

# O-22 run_console_captured still captures the LOG and now also reports execOutput.
curl -s -X POST $B/run_console_captured "${H[@]}" -d '{"command":"stat unit"}'
# expect: ok:true, output[] (log lines, unchanged) AND execOutput (the command's own device text).
```

---

## `ui_click` — NOT IMPLEMENTED. The decision, written down so it can be made deliberately.

This is not "we ran out of time". It is a design decision with evidence behind it, and the evidence
is here so a future session can revisit it in one sitting instead of re-deriving it.

### Why not

**It deadlocks the bridge if driven the obvious way, and this is provable, not suspected.**
`IDriverSequence::Perform()` bottoms out in a blocking future wait —
`Developer/AutomationDriver/Private/DriverSequence.cpp:1881-1884` → `:1835-1838`,
`return ActionSequence->Perform().GetFuture().Get();`. That promise is fulfilled only by the step
engine, which schedules onto `FTSTicker::GetCoreTicker()` and re-arms each step with a strictly
positive delay — `Private/StepExecutor.cpp:142` (`Delay = FMath::Max(SMALL_NUMBER, …)`) and `:151` —
so step N+1 **cannot** run in the same `FTSTicker::Tick()` pass as step N
(`Runtime/Core/Private/Containers/Ticker.cpp:103`: an element whose `FireTime > CurrentTime` is
deferred to a later tick). MifBridge handlers run **inside** that very tick, because
`FHttpServerModule` is an `FTSTickerObjectBase`
(`Runtime/Online/HTTPServer/Public/HttpServerModule.h:23-25`) and `MifBridgeServer.cpp:229-265`
deliberately runs the endpoint inline there. It does not even reach step 0: `Execute()` posts an
`AsyncTask(ENamedThreads::GameThread, …)` (`StepExecutor.cpp:57-80`) that is never pumped while the
game thread is parked in `TFuture::Get()`. The engine's own tests confirm the intended usage — every
case in `AutomationDriver.spec.cpp` is `EAsyncExecution::ThreadPool` (`:80, :85, :92, :100, :107`),
i.e. the synchronous API is only ever called from a worker thread.

**Its header lies about the safest-sounding thing it does.**
`IAutomationDriverModule.h:47-51` says enabling *"causes most traditional input messages from the
platform to stop being received"*. The implementation does the opposite:
`Private/AutomationDriverModule.cpp:66` calls `AutomatedApplication->AllowPlatformMessageHandling()`,
which turns pass-through **on** for both handler and cursor
(`Private/AutomatedApplication.cpp:178-189`). So the user's keyboard and mouse keep working, the user
and the driver drive the editor simultaneously, and **the user's physical OS cursor gets warped** —
`DriverSequence.cpp:825/894/907` calls `Cursor->SetPosition`, which forwards to the real cursor while
pass-through is on (`AutomatedApplication.cpp:33-44`). The only off-switch is a **physical ScrollLock
press** (`Private/PassThroughMessageHandler.cpp:53-69`); `DisablePlatformMessageHandling()` is
declared in a Private, unexported header. Enabling it fights a human at the keyboard, by design.

**It cannot address the thing the user actually asked about.** `By::Id` is sugar over a path locator
whose `#`-segments match **only** `FDriverIdMetaData` (`Private/LocateBy.cpp:29-32`,
`Private/Locators/SlateWidgetLocatorByPath.cpp:36-54`, `:295-309`). A ripgrep for
`FDriverIdMetaData` over `D:/UE532/Engine/Source/Editor` returns **zero matches**; over `Runtime` it
returns only the type's own declaration and its factory. **No engine editor widget carries a stable
automation id, so a Details-panel row cannot be targeted by identity at all.** `<SType>` paths do not
save it either: a Details panel holds dozens of identical widgets, and ambiguity is a hard failure —
`DriverSequence.cpp:102-126` returns `FStep::Failed()` when `Elements.Num() > 1`.

### What it would look like if it were built

Kept here in full so the next session starts from a design, not a blank page.

```
ui_click                                                 [self-managed, GATED]
  in : { enable: true,                       // explicit opt-in, required, no default
         locator: { by: "id"|"path"|"tag"|"type", value: string },
         action: "click"|"doubleClick"|"rightClick"|"moveTo"|"scrollTo"|"type",
         text?: string,
         timeoutMs?: int (default 15000, hard cap 60000),
         implicitWaitMs?: int (default 3000),
         restoreCursor?: bool (default true) }
  out: { ok, opId, deferred: true, pollWith: "ui_automation_status", warnings: [...] }

ui_automation_status                                     [read-only]
  in : { opId?: int }
  out: { ok, driverEnabled, busy, pendingOps, ops: [ { opId, phase, completed, ok, error,
           locator, action, elapsedMs, matchCount,
           resolvedElement: { type, tag, ids[], readableLocation, absolutePos, size },
           focusStolenFrom, cursorMovedFrom, cursorMovedTo, passThroughWasDisabled } ] }

ui_automation_abort                                      [self-managed]
  in : { opId?: int }         // omitted = abort all
  out: { ok, aborted: [opId], driverDisabled }
```

It **must** use `IAsyncAutomationDriver` / `IAsyncDriverSequence`
(`Public/IAutomationDriver.h:20`, `Public/IDriverSequence.h:434`, `Perform()` returning
`TAsyncResult<bool>` at `:446`), kick from the handler, return an `opId`, and poll
`GetFuture().IsReady()` (`Async/Future.h:219`) from a ticker across frames — never
`IAutomationDriver`. State machine, one phase per tick:

| phase | runs on | does |
|---|---|---|
| `queued` | handler frame | validate; refuse if the driver is already enabled by someone else; record op; return `opId` |
| `preflight` | tick N | windows visible and target not minimised; snapshot cursor pos and active window; run the locator **once**, record `matchCount`, **abort now if 0 or >1** — before any input is faked |
| `enabling` | tick N+1 | `IAutomationDriverModule::Get().Enable()`; assert `IsEnabled()` |
| `running` | tick N+2 | build the `IAsyncDriverSequence`, `Perform()`, keep the `TAsyncResult<bool>` |
| `awaiting` | every tick | `Result.GetFuture().IsReady()`; also re-check the deadline |
| `verifying` | on ready | re-run the post-condition probe |
| `disabling` | next tick | `Disable()`; restore cursor unless `restoreCursor:false` |
| `done` / `failed` / `timedOut` / `aborted` | — | write the ring entry, never overwrite an existing one |

`Perform()==true` is **not** proof: a ScrollLock press mid-sequence ends the chain and sets the
promise to **true** (`StepExecutor.cpp:113-119`). Every op record must therefore carry `matchCount`,
`resolvedElement.readableLocation` (`SWidget::GetReadableLocation`, `SWidget.h:1472` — the
`"BaseFileName(LineNumber)"` of the `SNew`, the single most useful field for a human reading the
log), `cursorMovedFrom`/`cursorMovedTo`, `focusStolenFrom`, and `passThroughWasDisabled`.

`MifBridge.Build.cs` would need `"AutomationDriver"` in `PrivateDependencyModuleNames`
(`Developer/AutomationDriver/AutomationDriver.Build.cs` has no `PublicDependencyModuleNames` block at
all; the only in-tree consumer is `Engine/Plugins/Tests/AutomationDriverTests`). The DLL ships:
`D:/UE532/Engine/Binaries/Win64/UnrealEditor-AutomationDriver.dll`.

### The guardrails it would need (R2 §7, verbatim in intent)

1. **Two-key gate.** A CVar (`mif.UIAutomation.Enabled`, default 0, set from the editor console **by
   a human**) **and** `enable:true` in the request body. Either alone refuses. The CVar exists so an
   agent cannot turn it on over the bridge.
2. **Never leave the driver enabled.** `Enable()` in `enabling`, `Disable()` in `disabling`, in the
   same op — on timeout, on abort, and on every failure path. `IsEnabled()` still true when the ring
   entry is written is a bug and must be logged as one.
3. **Preflight refuses before faking anything:** editor minimised; zero or multiple locator matches;
   another op in flight; target window not the active top-level window when `action` is a click
   (because `InternalActivateWindow` will raise it, `DriverSequence.cpp:1112-1139`).
4. **Single-flight, process-wide.** `Enable()` swaps `FSlateApplication`'s platform application
   globally (`AutomationDriverModule.cpp:56-67`), so two concurrent ops corrupt each other's
   save/restore. Return `ok:false, error:"ui automation busy (opId N)"` — never queue.
5. **Hard timeout, capped at 60 s** regardless of what the caller asks, and well under
   `MifOffThreadTimeoutSeconds = 120.0f` (`MifBridgeServer.cpp:89`) so the HTTP layer is never the
   thing that gives up first. On expiry go straight to `disabling`, record `timedOut`, and do **not**
   touch the `TAsyncResult` — the driver's ticker chain may still fire, so hold the shared ref (the
   discipline `FMifPendingCall` uses at `MifBridgeServer.cpp:64-86`).
6. **Restore the cursor.** Snapshot `FSlateApplication::Get().GetCursorPos()` in preflight, restore in
   `disabling`. The pointer physically moved on the user's desk.
7. **Tell the user about ScrollLock.** It is the only in-editor panic switch
   (`PassThroughMessageHandler.cpp:57-61`) and it silently changes semantics. Put it in the endpoint
   note and in `02_GOTCHAS.md`.
8. **Log every op at `Log`, not `Verbose`.** Faking input into a user's editor is not a debug event.
9. **Refuse `type` actions containing anything that could be a destructive keystroke** unless a second
   explicit flag is passed. `IActionSequence::Type` synthesises real key events
   (`DriverSequence.cpp:1163-1180`) into whatever currently has focus.
10. **Abort is a teardown, not a cancel.** `IAsyncDriverSequence` (`IDriverSequence.h:434-447`) has
    `Actions()` and `Perform()` and nothing else. `Disable()` is the only lever and it works *because*
    of `StepExecutor.cpp:113` — once `IsHandlingMessages()` goes false the chain stops. Report it as
    `aborted`, never as `ok`.

### What CANNOT be made safe — carried forward verbatim (R2 §6)

1. **A modal opened by an invoked action.** Not preventable in general. Only detectable from outside
   the process. **This one applies to everything in Batch O, not just to `ui_click`.**
2. **Chorded (modified) input, on either synthetic Slate input or the driver.**
   `FSlateApplication::GetModifierKeys()` reads real platform state
   (`SlateApplication.cpp:3034-3037`) and the driver's fake state is ignored while pass-through is on
   (`AutomatedApplication.cpp:278-286`). `send_editor_key` refuses rather than pretending — that is
   this batch's answer to this item.
3. **Clicking an engine Details-panel row by identity.** No `FDriverIdMetaData` exists anywhere in
   `Engine/Source/Editor`, and `<SType>` paths hit `TooManyElementsFound → FStep::Failed()`
   (`DriverSequence.cpp:107-111`). Refuse with an explanation; never attempt a coordinate hack.
4. **Cancelling a driver sequence cleanly.** Only `Disable()`, which tears down the platform-application
   swap under a running step chain.
5. **Running any of it while the editor is minimised.** `GetAllVisibleWindowsOrdered` filters
   minimised windows (`SlateApplication.cpp:3602-3612`). Preflight must refuse, not wait 3 s and fail.
6. **Guaranteeing the user does not fight the automation.** Pass-through stays on after `Enable()`;
   real input keeps flowing.
7. **Enumerating tab ids.** `TabSpawner` and `HasTabSpawnerFor` are both `protected`
   (`TabManager.h:1113-1117`). Probe-only — which is what `invoke_editor_tab` does.
8. **Invoking raw-`FUIAction` and string-command ToolMenus entries.** No public accessor
   (`ToolMenuEntry.h:214`, `:216`, both private, neither reflected). `list_editor_commands` labels
   them `"unreachableOrToolUIAction"` and `invoke_editor_command` refuses them by name.

**Route C's only honest remaining use** — recorded so a future session does not re-litigate the whole
question to reach it — is a widget that MifBridge itself authored or patched and deliberately tagged
with `FDriverMetaData::Id` (`Runtime/Slate/Public/Framework/MetaData/DriverMetaData.h:20`), plus
scroll/drag gestures that have no action equivalent. Everything else is better served by Batch O.

---

### Spec deviations, and what was deliberately NOT built

* **`run_editor_exec` — folded into `run_console`, not built as an endpoint.** Justified above: it
  would have been a third copy of the same `UEngine::Exec` call (PM-005), and everything it was meant
  to add is now in `run_console` additively. This is one of the two options the brief offered, taken
  explicitly.
* **Buckets are self-managed, not transacted** (spec §5.1/§5.2 said transacted). Reasoned above from
  this codebase's own transaction policy; the spec's table did not account for the invoked action
  being able to be undo/redo or a compile.
* **`send_editor_key` runs inline, not deferred through an op ring** (spec §5.2). Deferring needed an
  op ring plus a new poll endpoint, does not avoid the modal hazard, and would have cost the response
  its ability to report `downHandled`. The spec's own §9 item 5 records that inline was never proved
  unsafe.
* **`list_editor_menus` was not built as a separate endpoint** (spec §5.1). Its useful half — the
  sections and entries of a NAMED menu — is the `menu`/`section` parameters on
  `list_editor_commands`, so the ToolMenus invoke route has a discovery half without a new endpoint.
  Its other half, enumerating menu NAMES, would require reflecting into `UToolMenus::Menus`, a private
  member of another module, purely to produce a listing; that was judged the wrong trade and the
  response says `enumerable:false` with the citation rather than pretending otherwise. Spec §9 item 4
  also records that this reflection was never live-probed.
* **No `LevelEditor` / `MainFrame` module dependency, so no `Build.cs` change.** The
  `OnRegisterCommandList` route reaches those command lists anyway (and four more), and adding a
  module dependency that cannot be compile-tested in this session would put a build break in the main
  session's path for no capability gain.
* **`ui_click` / `ui_automation_status` / `ui_automation_abort` not built.** The whole decision is the
  section above.
* **Closing a tab (`invoke_editor_tab {close:…}`) not built.** `SDockTab::RequestCloseTab` runs a
  third-party `OnCanCloseTab` that may show a dialog, with no pre-check available. Refused by name.
* **Typing a string (`send_editor_key {text:…}`) not built.** `ProcessKeyCharEvent` per character goes
  into whatever has focus, which is unbounded; R2 §7 item 8 wants a second explicit gate. Refused by
  name.
* **The modal deny-list has four entries and is documented as a SEED.** Four verified entries out of
  hundreds of commands is not protection; it is a mechanism plus the cases that could be made exact.
  The protection that carries weight is `confirm` + `dryRun` + the `CanExecute` gate. Saying otherwise
  would be the "assert a guarantee no test exercises" mistake PM-007 §4 names.
* **`FToolMenuContext` is default-constructed on the ToolMenus route.** Spec §9 item 8 flags that some
  menus expect specific context objects via `FindContext<T>()`, so an empty context may make
  `CanExecute` false or make a dynamic section misbehave. Not solved; the failure is reported with the
  step that failed, and the cache route is the primary one.
* **Nothing was built and the editor was not launched.** Every engine claim above is a source citation
  with a file, a line, an export macro and an access specifier; every bridge claim is one of the
  O-numbered curls, which the main session runs.

---

## Delivery-status correction — a live endpoint NAME is not a delivered catalogue entry

_Appended 2026-07-29. This file is append-only, so nothing above is rewritten; this section is the
authority where it contradicts an earlier batch verdict. It closes findings 3/6 in
`07_SELF_AUDIT_FINDINGS.md`, which the Batch K disposition table above recorded as **NOT FIXED**._

### The error

The 250-entry catalogue contains two kinds of row: **242 new endpoints** and **8 behaviour changes to
endpoints that already existed**. Delivery was being counted the same way for both — *is the endpoint
name live?* For a new endpoint that test is sound. For a behaviour-change entry it is meaningless:
**the name was already live before the catalogue was written.** The entry asks for a change to what
the handler DOES, and no amount of the name answering delivers it.

That is how `07_SELF_AUDIT_FINDINGS.md` §6 arrived at *"41 catalogue/ranked entries are live today"*.
Eight of its 29 built-ins are behaviour-change entries.

### Corrected counts

| | before (as circulated) | after (corrected) |
|---|---|---|
| "catalogue/ranked entries live today" | **41** (29 built-in names + 12 `kr_*`) | **33** by that same name-counting method (21 built-in names + 12 `kr_*`) |
| catalogue rows SHIPPED | *(never stated as a row count)* | **34** of 250 (32 unique endpoint names) |
| SHIPPED (PARTIAL) | — | **2** |
| SUPERSEDED | — | **9** |
| WITHDRAWN | — | **2** |
| STILL OPEN | — | **203** |

Row-level figures are `work/R3_REMAINING_WORK.md`, which reconciled all 250 rows line by line against
source and `self_audit` on 2026-07-28. `self_audit` remains the authority for the *live endpoint
count* (203 = 191 built-in + 12 external); it can say nothing about whether a catalogue entry is
delivered, and must not be used as if it could.

### The eight, individually

Status is R3's, re-verified against source 2026-07-29. Line numbers are 2026-07-29 positions and
drift as handlers are edited — R3 cited `connect_pins` at `MifBridgeCommon.cpp:2052` and the same
statement is now at `:3762`.

| Entry | Status | Evidence in current source |
|---|---|---|
| `connect_pins` | **STILL OPEN** | `MifBridgeCommon.cpp:3762`, in `ConnectPinsChecked` (:3738), is still `const UEdGraphSchema_K2* Schema = K2();`. That hardcoded K2 CDO drives `BreakPinLinks` (:3773-3774), `CanCreateConnection` (:3777) and `TryCreateConnection` (:3783). |
| `add_component` | **STILL OPEN** | `MifBridgeComponents.cpp:76` — `SCS->FindSCSNode(FName(*ParentName))`, own SCS only; inherited/native parent fails at `:79`. |
| `list_variables` | **STILL OPEN** | `MifBridgeIntrospect.cpp:247` iterates `NewVariables` only; `:252` hardcodes `scope` to `"member"`. |
| `rename_function` | **STILL OPEN** | no `graphType` emitted anywhere in `MifBridgeNodes2.cpp`. |
| `read_modloader_log` | **STILL OPEN** | `MifBridgePipeline.cpp:82` → `PushLine` → bare `FJsonValueString` (:19-22). File untouched since 2026-07-11. |
| `pie_status` | **SHIPPED (PARTIAL)** | readiness landed (`MifBridgePIE.cpp:93-116`); `:114` emits only `running`/`starting`/`stopped` — `travelling`/`stopping`/`simulating` are not state words. |
| `snap_actors_to_ground` | **SHIPPED (PARTIAL)** | multi-trace landed; `MifBridgeWorld.cpp:486` is `LineTraceMultiByChannel`, first blocking hit — no penetrating trace. |
| `list_components` | **IN SOURCE ONLY** | delivered by **Batch N** (above) at 2026-07-29 01:05, *after* R3 recorded it open. Batch N was source-only — **nothing was built** — so it is not in a running DLL and cannot move the SHIPPED row count yet. |

### `connect_pins` — why this one was the expensive claim

It was reported as shipped and is not. The endpoint answers, so every name-based check passed it,
and the consequence is invisible from the outside: because the K2 CDO is asked whether a connection
is legal, **a graph whose own schema is not a `UEdGraphSchema_K2` is validated by the wrong object.**
`UAnimationStateMachineSchema : public UEdGraphSchema` (`AnimGraph/Public/AnimationStateMachineSchema.h`)
is exactly that case, so **AnimGraph schema overrides still never run** and `connect_pins` cannot be
the wiring verb for `add_anim_state` / `add_anim_state_machine` / `add_anim_transition` — the largest
coherent block of open Tier-1 work with no module cost. The fix and the verified call
(`UEdGraph::GetSchema()`, `EdGraph.h:115`) are `work/R3_REMAINING_WORK.md` §4.3.

### Rule

**Never derive delivery from the endpoint registry.** `MIF_DECL`/`MIF_BIND`/`self_audit` answer *does
this name exist*. Whether an entry is delivered is a claim about a handler BODY, and only reading the
handler settles it. Same family as PM-007 §4: a guarantee that no test exercises is a guarantee that
is probably false — here, a status that no source read exercises.
