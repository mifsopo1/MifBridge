# Axis C — Blueprints and graphs
_Sweep date: 2026-07-26. Engine: D:/UE532 (5.3.2 fork). Agent: phase-1 breadth._
_Phase-2 adversarial verification: 2026-07-26. All 18 entries (13 endpoints + 5 behaviour
changes) re-checked against source; 14 CONFIRMED, 4 CORRECTED (export-macro attributions only —
no design change, no demotions). All 7 negatives re-verified, none overturned. One UNVERIFIED
item (FStringOutputDevice) resolved with citation._

This axis has the deepest existing coverage (~100 of the 159 endpoints). Everything below was
diffed against the covered list in `_BRIEF.md`; "already covered in another shape" ideas are in
the Compositions section, not re-proposed.

## Surface inventory

Everything below was read from disk this sweep; counts are from greps run against
`D:/UE532/Engine/Source` (paths relative to it unless noted).

**UK2Node subclass census** (`grep '^class .*UK2Node_'`):
- `Editor/BlueprintGraph/Classes/*.h`: **111 declarations**, of which 3 are UInterfaces
  (`UK2Node_AddPinInterface`, `UK2Node_EventNodeInterface`, `UK2Node_ExternalGraphInterface`)
  and **27 carry `class BLUEPRINTGRAPH_API`**; the rest are `UCLASS(MinimalAPI)` (linkable only
  via reflection + virtual dispatch, or via method-level `BLUEPRINTGRAPH_API` exports).
- `Editor/AnimGraph/Public/*.h`: **80 `UAnimGraphNode_*` subclasses** (all derive from
  `UAnimGraphNode_Base : public UK2Node`, AnimGraphNode_Base.h:194, `ANIMGRAPH_API`), plus
  **17 state-machine object-model classes** (`UAnimStateNode`, `UAnimStateTransitionNode`,
  `UAnimStateAliasNode`, `UAnimStateConduitNode`, `UAnimStateEntryNode`,
  `UAnimationStateMachineGraph/Schema`, `UAnimationStateGraph/Schema`,
  `UAnimationTransitionGraph/Schema`, conduit/custom-transition variants) — all in
  `Editor/AnimGraph/Public/`, mostly `UCLASS(MinimalAPI)`.
- Other engine modules: `Editor/AnimGraph/Public/K2Node_AnimGetter.h`, `K2Node_PlayMontage.h`,
  `K2Node_TransitionRuleGetter.h`; `Editor/UMGEditor/Classes/K2Node_WidgetAnimationEvent.h`
  (+ `UK2Node_CreateWidget`, already covered by add_create_widget);
  `Editor/MovieSceneTools/Public/K2Node_GetSequenceBinding.h`.
- Plugins (`grep -rln 'class .*UK2Node_.* : public' D:/UE532/Engine/Plugins`): 35 headers —
  EnhancedInput `InputBlueprintNodes` (`K2Node_EnhancedInputAction`, `K2Node_GetInputActionValue`,
  + 3 private), LiveLink (4), GameplayAbilities (`K2Node_LatentAbilityCall`,
  `K2Node_GameplayCueEvent`), OnlineBlueprintSupport (12 InAppPurchase/Leaderboard),
  PropertyAccessNode, StructUtils (`K2Node_InstancedStruct`), JsonBlueprintGraph,
  HttpBlueprintGraph, PythonScriptPlugin (not enabled in this project), Chooser, Dataprep,
  BlueprintSnapNodes.

**Spawnability triage of the BlueprintGraph 108 concrete node classes** (dedicated endpoint
already exists / generic-safe / needs-init / dangerous) — full disposition table under the
`add_node_by_class` entry below.

**Headers walked end-to-end for this axis** (regions cited by line in the entries):
- `Editor/UnrealEd/Public/Kismet2/BlueprintEditorUtils.h` — graph lifecycle 242–466, local
  variables 668, 832, 950–1050, member retype 933–942, 1215, 1270.
- `Editor/SubobjectDataInterface/Public/SubobjectDataSubsystem.h` (full: params structs 18–85,
  class 87–380) + `Private/SubobjectDataSubsystem.cpp` 1622–2096 (reparent/attach internals).
- `Runtime/Engine/Classes/Engine/SCS_Node.h` 44–192, `Engine/SimpleConstructionScript.h` 77–110.
- `Developer/ScriptDisassembler/Public/ScriptDisassembler.h` (full) + its Build.cs.
- `Editor/BlueprintGraph/Classes/`: K2Node_CreateDelegate.h (full), K2Node_BaseAsyncTask.h 44–118,
  K2Node_Select.h 105–142, K2Node_FunctionEntry.h 30–64, K2Node_StructOperation.h 35.
- `Editor/AnimGraph/Public/`: AnimationStateMachineGraph.h (full), AnimStateNode.h (full),
  AnimStateTransitionNode.h 19–149, AnimationStateMachineSchema.h 55–80 +
  `Private/AnimationStateMachineSchema.cpp` 211–266, `Private/AnimStateNode.cpp` 113–139,
  `Private/AnimGraphNode_StateMachineBase.cpp` 135–160.
- `Runtime/Engine/Classes/Animation/AnimBlueprintGeneratedClass.h` 354–467,
  `Animation/AnimStateMachineTypes.h` 247–396.
- `Runtime/CoreUObject/Public/UObject/Class.h` 405–409 (`UStruct::Script`),
  `Runtime/Engine/Classes/EdGraph/EdGraph.h` 67, 115, `Runtime/Engine/Classes/Engine/Blueprint.h`
  253–293, 655.
- Plugin source (D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private): MifBridgeHandlers.h
  (full registry), MifBridgeCommon.cpp 475–478 (`K2()`), 1306–1328 (`PlaceAndInit`), 1470–1516
  (`ConnectPinsChecked` — K2-CDO hardcode confirmed at 1494/1509/1515), MifBridgeComponents.cpp
  42–180 (add_component SCS-only parent lookup at 82; list_components own-SCS-only at 147–152),
  MifBridgeIntrospect.cpp 250–275 (list_variables = NewVariables only), 780–868 (add_variable
  scope=local EXISTS — locals are write-once, not write-never), 870–964 (rename/remove/default
  = member-only), MifBridgeNodes2.cpp 441–499 (rename_function refuses delegate graphs, accepts
  any graphId), MifBridgeFunctions.cpp 78–110 (remove_function searches FunctionGraphs only),
  MifBridgeIntrospect.cpp 315–360 (describe_class — no bytecode, no anim data).
- `docs/06_CAPABILITY_ROADMAP.md`, `docs/01_POSTMORTEMS.md` PM-004 (terminator
  AllocateDefaultPins double-pin trap — drives the generic-spawn denylist).

## Proposed endpoints

### add_node_by_class
**Purpose**: spawn ANY concrete `UK2Node` subclass by class path through the existing
`PlaceAndInit` machinery, with a reflection-applied init map — unlocking every node class that
has no dedicated `add_*` endpoint (Select, MultiGate, DoOnceMultiInput, SwitchName, MakeSet,
Knot, GetClassDefaults, SetFieldsInStruct, GenericCreateObject, ConvertAsset, LoadAsset,
BitmaskLiteral, EaseFunction, enum utility nodes, plugin nodes) without one endpoint per class.
**Engine API**: no new engine entry point — `NewObject<UEdGraphNode>(Graph, NodeClass)` + the
plugin's own `MifBridge::PlaceAndInit` (MifBridgeCommon.cpp:1306). Class resolution via
`FindObject<UClass>`/`LoadObject<UClass>` on a `/Script/Module.ClassName` path. Init map applied
via `FProperty::ImportText_InContainer` BEFORE `PlaceAndInit` (reflection writes UPROPERTYs
regardless of C++ `protected:` — verified needed for e.g. `UK2Node_StructOperation::StructType`,
K2Node_StructOperation.h:35, and the async-task Proxy* fields, K2Node_BaseAsyncTask.h:95–110).
Post-init reconstruction via the virtual `UEdGraphNode::ReconstructNode()` — inline no-op body
`{}` at Runtime/Engine/Classes/EdGraph/EdGraphNode.h:689 (no export macro; callable regardless),
with UK2Node's override method-exported (`BLUEPRINTGRAPH_API virtual void ReconstructNode()
override;`, K2Node.h:214) and reached by normal virtual dispatch.
**Export**: none needed beyond what is linked — node classes are reachable because the endpoint
only uses reflection + virtual dispatch. NOTE (Phase-2 correction): `UEdGraphNode` is
`UCLASS(MinimalAPI)` (EdGraphNode.h:272–273) and `UK2Node` is `UCLASS(abstract, MinimalAPI)`
(K2Node.h:200–201) — NEITHER is a class-level export; both rely on method-level macros
(ENGINE_API / BLUEPRINTGRAPH_API per method). MinimalAPI still exports type info, so
`StaticClass()`/`Cast<>`/`IsChildOf` work cross-module. | **Module**:
none — already linked (BlueprintGraph). Nodes from unloaded modules: resolve, and if null,
error naming the module (do NOT auto-load plugins). | **Guards**: none (whole plugin is editor-only)
**Bucket**: transacted — single node add, same as every existing add_* endpoint.
**Async**: no
**Params**:
| name | aliases | type | default | required |
|---|---|---|---|---|
| graphId | graph | string | — | yes |
| nodeClass | class | string (`/Script/Module.K2Node_X` or bare `K2Node_X`) | — | yes |
| init | properties | object: propertyName → value string (ImportText syntax) | {} | no |
| x, y | — | int | 0 | no |
Unrecognised → error. Bare names resolved via `FindFirstObject<UClass>`; ambiguity → error listing matches.
**Denylist (hard, with error text naming the dedicated endpoint or reason)** — from the census:
- terminators `K2Node_FunctionEntry/FunctionResult/FunctionTerminator` (PM-004: their
  `PostPlacedNewNode` → `SyncWithEntryNode` → `ReconstructNode` pin-allocation order is special;
  created only via `AddFunctionGraph`/`CreateFunctionGraphTerminators`) → "use create_function";
- `K2Node_Event/CustomEvent/ActorBoundEvent/ComponentBoundEvent/InputActionEvent/...Event`
  (need EventReference/delegate registration) → "use add_custom_event / add_override_event";
- `K2Node_Timeline` (needs a registered UTimelineTemplate) → "use add_timeline";
- `K2Node_MacroInstance` (needs SetMacroGraph before alloc) → "use add_macro_instance";
- `K2Node_Composite/Tunnel/TunnelBoundary` (collapse machinery — documented non-goal);
- compiler-internal: `K2Node_DeadClass`, `K2Node_SetVariableOnPersistentFrame`,
  `K2Node_TemporaryVariable`, `K2Node_PureAssignmentStatement`, `K2Node_MakeVariable`,
  `K2Node_AddComponent` (needs Blueprint->ComponentTemplates entry; AddComponentByClass is fine);
- abstract classes (`ClassDefaultObject` check `CLASS_Abstract`) → error "abstract".
Nodes needing init to be useful but SAFE with defaults (documented, not blocked):
`K2Node_Select` (enum via init `Enum=` + ReconstructNode), `K2Node_SetFieldsInStruct`
(`StructType=`), `K2Node_EnumLiteral`/`CastByteToEnum`/`ForEachElementInEnum`/`GetNumEnumEntries`
(`Enum=`), `K2Node_GenericCreateObject` (none), `K2Node_MultiGate`/`DoOnceMultiInput`/`MakeSet`/
`SwitchName`/`Knot`/`GetClassDefaults`/`ConvertAsset`/`LoadAsset(Class)`/`BitmaskLiteral`/
`EaseFunction`/`VariableSetRef` (none). AnimGraph node classes are ACCEPTED when the target
graph's schema accepts them (checked via `UEdGraphNode::IsCompatibleWithGraph` — Phase-2
correction: declared on UEdGraphNode, not UK2Node, as a method-level export:
`ENGINE_API virtual bool IsCompatibleWithGraph(UEdGraph const* Graph) const;`,
Runtime/Engine/Classes/EdGraph/EdGraphNode.h:714; K2Node_BaseAsyncTask.h:58 shows the override
pattern) — reject with the schema's reason when incompatible.
**Failure modes**: unknown class → `nodeClass 'X' not found — pass a /Script/Module.Class path;
if the class lives in an unloaded module, load-order is the issue, not the name`; denylisted →
message above; abstract → named; init property not found on class → `init key 'Foo' is not a
property of K2Node_X`; ImportText parse failure → key + expected type; graph-schema rejection →
`node class 'X' is not allowed in graph 'Y' (schema Z)`.
**Cooked**: refuses like every graph mutation — cooked BPs have no graphs (`ResolveBlueprint`
already grades this).
**Verify**: response emits full `SerializeNode` (class, GUID, pins with types). Numeric check:
`get_node` → pin count/categories equal expected for the class; `compile` → numErrors=0.
**Score**: U5 E2 R3 → tier 0 (roadmap: "No generic add-node-by-class"). Denylist prevents the
documented PM-004 failure class.
**Phase-2 verdict**: CORRECTED — export claims fixed: UK2Node and UEdGraphNode are both
MinimalAPI classes (K2Node.h:200–201, EdGraphNode.h:272–273), not class-level exports;
ReconstructNode is an inline `{}` virtual (EdGraphNode.h:689) with a BLUEPRINTGRAPH_API override
(K2Node.h:214); graph-compat check re-cited to `UEdGraphNode::IsCompatibleWithGraph`
(ENGINE_API method, EdGraphNode.h:714). Design unchanged and viable: reflection + virtual
dispatch route verified; PlaceAndInit (MifBridgeCommon.cpp:1306–1328), StructType
(K2Node_StructOperation.h:34–35), Proxy* fields (K2Node_BaseAsyncTask.h:95–110),
`FProperty::ImportText_InContainer` (UnrealType.h:480) all re-read verbatim.

### list_node_classes
**Purpose**: enumerate every spawnable `UK2Node` subclass currently loaded (name, module,
abstract flag, denylist status, whether a dedicated endpoint exists) so an agent can discover
what `add_node_by_class` accepts instead of guessing class paths.
**Engine API**:
```cpp
// Runtime/CoreUObject/Public/UObject/UObjectIterator.h — TObjectIterator<UClass>
// filter: It->IsChildOf(UK2Node::StaticClass()) — UK2Node is
UCLASS(abstract, MinimalAPI)
class UK2Node : public UEdGraphNode
```
Editor/BlueprintGraph/Classes/K2Node.h:200–201 (Phase-2 correction: the class is MinimalAPI, NOT
`class BLUEPRINTGRAPH_API`; MinimalAPI still exports the type info, so `UK2Node::StaticClass()`
and `IsChildOf` link fine cross-module). The separate census fact — 27 node classes carrying
`class BLUEPRINTGRAPH_API` — re-verified by grep (27 headers). `TObjectIterator` is header-only
(COREUOBJECT).
**Export**: UK2Node is MinimalAPI (type-info export only — sufficient for the StaticClass anchor
this endpoint needs) | **Module**: none — already linked
| **Guards**: none
**Bucket**: read-only — pure query.
**Async**: no
**Params**: | filter | — | string substring | "" | no | ; | includeAnimGraph | — | bool | true | no |
Unrecognised → error.
**Failure modes**: none hard; empty result only if filter matches nothing (returns count 0, not error).
**Cooked**: unaffected — class registry, not assets.
**Verify**: `count` ≥ 108 in a default editor session (census above); every entry's `class`
round-trips through `add_node_by_class` or returns its documented denial.
**Score**: U3 E5 R5 → tier 1 (companion; makes the generic endpoint self-documenting).
**Phase-2 verdict**: CORRECTED — export claim fixed (UK2Node is UCLASS(abstract, MinimalAPI),
K2Node.h:200–201, not a BLUEPRINTGRAPH_API class; StaticClass anchor still works via MinimalAPI
type-info export). 27-exported-class census independently re-counted: exactly 27 headers match
`^class BLUEPRINTGRAPH_API UK2Node_` in Editor/BlueprintGraph/Classes. Read-only bucket correct.

### add_create_delegate
**Purpose**: spawn `UK2Node_CreateDelegate` wired to a target function — closes the roadmap gap
"a dispatcher can only be bound to a freshly-authored custom event, never from inside a function
or macro graph": CreateDelegate + add_bind_dispatcher binds any existing function to a dispatcher
from ANY graph.
**Engine API**:
```cpp
UCLASS(MinimalAPI)
class UK2Node_CreateDelegate : public UK2Node
	UPROPERTY(meta = (BlueprintSearchable = "true"))
	FName SelectedFunctionName;
	/** Set new Function name (Without notifying about the change) */
	BLUEPRINTGRAPH_API void SetFunction(FName Name);
	BLUEPRINTGRAPH_API UFunction* GetDelegateSignature() const;
	BLUEPRINTGRAPH_API UClass* GetScopeClass(bool bDontUseSkeletalClassForSelf = false) const;
	BLUEPRINTGRAPH_API UEdGraphPin* GetDelegateOutPin() const;
	BLUEPRINTGRAPH_API UEdGraphPin* GetObjectInPin() const;
	BLUEPRINTGRAPH_API void HandleAnyChange(bool bForceModify = false);
	bool IsValid(FString* OutMsg = nullptr, bool bDontUseSkeletalClassForSelf = false) const;
```
Editor/BlueprintGraph/Classes/K2Node_CreateDelegate.h:27–33, 59–71.
**Export**: class is MinimalAPI but every method we need is method-level `BLUEPRINTGRAPH_API`
(SetFunction :62, GetDelegateOutPin :68, GetObjectInPin :69, HandleAnyChange :71). NOTE:
`IsValid` (:59) is NOT exported — validate via `GetDelegateSignature() != nullptr` instead.
| **Module**: none — BlueprintGraph linked | **Guards**: none
**Bucket**: transacted — plain node add.
**Sequence**: `NewObject<UK2Node_CreateDelegate>` → `PlaceAndInit` (pins: delegate out + self
object in) → `SetFunction(FName(functionName))` → `HandleAnyChange(true)` (resolves the delegate
pin's signature). Wire the delegate out-pin to add_bind_dispatcher's Delegate pin (red pin) with
connect_pins.
**Async**: no
**Params**: graphId (req), functionName|function (req — validated against scope class after the
object pin is optionally wired; if `targetClass` given, check `FindFunctionByName` up front),
x, y. Unrecognised → error.
**Failure modes**: function not found on scope class → `functionName 'X' not found on 'Y' —
create it first (create_function) or pass the correct targetClass`; signature mismatch surfaces
at compile with node-mapped message (existing compile covers it).
**Cooked**: refuses (graph mutation).
**Verify**: `get_node` shows `SelectedFunctionName`; `compile` numErrors=0; connecting a
mismatched dispatcher yields a node-mapped compile error (negative test).
**Score**: U4 E4 R4 → tier 0 (named roadmap item).
**Phase-2 verdict**: CONFIRMED — K2Node_CreateDelegate.h re-read: UCLASS(MinimalAPI) :27–28,
SelectedFunctionName :32–33, IsValid unexported :59, SetFunction :62, GetDelegateSignature :64,
GetDelegateOutPin :68, GetObjectInPin :69, HandleAnyChange(bool) :71 — every method-level
BLUEPRINTGRAPH_API claim verbatim, including the IsValid-unexported caveat. Bucket/async/cooked
consistent with existing add_* endpoints.

### add_async_action
**Purpose**: spawn the `UK2Node_AsyncAction` family (Delay-style latent proxy nodes: AIMoveTo,
PlayMontage-style tasks, any `UBlueprintAsyncActionBase` factory) — currently the whole async
node family is unauthorable.
**Engine API**:
```cpp
UCLASS(Abstract)
class BLUEPRINTGRAPH_API UK2Node_BaseAsyncTask : public UK2Node
protected:
	// The name of the function to call to create a proxy object
	UPROPERTY()
	FName ProxyFactoryFunctionName;
	// The class containing the proxy object functions
	UPROPERTY()
	TObjectPtr<UClass> ProxyFactoryClass;
	// The type of proxy object that will be created
	UPROPERTY()
	TObjectPtr<UClass> ProxyClass;
	// The name of the 'go' function on the proxy object that will be called after delegates are in place, can be NAME_None
	UPROPERTY()
	FName ProxyActivateFunctionName;
```
Editor/BlueprintGraph/Classes/K2Node_BaseAsyncTask.h:49–52, 95–110. Concrete spawn class:
`class BLUEPRINTGRAPH_API UK2Node_AsyncAction : public UK2Node_BaseAsyncTask`
(K2Node_AsyncAction.h:18).
**Export**: BLUEPRINTGRAPH_API on both classes. The four Proxy* members are `protected` C++ but
`UPROPERTY()` — set them via `FProperty::ImportText_InContainer`/SetValue reflection BEFORE
`PlaceAndInit` so `AllocateDefaultPins` (K2Node_BaseAsyncTask.h:55) can derive exec/delegate/data
pins from the factory function. | **Module**: none | **Guards**: none
**Bucket**: transacted.
**Async**: no (the NODE is latent at runtime; spawning it is synchronous).
**Params**: graphId (req); factoryFunction (req — `Class::Function` or function name);
factoryClass (req unless embedded in factoryFunction); x, y. Endpoint resolves the factory
UFunction, requires its return type to be a `UBlueprintAsyncActionBase`/proxy subclass, then sets
ProxyFactoryFunctionName/ProxyFactoryClass/ProxyClass(=return type)/ProxyActivateFunctionName
(`Activate` if present, else None). Unrecognised → error.
**Failure modes**: factory not found → names class+function; factory return type not a UObject
class → `'X.Y' does not return an async proxy object — not an async factory`; node placed in a
function graph → schema/IsCompatibleWithGraph rejection surfaced verbatim (latent nodes are
event-graph/macro-only).
**Cooked**: refuses (graph mutation).
**Verify**: `get_node` pin list contains one output exec per multicast delegate on ProxyClass
(count check against `describe_class` of the proxy); `compile` numErrors=0.
**Score**: U4 E3 R3 → tier 1 (roadmap: "the async-action family ... unreachable").
**Phase-2 verdict**: CONFIRMED — K2Node_BaseAsyncTask.h re-read: `UCLASS(Abstract)` + `class
BLUEPRINTGRAPH_API` :49–50, AllocateDefaultPins :55, protected Proxy* UPROPERTYs :95–110 all
verbatim; `class BLUEPRINTGRAPH_API UK2Node_AsyncAction` K2Node_AsyncAction.h:18 verbatim.
Reflection write of protected UPROPERTYs before PlaceAndInit is sound
(ImportText_InContainer, UnrealType.h:480).

### set_variable_type
**Purpose**: retype a member OR local variable in place — today "repair means remove + add,
dropping every get/set node, flags and category" (roadmap).
**Engine API**:
```cpp
/** Changes the type of a member variable */
static UNREALED_API void ChangeMemberVariableType(UBlueprint* Blueprint, const FName VariableName, const FEdGraphPinType& NewPinType);
```
Editor/UnrealEd/Public/Kismet2/BlueprintEditorUtils.h:942
```cpp
static UNREALED_API void ChangeLocalVariableType(UBlueprint* InBlueprint, const UStruct* InScope, const FName InVariableName, const FEdGraphPinType& InNewPinType);
```
BlueprintEditorUtils.h:1050 (scope resolution via
`static UNREALED_API FBPVariableDescription* FindLocalVariable(const UBlueprint* InBlueprint, const UEdGraph* InScopeGraph, const FName InVariableName, class UK2Node_FunctionEntry** OutFunctionEntry = NULL);`
:990 — the graph→UStruct scope hop uses the entry node's generated function; or pass the
FunctionGraph-derived `UFunction` from the class).
**Export**: UNREALED_API (method-level; verified on every line above) | **Module**: none —
UnrealEd linked | **Guards**: none
**Bucket**: transacted. NOT compile-heavy itself (marks structurally modified; callers compile
separately) — but document that dependent Blueprints recompile on next compile.
**Async**: no
**Params**: name (req), type (req — same grammar as add_variable, incl. container/valueType via
existing `MakePinType`), scope: `member`(default)|`local`, function (req when scope=local).
Unrecognised → error.
**Failure modes**: unknown variable → `variable 'X' not found (scope=member; did you mean
scope=local + function=?)`; MakePinType error verbatim; retype of a dispatcher's backing
PC_MCDelegate variable → REFUSE (same footgun class as rename_variable-on-dispatcher):
`'X' is an event-dispatcher delegate variable — retype the dispatcher signature instead
(add_pin/remove_pin on its signature graph)`.
**Cooked**: refuses.
**Verify**: `list_variables` shows new `type` object; existing get/set nodes survive —
`find_nodes varName=X` count unchanged before/after; `compile` reports any now-invalid links as
node-mapped errors (expected when narrowing).
**Score**: U5 E4 R3 → tier 0 (roadmap "No variable retype").
**Phase-2 verdict**: CONFIRMED — BlueprintEditorUtils.h re-read: ChangeMemberVariableType :942,
FindLocalVariable(graph overload, OutFunctionEntry) :990, ChangeLocalVariableType :1050 — all
three signatures verbatim, all method-level UNREALED_API. Dispatcher-retype refusal matches the
existing rename_variable PC_MCDelegate guard precedent (MifBridgeIntrospect.cpp:899–909).

### create_macro
**Purpose**: create a user macro graph (entry/exit tunnels) so add_macro_instance can place
user-authored macros — named roadmap item; today only engine standard-macro instances work.
**Engine API**:
```cpp
static UNREALED_API class UEdGraph* CreateNewGraph(UObject* ParentScope, const FName& GraphName, TSubclassOf<class UEdGraph> GraphClass, TSubclassOf<class UEdGraphSchema> SchemaClass);
```
BlueprintEditorUtils.h:329
```cpp
static UNREALED_API void AddMacroGraph(UBlueprint* Blueprint, class UEdGraph* Graph,  bool bIsUserCreated, UClass* SignatureFromClass);
```
BlueprintEditorUtils.h:421. Engine call sequence verified in FBlueprintEditor (the editor's own
"new macro" action): `CreateNewGraph(BP, Name, UEdGraph::StaticClass(), <K2 schema>)` then
`AddMacroGraph(BP, NewGraph, /*bIsUserCreated=*/true, nullptr)` —
Editor/Kismet/Private/BlueprintEditor.cpp:9426–9427 (and 5893–5894). AddMacroGraph creates the
tunnel entry/exit nodes (bIsUserCreated path), so PM-004 does not apply — we never hand-spawn
terminators. Inputs/outputs afterwards via the EXISTING `add_pin` on the tunnel nodes
(UK2Node_Tunnel is `UK2Node_EditablePinBase`, K2Node_Tunnel.h:31 — same editable-pin path
add_pin already drives for function entries).
**Export**: UNREALED_API both | **Module**: none | **Guards**: none
**Bucket**: transacted.
**Async**: no
**Params**: blueprintId (req), name (req, IsValidIdentifier + FindUniqueKismetName collision
check → error, not silent rename), x/y n/a. Unrecognised → error.
**Failure modes**: duplicate name → `graph name 'X' already exists in <BP> — pass a different
name`; macro in an interface/data-only BP → refuse with reason.
**Cooked**: refuses.
**Verify**: `list_graphs` gains graphId `<BP>.X` with type macro; `add_macro_instance` +
`compile` numErrors=0; tunnel pin counts via `list_nodes` equal add_pin calls made.
**Score**: U4 E4 R4 → tier 0 (roadmap "create_macro").
**Phase-2 verdict**: CONFIRMED — CreateNewGraph BlueprintEditorUtils.h:329 and AddMacroGraph :421
verbatim UNREALED_API; the engine call sequence re-read at BOTH cited sites
(BlueprintEditor.cpp:9426–9427 new-macro action, :5893–5894 collapse-validation temp macro) —
identical two-call pattern with bIsUserCreated=true. `class BLUEPRINTGRAPH_API UK2Node_Tunnel :
public UK2Node_EditablePinBase` K2Node_Tunnel.h:31 verbatim, so the add_pin route holds.

### remove_graph
**Purpose**: delete a macro or collapsed/extra graph (remove_function only searches
`Blueprint->FunctionGraphs` — MifBridgeFunctions.cpp:93 — so macros are permanent today).
**Engine API**:
```cpp
static UNREALED_API void RemoveGraph( UBlueprint* Blueprint, class UEdGraph* GraphToRemove, EGraphRemoveFlags::Type Flags = EGraphRemoveFlags::Default );
```
BlueprintEditorUtils.h:449.
**Export**: UNREALED_API | **Module**: none | **Guards**: none
**Bucket**: transacted; confirm=true gated like remove_function.
**Async**: no
**Params**: graphId (req), confirm (req true). Refuses: ubergraph pages (`UbergraphPages`
membership) unless `allowEventGraph=true` AND >1 page; delegate signature graphs (route to
remove via dispatcher endpoints); anim state-machine internal graphs (bound graphs die with
their owning node — use remove_node on the state instead, error says so). Unrecognised → error.
**Failure modes**: graph not found (ResolveGraph text); protected graph classes as above with
the redirect message.
**Cooked**: refuses.
**Verify**: `list_graphs` count decreases by exactly 1 (+ nested bound graphs); `compile`
numErrors=0; macro instances referencing the dead macro produce node-mapped compile errors
(listed in response as `orphanedInstances` by pre-scanning `find_nodes`).
**Score**: U3 E4 R3 → tier 1.
**Phase-2 verdict**: CONFIRMED — RemoveGraph BlueprintEditorUtils.h:449 verbatim (incl. the
EGraphRemoveFlags default). remove_function precedent re-read: it already calls the same
RemoveGraph (MifBridgeFunctions.cpp:108) after a FunctionGraphs-only search (:93), so the
protected-graph refusal list is the only new logic. Confirm-gate matches house style.

### reparent_component
**Purpose**: move an EXISTING SCS component under a different parent (own-SCS node, inherited-SCS
node, or native component) without destroy/recreate — remove+add loses the template's property
edits and any nodes referencing the variable.
**Engine API** (direct SCS route — deliberately NOT USubobjectDataSubsystem::ReparentSubobjects,
see Negative results):
```cpp
ENGINE_API void AddChildNode(USCS_Node* InNode, bool bAddToAllNodes = true);
ENGINE_API void RemoveChildNode(USCS_Node* InNode, bool bRemoveFromAllNodes = true);
#if WITH_EDITOR
	ENGINE_API void SetParent(USCS_Node* InParentNode);
	ENGINE_API void SetParent(const USceneComponent* InParentComponent);
```
Runtime/Engine/Classes/Engine/SCS_Node.h:126, 129, 184–189.
```cpp
ENGINE_API void AddNode(USCS_Node* Node);
ENGINE_API void RemoveNode(USCS_Node* Node, bool bValidateSceneRootNodes = true);
ENGINE_API USCS_Node* FindSCSNode(const FName InName) const;
```
Runtime/Engine/Classes/Engine/SimpleConstructionScript.h:91, 98, 107.
This mirrors the engine's own attach paths verbatim
(Editor/SubobjectDataInterface/Private/SubobjectDataSubsystem.cpp:2046–2072): same-SCS parent →
`Parent->AddChildNode(Child)`; inherited-SCS parent → `SCS->AddNode(Child); Child->SetParent(ParentNode)`;
native parent → `SCS->AddNode(Child); Child->SetParent(Cast<const USceneComponent>(NativeTemplate))`.
Native template lookup: `GeneratedClass->GetDefaultObject()` →
`AActor::GetComponents`/`FindComponentByClass` by name on the CDO.
**Export**: ENGINE_API throughout | **Module**: none — Engine linked | **Guards**: `SetParent`
overloads are inside `#if WITH_EDITOR` (SCS_Node.h:184) — plugin is editor-only, no extra guard
needed at call sites but note it.
**Bucket**: transacted.
**Async**: no
**Params**: blueprintId (req), name (req — component variable name), newParent (req — SCS node
name, inherited node name, or native component name; `""`/`"root"` = make root). detach first:
if the node currently has an SCS parent, `Parent->RemoveChildNode(Node)`; if it was a root with
native/inherited parent hints, clear `ParentComponentOrVariableName`/`bIsParentComponentNative`
via `SetParent(nullptr-equivalent)` — actually re-`AddNode` after `RemoveNode(Node, false)`.
Unrecognised → error.
**Failure modes**: component not found; newParent not found in any of the three scopes → error
enumerates which scopes were searched and the closest names; parent is not a USceneComponent
class while child is scene-typed → refuse; cycle (newParent is a descendant of name) → refuse
`would create a cycle`.
**Cooked**: refuses (SCS lives only on uncooked UBlueprint).
**Verify**: `list_components` (with the inherited/native listing change below) shows the new
`parent` for the node and unchanged `templatePath`; template property spot-check via
`get_property` on `<Class>:<Name>_GEN_VARIABLE` proves the template survived; `compile`
numErrors=0.
**Score**: U4 E3 R2 → tier 1 (completes the roadmap SCS gap together with the add_component
change below).
**Phase-2 verdict**: CONFIRMED — SCS_Node.h re-read: AddChildNode :126, RemoveChildNode :129,
both SetParent overloads :186/:189 inside `#if WITH_EDITOR` opening at :184 — all ENGINE_API
verbatim. SimpleConstructionScript.h: AddNode :91, RemoveNode :98, FindSCSNode :107 verbatim.
The three-scope attach mirror re-read at SubobjectDataSubsystem.cpp:2043–2072 — matches the
entry's description line-for-line (same-SCS :2048, inherited :2054–2057, native :2063–2066).
No modal/blocking hazards in these paths; transacted bucket appropriate (no compile, no
world teardown, single-object mutation).

### disassemble_function
**Purpose**: read-only Kismet bytecode disassembly of ANY UFunction — including functions of
cooked `UBlueprintGeneratedClass`es whose graphs are stripped. Turns "cooked BP is a black box"
into per-function instruction listings (called functions, property reads/writes, jump structure)
— a Tier-1 aid for the MifKismetReconstructor workflow.
**Engine API**:
```cpp
class FKismetBytecodeDisassembler
{
public:
	SCRIPTDISASSEMBLER_API FKismetBytecodeDisassembler(FOutputDevice& InAr);
	SCRIPTDISASSEMBLER_API void DisassembleStructure(UFunction* Source);
	SCRIPTDISASSEMBLER_API static void DisassembleAllFunctionsInClasses(FOutputDevice& Ar, const FString& ClassnameSubstring);
```
Developer/ScriptDisassembler/Public/ScriptDisassembler.h:25, 37, 44, 52. Bytecode source is
`UStruct::Script`:
```cpp
	/** Script bytecode associated with this object */
	TArray<uint8> Script;
```
Runtime/CoreUObject/Public/UObject/Class.h:408–409 (public member of COREUOBJECT-exported
UStruct; serialized for cooked classes — it is what the VM executes at runtime, so it is present
on pak-mounted `UBlueprintGeneratedClass` functions). Engine precedent for the exact call:
`UUnrealEdEngine::HandleDisasmScriptCommand` → `FKismetBytecodeDisassembler::DisassembleAllFunctionsInClasses(Ar, ClassName)`
(Editor/UnrealEd/Private/UnrealEdSrv.cpp:620–630). Capture via `FStringOutputDevice` (Core).
**Export**: SCRIPTDISASSEMBLER_API (method-level; class unexported — fine, we only call exported
members + ctor). | **Module**: **NEW dep: `ScriptDisassembler`** — Developer module, deps only
Core+CoreUObject (Developer/ScriptDisassembler/ScriptDisassembler.Build.cs), editor/developer
only, not a runtime leak (MifBridge is editor-only). | **Guards**: none.
**Bucket**: read-only — writes nothing, no transaction.
**Async**: no (single function disassembly is fast; refuse `function="*"` over huge classes? No —
cap: if function omitted, disassemble all functions but report per-function text with a
`totalBytes` guard, warning above ~1 MB output).
**Params**: class|objectPath (req — class path, e.g. `/Game/X/BP_Foo.BP_Foo_C` or native class),
function (opt — name; omitted = all), includeRaw (opt bool, default false — also emit
`Script.Num()` byte count only, never raw bytes). Unrecognised → error.
**Failure modes**: class not found (graded cooked-aware message via existing
DescribeMissingBlueprint precedent); function not found → lists available function names;
`Script.Num()==0` (BlueprintPure stubs / native) → `function 'X' has no bytecode (native or
empty)` — not an error, `bytecode:false` field.
**Cooked**: WORKS — this is its main purpose. Note: `EX_*` listing reflects post-cook final
form (skip-offsets resolved), which is exactly what the reconstructor wants.
**Verify**: for an uncooked BP, `numFunctions` equals `list_functions` count; a function with a
known body (e.g. authored by recipe_add_debug_print) contains `EX_CallMath`/named
`PrintString` in its listing; `scriptBytes` > 0 matches `UFunction::Script.Num()` reported.
**Score**: U5 E4 R5 → tier 1 (read-only; unlocks cooked-code inspection category).
**Phase-2 verdict**: CONFIRMED — ScriptDisassembler.h re-read: ctor :37, DisassembleStructure
:44, static DisassembleAllFunctionsInClasses :52, all SCRIPTDISASSEMBLER_API method-level on an
unexported class (:25) exactly as claimed. Build.cs re-read: Core+CoreUObject only,
bRequiresImplementModule=false — clean Developer-module dep. `TArray<uint8> Script;`
Class.h:408–409 verbatim. Engine precedent UnrealEdSrv.cpp:620–630 verbatim. Capture device
resolved this pass: `class FStringOutputDevice : public FString, public FOutputDevice`,
Runtime/Core/Public/Containers/UnrealString.h:2387–2414 — fully inline, zero export concerns
(UNVERIFIED item cleared). Read-only bucket + output cap sound.

### describe_anim_class
**Purpose**: structured read of a (cooked or uncooked) Animation Blueprint GENERATED class:
baked state machines (states, transitions, entry rules), anim-node property census — the
"what is inside this cooked AnimBP" question describe_class cannot answer (it lists
BlueprintCallable functions/properties only, MifBridgeIntrospect.cpp:315–360).
**Engine API**:
```cpp
UCLASS(MinimalAPI)
class UAnimBlueprintGeneratedClass : ...
	UPROPERTY()
	TArray<FBakedAnimationStateMachine> BakedStateMachines;
	...
	TArray<FStructProperty*> AnimNodeProperties;
	virtual const TArray<FBakedAnimationStateMachine>& GetBakedStateMachines() const override { return GetRootClass()->GetBakedStateMachines_Direct(); }
	virtual const TArray<FStructProperty*>& GetAnimNodeProperties() const override { return AnimNodeProperties; }
```
Runtime/Engine/Classes/Animation/AnimBlueprintGeneratedClass.h:363, 373–374, 392, 448, 451
(accessors are INLINE in the header — MinimalAPI is irrelevant, they compile into our TU).
Payload structs (all UPROPERTY, Runtime/Engine/Classes/Animation/AnimStateMachineTypes.h):
`FBakedAnimationStateMachine{ FName MachineName; int32 InitialState; TArray<FBakedAnimationState> States; TArray<FAnimationTransitionBetweenStates> Transitions; }` :355–375;
`FBakedAnimationState{ FName StateName; TArray<FBakedStateExitTransition> Transitions; int32 StateRootNodeIndex; ...; TArray<int32> PlayerNodeIndices; ... }` :299–341;
`FBakedStateExitTransition` :247.
`AnimNodeProperties` census: for each `FStructProperty*`, emit property name + `Struct->GetName()`
(e.g. FAnimNode_SequencePlayer) + offsets — this array is NOT reflected (raw FStructProperty
pointers built at Link time), so get_property cannot reach it: genuine endpoint value.
**Export**: none needed (inline accessors + public UPROPERTY data + reflection) | **Module**:
none — Engine linked | **Guards**: none (BakedStateMachines is runtime data, not editor-only).
**Bucket**: read-only.
**Async**: no
**Params**: class|objectPath (req — accepts `_C` class path or the anim BP asset path, resolving
its GeneratedClass when uncooked). Unrecognised → error.
**Failure modes**: class is not a UAnimBlueprintGeneratedClass → says what it actually is;
asset unloadable → graded message.
**Cooked**: WORKS — BakedStateMachines/AnimNodeProperties are cooked runtime data. (Editor
graphs stripped: response includes `graphsAvailable:false` for cooked so agents don't try
graph endpoints next.)
**Verify**: state/transition COUNTS equal what `describe_animation`/gameplay shows (e.g.
Locomotion machine: N states, M transitions); on an UNCOOKED AnimBP, counts equal
`list_graphs`-visible state machine contents (cross-check both routes on the same asset).
**Score**: U4 E4 R5 → tier 1 (reconstructor aid; read-only).
**Phase-2 verdict**: CONFIRMED — AnimBlueprintGeneratedClass.h re-read: UCLASS(MinimalAPI)
:363–364, BakedStateMachines UPROPERTY :373–374 (public — GENERATED_UCLASS_BODY leaves public
scope; `private:` only starts at :418), AnimNodeProperties :392, inline GetBakedStateMachines
:448 and GetAnimNodeProperties :451 — all verbatim. Payload structs verbatim at
AnimStateMachineTypes.h: FBakedStateExitTransition :247, FBakedAnimationState :299 (StateName
:305, Transitions :309, StateRootNodeIndex :313, PlayerNodeIndices :332),
FBakedAnimationStateMachine :355 (MachineName :361, InitialState :365, States :369, Transitions
:373). Cooked-works claim consistent with these being UPROPERTY runtime data.

### add_anim_state_machine
**Purpose**: create a state machine node inside an Animation Blueprint's AnimGraph — the entry
point of AnimBP authoring, currently impossible (add_* endpoints spawn only K2 classes).
**Engine API**: spawn `UAnimGraphNode_StateMachine`
(`class UAnimGraphNode_StateMachine : public UAnimGraphNode_StateMachineBase`,
Editor/AnimGraph/Public/AnimGraphNode_StateMachine.h:12, MinimalAPI) through the generic
NewObject+PlaceAndInit path; its `PostPlacedNewNode` self-builds the whole object model:
```cpp
void UAnimGraphNode_StateMachineBase::PostPlacedNewNode()
{
	...
	EditorStateMachineGraph = CastChecked<UAnimationStateMachineGraph>(FBlueprintEditorUtils::CreateNewGraph(this, NAME_None, UAnimationStateMachineGraph::StaticClass(), UAnimationStateMachineSchema::StaticClass()));
	...
	FBlueprintEditorUtils::RenameGraphWithSuggestion(EditorStateMachineGraph, NameValidator, TEXT("New State Machine"));
	...
	Schema->CreateDefaultNodesForGraph(*EditorStateMachineGraph);   // spawns the Entry node
	...
	ParentGraph->SubGraphs.Add(EditorStateMachineGraph);
```
Editor/AnimGraph/Private/AnimGraphNode_StateMachineBase.cpp:135–160. Rename to the requested
name afterwards via `FBlueprintEditorUtils::RenameGraph` (BlueprintEditorUtils.h:458).
**Export**: not needed for spawning — class resolved by path
(`/Script/AnimGraph.AnimGraphNode_StateMachine`), all calls are ENGINE_API virtuals
(`PostPlacedNewNode`, `AllocateDefaultPins` on UEdGraphNode). **Module**: NONE at link time;
the AnimGraph EDITOR module must be LOADED (it is whenever any AnimBP asset is loaded; else
`FModuleManager::Get().LoadModule("AnimGraph")` — editor-only module, fine). Linking `AnimGraph`
becomes necessary only if the handler wants typed casts (optional; recommended for the
state-machine graphId return: find the created `UAnimationStateMachineGraph` generically via
`Node->GetSubGraphs()` — Phase-2 correction: an inline virtual with header body, NOT ENGINE_API
(`virtual TArray<UEdGraph*> GetSubGraphs() const { return TArray<UEdGraph*>(); }`,
EdGraphNode.h:674); the override exists on `class ANIMGRAPH_API UAnimGraphNode_StateMachineBase`
(AnimGraphNode_StateMachineBase.h:15, override declared :35, EditorStateMachineGraph UPROPERTY
:21) — callable via virtual dispatch, so still NO link dep). | **Guards**: none.
**Bucket**: transacted.
**Async**: no
**Params**: graphId (req — must be an AnimGraph; enforced via graph schema class name
`AnimationGraphSchema`, else error), name (req), x, y. Unrecognised → error.
**Failure modes**: target graph is not an anim graph → `graphId 'X' uses schema 'Y' — state
machines can only be placed in an Animation Blueprint's AnimGraph`; blueprint is not an AnimBP →
same class of message; name collision → error.
**Cooked**: refuses.
**Verify**: response returns `stateMachineGraphId`; `list_graphs` (GatherGraphs already recurses
via GetSubGraphs — MifBridgeHandlers.h:63–65) shows it with 1 node (entry); `compile`
numErrors=0; output pose pin present on the node (`get_node` pins=1 out "Pose").
**Score**: U5 E3 R3 → tier 1. Needs AnimGraph module: loaded yes / linked no.
**Phase-2 verdict**: CORRECTED — GetSubGraphs export claim fixed (inline virtual EdGraphNode.h:674,
not ENGINE_API; override on the ANIMGRAPH_API-exported UAnimGraphNode_StateMachineBase,
AnimGraphNode_StateMachineBase.h:15/:35 — a class-level export the entry may exploit if AnimGraph
is ever linked). Everything else verbatim: UCLASS(MinimalAPI) UAnimGraphNode_StateMachine
(AnimGraphNode_StateMachine.h:11–12), PostPlacedNewNode self-build
AnimGraphNode_StateMachineBase.cpp:135–161 (quoted code exact), RenameGraph
BlueprintEditorUtils.h:458. No modal/blocking hazards in PostPlacedNewNode.

### add_anim_state
**Purpose**: add a state to a state machine graph; returns the state's bound animation graph id
so existing graph endpoints can author its contents.
**Engine API**: spawn `UAnimStateNode` (UCLASS(MinimalAPI),
Editor/AnimGraph/Public/AnimStateNode.h:22–30, `BoundGraph` UPROPERTY :29–30,
`GetSubGraphs()` override :62) via NewObject+PlaceAndInit; self-building:
```cpp
void UAnimStateNode::PostPlacedNewNode()
{
	check(BoundGraph == NULL);
	BoundGraph = FBlueprintEditorUtils::CreateNewGraph(this, NAME_None, UAnimationStateGraph::StaticClass(), UAnimationStateGraphSchema::StaticClass());
	...
	FBlueprintEditorUtils::RenameGraphWithSuggestion(BoundGraph, NameValidator, TEXT("State"));
	...
	Schema->CreateDefaultNodesForGraph(*BoundGraph);   // spawns the state Result (pose sink)
	...
	ParentGraph->SubGraphs.Add(BoundGraph);
```
Editor/AnimGraph/Private/AnimStateNode.cpp:113–139. Rename via RenameGraph (see above).
**Export**: same story as add_anim_state_machine — reflection spawn + ENGINE_API virtuals only.
| **Module**: AnimGraph loaded, not linked | **Guards**: none
**Bucket**: transacted.
**Async**: no
**Params**: graphId (req — must be a UAnimationStateMachineGraph; class-name check), name (req),
x, y. Unrecognised → error.
**Failure modes**: wrong graph type → names the actual schema and the fix (`use the
stateMachineGraphId returned by add_anim_state_machine`); duplicate state name → error.
**Cooked**: refuses.
**Verify**: `list_graphs` gains `...<Machine>.<State>`; the bound graph contains exactly 1 node
(result) via `list_nodes`; wire an asset player inside it (add_node_by_class
`AnimGraphNode_SequencePlayer` + set_property on its Sequence) then `compile` numErrors=0.
**Score**: U5 E3 R3 → tier 1. Needs AnimGraph module: loaded yes / linked no.
**Phase-2 verdict**: CONFIRMED — AnimStateNode.h re-read: UCLASS(MinimalAPI) :22–23, BoundGraph
UPROPERTY :29–30, GetSubGraphs override :62 (inline `{ return TArray<UEdGraph*>({ BoundGraph }); }`);
AnimStateNode.cpp:113–139 PostPlacedNewNode quoted code exact (CreateNewGraph with
UAnimationStateGraph/UAnimationStateGraphSchema, RenameGraphWithSuggestion "State",
CreateDefaultNodesForGraph, SubGraphs.Add).

### add_anim_transition
**Purpose**: create a transition between two states, returning the transition node + its rule
graph id (whose contents are then authorable with EXISTING K2 endpoints — the rule graph is a
K2-schema graph reachable through GatherGraphs).
**Engine API**: the state-machine schema auto-creates the transition when two state pins are
connected — this is the engine's only supported path:
```cpp
bool UAnimationStateMachineSchema::TryCreateConnection(UEdGraphPin* PinA, UEdGraphPin* PinB) const
```
Editor/AnimGraph/Private/AnimationStateMachineSchema.cpp:211 (redirects same-direction pins via
GetInputPin/GetOutputPin, then defers to base) →
```cpp
bool UAnimationStateMachineSchema::CreateAutomaticConversionNodeAndConnections(UEdGraphPin* PinA, UEdGraphPin* PinB) const
{
	...
	UAnimStateTransitionNode* TransitionNode = FEdGraphSchemaAction_NewStateNode::SpawnNodeFromTemplate<UAnimStateTransitionNode>(NodeA->GetGraph(), NewObject<UAnimStateTransitionNode>(), FVector2D(0.0f, 0.0f), false);
	if (PinA->Direction == EGPD_Output) { TransitionNode->CreateConnections(NodeA, NodeB); }
	else { TransitionNode->CreateConnections(NodeB, NodeA); }
```
AnimationStateMachineSchema.cpp:239–266. Schema class: `UCLASS(MinimalAPI) class
UAnimationStateMachineSchema : public UEdGraphSchema` (AnimationStateMachineSchema.h:66–67) —
called via the ENGINE_API virtual `UEdGraph::GetSchema()` (EdGraph.h:115) +
`UEdGraphSchema::TryCreateConnection` virtual: NO AnimGraph link needed. (Direct alternative
`ANIMGRAPH_API void CreateConnections(UAnimStateNodeBase*, UAnimStateNodeBase*)`,
AnimStateTransitionNode.h:149, is method-exported if ever needed.)
**Implementation**: resolve both state nodes by name/GUID in the machine graph, take
fromState's output pin / toState's input pin, call
`Graph->GetSchema()->TryCreateConnection(OutPin, InPin)` — i.e., EXACTLY the connect_pins
schema fix below; this endpoint is a thin, discoverable wrapper that also finds and returns the
new transition node + rule graph id afterwards (scan graph for the new UAnimStateTransitionNode
linked between the two states).
**Export**: virtuals only | **Module**: AnimGraph loaded, not linked | **Guards**: none
**Bucket**: transacted.
**Async**: no
**Params**: graphId (req — state machine graph), from (req — state name), to (req), x, y
(cosmetic; transition auto-positions). Unrecognised → error.
**Failure modes**: state not found → lists states present; from==to → refuse (self-transitions
need conduit/alias design — out of scope, message says so); duplicate transition → engine
allows priority-ordered multiples, so allow but report `existingTransitions` count.
**Cooked**: refuses.
**Verify**: `list_nodes` on the machine graph gains one node of class AnimStateTransitionNode
whose pins link From→To; rule graph (`list_graphs`) contains a Result node with a
`bCanEnterTransition` bool pin (`list_nodes`); after wiring a rule with existing endpoints,
`compile` numErrors=0. Transition properties (CrossfadeDuration etc.,
AnimStateTransitionNode.h:39) via set_property on the node object path.
**Score**: U5 E3 R3 → tier 1. Needs AnimGraph module: loaded yes / linked no. (set_anim_transition_rule
is NOT proposed — the rule graph is authorable by existing endpoints; see Compositions.)
**Phase-2 verdict**: CONFIRMED — AnimationStateMachineSchema.h: UCLASS(MinimalAPI) :66–67,
TryCreateConnection override :76; AnimationStateMachineSchema.cpp:211–237 (same-direction pin
redirect then defers to base) and :239–266 (CreateAutomaticConversionNodeAndConnections spawns
UAnimStateTransitionNode + CreateConnections) — quoted code exact. Base-class call route sound:
`UEdGraph::GetSchema()` ENGINE_API method (EdGraph.h:115 — UEdGraph itself is MinimalAPI, :66–67),
`UEdGraphSchema::TryCreateConnection` ENGINE_API method (EdGraphSchema.h:777 — UEdGraphSchema is
UCLASS(abstract, MinimalAPI), :686–687). Fallback `ANIMGRAPH_API CreateConnections`
AnimStateTransitionNode.h:149 verbatim; GetBoundGraph :138, BoundGraph :26, CrossfadeDuration :39.

## Endpoint-behaviour changes (not new endpoints)

### connect_pins (+ ConnectPinsChecked): use the graph's own schema
Today `ConnectPinsChecked` hardcodes the K2 CDO: `const UEdGraphSchema_K2* Schema = K2();`
(MifBridgeCommon.cpp:1494, `K2()` = `GetDefault<UEdGraphSchema_K2>()` :475–478) for
CanCreateConnection (:1509) and TryCreateConnection (:1515). Any graph whose schema overrides
these — `UAnimationGraphSchema`, `UAnimationStateMachineSchema` (TryCreateConnection override,
AnimationStateMachineSchema.h:76), `UAnimationTransitionSchema`, `WidgetGraphSchema` — silently
gets K2 semantics: state-to-state connections never spawn transitions, anim pose-pin rules never
run. Fix: `const UEdGraphSchema* Schema = OutPin->GetOwningNode()->GetGraph()->GetSchema();`
(`ENGINE_API const class UEdGraphSchema* GetSchema() const;`,
Runtime/Engine/Classes/EdGraph/EdGraph.h:115). Phase-2 precision on the base-class virtuals:
`TryCreateConnection` (EdGraphSchema.h:777) and `BreakPinLinks` (:967) are method-level
ENGINE_API; `CanCreateConnection` (:724) is an inline virtual with header body — all three
callable from MifBridge without new exports — no new module. Roadmap item
verbatim ("connect_pins hardcodes the K2 schema CDO, so UAnimationGraphSchema overrides never
run"). Risk: K2 graphs keep identical behaviour (their schema IS UEdGraphSchema_K2); the exec
`SkipKnots` and alias logic is schema-agnostic. Verify: connecting two anim states with
connect_pins creates a transition node (count check), and an existing K2 regression recipe
compiles 0/0. **U4 E5 R4 → tier 0.**
**Phase-2 verdict**: CORRECTED — the K2-CDO hardcode re-verified exactly (MifBridgeCommon.cpp:
K2() :475–478, `const UEdGraphSchema_K2* Schema = K2();` :1494, CanCreateConnection :1509,
TryCreateConnection :1515, plus BreakPinLinks through the same CDO :1505–1506, which the fix must
also reroute); "all ENGINE_API virtuals" claim tightened — CanCreateConnection is an inline
virtual (EdGraphSchema.h:724), TryCreateConnection/BreakPinLinks are ENGINE_API (:777/:967).
Fix design unchanged and verified viable.

### add_component: parentName resolves inherited-SCS and native parents
Current lookup is own-SCS only: `Parent = SCS->FindSCSNode(FName(*ParentName))`
(MifBridgeComponents.cpp:82) — attaching to a Character's `Mesh` hard-fails (roadmap blocking
item). Extend resolution, mirroring SubobjectDataSubsystem.cpp:2046–2072 (cited above):
1. own SCS → `Parent->AddChildNode(Node)` (unchanged);
2. ancestor Blueprints' SCS (walk `GeneratedClass->GetSuperClass()` chain of UBlueprintGeneratedClass,
   each `->SimpleConstructionScript->FindSCSNode`) → `SCS->AddNode(Node); Node->SetParent(InheritedNode);`
   (SCS_Node.h:186);
3. native component on the CDO (`GeneratedClass->GetDefaultObject<AActor>()`, match by name over
   `GetComponents()`) → `SCS->AddNode(Node); Node->SetParent(NativeSceneComponent);` (SCS_Node.h:189).
All ENGINE_API / WITH_EDITOR as cited under reparent_component. Error text on miss enumerates
all three scopes searched. Verify: `list_components` change below shows
`parent:"Mesh", parentSource:"native"`; PIE spawn shows the component attached (list_pie_actors
component check). **U5 E4 R3 → tier 0 (roadmap blocking).**
**Phase-2 verdict**: CONFIRMED — own-SCS-only lookup verbatim at MifBridgeComponents.cpp:82
(with the clean parent-not-found fail at :83–87); the three-scope engine mirror re-read at
SubobjectDataSubsystem.cpp:2043–2072 matches the proposed steps exactly; SetParent overloads
ENGINE_API at SCS_Node.h:186/:189 under WITH_EDITOR (:184).

### list_components: include inherited and native components
Currently iterates own `SCS->GetAllNodes()` only (MifBridgeComponents.cpp:147–152), so agents
cannot discover legal parents for the change above. Add ancestor-SCS nodes
(`source:"inherited"`, owning BP named) and CDO native components (`source:"native"`, class +
name; template path = the CDO subobject path for set_property). Read-only walk of the same
UPROPERTY data — no new API. Verify: on a Character-derived BP the response contains
`CharacterMesh0/Mesh` with `source:"native"`. **U4 E5 R5 → tier 0 companion.**
**Phase-2 verdict**: CONFIRMED — own-SCS-only iteration verbatim at MifBridgeComponents.cpp:
147–152 (`SCS->GetAllNodes()` loop at :152). Read-only walk of UPROPERTY data; no new API needed,
as claimed.

### list_variables / rename_variable / remove_variable / set_variable_default: scope=local
`add_variable` ALREADY creates locals (`scope=local` + `function`,
MifBridgeIntrospect.cpp:797–825 calling
`FBlueprintEditorUtils::AddLocalVariable` — BlueprintEditorUtils.h:961), but nothing can see or
edit them afterwards: list reads `Blueprint->NewVariables` only (:258), rename/remove/default
are member-only (:870–964). Extend each with `scope:"member"(default)|"local"` + `function`
(required for local), erroring on the missing-function case by name. Engine APIs, all
UNREALED_API method-level, BlueprintEditorUtils.h:
```cpp
static UNREALED_API void RenameLocalVariable(UBlueprint* InBlueprint, const UStruct* InScope, const FName InOldName, const FName InNewName);   // :1040
static UNREALED_API void RemoveLocalVariable(UBlueprint* InBlueprint, const UStruct* InScope, const FName InVarName);                          // :970
static UNREALED_API FBPVariableDescription* FindLocalVariable(const UBlueprint* InBlueprint, const UEdGraph* InScopeGraph, const FName InVariableName, class UK2Node_FunctionEntry** OutFunctionEntry = NULL); // :990
```
Local list + default-write route: locals live on the entry node —
`UPROPERTY() TArray<FBPVariableDescription> LocalVariables;` on `UCLASS(MinimalAPI)
UK2Node_FunctionEntry` (K2Node_FunctionEntry.h:35–50; public data member of a MinimalAPI class —
direct member access compiles without export; `FBPVariableDescription::DefaultValue` is
Engine/Blueprint.h:293). Default-write = `FindLocalVariable(..., &Entry)` → `Entry->Modify()` →
`Desc->DefaultValue = value` → MarkStructural (the same pattern set_variable_default already
uses for members at MifBridgeIntrospect.cpp:956–964). The UStruct* scope for the :970/:1040
overloads comes from the function graph's generated `UFunction`
(`GeneratedClass->FindFunctionByName(GraphName)`), matching how the engine's details panel calls
them. Verify: add local → list shows it with scope:"local" → rename → get/set nodes still
resolve (`find_nodes` count unchanged) → compile 0/0. **U4 E4 R4 → tier 0 (roadmap "locals are
currently write-once and invisible").**
**Phase-2 verdict**: CONFIRMED — all three UNREALED_API signatures verbatim
(RenameLocalVariable :1040, RemoveLocalVariable :970, FindLocalVariable graph-overload :990;
AddLocalVariable :961 also re-verified). Plugin-side claims exact: list reads NewVariables only
(MifBridgeIntrospect.cpp:258), add_variable local branch calls AddLocalVariable (:820),
rename/remove/default are member-only (:912/:935/:956–964). UCLASS(MinimalAPI)
UK2Node_FunctionEntry :35–36 with public `TArray<FBPVariableDescription> LocalVariables` :49–50;
`FString DefaultValue` Blueprint.h:293 (struct FBPVariableDescription :253).

### rename_function / remove_function: accept macro graphs
`rename_function` already renames any graphId via `FBlueprintEditorUtils::RenameGraph`
(MifBridgeNodes2.cpp:443–447 comment documents this; RenameGraph = BlueprintEditorUtils.h:458)
— macro RENAME works today by accident of design; document it and add a `graphType` field to
the response. `remove_function` however searches `Blueprint->FunctionGraphs` only
(MifBridgeFunctions.cpp:93) — superseded by the remove_graph proposal above (keep
remove_function as-is for compatibility). **U2 E5 R5 → tier 2 (docs + one response field).**
**Phase-2 verdict**: CONFIRMED — rename_function accepts any graphId (graphId branch,
MifBridgeNodes2.cpp:459–463) and the RenameGraph asymmetry comment is at :441–447 as cited;
remove_function's FunctionGraphs-only search verbatim at MifBridgeFunctions.cpp:93 (it already
calls FBlueprintEditorUtils::RemoveGraph at :108, so remove_graph supersedes cleanly).

## Compositions (no new endpoint needed)

- **set_anim_transition_rule**: NOT an endpoint. The transition's rule graph is a K2-schema
  graph already reachable — `GatherGraphs` recurses `UEdGraphNode::GetSubGraphs()`
  (MifBridgeHandlers.h:63–65 documents state machines/states/transition rules as reachable), and
  `UAnimStateTransitionNode::GetBoundGraph()` feeds it (AnimStateTransitionNode.h:26, 138).
  Author the rule with existing endpoints: `list_nodes` on the rule graphId → find the
  TransitionResult node (`UAnimGraphNode_TransitionResult`, AnimGraphNode_TransitionResult.h:14)
  → wire comparisons into its bool input with add_function_call/add_variable_get/connect_pins
  (connect_pins works there because the rule schema derives from K2 — and the schema fix makes
  it exact).
- **Transition/state details-panel properties** (`CrossfadeDuration` AnimStateTransitionNode.h:39,
  `StateEntered/StateLeft` notifies AnimStateNode.h:36–43): `set_property` on the node's object
  path — blocked today only by the known "SerializeNode never emits GetPathName()" reflection-
  addressing roadmap item (another axis owns it); once nodes carry `objectPath`, zero new code.
- **add_select / add_multigate / add_do_once_multi_input / add_switch_name / add_make_set /
  add_knot / add_get_class_defaults / add_set_fields_in_struct / add_generic_create_object**:
  all `add_node_by_class` + documented `init` keys (table in that entry) — no dedicated
  endpoints. `IK2Node_AddPinInterface` nodes (Select/Sequence/MultiGate/DoOnceMultiInput/
  MakeContainer, K2Node_AddPinInterface.h:18) grow pins via the EXISTING `add_pin`.
- **Bind a dispatcher to an existing function from any graph**: add_create_delegate →
  connect_pins (delegate out → add_bind_dispatcher's Delegate pin) → compile. Documented as the
  intended recipe in the add_create_delegate entry.
- **Macro rename**: rename_function already accepts any graphId and routes through
  `FBlueprintEditorUtils::RenameGraph` — works for macro graphs today (behaviour-change entry
  only documents it).
- **Plugin K2 nodes** (K2Node_EnhancedInputAction, K2Node_LatentAbilityCall, ...): spawnable via
  add_node_by_class IF their module is loaded; the endpoint's unknown-class error names the
  module so the failure is diagnosable. No per-plugin endpoints.
- **Cooked function inventory**: `describe_class` (functions/flags) + new `disassemble_function`
  (bodies) + new `describe_anim_class` (anim structure) together close the "what can be read
  from a cooked BP" question — no further cooked-read endpoint needed on this axis.

## Negative results / gaps (for 03_GAPS_AND_RISKS.md)

_Phase-2 (2026-07-26): all 7 negatives re-verified against source; NONE overturned._
_#1 UI-lock verbatim at SubobjectDataSubsystem.cpp:1818–1824 and attach internals at :2046–2072;
class export SUBOBJECTDATAINTERFACE_API at SubobjectDataSubsystem.h:94, methods :200/:212/:268/
:321 (ReparentSubobjects itself at :301). #2 IsValid has no macro at K2Node_CreateDelegate.h:59
while neighbours :62–77 are exported. #3 SetEnum bare virtual at K2Node_Select.h:139 on a
UCLASS(MinimalAPI) class (:31–32). #4 MoveGraphBeforeOtherGraph signature verbatim at
BlueprintEditorUtils.h:1215. #5 KismetCompiler's only reference is usage
(KismetCompiler.cpp:5087); header + method-level SCRIPTDISASSEMBLER_API confirmed in
Developer/ScriptDisassembler (Build.cs: Core+CoreUObject). #6 all three anim classes
UCLASS(MinimalAPI) confirmed (AnimationStateMachineGraph.h:15, AnimStateNode.h:22,
AnimStateTransitionNode.h:19–20). #7 PM-004 present in docs/01_POSTMORTEMS.md:8; the
Pins.Num()==0 guard verbatim at MifBridgeCommon.cpp:1324–1327._

1. **USubobjectDataSubsystem reparenting is UI-locked in Blueprint contexts.**
   `ReparentSubobjects` hard-requires a preview actor:
   `if (Params.BlueprintContext) { if (!Params.ActorPreviewContext) { UE_LOG(... "Failed to
   reparent: In a blueprint context there must be an actor preview!"); return false; } }` —
   Editor/SubobjectDataInterface/Private/SubobjectDataSubsystem.cpp:1818–1824. The preview actor
   is an SCS-editor construct a headless bridge does not have. Consequence: reparent_component
   is specified over direct ENGINE_API SCS calls (SCS_Node.h:126–189), which is also exactly
   what the subsystem itself does underneath (SubobjectDataSubsystem.cpp:2046–2072).
   `AddNewSubobject`/`AttachSubobject`/`DeleteSubobjects`/`RenameSubobject`
   (SubobjectDataSubsystem.h:200, 321, 212, 268; class exported SUBOBJECTDATAINTERFACE_API :94)
   are callable but add nothing over the direct route here. (Roadmap already notes the module is
   transitive via UnrealEd — no Build.cs change either way.)
2. **UK2Node_CreateDelegate::IsValid is unexported** (K2Node_CreateDelegate.h:59 — MinimalAPI
   class, no method macro, unlike its neighbours :62–77). Validation must use
   `GetDelegateSignature() != nullptr` (exported :64).
3. **UK2Node_Select::SetEnum is unexported** (K2Node_Select.h:139 — no BLUEPRINTGRAPH_API,
   MinimalAPI class). A typed add_select endpoint cannot call it; the reflection route
   (`init: {Enum: <path>}` + `ReconstructNode()`) is the supported path.
4. **No engine API moves a function/graph between Blueprints.** The only "move" in
   FBlueprintEditorUtils is ordering within one Blueprint:
   `static UNREALED_API bool MoveGraphBeforeOtherGraph(UEdGraph* Graph, int32 NewIndex, bool
   bDontRecompile);` (BlueprintEditorUtils.h:1215). The editor does cross-BP moves via
   copy/paste — a documented MifBridge non-goal. Dead end; do not attempt.
5. **FKismetBytecodeDisassembler is NOT in Editor/KismetCompiler** (the two references there are
   includes/usage: KismetCompiler.cpp, UnrealEdSrv.cpp). It lives in
   `Developer/ScriptDisassembler/Public/ScriptDisassembler.h` with method-level
   SCRIPTDISASSEMBLER_API (:37–52) — usable, but it IS a new Build.cs module dependency
   (`ScriptDisassembler`, Core+CoreUObject only).
6. **Typed AnimBP authoring requires accepting one of two costs**: link `AnimGraph`
   (editor-only module, roadmap already budgets it) for `Cast<UAnimStateNode>` etc., or stay
   reflection-only (class-name string checks, GetSubGraphs for discovery) with zero new deps.
   The three anim endpoints above are written to work reflection-only; Phase-2 may still choose
   to link AnimGraph for cleaner code. All state-machine object-model classes are
   UCLASS(MinimalAPI) (AnimationStateMachineGraph.h:15, AnimStateNode.h:22,
   AnimStateTransitionNode.h:19), so without the link no direct member access — only UPROPERTY
   reflection (`BoundGraph`, `CrossfadeDuration`, ...) and exported/virtual methods.
7. **Function terminator spawning stays forbidden** (generic endpoint denylist) — PM-004's
   root cause is structural: `UK2Node_FunctionResult::AllocateDefaultPins` has no FindPin guard
   and `PostPlacedNewNode` pre-allocates via SyncWithEntryNode (docs/01_POSTMORTEMS.md PM-004;
   mirrored by the `Node->Pins.Num() == 0` guard in MifBridgeCommon.cpp:1315–1327).

## UNVERIFIED

- `UK2Node_MathExpression` generic-spawn safety: BLUEPRINTGRAPH_API class
  (K2Node_MathExpression.h:33) but it derives from Composite and rebuilds an inner parsed graph;
  its PostPlacedNewNode/Expression flow was not read. Left OFF the safe list — needs a read of
  K2Node_MathExpression.cpp before allowing.
- Whether the `AnimGraph` module is loaded at editor startup or only on first AnimBP/Persona
  use — handlers should `FModuleManager::LoadModuleChecked(TEXT("AnimGraph"))` defensively;
  load-order behaviour not verified.
- ~~`FStringOutputDevice` (capture device for disassemble_function)~~ — RESOLVED by Phase-2:
  `class FStringOutputDevice : public FString, public FOutputDevice`,
  Runtime/Core/Public/Containers/UnrealString.h:2387–2414, fully inline (ctor, Serialize
  override, operator+= all header-bodied) — zero export concerns, usable as cited.
- Exact node set created by `CreateDefaultNodesForGraph` for UAnimationStateMachineSchema
  (entry node class) and UAnimationStateGraphSchema (state result class) — inferred from the
  PostPlacedNewNode call sites (AnimStateNode.cpp:130, AnimGraphNode_StateMachineBase.cpp:151)
  and the UPROPERTY `EntryNode` (AnimationStateMachineGraph.h:22); the two cpp functions were
  not read. Verify counts empirically in the numeric checks.
- Conduits/aliases (`UAnimStateConduitNode`, `UAnimStateAliasNode` — headers listed, not read)
  — plausible follow-on endpoints; not proposed this pass.
- `UK2Node_AsyncAction` factory discovery (enumerating all UBlueprintAsyncActionBase-returning
  static factories for a list endpoint) — mechanism not verified; add_async_action requires the
  caller to name the factory instead.
- EnhancedInput/GameplayAbilities plugin-module enabled/loaded state for THIS project was not
  checked against the .uproject — only relevant to spawning their K2 nodes via
  add_node_by_class (fails with a clean error if unloaded).
- `FBakedStateExitTransition` field list (AnimStateMachineTypes.h:247) — struct located, fields
  not extracted; describe_anim_class should serialize it via reflection anyway.

## Coverage log

**Covered this sweep**: UK2Node census (BlueprintGraph complete, 111 decls; AnimGraph 80+17;
plugins listed); spawnability triage incl. denylist rationale (PM-004); generic add_node_by_class
+ companion list endpoint; CreateDelegate; AsyncAction family; FBlueprintEditorUtils walk for
graph lifecycle (CreateNewGraph/AddMacroGraph/RemoveGraph/RenameGraph/MoveGraphBeforeOtherGraph)
and variables (member retype, full local-variable lifecycle incl. the discovery that
add_variable scope=local already exists); SCS attach-to-native/inherited + reparent (direct
ENGINE_API route, subsystem UI-lock documented); bytecode disassembly (module located, exports
verified, cooked behaviour reasoned from UStruct::Script); cooked AnimBP structured read
(BakedStateMachines/AnimNodeProperties); AnimBP state-machine authoring trio + rule-graph
composition; connect_pins schema fix; local/scope behaviour changes; existing-handler diffs
(add_component, list_components, list/rename/remove/default variables, rename/remove_function,
describe_class, ConnectPinsChecked, PlaceAndInit, K2()).

**Remaining for Phase-2 on this axis**: BlendSpace graph nodes
(UAnimGraphNode_BlendSpaceGraph(Base) — creation path unread); conduit/alias state nodes;
K2Node_AnimGetter / K2Node_TransitionRuleGetter / K2Node_PlayMontage (AnimGraph K2 nodes);
UbergraphPages (AddUbergraphPage — multiple event graphs, value unclear);
Editor/UnrealEd/Public/Kismet2/KismetEditorUtilities.h full walk (only used transitively here);
KismetDebugUtilities.h (breakpoint/watch READ endpoints — stepping is impossible, but watch
values during PIE may not be); MovieSceneTools K2 nodes (Sequencer axis overlap);
linked-anim-layer nodes (UAnimGraphNode_LinkedAnimLayer); per-node "advanced" pin exposure
(UK2Node::GetPinMetaData); FBlueprintActionDatabase as an alternative node-discovery backend for
list_node_classes.
