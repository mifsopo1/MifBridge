# R1 — Details-panel parity for `set_property`

**Scope.** Everything the UE 5.3 Details panel can do to a property that MifBridge cannot, and the
endpoint / behaviour changes that close each gap. Driven by the single most-wanted capability:
*"assigning anything in the Details panel."*

**Engine:** `D:/UE532/Engine/Source` (5.3 source fork). **Bridge:** `D:/DDS2SDK/Game/Plugins/MifBridge`.
**Mode:** READ-ONLY. No source edited, no build run. Live probes were read-only (`get_property`,
`list_object_properties`, `describe_class`) against the running bridge on 127.0.0.1:8791, 2026-07-28.

Every claim below carries a verbatim signature, a `file:line`, an **export-macro** check and an
**access-specifier** check. Anything not verified against engine source is in
[§UNVERIFIED](#unverified) and nowhere else.

> **Bridge line numbers are volatile; engine line numbers are not.** MifBridge source was being
> edited by another agent *while this audit ran*. Every `MifBridge*` citation here was re-verified
> against the on-disk file at **2026-07-28 23:10** and is anchored to a named symbol as well as a
> line, so a drifted number is still resolvable. One structural change landed mid-audit and is
> reflected throughout: the dot-path walker existed in **three** copies (`MifBridgeNodes5.cpp`
> `ResolvePropertyPath`, `MifBridgeNodes6.cpp` `ResolveReadPropertyPath`, `MifBridgeInherited.cpp`
> `ResolvePropertyPathLocal`) and is now **one** — `MifBridge::ResolvePropertyPath`,
> `MifBridgeCommon.cpp:943`, declared `MifBridgeHandlers.h:173`. That materially cheapens
> [G1](#2-g1--element-level-addressing-and-container-element-lifecycle): the grammar extension is now
> a single-site change. `D:/UE532` was not touched by anyone; engine citations are stable.

---

## 0. What already exists — do not re-propose

Read before writing this report, so none of it is claimed as new:

| Already shipped | Where |
|---|---|
| `set_property` — target resolve → dot-walk → `ImportText_Direct` into a **scratch** buffer → `Modify`/`PreEditChange`/publish/`PostEditChangeProperty`, plus post-write re-export verification | `MifBridgeNodes5.cpp:674-941` |
| JSON→property-text converter (arrays/sets/maps/structs/enums/objects, refusals that state the accepted form) | `MifBridgeNodes5.cpp:247-511` |
| Typed-JSON emitter shared by reader and writer (`PropertyValueToTypedJson`) | `MifBridgeNodes5.cpp:518-658` |
| Dot-path walker (structs in place, object-ref hops, containers **leaf-only**) | `MifBridgeCommon.cpp:943-1015` — **unified mid-audit**; was three copies (`Nodes5` `ResolvePropertyPath`, `Nodes6` `ResolveReadPropertyPath`, `Inherited` `ResolvePropertyPathLocal`), now one, declared `MifBridgeHandlers.h:173` |
| `get_property` / `list_object_properties` | `MifBridgeNodes6.cpp:67-169` |
| Inherited-component overrides on a **child Blueprint** via `UInheritableComponentHandler` (`get_/override_/revert_inherited_component`) | `MifBridgeInherited.cpp` (whole file) |
| Documented objectPath routes: `Default__<Class>` CDOs, `<Name>_GEN_VARIABLE` SCS templates, widget templates via `blueprintId`+`widgetName`, graph-node objects, placed actors | `docs/02_GOTCHAS.md` §5d |
| PM-003 discipline (never `ImportText` into a live address) | `MifBridgeNodes5.cpp:70-90` (`FScratchValue`), `:814-845` (the write bracket) |

Two of those are load-bearing for what follows and are **stricter than the engine**, deliberately:

* The bridge imports into `FScratchValue` and publishes with `CopyCompleteValue`. The panel imports
  **straight into the live address** — `Property->ImportText_Direct(Buffer, ValueAddress, Object, PortFlags);`
  (`Editor/PropertyEditor/Private/PropertyTextUtilities.cpp:34`). **Do not "fix" the bridge to match
  the panel.** PM-003 exists because that live-address import destroys the value it fails to set.
* The bridge verifies by re-export. The panel does not verify at all.

---

## 1. Ranked gap list

Ranked by **how much Details-panel work each unlocks**. The "defect?" column flags gaps that are not
missing features but wrong behaviour in code that ships today — those should be fixed first
regardless of rank.

| # | Gap | Unlocks | Defect in shipped code? |
|---|---|---|---|
| **G1** | **Element-level addressing + container element lifecycle** — no index/key grammar, no add/insert/remove/clear/swap | Every row *inside* any `TArray`/`TMap`/`TSet`/C-array on every object. Today the only move is a whole-container rewrite. | No — pure gap |
| **G2** | **Per-property override flags (EditCondition)** — a value written behind an unset gate is silently ignored by the engine | 837 `editcondition`-gated `UPROPERTY`s in `Runtime/**.h`; 423 of them in `FPostProcessSettings` alone | **Yes** — silent no-op, the exact banned bug class |
| **G3** | **Editability + metadata surface** (`describe_property`) — the bridge cannot report `CPF_Edit`/`EditConst`/`DisableEditOnInstance`/`Transient`/clamps/`Category`/`EditCondition` for any property | The *discovery* layer. Without it an agent cannot know G2, G7 or G8 apply. Cheap; multiplies everything else. | No — pure gap |
| **G4** | **The notification bracket** — `PostEditChangeProperty` only, `MemberProperty` = leaf, no array index, no rehash, no archetype value propagation | Correctness of **every write the bridge already makes**, incl. all 40 `PostEditChangeChainProperty` overrides in `Runtime/Engine/Private` | **Yes** |
| **G5** | **Instance vs template on a placed actor** — construction-script rerun trashes the component the bridge just wrote and then reads back | Every placed-actor component edit. Currently can report `verified:true` off a `TRASH_*` object. | **Yes** — latent dangling read |
| **G6** | **Reset to Default / diff vs default** (the yellow arrow) | "What does this object actually override?" — currently unanswerable | No — pure gap |
| **G7** | **Instanced / EditInline subobject creation** — the `+` that instantiates a new `UObject` of a chosen class into the property | 316 `Instanced`/`EditInline` metas in `Runtime/**.h` | No — pure gap |
| **G8** | **Metadata clamps not enforced** (`ClampMin`/`ClampMax`/`Multiple`/`ArrayClamp`) | 899 `ClampMin` + 736 `UIMin` metas in `Runtime/**.h`. Bridge can write values the panel refuses. | Partial — silent out-of-range write |
| **G9** | **Multi-target writes** — the panel edits every selected object at once | Batch edits; also the only correct way to express "set this on all 12 placed lamps" | No — pure gap |

Numeric verification of the counts is in [§9](#9-numeric-verification-commands).

**Correctness-first order:** G4 → G5 → G2 → then the coverage work G1 → G3 → G6 → G7 → G8 → G9.

---

## 2. G1 — Element-level addressing and container element lifecycle

### 2.1 What the panel does that the bridge cannot

The panel gives every element its own row: `Keys[2].Value`, `FloatCurves[1]`, a map entry keyed by
`Roughness`, plus `+` / `x` / insert / duplicate / clear / drag-reorder.

The bridge's walker refuses containers mid-path, for reads and writes alike — and since the
three copies were unified into `MifBridge::ResolvePropertyPath` there is now exactly **one** place to
fix, which is the single biggest reason this gap is now cheap:

```
MifBridgeCommon.cpp:1005-1011
  else
  {
      OutError = FString::Printf(
          TEXT("segment '%s' is a %s — arrays/maps/sets are not walkable mid-path and may only be the LAST segment"),
          *Segs[i], *Prop->GetClass()->GetName());
      return false;
  }
```

Live, 2026-07-28 (against the pre-unification build still running in the editor, hence the older
wording — the refusal itself is unchanged):

```
POST /api/get_property {"objectPath":"/Script/Engine.Default__StaticMeshComponent","propertyPath":"OverrideMaterials[0]"}
  -> {"ok":false,"error":"property 'OverrideMaterials[0]' not found on 'StaticMeshComponent'"}
POST /api/get_property {... "propertyPath":"OverrideMaterials.0"}
  -> {"ok":false,"error":"segment 'OverrideMaterials' is not a struct or object ref (arrays/maps/sets unsupported mid-path)"}
```

The first error is actively misleading: `OverrideMaterials` **does** exist; only the grammar is
unparsed. That error text must change with the grammar.

**Also missing: static C-arrays.** `ArrayDim > 1` is handled by the typed-JSON emitter
(`MifBridgeNodes5.cpp:522-536`) but **not by either walker**, so the two real targets the roadmap
names are unreachable for a different reason than assumed:

* `UCurveVector::FloatCurves` is `FRichCurve FloatCurves[3]` — `Curves/CurveVector.h:36` — a C-array,
  not a `TArray`. `FRichCurve::Keys` is `TArray<FRichCurveKey> Keys;` — `Curves/RichCurve.h:356`.
* `UBlendSpace::BlendParameters` is `struct FBlendParameter BlendParameters[3];` — `Animation/BlendSpace.h:862`.
* Material-instance parameters are `TArray<FScalarParameterValue>` whose element is
  `FMaterialParameterInfo ParameterInfo; float ParameterValue;` — `Materials/MaterialInstance.h:83,86`.
  There is no map here: selecting "the Roughness one" is a **linear find on a member**, not a key
  lookup. Any grammar that pretends otherwise will be wrong.

### 2.2 Proposed grammar (extends the existing dot-walker; no new parser)

One new token type. The walker already splits on `.` (`MifBridgeCommon.cpp:950`); the change is that
each **segment** may now carry a trailing accessor chain.

```
segment    := name accessor*
accessor   := '[' index ']'          // FArrayProperty | FSetProperty | ArrayDim>1  -> integer only
            | '[' member '=' text ']'// FArrayProperty of struct -> linear find, first match
            | '{' keytext '}'        // FMapProperty -> the KEY
            | '[' keytext ']'        // FMapProperty -> alias for {keytext}
```

Disambiguation is by **container type**, not by the text, so nothing is ambiguous:

| Leaf-so-far is | `[N]` means | `[A=B]` means | `{K}` means |
|---|---|---|---|
| `FArrayProperty` | element N | first element whose member `A` exports as `B` | error: "use `[N]`; this is an array, not a map" |
| `FSetProperty` | the Nth **valid** index (sparse-aware) | error | error |
| `ArrayDim > 1` | static-array element N | error | error |
| `FMapProperty` | the entry with key `N` | error | the entry with key `K` |
| anything else | error naming the actual `FProperty` class | — | — |

Worked examples that must resolve after this change:

```
FloatCurves[1].Keys[0].Value                       UCurveVector    (C-array -> TArray -> struct member)
BlendParameters[2].Max                             UBlendSpace     (C-array -> struct member)
ScalarParameterValues[ParameterInfo.Name=Roughness].ParameterValue
                                                   UMaterialInstanceConstant
ScalarParameterValues[0].ParameterValue            same, by index
OverrideMaterials[1]                               UStaticMeshComponent (leaf is the element itself)
Settings.WeightedBlendables.Array[0].Weight        APostProcessVolume
SomeMap{Alpha}.Threshold                           TMap<FName, FThing>
```

**Set indexing is by valid index, not raw index.** `FScriptSetHelper` is sparse; the existing reader
already skips holes (`MifBridgeNodes5.cpp:615-618` (set) / `:626-629` (map)). `[N]` on a set must mean the Nth element in
iteration order, and the response must say so (`ordering: "iteration"`), because that order is not
stable across a rehash.

### 2.3 Engine APIs for the walk and for add/remove

All three helpers are header-only classes in `Runtime/CoreUObject/Public/UObject/UnrealType.h` with
**no class-level export macro** (nothing to export — every member below is inline except the two
`Rehash()` overloads, which carry `COREUOBJECT_API`). Access verified against the `public:` /
`private:` markers in the same header.

| Verbatim signature | file:line | Export | Access |
|---|---|---|---|
| `class FScriptArrayHelper` | `UnrealType.h:4047` | none (header-only) | `public:` @ `:4064`, `private:` @ `:4317` |
| `FORCEINLINE FScriptArrayHelper(const FArrayProperty* InProperty, const void* InArray)` | `UnrealType.h:4070` | inline | public |
| `FORCEINLINE bool IsValidIndex( int32 Index ) const` | `UnrealType.h:4080` | inline | public |
| `FORCEINLINE int32 Num() const` | `UnrealType.h:4088` | inline | public |
| `FORCEINLINE uint8* GetRawPtr(int32 Index = 0)` | `UnrealType.h:4099` | inline | public |
| `FORCEINLINE uint8* GetElementPtr(int32 Index = 0)` | `UnrealType.h:4115` | inline | public |
| `void Resize(int32 Count)` | `UnrealType.h:4162` | inline | public |
| `int32 AddValues(int32 Count)` | `UnrealType.h:4184` | inline | public |
| `FORCEINLINE int32 AddValue()` | `UnrealType.h:4194` | inline | public |
| `void InsertValues( int32 Index, int32 Count = 1)` | `UnrealType.h:4223` | inline | public |
| `void EmptyValues(int32 Slack = 0)` | `UnrealType.h:4234` | inline | public |
| `void RemoveValues(int32 Index, int32 Count = 1)` | `UnrealType.h:4252` | inline | public |
| `void SwapValues(int32 A, int32 B)` | `UnrealType.h:4277` | inline | public |
| `class FScriptMapHelper` | `UnrealType.h:4444` | none | `public:` @ `:4463`, `private:` @ `:5073` |
| `FORCEINLINE bool IsValidIndex(int32 Index) const` | `UnrealType.h:4482` | inline | public |
| `FORCEINLINE int32 Num() const` | `UnrealType.h:4492` | inline | public |
| `FORCEINLINE int32 GetMaxIndex() const` | `UnrealType.h:4504` | inline | public |
| `FORCEINLINE uint8* GetPairPtr(int32 Index)` | `UnrealType.h:4521` | inline | public |
| `FORCEINLINE uint8* GetKeyPtr(int32 Index)` | `UnrealType.h:4545` | inline | public |
| `FORCEINLINE uint8* GetValuePtr(int32 Index)` | `UnrealType.h:4567` | inline | public |
| `void EmptyValues(int32 Slack = 0)` | `UnrealType.h:4680` | inline | public |
| `int32 AddDefaultValue_Invalid_NeedsRehash()` | `UnrealType.h:4704` | inline | public |
| `void RemoveAt(int32 Index, int32 Count = 1)` | `UnrealType.h:4742` | inline | public |
| `COREUOBJECT_API void Rehash();` | `UnrealType.h:4764` | **COREUOBJECT_API** | public |
| `int32 FindMapIndexWithKey(const void* PairWithKeyToFind, int32 IndexHint = 0) const` | `UnrealType.h:4811` | inline | public |
| `uint8* FindMapPairPtrFromHash(const void* KeyPtr)` | `UnrealType.h:4872` | inline | public |
| `uint8* FindValueFromHash(const void* KeyPtr)` | `UnrealType.h:4888` | inline | public |
| `void AddPair(const void* KeyPtr, const void* ValuePtr)` | `UnrealType.h:4902` | inline | public |
| `bool RemovePair(const void* KeyPtr)` | `UnrealType.h:5005` | inline | public |
| `class FScriptSetHelper` | `UnrealType.h:5238` | none | `public:` @ `:5242`, `private:` @ `:5677` |
| `FORCEINLINE uint8* GetElementPtr(int32 Index)` | `UnrealType.h:5314` | inline | public |
| `void EmptyElements(int32 Slack = 0)` | `UnrealType.h:5391` | inline | public |
| `int32 AddDefaultValue_Invalid_NeedsRehash()` | `UnrealType.h:5412` | inline | public |
| `void RemoveAt(int32 Index, int32 Count = 1)` | `UnrealType.h:5435` | inline | public |
| `COREUOBJECT_API void Rehash();` | `UnrealType.h:5454` | **COREUOBJECT_API** | public |
| `int32 FindElementIndex(const void* ElementToFind, int32 IndexHint = 0) const` | `UnrealType.h:5497` | inline | public |
| `void AddElement(const void* ElementToAdd)` | `UnrealType.h:5575` | inline | public |
| `bool RemoveElement(const void* ElementToRemove)` | `UnrealType.h:5608` | inline | public |
| `COREUOBJECT_API void PerformOperationWithSetter(void* OutContainer, void* DirectPropertyAddress, TFunctionRef<void(void*)> DirectValueAccessFunc) const;` | `UnrealType.h:560` | **COREUOBJECT_API** | `public:` @ `:352` (before `protected:` @ `:592`) |
| `virtual bool HasSetterOrGetter() const` | `UnrealType.h:271` | inline virtual | `public:` @ `:181` (before `private:` @ `:294`) |

`FindMapIndexWithKey` wants **a pointer to a map pair whose key part is filled in**
(`UnrealType.h:4805-4811`, "The address of a map pair which contains the key to search for"), which is
awkward. Use `FindMapPairPtrFromHash(KeyPtr)` / `FindValueFromHash(KeyPtr)` instead — they take a bare
key pointer. Both require the key property to be hashable; `AddPair` passes
`[LocalKeyPropForCapture](const void* ElementKey) { return LocalKeyPropForCapture->GetValueTypeHash(ElementKey); }`
to `Map->Add` (`UnrealType.h:4910`), so a key type without `CPF_HasGetValueTypeHash` must be refused
by name rather than crashed on.

### 2.4 Three hazards that must be encoded, not discovered

1. **Rehash after any key/element mutation.** The panel does exactly this and only when the value
   actually changed:
   ```
   PropertyHandleImpl.cpp:522-534
     if (NodeProperty->GetOwner<FMapProperty>()) { ... MapHelper.Rehash(); }
     else if (NodeProperty->GetOwner<FSetProperty>()) { ... SetHelper.Rehash(); }
   ```
   `AddDefaultValue_Invalid_NeedsRehash()` names the requirement in the symbol. Skipping it leaves a
   map that `Find` cannot see its own entries in.

2. **Pointer invalidation.** `FScriptArrayHelper::GetRawPtr` returns a pointer into the array's data
   block (`UnrealType.h:4099-4110`). Any subsequent `AddValues` / `InsertValues` / `RemoveValues`
   reallocates. Resolve → mutate → **re-resolve** before reading back. Same for map/set indices after
   `Rehash()`.

3. **PM-003 still applies at element granularity.** Never `ImportText_Direct` into
   `Helper.GetRawPtr(i)`. Import into `FScratchValue(Inner)` and publish with
   `Inner->CopyCompleteValue(Helper.GetRawPtr(i), Scratch.Mem)`. `FScratchValue` already exists
   (`MifBridgeNodes5.cpp:73-90`) and is constructed from any `FProperty`, so `AP->Inner`,
   `MP->KeyProp`, `MP->ValueProp` and `SP->ElementProp` all work unchanged.

Two further behaviours the panel has and a naive implementation will not:

* **Duplicate refusal.** Editing a set element or map key to a value that already exists is refused
  outright: `ShowInvalidOperationError(LOCTEXT("DuplicateSetElement", "Duplicate elements are not allowed in Set properties."));`
  (`PropertyHandleImpl.cpp:389`) and `LOCTEXT("DuplicateMapKey", "Duplicate keys are not allowed in Map properties.")`
  (`PropertyHandleImpl.cpp:446`). `AddPair` **overwrites** silently, so without this check
  `edit_container` turns "add" into "replace" with no notice.
* **Native setters on structural ops.** The panel routes add/insert/remove through
  `Array->PerformOperationWithSetter(Obj, Addr, [...])` (`PropertyHandleImpl.cpp:1132` array,
  `:1148` set, `:1166` map) so a `UPROPERTY(Setter=...)` container is written via its
  setter. 288 `UPROPERTY(... Setter= ...)` declarations exist in `Runtime/**.h`. Note the panel does
  **not** do this for a plain scalar value write — `TextToPropertyHelper` imports directly
  (`PropertyTextUtilities.cpp:32-39`) — so this belongs to `edit_container`, not to `set_property`.
* **`CPF_EditFixedSize`** (`ObjectMacros.h:403`, *"Indicates that elements of an array can be
  modified, but its size cannot be changed"*) hides the panel's add/remove buttons
  (`PropertyEditorHelpers.cpp:679`). 68 occurrences in `Runtime/**.h`. `edit_container` must refuse
  size-changing ops on those and say which flag stopped it.

### 2.5 Endpoint specs

#### `set_property` — grammar extension (no new endpoint)

Element **value** edits stay in `set_property`; only the path grammar changes.

#### `edit_container` — NEW endpoint (element lifecycle)

| Field | Value |
|---|---|
| **1. Engine API** | `FScriptArrayHelper::AddValue/AddValues/InsertValues/RemoveValues/SwapValues/EmptyValues/Resize`; `FScriptMapHelper::AddPair/RemovePair/RemoveAt/EmptyValues/Rehash/FindValueFromHash`; `FScriptSetHelper::AddElement/RemoveElement/RemoveAt/EmptyElements/Rehash/FindElementIndex`; `FProperty::PerformOperationWithSetter`; `FProperty::AllocateAndInitializeValue` / `DestroyAndFreeValue` |
| **2. file:line + export + access** | see the table in §2.3 — every symbol individually checked. The only exported symbols are `FScriptMapHelper::Rehash` (`UnrealType.h:4764`), `FScriptSetHelper::Rehash` (`UnrealType.h:5454`), `FProperty::PerformOperationWithSetter` (`UnrealType.h:560`), `FProperty::AllocateAndInitializeValue` (`UnrealType.h:549`), `FProperty::DestroyAndFreeValue` (`UnrealType.h:552`) — all `COREUOBJECT_API`, all `public:`. Everything else is inline and public. |
| **3. Module** | `CoreUObject` — already a `PublicDependencyModuleName` (`MifBridge.Build.cs:13-17`). **No new module.** |
| **4. Guards** | `RejectUnknownParams` with the full key set; container-kind check with the actual `FProperty` class name in the error; `CPF_EditFixedSize` refusal for size-changing ops; hashability check (`CPF_HasGetValueTypeHash`) before any map/set op; duplicate-key/element check before `AddPair`/`AddElement`; index range check **before** mutation; PM-003 scratch import for every element value; re-resolve the helper after every structural op; `Rehash()` after every map/set mutation. |
| **5. Bucket** | **Default (transacted).** It runs no `FKismetEditorUtilities::CompileBlueprint`, so it must **not** go in `IsSelfManagedEndpoint` (that bucket exists only for full compiles — `00_ARCHITECTURE.md` §Transaction policy). **Exception:** if the target is a widget template reached via `blueprintId`+`widgetName`, the widget branch compiles, exactly as `set_property` does — in that case either refuse the widget form on this endpoint (preferred: widget slots have no interesting containers) or promote to self-managed and mirror `set_property`'s tight inner transaction (`MifBridgeNodes5.cpp:832-845`). Pick one and state it in the header comment. |
| **6. Async** | Synchronous, game thread. No latent work. `Rehash()` is O(n) over the container; `FindElementIndex` is O(n) linear (`UnrealType.h:5497-5530`) — cap `limit`-less scans by refusing containers above a stated element count rather than blocking the editor. |
| **7. Params (+ aliases)** | `objectPath` (alias `actorPath` → note "a placed actor's path IS an objectPath", mirroring `MifBridgeNodes5.cpp:684-685`); `blueprintId` \| `path` + `widgetName`; `propertyPath` (alias `property`); `op` ∈ `add`\|`insert`\|`remove`\|`clear`\|`swap`\|`resize`\|`setKey` — **note the collision**: `op` is `batch`'s routing key, which `RejectUnknownParams` already tolerates centrally (`MifBridgeHandlers.h:44-45`), so this endpoint's op parameter **must** be named something else — use `operation` (alias `action`); `index` (alias `at`); `count` (default 1); `key`; `newKey`; `value`; `swapWith`; `newSize`. |
| **8. Failure modes (+ error text)** | Out of range: `"'OverrideMaterials[7]': index 7 is out of range — the array has 2 elements (valid 0..1). Use edit_container operation=add to grow it."` — the index **and** the actual length, both, always. Wrong container: `"'Settings' is a FStructProperty (FPostProcessSettings), not a container; edit_container operates on TArray/TMap/TSet and fixed-size C-arrays only."` Fixed size: `"'MyArray' is CPF_EditFixedSize (UPROPERTY meta EditFixedSize): elements can be edited but the array cannot be resized. The Details panel hides its add/remove buttons for the same reason."` Duplicate: `"key 'Alpha' already exists in 'SomeMap' (index 3). Maps reject duplicate keys — use operation=setKey to rename it, or set_property on SomeMap{Alpha} to overwrite its value."` Unhashable: `"map key type FVector has no GetTypeHash; UE cannot add to this map through reflection."` |
| **9. Cooked behaviour** | Editor-only endpoint (whole plugin is). Cooked packages: the *containers* still exist and are readable, but every mutation must be refused for a cooked target — the bridge already treats cooked content as read-only (`MifBridgeCooked.cpp`). Additionally `WITH_EDITORONLY_DATA` gates all `FField` metadata (`Field.h:709` `#if WITH_EDITORONLY_DATA`), so `CPF_EditFixedSize` (a flag, not metadata) survives a cook but the `EditFixedSize` *meta string* does not — key the guard off the **flag**, never the metadata. |
| **10. Numeric verification** | Response must carry `elementsBefore` / `elementsAfter` (the endpoint already has `ContainerElementCount`, `MifBridgeNodes5.cpp:103-110`), `index` of the affected element, and `rehashed:true/false`. A structural op that leaves `elementsAfter == elementsBefore` is a **failure**, not a success — same rule as `set_property`'s `bRequestedChange && !bChanged` guard (`MifBridgeNodes5.cpp:885-897`). |

---

## 3. G2 — Per-property override flags (EditCondition)

### 3.1 How the panel actually knows — and the correction to the assumption

The mechanism is **not** the `bOverride_` naming convention. It is `UPROPERTY meta` on the *gated*
property, read here:

```
Editor/PropertyEditor/Private/PropertyNode.cpp:230
  const FString& EditConditionString = MyProperty->GetMetaData(TEXT("EditCondition"));
Editor/PropertyEditor/Private/PropertyNode.cpp:236
  EditConditionExpression = EditConditionParser.Parse(EditConditionString);
Editor/PropertyEditor/Private/PropertyNode.cpp:1237-1239
  if (!bIsEditConst && HasEditCondition()) { bIsEditConst = !IsEditConditionMet(); }
```

`bOverride_` is only a convention used by `FPostProcessSettings`. What ties the pair together is the
metadata plus the companion flag carrying `InlineEditConditionToggle`, which is what makes the panel
draw the flag as the little checkbox on the value's own row instead of as a separate property:

```
Runtime/Engine/Classes/Engine/Scene.h:772-773
  UPROPERTY(EditAnywhere, BlueprintReadWrite, Category=Overrides, meta=(PinHiddenByDefault, InlineEditConditionToggle))
  uint8 bOverride_BloomIntensity:1;
Runtime/Engine/Classes/Engine/Scene.h:1431-1432
  UPROPERTY(interp, BlueprintReadWrite, Category="Lens|Bloom", meta=(ClampMin = "0.0", UIMax = "8.0", editcondition = "bOverride_BloomIntensity", DisplayName = "Intensity"))
  float BloomIntensity;
```

and the same shape without the prefix convention:

```
Runtime/Engine/Classes/Components/StaticMeshComponent.h:115-116
  UPROPERTY(EditAnywhere, AdvancedDisplay, BlueprintReadOnly, Category=LOD, meta=(editcondition = "bOverrideMinLOD"))
  int32 MinLOD;
Runtime/Engine/Classes/Components/StaticMeshComponent.h:226
  uint8 bOverrideMinLOD:1;
```

**Case does not matter.** `MinLOD` spells it `editcondition` (lowercase) and the panel reads
`TEXT("EditCondition")`. Metadata is keyed by `FName`:

```
Runtime/CoreUObject/Private/UObject/Field.cpp:749-757
  const FString* FField::FindMetaData(const TCHAR* Key) const { return FindMetaData(FName(Key, FNAME_Find)); }
  const FString* FField::FindMetaData(const FName& Key) const { return (MetaDataMap ? MetaDataMap->Find(Key) : nullptr); }
```

`FName` comparison is case-insensitive, so one lookup finds both spellings. Use a `static const FName`
like the engine does (`PropertyNode.cpp:1281`, `PropertyNode.cpp:1292`) — `FName(Key, FNAME_Find)`
returns `NAME_None` for a name never interned, and a `static` avoids re-hashing per call.

**Where the flag lives:** the companion bool is looked up in the *gated property's own owner struct*
(and its supers, since `FindFProperty` uses `TFieldIterator` with `EFieldIterationFlags::Default`,
`UnrealType.h:6743-6755`):

```
Editor/PropertyEditor/Private/EditConditionContext.cpp:55
  BoolProperty = FindFProperty<FBoolProperty>(Property->GetOwnerStruct(), *PropertyToken->PropertyName);
```

and `GetSingleBoolProperty` returns null the moment a **second** property token appears in the
expression (`EditConditionContext.cpp:49-53`) — i.e. the engine itself only auto-toggles when the
condition is one bool.

### 3.2 Proof this is a silent-ignore, not a cosmetic gate

The engine's *runtime* consumers read the flag, not the value:

```
Runtime/Engine/Private/StaticMeshRender.cpp:248
  int32 EffectiveMinLOD = InComponent->bOverrideMinLOD ? InComponent->MinLOD : SMCurrentMinLOD;
Runtime/Engine/Private/StaticMeshRender.cpp:2661
  const int32 EffectiveMinLOD = bOverrideMinLOD ? MinLOD : SMCurrentMinLOD;
Runtime/Engine/Private/Rendering/NaniteResources.cpp:838
  int32 EffectiveMinLOD = Component->bOverrideMinLOD ? Component->MinLOD : SMCurrentMinLOD;
```

```
Runtime/Engine/Private/SceneView.cpp:1440-1442
  #define LERP_PP(NAME) if(Src.bOverride_ ## NAME)  Dest . NAME = FMath::Lerp(Dest . NAME, Src . NAME, Weight);
  #define SET_PP(NAME)  if(Src.bOverride_ ## NAME)  Dest . NAME = Src . NAME;
  #define IF_PP(NAME)   if(Src.bOverride_ ## NAME && Src . NAME)
```

So `set_property` today returns `applied:true, verified:true, changed:true` for
`MinLOD = 2` and the renderer never reads it. The write landed in memory; the **capability** did not.
That is precisely the class of bug the `set_property` verification bracket was built to eliminate,
and the verification bracket cannot see it, because the value genuinely changed.

Confirmed live, 2026-07-28 (read-only):

```
get_property /Script/Engine.Default__StaticMeshComponent MinLOD          -> {"value":"0","typed":0}
get_property /Script/Engine.Default__StaticMeshComponent bOverrideMinLOD -> {"value":"False","typed":false}
```

Both readable; neither reader mentions the other. `list_object_properties` with
`nameContains:"MinLOD"` returns both rows with no relationship between them.

### 3.3 The evaluator: what the bridge may and may not reuse

`FEditConditionParser` **cannot be linked**:

```
Editor/PropertyEditor/Private/EditConditionParser.h:100
  class FEditConditionParser
```

* Export macro: **none** (no `PROPERTYEDITOR_API`).
* Location: `Editor/PropertyEditor/**Private**/` — not in `Public/` (the module's `Public/` listing
  contains no `EditCondition*.h`, no `PropertyNode.h`, no `PropertyHandleImpl.h`).
* `FPropertyNode` is likewise `Editor/PropertyEditor/Private/PropertyNode.h`.
* And `PropertyEditor` is not even a link dependency of MifBridge today: `MifBridge.Build.cs` lists
  `UnrealEd`, and `UnrealEd.Build.cs` names `PropertyEditor` only under
  `DynamicallyLoadedModuleNames` / `PrivateIncludePathModuleNames` / `PublicIncludePathModuleNames`
  (`UnrealEd.Build.cs:125, 270, 291`) — never as a public or private **dependency**.

So the bridge must implement a **restricted evaluator** and be honest about its limits. Restricted is
enough: measured over `Runtime/**.h`, of 837 `editcondition="…"` occurrences, **619** are a bare
single identifier and **94** are a single negated identifier — **713 / 837 = 85.2 %** handled by a
one-bool evaluator. The remaining **122** contain `== != && || < >` and must be reported as
*"condition not evaluated"*, never guessed. (Commands in §9.)

Function-valued conditions exist too — `FEditConditionContext::GetFunction`
(`EditConditionContext.cpp:66-93`) resolves `meta=(EditCondition="CanEditFoo()")` and even static
`Package.Class:Func` forms. Those go in the same "not evaluated, named" bucket.

### 3.4 Behaviour change to `set_property`

Before writing leaf `P` on owner `O`:

1. `static const FName NAME_EditCondition("EditCondition"); const FString* Cond = P->FindMetaData(NAME_EditCondition);`
2. If none → today's behaviour, but **emit `editCondition: null`** so the absence is stated.
3. If present, classify: `Ident` / `!Ident` / complex.
4. For `Ident` / `!Ident`: `FindFProperty<FBoolProperty>(P->GetOwnerStruct(), *Ident)`. If it resolves,
   read it from the **same container address the leaf was resolved in** (for a struct member such as
   `Settings.BloomIntensity` that is the `FPostProcessSettings` memory, not the actor) via
   `BoolProp->GetPropertyValue(BoolProp->ContainerPtrToValuePtr<void>(ContainerAddr))`.
5. Decide by the new `overrideFlag` parameter:

| `overrideFlag` | Behaviour when the gate is unmet |
|---|---|
| `"set"` (**proposed default**) | Set the companion bool to the satisfying value **in the same transaction**, write the value, and report `overrideFlagWritten: {name, from, to}` |
| `"refuse"` | Do not write. Fail naming the flag and its current value. |
| `"ignore"` | Write anyway, but the response carries `overrideFlagUnmet:true` **and** `warning` text. Never silent. |

Default = `"set"` because it is what the panel does when a human types into the field: the value row
is edit-const until the inline toggle is ticked, so a human physically cannot produce the
"value written, flag off" state that the bridge produces today. `"ignore"` exists only for a caller
who *wants* to stage a value behind a disabled gate.

**Whatever the mode, the response must state the flag.** Silently writing a value the engine ignores
is the banned bug class; silently *fixing* it without saying so is the same failure wearing a hat.

The panel's own toggle also propagates to instances, which is the model for `"set"`:

```
Editor/PropertyEditor/Private/PropertyNode.cpp:1318-1364  FPropertyNode::ToggleEditConditionState()
  ... EditConditionProperty->SetPropertyValue(ValuePtr, !OldValue);
  // Propagate the value change to any instances if we're editing a template object
  ... Object->GetArchetypeInstances(ArchetypeInstances);
  // Only propagate if the current value on the instance matches the previous value on the template.
  if (OldValue == CurValue) { EditConditionProperty->SetPropertyValue(ArchetypeValueAddr, !OldValue); }
```

Note also `EditConditionHides` (`PropertyNode.cpp:1366-1373`): with that meta the row is hidden
entirely, not merely greyed. Report it as `editConditionHides:true` so a caller knows the property is
invisible in the panel, not just disabled.

### 3.5 Verification block — `set_property` override-flag handling

| Field | Value |
|---|---|
| **1. Engine API** | `FField::FindMetaData(const FName&)`, `FField::GetMetaData(const FName&)`, `FField::GetOwnerStruct()`, `FindFProperty<FBoolProperty>(const UStruct*, const TCHAR*)`, `FBoolProperty::GetPropertyValue` / `SetPropertyValue`, `FProperty::ContainerPtrToValuePtr<T>` |
| **2. file:line + export + access** | `COREUOBJECT_API const FString* FindMetaData(const FName& Key) const;` — `Field.h:757`, **COREUOBJECT_API**, `public:` @ `Field.h:715` (next `private:` is past `:800`). `COREUOBJECT_API const FString& GetMetaData(const FName& Key) const;` — `Field.h:766`, same section. `COREUOBJECT_API UStruct* GetOwnerStruct() const;` — `Field.h:610`, **COREUOBJECT_API**, `public:` @ `Field.h:492`. `FindFProperty` — `UnrealType.h:6743` and `:6767`, template, header-only, namespace scope (no access specifier applies). `FBoolProperty` get/set are inline public members of `UnrealType.h`. **Not used, and cannot be:** `FEditConditionParser` (`EditConditionParser.h:100`, no export macro, `Private/`), `FPropertyNode::IsEditConditionMet` (`PropertyNode.cpp:1258`, `Private/`). |
| **3. Module** | `CoreUObject` only. **No new module.** Explicitly *not* `PropertyEditor` — see §3.3. |
| **4. Guards** | Metadata is `WITH_EDITORONLY_DATA` (`Field.h:709-712`) — the whole plugin is editor-only so this is satisfied, but the code must still compile under a `#if WITH_EDITORONLY_DATA` guard so a future runtime-target slip is a compile error, not a null `MetaDataMap` deref. Refuse if the named flag does not resolve, or resolves to a non-`FBoolProperty`. Never write the flag on a **cooked/read-only** target. Set the flag with the *same* `Modify`/`PreEditChange` bracket as the value, inside the *same* transaction, so one Ctrl-Z undoes both. |
| **5. Bucket** | Unchanged — `set_property` stays in `IsSelfManagedEndpoint` (`MifBridgeCommon.cpp:449`, *"widget-BP branch calls CompileBlueprint; opens its own tight write transaction"*). The flag write goes inside the existing tight inner transaction at `MifBridgeNodes5.cpp:832-845`. |
| **6. Async** | Synchronous. Two extra `FName` map lookups and one `TFieldIterator` walk per call; negligible. |
| **7. Params (+ aliases)** | `overrideFlag` ∈ `set`\|`refuse`\|`ignore`, aliases `editCondition`, `override`. Must be added to `set_property`'s `RejectUnknownParams` accepted list (`MifBridgeNodes5.cpp:680-692`) or it is silently dropped — the exact failure that list exists to prevent. |
| **8. Failure modes (+ error text)** | `refuse` mode: `"'MinLOD' (int32) is gated by meta EditCondition=\"bOverrideMinLOD\" and that flag is currently False. UStaticMeshComponent ignores MinLOD entirely while it is False (StaticMeshRender.cpp:248). Pass overrideFlag:\"set\" to set the flag with the value, or write bOverrideMinLOD yourself first."` Unresolvable flag: `"'Foo' has meta EditCondition=\"bBarEnabled\" but no FBoolProperty named bBarEnabled exists on FBaz or any of its supers. The value was NOT written; the engine may ignore it."` Complex condition: `"'Foo' has meta EditCondition=\"Mode == EFooMode::Advanced\", which this bridge does not evaluate (only a single bool or its negation). The value WAS written; verify by hand that the condition holds."` — note this one is a **warning on a successful write**, not a failure, and must appear in `warnings[]`. |
| **9. Cooked behaviour** | Metadata is stripped when `WITH_EDITORONLY_DATA == 0`, so in a cooked runtime there is no `EditCondition` string at all — but the **flag itself** is a real `UPROPERTY` and is cooked. Consequence: the flag→value relationship is only discoverable in the editor; a value written without its flag in the editor ships as dead data. State this in the response note for CDO/asset writes. |
| **10. Numeric verification** | Response gains `editCondition` (the raw meta string or null), `editConditionKind` ∈ `none`\|`bool`\|`negatedBool`\|`unevaluated`, `editConditionMet` (bool or null), and `overrideFlagWritten:{name, valueBefore, valueAfter}`. The countable claim: after a `set` write, re-reading the flag must return the satisfying value — verified by re-export exactly like the main value, so `overrideFlagWritten.valueAfter` is a measured readback, not an echo. |

---

## 4. G3 — `describe_property` (the discovery layer)

Nothing in the bridge reports property flags or metadata. `list_object_properties` emits
`{name, type, value}` only (`MifBridgeNodes6.cpp:151-160`); `describe_class` enumerates functions and
dispatchers (`MifBridgeIntrospect.cpp:303`ff) and no properties at all — confirmed live.

Without this, an agent cannot tell `EditAnywhere` from `VisibleAnywhere`, cannot see a gate, cannot
see a clamp, cannot see `Transient`, and cannot see which `Category` a property is under. Every other
gap in this document is un-actionable until it can.

### 4.1 What the panel computes, and from what

| Panel behaviour | Engine rule | file:line |
|---|---|---|
| Row is shown at all | `bShowIfEditableProperty && !bOnlyShowAsInlineEditCondition && bShowIfDisableEditOnInstance` where the parts are `CPF_Edit`, `meta InlineEditConditionToggle`, `CPF_DisableEditOnInstance` | `PropertyEditorHelpers.cpp:374-390` |
| Row is greyed (edit-const) | `IsPropertyConst()` = `CPF_EditConst`; then the owning-struct chain; then every object's `CanEditChange(chain)`; then `!IsEditConditionMet()` | `PropertyNode.cpp:1137-1150` (const), `:1153-1246` (full) |
| Hidden on a template | `PropertyOwnerClass->IsNative() && Property.HasAnyPropertyFlags(CPF_DisableEditOnTemplate)` | `PropertyEditorModule.cpp:130-145` |
| Default `CanEditChange` | `const bool bIsMutable = !InProperty->HasAnyPropertyFlags( CPF_EditConst ); return bIsMutable;` | `Obj.cpp:507-511` |

UHT's specifier→flag mapping, so the report can name the *authored* specifier rather than raw flags
(`Programs/Shared/EpicGames.UHT/Specifiers/UhtPropertyMemberSpecifiers.cs`):

| Specifier | Flags | line |
|---|---|---|
| `EditAnywhere` | `Edit` | `:21-31` |
| `EditInstanceOnly` | `Edit \| DisableEditOnTemplate` | `:40` |
| `EditDefaultsOnly` | `Edit \| DisableEditOnInstance` | `:52` |
| `VisibleAnywhere` | `Edit \| EditConst` | `:64` |
| `VisibleInstanceOnly` | `Edit \| EditConst \| DisableEditOnTemplate` | `:76` |
| `VisibleDefaultsOnly` | `Edit \| EditConst \| DisableEditOnInstance` | `:88` |

So `VisibleAnywhere` is exactly `CPF_Edit | CPF_EditConst` — a property a human **cannot** edit in the
panel. The bridge writes those today with no comment.

Persistence flags that decide whether a write survives a save (`FProperty::ShouldSerializeValue`,
`Property.cpp:1167-1225`):

```
Property.cpp:1181
  const uint64 SkipFlags = CPF_Transient | CPF_DuplicateTransient | CPF_NonPIEDuplicateTransient | CPF_NonTransactional | CPF_Deprecated | CPF_DevelopmentAssets | CPF_SkipSerialization;
Property.cpp:1188
  if ((PropertyFlags & CPF_Transient) && Ar.IsPersistent() && !Ar.IsSerializingDefaults()) { return false; }
Property.cpp:1193
  if ((PropertyFlags & CPF_DuplicateTransient) && (Ar.GetPortFlags() & PPF_Duplicate)) { return false; }
Property.cpp:1205
  if ((PropertyFlags & CPF_NonTransactional) && Ar.IsTransacting()) { return false; }
```

Three separate consequences the bridge must report, because they are three different lies:

* `CPF_Transient` → the write is real now and **gone on reload** (except on an archetype, where
  `IsSerializingDefaults()` spares it — note the asymmetry: a Blueprint CDO *does* keep transient
  values, a level instance does not).
* `CPF_DuplicateTransient` → survives a save, **lost on copy/paste/duplicate**. That is exactly what
  `duplicate_asset` / `duplicate_actors` do.
* `CPF_NonTransactional` → **Ctrl-Z will not undo the bridge's own write.** The bridge advertises
  undo as a guarantee (`00_ARCHITECTURE.md` §Transaction policy); for these properties it is not one.

### 4.2 Endpoint spec — `describe_property`

Returns, for one property (or, with `nameContains`, for many): authored specifier, raw
`CPF_*` names, all metadata keys, `Category`, `DisplayName`, `EditCondition` + its resolved flag +
current met/unmet state, `ClampMin`/`ClampMax`/`UIMin`/`UIMax`/`Multiple`/`ArrayClamp`,
`EditFixedSize`, `Instanced`/`EditInline` + `AllowedClasses`/`DisallowedClasses`, `GetOptions`,
`Units`/`ForceUnits`, `Bitmask`/`BitmaskEnum`, `ArrayDim`, container kind + element count + inner
type, `editableByHuman` (the panel's own predicate, recomputed), `persistence`
(`saved`/`transient`/`duplicateTransient`/`nonTransactional`), `differsFromDefault` + `defaultValue`
(see G6), and `targetKind` (see G5).

| Field | Value |
|---|---|
| **1. Engine API** | `FField::GetMetaDataMap()`, `FField::FindMetaData`, `FProperty::HasAnyPropertyFlags`, `FProperty::GetOwnerStruct`, `UObject::CanEditChange(const FProperty*)`, `FProperty::ShouldSerializeValue` (read the flags directly rather than calling it — it needs an `FArchive`), `TFieldIterator<FProperty>` |
| **2. file:line + export + access** | `COREUOBJECT_API const TMap<FName, FString>* GetMetaDataMap() const;` — `Field.h:885`, **COREUOBJECT_API**, public. `COREUOBJECT_API virtual bool CanEditChange( const FProperty* InProperty ) const;` — `Object.h:440`, **COREUOBJECT_API**, `public:` @ `Object.h:212` (next specifier `protected:` @ `:496`) — **checked because this project has been bitten by an `ENGINE_API` method that was `protected`; this one is genuinely public.** `COREUOBJECT_API bool ShouldSerializeValue( FArchive& Ar ) const;` — `UnrealType.h:1031`, **COREUOBJECT_API**, `public:` @ `:960`. `FORCEINLINE bool ContainsInstancedObjectProperty() const` — `UnrealType.h:1014`, inline, same public section. Flag constants: `ObjectMacros.h:397` (`CPF_Edit`), `:403` (`CPF_EditFixedSize`), `:408` (`CPF_DisableEditOnTemplate`), `:410` (`CPF_Transient`), `:411` (`CPF_Config`), `:413` (`CPF_DisableEditOnInstance`), `:414` (`CPF_EditConst`), `:418` (`CPF_DuplicateTransient`), `:444` (`CPF_NonPIEDuplicateTransient`), `:452` (`CPF_SkipSerialization`). |
| **3. Module** | `CoreUObject` (+ `Engine` for `UActorComponent` in the `targetKind` branch). **No new module.** |
| **4. Guards** | `RejectUnknownParams`. `GetMetaDataMap()` may return **null** — every metadata read must null-check, not deref. Wrap in `#if WITH_EDITORONLY_DATA`. Cap the multi-property form with `limit`/`maxValueChars` exactly as `list_object_properties` already does (`MifBridgeNodes6.cpp:115-119`) — the same `Ultra_Dynamic_Sky` 545-property blow-up applies, and metadata maps make each row bigger, not smaller. |
| **5. Bucket** | **Read-only** — add `TEXT("describe_property")` to `IsReadOnlyEndpoint`'s `TSet` (`MifBridgeCommon.cpp:353`), beside `get_property` / `list_object_properties` at `:364`. Without that entry every call pushes an empty undo entry. |
| **6. Async** | Synchronous. Pure reflection reads. |
| **7. Params (+ aliases)** | `objectPath` (alias `actorPath`); `blueprintId` \| `path` + `widgetName`; `propertyPath` (alias `property`) **or** `nameContains` (aliases `filter`, `nameFilter`) for the survey form; `class` / `className` as an alternative to an instance (describe the class's properties without loading an object — `describe_class` already accepts both spellings, `MifBridgeIntrospect.cpp:305-308`); `limit` (default 200, clamp 1..5000); `includeMetadata` (default true); `includeDefault` (default true — costs one archetype walk). |
| **8. Failure modes (+ error text)** | Unknown property: reuse the walker's existing text but **add the candidates**, mirroring the struct-member refusal already in the codebase (`MifBridgeNodes5.cpp:392-393`): `"property 'MinLod' not found on 'StaticMeshComponent'. Did you mean: MinLOD, bOverrideMinLOD, MinDrawDistance?"` Neither `propertyPath` nor `nameContains`: `"supply propertyPath (one property, full detail) or nameContains (a filtered survey)"`. |
| **9. Cooked behaviour** | On a cooked package `GetMetaDataMap()` is null and every `meta` field must be emitted as `null` with a top-level `"metadataAvailable": false` — **not** as empty strings, which read as "no clamp" when the truth is "unknown". Flags (`CPF_*`) are cooked and remain accurate. |
| **10. Numeric verification** | `metadataKeyCount`, `flagCount`, and for the survey form `matched` / `count` / `truncated` exactly as `list_object_properties` already emits (`MifBridgeNodes6.cpp:165-167`). A caller can cross-check `editableByHuman` against `CanEditChange` by asking for both and comparing. |

---

## 5. G4 — The notification bracket (defect in shipped code)

### 5.1 `PostEditChangeProperty` does not propagate to instances

`MifBridgeNodes5.cpp:840-841` reads:

```cpp
FPropertyChangedEvent Evt(Leaf, EPropertyChangeType::ValueSet);
LeafOwner->PostEditChangeProperty(Evt);       // propagates to instances/archetype
```

The comment is wrong. The engine's implementation is:

```
Runtime/CoreUObject/Private/UObject/Obj.cpp:433-444
  void UObject::PostEditChangeProperty(FPropertyChangedEvent& PropertyChangedEvent)
  {
      FCoreUObjectDelegates::OnObjectPropertyChanged.Broadcast(this, PropertyChangedEvent);
      if (PropertyChangedEvent.ChangeType == EPropertyChangeType::Interactive) { SnapshotTransactionBuffer(...); }
  }
```

A broadcast and an interactive-snapshot. **No archetype handling of any kind.**

The chain variant does propagate:

```
Runtime/CoreUObject/Private/UObject/Obj.cpp:501-509
  if (HasAnyFlags(RF_ClassDefaultObject | RF_ArchetypeObject) && PropertyChangedEvent.PropertyChain.GetActiveMemberNode() == PropertyChangedEvent.PropertyChain.GetHead())
  {
      TArray<UObject*> ArchetypeInstances;
      GetArchetypeInstances(ArchetypeInstances);
      PropagatePostEditChange(ArchetypeInstances, PropertyChangedEvent);
  }
Runtime/CoreUObject/Private/UObject/Obj.cpp:541
  PostEditChangeProperty(PropertyEvent);
```

— and ends by calling `PostEditChangeProperty` anyway (`Obj.cpp:541`), so **calling the chain variant
is strictly a superset of what the bridge does now.**

### 5.2 Four further divergences from the panel's write

| Panel | Bridge today | Consequence |
|---|---|---|
| `Object->PostEditChangeChainProperty(ChainEvent)` whenever the chain is non-empty, `PostEditChangeProperty` only when it is empty (`PropertyNode.cpp:3001-3011`) | always `PostEditChangeProperty` | 40 `::PostEditChangeChainProperty` overrides in `Runtime/Engine/Private` alone never fire. Includes `UMeshComponent::PostEditChangeChainProperty` → `CleanUpOverrideMaterials()` (`MeshComponent.cpp:155-166`), `UPrimitiveComponent` (`PrimitiveComponent.cpp:1251`), `UChildActorComponent` (`ChildActorComponent.cpp:257`), `ABrush` (`Brush.cpp:71`). |
| `MemberProperty` = the outermost member (`Obj.cpp:494-497`, `PropertyNode.cpp:3081-3083`) | `FPropertyChangedEvent(InProperty)` sets `MemberProperty = InProperty` (`UnrealType.h:6349-6350`) | For `Settings.BloomIntensity` the bridge reports `MemberProperty = BloomIntensity`; the panel reports `Settings`. `AActor::PostEditChangeProperty` switches on `PropertyChangedEvent.MemberProperty` (`ActorEditor.cpp:134-135`), so member-keyed handlers do not fire. |
| `ChangeEvent.SetArrayIndexPerObject(ArrayIndicesPerObject)` (`PropertyHandleImpl.cpp:543`) | never set | `GetArrayIndex(Name)` returns -1 in every handler. Blocks correct per-element notifications for G1. |
| `MapHelper.Rehash()` / `SetHelper.Rehash()` after a key/element change (`PropertyHandleImpl.cpp:522-534`) | never | see G1 |
| `InPropertyNode->PropagatePropertyChange(CurObject, *NewValue, ...)` when the object is a CDO/archetype/default-subobject-of-CDO and not a game world (`PropertyHandleImpl.cpp:500-509`) | never | **Editing a Blueprint CDO does not update already-placed instances in the open level.** They keep the old value until reload. |

The value-propagation rule the panel uses — *only overwrite an instance whose value still equals the
template's old value* — lives in `FPropertyNode::PropagatePropertyChange`
(`Editor/PropertyEditor/Private/PropertyNode.cpp:3728-3870`):

```
PropertyNode.cpp:3838
  // Only import if the value matches the previous value of the property that changed
  if (bShouldImport) { Prop->ImportText_Direct(NewValue, DestSimplePropAddr, ActualObjToChange, PPF_InstanceSubobjects); }
```

That function is `Private/` and unexported, so it must be reimplemented from
`UObject::GetArchetypeInstances` + `FProperty::Identical`.

### 5.3 Behaviour spec — `set_property` bracket rewrite

Replace `MifBridgeNodes5.cpp:832-845` with the panel's sequence, in this order:

1. Build an `FEditPropertyChain` from the walked path (the walker already knows every segment's
   `FProperty` — collect them instead of discarding them). Head = the object-level member, tail = the
   leaf.
2. `LeafOwner->Modify()`; `LeafOwner->PreEditChange(Chain)` (chain overload, not the `FProperty*` one).
3. Publish from the scratch buffer (unchanged, PM-003 preserved).
4. If `LeafOwner->HasAnyFlags(RF_ClassDefaultObject|RF_ArchetypeObject)` (or is an `RF_DefaultSubObject`
   whose outer is one) **and** the world is not a game world → propagate values to
   `GetArchetypeInstances`, overwriting only instances whose pre-write value was `Identical` to the
   template's pre-write value.
5. `Rehash()` if the edited node was a map key or set element.
6. `FPropertyChangedChainEvent ChainEvent(Chain, Evt)` with `ChangeType = ValueSet`,
   `SetActiveMemberProperty(head)`, `SetArrayIndexPerObject(...)`; call
   `LeafOwner->PostEditChangeChainProperty(ChainEvent)`.
7. Verify by re-export as today — but **re-resolve first** (see G5).

| Field | Value |
|---|---|
| **1. Engine API** | `UObject::PreEditChange(FEditPropertyChain&)`, `UObject::PostEditChangeChainProperty(FPropertyChangedChainEvent&)`, `UObject::GetArchetypeInstances(TArray<UObject*>&)`, `UObject::IsTemplate(EObjectFlags)`, `FProperty::Identical`, `FEditPropertyChain::SetActivePropertyNode/SetActiveMemberPropertyNode`, `FPropertyChangedEvent::SetActiveMemberProperty/SetArrayIndexPerObject`, `FScriptMapHelper::Rehash`, `FScriptSetHelper::Rehash` |
| **2. file:line + export + access** | `COREUOBJECT_API virtual void PreEditChange( class FEditPropertyChain& PropertyAboutToChange );` — `Object.h:429`, **COREUOBJECT_API**, `public:` @ `Object.h:212`. `COREUOBJECT_API virtual void PostEditChangeChainProperty( struct FPropertyChangedChainEvent& PropertyChangedEvent );` — `Object.h:469`, **COREUOBJECT_API**, same public section. `COREUOBJECT_API void GetArchetypeInstances( TArray<UObject*>& Instances );` — `Object.h:1372`, **COREUOBJECT_API**, `public:` @ `Object.h:1275`. `COREUOBJECT_API bool IsTemplate( EObjectFlags TemplateTypes = RF_ArchetypeObject\|RF_ClassDefaultObject ) const;` — `UObjectBaseUtility.h:626`, **COREUOBJECT_API**, public. `COREUOBJECT_API virtual bool Identical( const void* A, const void* B, uint32 PortFlags=0 ) const PURE_VIRTUAL(...)` — `UnrealType.h:379`, **COREUOBJECT_API**, `public:` @ `UnrealType.h:352`. `class COREUOBJECT_API FEditPropertyChain : public TDoubleLinkedList<FProperty*>` — `UnrealType.h:6221`, **class-level COREUOBJECT_API**, `public:` @ `:6224`. `struct FPropertyChangedChainEvent : public FPropertyChangedEvent` — `UnrealType.h:6471`, header-only struct. `void SetActiveMemberProperty( FProperty* )` — `UnrealType.h:6358`, inline public. `void SetArrayIndexPerObject(TArrayView<const TMap<FString, int32>>)` — `UnrealType.h:6366`, inline public. |
| **3. Module** | `CoreUObject`. **No new module.** |
| **4. Guards** | `PropagatePostEditChange` contains `check(PropertyChangedEvent.PropertyChain.GetActiveMemberNode() != nullptr);` (`Obj.cpp:660`) — an empty or unset chain **asserts**. Build the chain fully or fall back to the current `PostEditChangeProperty` path; never hand over a half-built chain. Skip instance propagation entirely when `FApp::IsGame()` / a PIE world is the outer, mirroring `PropertyHandleImpl.cpp:502` (`!bIsGameWorld`) and `Obj.cpp:499` (`!FApp::IsGame()`). Do not propagate for sparse class data (`PropertyHandleImpl.cpp:505-508`). |
| **5. Bucket** | Unchanged. Note `AActor::PostEditChangeProperty` calls `UnregisterAllComponents(); RerunConstructionScripts(); ReregisterAllComponents();` (`ActorEditor.cpp:213-215`) — that happens **inside** the bridge's tight inner transaction today. It is not a Blueprint compile, so self-managed is still correct, but see G5 for the consequence. |
| **6. Async** | Synchronous, but no longer cheap: `GetArchetypeInstances` walks the object hash for the class, and construction-script reruns can be triggered downstream. On a CDO with many placed instances this is the dominant cost. Add `propagateToInstances` (default `true`) so a caller doing 200 CDO writes can turn it off for 199 of them and pay once. |
| **7. Params (+ aliases)** | `propagateToInstances` (aliases `propagate`, `updateInstances`), bool, default true. Must be added to `RejectUnknownParams`'s accepted list. |
| **8. Failure modes (+ error text)** | Chain build failure: `"could not build an FEditPropertyChain for 'Settings.BloomIntensity' (segment 2 resolved to no FProperty); fell back to PostEditChangeProperty. Handlers that only override PostEditChangeChainProperty did NOT run."` — reported as a `warning` on an otherwise successful write, because degrading silently is the failure mode being removed. Propagation partial: `"propagated to 7 of 12 archetype instances; 5 had already overridden this property and were left alone (this matches the Details panel)."` |
| **9. Cooked behaviour** | `PostEditChangeChainProperty` is `WITH_EDITOR`-only on most overrides; the bridge is editor-only so this is fine. Cooked targets must not be written at all. |
| **10. Numeric verification** | New response fields: `memberProperty` (name), `chainDepth` (int), `chainBuilt` (bool), `notification` ∈ `chain`\|`plain`, `instancesFound` (int), `instancesPropagated` (int), `instancesSkippedOverridden` (int), `rehashed` (bool). `instancesFound == instancesPropagated + instancesSkippedOverridden` is a checkable invariant. |

---

## 6. G5 — Instance vs template on a placed actor (defect in shipped code)

### 6.1 Does the bridge distinguish them?

**No.** `set_property` resolves `objectPath` with `StaticLoadObject` and walks
(`MifBridgeNodes5.cpp:712-756`), then treats every target identically. It never reads
`UActorComponent::CreationMethod`, never calls `IsTemplate()`, and never reports which of the two it
wrote. A caller who passes `/Game/BP/BP_Lamp.BP_Lamp_C:Light_GEN_VARIABLE` edits the template; a
caller who passes `/Game/Maps/Town.Town:PersistentLevel.BP_Lamp_C_3.LightComponent0` edits one
instance; both responses look identical.

### 6.2 Does an instance edit get reverted by a construction-script rerun?

**Yes, in three distinct ways, and the bridge triggers the rerun itself.**

The trigger is the bridge's own `PreEditChange`/`PostEditChangeProperty` pair:

```
Runtime/Engine/Private/Components/ActorComponent.cpp:806-822  UActorComponent::PreEditChange
  if(IsRegistered()) { ... EditReregisterContexts.Add(this,new FComponentReregisterContext(this)); }
Runtime/Engine/Private/Components/ActorComponent.cpp:927-941  UActorComponent::ConsolidatedPostEditChange
  if(EditReregisterContexts.RemoveAndCopyValue(this, ReregisterContext))
  {
      delete ReregisterContext;
      AActor* MyOwner = GetOwner();
      if ( MyOwner && !MyOwner->IsTemplate() && PropertyChangedEvent.ChangeType != EPropertyChangeType::Interactive )
      { MyOwner->RerunConstructionScripts(); }
  }
```

A placed actor's components are registered, and the bridge sends `ValueSet` (not `Interactive`), so
**every** `set_property` on a placed actor's component reruns that actor's construction scripts.

What the rerun does to the object the bridge is holding:

```
Runtime/Engine/Private/ActorConstruction.cpp:167-170
  if (Component->IsCreatedByConstructionScript()) { bDestroyComponent = true; }
Runtime/Engine/Private/ActorConstruction.cpp:205-210
  Component->DestroyComponent();
  FName const NewBaseName( *(FString::Printf(TEXT("TRASH_%s"), *Component->GetClass()->GetName())) );
  FName const NewObjectName = MakeUniqueObjectName(this, GetClass(), NewBaseName);
  Component->Rename(*NewObjectName.ToString(), this, REN_ForceNoResetLoaders|REN_DontCreateRedirectors|REN_NonTransactional|REN_DoNotDirty);
Runtime/Engine/Private/Components/ActorComponent.cpp:520-523
  bool UActorComponent::IsCreatedByConstructionScript() const
  { return ((CreationMethod == EComponentCreationMethod::SimpleConstructionScript) || (CreationMethod == EComponentCreationMethod::UserConstructionScript)); }
```

**So `LeafOwner` and `LeafAddr` are dangling by the time `MifBridgeNodes5.cpp:863` re-reads them.**
The component was `DestroyComponent()`ed and renamed to `TRASH_<Class>_N`; a *new* component of the
same name now hangs off the actor. The verification block reads the trashed object, sees the value it
just wrote, and reports `applied:true, verified:true` — about an object that is no longer part of the
actor. After the next GC that read is a use-after-free.

Which values the new component inherits is decided by `FComponentInstanceDataCache`, and its skip
rules are the second and third revert paths:

```
Runtime/Engine/Private/ComponentInstanceDataCache.cpp:54-66  FDataCachePropertyWriter::ShouldSkipProperty
  return (!bPropertyInImmutableStruct
      && (InProperty->HasAnyPropertyFlags(CPF_Transient)
          || !InProperty->HasAnyPropertyFlags(CPF_Edit | CPF_Interp)
          || InProperty->IsA<FMulticastDelegateProperty>()
          || PropertiesToSkip.Contains(InProperty)
          )
      );
Runtime/Engine/Private/ComponentInstanceDataCache.cpp:171
  Component->GetUCSModifiedProperties(PropertiesToSkip);
```

Therefore an instance write is **silently reverted** when the property is:

* `CPF_Transient`, **or**
* **not** `CPF_Edit | CPF_Interp` — i.e. a bare `UPROPERTY()` with no `EditAnywhere`. The bridge can
  write those; the panel cannot even show them; the instance cache refuses to carry them.
* a multicast delegate, **or**
* in `UCSModifiedProperties` — anything the construction script itself writes. The CS wins by design
  (`ActorConstruction.cpp:929` `Component->DetermineUCSModifiedProperties();`).
* Additionally the root component's `RelativeLocation`/`RelativeRotation`/`RelativeScale3D` are
  deliberately skipped and handled separately (`ComponentInstanceDataCache.cpp:173-183`).

`EComponentCreationMethod` (`Runtime/Engine/Public/ComponentInstanceDataCache.h:24-35`) gives the four
cases a caller needs named:

| Value | line | Reruns destroy it? | Bridge should report |
|---|---|---|---|
| `Native` | `:28` | No (unless orphaned, `ActorConstruction.cpp:187-195`) | `creationMethod:"Native"` — edit survives |
| `SimpleConstructionScript` | `:30` | **Yes** | `creationMethod:"SimpleConstructionScript"` + revert risk |
| `UserConstructionScript` | `:32` | **Yes**, and `IsEditableWhenInherited()` returns false for it (`ActorComponent.cpp:2243-2246`) | refuse or hard-warn |
| `Instance` | `:34` | No | `creationMethod:"Instance"` — edit survives |

### 6.3 Behaviour spec — what the endpoint must report and do

**Report (new response fields on `set_property` / `edit_container` / `reset_property_to_default`):**

```
targetKind      : "cdo" | "scsTemplate" | "widgetTemplate" | "ichOverride" | "levelInstance" | "instanceComponent" | "asset" | "graphNode"
isTemplate      : bool                       // UObject::IsTemplate()
archetype       : "<path>"                   // UObject::GetArchetype()->GetPathName()
owningActor     : "<path>" | null
creationMethod  : "Native" | "SimpleConstructionScript" | "UserConstructionScript" | "Instance" | null
reconstructed   : bool                       // did the write trigger a construction-script rerun
retargetedTo    : "<path>" | null            // the NEW object, if reconstructed
survivesRerun   : true | false | "unknown"
survivesRerunReason : "CPF_Transient" | "not CPF_Edit|CPF_Interp" | "multicast delegate" | "written by the construction script (UCSModifiedProperties)" | null
templateEquivalent : "<path>" | null         // the _GEN_VARIABLE / Default__ path that edits ALL instances
```

`templateEquivalent` is the field that answers the question a caller actually has — *"I edited one
lamp; how do I edit all of them?"* — and it is the same discovery move `MifBridgeInherited.cpp` already
makes for native components (its header notes `Mesh` → `:CharacterMesh0`, `CharacterMovement` →
`:CharMoveComp`, resolved from the object rather than guessed).

**Do:** after `PostEditChange*` returns, before verifying:

1. `if (!IsValid(LeafOwner) || LeafOwner->GetFName().ToString().StartsWith(TEXT("TRASH_")))` → the
   object was reconstructed.
2. Re-resolve the original `objectPath` (`StaticFindObject`, not `StaticLoadObject` — the package is
   already loaded and we must not resurrect anything) and re-walk the property path on the **new**
   object.
3. Verify against the new object. If it does not resolve, **fail** with the reconstruction named —
   never fall back to reading the trashed pointer.
4. Emit `reconstructed` and `retargetedTo`.

| Field | Value |
|---|---|
| **1. Engine API** | `UActorComponent::CreationMethod`, `UActorComponent::IsCreatedByConstructionScript()`, `UActorComponent::GetUCSModifiedProperties(TSet<const FProperty*>&)`, `UActorComponent::IsEditableWhenInherited()`, `UObject::IsTemplate()`, `UObject::GetArchetype()`, `AActor::RerunConstructionScripts()` (observed, not called), `IsValid(UObject*)` |
| **2. file:line + export + access** | `EComponentCreationMethod CreationMethod;` — `Components/ActorComponent.h:315`, `UPROPERTY()`, `public:` @ `:311` — **public data member, no export macro needed**. `ENGINE_API bool IsCreatedByConstructionScript() const;` — `ActorComponent.h:380`, **ENGINE_API**, `public:` @ `:336`. `ENGINE_API void GetUCSModifiedProperties(TSet<const FProperty*>& ModifiedProperties) const;` — `ActorComponent.h:347`, **ENGINE_API**, same public section — **explicitly access-checked; the private block at `:336`-ish covers only `DetermineUCSSerializationIndexForLegacyComponent`.** `ENGINE_API bool IsEditableWhenInherited() const;` — `ActorComponent.h:356`, **ENGINE_API**, public. `uint8 bEditableWhenInherited:1;` — `ActorComponent.h:243`, `UPROPERTY(EditDefaultsOnly, Category="Variable")`, `public:` @ `:239`. `COREUOBJECT_API UObject* GetArchetype() const;` — `Object.h:1365`, **COREUOBJECT_API**, `public:` @ `:1275`. `class FComponentInstanceDataCache` — `Runtime/Engine/Public/ComponentInstanceDataCache.h:216`, **no class-level export**; `ENGINE_API void ApplyToActor(AActor*, const ECacheApplyPhase) const;` at `:239`, `public:` @ `:218`. (The bridge only needs to *reason about* this cache, not call it.) |
| **3. Module** | `Engine` — already a `PublicDependencyModuleName`. **No new module.** |
| **4. Guards** | Never dereference `LeafOwner` after `PostEditChange*` without the `IsValid` + `TRASH_` check. Never `StaticLoadObject` on the retarget (use `StaticFindObject`). Do not call `RerunConstructionScripts` directly — it is `checkf(!HasAnyFlags(RF_ClassDefaultObject), ...)` (`ActorConstruction.cpp:245`) and `ensureMsgf(bAllowReconstruction, ...)` if the actor is mid-construction (`ActorConstruction.cpp:258`). |
| **5. Bucket** | Unchanged (self-managed for `set_property`). A construction-script rerun inside a transaction is engine-normal — `RerunConstructionScripts` suspends the undo buffer itself: `ITransaction* CurrentTransaction = GUndo; GUndo = nullptr;` (`ActorConstruction.cpp:293-294`). That is *also* why the rerun's component recreation is not undoable, which is worth stating in the response. |
| **6. Async** | Synchronous but expensive: a rerun tears down and rebuilds every CS component, re-registers them, and reapplies the instance cache. Batching many writes to one placed actor pays that cost per write. **Recommend a `deferReconstruction` note in the docs rather than a parameter** — suppressing the rerun would desynchronise the actor from its CS. |
| **7. Params (+ aliases)** | No new input params. All ten fields above are **output**. Optionally `refuseIfReverted` (alias `strict`), bool, default false: fail rather than write when `survivesRerun == false`. |
| **8. Failure modes (+ error text)** | Reverted-by-design: `"'bSomeFlag' on LightComponent0 is a plain UPROPERTY() with no EditAnywhere/Interp. FComponentInstanceDataCache skips such properties (ComponentInstanceDataCache.cpp:54-66), so this instance edit will be lost the next time BP_Lamp_C_3's construction scripts run — which this very call just triggered. Write the template instead: /Game/BP/BP_Lamp.BP_Lamp_C:Light_GEN_VARIABLE"`. UCS-owned: `"'RelativeLocation' is in LightComponent0's UCSModifiedProperties — the construction script writes it, so the instance cache deliberately does not preserve your value (ComponentInstanceDataCache.cpp:171). Change the construction script, not the instance."` Retarget failed: `"the write triggered a construction-script rerun that destroyed LightComponent0 (renamed TRASH_PointLightComponent_0) and the replacement could not be re-resolved at <path>. The write is UNVERIFIED."` |
| **9. Cooked behaviour** | Construction scripts do not rerun in a cooked game (`ActorConstruction.cpp:921-923`: *"Since re-run construction scripts will never be run … don't spend time determining the UCS modified properties in game worlds"*), so `UCSModifiedProperties` is not even populated there. All of this is an editor-only concern; report `survivesRerun:"unknown"` for a target with no editor world. |
| **10. Numeric verification** | `reconstructed` (bool), `retargetedTo` (path or null), and — the checkable one — `valueAfter` must be read from the **re-resolved** object, so `target` in the response and the object the value came from are provably the same object. Add `verifiedOn:"<path>"` and assert `verifiedOn == retargetedTo ?? target`. |

> **Cross-reference.** Per-instance component overrides on a *child Blueprint* are already solved by
> `get_/override_/revert_inherited_component` (`MifBridgeInherited.cpp`). The remaining gap on that
> path is discovery-side only: none of those endpoints reports `bEditableWhenInherited`
> (`ActorComponent.h:243`) or `IsEditableWhenInherited()` (`ActorComponent.h:356`), so a caller cannot
> tell "this component is not overridable" from "this override failed". Fold both into
> `get_inherited_component`'s response.

---

## 7. G6 — Reset to Default and diff vs default

### 7.1 How the panel computes it

**Which object is "default":** the archetype, with a `UClass`→CDO hop first.

```
Editor/PropertyEditor/Private/PropertyNode.cpp:1651-1654
  // if the object specified is a class object, transfer to the CDO instead
  if ( Cast<UClass>(PropertyValueRoot.OwnerObject) != NULL )
  { PropertyValueRoot.OwnerObject = Cast<UClass>(PropertyValueRoot.OwnerObject)->GetDefaultObject(); }
Editor/PropertyEditor/Private/PropertyNode.cpp:1669
  PropertyDefaultValueRoot.OwnerObject = PropertyValueRoot.OwnerObject ? PropertyValueRoot.OwnerObject->GetArchetype() : nullptr;
```

**The comparison:** `FProperty::Identical`, with `PPF_DeepComparison` when the property contains an
instanced object, and an `ArrayDim` loop for C-arrays.

```
Editor/PropertyEditor/Private/PropertyNode.cpp:2275-2278
  uint32 PortFlags = 0;
  if (InProperty->ContainsInstancedObjectProperty()) { PortFlags |= PPF_DeepComparison; }
Editor/PropertyEditor/Private/PropertyNode.cpp:2287-2297
  for (int32 Idx = 0; !bDiffersFromDefault && Idx < InProperty->ArrayDim; Idx++)
  { bDiffersFromDefaultValue = !InProperty->Identical(PropertyValueAddress + Idx * InProperty->ElementSize, PropertyDefaultAddress + Idx * InProperty->ElementSize, PortFlags); }
Editor/PropertyEditor/Private/PropertyNode.cpp:2304-2308
  bDiffersFromDefaultValue = !InProperty->Identical(PropertyValueAddress, PropertyDefaultAddress, PortFlags);
```

Plus a container-specific pre-check: an element index that does not exist in the default **differs by
definition** (`PropertyNode.cpp:2246-2270`, using `FScriptArrayHelper`/`FScriptSetHelper`/`FScriptMapHelper::IsValidIndex`).

**The reset itself** is an `ImportText` of the default's export text, with `PPF_InstanceSubobjects`:

```
Editor/PropertyEditor/Private/PropertyHandleImpl.cpp:992-1008  FPropertyValueImpl::ResetToDefault()
  if( PropertyNodePin.IsValid() && !PropertyNodePin->IsEditConst() && PropertyNodePin->GetDiffersFromDefault() )
  {
      FScopedTransaction Transaction( NSLOCTEXT("UnrealEd", "PropertyWindowResetToDefault", "Reset to Default") );
      ... ImportText(PropertyNodePin->GetDefaultValueAsString(bUseDisplayName), EPropertyValueSetFlags::InstanceObjects);
      PropertyNodePin->BroadcastPropertyResetToDefault();
  }
```

with `EPropertyValueSetFlags::InstanceObjects` mapping to `PPF_InstanceSubobjects`
(`PropertyHandleImpl.cpp:490-492`), and the default text produced by

```
Editor/PropertyEditor/Private/PropertyNode.cpp:2422-2456  FPropertyNode::GetDefaultValueAsString(const uint8*, const FProperty*, bool)
  // no default available, fall back on the default value for our primitive:
  ... InProperty->InitializeValue(TempComplexPropAddr); InProperty->ExportText_Direct(DefaultValue, TempComplexPropAddr, TempComplexPropAddr, nullptr, PPF_None);
  else if ( GetArrayIndex() == INDEX_NONE && InProperty->ArrayDim > 1 ) { FArrayProperty::ExportTextInnerItem(...); }
  else { InProperty->ExportTextItem_Direct(DefaultValue, PropertyDefaultAddress, PropertyDefaultAddress, nullptr, PortFlags, nullptr); }
```

**Two refusals the panel applies and a naive reset will not:**

```
Editor/PropertyEditor/Private/PropertyHandleImpl.cpp:3421-3433  FPropertyHandleBase::CanResetToDefault()
  const bool bCanResetToDefault = (Property->PropertyFlags & CPF_Config) == 0;
  const bool bFixedSized = (Property->PropertyFlags & CPF_EditFixedSize) != 0;
  return bCanResetToDefault && !bFixedSized && DiffersFromDefault();
```

`CPF_Config` properties (`ObjectMacros.h:411`) have **no** reset arrow, and `CPF_EditFixedSize`
containers do not either.

**A copy of the default is not always available.** `Private_HasDefaultValue()` walks the archetype
chain and returns false when the property is not in the archetype's class
(`PropertyNode.cpp:1834-1862`) — e.g. a property added by a child Blueprint. The bridge must handle
`archetype == nullptr` / property-not-in-archetype by falling back to a **freshly constructed**
value, exactly as `GetDefaultValueAsString` does at `PropertyNode.cpp:2432-2443`, and must say which
of the two it used.

### 7.2 Endpoint specs

#### `reset_property_to_default` — NEW

| Field | Value |
|---|---|
| **1. Engine API** | `UObject::GetArchetype()`, `UClass::GetDefaultObject()`, `FProperty::Identical`, `FProperty::ContainsInstancedObjectProperty()`, `FProperty::ExportTextItem_Direct`, `FProperty::ImportText_Direct` with `PPF_InstanceSubobjects`, `FProperty::InitializeValue` / `DestroyValue`, `FArrayProperty::ExportTextInnerItem` |
| **2. file:line + export + access** | `COREUOBJECT_API UObject* GetArchetype() const;` — `Object.h:1365`, **COREUOBJECT_API**, `public:` @ `:1275`. `COREUOBJECT_API virtual bool Identical(...)` — `UnrealType.h:379`, **COREUOBJECT_API**, `public:` @ `:352`. `FORCEINLINE bool ContainsInstancedObjectProperty() const` — `UnrealType.h:1014`, inline, `public:` @ `:960`. `FORCEINLINE void InitializeValue( void* Dest ) const` — `UnrealType.h:929`, inline, `public:` @ `:920`. `FORCEINLINE void DestroyValue( void* Dest ) const` — `UnrealType.h:898`, inline, `public:` @ `:891`. `PPF_InstanceSubobjects = 0x00040000` — `Runtime/Core/Public/UObject/PropertyPortFlags.h:85` (note: **Core**, not CoreUObject). `PPF_DeepComparison = 0x00000100` — same file `:46`. `CPF_Config = 0x0000000000004000` — `ObjectMacros.h:411`. `CPF_EditFixedSize = 0x0000000000000040` — `ObjectMacros.h:403`. **Not used, cannot be:** `FPropertyValueImpl::ResetToDefault` (`PropertyHandleImpl.cpp:992`, `Private/`, unexported), `FPropertyNode::GetDiffersFromDefault` (`PropertyNode.cpp:2335`, `Private/`, unexported). |
| **3. Module** | `Core` + `CoreUObject`. **No new module.** |
| **4. Guards** | Refuse when `CPF_Config` or `CPF_EditFixedSize` (state which, quoting `CanResetToDefault`). Refuse when the property is edit-const **unless** `force:true`. PM-003: import the default text into `FScratchValue`, then publish — the panel imports into the live address (`PropertyTextUtilities.cpp:34`); do not copy that. Fall back to a constructed default when the archetype lacks the property, and say so. Use the **same** notification bracket as G4 (chain event + propagation), because a reset is a write. |
| **5. Bucket** | Same as `set_property`: **self-managed** if it accepts the `blueprintId`+`widgetName` widget form (which compiles), otherwise **default/transacted**. Simplest correct choice: register it self-managed and mirror `set_property`'s tight inner transaction so the two behave identically — a caller should not have to know which verb they used to predict undo behaviour. |
| **6. Async** | Synchronous; one archetype walk plus the G4 propagation cost. |
| **7. Params (+ aliases)** | `objectPath` (alias `actorPath`); `blueprintId` \| `path` + `widgetName`; `propertyPath` (alias `property`); `recursive` (alias `includeChildren`, default false — reset every sub-property of a struct rather than the struct as a whole); `force` (alias `allowEditConst`, default false); `propagateToInstances` (default true, same as G4). |
| **8. Failure modes (+ error text)** | Already default: `"'MinLOD' already equals its default (0) on Default__BP_Lamp_C; nothing to reset (changed:false)."` — reported, not failed, matching `set_property`'s idempotent-write note (`MifBridgeNodes5.cpp:919-923`). Config: `"'r.SomeSetting' is CPF_Config: the Details panel shows no reset arrow for config properties (PropertyHandleImpl.cpp:3431). Reset it by editing the .ini, or pass force:true to write the archetype value anyway."` No archetype default: `"'MyChildOnlyVar' does not exist on the archetype (Default__BP_Parent_C); reset used a freshly constructed default instead. defaultSource:\"constructed\"."` |
| **9. Cooked behaviour** | A cooked object's archetype resolves normally, so the *diff* is answerable; the *reset* must be refused for cooked targets like every other write. |
| **10. Numeric verification** | `valueBefore`, `defaultValue`, `valueAfter`, `differedFromDefault` (bool, pre-write), `changed` (bool, post-write), `defaultSource` ∈ `archetype`\|`constructed`, `archetype` (path). Invariant: after a successful reset, `valueAfter == defaultValue` byte-for-byte under the same exporter — assert it and fail if not, reusing `set_property`'s existing "import said success, readback says otherwise" failure shape (`MifBridgeNodes5.cpp:885-897`). |

#### `diff_properties_vs_default` — NEW (read-only)

Walks every property on an object (or a filtered subset) and reports only those that differ from the
archetype. This is the "what does this object actually override?" verb — the single most useful read
for anyone auditing a Blueprint, a placed actor, or a CDO.

| Field | Value |
|---|---|
| **1. Engine API** | as above, minus the import side; plus `TFieldIterator<FProperty>` and `UObject::GetArchetype()` |
| **2. file:line + export + access** | identical citations to `reset_property_to_default`; no new symbols |
| **3. Module** | `Core` + `CoreUObject` |
| **4. Guards** | Skip `CPF_Transient` unless `includeTransient:true` (they always differ and drown the signal). Cap with `limit` / `maxValueChars` like `list_object_properties`. Null-archetype fallback to constructed defaults, flagged per row. |
| **5. Bucket** | **Read-only** — add to `IsReadOnlyEndpoint` beside `get_property` (`MifBridgeCommon.cpp:366`). |
| **6. Async** | Synchronous. O(properties) `Identical` calls; `PPF_DeepComparison` on instanced-object properties is the expensive case — bound it with `deep` (default true) and report when it was disabled. |
| **7. Params (+ aliases)** | `objectPath` (alias `actorPath`); `blueprintId` \| `path` + `widgetName`; `nameContains` (aliases `filter`, `nameFilter`); `limit` (default 200, clamp 1..5000); `maxValueChars` (default 200); `includeTransient` (default false); `deep` (default true); `recursive` (descend into struct members, default false). |
| **8. Failure modes (+ error text)** | No archetype: `"'/Script/Engine.Default__Actor' is the root CDO; its archetype is itself, so every property matches by definition (differing:0)."` — a stated result, not an error. |
| **9. Cooked behaviour** | Works on cooked objects (read-only). Metadata-derived fields are null; flag-derived fields are accurate. |
| **10. Numeric verification** | `inspected` (int), `differing` (int), `skippedTransient` (int), `truncated` (bool), and per row `{name, type, value, defaultValue, defaultSource}`. `inspected == differing + matching + skippedTransient` is a checkable invariant that must be emitted, not implied. |

---

## 8. G7–G9 — the remaining non-plain-write behaviours

### 8.1 G7 — Instanced / EditInline subobject creation

The `+`/class-picker on an `Instanced` property does not assign an existing object; it **constructs
one**:

```
Editor/PropertyEditor/Private/UserInterface/PropertyEditor/SPropertyEditorEditInline.cpp:290-292
  UObject*      Object = Itor->Get();
  UObject*      UseOuter = (InClass->IsChildOf(UClass::StaticClass()) ? Cast<UClass>(Object)->GetDefaultObject() : Object);
  EObjectFlags  MaskedOuterFlags = UseOuter ? UseOuter->GetMaskedFlags(RF_PropagateToSubObjects) : RF_NoFlags;
SPropertyEditorEditInline.cpp:293-296
  if (UseOuter && UseOuter->HasAnyFlags(RF_ClassDefaultObject | RF_ArchetypeObject)) { MaskedOuterFlags |= RF_ArchetypeObject; }
SPropertyEditorEditInline.cpp:312
  UObject* NewUObject = NewObject<UObject>(UseOuter, InClass, *NewObjectName, MaskedOuterFlags, NewObjectTemplate);
SPropertyEditorEditInline.cpp:319
  NewValue = FString::Printf(TEXT("\"%s\""), *NewUObject->GetPathName().ReplaceQuotesWithEscapedQuotes());
```

`set_property` can only assign a path to an object that already exists — and its
`CanonicaliseLeaf` object guard explicitly refuses an unresolvable path
(`MifBridgeNodes5.cpp:214-226`), which is correct but means the "create it" case has no route at all.

The non-obvious parts, all of which must be encoded rather than reinvented:
the outer is the **owning object** (the CDO for a class target); the flags are the outer's
`RF_PropagateToSubObjects` mask **plus** `RF_ArchetypeObject` when the outer is a CDO/archetype; when
re-picking the archetype's own class, the panel reuses the archetype's **name and template**
(`SPropertyEditorEditInline.cpp:244-257`); the previous subobject is renamed into the transient
package rather than deleted (`SPropertyEditorEditInline.cpp:333-341`); and the assigned value is
**quoted** because subobject paths can contain spaces (`SPropertyEditorEditInline.cpp:314-319`).

Proposed endpoint: **`create_instanced_subobject`** — `{objectPath|blueprintId+widgetName,
propertyPath, class, name?, replaceExisting?}` → constructs and assigns, returns the new object's
path so `set_property` can then configure it.

* **Module:** `CoreUObject` (`NewObject`, `MakeUniqueObjectName`, `StaticFindObject`) + `Engine`. No new module.
* **Guards:** refuse when the target property is not an `FObjectPropertyBase`; refuse when the class
  is abstract or not a child of the property's `PropertyClass`; honour `AllowedClasses` /
  `DisallowedClasses` metadata if present (`SPropertyEditorAsset.cpp:135`
  `AllowedClassFilters = PropertyCustomizationHelpers::GetClassesFromMetadataString(MetadataProperty->GetMetaData("AllowedClasses"));`)
  and name the filter in the refusal; never delete the previous subobject — rename it to the transient
  package, as the panel does.
* **Bucket:** default/transacted (no compile), except the widget form.
* **Numeric verification:** `created` (path), `previousSubobject` (path or null),
  `previousRenamedTo` (path or null), plus the standard `valueBefore`/`valueAfter`.
* **Cooked:** refuse outright.
* **UNVERIFIED-adjacent note:** `PropertyCustomizationHelpers::GetClassesFromMetadataString` lives in
  `Editor/PropertyEditor/Public/PropertyCustomizationHelpers.h`, i.e. in a module MifBridge does not
  link. Parse the comma-separated metadata string directly with `UClass::TryFindTypeSlow` instead —
  do **not** add a `PropertyEditor` dependency for one string split.

### 8.2 G8 — Metadata clamps are not enforced by `ImportText`

`ClampMin`/`ClampMax` are applied **only** in the typed numeric setters:

```
Editor/PropertyEditor/Private/PropertyHandleImpl.cpp:870-897  ClampValueFromMetaData<Type>
  const FString& MinString = Property->GetMetaData(TEXT("ClampMin"));  ... RetVal = FMath::Max<Type>(MinValue, RetVal);
  const FString& MaxString = Property->GetMetaData(TEXT("ClampMax"));  ... RetVal = FMath::Min<Type>(MaxValue, RetVal);
Editor/PropertyEditor/Private/PropertyHandleImpl.cpp:901-931  ClampIntegerValueFromMetaData<Type>  // + "Multiple" and "ArrayClamp"
```

called from `FPropertyHandleInt/Float/Double::SetValue` (`PropertyHandleImpl.cpp:3647`, `:3660`,
`:3673`, `:3686`, `:3697`, `:3710`, `:3722`, `:3759`, `:3797`, `:4413`), which is what the numeric
spinbox uses (`SPropertyEditorNumeric.h:506`, `:526`, `:587` — `PropertyHandle->SetValue(NewValue)`).

The **text** path does not clamp: `FPropertyValueImpl::SetValueAsString`
(`PropertyHandleImpl.cpp:818-853`) goes straight to `ImportText`, and CoreUObject's importers never
read `ClampMin` at all — a grep of `Runtime/CoreUObject` for `ClampMin` returns only `UPROPERTY`
declarations in `NoExportTypes.h:1352-1361`, no consuming code.

So: `set_property` mirrors the panel's *copy/paste* path, which is genuinely unclamped, but not its
*typed-entry* path, which is. Both are "the Details panel". **Proposed policy:** do not silently
clamp (that would be a silent value change, the same bug class in the other direction). Instead:

* read `ClampMin`/`ClampMax`/`UIMin`/`UIMax`/`Multiple`/`ArrayClamp` and, when the written value falls
  outside `ClampMin..ClampMax`, emit `clampViolation: {meta:"ClampMin", limit:"0.0", written:"-3.0"}`
  in `warnings[]` on a successful write;
* add `enforceClamps` (aliases `clamp`, `respectClamps`), default `false`, which switches to the
  panel's typed-setter behaviour and reports the coercion via the existing `coerced` /
  `valueStaged` fields (`MifBridgeNodes5.cpp:911-918`);
* `UIMin`/`UIMax` are **slider bounds only** and are never enforced by anything — report them, never
  act on them.

Note the interaction with the existing `coerced` flag: it already catches *engine-side* coercion by
comparing `valueStaged` against `valueAfter`. Clamp enforcement must set `coerced:true` too, so a
caller has one field to check regardless of who did the clamping.

### 8.3 G9 — Multi-target writes

The panel writes every selected object in one bracket, one transaction, one
`FPropertyChangedEvent` carrying `TopLevelObjects` (`PropertyHandleImpl.cpp:542`,
`ChangeEvent.SetArrayIndexPerObject(ArrayIndicesPerObject)` at `:543`). `set_property` takes exactly
one target.

`batch` (already registered, `MifBridgeCommon.cpp:443`) gets *close* — one transaction around N ops —
but each op still fires its own `PostEditChange*` and its own instance propagation, which for N
instances of the same archetype is N propagation passes doing the same work.

Proposed: `targets: ["<path>", ...]` (alias `objectPaths`) on `set_property`, mutually exclusive with
`objectPath`, capped (suggest 256) and refusing mixed classes unless `allowMixedClasses:true`.
Response becomes `results: [{target, applied, changed, valueBefore, valueAfter, ...}]` plus
`succeeded` / `failed` counts. Failure of one target must not roll back the others silently — report
per-target and set top-level `ok:false` if any failed, with `failed > 0` as the checkable number.

This one is ranked last deliberately: it is convenience, not capability. Everything it does is
already expressible as N calls or one `batch`.

---

## 9. Numeric verification (commands)

All counts below were produced against `D:/UE532/Engine/Source` on 2026-07-28 and are reproducible.

```bash
# EditCondition-gated UPROPERTYs across Runtime headers
cd D:/UE532/Engine/Source/Runtime
grep -rhoiE 'editcondition[[:space:]]*=[[:space:]]*"[^"]*"' --include=*.h . > /tmp/ec.txt
wc -l < /tmp/ec.txt                                                            # 837
grep -ciE 'editcondition[[:space:]]*=[[:space:]]*"[[:space:]]*[A-Za-z_][A-Za-z0-9_]*[[:space:]]*"' /tmp/ec.txt   # 619  bare identifier
grep -ciE 'editcondition[[:space:]]*=[[:space:]]*"[[:space:]]*![[:space:]]*[A-Za-z_][A-Za-z0-9_]*[[:space:]]*"' /tmp/ec.txt  # 94  negated identifier
grep -ciE '==|!=|&&|\|\||<|>' /tmp/ec.txt                                      # 122 needs a real expression parser

grep -rio "InlineEditConditionToggle" --include=*.h . | wc -l                   # 264
grep -rio "ClampMin"      --include=*.h . | wc -l                               # 899
grep -rio "UIMin"         --include=*.h . | wc -l                               # 736
grep -rio "EditFixedSize" --include=*.h . | wc -l                               # 68
grep -rioE "meta *= *\(Instanced|EditInline" --include=*.h . | wc -l            # 316
grep -rio "GetOptions"    --include=*.h . | wc -l                               # 23
grep -rioE "UPROPERTY\([^)]*Setter[[:space:]]*=" --include=*.h . | wc -l        # 288

# FPostProcessSettings override flags
grep -c "bOverride_" D:/UE532/Engine/Source/Runtime/Engine/Classes/Engine/Scene.h   # 423

# PostEditChangeChainProperty overrides the bridge currently never triggers
grep -rn "::PostEditChangeChainProperty" D:/UE532/Engine/Source/Runtime/Engine/Private/ | wc -l   # 40
```

Derived figures used above:

| Claim | Arithmetic |
|---|---|
| A single-bool EditCondition evaluator covers 85.2 % of gated properties | (619 + 94) / 837 = 0.8518 |
| 122 gated properties (14.6 %) must be reported as "not evaluated" | 122 / 837 = 0.1458 |
| `FPostProcessSettings` alone is 50.5 % of the EditCondition surface | 423 / 837 = 0.5054 |

---

## 10. Files that must stay in sync

Per `00_ARCHITECTURE.md` §"Adding an endpoint", for **each** of `describe_property`,
`edit_container`, `reset_property_to_default`, `diff_properties_vs_default`,
`create_instanced_subobject`:

1. `MifBridgeHandlers.h` — `MIF_DECL(<name>)`
2. `MifBridgeCommon.cpp` — `MIF_BIND(<name>)` in `Handlers()`
3. `MifBridgeCommon.cpp` — `IsReadOnlyEndpoint` (`describe_property`, `diff_properties_vs_default`)
   **or** `IsSelfManagedEndpoint` (`reset_property_to_default` if it accepts the widget form; and
   `edit_container` likewise) — a new endpoint in **neither** set silently becomes transacted, which
   is correct for `create_instanced_subobject` and wrong for the two read verbs
4. the defining `.cpp`
5. `server.py` (`C:\Users\andre\Documents\GitHub\Eddie_v2\tools\ue5-mcp-bridge\server.py`) — **note
   the standing drift**: `get_property`, `set_property` and `list_object_properties` are *already*
   among the 20 endpoints with no MCP tool (`00_ARCHITECTURE.md` §"Known sync hazard"). Adding five
   more property endpoints without touching `server.py` leaves the entire Details-panel surface
   HTTP-only.
6. `README.md` + `docs/02_GOTCHAS.md` — §5d is the natural home; it already owns the objectPath routes.

Behaviour changes to `set_property` (G2, G4, G5, G8) touch only item 4 plus item 6, **but** every new
input parameter must be added to its `RejectUnknownParams` list at `MifBridgeNodes5.cpp:680-692` or it
is silently dropped — the precise failure that list exists to prevent
(`01_POSTMORTEMS.md`, `spawn_actor_in_level`'s dropped `mesh`).

---

## UNVERIFIED

Everything here is stated as unverified because it was not confirmed against engine source, or was
confirmed only partially. None of it is relied on above.

1. **`FPropertyEditorModule::CreatePropertyRowGenerator` as an alternative to reimplementing the
   evaluator.** `class FPropertyEditorModule : public IModuleInterface` at
   `Editor/PropertyEditor/Public/PropertyEditorModule.h:222` has **no export macro**, and
   `virtual TSharedRef<class IPropertyRowGenerator> CreatePropertyRowGenerator(const struct FPropertyRowGeneratorArgs& InArgs);`
   at `:369` likewise. Because it is `virtual` and reached through
   `FModuleManager::LoadModuleChecked<FPropertyEditorModule>("PropertyEditor")`, the call would
   dispatch through the vtable and link without an exported symbol — *in principle*. I did not build
   anything to confirm that, did not confirm what `IPropertyRowGenerator` costs per call, and did not
   confirm it works headlessly without a Slate window. Treat "use the real property tree" as an
   unproven option, not a plan.
2. **Exact behaviour of `EditConditionHides` in the bridge's context.** `PropertyNode.cpp:1366-1373`
   is verified; what a caller should *do* about a hidden-but-writable property is a policy question I
   did not resolve.
3. **Whether `set_property` writes to a widget template currently trigger the same
   reconstruct-and-dangle problem as G5.** The widget branch compiles the Widget Blueprint
   (`MifBridgeNodes5.cpp:929-937`) *after* the verification read, so the ordering looks safe, but I
   did not trace whether `FKismetEditorUtilities::CompileBlueprint` reinstances the `UWidgetTree`
   nodes the response already reported on.
4. **Sparse class data.** `PropertyNode.cpp` branches on `EPropertyNodeFlags::IsSparseClassData` in
   several places (e.g. `:2352`, `PropertyHandleImpl.cpp:505-508` skips propagation for it). I did not
   determine whether any object the bridge targets uses sparse class data, so no guard is specified
   for it.
5. **`UCurveVector` / `UBlendSpace` / `UMaterialInstanceConstant` end-to-end.** The property *shapes*
   are verified (`CurveVector.h:36`, `RichCurve.h:356`, `BlendSpace.h:862`,
   `MaterialInstance.h:83,86`). Whether writing them through reflection produces a correctly
   re-evaluated curve / rebuilt blend grid / recompiled material instance — as opposed to a correct
   value with a stale derived cache — was **not** tested. Material instances in particular have
   `set_material_parameter` already; prefer it over raw element addressing until this is settled.
6. **Element-level `FPropertyChangedEvent` array indexing on nested containers.** The panel builds
   the index map with `FPropertyValueImpl::GenerateArrayIndexMapToObjectNode`
   (declared/defined `PropertyHandleImpl.cpp:206`, called at `:539` for a value write and `:1115` for
   an add, `Private/` and unexported). I did not read that function, so the exact key format
   for a nested path (`Curves[1].Keys[0]`) is unknown; the spec above assumes one entry keyed by the
   container property's `GetName()`, which is what `UObject::PostEditChangeChainProperty` reads
   (`Obj.cpp:482-483`), but the multi-level case is unconfirmed.
7. **Interaction with `MifBridgeUndo.cpp` / `list_transactions`.** `CPF_NonTransactional` writes will
   not appear in the undo buffer; I did not check whether `list_transactions` would then report a
   transaction with no changes, or none at all.
