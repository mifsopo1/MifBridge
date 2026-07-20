// MifBridge — create_editable_child: drive the engine Kismet module's headless editable-copy export
// (CreateEditableBlueprintCopy) to mint a PERSISTENT editable child/sibling of a cooked blueprint.
// This is the programmatic form of the right-click "Create Editable Child Blueprint" action.
// Self-managed (it compiles + saves an asset). The COMPANION half — decompile — is not here: the
// reconstructor exposes `mif.kr.Reconstruct <BP> <Fn>` as a console command reached via run_console.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "CompiledBlueprintReconstructor.h"     // CreateEditableBlueprintCopy (KISMET_API, engine fork)
#include "Engine/Blueprint.h"
#include "Engine/BlueprintGeneratedClass.h"
#include "UObject/UObjectGlobals.h"

namespace MifBridge
{
	// { sourceAsset, childPath?, variant?: "child" | "sibling" | "uncooked" | "sibling_full" | "full" }
	// sourceAsset: the cooked BP — pass its generated-class path (…/BP_Foo.BP_Foo_C) or the plain asset path.
	// variant "child" = IS-A source (inherits CDO); "sibling"/"uncooked" = parent-class copy (CDO stamped);
	// "sibling_full"/"full" = sibling whose Blueprint-parent chain is ALSO reconstructed into editable siblings
	// (each saved as "<Ancestor>_Editable" beside the leaf), so no parent layer is left as cooked stubs.
	void H_create_editable_child(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
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
}
