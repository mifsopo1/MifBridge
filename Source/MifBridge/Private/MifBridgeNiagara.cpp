// MifBridge — NIAGARA USER PARAMETERS (read).
//
// What is actually blocked today. A NiagaraSystem's user parameters live in an
// FNiagaraUserRedirectionParameterStore, and get_property will happily hand you the whole thing —
// 8830 characters for /Game/UltraDynamicSky/Particles/Rain.Rain. Inside it:
//
//   SortedParameterOffsets=((Offset=100,Name="User.Spawn Rate",TypeDefHandle=(RegisteredTypeIndex=86)),…)
//   ParameterData=(0,0,47,68,0,192,90,69,…)
//
// The NAMES are reachable — ExposedParameters.SortedParameterOffsets[0].Name resolves through the
// normal path walker and returns "User.Camera Forward Offset". What is NOT reachable is the VALUE.
// It is a flat byte array indexed by Offset, and the only type information is an opaque
// RegisteredTypeIndex: an index into FNiagaraTypeRegistry, a C++ singleton with no reflection
// surface. So "what is User.Spawn Rate set to" cannot be answered by any composition of the existing
// endpoints, which is what makes this worth a handler rather than help text.
//
// NO NIAGARA MODULE DEPENDENCY, deliberately. Resolving RegisteredTypeIndex to a UStruct needs
// FNiagaraTypeDefinition::GetStruct(), which needs the Niagara module linked, which would mean the
// whole of MifBridge fails to load in any build where Niagara is not compiled in. That is a poor
// trade for one read: of the 38 NiagaraSystem assets in this project, 27 are Ultra Dynamic Sky, 4 are
// engine templates, 3 come from the Oceanology and Water plugins, and exactly 4 are DDS2's own. The
// asset is recognised by CLASS NAME instead, the same string-check discipline the cooked-Niagara
// duplication guard in MifBridgeAssetOps.cpp uses and for the same reason.
//
// THREE OFFSET SPACES, NOT ONE. This is the part that is easy to get wrong, and the first version
// did. A parameter store keeps three parallel arrays — ParameterData (bytes), DataInterfaces and
// UObjects (both object arrays) — behind ONE SortedParameterOffsets covering all of them. An Offset
// is a BYTE POSITION for a value parameter and an ARRAY INDEX for an object one, and nothing in the
// entry says which. On /Game/UDS_Mif/Particles/Rain.Rain three different parameters all report
// Offset=0. Taking a width as "distance to the next sorted offset" therefore gave a float a width of
// ONE BYTE, the gap to a UObject index. The first asset tested had both object arrays empty, which is
// exactly why it looked correct.
//
// The rule used instead is proved, not inferred. One typeIndex is one type, so all parameters sharing
// a typeIndex live in one space. With T = max(DataInterfaces.Num(), UObjects.Num()), any offset >= T
// cannot be an index into either object array and must be a ParameterData offset — so a typeIndex
// with any parameter at offset >= T is a value type, and all of its parameters are values. It is then
// VERIFIED rather than trusted: the value parameters must tile ParameterData from byte 0 to the end
// with no gap and no overlap, and parameterLayoutVerified reports whether they did. If they did not,
// values are WITHHELD, because a wrong number here reads exactly like a right one.
//
// That rule is conservative and says so. A value type whose every parameter sits below the ceiling
// cannot be proven, and one asset in this project (NS_LakeBurbblesUnderwater, 8 bytes of parameter
// data) is genuinely ambiguous — both {86,88} and {86,72} tile it exactly. valueTypeIndices is
// reported so a caller can carry the proven indices over from a richer system, which is a decision
// made with stated evidence rather than one made silently here.
//
// SO THE TYPE IS NOT GUESSED EITHER. sizeBytes is exact once the space is known, but a byte width
// cannot distinguish float from int32 from bool, all of which are 4 bytes. Rather than pick one and be
// quietly wrong, every valid interpretation is reported side by side: asFloat, asInt32, asBool for
// four bytes; a float tuple for twelve or sixteen. On this project typeIndex 88 holds collision
// channels whose float reading is denormal garbage (1.4e-45) and typeIndex 89 holds bools stored as
// -1, whose float bits are NaN — anything that picked "float" because the width was 4 would report
// both as nonsense while looking successful.
//
// typeIndex is passed through unchanged for the same reason — it is a real, stable-within-a-build
// discriminator, so a caller that learns index 86 is float on this build can rely on it. It is
// explicitly NOT translated to a name here, because translating it would be the guess.
//
// Read-only: no Modify, no transaction, nothing dirtied.
#include "MifBridgeHandlers.h"
#include "MifBridgeVersion.h"
#if MIF_WITH_NIAGARA
	// The typed read below needs these. They are guarded because this endpoint still answers on a
	// build without the Niagara plugin - via the reflection fallback, which knows sizes but not
	// types.
	#include "NiagaraSystem.h"
	#include "NiagaraTypes.h"
	#include "NiagaraParameterStore.h"
#endif
#include "MifBridgeLog.h"

#include "UObject/UnrealType.h"      // FArrayProperty / FStructProperty / FScriptArrayHelper
#include "UObject/Package.h"

namespace MifBridge
{
	namespace
	{
		/** One parameter's slice of ParameterData, decoded every way its width permits.
		 *
		 *  Named NiagaraDecodeValue rather than DecodeValue on purpose: this module builds as a unity
		 *  blob, and two files defining the same short helper name is exactly the C2084 that PM-005
		 *  records. */
		void NiagaraDecodeValue(const TSharedRef<FJsonObject>& Entry, const uint8* Bytes, int32 Size)
		{
			Entry->SetNumberField(TEXT("sizeBytes"), Size);
			if (!Bytes || Size <= 0)
			{
				return;
			}
			if (Size == 4)
			{
				// Four bytes is float, int32 or bool and the store does not say which. All three, then.
				float F = 0.0f;   FMemory::Memcpy(&F, Bytes, 4);
				int32 I = 0;      FMemory::Memcpy(&I, Bytes, 4);
				// A Niagara bool is a full int32 whose false is 0 and whose true is any non-zero
				// (FNiagaraBool uses -1), so this is a comparison, not a byte read.
				Entry->SetNumberField(TEXT("asFloat"), FMath::IsFinite(F) ? double(F) : 0.0);
				if (!FMath::IsFinite(F))
				{
					// A NaN or infinity here almost always means the bytes are not a float at all.
					Entry->SetBoolField(TEXT("floatIsFinite"), false);
				}
				Entry->SetNumberField(TEXT("asInt32"), I);
				Entry->SetBoolField(TEXT("asBool"), I != 0);
			}
			else if (Size == 8 || Size == 12 || Size == 16)
			{
				// 2, 3 or 4 floats: Vector2f, Vector3f/Position, LinearColor/Quat. Which of each pair
				// it is cannot be told from the bytes, so the tuple is reported and left unnamed.
				TArray<TSharedPtr<FJsonValue>> Floats;
				for (int32 i = 0; i < Size / 4; ++i)
				{
					float F = 0.0f;
					FMemory::Memcpy(&F, Bytes + i * 4, 4);
					Floats.Add(MakeShared<FJsonValueNumber>(FMath::IsFinite(F) ? double(F) : 0.0));
				}
				Entry->SetArrayField(TEXT("asFloats"), Floats);
			}
			else
			{
				// Struct payloads, data interfaces and anything else. The bytes are still reported so
				// the answer is "here is what is there" rather than a silent omission.
				Entry->SetStringField(TEXT("valueNote"),
					TEXT("this parameter is neither 4 bytes nor a 2/3/4-float vector, so it is a struct "
						 "or data-interface payload. Its raw bytes are in rawBytes; nothing here can name "
						 "its type without the Niagara type registry."));
			}
			TArray<TSharedPtr<FJsonValue>> Raw;
			for (int32 i = 0; i < Size && i < 64; ++i)
			{
				Raw.Add(MakeShared<FJsonValueNumber>(Bytes[i]));
			}
			Entry->SetArrayField(TEXT("rawBytes"), Raw);
			if (Size > 64)
			{
				Entry->SetBoolField(TEXT("rawBytesTruncated"), true);
			}
		}

		/** Reads one integer/name member out of a struct element by NAME, so nothing here depends on
		 *  Niagara's headers or on member ordering. Returns false when the member is absent, which is
		 *  how a future engine version renaming a field surfaces as a clear failure. */
		bool NiagaraReadInt(UStruct* Struct, const void* Addr, const TCHAR* Member, int64& Out)
		{
			if (!Struct || !Addr) { return false; }
			FProperty* P = Struct->FindPropertyByName(FName(Member));
			if (FNumericProperty* N = CastField<FNumericProperty>(P))
			{
				Out = N->GetSignedIntPropertyValue(N->ContainerPtrToValuePtr<void>(Addr));
				return true;
			}
			return false;
		}
	}

	// --- list_niagara_user_parameters ----------------------------------------
	//   in:  { path }
	//   out: { system, count, parameters:[{ name, offset, sizeBytes, typeIndex, asFloat|asFloats|…, rawBytes }] }
	//
	// The one question no composition of existing endpoints can answer: what are this system's user
	// parameters SET TO. See the file header for why the type is reported as an index and the value as
	// every interpretation its width permits, rather than as a single type this could not verify.
	void H_list_niagara_user_parameters(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("system"), TEXT("nameContains") },
			TEXT("path (aliases: assetPath, system) of a NiagaraSystem; nameContains to filter"),
			{ { TEXT("component"), TEXT("this reads the ASSET's user parameters. A spawned component's overrides are a different question and are not read here") },
			  { TEXT("value"), TEXT("this endpoint is read-only - writing Niagara user parameters is deliberately not implemented") },
			  { TEXT("emitter"), TEXT("emitter-scope parameters are not user parameters; only the User. namespace is exposed by a system") } }))
		{
			return;
		}

		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("system") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required - a NiagaraSystem asset "
						   "(find_assets with class=NiagaraSystem lists them)."));
			return;
		}
		UObject* Asset = LoadObject<UObject>(nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!Asset)
		{
			Fail(Out, FString::Printf(TEXT("no asset at %s"), *Path));
			return;
		}
		// CLASS NAME, not a cast - see the header. Recognising the asset must not cost a link
		// dependency on the whole Niagara plugin.
		if (Asset->GetClass()->GetName() != TEXT("NiagaraSystem"))
		{
			Fail(Out, FString::Printf(
				TEXT("%s is a %s, not a NiagaraSystem. Only a system carries User. parameters - an "
					 "emitter does not."), *Path, *Asset->GetClass()->GetName()));
			return;
		}

		// TYPED PATH, when the Niagara module is linked. The parameter store carries a real
		// FNiagaraTypeDefinition for every entry, so the type never has to be inferred - and
		// inferring it is what made this endpoint report asFloat, asInt32 AND asBool for the same
		// four bytes, leaving the caller to pick. User.BoatSize came back as 1, 1065353216 and true
		// simultaneously.
		//
		// The reflection path below is kept as the fallback for a build without the Niagara plugin.
		// It still works; it just cannot know a type.
#if MIF_WITH_NIAGARA
		if (UNiagaraSystem* NiagaraSys = Cast<UNiagaraSystem>(Asset))
		{
			const FNiagaraUserRedirectionParameterStore& Store = NiagaraSys->GetExposedParameters();
			const FString Filter = JStr(In, TEXT("nameContains"));
			TArray<TSharedPtr<FJsonValue>> Params;

			for (const FNiagaraVariableWithOffset& Var : Store.ReadParameterVariables())
			{
				const FString Name = Var.GetName().ToString();
				if (!Filter.IsEmpty() && !Name.Contains(Filter)) { continue; }

				TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
				P->SetStringField(TEXT("name"), Name);
				const FNiagaraTypeDefinition& Type = Var.GetType();
				// The REAL type name - "float", "Vector", "LinearColor" - not an internal index.
				P->SetStringField(TEXT("type"), Type.GetName());
				P->SetNumberField(TEXT("sizeBytes"), Type.GetSize());

				// A data interface or UObject parameter has no POD payload to decode; saying so is
				// the honest answer rather than dumping the pointer bytes.
				if (Type.IsDataInterface() || Type.IsUObject())
				{
					P->SetStringField(TEXT("valueKind"),
						Type.IsDataInterface() ? TEXT("dataInterface") : TEXT("object"));
					P->SetStringField(TEXT("note"),
						TEXT("this parameter holds an object rather than a value, so there is no "
							 "number to report. describe_niagara_system lists what is bound."));
					Params.Add(MakeShared<FJsonValueObject>(P));
					continue;
				}

				const uint8* Data = Store.GetParameterData(Var.Offset);
				if (!Data)
				{
					P->SetStringField(TEXT("valueKind"), TEXT("unavailable"));
					P->SetStringField(TEXT("note"),
						TEXT("the store reported no data at this parameter's offset. Reported as "
							 "unavailable rather than decoded from whatever happened to be there."));
					Params.Add(MakeShared<FJsonValueObject>(P));
					continue;
				}

				// ONE decoding, chosen by the type the engine recorded. Every branch below writes
				// exactly one "value", so there is nothing left for a caller to disambiguate.
				auto Floats = [&P, Data](int32 Count)
				{
					TArray<TSharedPtr<FJsonValue>> Arr;
					for (int32 i = 0; i < Count; ++i)
					{
						float F = 0.0f;
						FMemory::Memcpy(&F, Data + i * sizeof(float), sizeof(float));
						Arr.Add(MakeShared<FJsonValueNumber>(F));
					}
					P->SetArrayField(TEXT("value"), Arr);
				};

				if (Type == FNiagaraTypeDefinition::GetFloatDef())
				{
					float F = 0.0f; FMemory::Memcpy(&F, Data, sizeof(float));
					P->SetNumberField(TEXT("value"), F);
					P->SetStringField(TEXT("valueKind"), TEXT("float"));
				}
				else if (Type == FNiagaraTypeDefinition::GetIntDef())
				{
					int32 I = 0; FMemory::Memcpy(&I, Data, sizeof(int32));
					P->SetNumberField(TEXT("value"), I);
					P->SetStringField(TEXT("valueKind"), TEXT("int"));
				}
				else if (Type == FNiagaraTypeDefinition::GetBoolDef())
				{
					// FNiagaraBool is an int32 where 0 is false and anything else is true - NOT a
					// C++ bool, so reading one byte would be wrong on a non-zero high byte.
					int32 I = 0; FMemory::Memcpy(&I, Data, sizeof(int32));
					P->SetBoolField(TEXT("value"), I != 0);
					P->SetStringField(TEXT("valueKind"), TEXT("bool"));
				}
				else if (Type == FNiagaraTypeDefinition::GetVec2Def())      { Floats(2); P->SetStringField(TEXT("valueKind"), TEXT("vec2")); }
				else if (Type == FNiagaraTypeDefinition::GetVec3Def())      { Floats(3); P->SetStringField(TEXT("valueKind"), TEXT("vec3")); }
				else if (Type == FNiagaraTypeDefinition::GetPositionDef())  { Floats(3); P->SetStringField(TEXT("valueKind"), TEXT("position")); }
				else if (Type == FNiagaraTypeDefinition::GetVec4Def())      { Floats(4); P->SetStringField(TEXT("valueKind"), TEXT("vec4")); }
				else if (Type == FNiagaraTypeDefinition::GetQuatDef())      { Floats(4); P->SetStringField(TEXT("valueKind"), TEXT("quat")); }
				else if (Type == FNiagaraTypeDefinition::GetColorDef())     { Floats(4); P->SetStringField(TEXT("valueKind"), TEXT("linearColor")); }
				else
				{
					// A struct this endpoint does not decode. The BYTES are still offered, but the
					// type is NAMED - which is the whole difference from the old behaviour, where an
					// unknown type and a float looked the same.
					TArray<TSharedPtr<FJsonValue>> Raw;
					for (int32 i = 0; i < Type.GetSize(); ++i)
					{
						Raw.Add(MakeShared<FJsonValueNumber>(Data[i]));
					}
					P->SetArrayField(TEXT("rawBytes"), Raw);
					P->SetStringField(TEXT("valueKind"), TEXT("raw"));
					P->SetStringField(TEXT("note"), FString::Printf(
						TEXT("'%s' is a struct this endpoint does not decode, so the bytes are given "
							 "as-is. The TYPE is reported, which is what tells you how to read them."),
						*Type.GetName()));
				}
				Params.Add(MakeShared<FJsonValueObject>(P));
			}

			Out->SetStringField(TEXT("system"), NiagaraSys->GetPathName());
			Out->SetNumberField(TEXT("count"), Params.Num());
			Out->SetArrayField(TEXT("parameters"), Params);
			Out->SetBoolField(TEXT("typed"), true);
			Out->SetStringField(TEXT("source"),
				TEXT("read through the Niagara parameter store, so every entry carries the type the "
					 "engine recorded and exactly one decoded value. The older reflection path could "
					 "only report the byte SIZE, which is why it used to return asFloat, asInt32 and "
					 "asBool for the same four bytes and leave you to choose."));
			return;
		}
#endif

		// Both halves come through the normal path walker, so this endpoint inherits its error
		// reporting and cannot drift from what get_property would resolve.
		FPropertyPathResolution OffsetsRes, DataRes;
		FString Error;
		if (!ResolvePropertyPathEx(Asset, TEXT("ExposedParameters.SortedParameterOffsets"), OffsetsRes, Error))
		{
			Fail(Out, FString::Printf(
				TEXT("could not read ExposedParameters.SortedParameterOffsets on this system (%s). The "
					 "parameter store's layout has changed; nothing was read."), *Error));
			return;
		}
		if (!ResolvePropertyPathEx(Asset, TEXT("ExposedParameters.ParameterData"), DataRes, Error))
		{
			Fail(Out, FString::Printf(
				TEXT("could not read ExposedParameters.ParameterData on this system (%s). Names would be "
					 "readable but no VALUE could be, which is the point of this call, so nothing was "
					 "returned."), *Error));
			return;
		}
		FArrayProperty* OffsetsProp = CastField<FArrayProperty>(OffsetsRes.Leaf);
		FArrayProperty* DataProp    = CastField<FArrayProperty>(DataRes.Leaf);
		if (!OffsetsProp || !DataProp || !OffsetsRes.LeafAddr || !DataRes.LeafAddr)
		{
			Fail(Out, TEXT("the parameter store did not resolve to the two arrays this reads "
						   "(SortedParameterOffsets and ParameterData). Nothing was read."));
			return;
		}
		FStructProperty* ElemProp = CastField<FStructProperty>(OffsetsProp->Inner);
		if (!ElemProp || !ElemProp->Struct)
		{
			Fail(Out, TEXT("SortedParameterOffsets is not an array of structs. Nothing was read."));
			return;
		}

		FScriptArrayHelper Offsets(OffsetsProp, OffsetsRes.LeafAddr);
		FScriptArrayHelper Data(DataProp, DataRes.LeafAddr);
		const int32 DataNum = Data.Num();
		const uint8* DataPtr = DataNum > 0 ? reinterpret_cast<const uint8*>(Data.GetRawPtr(0)) : nullptr;

		// The OTHER two offset spaces. A parameter store keeps three parallel arrays and one shared
		// list of offsets across all of them, so an Offset is a byte position for a value parameter and
		// an ARRAY INDEX for a data-interface or object parameter, with nothing in the entry saying
		// which. Reading their lengths is what makes the value parameters identifiable at all.
		TArray<TSharedPtr<FJsonValue>> DataInterfaceList, UObjectList;
		int32 NumDataInterfaces = 0, NumUObjects = 0;
		for (int32 Which = 0; Which < 2; ++Which)
		{
			const TCHAR* Member = Which == 0 ? TEXT("ExposedParameters.DataInterfaces")
											 : TEXT("ExposedParameters.UObjects");
			FPropertyPathResolution Res;
			FString Ignored;
			if (!ResolvePropertyPathEx(Asset, Member, Res, Ignored)) { continue; }
			FArrayProperty* Arr = CastField<FArrayProperty>(Res.Leaf);
			if (!Arr || !Res.LeafAddr) { continue; }
			FScriptArrayHelper H(Arr, Res.LeafAddr);
			FObjectPropertyBase* ObjProp = CastField<FObjectPropertyBase>(Arr->Inner);
			for (int32 i = 0; i < H.Num(); ++i)
			{
				UObject* Held = ObjProp ? ObjProp->GetObjectPropertyValue(H.GetRawPtr(i)) : nullptr;
				(Which == 0 ? DataInterfaceList : UObjectList).Add(
					MakeShared<FJsonValueString>(Held ? Held->GetPathName() : TEXT("")));
			}
			(Which == 0 ? NumDataInterfaces : NumUObjects) = H.Num();
		}
		// Any offset at or above this cannot be an index into either object array, so it is a
		// ParameterData byte offset. That single fact is what the classification below rests on.
		const int32 ObjectSpaceCeiling = FMath::Max(NumDataInterfaces, NumUObjects);

		FProperty* NameProp = ElemProp->Struct->FindPropertyByName(FName(TEXT("Name")));
		if (!NameProp)
		{
			Fail(Out, TEXT("the parameter entries carry no Name member, so nothing could be identified. "
						   "Nothing was read."));
			return;
		}

		// Gathered first so the widths can be computed from the SORTED offsets, which is what makes the
		// size exact rather than assumed. The array is named SortedParameterOffsets and is sorted by
		// name, not by offset, so it is sorted here rather than trusted.
		struct FEntry { FString Name; int64 Offset = 0; int64 TypeIndex = -1; };
		TArray<FEntry> Entries;
		Entries.Reserve(Offsets.Num());
		for (int32 i = 0; i < Offsets.Num(); ++i)
		{
			const void* Elem = Offsets.GetRawPtr(i);
			FEntry E;
			NameProp->ExportText_Direct(E.Name, NameProp->ContainerPtrToValuePtr<void>(Elem),
				NameProp->ContainerPtrToValuePtr<void>(Elem), nullptr, PPF_None);
			if (!NiagaraReadInt(ElemProp->Struct, Elem, TEXT("Offset"), E.Offset))
			{
				continue;   // no offset means no value to read; a nameless half-entry helps nobody
			}
			// TypeDefHandle.RegisteredTypeIndex, one level down. Absent is fine - it is reported as -1
			// rather than faked, and the value decoding does not depend on it.
			if (FStructProperty* Handle = CastField<FStructProperty>(
					ElemProp->Struct->FindPropertyByName(FName(TEXT("TypeDefHandle")))))
			{
				NiagaraReadInt(Handle->Struct, Handle->ContainerPtrToValuePtr<void>(Elem),
					TEXT("RegisteredTypeIndex"), E.TypeIndex);
			}
			Entries.Add(MoveTemp(E));
		}
		Entries.Sort([](const FEntry& A, const FEntry& B) { return A.Offset < B.Offset; });

		// ONE typeIndex IS ONE TYPE, so every parameter sharing a typeIndex lives in the same space.
		// A typeIndex with any parameter above the object-space ceiling is therefore a VALUE type, and
		// all of its parameters are value parameters. With both object arrays empty the ceiling is 0
		// and everything qualifies, which is the correct answer for that case rather than a special one.
		TSet<int64> ValueTypeIndices;
		for (const FEntry& E : Entries)
		{
			if (E.Offset >= ObjectSpaceCeiling) { ValueTypeIndices.Add(E.TypeIndex); }
		}
		TArray<FEntry> ValueEntries;
		for (const FEntry& E : Entries)
		{
			if (ValueTypeIndices.Contains(E.TypeIndex)) { ValueEntries.Add(E); }
		}
		// Widths come from the gaps between VALUE parameters only - taking them across the object
		// parameters is exactly the bug this replaces.
		TMap<FString, int32> WidthByName;
		for (int32 i = 0; i < ValueEntries.Num(); ++i)
		{
			const int64 Next = (i + 1 < ValueEntries.Num()) ? ValueEntries[i + 1].Offset : int64(DataNum);
			WidthByName.Add(ValueEntries[i].Name, int32(FMath::Clamp(Next - ValueEntries[i].Offset,
																	int64(0), int64(DataNum))));
		}
		// VERIFIED, not assumed. The value parameters must cover ParameterData from byte 0 to the end
		// with no gap and no overlap. If they do not, the classification is wrong and values are
		// withheld - a wrong number here would read exactly like a right one.
		bool bTiles = (ValueEntries.Num() > 0) || (DataNum == 0);
		{
			int64 Cursor = 0;
			for (const FEntry& E : ValueEntries)
			{
				if (E.Offset != Cursor) { bTiles = false; break; }
				Cursor += WidthByName.FindRef(E.Name);
			}
			if (Cursor != DataNum) { bTiles = false; }
		}

		const FString Filter = JStr(In, TEXT("nameContains"));
		TArray<TSharedPtr<FJsonValue>> Params;
		int32 Shown = 0;
		for (const FEntry& E : Entries)
		{
			if (!Filter.IsEmpty() && !E.Name.Contains(Filter)) { continue; }
			++Shown;

			TSharedRef<FJsonObject> Entry = MakeShared<FJsonObject>();
			Entry->SetStringField(TEXT("name"), E.Name);
			Entry->SetNumberField(TEXT("offset"), E.Offset);
			// Passed through, never translated - translating it is the one thing this cannot verify.
			Entry->SetNumberField(TEXT("typeIndex"), E.TypeIndex);

			const bool bIsValue = ValueTypeIndices.Contains(E.TypeIndex);
			// Said on every entry, because "offset 0" means a different thing in each space and a
			// caller comparing offsets across parameters would otherwise be comparing nothing.
			Entry->SetStringField(TEXT("offsetSpace"), bIsValue ? TEXT("parameterData") : TEXT("objectArray"));
			if (!bIsValue)
			{
				// Not decoded, but not dropped: the arrays are reported whole at the top level, so the
				// caller can resolve this index itself. That is how you find which Material a
				// "User.…Material" parameter points at.
				Entry->SetStringField(TEXT("valueNote"),
					TEXT("this parameter holds an OBJECT, not bytes - its offset is an index into the "
						 "dataInterfaces or uobjects array reported alongside these parameters, not a "
						 "position in ParameterData. Which of the two cannot be told apart without "
						 "Niagara's type registry, so both are given and neither is guessed at."));
				Params.Add(MakeShared<FJsonValueObject>(Entry));
				continue;
			}
			if (!bTiles)
			{
				// The classification did not hold, so no value here can be trusted. Withheld rather
				// than reported, because a wrong number reads exactly like a right one.
				Entry->SetStringField(TEXT("valueNote"),
					TEXT("no value is reported: the value parameters did not tile ParameterData exactly, "
						 "which means this build lays the store out differently than this endpoint can "
						 "read. See parameterLayoutVerified."));
				Params.Add(MakeShared<FJsonValueObject>(Entry));
				continue;
			}
			const int32 Size = WidthByName.FindRef(E.Name);
			if (DataPtr && E.Offset >= 0 && E.Offset + Size <= DataNum)
			{
				NiagaraDecodeValue(Entry, DataPtr + E.Offset, Size);
			}
			else
			{
				Entry->SetNumberField(TEXT("sizeBytes"), Size);
				Entry->SetStringField(TEXT("valueNote"), FString::Printf(
					TEXT("offset %lld + %d bytes falls outside the %d-byte ParameterData buffer, so no "
						 "value could be read for this parameter."), E.Offset, Size, DataNum));
			}
			Params.Add(MakeShared<FJsonValueObject>(Entry));
		}

		Out->SetStringField(TEXT("system"), Asset->GetPathName());
		Out->SetNumberField(TEXT("count"), Shown);
		Out->SetNumberField(TEXT("totalParameters"), Entries.Num());
		Out->SetNumberField(TEXT("parameterDataBytes"), DataNum);
		// The check that makes sizeBytes trustworthy. Reported ALWAYS, so a caller never has to guess
		// whether the classification held.
		Out->SetBoolField(TEXT("parameterLayoutVerified"), bTiles);
		// The PROVEN value type indices. Reported always, because indices are stable within a build, so
		// a caller can carry them from a system where they were provable to one where they were not.
		{
			TArray<int64> Sorted = ValueTypeIndices.Array();
			Sorted.Sort();
			TArray<TSharedPtr<FJsonValue>> Idx;
			for (int64 V : Sorted) { Idx.Add(MakeShared<FJsonValueNumber>(double(V))); }
			Out->SetArrayField(TEXT("valueTypeIndices"), Idx);
		}
		if (!bTiles)
		{
			Out->SetStringField(TEXT("layoutNote"),
				TEXT("no values were decoded. The parameters that could be PROVEN to be values did not "
					 "tile ParameterData exactly, and a width that cannot be proven produces a number "
					 "indistinguishable from a correct one. Names, offsets and type indices above are "
					 "still exact.\n\nThe usual cause is a small system: a type can only be proven to be "
					 "a value type when one of its parameters sits at an offset at or above the object "
					 "arrays' length, and on a system with very few parameters none may. Run this "
					 "against a system with many parameters, take its valueTypeIndices - indices are "
					 "stable within a build - and compare them with the typeIndex values above to work "
					 "out which parameters here are values."));
		}
		if (DataInterfaceList.Num() > 0) { Out->SetArrayField(TEXT("dataInterfaces"), DataInterfaceList); }
		if (UObjectList.Num() > 0)       { Out->SetArrayField(TEXT("uobjects"), UObjectList); }
		Out->SetArrayField(TEXT("parameters"), Params);
		Out->SetStringField(TEXT("typeNote"),
			TEXT("typeIndex is Niagara's RegisteredTypeIndex, an index into a runtime registry with no "
				 "reflection surface, so it is passed through rather than translated to a type name - "
				 "translating it would be a guess. sizeBytes IS exact (it comes from the gap to the next "
				 "parameter). Where four bytes could be a float, an int32 or a bool, all three readings "
				 "are given rather than one of them being picked for you."));
		Out->SetStringField(TEXT("writeNote"),
			TEXT("read-only. To change one of these in a cooked-game mod you do not edit the asset - you "
				 "call SetNiagaraVariableFloat/Vec3/Bool on the spawned component from Blueprint, and "
				 "the exact name string above is what those take."));
		UE_LOG(LogMifBridge, Log, TEXT("list_niagara_user_parameters: %d of %d on %s"),
			Shown, Entries.Num(), *Asset->GetName());
	}

	// =======================================================================
	// set_niagara_user_parameter - the write half of the typed read above
	// =======================================================================
	//
	// TWO CRASH TRAPS, both check() rather than an error return, both guarded by dispatching only on
	// an exact type match:
	//
	//   SetParameterValue<T>  check(Param.GetSizeInBytes() == sizeof(T))   ParameterStore.h:527
	//   Position parameters   check(HasPositionData(Param.GetName()))      ParameterStore.h:531
	//
	// So there is no default branch that "tries" a float: a type this endpoint does not handle is
	// refused by name. Guessing here does not produce a bad value, it ends the process.

	void H_set_niagara_user_parameter(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("system"), TEXT("name"), TEXT("value") },
			TEXT("path (aliases assetPath, system) - a NiagaraSystem; name - the User. parameter "
				 "(list_niagara_user_parameters reports them, with types); value - a number for "
				 "float/int, true/false for bool, or an array for vec2/vec3/vec4/quat/color/position"),
			{ { TEXT("add"), TEXT("this sets an EXISTING parameter. Adding one is not offered: a user "
								  "parameter no emitter reads is invisible in the editor and does "
								  "nothing, so creating one by typo is worse than being told the "
								  "name is unknown") },
			  { TEXT("component"), TEXT("this writes the ASSET's default. To override on one placed "
									   "component, use set_niagara_component_parameter") },
			  { TEXT("type"), TEXT("the type is the one the system already records for that "
								   "parameter - it is not something a caller chooses, and writing a "
								   "mismatched type would terminate the editor") } }))
		{
			return;
		}

#if !MIF_WITH_NIAGARA
		Fail(Out, TEXT("this build has no Niagara module linked, so a user parameter cannot be "
					   "written. NOTHING was changed."));
		return;
#else
		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("system") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required - a NiagaraSystem asset. NOTHING was changed."));
			return;
		}
		UObject* Asset = LoadObject<UObject>(nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!Asset)
		{
			Fail(Out, FString::Printf(TEXT("no asset at %s. NOTHING was changed."), *Path));
			return;
		}
		UNiagaraSystem* Sys = Cast<UNiagaraSystem>(Asset);
		if (!Sys)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is a %s, not a NiagaraSystem. Only a system carries User. parameters. "
					 "NOTHING was changed."), *Path, *Asset->GetClass()->GetName()));
			return;
		}

		// COOKED: refused for persistence, not for safety. Writing the store would not crash - it is
		// runtime data - but the change cannot be saved and the system cannot be recompiled, so the
		// old value returns on restart while this response claims the new one. A wrong answer with a
		// delay on it is worse than a refusal.
		if (IsCookedOrContainerPackage(Sys->GetOutermost()))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' came from a COOKED package. The parameter store is runtime data and the "
					 "write itself would succeed, but it cannot be SAVED and the system cannot be "
					 "recompiled - so the old value would come back on restart with this call having "
					 "reported success. Refused for that reason rather than for safety. NOTHING was "
					 "changed."), *Sys->GetPathName()));
			return;
		}

		const FString Name = JStr(In, TEXT("name"));
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("name is required - the User. parameter to set. NOTHING was changed."));
			return;
		}
		if (!In->HasField(TEXT("value")))
		{
			Fail(Out, TEXT("value is required. NOTHING was changed."));
			return;
		}

		// FIND THE PARAMETER AND ITS RECORDED TYPE. Everything below dispatches on this, because a
		// mismatched T is a check() - a process kill, not a bad write.
		FNiagaraUserRedirectionParameterStore& Store = Sys->GetExposedParameters();
		const FNiagaraVariableWithOffset* Found = nullptr;
		TArray<FString> Known;
		for (const FNiagaraVariableWithOffset& Var : Store.ReadParameterVariables())
		{
			const FString VarName = Var.GetName().ToString();
			Known.Add(VarName);
			// Accept both spellings: the store keys on "User.Foo" and a caller reading the editor UI
			// sees "Foo".
			if (VarName == Name || VarName == (TEXT("User.") + Name)) { Found = &Var; }
		}
		if (!Found)
		{
			Fail(Out, FString::Printf(
				TEXT("no user parameter named '%s' on '%s'. It has: %s. Adding one is deliberately "
					 "not offered - a parameter no emitter reads does nothing. NOTHING was changed."),
				*Name, *Sys->GetName(),
				Known.Num() ? *FString::Join(Known, TEXT(", ")) : TEXT("(none)")));
			return;
		}

		const FNiagaraVariable Param(Found->GetType(), Found->GetName());
		const FNiagaraTypeDefinition& Type = Found->GetType();
		const TSharedPtr<FJsonValue> Val = In->TryGetField(TEXT("value"));

		auto NeedArray = [&](int32 Count, TArray<double>& OutNums) -> bool
		{
			const TArray<TSharedPtr<FJsonValue>>* Arr = nullptr;
			if (!Val.IsValid() || !Val->TryGetArray(Arr) || !Arr || Arr->Num() != Count)
			{
				Fail(Out, FString::Printf(
					TEXT("'%s' is a %s, so value must be an array of %d numbers. NOTHING was "
						 "changed."), *Param.GetName().ToString(), *Type.GetName(), Count));
				return false;
			}
			for (const TSharedPtr<FJsonValue>& V : *Arr)
			{
				double D = 0.0;
				if (!V.IsValid() || !V->TryGetNumber(D))
				{
					Fail(Out, TEXT("value array holds numbers. NOTHING was changed."));
					return false;
				}
				OutNums.Add(D);
			}
			return true;
		};

		bool bWrote = false;
		TArray<double> Nums;
		if (Type == FNiagaraTypeDefinition::GetFloatDef())
		{
			double D = 0.0;
			if (!Val.IsValid() || !Val->TryGetNumber(D))
			{
				Fail(Out, TEXT("value must be a number for a float parameter. NOTHING was changed."));
				return;
			}
			bWrote = Store.SetParameterValue(float(D), Param, false);
		}
		else if (Type == FNiagaraTypeDefinition::GetIntDef())
		{
			double D = 0.0;
			if (!Val.IsValid() || !Val->TryGetNumber(D))
			{
				Fail(Out, TEXT("value must be a number for an int parameter. NOTHING was changed."));
				return;
			}
			bWrote = Store.SetParameterValue(int32(D), Param, false);
		}
		else if (Type == FNiagaraTypeDefinition::GetBoolDef())
		{
			bool B = false;
			if (!Val.IsValid() || !Val->TryGetBool(B))
			{
				Fail(Out, TEXT("value must be true or false for a bool parameter. NOTHING was "
							   "changed."));
				return;
			}
			// FNiagaraBool, not a C++ bool - the store keeps an int32 where 0 is false.
			bWrote = Store.SetParameterValue(FNiagaraBool(B), Param, false);
		}
		else if (Type == FNiagaraTypeDefinition::GetVec2Def())
		{
			if (!NeedArray(2, Nums)) { return; }
			bWrote = Store.SetParameterValue(FVector2f(Nums[0], Nums[1]), Param, false);
		}
		else if (Type == FNiagaraTypeDefinition::GetVec3Def())
		{
			if (!NeedArray(3, Nums)) { return; }
			bWrote = Store.SetParameterValue(FVector3f(Nums[0], Nums[1], Nums[2]), Param, false);
		}
		else if (Type == FNiagaraTypeDefinition::GetVec4Def())
		{
			if (!NeedArray(4, Nums)) { return; }
			bWrote = Store.SetParameterValue(FVector4f(Nums[0], Nums[1], Nums[2], Nums[3]), Param, false);
		}
		else if (Type == FNiagaraTypeDefinition::GetQuatDef())
		{
			if (!NeedArray(4, Nums)) { return; }
			bWrote = Store.SetParameterValue(FQuat4f(Nums[0], Nums[1], Nums[2], Nums[3]), Param, false);
		}
		else if (Type == FNiagaraTypeDefinition::GetColorDef())
		{
			if (!NeedArray(4, Nums)) { return; }
			bWrote = Store.SetParameterValue(
				FLinearColor(Nums[0], Nums[1], Nums[2], Nums[3]), Param, false);
		}
		else if (Type == FNiagaraTypeDefinition::GetPositionDef())
		{
			if (!NeedArray(3, Nums)) { return; }
			// SEPARATE CALL, not the template. SetParameterValue check()s HasPositionData for a
			// Position parameter, so routing this through it would terminate the editor.
			bWrote = Store.SetPositionParameterValue(FVector(Nums[0], Nums[1], Nums[2]),
													 Found->GetName(), false);
		}
		else
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is a %s, which this endpoint does not write. It is refused rather than "
					 "attempted, because SetParameterValue check()s the size against the type and a "
					 "mismatch TERMINATES the editor - so guessing here does not produce a bad "
					 "value, it ends the process. NOTHING was changed."),
				*Param.GetName().ToString(), *Type.GetName()));
			return;
		}

		Sys->MarkPackageDirty();

		// POSTCONDITION, read back from the store. SetParameterValue's bool reports whether an
		// offset was found, not whether the value is the one that was asked for.
		const uint8* After = Store.GetParameterData(Store.IndexOf(Param));
		Out->SetStringField(TEXT("system"), Sys->GetPathName());
		Out->SetStringField(TEXT("name"), Param.GetName().ToString());
		Out->SetStringField(TEXT("type"), Type.GetName());
		Out->SetBoolField(TEXT("wrote"), bWrote);
		if (!bWrote || !After)
		{
			Fail(Out, FString::Printf(
				TEXT("the store did not accept a value for '%s'. NOTHING reliable was produced - "
					 "read it back with list_niagara_user_parameters before relying on it."),
				*Param.GetName().ToString()));
			return;
		}
		// Echo what is actually there now, decoded the same way the read does it.
		if (Type == FNiagaraTypeDefinition::GetFloatDef())
		{
			float F = 0.0f; FMemory::Memcpy(&F, After, sizeof(float));
			Out->SetNumberField(TEXT("value"), F);
		}
		else if (Type == FNiagaraTypeDefinition::GetIntDef())
		{
			int32 I = 0; FMemory::Memcpy(&I, After, sizeof(int32));
			Out->SetNumberField(TEXT("value"), I);
		}
		else if (Type == FNiagaraTypeDefinition::GetBoolDef())
		{
			int32 I = 0; FMemory::Memcpy(&I, After, sizeof(int32));
			Out->SetBoolField(TEXT("value"), I != 0);
		}
		else
		{
			const int32 Count = Type.GetSize() / int32(sizeof(float));
			TArray<TSharedPtr<FJsonValue>> Arr;
			for (int32 i = 0; i < Count; ++i)
			{
				float F = 0.0f;
				FMemory::Memcpy(&F, After + i * sizeof(float), sizeof(float));
				Arr.Add(MakeShared<FJsonValueNumber>(F));
			}
			Out->SetArrayField(TEXT("value"), Arr);
		}
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the system is dirty and NOTHING has been saved. The value reported above was read "
				 "back out of the parameter store, not echoed from the request."));
#endif
	}
}
