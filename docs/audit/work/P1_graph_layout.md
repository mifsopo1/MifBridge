# Axis P1 — Graph-layout plugin family + Blueprint-to-text
_Sweep date: 2026-07-26. Engine: D:/UE532 (5.3.2 fork). Agent: phase-1 breadth._

Closes Phase-4 gap #5 of docs/10_FULL_SCOPE_EXPANSION_PROMPT.md: an agent that spawns 40 nodes
leaves them heaped at the origin. This axis audits the four layout/text plugins living in
`D:/DDS2SDK/Game/Plugins/` plus the engine-native fallback, and delivers `format_graph`
(Plan A plugin-backed AND Plan B bridge-native), `fit_comment_to_nodes`, `export_graph_text`,
and negatives for the two wire/node *style* plugins.

## Surface inventory

### Plugins swept (all under D:/DDS2SDK/Game/Plugins/)

| Plugin | .uplugin module type / phase | Enabled how | Binaries | Export census (`*_API` in Source) | Linkable from MifBridge? |
|---|---|---|---|---|---|
| BlueprintAssist 4.4.9 | `BlueprintAssist`, Type `EditorNoCommandlet`, LoadingPhase `Default`, TargetAllowList Editor | project plugin, NOT in .uproject → enabled by project-plugin default rule (see below) | `Binaries/Win64/UnrealEditor-BlueprintAssist.dll` + import lib `Intermediate/Build/Win64/x64/UnrealEditor/Development/BlueprintAssist/UnrealEditor-BlueprintAssist.lib` | **78** `BLUEPRINTASSIST_API` across 68 Public headers | **YES** — unusually well exported for a marketplace plugin (FBAGraphHandler, FBATabHandler, FBACache, FBAUtils, UBASettings all exported) |
| AutoSizeComments 3.4.3 | `AutoSizeComments`, Type `EditorNoCommandlet`, LoadingPhase `Default`, TargetAllowList Editor, `EnabledByDefault: true` | explicitly enabled in .uproject | dll + .lib present | **9** `AUTOSIZECOMMENTS_API` across 13 Public headers (settings, cache, commands, factory, input processor ONLY) | **Partial** — settings + cache linkable; ALL resize logic unexported |
| ElectronicNodes 3.11 | `ElectronicNodes`, Type `EditorNoCommandlet`, LoadingPhase `Default` | explicitly enabled in .uproject | dll present | **2** (`UElectronicNodesSettings` only; all policy classes in Private/) | Settings only — nothing worth calling |
| FlatNodes 1.1 | `FlatNodes`, Type `Editor`, LoadingPhase `Default`, `EnabledByDefault: true` | project plugin, not in .uproject → default rule | dll present | **0** — exports nothing at all | **NO** |
| NS_BlueprintToText 1.0 | `NS_BlueprintToText`, Type `Editor`, LoadingPhase `Default`, `EnabledByDefault: true`, Win64 only | project plugin, not in .uproject → default rule | dll present | **1** (`UNS_BlueprintToTextSettings` only) | Settings only — all export logic is private module methods |

Project-plugin default-enable rule, verified: `FPlugin::IsEnabledByDefault` returns
`GetLoadedFrom() == EPluginLoadedFrom::Project` when the descriptor has no `EnabledByDefault`
field — `Runtime/Projects/Private/PluginManager.cpp:409-423`. So BlueprintAssist, FlatNodes and
NS_BlueprintToText are all active despite not appearing in the .uproject.

**Live confirmation** (read-only curls against the running bridge, 2026-07-26):
`describe_class {"class":"BASettings"}` → `"path":"/Script/BlueprintAssist.BASettings"` and
`describe_class {"class":"AutoSizeCommentsSettings"}` → `"path":"/Script/AutoSizeComments.AutoSizeCommentsSettings"`.
Both modules are loaded in the current editor session. `self_audit` → `endpointCount: 160`.

### Files read (proof of coverage)

- BlueprintAssist: `Public/BlueprintAssistGraphHandler.h` (full), `Public/BlueprintAssistTabHandler.h` (full),
  `Public/BlueprintAssistCache.h` (full), `Public/BlueprintAssistSettings.h` (100-225, 404),
  `Public/BlueprintAssistFormatters/FormatterInterface.h` (full), `Public/BlueprintAssistFormatters/EdGraphFormatter.h` (1-120),
  `Public/BlueprintAssistFormatters/GraphFormatterTypes.h` (22-47), `Public/BlueprintAssistUtils.h` (selected),
  `Private/BlueprintAssistGraphHandler.cpp` (Tick 439-489, MakeFormatter 1763-1791, AddPendingFormatNodes 2325-2348,
  GetCachedNodeBounds 2203-2233, UpdateCachedNodeSize 2401-2431, UpdateNodesRequiringFormatting 2639-2684,
  SimpleFormatAll 2765-2805, SmartFormatAll 2856-2896, FormatAllEvents 3233-3352, FormatNodes 3406-3451, CacheNodeSize 3514-3544),
  `Private/BlueprintAssistInputProcessor.cpp:99`, `Private/BlueprintAssistModule.cpp` (ticker/PostEngineInit region).
- AutoSizeComments: `Public/AutoSizeCommentsGraphHandler.h` (full), `Public/AutoSizeCommentsUtils.h` (full),
  `Public/AutoSizeCommentsCacheFile.h` (10-109), `Public/AutoSizeCommentsGraphNode.h` (class decl + 112-113, 233-244),
  `Public/AutoSizeCommentsSettings.h` (109-110, 195, 398-400).
- ElectronicNodes: `Public/ElectronicNodesSettings.h:56`, `Private/ENConnectionDrawingPolicy.h` (14-48), Private tree listing.
- FlatNodes: `Public/FlatNodes.h`, `Public/FlatNodesSettings.h`, `Private/FlatNodes.cpp:32`.
- NS_BlueprintToText: `Public/NS_BlueprintToText.h` (full), `Public/NS_BlueprintToTextSettings.h` (full),
  `Private/BlueprintCommentTranslator.h` (full), `Private/NS_BlueprintToText.cpp` (179-233, 251-380, 579-627, 658-712).
- Engine: `Editor/UnrealEd/Public/EdGraphUtilities.h` (55-147), `Editor/UnrealEd/Private/EdGraphUtilities.cpp` (505-539),
  `Editor/UnrealEd/Public/EdGraphNode_Comment.h` (23-122), `Editor/UnrealEd/Public/Subsystems/AssetEditorSubsystem.h` (92-138),
  `Editor/UnrealEd/Public/Kismet2/KismetEditorUtilities.h:442`, `Editor/UnrealEd/Private/Kismet2/Kismet2.cpp:2439-2447`,
  `Editor/Kismet/Private/BlueprintEditor.cpp:4337+`, `Runtime/Engine/Classes/EdGraph/EdGraphNode.h` (278-298, 393),
  `Runtime/Engine/Classes/EdGraph/EdGraphPin.h` (296, 302, 378), `Editor/BlueprintGraph/Classes/K2Node.h` (200-241),
  `Editor/BlueprintGraph/Classes/K2Node_Event.h` (37-41), `Editor/BlueprintGraph/Classes/EdGraphSchema_K2.h` (348-354),
  `Editor/BehaviorTreeEditor/Classes/BehaviorTreeGraph.h` (18-64), `Editor/BehaviorTreeEditor/Private/BehaviorTreeGraph.cpp` (1279-1304),
  `Runtime/Projects/Private/PluginManager.cpp` (409-423).
- MifBridge (existing endpoints composed with): `Private/MifBridgeIntrospect.cpp` (H_open_blueprint 34-65, H_list_nodes 213-238),
  `Private/MifBridgeNodes.cpp` (H_move_node 678-693), `Private/MifBridgeNodes4.cpp` (H_add_comment 316-337).

### The headless question, answered from source (BlueprintAssist)

The formatting pipeline is: `AddPendingFormatNodes`/`FormatAllEvents` → (tick) node-size caching →
(tick) `UpdateNodesRequiringFormatting` → `FormatNodes` → `FEdGraphFormatter`. Verdict on each stage:

1. `FBAGraphHandler` can only be constructed against live Slate:
   `FBAGraphHandler(TWeakPtr<SDockTab> InTab, TWeakPtr<SGraphEditor> InGraphEditor);`
   (`BlueprintAssistGraphHandler.h:28`). There is no UEdGraph-only constructor.
2. `FormatNodes` hard-refuses without a panel: `if (!GetGraphPanel().IsValid()) { return nullptr; }`
   (`BlueprintAssistGraphHandler.cpp:3410-3413`).
3. Node sizes come from Slate: `FVector2D Size = GraphNode->GetDesiredSize();`
   (`BlueprintAssistGraphHandler.cpp:3522`, inside `CacheNodeSize`), with a persisted per-GUID cache
   (`FBANodeData::GetNodeSize`, `BlueprintAssistCache.h:58`) and a hard fallback `FIntPoint Size(300, 150);`
   (`BlueprintAssistGraphHandler.cpp:2212`). Freshly spawned nodes have no cache entry and are measured
   by the panel over multiple ticks (`UpdateCachedNodeSize`, cpp:2401+, early-outs without GraphEditor/GraphPanel).
4. The low-level formatter `class FEdGraphFormatter final : public FFormatterInterface`
   (`EdGraphFormatter.h:53`) has **no export macro** and takes a `TSharedPtr<FBAGraphHandler>` anyway.

**Honest verdict: headless BlueprintAssist formatting on a bare UEdGraph is impossible.** The viable
Plan A is "format through a real open editor": open the Blueprint editor, let BA attach, kick its
public exported entry points, poll. That works because everything needed IS exported:
`FBATabHandler::Get()` (`BlueprintAssistTabHandler.h:18,21`), `GetAllGraphHandlers()` (:34),
`FBAGraphHandler::FormatAllEvents()` (h:137), `AddPendingFormatNodes(...)` (h:63-66),
`IsCalculatingNodeSize()` (h:129), `GetNumberOfPendingNodesToCache()` (h:143),
`GetPendingNodeSizeProgress()` (h:145), `HasActiveTransaction()` (h:157), `GetFocusedEdGraph()` (h:109).
Ticking is automatic: `FBATabHandler::Get().Tick(DeltaTime)` runs from BA's Slate input processor
(`BlueprintAssistInputProcessor.cpp:99`), and `FBAGraphHandler::Tick` drives `UpdateCachedNodeSize(DeltaTime)`
and `UpdateNodesRequiringFormatting()` (`BlueprintAssistGraphHandler.cpp:483,489`) every editor frame.

Note: no plugin in this family registers a console command (`grep FAutoConsoleCommand|RegisterConsoleCommand|IConsoleManager`
over all five Source trees: zero hits), so there is no run_console route to any of this; C++ linkage is the only route.

---

## Proposed endpoints

### format_graph
**Purpose**: Deterministic, headless, dependency-free layered-DAG layout ("Sugiyama-lite") of a K2
graph — fixes "40 spawned nodes heaped at origin" without any editor window, on any graph MifBridge
can already author. This is Plan B and the default implementation; it should be built first.
**Engine API** (all already-linked modules; the layout math is bridge code over these primitives):
```cpp
TArray<UEdGraphPin*> Pins;                                       // EdGraphNode.h:278
int32 NodePosX;                                                  // EdGraphNode.h:286
int32 NodePosY;                                                  // EdGraphNode.h:290
int32 NodeWidth;                                                 // EdGraphNode.h:294  (comments/resizable only)
int32 NodeHeight;                                                // EdGraphNode.h:298
FGuid NodeGuid;                                                  // EdGraphNode.h:393
TArray<UEdGraphPin*> LinkedTo;                                   // EdGraphPin.h:378
TEnumAsByte<enum EEdGraphPinDirection> Direction;                // EdGraphPin.h:302
virtual bool IsNodePure() const { return false; }                // K2Node.h:241 (inline — callable on MinimalAPI class)
static const FName PC_Exec;                                      // EdGraphSchema_K2.h:354
static UNREALED_API FIntRect CalculateApproximateNodeBoundaries(const TArray<UEdGraphNode*>& Nodes); // EdGraphUtilities.h:130
```
Files relative to D:/UE532/Engine/Source: `Runtime/Engine/Classes/EdGraph/EdGraphNode.h`,
`Runtime/Engine/Classes/EdGraph/EdGraphPin.h`, `Editor/BlueprintGraph/Classes/K2Node.h`,
`Editor/BlueprintGraph/Classes/EdGraphSchema_K2.h`, `Editor/UnrealEd/Public/EdGraphUtilities.h`.
**Export**: `UEdGraphNode`/`UEdGraphPin` are ENGINE_API-exported core types already used by 40+
existing handlers; `UEdGraphSchema_K2` is `class BLUEPRINTGRAPH_API UEdGraphSchema_K2 : public UEdGraphSchema`
(`EdGraphSchema_K2.h:349`); `UK2Node` is `UCLASS(abstract, MinimalAPI)` (`K2Node.h:200-201`) — `Cast<UK2Node>`
and the inline `IsNodePure()` are fine (StaticClass is exported by MinimalAPI; the virtual is defined
in the header). `FEdGraphUtilities::CalculateApproximateNodeBoundaries` is method-level UNREALED_API
(class itself unexported, `EdGraphUtilities.h:55` — statics carry their own macro).
| **Module**: none — Engine, BlueprintGraph, UnrealEd already in MifBridge.Build.cs. | **Guards**: none beyond the module being editor-only.
**Bucket**: `transacted` — position-only writes (`Node->Modify(); NodePosX/Y = …`), exactly the
existing `move_node` pattern (`MifBridgeNodes.cpp:678-693`) at N-node scale; no compile, no
reinstancing, fully undoable as one blanket transaction.
**Async**: no — pure math on the game thread; a 500-node graph is microseconds of BFS + sorting.
**Algorithm (spec, so the implementer needn't design it)**:
1. Resolve graph via existing `ResolveGraphField`. Collect `Graph->Nodes`, skip `UEdGraphNode_Comment` and `UK2Node_Knot` (both already special-cased by `list_nodes`).
2. Roots = nodes with an output exec pin (`PinType.PinCategory == UEdGraphSchema_K2::PC_Exec`, `Direction == EGPD_Output`) and no linked input exec pin — events/timelines/entry nodes; for pure-only graphs fall back to nodes with no linked input pins.
3. Layer assignment: BFS along exec links (output→input). `layer(n) = max(layer(pred)+1)`. Impure nodes not reached (orphans) go to layer 0 of a separate island; islands stack vertically.
4. Pure-node placement: a pure node feeding node n via data pins is placed at `layer(n) - 1` (recursively), offset below the consumer row — mirrors BA's "parameter formatter" concept without measuring widgets.
5. Size estimation (needed because non-resizable nodes carry no size): `estWidth = clamp(180 + 7*titleLen, 220, 480)`, `estHeight = 48 + 22*max(numInPins, numOutPins)`. Constants sanity-anchored to the two in-engine/in-plugin precedents: engine average 200x128 (`EdGraphUtilities.cpp:525-526`) and BA fallback 300x150 (`BlueprintAssistGraphHandler.cpp:2212`). If the BlueprintAssist module dependency from format_graph_ba_request is compiled in, read real measured sizes first: `FBACache::Get().GetGraphData(Graph)` (`BlueprintAssistCache.h:117-137`) → `GetNodeDataPtr(Node)` (:88) → `HasSize()/GetNodeSize()` (:47,58) and fall back to the estimate only when absent.
6. X per layer = running max column width + `spacingX`; Y within layer = stable order by (existing Y, then GUID) with `spacingY` gaps. Optionally keep node `nodeToKeepStill` at its original position, translating the whole result.
7. Write positions; return per-node `{nodeGuid, oldX, oldY, x, y}` plus post-layout bounds from `CalculateApproximateNodeBoundaries` and an `overlapCount` (estimated-rect pair overlaps, should be 0).
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| graphId | graph | string `<bpPath>::<graphName>` | — | yes (strict; empty ⇒ error naming graphId) |
| scope | — | string: `all` \| `from_node` | `all` | no |
| nodeGuid | node | string GUID (root for `from_node`) | — | only when scope=from_node |
| spacingX | spacing_x | int (px between layers) | 320 | no |
| spacingY | spacing_y | int (px between rows) | 80 | no |
| keepStillGuid | keep_still | string GUID | none | no |
| sizeSource | — | string: `auto` \| `ba_cache` \| `estimate` | `auto` | no (`ba_cache` errors if BA dep not compiled in) |
Unrecognised parameter ⇒ `{"ok":false,"error":"unknown parameter 'spacingZ' — accepted: graphId, scope, nodeGuid, spacingX, spacingY, keepStillGuid, sizeSource"}`.
**Failure modes**:
- graph not found ⇒ `graphId 'X' not found — use open_blueprint/list_graphs to enumerate` (existing ResolveGraphField behaviour).
- scope=from_node with missing/unknown nodeGuid ⇒ `nodeGuid required when scope=from_node` / `nodeGuid 'X' not in graph 'Y'`.
- graph has 0 layout-able nodes (only comments/knots) ⇒ ok:true, `moved:0`, note field.
- cyclic exec links (possible via reroutes) ⇒ break cycles at back-edges (BFS visited set), report `cyclesBroken:N` — never hang.
**Cooked**: cooked .pak Blueprints have stripped graphs (0 nodes) ⇒ behaves as "0 layout-able nodes"; works fully on loose assets and on kr_*-reconstructed Blueprints — the primary customer, since the reconstructor emits position-less nodes.
**Verify**: `list_nodes` before/after: (a) every returned node has the returned x/y; (b) `overlapCount == 0`; (c) min pair-distance between estimated rects ≥ min(spacingX, spacingY) − 1; (d) undo (Ctrl-Z semantics via transaction) restores all old positions — re-run list_nodes and diff. All numeric.
**Score**: U5 E3 R4 → tier 0 — directly closes gap #5 with zero new dependencies.
**Phase-2 verdict**: CORRECTED — export-claim wording only: `UEdGraphNode` is `UCLASS(MinimalAPI)`
(EdGraphNode.h:272-273), NOT "ENGINE_API-exported", and `UEdGraphPin` is a plain unexported class
(EdGraphPin.h:283). Capability unaffected: NodePosX/Y etc. are public data members (no symbol needed),
`UObject::Modify` is COREUOBJECT_API, and existing `move_node` (MifBridgeNodes.cpp:678-693, re-read)
already links this exact write path. All other cites re-verified exact: Pins :278, NodePosX :286,
NodePosY :290, NodeWidth :294, NodeHeight :298, NodeGuid :393; LinkedTo EdGraphPin.h:378, Direction :302;
`UCLASS(abstract, MinimalAPI)` UK2Node + inline IsNodePure K2Node.h:199-241; `class BLUEPRINTGRAPH_API
UEdGraphSchema_K2` :349 + PC_Exec :354; CalculateApproximateNodeBoundaries UNREALED_API static
EdGraphUtilities.h:130, impl 200x128 margin confirmed (EdGraphUtilities.cpp:505-529 — note the margin is
added to the max corner only).

### format_graph_ba_request
**Purpose**: Pixel-quality formatting identical to a human pressing BlueprintAssist's Format-All —
real measured node sizes, comment handling, optional knot tracks — for graphs an agent is about to
hand back to a human. Plan A; requires the editor UI to open the Blueprint (BA is Slate-coupled by
design, see Surface inventory).
**Engine API**:
```cpp
// open + focus the target graph (UnrealEd, already linked)
static UNREALED_API void BringKismetToFocusAttentionOnObject(const UObject* ObjectToFocusOn, bool bRequestRename=false);
// Editor/UnrealEd/Public/Kismet2/KismetEditorUtilities.h:442
// (impl opens the BP editor when closed: GetIBlueprintEditorForObject(obj, true) — Kismet2.cpp:2439-2447;
//  JumpToHyperlink has an explicit UEdGraph branch — Editor/Kismet/Private/BlueprintEditor.cpp:4337+)

// BlueprintAssist exported surface (plugin headers, relative to the plugin's Source/BlueprintAssist/Public)
class BLUEPRINTASSIST_API FBATabHandler                     // BlueprintAssistTabHandler.h:18
	static FBATabHandler& Get();                            // :21
	TSharedPtr<FBAGraphHandler> GetActiveGraphHandler();    // :32
	TArray<TSharedPtr<FBAGraphHandler>> GetAllGraphHandlers(); // :34
	void ProcessTab(TSharedPtr<SDockTab> Tab);              // :40
class BLUEPRINTASSIST_API FBAGraphHandler                   // BlueprintAssistGraphHandler.h:20
	void AddPendingFormatNodes(UEdGraphNode* Node, TSharedPtr<FScopedTransaction> PendingTransaction = ..., FEdGraphFormatterParameters FormatterParameters = ...); // :63-66
	void FormatAllEvents();                                 // :137
	UEdGraph* GetFocusedEdGraph();                          // :109
	bool IsCalculatingNodeSize() const;                     // :129
	int32 GetNumberOfPendingNodesToCache() const;           // :143
	float GetPendingNodeSizeProgress() const;               // :145
	bool HasActiveTransaction() const;                      // :157
struct BLUEPRINTASSIST_API FBAUtils                         // BlueprintAssistUtils.h:51
	static TSet<UEdGraphNode*> GetNodeTree(UEdGraphNode* Node, EEdGraphPinDirection Direction = EGPD_MAX, bool bOnlyInitialDirection = false); // :254
class BLUEPRINTASSIST_API UBASettings final : public UBASettingsBase // BlueprintAssistSettings.h:133
	static UBASettings& GetMutable();                       // :144-147
	bool bCreateKnotNodes;                                  // :215 (UPROPERTY config)
```
**Export**: all `BLUEPRINTASSIST_API` verbatim above; import lib exists (`Intermediate/.../UnrealEditor-BlueprintAssist.lib`).
| **Module**: **NEW dependency** `"BlueprintAssist"` in MifBridge.Build.cs PrivateDependencyModuleNames
+ `"Plugins": [{"Name":"BlueprintAssist","Enabled":true}]` in MifBridge.uplugin. Plugin is editor-only
(`EditorNoCommandlet`), project-local, enabled (default rule + live-confirmed loaded). COST FLAG: this
couples MifBridge to a marketplace plugin; guard with a Build.cs directory-existence check defining
`WITH_BLUEPRINT_ASSIST=1` and `#if` the handler body so MifBridge still builds if BA is removed
(endpoint then returns `error: BlueprintAssist not compiled in — use format_graph`).
| **Guards**: `WITH_BLUEPRINT_ASSIST` (bridge-defined), nothing engine-side.
**Bucket**: `self-managed` — MUST NOT run under the blanket RunEndpoint transaction:
`FormatAllEvents` opens its **own** `FScopedTransaction` ("Format All Nodes",
`BlueprintAssistGraphHandler.cpp:3350`) which stays outstanding across multiple frames until
formatting completes; wrapping it would nest a multi-frame transaction inside a same-frame one.
The request handler itself performs no mutation — BA's tick does, inside BA's transaction.
**Async**: request + poll, mandatory. Formatting is inherently multi-frame (node-size measurement
zooms the graph panel per node, `UpdateCachedNodeSize` cpp:2401+; formatting runs later in
`UpdateNodesRequiringFormatting`, driven every frame from `BlueprintAssistInputProcessor.cpp:99`).
Job design: request (1) resolves graph, (2) snapshots all `{NodeGuid, NodePosX, NodePosY}` bridge-side,
(3) calls `BringKismetToFocusAttentionOnObject(Graph)`, (4) registers an FTSTicker job: each tick,
locate handler via `FBATabHandler::Get().GetAllGraphHandlers()` matching `GetFocusedEdGraph() == Graph`
(also try `ProcessTab(GetLastMajorTab())` on the first ticks); once found → call `FormatAllEvents()`
(scope=all) or `AddPendingFormatNodes(root)` (scope=from_node) exactly once → phase=formatting;
done when `!IsCalculatingNodeSize() && !HasActiveTransaction()`. Timeout param aborts the job status
(BA itself shows a size-timeout notification; we never block the HTTP thread).
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| graphId | graph | string | — | yes (strict) |
| scope | — | `all` \| `from_node` | `all` | no |
| nodeGuid | node | string GUID | — | when scope=from_node |
| createKnots | create_knots | bool — sets `UBASettings::GetMutable().bCreateKnotNodes` for the job, restores after | current setting | no |
| timeoutSec | timeout | number | 30 | no |
Unrecognised parameter ⇒ error naming it. Returns `{jobId}`.
(Other BA knobs need NO parameters: `UBASettings` is `UCLASS(config)` — already reachable today via
existing `set_property` on objectPath `/Script/BlueprintAssist.Default__BASettings`.)
**Failure modes**:
- BA not compiled in ⇒ `BlueprintAssist support not compiled into MifBridge — use format_graph (plan B)`.
- editor never produces a graph handler within timeout (headless commandlet, tab failed to open) ⇒ status `failed: BlueprintAssist did not attach to '<graphId>' within <timeoutSec>s — is the editor UI running?`.
- graph read-only (`FBlueprintEditorUtils::IsGraphReadOnly` check inside `FormatNodes`, cpp:3426) ⇒ BA silently returns null formatters; detect via zero moved nodes and report `warning: graph is read-only`.
- a second format job while one is active ⇒ `error: format job <id> still running — poll format_graph_ba_status`.
**Cooked**: refuses usefully — cooked-stripped graphs have no nodes; reconstructed (kr_*) BPs work since they are real loose UBlueprints.
**Verify**: via format_graph_ba_status (below): `movedNodes` count vs snapshot, final bounds; then `list_nodes` — every event root at a distinct Y, zero coordinate pairs duplicated. If createKnots=true, `list_nodes hideKnots=false` shows new `UK2Node_Knot` count ≥ 0 (report it — topology change is intentional and visible).
**Score**: U4 E3 R2 → tier 2 — high polish, but new plugin dependency + UI-required + multi-frame job.
**Phase-2 verdict**: CONFIRMED — every BA export re-verified verbatim in plugin headers: FBATabHandler
h:18/21/32/34/40 (+ ProcessTabs timer :51/:72), FBAGraphHandler h:20/28/63-66/109/129/137/143/145/157,
UBASettings :133/:144-147/:215, FBAUtils :51/:254; import lib present (864 KB, Jul 25 build).
Cpp claims re-read: FScopedTransaction "Format All Nodes" :3350; FormatNodes panel early-out :3410-3413;
IsGraphReadOnly null-return :3426; tick chain InputProcessor.cpp:99 → TabHandler → GraphHandler
Tick :483/:489. BringKismetToFocusAttentionOnObject UNREALED_API KismetEditorUtilities.h:442, impl
GetIBlueprintEditorForObject(obj, /*bOpenEditor*/true) Kismet2.cpp:2439-2447; JumpToHyperlink's
`Cast<const UEdGraph>` branch confirmed (BlueprintEditor.cpp:4337+, OpenDocument path). Hazard grep of
BlueprintAssistGraphHandler.cpp + TabHandler.cpp: zero MakeDialog/FMessageDialog/FScopedSlowTask/Flush/
Wait/CollectGarbage hits — no hidden modal or blocking calls. Self-managed bucket + request/poll design
consistent with invariants 2/3. Live re-check this pass: describe_class BASettings still resolves
(module loaded); bridge now serves 165 endpoints — no name collision with format_graph*.

### format_graph_ba_status
**Purpose**: Poll the Plan-A job; read-only completion + progress numbers (invariant 3 pair).
**Engine API**: the four exported status getters cited above (`IsCalculatingNodeSize` h:129,
`GetNumberOfPendingNodesToCache` h:143, `GetPendingNodeSizeProgress` h:145, `HasActiveTransaction` h:157)
plus bridge-side snapshot diff. Deliberately NOT using `GetFormattingChangeData()` (h:184): its value
type `class FBAFormattingChangeData` (`BlueprintAssistNodeSizeChangeData.h:77`) has **no export macro** —
calling its methods would not link. Position diffing against the request-time snapshot is dependency-free.
**Export**: as above | **Module**: same BlueprintAssist dep (compiled together) | **Guards**: `WITH_BLUEPRINT_ASSIST`
**Bucket**: `read-only` — pure query of job state + node positions.
**Async**: this IS the poll half.
**Params**: | jobId | job | string | — | yes (strict) | — unrecognised ⇒ error.
**Failure modes**: unknown jobId ⇒ `unknown jobId 'X' — jobs are not persisted across editor restarts`.
**Cooked**: n/a (mirrors request).
**Verify**: payload is the proof: `{phase: attaching|sizing|formatting|done|failed, pendingSizeNodes, sizeProgress (0-1), transactionOpen, movedNodes, bounds:{minX,minY,maxX,maxY}, knotsCreated}`. Terminal phase within timeoutSec.
**Score**: U4 E4 R5 → tier 2 (pairs with request).
**Phase-2 verdict**: CONFIRMED — four status getters re-verified at h:129/:143/:145/:157 (IsCalculatingNodeSize
is inline `PendingSize.Num() > 0`); GetFormattingChangeData h:184 returns `TMap<FGuid, FBAFormattingChangeData>`
and `class FBAFormattingChangeData : public FBANodeSizeChangeData` (BlueprintAssistNodeSizeChangeData.h:77)
carries no export macro — the decision to snapshot-diff bridge-side instead is correct and necessary.

### fit_comment_to_nodes
**Purpose**: Make a comment box actually contain a set of nodes — compute the union rect, set bounds
+ membership, so comments made by agents behave like human comments (drag-with, ASC-tracked). Delta
over existing `add_comment` (which only writes x/y/width/height blindly, `MifBridgeNodes4.cpp:316-337`).
**Engine API**:
```cpp
UCLASS(MinimalAPI)
class UEdGraphNode_Comment : public UEdGraphNode            // Editor/UnrealEd/Public/EdGraphNode_Comment.h:44-45
UNREALED_API void	AddNodeUnderComment(UObject* Object);     // :102
UNREALED_API void	ClearNodesUnderComment();                 // :105
UNREALED_API void SetBounds(const class FSlateRect& Rect);  // :108
UNREALED_API const FCommentNodeSet& GetNodesUnderComment() const; // :111
static UNREALED_API FIntRect CalculateApproximateNodeBoundaries(const TArray<UEdGraphNode*>& Nodes); // EdGraphUtilities.h:130
// impl: union of NodePosX/Y..(+NodeWidth/Height), then +200x128 average margin — EdGraphUtilities.cpp:505-529
```
Optional ASC cache sync (keeps AutoSizeComments tracking the membership when a human opens the graph):
```cpp
class AUTOSIZECOMMENTS_API FAutoSizeCommentsCacheFile       // AutoSizeCommentsCacheFile.h:77
	static FAutoSizeCommentsCacheFile& Get();               // :80
	void UpdateNodesUnderComment(UEdGraphNode_Comment* Comment) { GetCommentData(Comment).UpdateNodesUnderComment(Comment); } // :103
struct AUTOSIZECOMMENTS_API FASCCommentData { TArray<FGuid> NodeGuids; ... void UpdateNodesUnderComment(UEdGraphNode_Comment* Comment); } // :13,19,27
```
**Export**: `UEdGraphNode_Comment` is MinimalAPI with method-level UNREALED_API on exactly the four
methods needed (verbatim above) — links from MifBridge today. ASC classes `AUTOSIZECOMMENTS_API`.
| **Module**: core path none (UnrealEd linked; `add_comment` already constructs `UEdGraphNode_Comment`).
ASC sync = **NEW optional dependency** `"AutoSizeComments"` (project-enabled explicitly in .uproject,
.lib exists) guarded `WITH_AUTO_SIZE_COMMENTS`; without it the endpoint still works, ASC will simply
re-derive membership visually when the graph is opened. | **Guards**: bridge-defined `WITH_AUTO_SIZE_COMMENTS` for the sync line only.
**Bucket**: `transacted` — property writes on one node (bounds + membership array), no compile.
**Async**: no.
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| graphId | graph | string | — | yes (strict) |
| commentGuid | comment | string GUID of existing UEdGraphNode_Comment | — | yes unless create=true |
| create | — | bool — create a new comment instead | false | no |
| text | comment_text | string (create only) | "" | no |
| nodeGuids | nodes | array of GUID strings | — | yes; empty array ⇒ error naming nodeGuids |
| paddingX | padding_x | int | 60 | no |
| paddingY | padding_y | int | 60 (plus fixed +36 title-bar headroom at top) | no |
| sizeSource | — | `auto` \| `ba_cache` \| `estimate` | `auto` | no (same resolution as format_graph) |
Unrecognised ⇒ error naming the parameter.
**Failure modes**:
- commentGuid resolves to non-comment node ⇒ `node 'X' is a K2Node_CallFunction, not a UEdGraphNode_Comment`.
- any nodeGuid not in the same graph ⇒ `nodeGuid 'X' not found in graph 'Y' — all nodes must be in the comment's graph`.
- both commentGuid and create=true ⇒ `pass either commentGuid or create=true, not both`.
- comment node listed in its own nodeGuids ⇒ error (self-containment).
**Cooked**: same as all graph mutation — loose/reconstructed assets only; stripped graphs have nothing to fit around.
**Verify**: response returns `{x, y, width, height, contained:N}`; then (a) `get_node` on the comment shows the same NodePosX/Y/NodeWidth/NodeHeight; (b) for every input node: `NodePosX >= x && NodePosY >= y && NodePosX + estWidth <= x + width && …` — checked bridge-side and returned as `allInside:true`; (c) `list_object_properties` on the comment shows `NodesUnderComment` length == N.
**Score**: U3 E4 R4 → tier 1 — small, composes with format_graph (format first, then fit comments).
**Phase-2 verdict**: CORRECTED — `NodesUnderComment` is a PRIVATE, NON-UPROPERTY member
(`TArray<TObjectPtr<class UObject>> NodesUnderComment;` under `private:`, EdGraphNode_Comment.h:119-122;
UPROPERTY grep of the header confirms none attaches to it). Two consequences: (a) Verify step (c) is
impossible — `list_object_properties` cannot see a non-UPROPERTY member; verify membership instead via
the exported `GetNodesUnderComment().Num()` echoed as `contained:N` in the response (and via the ASC
cache GUID list when the sync is compiled in); (b) membership is neither asset-serialized nor captured
by the transaction snapshot — it is session-transient, and the ASC cache sync is the ONLY persistence,
which upgrades that "optional" dependency to strongly-recommended. Bounds writes (SetBounds →
NodePosX/Y/NodeWidth/NodeHeight) persist normally. All four method-level UNREALED_API cites exact
(:102/:105/:108/:111 on UCLASS(MinimalAPI) :44-45); ASC exports exact (FASCCommentData :13/:19/:27,
FAutoSizeCommentsCacheFile :77/:80/:103, .lib present).

### export_graph_text
**Purpose**: Lossless T3D (clipboard-format) serialization of a graph or node subset — exact pin
defaults, object literals, positions, node classes — for diffing, external analysis, backup, and
verifying kr_* reconstruction fidelity. Strictly richer than `list_nodes` (which is a summarizing
JSON) and strictly better than NS_BlueprintToText's lossy name-digest (see negatives). EXPORT ONLY:
the import half (`ImportNodesFromText`, `EdGraphUtilities.h:119`) is the documented copy/paste
non-goal and is NOT proposed.
**Engine API**:
```cpp
static UNREALED_API void ExportNodesToText(TSet<UObject*> NodesToExport, /*out*/ FString& ExportedText); // Editor/UnrealEd/Public/EdGraphUtilities.h:110
```
**Export**: method-level `UNREALED_API` (class `FEdGraphUtilities` at :55 is unexported; the static
carries its own macro). | **Module**: none — UnrealEd already linked. | **Guards**: none.
**Bucket**: `read-only` — serialization only; no object mutation (exporter walks the objects const-ly).
**Async**: no — single-frame even for large graphs (it is the Ctrl-C path).
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| graphId | graph | string | — | yes (strict) |
| nodeGuids | nodes | array of GUIDs — subset; omitted = whole graph | all nodes | no |
| maxBytes | max_bytes | int cap on returned text | 2000000 | no |
Unrecognised ⇒ error naming it.
**Failure modes**: nodeGuid not in graph ⇒ error naming the GUID; output over maxBytes ⇒ `ok:false, error:"export is <n> bytes, exceeds maxBytes=<cap> — pass nodeGuids to subset or raise maxBytes"` (never silently truncate a serialization).
**Cooked**: stripped graphs export an empty node list — return `nodes:0, text:""` with a `cookedStripped:true` note; full fidelity on loose/reconstructed assets.
**Verify**: numbers: response `{nodes:N, bytes:B, text}`; N == list_nodes count (same filters); text contains exactly N occurrences of `Begin Object` at top level; re-running is byte-identical (deterministic) unless the graph changed.
**Score**: U3 E5 R5 → tier 1 — one engine call, read-only, immediately useful for kr_* drift checks.
**Phase-2 verdict**: CORRECTED — hidden CRASH hazard in the impl: `ExportNodesToText`
(EdGraphUtilities.cpp:458-481) contains `check((LastOuter == ThisOuter) || (LastOuter == NULL));` —
passing nodes with different outers is a hard check() crash, not an error. The handler's same-graph
validation of every nodeGuid is therefore LOAD-BEARING and must run before the engine call (the
existing failure-mode line covers it, but it must be implemented as a pre-pass, never trusted to the
engine). Signature at EdGraphUtilities.h:110 verbatim UNREALED_API static ✓. Impl also calls
`UnMarkAllObjects(OBJECTMARK_TagExp|OBJECTMARK_TagImp)` — transient global annotation only, read-only
bucket stands. Per-node output via UExporter::ExportToOutputDevice with PPF_Copy — "Begin Object"
count check valid at top-level indentation only, as specified.

---

## Negative results / gaps (for 03_GAPS_AND_RISKS.md)

_Phase-2 spot-verification of ALL negatives (2026-07-26, this pass): #1 FBAGraphHandler ctor h:28 +
panel early-out cpp:3410-3413 + GetDesiredSize cpp:3522 + 300x150 fallback cpp:2212 re-read — stands.
#2 `class FEdGraphFormatter final` unexported (EdGraphFormatter.h:53) — stands. #3 EN policy classes
confirmed Private-only, `ELECTRONICNODES_API UElectronicNodesSettings` :56 — stands. #4 FlatNodes
FAppStyle const-cast (FlatNodes.cpp:32), unexported UFlatNodesSettings (:14), export census 0 — stands.
#5 SAutoSizeCommentsGraphNode :37 / ResizeToFit :112 unexported Slate widget; FAutoSizeCommentGraphHandler
:27 and FASCUtils :14 both export-macro-free — stands (settings/cache reflection route noted, correct).
#6 ProcessBlueprint on unexported module class (NS_BlueprintToText.h:8,23) + Ollama HTTP translator
(BlueprintCommentTranslator.h — IHttpRequest + ParseOllamaResponse :60) — stands. #7 BT AutoArrange
BEHAVIORTREEEDITOR_API (BehaviorTreeGraph.h:19,:64) but impl derefs `DEPRECATED_NodeWidget.Pin()->`
unchecked (BehaviorTreeGraph.cpp:1296-1303) — stands. #8 ImportNodesFromText exclusion — policy, stands.
Console-command absence re-grepped across all five plugin Source trees: 0 hits. Export censuses
recounted: BA 78 macros / 68 public headers exact; ASC 9. BlueprintAssist/FlatNodes/NS_BlueprintToText
absent from .uproject re-confirmed; project-plugin default-enable rule re-read (PluginManager.cpp:409-423)._

1. **BlueprintAssist cannot format headlessly — SGraphPanel is load-bearing.**
   `FBAGraphHandler` is constructible only from `(TWeakPtr<SDockTab>, TWeakPtr<SGraphEditor>)`
   (`BlueprintAssistGraphHandler.h:28`); `FormatNodes` early-outs on `!GetGraphPanel().IsValid()`
   (`BlueprintAssistGraphHandler.cpp:3410-3413`); node sizes come from `SGraphNode::GetDesiredSize()`
   (cpp:3522) with only a per-GUID disk cache (useless for freshly spawned nodes) and a blind
   300x150 fallback (cpp:2212). Any "just call the formatter on the UEdGraph" endpoint is impossible;
   Plan A above is the only BA route and it requires the editor UI.
2. **`FEdGraphFormatter` is unexported.** `class FEdGraphFormatter final : public FFormatterInterface`
   (`EdGraphFormatter.h:53`) — no BLUEPRINTASSIST_API despite the interface being exported. The only
   linkable formatting entries are the FBAGraphHandler methods. (Same for `class FBAFormattingChangeData`,
   `BlueprintAssistNodeSizeChangeData.h:77` — status payloads must snapshot positions bridge-side.)
3. **ElectronicNodes: no endpoint surface exists, by construction.** Its entire effect is a
   render-time `FConnectionDrawingPolicy` (`class FENConnectionDrawingPolicy : public FKismetConnectionDrawingPolicy`,
   `Private/ENConnectionDrawingPolicy.h:45`, registered via `FENConnectionDrawingPolicyFactory : public FGraphPanelPinConnectionFactory`, :36
   — all in Private/, zero exports beyond the settings UCLASS). Wire style is drawn at paint time
   and persists NOTHING to the graph: agent-invisible, position-irrelevant. No endpoint proposed.
   Its knobs are already reachable: `set_property` on `/Script/ElectronicNodes.Default__ElectronicNodesSettings`
   (`class ELECTRONICNODES_API UElectronicNodesSettings : public UDeveloperSettings`, `Public/ElectronicNodesSettings.h:56`).
4. **FlatNodes: no endpoint surface; exports literally nothing (census: 0).** Its module const-casts
   the global style set — `FSlateStyleSet* Style = (FSlateStyleSet*)&FAppStyle::Get();`
   (`Private/FlatNodes.cpp:32`) — and swaps node-body brushes. Pure paint. Even its
   `UFlatNodesSettings` (`Public/FlatNodesSettings.h:14`) lacks an export macro (reflection-only).
5. **AutoSizeComments: programmatic resize is unreachable.** The resize logic lives on the Slate
   widget `class SAutoSizeCommentsGraphNode final : public SGraphNode` (`Public/AutoSizeCommentsGraphNode.h:37`)
   — `void ResizeToFit();` (:112) is an unexported instance method needing a live widget; the
   singleton `class FAutoSizeCommentGraphHandler` (`Public/AutoSizeCommentsGraphHandler.h:27`) and
   `struct FASCUtils` (`Public/AutoSizeCommentsUtils.h:14`) both lack export macros. Only the settings
   UCLASS and the cache (`FAutoSizeCommentsCacheFile`, membership GUIDs only — no sizes) are exported.
   Hence fit_comment_to_nodes is bridge-computed with optional cache sync, not ASC-invoked. Note ASC
   auto-corrects any comment the bridge creates once a human opens the graph (reactive resizing) —
   the bridge only needs to be approximately right.
6. **NS_BlueprintToText: export_blueprint_text via this plugin is a dead end.** All logic is private
   unexported module methods (`bool ProcessBlueprint(UBlueprint*, const FString&)`,
   `Public/NS_BlueprintToText.h:23` — on `class FNS_BlueprintToTextModule : public IModuleInterface`, no export;
   menu-driven only, no console commands). Output (read from `Private/NS_BlueprintToText.cpp:251-380,579`)
   is a lossy flat digest — component/variable/function NAMES plus `GetNodeTitle(ListView)` strings
   and pin defaults — written to `Content/BlueprintToText/<Asset>.txt` (:179,212): strictly inferior
   to existing `list_nodes`/`list_components`/`list_variables` JSON and to the proposed T3D
   `export_graph_text`. Additionally its second feature silently POSTs Blueprint comment text to an
   external LLM HTTP endpoint (Ollama-compatible, `Private/BlueprintCommentTranslator.h:48`,
   configured via `UNS_BlueprintToTextSettings.HttpApiURL`) — do not wire anything that could trigger
   it. Negative-resulted; no endpoint.
7. **Engine-native auto-layout does not exist in 5.3.2.** No `IPositioner`/`FNodePositioner`/
   `IGraphFormatter`/FormatterModule anywhere under Engine/Source (grep across Editor + Runtime: zero
   hits); no `UEdGraphSchema::GetPositionForNewNode` (grep of `Runtime/Engine/Classes/EdGraph/EdGraphSchema.h`: zero).
   The only in-engine auto-arrange is Behavior-Tree-only: `void AutoArrange();`
   (`Editor/BehaviorTreeEditor/Classes/BehaviorTreeGraph.h:64`, class IS exported
   `class BEHAVIORTREEEDITOR_API UBehaviorTreeGraph : public UAIGraph`, :19) — but its implementation
   dereferences live Slate widgets UNCHECKED (`RootNode->DEPRECATED_NodeWidget.Pin()->GetDesiredSize()`,
   `BehaviorTreeGraph.cpp:1296-1303`) so it crashes without an open BT editor, and it applies only to
   BT graphs, not K2. Hence Plan B (bridge-side Sugiyama-lite) is the honest fallback, not an engine call.
8. **`FEdGraphUtilities::ImportNodesFromText` deliberately not proposed** (`EdGraphUtilities.h:119`)
   — node paste is a documented MifBridge non-goal; export_graph_text is one-way by design.

## UNVERIFIED

- **BA attach latency**: how many frames `FBATabHandler` needs after `BringKismetToFocusAttentionOnObject`
  before a graph handler exists (ProcessTabs runs off a timer, `BlueprintAssistTabHandler.h:51`) —
  static reading cannot bound it; the ticker-job design tolerates it, but the default timeoutSec=30
  needs live tuning. Not testable via read-only curls (would require opening editors).
- **`FormatAllEvents` on a graph whose events all have cached sizes**: whether formatting completes
  same-frame (making `HasActiveTransaction()` briefly true-then-false between two polls) — poll
  design handles it via the position-snapshot diff, but the exact phase sequence is unobserved.
- **BA knot-node pool vs MifBridge node enumeration**: with `bUseKnotNodePool` (`BlueprintAssistSettings.h:191`)
  BA reuses knot nodes across formats; whether reused knots keep NodeGuids stable across format runs
  (affects list_nodes diffs) is unverified from source.
- **JumpToHyperlink on function graphs of kr_*-reconstructed Blueprints**: the UEdGraph branch exists
  (`BlueprintEditor.cpp:4337+`) but behaviour when the document tab was never opened before in a
  reconstructed asset is untested.
- **FBANodeData.CachedPins pin-offset reuse for Plan B** (`BlueprintAssistCache.h:26`): could give exact
  per-pin Y for wire-aware row ordering; value unproven, left out of the format_graph spec.

## Coverage log

Covered: all four named plugins (uplugin metadata, export census, binaries/import-lib check,
formatter/resize/export entry-point reading, linkability verdicts), BlueprintAssist formatting
pipeline end-to-end from source (tab handler → graph handler → size cache → formatter), engine-native
layout absence sweep (AutoArrange grep across Editor/, EdGraphSchema.h, EdGraphUtilities.h/.cpp,
BehaviorTreeGraph), engine comment-node API, asset-editor open/focus route, project-plugin
enablement rule (PluginManager.cpp), live-bridge confirmation that BlueprintAssist + AutoSizeComments
modules are loaded (describe_class curls) and endpointCount=160 (self_audit). Composition points
read in MifBridge source: H_open_blueprint (does NOT open the editor UI — Plan A must), H_move_node,
H_list_nodes, H_add_comment.

Remaining for Phase 2: live-tune BA attach latency + timeout defaults; decide whether format_graph's
size estimator constants should be calibrated against BA cache data from this project's real graphs
(a one-off measurement script over `BlueprintAssistCache` json would do); evaluate whether
fit_comment_to_nodes should also set `MoveMode = GroupMovement` (current add_comment hardcodes
NoGroupMovement, `MifBridgeNodes4.cpp:333`) once membership is real.
