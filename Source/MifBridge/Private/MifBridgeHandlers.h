// MifBridge — endpoint registry + shared graph-edit helpers.
//
// Every endpoint is a free function with the signature (In, Out). Read-only endpoints
// fill Out; mutating endpoints call Modify()/MarkBlueprintAsStructurallyModified inside
// the single transaction opened by RunEndpoint (never their own). The registry is built
// in MifBridgeCommon.cpp from the declarations below.
#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

class UBlueprint;
class UEdGraph;
class UEdGraphNode;
class UEdGraphPin;
class UEdGraphSchema_K2;
class UClass;
class UScriptStruct;
class UWorld;                 // CollectPIEWorlds' parameter. CoreMinimal.h does NOT reach it
                              // (UObjectHierarchyFwd.h forward-declares only UObjectBase/UObjectBaseUtility),
                              // so without this line the header compiles only by accident of the shared PCH.
class UPackage;               // BackupPackage's parameter (same reason as UWorld above).
class FProperty;              // JsonToPropertyText / PropertyValueToTypedJson operate on reflection
                              // properties by pointer only; no UObject/UnrealType.h needed here.
class FBoolProperty;          // InspectEditCondition hands back the companion flag by pointer only.
class FMapProperty;           // FindMapEntryByKeyText / SampleMapKeyText - pointer only.
class UActorComponent;        // FComponentOriginRow / FindNativeComponentOnCDO - pointers only.
class USCS_Node;              // FComponentOriginRow - pointer only.
class UWidgetBlueprint;       // ResolvePropertyTarget's out-param - pointer only.
class UStruct;
class FUICommandList;         // MifBridgeUI.cpp's command-list cache hands these out by TSharedPtr only;
                              // Framework/Commands/UICommandList.h is a Slate header this contract does not need.
struct FEdGraphPinType;
enum EEdGraphPinDirection : int;

namespace MifBridge
{
	using FHandlerFn = TFunction<void(const TSharedRef<FJsonObject>& /*In*/, const TSharedRef<FJsonObject>& /*Out*/)>;

	// --- Registry / dispatch ------------------------------------------------
	const TMap<FString, FHandlerFn>& Handlers();
	TArray<FString> GetEndpointNames();
	/** Wrap the named handler in an editor-script guard + one transaction and run it. */
	void RunEndpoint(const FString& Endpoint, const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out);
	/** True if the endpoint runs a full compile and therefore must never execute inside another
	 *  endpoint's open transaction (batch uses this instead of its own literal list, which drifted). */
	bool IsCompileHeavyEndpoint(const FString& Endpoint);
	/** True while batch's single transaction is open, on this (the game) thread.
	 *  Two uses, both about telling "called directly" apart from "called inside batch":
	 *   - a handler that is batchable in general but has ONE compile-heavy branch refuses just that
	 *     branch (set_property's widget-Blueprint branch) instead of the whole endpoint being banned;
	 *   - RejectUnknownParams tolerates batch's routing key 'op' only where it can actually occur, so
	 *     a stray "op" on a direct HTTP call is a named error again rather than a silent no-op. */
	bool IsBatchTransactionOpen();
	/** RAII marker for the above. batch declares one beside its FScopedTransaction. Not reentrant by
	 *  design — a nested batch is refused before this is ever constructed twice. */
	struct FBatchTransactionScope
	{
		FBatchTransactionScope();
		~FBatchTransactionScope();
	};
	/** Called once by FMifBridgeServer::Start() after the route table is bound — after this,
	 *  RegisterExternalEndpoint (Public/MifBridgeEndpointRegistry.h) refuses loudly instead of
	 *  accepting an endpoint that would have no HTTP route (routes bind once per name). */
	void MarkRouteTableLive();
	/** The handler for an externally-registered (provider) endpoint, or null when the name is not
	 *  one. Exists so batch can mirror RunEndpoint's resolution order (built-ins, THEN externals):
	 *  H_batch consulted only Handlers(), so every kr_* op inside ops[] was answered
	 *  "unknown op: 'kr_list_events'" for an endpoint self_audit lists as present — a confidently
	 *  wrong error about the bridge's own surface. Returns a pointer INTO the registry map: valid
	 *  for the duration of the call, never stored. FExternalHandler and FHandlerFn are the same
	 *  TFunction type, so this leaks no Public/ type into this private header. */
	const FHandlerFn* FindExternalHandler(const FString& Endpoint);

	// --- Result helpers -----------------------------------------------------
	void Fail(const TSharedRef<FJsonObject>& Out, const FString& Message);
	bool IsOk(const TSharedRef<FJsonObject>& Out);

	// --- JSON field accessors (optional reads with defaults) ----------------
	FString JStr(const TSharedRef<FJsonObject>& In, const TCHAR* Field, const FString& Default = FString());
	double JNum(const TSharedRef<FJsonObject>& In, const TCHAR* Field, double Default = 0.0);
	int32 JInt(const TSharedRef<FJsonObject>& In, const TCHAR* Field, int32 Default = 0);
	bool JBool(const TSharedRef<FJsonObject>& In, const TCHAR* Field, bool Default = false);
	/** First non-empty of several accepted spellings — lets an endpoint accept {"node"} and
	 *  {"nodeGuid"} interchangeably instead of silently reading nothing. */
	FString JStrAny(const TSharedRef<FJsonObject>& In, std::initializer_list<const TCHAR*> Fields, const FString& Default = FString());
	/** As JBool, but tries several accepted spellings before falling back to Default. */
	bool JBoolAny(const TSharedRef<FJsonObject>& In, std::initializer_list<const TCHAR*> Fields, bool Default = false);
	/** As JInt, but tries several accepted spellings before falling back to Default. Born file-local
	 *  in MifBridgeUndo.cpp (Batch C) with a "local until a second file needs it" note; Batch D's
	 *  add_material_expression is that second file, so it moved here per its own eviction clause. */
	int32 JIntAny(const TSharedRef<FJsonObject>& In, std::initializer_list<const TCHAR*> Fields, int32 Default = 0);
	/** True if ANY of the spellings is present (regardless of value) — distinguishes
	 *  "caller explicitly passed false" from "caller omitted the field". */
	bool JHasAny(const TSharedRef<FJsonObject>& In, std::initializer_list<const TCHAR*> Fields);

	// --- Strict numeric reading (Batch L, defect 1) --------------------------
	// LIVE EVIDENCE. set_actor_transform {actorPath:"RollbackProbe",
	// location:{"x":"not-a-number","y":123,"z":456}} answered ok:true and left the actor at
	// {x:700,y:123,z:456}: y and z applied, x silently kept its previous value, and the response
	// echoed that MIXED location so it read as intentional. The caller got a transform it never asked
	// for. The cause is the shape of every accessor above — JNum's Default is returned both when the
	// field is ABSENT (correct) and when it is PRESENT but of the wrong JSON type (silent-ignore, the
	// bug class this codebase keeps paying for).
	//
	// It is worse than it looks, because UE's own coercions hide it:
	//   FJsonValueString::TryGetNumber(int32&)  ALWAYS returns true and LexFromString yields 0 for
	//     garbage (JsonValue.h:135) — so JInt on "abc" reports SUCCESS with 0.
	//   FJsonValueString::TryGetNumber(double&) accepts anything FString::IsNumeric() likes.
	//   FJsonValueBoolean::TryGetNumber(double&) turns true into 1.0 (JsonValue.h:201).
	// "TryGetNumber succeeded" is therefore NOT the same question as "the caller sent a number".

	/** Parse the WHOLE trimmed string as a number. Deliberately NOT LexTryParseString/Strtod: those
	 *  stop at the first unparseable character and report success for a PREFIX, which is precisely how
	 *  "not-a-float" becomes 0.0. Empty, prefix-only ("12abc"), trailing-garbage and
	 *  whitespace-separated forms are all rejected. Shared with ValidatePropertyText below, so the
	 *  JSON reader and the property-text validator agree on what "a number" is. */
	bool ParseWholeNumber(const FString& Text, double& OutValue);

	/** One JSON value as a number, strictly: EJson::Number, or a string that parses WHOLE. A boolean,
	 *  null, array, object or partly-numeric string is an error naming Where, the offending value and
	 *  the expected type — never a fallback. */
	bool JsonValueAsNumber(const TSharedPtr<FJsonValue>& Value, const FString& Where,
		double& OutValue, FString& OutError);

	/** Three-state field read. `Absent` is not an error — the caller simply did not supply the field
	 *  and InOut keeps what it came in holding, which is what makes "move only" work. `Invalid` means
	 *  SUPPLIED AND UNUSABLE and must never be silently downgraded to Absent. */
	enum class EJsonRead : uint8 { Absent, Read, Invalid };

	/** One numeric field, strictly. Where is quoted in the error ("location.x"). */
	EJsonRead ReadNumberField(const TSharedRef<FJsonObject>& In, const TCHAR* Field,
		const FString& Where, double& InOutValue, FString& OutError);

	/** {x,y,z} object or [x,y,z] array. ONE vector reader: it existed as ReadVector
	 *  (MifBridgeLevel.cpp), ReadVec (MifBridgeWorld.cpp), ReadVec3 (MifBridgeComponents.cpp) and
	 *  JNumFrom/ReadTransform (MifBridgeAuthoring.cpp) — four copies, every one of them silently
	 *  defaulting a non-numeric component, and two of them reading the array form through
	 *  FJsonValue::AsNumber(), which returns 0.0 for a string with no way to tell. A component the
	 *  caller OMITTED keeps InOutVec's incoming value (partial vectors are legitimate); a component the
	 *  caller SUPPLIED must be a number. An unrecognised key inside the object is rejected for the same
	 *  reason RejectUnknownParams exists: {"x":1,"y":2,"zz":3} is a typo, not a 2D vector. */
	EJsonRead ReadVectorField(const TSharedRef<FJsonObject>& In, const TCHAR* Field,
		FVector& InOutVec, FString& OutError);

	/** As ReadVectorField, but also accepts {pitch,yaw,roll}. x/y/z map to pitch/yaw/roll, which is the
	 *  mapping every MifBridge transform already documents and emits. */
	EJsonRead ReadRotatorField(const TSharedRef<FJsonObject>& In, const TCHAR* Field,
		FRotator& InOutRot, FString& OutError);

	/** As ReadVectorField, but a bare JSON number means a UNIFORM scale (spawn_many's convention). */
	EJsonRead ReadScaleField(const TSharedRef<FJsonObject>& In, const TCHAR* Field,
		FVector& InOutVec, FString& OutError);

	/** {x,y,z} where the OBJECT ITSELF is the vector rather than a named field on one — a points[]
	 *  entry in set_spline_points. Same component rules as ReadVectorField, which calls this. */
	bool ReadVectorObject(const TSharedRef<FJsonObject>& Obj, const FString& Where,
		FVector& InOutVec, FString& OutError);

	// --- Silent-ignore backstop ---------------------------------------------
	// The strict readers above cover the vector/rotator/scale sites that were rewritten. This covers
	// EVERYTHING ELSE in one place: JNum/JInt/JIntAny/JBool/JBoolAny record a violation whenever a
	// field is PRESENT but its JSON type is wrong, and RunEndpoint turns any recording into a FAILED
	// response. (It also cancels the transaction — which discards the undo entry and does NOT roll a
	// mutation back; see the corrected comment in RunEndpoint and PM-007. This sentence used to claim
	// the rollback.) One
	// implementation; every endpoint inherits it — including the ~20 open-coded
	// FVector(JNum(O,"x"),JNum(O,"y"),JNum(O,"z")) readers in Landscape / Navigation / PIE / Spatial /
	// Streaming / Viewport, which is how those are hardened without being individually rewritten.
	/** Clear the per-request record. Called by RunEndpoint before dispatch, never by a handler. */
	void ResetParamTypeViolations();
	/** How many "supplied but wrong JSON type" reads happened during this request. */
	int32 NumParamTypeViolations();
	/** One clause per violation, naming the field, the offending value and the expected type. */
	FString DescribeParamTypeViolations();

	/** Strict-params guard: fails Out (and returns true) naming EVERY key in In that is not in
	 *  AcceptedKeys, listing the accepted set. The audit's #1 bug class, live-proven by find_assets
	 *  (docs/audit/03_GAPS_AND_RISKS.md §7.1): an IGNORED parameter is worse than a rejected one —
	 *  the caller gets ok:true and then debugs the wrong subsystem. Matching is case-insensitive to
	 *  mirror how JStr/JBool/JInt find fields, so a key that WOULD be honoured is never rejected.
	 *  KeyNotes explains a specific unknown key where "unrecognised" would mislead (an
	 *  unimplemented capability rather than a typo). Born file-local in MifBridgeCooked.cpp
	 *  (Batch B); promoted here in Batch C so every handler file shares ONE implementation
	 *  (MifBridgeCommon.cpp) instead of drifting copies. */
	bool RejectUnknownParams(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out,
		std::initializer_list<const TCHAR*> AcceptedKeys, const TCHAR* AcceptedSummary,
		std::initializer_list<TPair<const TCHAR*, const TCHAR*>> KeyNotes = {});

	/** ONE writer for the asset-identity fields every asset-emitting endpoint returns, so no emitter
	 *  can spell them differently: objectPath (/Game/X/Foo.Foo_C) vs packageName (/Game/X/Foo).
	 *  Born as duplicate file-local statics in MifBridgeCooked.cpp and MifBridgeAssetOps.cpp, each
	 *  carrying an eviction clause to promote on the next header edit — collected here when the
	 *  unity build proved the point by failing on the duplicate definition (C2084). */
	void EmitAssetIdentity(const TSharedRef<FJsonObject>& Row, const FString& ObjectPath, const FString& PackageName);

	/** Path helpers shared by every endpoint that takes a /Game/ asset path. Defined in
	 *  MifBridgeAssetOps.cpp. Promoted out of file-local `static` for the same reason as
	 *  EmitAssetIdentity above: a second caller (MifBridgeCollision.cpp) compiled against them
	 *  only because the unity build merged the two .cpp files into one translation unit. That is
	 *  not linkage — UBT regroups the unity blobs whenever files are added or removed, so the
	 *  next new file in Private/ could have broken the build with "identifier not found".
	 *  NormalizePackagePath: "/Game/Foo/Bar.Bar" or "/Game/Foo/Bar" -> "/Game/Foo/Bar".
	 *  LoadAssetLenient: loads from either spelling, quietly (no warning on a miss). */
	FString NormalizePackagePath(const FString& InPath);
	UObject* LoadAssetLenient(const FString& Path);

	/** Every in-process PIE world, not just GEditor->PlayWorld. With RunUnderOneProcess and >1
	 *  client there are SEVERAL, and PlayWorld is only ever one of them — answering for "the" PIE
	 *  world without saying which is a silent wrong answer (the defect axis Q root-caused in
	 *  pie_status). Defined in MifBridgeCommon.cpp alongside the other shared helpers; shared so
	 *  level streaming selects worlds the same way list_pie_actors does. */
	void CollectPIEWorlds(TArray<UWorld*>& OutWorlds);

	// --- Shared helpers that used to exist as per-file copies ----------------
	// A unity build merges every unnamed namespace in a translation unit into ONE namespace
	// ([namespace.unnamed]/1), and `static` at namespace scope collapses the same way, so two files
	// that land in the same Module.MifBridge.N.cpp blob and define the same helper are a hard C2084 —
	// which is exactly how EmitAssetIdentity and CollectPIEWorlds broke the build. Blob membership is
	// a function of file SIZES and moves on its own, so "they're in different blobs today" is not a
	// defence. Everything below is declared here and defined ONCE in MifBridgeCommon.cpp.

	/** The PIE world when one is running, otherwise the editor world. Use this for anything a caller
	 *  may reasonably run DURING play (spatial queries, navigation, actor lookup for movement); use
	 *  EditorWorld() for anything that edits persistent level content. Endpoints that can be called in
	 *  both states should say which world answered — five file-local "current world" helpers had
	 *  silently split into two different policies (Spatial/Navigation preferred PIE, Streaming/World/PIE
	 *  did not), so during play check_overlaps answered about the play world while snap_actors_to_ground
	 *  answered about the editor world, with nothing in either response saying so. */
	UWorld* ActiveWorld();

	/** Find a placed actor by object path, object name, or display label — the three spellings every
	 *  level endpoint accepts, because delete_level_actor historically matched only name/label and a
	 *  path straight out of list_level_actors could not be deleted. ONE definition: this body existed
	 *  five times under five different names, so a fix to the matching rule reached one of them. */
	AActor* FindActorInWorld(UWorld* World, const FString& Query);

	/** The EDITOR world — never the PIE world, even during play. Was defined verbatim three times
	 *  (MifBridgeStreaming.cpp, MifBridgeWorld.cpp, and as GetEditorWorld in MifBridgePIE.cpp);
	 *  Streaming and World survived only because file sizes happened to put them in different unity
	 *  blobs, with ~8 KB of source growth anywhere in blob 1 enough to collide them. */
	UWorld* EditorWorld();

	/** JSON type noun for refusal text ("cannot convert JSON array"). Two copies had already
	 *  DIVERGED in caller-visible output — set_material_parameter said "boolean" and set_property
	 *  said "bool" for the same JSON type. "boolean" wins: it is the JSON spec's own noun. */
	const TCHAR* JsonTypeName(EJson Type);

	/** FBoolProperty::ImportText is CASE-SENSITIVE and word-based (PropertyBool.cpp:384-397):
	 *  1/True/Yes => true, 0/False/No => false — lowercase true/false FAIL. Normalise before import.
	 *  Two copies (MifBridgeNodes5.cpp, MifBridgeInherited.cpp) whose eviction clause had no trigger. */
	FString NormalizeBoolLiteral(const FString& In);

	/** {x,y,z} JSON. The two former copies (MifBridgeSpatial.cpp FVector form,
	 *  MifBridgeStreaming.cpp 3-double form) were already IN THE SAME unity blob and compiled only
	 *  because their arities differed — so Streaming silently called Spatial's implementation, and any
	 *  signature change on either side was an immediate C2084. */
	TSharedRef<FJsonObject> Vec3(const FVector& V);
	TSharedRef<FJsonObject> Vec3(double X, double Y, double Z);

	/** Walk a Details-panel dot path ("A.B.C") from Object: descend FStructProperty IN PLACE, hop
	 *  FObjectProperty to the pointed-to UObject. Yields the leaf FProperty, its VALUE address (what
	 *  ImportText_Direct/ExportText_Direct require — not the container), and the object PreEditChange/
	 *  PostEditChange must fire on. Dynamic containers (TArray/TMap/TSet) are not walkable mid-path;
	 *  a container may only be the LAST segment.
	 *
	 *  ONE implementation. It existed three times — MifBridgeNodes5.cpp (set_property, write),
	 *  MifBridgeNodes6.cpp (get_property/list_object_properties, read) and MifBridgeInherited.cpp
	 *  (override_inherited_component, write) — so a PM-003-class fix applied to one left the other two
	 *  exposed, which is the entire reason PM-003 has a number. The read callers take the same
	 *  non-const address and simply bind it to a const pointer; there is no second const overload to
	 *  drift. Errors name the missing segment, the struct it was looked for on, and near misses. */
	bool ResolvePropertyPath(UObject* Object, const FString& Path,
		FProperty*& OutLeaf, void*& OutLeafAddr, UObject*& OutLeafOwner, FString& OutError);

	/** ResolvePropertyPath, plus the FProperty of every segment from the head member down to the leaf —
	 *  exactly what an FEditPropertyChain needs, and exactly what the walker used to compute and throw
	 *  away. The chain is relative to OutLeafOwner, not to Object: crossing an FObjectProperty changes
	 *  which object PostEditChange fires on, so the chain RESTARTS at that boundary (a chain whose head
	 *  is not a member of the notified object's class is not a valid chain, and PropagatePostEditChange
	 *  check()s the active member node — Obj.cpp:660). ResolvePropertyPath forwards to this: one
	 *  walker, as PM-005 requires. */
	bool ResolvePropertyPathChain(UObject* Object, const FString& Path,
		FProperty*& OutLeaf, void*& OutLeafAddr, UObject*& OutLeafOwner,
		TArray<FProperty*>& OutChain, FString& OutError);

	/** " (did you mean 'Health', 'HealthMax'?)" for a name that was not found, or an empty string when
	 *  nothing is close. Appended to not-found errors so a caller that mistyped a name gets the fix in
	 *  the same response instead of "not found" and a round trip through list_variables. Matches, in
	 *  order: same name in a different CASE (the commonest miss, and the one a caller is least likely
	 *  to spot), substring either way, then edit distance within a third of the name's length. */
	FString NearMissSuggestion(const TArray<FString>& Available, const FString& Wanted, int32 MaxSuggestions = 3);

	/** Copy a package's file on disk to "<file>.bak". THE backup implementation: batch's inline copy
	 *  hardcoded FPackageName::GetAssetPackageExtension(), so a World package (.umap) produced NO
	 *  backup while the response still advertised one, and it discarded IFileManager::Copy's return
	 *  so Out["backup"] could name a .bak that was never written. A caller passes backup:true
	 *  precisely because what follows is destructive, so every failure path here is reported.
	 *  Returns false + OutError; on true, OutBackupPath is the file that now exists. */
	bool BackupPackage(UPackage* Package, FString& OutBackupPath, FString& OutError);

	/** Validate caller-supplied property TEXT against the TARGET PROPERTY TYPE, BEFORE any import.
	 *
	 *  LIVE EVIDENCE (Batch L, defect 2). override_inherited_component
	 *  {component:"Influence", properties:{"SphereRadius":"not-a-float"}} answered ok:true, applied:true
	 *  with wanted:"0.000000". UE's float importer (PropertyNumeric.cpp:125-137) accepts only [+-.0-9],
	 *  stops at the first character it does not like, has NO "nothing consumed" guard, and so parsed
	 *  "not-a-float" as 0.0 and returned SUCCESS. The post-write verification then compared after(0)
	 *  against wanted(0) and passed.
	 *
	 *  VERIFYING THAT THE WRITE LANDED DOES NOT VERIFY THAT THE VALUE WAS UNDERSTOOD. The anti-silence
	 *  guard cannot catch this class by construction — both sides of its comparison are derived from
	 *  the same misparse. The only place to catch it is BEFORE the import, against the destination
	 *  property's type. Numeric: the whole string must parse (ParseWholeNumber, so a prefix like
	 *  "12abc" is refused too). Bool: a recognised literal. Enum / byte-enum: a valid entry, with the
	 *  valid entries listed in the error. Object / class ref: a resolvable path or an explicit
	 *  None/null. Where a type cannot be validated reliably this returns true and says so in
	 *  OutError — a stated non-guarantee, never a silent guess.
	 *
	 *  Defined in MifBridgeNodes5.cpp beside AcceptedFormHint (whose text it quotes) and declared here,
	 *  so set_property, set_variable_default, override_inherited_component and the material-expression
	 *  writer all inherit ONE implementation. Do not copy it: PM-005. */
	bool ValidatePropertyText(const FProperty* Prop, const FString& Text, const FString& Where,
		FString& OutError, bool* bOutValidated = nullptr);

	/** Convert one JSON value into the UE export text that Prop's own importer accepts, refusing
	 *  anything that cannot convert faithfully (rather than importing "" and reporting success —
	 *  the array-wipe bug at MifBridgeNodes5.cpp:8-18). bDelimited is true for anything INSIDE a
	 *  container/struct literal. Defined in MifBridgeNodes5.cpp next to the conversion helpers it
	 *  depends on (AcceptedFormHint / CanonicaliseLeaf / the scratch bracket); declared here so
	 *  set_variable_default reuses THIS converter instead of growing a second one. */
	bool JsonToPropertyText(const TSharedPtr<FJsonValue>& Value, const FProperty* Prop,
		bool bDelimited, UObject* Owner, int32 Depth, const FString& Where,
		FString& OutText, FString& OutError);

	/** One `value` (either form) -> the export text Prop's own importer accepts. A STRING goes through
	 *  ValidatePropertyText (PM-006: verifying that a write landed does not verify that the value was
	 *  understood); anything else goes through JsonToPropertyText, which checks every leaf it converts.
	 *  OutForm is "string" or "json". THE conversion for every caller-supplied property value:
	 *  set_property, edit_container and reset_property_to_default share it rather than each growing a
	 *  two-branch dispatcher that can drift about which forms are accepted. */
	bool PropertyImportTextFromJson(const TSharedPtr<FJsonValue>& Value, const FProperty* Prop,
		UObject* Owner, const FString& Where, FString& OutText, FString& OutForm,
		bool& bOutTypeValidated, FString& OutTypeNote, FString& OutError);

	/** PM-003 as a callable: import Text into a SCRATCH copy of Prop (seeded from Seed when Seed is
	 *  non-null, so a partial struct literal keeps the members it did not mention, exactly as the
	 *  Details panel behaves) and copy the result to Dest only after the parse succeeded. ONE element,
	 *  so it is correct for a container row and for one slot of a C-array UPROPERTY. OutStagedText is
	 *  the canonical export of what was written, for a caller's before/after comparison.
	 *  ImportText_Direct parses IN PLACE and can consume/zero the destination before deciding the text
	 *  is invalid - never hand it a live address. */
	bool ImportPropertyTextSafely(const FProperty* Prop, const FString& Text, const void* Seed,
		void* Dest, UObject* Owner, FString& OutStagedText, FString& OutError);

	/** Typed JSON for one property value — the read counterpart of JsonToPropertyText, shared by
	 *  set_property's read-back and the get_property/list_object_properties readers. Also defined in
	 *  MifBridgeNodes5.cpp; the "promote the declaration when the header is next touched" note at the
	 *  top of that file is hereby honoured. */
	TSharedPtr<FJsonValue> PropertyValueToTypedJson(const FProperty* Prop, const void* ValueAddr, UObject* Owner);

	/** As PropertyValueToTypedJson, but for exactly ONE element. PropertyValueToTypedJson reports a
	 *  C-array UPROPERTY (int Foo[4]) as a JSON array of all ArrayDim elements, which is right when
	 *  the caller addressed the property - and reads off the end of the allocation when the caller
	 *  addressed FloatCurves[2] and the address is already element 2. Same definition site
	 *  (MifBridgeNodes5.cpp), same helper underneath; only the ArrayDim loop differs. */
	TSharedPtr<FJsonValue> PropertyValueToTypedJsonElement(const FProperty* Prop, const void* ValueAddr, UObject* Owner);

	// --- Component ORIGIN vocabulary + enumeration ---------------------------
	// Batch J shipped four route words in MifBridgeInherited.cpp as file-local literals; Batch N needs
	// the same four in MifBridgeComponents.cpp (list_components). Two files spelling the same state is
	// the PM-005 shape even when the copies are only string literals, so the words live here, once.
	extern const TCHAR* const kComponentOriginOwnSCS;      // "ownSCS"
	extern const TCHAR* const kComponentOriginParentSCS;   // "parentBlueprintSCS"
	extern const TCHAR* const kComponentOriginNative;      // "native"
	extern const TCHAR* const kComponentOriginNotFound;    // "notFound"

	/** One component reachable from a Blueprint, with WHERE it came from. Every pointer may be null
	 *  and must be checked. Filled by EnumerateBlueprintComponents. */
	struct FComponentOriginRow
	{
		FName            Name;                          // the name to pass to the inherited-component verbs
		const TCHAR*     Origin = nullptr;              // one of the kComponentOrigin* words above
		UClass*          ComponentClass = nullptr;
		UClass*          OwningClass = nullptr;         // BPGC whose SCS holds Node, or the class that
		                                                // DECLARES the native subobject (ACharacter for Mesh)
		USCS_Node*       Node = nullptr;                // ownSCS + parentBlueprintSCS
		USCS_Node*       AttachParentNode = nullptr;    // ownSCS only (SCS->FindParentNode)
		UActorComponent* Template = nullptr;            // SCS ComponentTemplate, or the CDO subobject (native)
		UActorComponent* OverrideTemplate = nullptr;    // parentBlueprintSCS with an ICH record already present
		FName            SubobjectName;                 // native: the REAL subobject name (Mesh -> CharacterMesh0)
		FName            AttachSocket;
		bool             bIsRoot = false;               // SCS root node, or (native) the CDO's RootComponent
		bool             bCanOverride = false;          // parentBlueprintSCS only
		bool             bEditableWhenInherited = true; // UActorComponent::IsEditableWhenInherited
		FString          CannotOverrideReason;
	};

	/** Every component reachable from Blueprint, in resolution order: this blueprint's OWN SCS, then
	 *  each parent BLUEPRINT's SCS up the UBlueprintGeneratedClass chain, then the NATIVE components
	 *  on the generated class's CDO. De-duplicated by name in that order, which is exactly the
	 *  precedence ResolveComponentOrigin applies, so a name that appears twice reports the route that
	 *  actually wins. Cap <= 0 means no cap.
	 *
	 *  READ-ONLY in the strong sense: it asks for the InheritableComponentHandler with
	 *  bCreateIfNecessary=FALSE, so enumerating a blueprint can never mint an ICH on the asset. That
	 *  is what lets list_components stay in IsReadOnlyEndpoint. */
	void EnumerateBlueprintComponents(UBlueprint* Blueprint, TArray<FComponentOriginRow>& OutRows, int32 Cap = 0);

	/** A NATIVE component on the child's own CDO, by PROPERTY name ("Mesh") or by SUBOBJECT name
	 *  ("CharacterMesh0") - the two differ and nobody guesses the second, which is the whole reason
	 *  this resolves from the object instead of composing a path from the caller's string.
	 *  OutMatchedBy is "property" or "subobject". Promoted out of MifBridgeInherited.cpp in Batch N so
	 *  list_components and get_inherited_component cannot disagree about what "native" means. */
	UActorComponent* FindNativeComponentOnCDO(UBlueprint* Blueprint, const FName Name, FString& OutMatchedBy);

	/** A component's EComponentCreationMethod as the word the Details panel and this audit use:
	 *  "Native" | "SimpleConstructionScript" | "UserConstructionScript" | "Instance" | "Unknown"
	 *  ("(none)" for a null component). Takes the COMPONENT rather than the enum so callers need no
	 *  extra include and a null is answerable rather than a crash. Was file-local in
	 *  MifBridgeInherited.cpp; list_components needs the same words, and a second copy under a second
	 *  name is the PM-005 failure the compiler never reports. */
	FString ComponentCreationMethodString(const UActorComponent* Component);

	// --- Reflection target resolution ---------------------------------------
	/** The ONE target resolver behind every property endpoint: `objectPath` (a placed actor's path IS
	 *  an objectPath), or `blueprintId`/`path` + `widgetName` for a widget template in a WBP's tree.
	 *  Writes the reason into Out and returns null on failure. When OutOwningWidgetBP is supplied it
	 *  receives the widget blueprint on that branch (set_property uses it to fence its compile out of
	 *  batch's transaction) and null otherwise.
	 *
	 *  It existed twice - inline in MifBridgeNodes5.cpp's set_property and as ResolveGenericTarget in
	 *  MifBridgeNodes6.cpp, whose own comment said the copy was deliberate ("duplicated here rather
	 *  than shared so this read-only file can't perturb the existing write path"). Batch N added four
	 *  more endpoints that need it; a sixth copy is how PM-005 happens, so the two became one. */
	UObject* ResolvePropertyTarget(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out,
		UWidgetBlueprint** OutOwningWidgetBP = nullptr);

	// --- Element-level property addressing ----------------------------------
	/** Everything the walker learned, including what it used to throw away. */
	struct FPropertyPathResolution
	{
		FProperty* Leaf = nullptr;
		void*      LeafAddr = nullptr;      // the element address when an accessor was used
		UObject*   LeafOwner = nullptr;
		TArray<FProperty*> Chain;           // declared members, head..leaf, relative to LeafOwner

		/** Base address of the struct/object that DECLARES Leaf. Null when Leaf is an element of a
		 *  DYNAMIC container (TArray/TSet/TMap): there is no declaring container for those, so
		 *  anything that needs a sibling property (EditCondition's companion flag) must not evaluate. */
		void*    LeafContainerAddr = nullptr;
		UStruct* LeafContainerStruct = nullptr;

		bool     bLeafIsElement = false;    // the last segment carried [i] / [k] / {k} / [Member=Text]
		int32    LeafCArrayIndex = 0;       // index WITHIN a C-array UPROPERTY (ArrayDim>1); else 0
		FProperty* ElementContainerProp = nullptr;  // the TArray/TSet/TMap the element came from
		void*    ElementContainerAddr = nullptr;    // ...and ITS address, which a set write needs in
		                                            // order to Rehash() afterwards
		int32    ElementIndex = INDEX_NONE;         // index inside that container (LOGICAL for a set:
		                                            // the Nth VALID element, matching [N]'s meaning)
		FString  ElementDescription;        // "OverrideMaterials[1]" - for responses and errors
		FString  ElementOrdering;           // "iteration" for sets/maps, empty otherwise
	};

	/** The accessor-aware walker. Grammar, on top of the existing dot path:
	 *
	 *      segment  := name accessor*
	 *      accessor := '[' index ']'           TArray | TSet | ArrayDim>1  (integer)
	 *                | '[' member '=' text ']' TArray of struct - linear find, first match
	 *                | '{' keytext '}'         TMap - the KEY
	 *                | '[' keytext ']'         TMap - alias for {keytext}
	 *
	 *  Disambiguation is by CONTAINER TYPE, never by the text, so nothing is ambiguous. Set and map
	 *  indices are sparse-aware (FindNthElementPtr / valid-index iteration), which is why the
	 *  resolution reports ElementOrdering:"iteration" - that order is not stable across a rehash.
	 *  Out-of-range names the index AND the actual length, always.
	 *
	 *  ResolvePropertyPath and ResolvePropertyPathChain forward to this: ONE walker, as PM-005
	 *  requires. */
	bool ResolvePropertyPathEx(UObject* Object, const FString& Path,
		FPropertyPathResolution& OutResolution, FString& OutError);

	// --- EditCondition (the Details panel's per-property gate) ---------------
	/** What the panel knows about meta=(EditCondition="...") on one property.
	 *
	 *  The mechanism is NOT the bOverride_ naming convention - that is only FPostProcessSettings'
	 *  house style. It is UPROPERTY metadata read at PropertyNode.cpp:230, with the companion flag
	 *  found as a sibling FBoolProperty on the gated property's own owner struct
	 *  (EditConditionContext.cpp:55). FEditConditionParser is unexported and lives in
	 *  Editor/PropertyEditor/Private, and PropertyEditor is not a link dependency of this module, so
	 *  the evaluator here is deliberately RESTRICTED to a single identifier or its negation - 713 of
	 *  the 837 editcondition metas in Runtime/**.h (85.2%). Everything else is reported as
	 *  "unevaluated" and NEVER guessed. */
	struct FEditConditionInfo
	{
		bool           bHasMeta = false;
		FString        MetaText;                 // the raw meta string
		FString        Kind = TEXT("none");      // none | bool | negatedBool | unevaluated
		FString        FlagName;
		FBoolProperty* FlagProp = nullptr;       // resolved companion, or null
		bool           bRequiredFlagValue = true;// the value the flag must hold for the gate to be OPEN
		bool           bEvaluated = false;       // false => bMet is a guess and must not be used
		bool           bMet = true;              // true when there is no gate at all
		bool           bHides = false;           // meta EditConditionHides: the row is hidden, not greyed
		bool           bInlineToggle = false;    // meta InlineEditConditionToggle on the FLAG itself
		FString        Note;                     // why it could not be evaluated
	};

	/** Fill Out for Prop. ContainerAddr is the base of the struct/object that DECLARES Prop (i.e.
	 *  FPropertyPathResolution::LeafContainerAddr) - pass null when there is none and the condition
	 *  is reported but not evaluated. Safe to call on any property; a property with no metadata comes
	 *  back Kind:"none", bMet:true. */
	void InspectEditCondition(const FProperty* Prop, const void* ContainerAddr, FEditConditionInfo& Out);

	/** ClampMin/ClampMax/UIMin/UIMax/Multiple/ArrayClamp as authored, for reporting. UIMin/UIMax are
	 *  SLIDER BOUNDS ONLY and are never enforced by anything, including the panel - they are reported
	 *  and never acted on. */
	struct FPropertyClampInfo
	{
		bool    bNumeric = false;
		bool    bHasClampMin = false, bHasClampMax = false;
		bool    bHasUIMin = false,    bHasUIMax = false;
		double  ClampMin = 0.0, ClampMax = 0.0;
		FString ClampMinText, ClampMaxText, UIMinText, UIMaxText, MultipleText, ArrayClampText;
	};
	void InspectClamps(const FProperty* Prop, FPropertyClampInfo& Out);

	/** True when ValueText (which must already have passed ValidatePropertyText) falls outside
	 *  ClampMin..ClampMax. OutMeta is "ClampMin" or "ClampMax", OutLimit the authored bound.
	 *  ImportText NEVER applies these - CoreUObject's importers do not read the metadata at all; only
	 *  the panel's TYPED numeric setters clamp (PropertyHandleImpl.cpp:870-931). So a bridge write
	 *  can legally exceed a clamp the panel would refuse, and the only honest options are to report
	 *  it or to coerce and say so. Silently exceeding it is not one of them. */
	bool DescribeClampViolation(const FProperty* Prop, const FString& ValueText,
		FString& OutMeta, FString& OutLimit);

	/** Export one property value as PLAIN comparable text - export text with any surrounding quotes
	 *  stripped. The exact normalisation the path walker's '{Key}' and '[Member=Value]' matching uses,
	 *  shared so a key that resolves inside a propertyPath also resolves as edit_container's `key`. */
	FString ExportPropertyTextForMatch(const FProperty* Prop, const void* Addr, UObject* Owner);

	/** The INTERNAL index of the map entry whose key exports as KeyText (case-insensitive), or
	 *  INDEX_NONE. Deliberately a linear compare of exported text rather than a hash probe: a key type
	 *  with no GetTypeHash is then still READABLE, and only the mutating path has to refuse it. */
	int32 FindMapEntryByKeyText(const FMapProperty* MapProp, const void* MapAddr, const FString& KeyText, UObject* Owner);

	/** A bounded, comma-joined sample of a map's keys, so a "no such key" refusal is actionable. */
	FString SampleMapKeyText(const FMapProperty* MapProp, const void* MapAddr, UObject* Owner, int32 Max);

	/** Cooked or container-only package: PKG_Cooked / bIsCookedForEditor first (authoritative), then
	 *  "present in a mounted IoStore container with no loose file". The IoDispatcher location is
	 *  checked EXPLICITLY rather than inferring "no loose file => cooked", because a brand-new
	 *  never-saved asset also has no loose file and calling it cooked would tell a caller their own
	 *  fresh asset cannot be edited. Born file-local in MifBridgeMaterials.cpp; promoted in Batch N
	 *  when edit_container / reset_property_to_default needed the same question answered the same way
	 *  (a second cooked test under a second name is the PM-005 failure the compiler never reports). */
	bool IsCookedOrContainerPackage(const UPackage* Package);

	/** Append one line to Out's "warnings" array, creating it on first use. A warning is for a call
	 *  that SUCCEEDED and did something the caller would want to know about; a refusal is a Fail. */
	void AddWarning(const TSharedRef<FJsonObject>& Out, const FString& Text);

	// --- Resolution ---------------------------------------------------------
	const UEdGraphSchema_K2* K2();

	UBlueprint* ResolveBlueprint(const FString& Path, FString& OutError);
	/** Graded "why isn't there a blueprint here" message: cooked (generated class only, graphs
	 *  stripped — names the decompile/editable-copy route) vs wrong asset type vs no such package. */
	FString DescribeMissingBlueprint(const FString& Path);
	/** Reads "blueprintId" (or "path"); on failure writes error into Out and returns null. */
	UBlueprint* ResolveBlueprintField(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out);

	/** Every graph in the blueprint INCLUDING nested ones — collapsed/composite node bodies, anim
	 *  state machines, their states, and transition rule graphs (reached via UEdGraphNode::GetSubGraphs). */
	void GatherGraphs(UBlueprint* Blueprint, TArray<UEdGraph*>& OutGraphs);
	/** Dotted path from the blueprint to the graph ("AnimGraph.Locomotion.Idle"). A top-level graph
	 *  yields just its own name, so existing graphIds are unchanged. */
	FString GraphNamePathOf(UBlueprint* Blueprint, UEdGraph* Graph);
	FString GraphIdOf(UBlueprint* Blueprint, UEdGraph* Graph);
	UEdGraph* ResolveGraph(const FString& GraphId, UBlueprint*& OutBlueprint, FString& OutError);
	/** Reads "graphId"; on failure writes error into Out and returns null. */
	UEdGraph* ResolveGraphField(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out, UBlueprint*& OutBlueprint);

	/** Globally locate a node by its (engine-unique) NodeGuid via TObjectIterator. */
	UEdGraphNode* ResolveNode(const FString& GuidStr, FString& OutError);
	UEdGraphNode* ResolveNodeField(const TSharedRef<FJsonObject>& In, const TCHAR* Field, const TSharedRef<FJsonObject>& Out);

	/** Find a pin by name (case-insensitive, with exec aliases). PreferDir breaks ties. */
	UEdGraphPin* FindPin(UEdGraphNode* Node, const FString& PinName, EEdGraphPinDirection PreferDir, bool bRequireDir);
	/** Follow a knot (reroute) chain to the first non-knot terminal pin. */
	UEdGraphPin* SkipKnots(UEdGraphPin* Pin);

	/** Resolve a class name. An EMPTY/"self" name resolves to ContextBP's own class — callers that
	 *  require an explicit class must use ResolveClassStrict, or a typo'd param silently self-targets. */
	UClass* ResolveClass(const FString& Name, UBlueprint* ContextBP);
	/** ResolveClass, but an empty/whitespace name is an ERROR rather than "self". Use this wherever
	 *  the class is mandatory (cast targets, spawn classes, component/interface classes): the empty
	 *  case used to fall through to the blueprint's own class and produce a silent self-cast/self-spawn.
	 *  ParamName is quoted in the error so the caller learns which key it should have passed. */
	UClass* ResolveClassStrict(const FString& Name, UBlueprint* ContextBP, const TCHAR* ParamName, FString& OutError);
	/** ResolveClassStrict + Fail(Out) on error. Returns null when it has already written the failure. */
	UClass* ResolveClassStrictField(const TSharedRef<FJsonObject>& In, std::initializer_list<const TCHAR*> Fields,
		UBlueprint* ContextBP, const TSharedRef<FJsonObject>& Out);
	UScriptStruct* ResolveStruct(const FString& Name);
	/** Parse a JSON array of {name, type, container?, valueType?} into (name, pin-type) pairs.
	 *  ONE definition: MifBridgeNodes2.cpp had ParsePinSpecs and MifBridgeDelegates.cpp had
	 *  ParseDispatcherParams with the same signature shape and an effectively identical body. The
	 *  Delegates copy's stated reason ("kept file-local to avoid header/type coupling") did not hold —
	 *  this header already forward-declares FEdGraphPinType and declares MakePinType, which is what
	 *  the parser calls. Field is quoted in errors so a caller learns which array was rejected.
	 *  A non-object entry is now an ERROR naming its index, not a silent skip. */
	bool ParsePinSpecs(const TSharedRef<FJsonObject>& In, const TCHAR* Field,
		TArray<TPair<FName, FEdGraphPinType>>& OutPins, FString& OutError);

	/** Build a pin type from the string grammar. Container is array|set|map (empty = single value).
	 *  For container="map", TypeStr is the KEY type and ValueTypeStr is the VALUE type — a map needs
	 *  both, the value going into FEdGraphPinType::PinValueType. */
	bool MakePinType(const FString& TypeStr, const FString& Container, FEdGraphPinType& OutType, FString& OutError,
		const FString& ValueTypeStr = FString());
	bool IsValidIdentifier(const FString& Name);

	// --- Node spawning ------------------------------------------------------
	/** Add to graph, assign GUID, PostPlacedNewNode, position, then AllocateDefaultPins.
	 *  Call any SetFromFunction / SetMacroGraph / VariableReference setup BEFORE this. */
	void PlaceAndInit(UEdGraph* Graph, UEdGraphNode* Node, int32 X, int32 Y);

	// --- Shared mutation helpers (used by node + recipe endpoints) ----------
	void MarkStructural(UBlueprint* Blueprint);
	void EmitNode(const TSharedRef<FJsonObject>& Out, UEdGraphNode* Node);
	/** Resolve pins by name (dir-preferring), tunnel knots, CanCreateConnection, TryCreateConnection.
	 *  Returns false + reason on failure. bBreakFirst clears both pins before wiring. */
	bool ConnectPinsChecked(UEdGraphNode* SrcNode, const FString& SrcPinName,
		UEdGraphNode* DstNode, const FString& DstPinName, bool bBreakFirst, FString& OutError);
	/** Insert a node into an exec chain AFTER SourceOut: SourceOut -> InsertIn, and every pin SourceOut
	 *  used to drive moves onto InsertOut. OutMovedTargets is a count of links ACTUALLY re-made.
	 *
	 *  Every connection is checked with CanCreateConnection BEFORE the first BreakPinLinks. The three
	 *  hand-rolled splices this replaces (recipe_add_debug_print/_reset_and_loop/_override_and_call_parent
	 *  via SpliceAfter, recipe_splice_before_parent, and splice_into_exec) all destroyed the old wiring
	 *  first, then discarded TryCreateConnection's bool, then reported OldTargets.Num() as
	 *  reconnectedTargets — so a refused connection left the exec chain SEVERED and answered ok:true
	 *  with a count of links that did not exist. A severed exec chain compiles clean and fails at
	 *  runtime: the add_cast/PM-001 profile, in the endpoints whose whole selling point is "one call
	 *  wires the cluster". Returns false + OutError naming the pins that could not be joined. */
	bool SpliceExecAfter(UEdGraphPin* SourceOut, UEdGraphPin* InsertIn, UEdGraphPin* InsertOut,
		int32& OutMovedTargets, FString& OutError);

	/** Insert a node into an exec chain BEFORE TargetIn: every existing upstream of TargetIn is
	 *  re-pointed at EntryIn, and ExitOut is wired to TargetIn. Same validate-before-destroy rule and
	 *  the same honest count as SpliceExecAfter. */
	bool SpliceExecBefore(UEdGraphPin* TargetIn, UEdGraphPin* EntryIn, UEdGraphPin* ExitOut,
		int32& OutMovedUpstreams, FString& OutError);

	/** UEdGraphSchema_K2::TrySetDefaultValue is void and the schema silently refuses a literal that
	 *  does not parse for the pin type, so `set_pin_default {value:"banana"}` on an int pin answered
	 *  ok:true. Snapshots DefaultValue/DefaultObject/DefaultTextValue around the call and reports what
	 *  actually landed. Returns false + OutError when a requested change did not take. */
	bool SetPinDefaultChecked(UEdGraphPin* Pin, const FString& Value,
		FString& OutBefore, FString& OutAfter, bool& bOutChanged, FString& OutError);

	/** First UFunction on Class matching any of the candidate names (for versioned pairs like Greater_*). */
	UFunction* ResolveFunctionByCandidates(UClass* Class, const TArray<FString>& Names);
	/** Create an empty Blueprint function graph (entry + result terminators); set pure via entry ExtraFlags.
	 *  Returns the graph or null+error. Caller adds user-defined pins to the entry/result nodes. */
	UEdGraph* CreateFunctionGraph(UBlueprint* Blueprint, const FString& Name, bool bPure, FString& OutError);

	/** Apply the member-variable flag set (replicated / repNotify / saveGame / transient / config /
	 *  instanceEditable / blueprintReadOnly / exposeOnSpawn / advancedDisplay / interp / deprecated /
	 *  category / tooltip) named in In onto Blueprint's variable VarName. Only keys actually present in
	 *  In are touched, so it is safe to call for both create (add_variable) and update (set_variable_flags).
	 *  Writes the resulting state into Out->"flags". Returns false + OutError on a hard failure. */
	bool ApplyVariableFlags(UBlueprint* Blueprint, const FName& VarName, const TSharedRef<FJsonObject>& In,
		const TSharedRef<FJsonObject>& Out, FString& OutError);
	/** Serialize a member variable's current flag state (used by list_variables and set_variable_flags). */
	TSharedRef<FJsonObject> SerializeVariableFlags(UBlueprint* Blueprint, const struct FBPVariableDescription& Var);

	// --- Compile ------------------------------------------------------------
	/** Compile the blueprint and write {ok,numErrors,numWarnings,messages[]} into Out.
	 *  Shared by the compile/validate endpoints and batch's compileAtEnd. */
	void CompileBlueprintInto(UBlueprint* Blueprint, const TSharedRef<FJsonObject>& Out);

	// --- JSON serializers ---------------------------------------------------
	TSharedRef<FJsonObject> SerializePinType(const FEdGraphPinType& Type);
	TSharedRef<FJsonObject> SerializePin(const UEdGraphPin* Pin);
	TSharedRef<FJsonObject> SerializeNode(const UEdGraphNode* Node, bool bIncludePins);

	// --- Editor UI invocation (Batch O, MifBridgeUI.cpp) ---------------------
	// FInputBindingManager enumerates COMMANDS but stores no command LISTS - RegisterCommandList is a
	// pure broadcast that keeps nothing (InputBindingManager.cpp:561-569) - and the two global lists
	// that do exist (FLevelEditorModule::GetGlobalLevelEditorActions, IMainFrameModule::
	// GetMainFrameCommandBindings) live in modules MifBridge does not depend on: LevelEditor and
	// MainFrame are a PRIVATE dep / DynamicallyLoadedModuleNames of UnrealEd (UnrealEd.Build.cs:147,
	// :206), so they are NOT transitively reachable and Batch O deliberately did not add them.
	//
	// What IS public is FInputBindingManager::OnRegisterCommandList (a public multicast member), which
	// five engine sites broadcast onto - LevelEditor.cpp:281, MainFrameModule.cpp:600,
	// SLevelViewport.cpp:1381, SContentBrowser.cpp:678, Sequencer.cpp:668-669. MifBridge loads at
	// PostEngineInit (LaunchEngineLoop.cpp:4838-4840, inside EngineLoop.Init()), and
	// UnrealEdGlobals.cpp:111 runs EngineLoop.Init() BEFORE :171 builds the editor UI - so subscribing
	// in FMifBridgeModule::StartupModule precedes all five. Anything registered earlier is invisible,
	// and every response that depends on this says so rather than implying the cache is complete.
	//
	// Declared here because TWO translation units need them (MifBridge.cpp subscribes/unsubscribes,
	// MifBridgeUI.cpp reads) - a second copy is the PM-005 bug class. Defined ONCE in MifBridgeUI.cpp.
	/** Subscribe the command-list observer. Call once, from module startup, on the game thread. */
	void SubscribeCommandListObserver();
	/** Unsubscribe and drop the cache. Call from module shutdown. */
	void UnsubscribeCommandListObserver();
	/** True once SubscribeCommandListObserver has run - reported to callers so an empty cache reads as
	 *  "nothing has registered yet" rather than "this editor has no commands". */
	bool AreCommandListsObserved();
	/** Every binding context that currently has at least one LIVE cached command list. */
	void GetCachedCommandListContexts(TArray<FName>& OutContexts);
	/** The live command lists cached for one binding context. Dead (weak-expired) entries are dropped,
	 *  never returned as null - a stale viewport's list must not be invoked. */
	void GetCachedCommandLists(FName Context, TArray<TSharedPtr<const FUICommandList>>& OutLists);

	/** THE one call site for UEngine::Exec in this module. Engine.h:2224 is ENGINE_API and PUBLIC;
	 *  UEngine::Exec_Editor (Engine.h:2229) and UEditorEngine::Exec_Editor (EditorEngine.h:817) are both
	 *  under `protected:` despite their export macros - exported and unusable, the same shape as the
	 *  UClass::IsA incident. Never reach for those.
	 *
	 *  OutText, when non-null, receives what the command wrote to its own FOutputDevice - and the device
	 *  TEES to GLog, so the existing "run the command, then tail the log" workflow is byte-for-byte
	 *  unchanged. Pass nullptr for exactly the old behaviour (Ar = *GLog).
	 *
	 *  Returns UEngine::Exec's bool: TRUE means a handler CLAIMED the command, not that it succeeded. */
	bool RunEngineExec(UWorld* World, const FString& Command, FString* OutText);

	// --- Endpoint declarations ---------------------------------------------
#define MIF_DECL(Name) void H_##Name(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)

	// Session / assets
	MIF_DECL(open_blueprint);
	MIF_DECL(list_blueprints);
	MIF_DECL(save_blueprint);
	MIF_DECL(save_package);
	MIF_DECL(backup_blueprint);

	// Introspection
	MIF_DECL(list_graphs);
	MIF_DECL(list_nodes);
	MIF_DECL(get_node);
	MIF_DECL(list_variables);
	MIF_DECL(list_functions);
	MIF_DECL(find_nodes);

	// Variables
	MIF_DECL(add_variable);
	MIF_DECL(rename_variable);
	MIF_DECL(remove_variable);
	MIF_DECL(set_variable_type);
	MIF_DECL(retarget_variable_node);
	MIF_DECL(set_variable_default);
	MIF_DECL(set_variable_flags);

	// Nodes
	MIF_DECL(add_function_call);
	MIF_DECL(add_variable_get);
	MIF_DECL(add_variable_set);
	MIF_DECL(add_branch);
	MIF_DECL(add_macro_instance);
	MIF_DECL(add_get_array_item);
	MIF_DECL(add_override_event);
	MIF_DECL(add_component_bound_event);
	MIF_DECL(add_parent_call);
	MIF_DECL(add_cast);
	MIF_DECL(set_cast_purity);
	MIF_DECL(move_node);
	MIF_DECL(remove_node);
	MIF_DECL(refresh_node);

	// Pins / wiring
	MIF_DECL(connect_pins);
	MIF_DECL(disconnect_pin);
	MIF_DECL(reconnect_pin);
	MIF_DECL(set_pin_default);
	MIF_DECL(splice_into_exec);
	MIF_DECL(add_pin);
	MIF_DECL(remove_pin);

	// Nodes (phase 3 additions)
	MIF_DECL(add_custom_event);
	MIF_DECL(add_enhanced_input_action);
	MIF_DECL(add_make_struct);
	MIF_DECL(add_break_struct);
	MIF_DECL(add_self);
	MIF_DECL(add_literal);
	MIF_DECL(create_function);
	MIF_DECL(set_function_flags);
	MIF_DECL(rename_function);
	MIF_DECL(rename_event);
	MIF_DECL(rename_event_dispatcher);
	MIF_DECL(create_blueprint);
	MIF_DECL(reparent_blueprint);
	MIF_DECL(resolve_struct);
	MIF_DECL(describe_class);
	MIF_DECL(list_enum_values);

	// Cooked / mounted-container introspection (MifBridgeCooked.cpp) - read-only.
	MIF_DECL(list_mounted_containers);
	MIF_DECL(find_assets);
	MIF_DECL(describe_package);
	MIF_DECL(diagnose_landscape);
	MIF_DECL(diagnose_landscape_draws);

	// Composite recipes (§10)
	MIF_DECL(recipe_add_debug_print);
	MIF_DECL(recipe_reset_and_loop);
	MIF_DECL(recipe_override_and_call_parent);
	MIF_DECL(recipe_splice_before_parent);
	MIF_DECL(recipe_argmax_over_components);

	// Pipeline hooks
	MIF_DECL(read_modloader_log);
	MIF_DECL(trigger_cook);

	// Phase 3 breadth — graph nodes
	MIF_DECL(add_timeline);
	MIF_DECL(add_class_cast);
	MIF_DECL(add_switch_enum);
	MIF_DECL(add_switch_int);
	MIF_DECL(add_switch_string);
	MIF_DECL(add_enum_literal);
	MIF_DECL(set_pin_type);

	// Phase 3 breadth — event dispatchers (multicast delegates)
	MIF_DECL(add_event_dispatcher);
	MIF_DECL(add_call_dispatcher);
	MIF_DECL(add_bind_dispatcher);
	MIF_DECL(list_dispatchers);

	// Phase 3 breadth — components (SimpleConstructionScript)
	MIF_DECL(add_component);
	MIF_DECL(list_components);
	MIF_DECL(remove_component);
	// Inherited components (MifBridgeInherited.cpp) — the Details-panel write path via
	// UInheritableComponentHandler. Only components inherited from a parent BLUEPRINT's SCS can be
	// overridden; native ones are excluded by the engine itself (SubobjectData.cpp:148), so
	// get_inherited_component reports the origin and hands back the CDO-subobject path instead.
	MIF_DECL(get_inherited_component);
	MIF_DECL(override_inherited_component);
	MIF_DECL(revert_inherited_component);
	// Back in MifBridgeComponents.cpp. It sat under the MifBridgeInherited.cpp heading above, which is
	// exactly the input that manufactures duplicates: an agent holding an ownership fence on "the
	// inherited-components file" reads this header, believes it owns set_component_transform, cannot
	// find it in that file, and writes a second one.
	MIF_DECL(set_component_transform);

	// Phase 3 breadth — interfaces
	MIF_DECL(add_interface);
	MIF_DECL(remove_interface);
	MIF_DECL(list_interfaces);

	// Phase 3 breadth — datatables (read-only)
	MIF_DECL(list_datatables);
	MIF_DECL(read_datatable);
	MIF_DECL(get_datatable_row);

	// Phase 3 completion — functions / interfaces / datatable write
	MIF_DECL(implement_interface_function);
	MIF_DECL(remove_function);
	MIF_DECL(write_datatable_rows);
	MIF_DECL(delete_datatable_rows);

	// Phase 3 completion — common nodes
	MIF_DECL(add_sequence);
	MIF_DECL(add_spawn_actor);
	MIF_DECL(add_create_widget);
	MIF_DECL(add_get_subsystem);
	MIF_DECL(add_make_array);
	MIF_DECL(add_make_map);
	MIF_DECL(add_format_text);
	MIF_DECL(add_get_data_table_row);
	MIF_DECL(add_comment);

	// UWidgetBlueprint asset endpoints (Is-Variable / bindings / widget tree) + generic property setter
	MIF_DECL(set_widget_is_variable);
	MIF_DECL(add_widget_binding);
	MIF_DECL(remove_widget_binding);
	MIF_DECL(add_tree_widget);
	MIF_DECL(remove_tree_widget);
	// Widget-tree TOPOLOGY (MifBridgeWidgets.cpp). add/remove could create and delete but never read
	// the shape or rearrange it, so callers were stuck at get_property "Slot" one widget at a time.
	MIF_DECL(list_tree_widgets);
	MIF_DECL(duplicate_tree_widget);
	MIF_DECL(wrap_tree_widget);
	MIF_DECL(move_tree_widget);

	MIF_DECL(set_property);
	MIF_DECL(get_property);
	MIF_DECL(list_object_properties);

	// DETAILS-PANEL PARITY (MifBridgeDetails.cpp), Batch N. Everything the panel does to a property
	// that set_property/get_property could not: the metadata + editability surface, the element
	// lifecycle inside a container, and the yellow-arrow pair (reset to default / diff vs default).
	// No new module - CoreUObject + Engine, both already public dependencies.
	//   describe_property           READ-ONLY. Flags, metadata, EditCondition + its resolved flag and
	//                               met/unmet state, clamps, persistence, container shape, and the
	//                               panel's own editableByHuman predicate recomputed.
	//   diff_properties_vs_default  READ-ONLY. What does this object actually OVERRIDE vs its
	//                               archetype - the question the panel answers with a yellow arrow.
	//   edit_container              TRANSACTED. add/insert/remove/clear/swap/resize/setKey via
	//                               FScriptArrayHelper / FScriptMapHelper / FScriptSetHelper. Runs no
	//                               compile, so the blanket transaction is both sufficient and
	//                               desirable; the widget-template form is REFUSED rather than
	//                               promoting the endpoint to self-managed for a case that has no
	//                               interesting containers.
	//   reset_property_to_default   TRANSACTED, same reasoning and the same widget refusal.
	MIF_DECL(describe_property);
	MIF_DECL(diff_properties_vs_default);
	MIF_DECL(edit_container);
	MIF_DECL(reset_property_to_default);

	// NAVIGATION (MifBridgeNavigation.cpp) — nav bounds, mesh build, and nav-driven movement.
	// Building is asynchronous: request then poll nav_status, never block.
	MIF_DECL(add_nav_volume);
	MIF_DECL(build_navmesh);
	MIF_DECL(nav_status);
	MIF_DECL(move_actor_to);

	// Level-authoring throughput + material control (MifBridgeAuthoring.cpp). Each of these was a
	// hard blocker when building a 426-actor town by hand.
	MIF_DECL(spawn_many);
	MIF_DECL(duplicate_actors);
	MIF_DECL(create_material_instance);
	MIF_DECL(set_material_parameter);
	MIF_DECL(add_foliage_instances);

	// LANDSCAPE authoring (MifBridgeLandscape.cpp) — real terrain, not a stretched plane.
	// Every argument is in WORLD units; the vertex-space conversion lives inside the handlers.
	// create_landscape is self-managed: Import() builds and registers heightmap/weightmap textures,
	// which must not sit inside RunEndpoint's blanket transaction.
	MIF_DECL(create_landscape);
	MIF_DECL(sculpt_landscape);
	MIF_DECL(paint_landscape);
	MIF_DECL(bind_landscape_rvt);
	MIF_DECL(landscape_info);

	// WORLD lifecycle + spline authoring + ground snapping (MifBridgeWorld.cpp).
	// new_level/load_level force bPromptUserToSave=false — a modal blocks the game thread, which is
	// also the thread this HTTP server runs on, so a prompt here deadlocks an unattended run.
	// set_spline_points is what makes NPCs walk: BP_SegmentedPathTaskMarker routes from its PathSpline.
	MIF_DECL(new_level);
	MIF_DECL(save_level_as);
	MIF_DECL(load_level);
	MIF_DECL(set_spline_points);
	MIF_DECL(get_spline_points);
	MIF_DECL(snap_actors_to_ground);

	// VIEWPORT control (MifBridgeViewport.cpp) — moving the camera the USER sees, as opposed to
	// capture_camera which spawns a transient scene-capture and changes nothing on screen.
	// Read-only in the transaction sense: a camera move dirties no asset.
	MIF_DECL(set_viewport_camera);
	MIF_DECL(focus_viewport);
	MIF_DECL(get_viewport_camera);

	// SPATIAL awareness + VISUAL feedback (MifBridgeSpatial.cpp) — numbers for correctness,
	// pixels for taste. These exist because a scene built blind came out wrong in ways that were
	// all detectable from data.
	MIF_DECL(get_actor_bounds);
	MIF_DECL(check_overlaps);
	MIF_DECL(trace_ground);
	MIF_DECL(capture_camera);
	MIF_DECL(scene_report);

	// Play-In-Editor control + runtime observation (MifBridgePIE.cpp).
	// start/stop are DEFERRED by the engine and these handlers run ON the game thread, so they
	// request and return — the caller polls pie_status. Blocking here would deadlock PIE startup.
	MIF_DECL(start_pie);
	MIF_DECL(stop_pie);
	MIF_DECL(pie_status);
	MIF_DECL(list_pie_actors);
	MIF_DECL(spawn_actor_in_pie);
	MIF_DECL(run_console_captured);

	// LEVEL / placed-actor editing (MifBridgeLevel.cpp) — operates on the level currently open.
	// The value is actorPath: set_property already edits a placed actor once you have one.
	MIF_DECL(list_level_actors);
	MIF_DECL(spawn_actor_in_level);
	MIF_DECL(set_actor_transform);
	MIF_DECL(set_actor_label);
	MIF_DECL(delete_level_actor);
	MIF_DECL(select_level_actors);

	// User-defined STRUCT and ENUM authoring (MifBridgeUserTypes.cpp).
	// Blueprint-only types; native C++ structs/enums cannot be edited.
	MIF_DECL(create_struct);
	MIF_DECL(list_struct_members);
	MIF_DECL(add_struct_member);
	MIF_DECL(remove_struct_member);
	MIF_DECL(create_enum);
	MIF_DECL(add_enum_value);
	MIF_DECL(remove_enum_value);

	// Animation ASSET introspection (MifBridgeAnimation.cpp) — read-only.
	// Animation BLUEPRINTS go through the normal graph endpoints; GatherGraphs recurses into
	// nested graphs, so state machines / states / transition rules are reachable there.
	MIF_DECL(describe_animation);
	MIF_DECL(list_animations);
	// One endpoint for the whole UAnimGraphNode_* family: UAnimGraphNode_Base derives from UK2Node,
	// so anim nodes place and wire exactly like K2 nodes.
	MIF_DECL(add_anim_node);

	// Asset lifecycle — confirm-gated (delete/rename), /Game/-only, no dialogs
	MIF_DECL(delete_asset);
	MIF_DECL(rename_asset);
	MIF_DECL(duplicate_asset);
	// Static-mesh simple collision. The StaticMeshEditor's collision toolbar cannot be reached
	// through invoke_editor_command (its FUICommandList is only broadcast when that editor is
	// actually opened), and writing BodySetup.AggGeom via set_property skips the propagation
	// step, so these call the engine's own generators directly. See MifBridgeCollision.cpp.
	MIF_DECL(remove_collision);
	MIF_DECL(add_simplified_collision);
	// reference queries — the asset registry's dependency graph, exposed
	MIF_DECL(get_referencers);
	MIF_DECL(get_dependencies);
	MIF_DECL(audit_unused);

	// Reconstructor unification — engine editable-child (decompile = run_console mif.kr.Reconstruct)
	MIF_DECL(create_editable_child);

	// Compile / diagnostics
	MIF_DECL(compile);
	MIF_DECL(run_console);
	MIF_DECL(validate);

	// Self-audit — the plugin reporting its own invariants from inside the running DLL.
	MIF_DECL(self_audit);

	// Per-endpoint parameter introspection (MifBridgeDescribe.cpp). A superset of ONE self_audit row:
	// bucket/provider/compileHeavy/batchable plus the accepted-parameter set harvested from the
	// RejectUnknownParams call sites. Its three states are never conflated — params_declared (the
	// endpoint guards its input, so the set can be enumerated), params_not_declared (NO ROW exists in
	// the harvested table, so acceptedParams is OMITTED rather than empty — an empty list would read as
	// "takes no parameters"), and no_such_endpoint.
	//
	// params_not_declared claims ONLY "no row", never "no guard". Those are different, and an earlier
	// revision of this comment asserted the second: it said the truth was "silently ignores anything it
	// does not read". A missing row has two causes with OPPOSITE consequences — no guard (silently
	// ignores) or a guard added after the harvest (strictly rejects) — and a static table cannot tell
	// them apart. Ten endpoints were in the second case while the endpoint confidently reported the
	// first. Do not reintroduce the stronger wording here or in server.py's tool description.
	// Read-only: it calls no Modify() and creates nothing (see IsReadOnlyEndpoint).
	MIF_DECL(describe_endpoint);

	// Batch
	MIF_DECL(batch);

	// UNDO introspection/rollback + dirty-package flows (MifBridgeUndo.cpp) — editor-SESSION
	// state, not any one asset. list_* are read-only; undo/redo/save_dirty_packages are
	// SELF-MANAGED: an undo cannot begin inside an open transaction (ensure(!GIsTransacting),
	// TransBuffer.h:74) and a save must not be recorded as an undoable step.
	MIF_DECL(list_transactions);
	MIF_DECL(undo_transactions);
	MIF_DECL(redo_transactions);
	MIF_DECL(list_dirty_packages);
	MIF_DECL(save_dirty_packages);

	// MATERIAL GRAPH AUTHORING (MifBridgeMaterials.cpp) — the audit's flagship Tier-0 loop:
	// mint materials/functions, add/wire/delete expression nodes, read the graph back, apply
	// via recompile, poll the async compile. Cooked materials have NO graph (UMaterialExpression
	// is UCLASS 'Optional' — stripped at cook), so every graph endpoint refuses on cooked
	// packages and the read degrades honestly (cooked:true). create_material/
	// create_material_function/recompile_material are SELF-MANAGED (asset creation + shader-map
	// regeneration must not ride the blanket transaction); list_material_expressions and
	// shader_compile_status are read-only; the rest are transacted graph edits.
	MIF_DECL(create_material);
	MIF_DECL(create_material_function);
	MIF_DECL(add_material_expression);
	MIF_DECL(connect_material_expressions);
	MIF_DECL(connect_material_property);
	MIF_DECL(delete_material_expression);
	MIF_DECL(list_material_expressions);
	MIF_DECL(layout_material_expressions);
	MIF_DECL(recompile_material);
	MIF_DECL(shader_compile_status);

	// LEVEL STREAMING control (MifBridgeStreaming.cpp) — sublevel composition in the editor, and
	// level instances in the LIVE PIE world (the reported gap: test setup needed a Lua command
	// because the bridge could not load/unload a level at runtime).
	//
	// Every mutating verb here pre-validates against a MODAL DIALOG or an ASSERT inside the engine
	// implementation it wraps — a modal stops the game-thread ticker and therefore stops this HTTP
	// server answering at all (docs/02_GOTCHAS.md §8), so these are correctness guards, not polish:
	//   add_sublevel            already-present / persistent-level ShowModal  EditorLevelUtils.cpp:441-451
	//   set_current_sublevel    locked-level FMessageDialog                   EditorLevelUtils.cpp:555-588
	//   remove_sublevel         locked-level + failed-unload FMessageDialogs  EditorLevelUtils.cpp:830-834, 894-897
	//   set_sublevel_streaming  check(Level) on an unloaded sublevel          EditorLevelUtils.cpp:524-525
	//
	// add_sublevel / remove_sublevel / set_sublevel_streaming are SELF-MANAGED and DEFERRED to the
	// next tick (the new_level/load_level precedent, MifBridgeWorld.cpp:144/204): they add or destroy
	// a ULevel, and remove_sublevel ends in a forced GC plus a stale-reference sweep that is FATAL
	// when the transaction buffer was reset (EditorLevelUtils.cpp:909, :929-937). They return an
	// opId; list_sublevels reports the outcome in ops[]. Streaming state itself lands across frames,
	// so list_sublevels is also the poll endpoint — for the editor world AND the PIE world.
	MIF_DECL(list_sublevels);
	MIF_DECL(add_sublevel);
	MIF_DECL(remove_sublevel);
	MIF_DECL(set_sublevel_visibility);
	MIF_DECL(set_current_sublevel);
	MIF_DECL(set_sublevel_streaming);
	MIF_DECL(pie_load_level_instance);
	MIF_DECL(pie_unload_level_instance);

	// EDITOR UI INVOCATION (MifBridgeUI.cpp, Batch O) - reaching an affordance that has no callable
	// API by invoking the ACTION it is bound to, never by clicking a pixel. Ranked by usefulness x
	// safety in docs/audit/work/R2_UI_AUTOMATION.md §8; pixel clicking (ui_click via the
	// AutomationDriver) is deliberately NOT here and the decision is recorded in
	// docs/audit/06_IMPLEMENTED.md "Batch O".
	//
	// list_editor_commands is READ-ONLY and invokes nothing. The other three are SELF-MANAGED: they
	// run arbitrary editor code that may open its own FScopedTransaction, run a full Blueprint compile,
	// or BE undo/redo - and an undo begun inside an open transaction trips the engine's own
	// ensure(!GIsTransacting) (TransBuffer.h:74) while a compile captured by an undo step restores a
	// dead CDO and crashes. Self-managed means RunEndpoint opens nothing and the invoked action behaves
	// exactly as it does when a human clicks it. It also makes them compile-heavy, so `batch` refuses
	// them - correct for the same reason.
	//
	// EVERY ONE OF THESE CAN OPEN A MODAL, which stops the game-thread ticker this HTTP server runs on
	// and takes the whole bridge down until a human clicks (docs/02_GOTCHAS.md §8). Mitigations, per
	// endpoint, in each handler's own comment block: confirm-gating, dryRun, a CanExecute pre-check,
	// HasTabSpawner refusal, and a small VERIFIED deny-list of commands whose engine implementation
	// opens a modal unconditionally. None of it makes an ARBITRARY third-party action safe, which
	// R2 §6 item 1 states plainly and the tool descriptions repeat.
	MIF_DECL(list_editor_commands);
	MIF_DECL(invoke_editor_command);
	MIF_DECL(invoke_editor_tab);
	MIF_DECL(send_editor_key);
	// Opens an asset's default editor, which is the only way an asset-specific editor
	// (StaticMesh, SkeletalMesh, Material, ...) ever broadcasts its FUICommandList — without
	// that, invoke_editor_command resolves those commands but cannot execute them.
	MIF_DECL(open_asset_editor);

	// SOURCE MEDIA INGEST (MifBridgeImport.cpp). The bridge could author assets but never bring
	// BYTES in - which is why 42 shop icon textures sit on disk as 4.7 KB header-only stubs (no
	// .uexp, no .ubulk, no source PNG anywhere) and render black.
	//
	// import_texture has TWO ingest modes: {sourcePath} a file on disk, and {base64} raw bytes
	// posted inline. The base64 mode is the load-bearing one - an agent that GENERATED an icon holds
	// bytes and has no file to point at, and reimport cannot help because there is nothing to
	// re-pull. With overwrite:true it re-Inits the EXISTING UTexture2D rather than replacing the
	// object, so the widgets already referencing those stubs keep working.
	//
	// import_asset covers general source media (fbx, wav, psd, obj) via UAssetImportTask +
	// IAssetTools::ImportAssetTasks. bAutomated is forced TRUE (it drives the
	// TGuardValue<bool>(GIsRunningUnattendedScript, ...) at AssetTools.cpp:3045, which is what
	// genuinely suppresses factory option dialogs) and bAsync forced FALSE (GetObjects() BLOCKS on an
	// async import - AssetImportTask.h:78 - which is the cross-frame stall this server forbids).
	// Task->Factory is ALWAYS set: Interchange is bypassed only when a factory is specified
	// (AssetTools.cpp:3068-3071), so a null factory can route a PNG or FBX to the async path.
	//
	// reimport_asset re-pulls a recorded source and refuses HONESTLY when there is none, naming
	// import_texture's base64 mode - a reimport that silently succeeded over a missing file would be
	// the worst possible answer for the icons this batch exists to fix.
	//
	// set_texture_settings is not optional polish: an icon imported with world-texture defaults gets
	// DXT compression, a full mip chain and streaming, so it paints blurry and colour-banded, which
	// reads to a human as a failed import. Without it import_texture is half a solution.
	//
	// All four are SELF-MANAGED (see IsSelfManagedEndpoint in MifBridgeCommon.cpp).
	MIF_DECL(import_texture);
	MIF_DECL(import_asset);
	MIF_DECL(reimport_asset);
	MIF_DECL(set_texture_settings);

	// SOURCE MEDIA EGRESS (MifBridgeExport.cpp). The mirror of the block above, and the half that did
	// not exist: content could come IN but nothing could get OUT, so any workflow that edits geometry
	// in an external DCC was blocked at step one. export_asset writes ONE asset to a disk file through
	// UExporter::RunAssetExportTask; StaticMesh -> FBX is the verified path and everything else
	// UExporter::FindExporter resolves is passed through with a warning that says so.
	//
	// READ-ONLY, not self-managed like its four ingest siblings — it writes a FILE and mutates no
	// asset, which is the render_thumbnail precedent, not the import_texture one. Bucket comment in
	// MifBridgeCommon.cpp.
	//
	// FOUR HAZARDS THE IMPORT SIDE DOES NOT HAVE, every one fatal if a later edit drops it (full
	// citations in the handler's file header):
	//   * The FBX exporter opens a MODAL unless BOTH Task->bAutomated is true AND Task->Options is a
	//     real UFbxExportOption — GetAutomatedExportOptionsFbx casts Options and returns null on a
	//     miss even when bAutomated is set (EditorExporters.cpp:2129-2136). And note WHICH guard
	//     actually suppresses the dialog: FillExportOptions tests FApp::IsUnattended()
	//     (FbxMainExport.cpp:188), NOT the GIsRunningUnattendedScript that import_asset relies on, so
	//     the ingest block's mitigation does not transfer. The handler also calls
	//     SetShowExportOption(false) as a belt, because UExporter's constructor defaults it to true.
	//   * RunAssetExportTask returns TRUE on three paths that write no file (UnrealExporter.cpp
	//     :320-323, :394-397, :364-407), so the FILE is the verdict, never the return value.
	//   * ...and it deletes the destination on NONE of them, so a stat with no pre-image cannot tell a
	//     fresh file from the previous run's leftovers — which, on a deterministic default path with
	//     overwrite defaulting true, is every call after the first. The handler therefore photographs
	//     every expected output (existence, timestamp, size) BEFORE the export and refuses a file that
	//     did not move. Do NOT "fix" that by deleting the target first: it discards a good previous
	//     export exactly when the new one has failed.
	//   * RunAssetExportTask does not necessarily write Task->Filename. With GetFileCount() > 1 it
	//     writes GetUniqueFilename(...) per index (UnrealExporter.cpp:366/:372) — UDIM and layered
	//     virtual textures, and surround SoundWaves. The handler enumerates the expected SET from the
	//     exporter and returns files[]; statting only Task->Filename reported a successful multi-file
	//     export as "produced no usable file".
	// The inverse trap: bWriteEmptyFiles MUST stay false. The FBX exporter writes the file itself and
	// hands the caller an empty archive, so true would clobber the real FBX with a 0-byte one.
	MIF_DECL(export_asset);

	// Asset ICON rendering (MifBridgeThumbnail.cpp). ThumbnailTools::RenderThumbnail is fully
	// SYNCHRONOUS, so there is no job slot and nothing to poll. Only write_thumbnail_texture
	// produces an ASSET - it is the one that actually fills an empty icon stub, because a PNG
	// cannot be referenced by a widget. render_thumbnail and thumbnail_capabilities are READ-ONLY
	// (they write an image FILE under <ProjectSaved> and mutate no asset); write_thumbnail_texture
	// and set_asset_thumbnail are SELF-MANAGED. Both bucket comments are in MifBridgeCommon.cpp.
	MIF_DECL(render_thumbnail);
	MIF_DECL(write_thumbnail_texture);
	MIF_DECL(set_asset_thumbnail);
	MIF_DECL(thumbnail_capabilities);
	// Console / cvar (MifBridgeConsole.cpp) — added so reconstruction flags like
	// mif.kr.Events can be read and flipped without leaving the bridge.
	MIF_DECL(exec_console);
	MIF_DECL(get_cvar);
	MIF_DECL(set_cvar);
	// Variable pin lists (MifBridgeNodePins.cpp) — Sequence / Make Array / Switch / Select.
	MIF_DECL(add_node_pin);

#undef MIF_DECL
}
