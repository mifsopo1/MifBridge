// MifBridge — create_editable_child: drive the engine Kismet module's headless editable-copy export
// (CreateEditableBlueprintCopy) to mint a PERSISTENT editable child/sibling of a cooked blueprint.
// This is the programmatic form of the right-click "Create Editable Child Blueprint" action.
// Self-managed (it compiles + saves an asset). The COMPANION half — decompile — is not here: the
// reconstructor exposes `mif.kr.Reconstruct <BP> <Fn>` as a console command reached via run_console.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#if MIF_WITH_RECONSTRUCTOR
#include "CompiledBlueprintReconstructor.h"   // CreateEditableBlueprintCopy - ENGINE FORK ONLY, see below
#endif
#include "Engine/Blueprint.h"
#include "Engine/BlueprintGeneratedClass.h"
#include "UObject/UObjectGlobals.h"

namespace MifBridge
{
#if !MIF_WITH_RECONSTRUCTOR
	// Stock engine. The endpoint stays REGISTERED and refuses with the reason, for the same argument
	// as every other guard in this plugin: a caller who gets 'unknown endpoint' learns nothing and has
	// no way to find out, while a caller who gets this message knows exactly where they stand.
	//
	// The wording matters here. Every other guarded endpoint can be brought back by enabling a plugin
	// or moving to an engine that ships it. This one cannot. CreateEditableBlueprintCopy is a DDS2 fork
	// addition to the engine's own Kismet module, and no stock Unreal at any version has an equivalent -
	// the editable-copy path simply is not exposed outside the editor UI. Saying 'rebuild against a newer
	// engine' would be actively misleading, so it says what is actually true instead.
	void H_create_editable_child(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		Fail(Out, TEXT(
			"create_editable_child is unavailable: it needs CreateEditableBlueprintCopy from "
			"Engine/Source/Editor/Kismet, which exists only in the ENGINE FORK this was built "
			"against and in no "
			"stock Unreal of any version. This is not a plugin you can enable and not something "
			"a newer engine adds - stock UE does not expose the editable-copy path outside the "
			"editor's own right-click menu. On a stock engine, duplicate an UNCOOKED blueprint "
			"with duplicate_asset instead; a cooked one cannot be made editable at all."));
	}
#else
	// { sourceAsset, childPath?, variant?: "child" | "sibling" | "uncooked" | "sibling_full" | "full" }
	// sourceAsset: the cooked BP — pass its generated-class path (…/BP_Foo.BP_Foo_C) or the plain asset path.
	// variant "child" = IS-A source (inherits CDO); "sibling"/"uncooked" = parent-class copy (CDO stamped);
	// "sibling_full"/"full" = sibling whose Blueprint-parent chain is ALSO reconstructed into editable siblings
	// (each saved as "<Ancestor>_Editable" beside the leaf), so no parent layer is left as cooked stubs.
	void H_create_editable_child(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("sourceAsset"), TEXT("childPath"), TEXT("variant") },
			TEXT("sourceAsset (the cooked BP - its _C class path or its asset path), childPath (destination; defaults to /Game/Mif/<Name>_Child or _Editable), variant: child | sibling | uncooked | sibling_full | full"),
			{ { TEXT("blueprintId"), TEXT("spell it sourceAsset - pass the cooked BP's _C class path (/Game/X/BP_Foo.BP_Foo_C) or its asset path") },
			  { TEXT("path"), TEXT("the SOURCE is sourceAsset; the DESTINATION is childPath") },
			  { TEXT("source"), TEXT("spell it sourceAsset") },
			  { TEXT("targetPath"), TEXT("spell it childPath") },
			  { TEXT("asChild"), TEXT("there is no boolean form - it is variant:\"child\" (the default) vs variant:\"sibling\"") },
			  { TEXT("fullParent"), TEXT("there is no boolean form - it is variant:\"sibling_full\" (alias: \"full\")") },
			  { TEXT("name"), TEXT("the new asset's name comes from childPath - pass the full destination package path") } }))
		{
			return;
		}

		const FString SourceAsset = JStr(In, TEXT("sourceAsset"));
		if (SourceAsset.IsEmpty())
		{
			Fail(Out, TEXT("sourceAsset required (the cooked BP: its _C class path or asset path)"));
			return;
		}

		// Resolve to the cooked generated class. Accept a *_C class path OR a UBlueprint asset path.
		UBlueprintGeneratedClass* SourceBPGC =
			LoadObject<UBlueprintGeneratedClass>(nullptr, *SourceAsset, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!SourceBPGC)
		{
			if (UBlueprint* BP = LoadObject<UBlueprint>(nullptr, *SourceAsset, nullptr, LOAD_NoWarn | LOAD_Quiet))
			{
				SourceBPGC = Cast<UBlueprintGeneratedClass>(BP->GeneratedClass);
			}
		}
		if (!SourceBPGC)
		{
			Fail(Out, FString::Printf(TEXT("source blueprint class not found: '%s' (try the .<Name>_C class path)"), *SourceAsset));
			return;
		}

		const FString Variant = JStr(In, TEXT("variant"), TEXT("child"));
		const bool bAsChild = Variant.Equals(TEXT("child"), ESearchCase::IgnoreCase);
		const bool bFullParent = Variant.Equals(TEXT("sibling_full"), ESearchCase::IgnoreCase)
			|| Variant.Equals(TEXT("full"), ESearchCase::IgnoreCase);

		FString TargetPath = JStr(In, TEXT("childPath"));
		if (TargetPath.IsEmpty())
		{
			FString BaseName = SourceBPGC->GetName();
			BaseName.RemoveFromEnd(TEXT("_C"));   // BP_Foo_C -> BP_Foo
			TargetPath = FString::Printf(TEXT("/Game/Mif/%s_%s"), *BaseName, bAsChild ? TEXT("Child") : TEXT("Editable"));
		}

		FText Err;
		UBlueprint* NewBP = CreateEditableBlueprintCopy(SourceBPGC, TargetPath, bAsChild, &Err, bFullParent);
		if (!NewBP)
		{
			Fail(Out, FString::Printf(TEXT("create_editable_child failed: %s"), *Err.ToString()));
			return;
		}

		Out->SetStringField(TEXT("blueprintId"), NewBP->GetPathName());
		Out->SetStringField(TEXT("assetPath"), TargetPath);
		Out->SetStringField(TEXT("source"), SourceBPGC->GetPathName());
		Out->SetBoolField(TEXT("asChild"), bAsChild);
		Out->SetBoolField(TEXT("fullParent"), bFullParent);
		if (NewBP->GeneratedClass) { Out->SetStringField(TEXT("class"), NewBP->GeneratedClass->GetPathName()); }
		// Graphs are filled with decompiled nodes iff the MifKismetReconstructor delegate is bound;
		// otherwise function/event graphs are signature-only stubs (see CompiledBlueprintReconstructor.h).
		UE_LOG(LogMifBridge, Log, TEXT("create_editable_child: %s -> %s (child=%d fullParent=%d)"),
			*SourceBPGC->GetName(), *TargetPath, bAsChild ? 1 : 0, bFullParent ? 1 : 0);
	}

#endif   // MIF_WITH_RECONSTRUCTOR
}
