// MifBridge — the INHERITANCE tab: the project's Blueprint class hierarchy as a real tree.
//
// The fourth of the panel's data views, and the one Andre asked for after seeing the competitor's
// Project Dashboard: "id also like the project brainmap, and all that".
//
// WHY IT LOADS NOTHING, which is the whole design rather than an optimisation. A blueprint publishes
// its parent as an ASSET REGISTRY TAG (FBlueprintTags::ParentClassPath, 5.3 BlueprintSupport.h:38 /
// 5.7 :32), so the entire hierarchy is metadata the registry already holds. Building this by loading
// every Blueprint and asking GeneratedClass->GetSuperClass() would be correct, far slower, and on a
// COOKED project actively dangerous - docs/06 issue 16 is an editor that died doing exactly that, and
// DDS2 is cooked. On this project it reads 2855 blueprints out of 32265 assets without loading one.
//
// STreeView rather than the brainmap's custom SLeafWidget. A force-directed graph is the right shape
// for "what depends on what", where the answer is a mesh with no natural root. Inheritance is a
// literal tree with literal roots, and Slate already has a virtualised, expandable, keyboard-navigable
// widget for that. Painting one by hand to match the brainmap would be building something worse for
// the sake of looking consistent.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "Blueprint/BlueprintSupport.h"      // FBlueprintTags - 5.3 :36-40, 5.7 :30-34
#include "Misc/PackageName.h"
#include "Styling/SlateIconFinder.h"
#include "Widgets/Input/SEditableTextBox.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/Text/STextBlock.h"
#include "Widgets/Views/STreeView.h"
#include "Widgets/SBoxPanel.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "Editor.h"

#define LOCTEXT_NAMESPACE "MifBridgeInherit"

namespace MifInherit
{
	// The panel's palette, restated rather than shared. MifBridgePanel.cpp keeps these in an anonymous
	// namespace, and exporting them to make one view reuse them would widen a header for four colours.
	// FColor::FromHex, NOT FLinearColor's float constructor: hex digits are sRGB, and feeding them to
	// FLinearColor directly renders everything about twice as bright - which is exactly what happened
	// the first time the panel was built and Andre said so.
	static const FLinearColor Purple  = FLinearColor(FColor::FromHex(TEXT("A78BFA")));
	static const FLinearColor TextDim = FLinearColor(FColor::FromHex(TEXT("9CA3AF")));
	static const FLinearColor TextHi  = FLinearColor(FColor::FromHex(TEXT("E5E7EB")));
	static const FLinearColor Native  = FLinearColor(FColor::FromHex(TEXT("60A5FA")));

	/** One node. Children are resolved once at build time; STreeView asks for them lazily. */
	struct FNode
	{
		FString AssetPath;       // "/Game/AI/BP_Guard.BP_Guard" - empty for a native root
		FString Display;         // "BP_Guard" or "Actor"
		FString NativeParent;    // the C++ class this branch ultimately derives from
		bool    bNative = false;
		int32   Descendants = 0; // whole subtree, not just direct children
		TArray<TSharedPtr<FNode>> Children;
	};

	using FNodePtr = TSharedPtr<FNode>;

	/** "BlueprintGeneratedClass'/Game/AI/BP_Guard.BP_Guard_C'" -> "/Game/AI/BP_Guard.BP_Guard".
	 *
	 *  The tag is EXPORT TEXT, not a bare path - class prefix and surrounding quotes included. The
	 *  endpoint version of this shipped without the unwrap and produced a tree in which every node was
	 *  a root, because no child's parent ever matched an asset path. Same trap, same fix. */
	static FString TagToAssetPath(const FString& In)
	{
		FString S = FPackageName::ExportTextPathToObjectPath(In);
		S.RemoveFromEnd(TEXT("_C"));
		return S;
	}

	static FString ShortName(const FString& ObjectPath)
	{
		FString S = FPackageName::ObjectPathToObjectName(ObjectPath);
		return S.IsEmpty() ? ObjectPath : S;
	}

	/** Build the whole forest from registry tags. Returns the roots. */
	static void BuildForest(TArray<FNodePtr>& OutRoots, int32& OutBlueprints, bool& bOutScanning)
	{
		OutRoots.Reset();
		OutBlueprints = 0;

		IAssetRegistry& Registry =
			FModuleManager::LoadModuleChecked<FAssetRegistryModule>("AssetRegistry").Get();
		bOutScanning = Registry.IsLoadingAssets();

		TArray<FAssetData> Assets;
		Registry.GetAssetsByPath(FName(TEXT("/Game")), Assets, /*bRecursive*/ true);

		TMap<FString, FNodePtr> ByPath;
		TMap<FString, FString> ParentOf;

		for (const FAssetData& A : Assets)
		{
			FString ParentTag;
			// By TAG, not by class name. A WidgetBlueprint or AnimBlueprint is a blueprint and carries
			// these tags; filtering on ClassName silently drops every one of them.
			if (!A.GetTagValue(FBlueprintTags::ParentClassPath, ParentTag) || ParentTag.IsEmpty())
			{
				continue;
			}
			const FString Self = A.GetObjectPathString();
			FNodePtr N = MakeShared<FNode>();
			N->AssetPath = Self;
			N->Display = ShortName(Self);
			FString NativeTag;
			if (A.GetTagValue(FBlueprintTags::NativeParentClassPath, NativeTag))
			{
				N->NativeParent = ShortName(FPackageName::ExportTextPathToObjectPath(NativeTag));
			}
			ByPath.Add(Self, N);
			ParentOf.Add(Self, TagToAssetPath(ParentTag));
			++OutBlueprints;
		}

		// Native roots are synthesised: a C++ class is not an asset and has no registry entry, so the
		// tree would otherwise be thousands of disconnected blueprints. Grouping them under the class
		// they derive from is the whole reason this reads as a hierarchy rather than a list.
		TMap<FString, FNodePtr> NativeRoots;
		for (const TPair<FString, FNodePtr>& P : ByPath)
		{
			const FString* Parent = ParentOf.Find(P.Key);
			if (Parent && ByPath.Contains(*Parent))
			{
				ByPath[*Parent]->Children.Add(P.Value);
				continue;
			}
			const FString NativeName = P.Value->NativeParent.IsEmpty()
				? TEXT("(unknown native parent)") : P.Value->NativeParent;
			FNodePtr& Root = NativeRoots.FindOrAdd(NativeName);
			if (!Root.IsValid())
			{
				Root = MakeShared<FNode>();
				Root->Display = NativeName;
				Root->bNative = true;
			}
			Root->Children.Add(P.Value);
		}

		// Descendant counts, bottom-up. Shown on the row because "this class has 200 things under it"
		// is the single most useful number in a hierarchy and is invisible until you expand everything.
		TFunction<int32(const FNodePtr&)> Count = [&](const FNodePtr& N) -> int32
		{
			int32 Total = 0;
			for (const FNodePtr& C : N->Children) { Total += 1 + Count(C); }
			N->Descendants = Total;
			return Total;
		};

		NativeRoots.GenerateValueArray(OutRoots);
		for (const FNodePtr& R : OutRoots)
		{
			Count(R);
			// The array holds FNodePtr, so the comparator takes SHARED POINTERS, not FNode&. TArray::Sort
			// deduces from the element type and the mismatch surfaces as an error inside Sorting.h
			// rather than here, which is a long way from the mistake.
			R->Children.Sort([](const FNodePtr& A, const FNodePtr& B)
				{ return A->Display < B->Display; });
		}
		// Biggest first: a hierarchy view opens on what matters, not on whatever sorted first.
		OutRoots.Sort([](const FNodePtr& A, const FNodePtr& B)
			{ return A->Descendants > B->Descendants; });
	}

	class SInheritView : public SCompoundWidget
	{
	public:
		SLATE_BEGIN_ARGS(SInheritView) {}
		SLATE_END_ARGS()

		void Construct(const FArguments&)
		{
			Rebuild();

			ChildSlot
			[
				SNew(SVerticalBox)
				+ SVerticalBox::Slot().AutoHeight().Padding(8.f, 8.f, 8.f, 4.f)
				[
					SNew(SHorizontalBox)
					+ SHorizontalBox::Slot().FillWidth(1.f).VAlign(VAlign_Center)
					[
						SAssignNew(FilterBox, SEditableTextBox)
							.HintText(LOCTEXT("Filter", "filter by name..."))
							.OnTextChanged(this, &SInheritView::OnFilterChanged)
					]
					+ SHorizontalBox::Slot().AutoWidth().Padding(6.f, 0.f, 0.f, 0.f)
					[
						SNew(SButton)
							.Text(LOCTEXT("Refresh", "refresh"))
							.ToolTipText(LOCTEXT("RefreshTip",
								"Re-read the asset registry. Nothing is loaded; this is metadata only."))
							.OnClicked(this, &SInheritView::OnRefresh)
					]
				]
				+ SVerticalBox::Slot().AutoHeight().Padding(8.f, 0.f, 8.f, 6.f)
				[
					SAssignNew(Summary, STextBlock)
						.ColorAndOpacity(FSlateColor(TextDim))
				]
				+ SVerticalBox::Slot().FillHeight(1.f).Padding(4.f)
				[
					SAssignNew(Tree, STreeView<FNodePtr>)
						.TreeItemsSource(&Visible)
						.OnGenerateRow(this, &SInheritView::MakeRow)
						.OnGetChildren(this, &SInheritView::GetChildren)
						.OnMouseButtonDoubleClick(this, &SInheritView::OnActivated)
						.SelectionMode(ESelectionMode::Single)
				]
			];
			UpdateSummary();
		}

	private:
		TSharedPtr<STreeView<FNodePtr>> Tree;
		TSharedPtr<SEditableTextBox> FilterBox;
		TSharedPtr<STextBlock> Summary;
		TArray<FNodePtr> Roots;      // everything
		TArray<FNodePtr> Visible;    // what the tree shows (filtered)
		int32 Blueprints = 0;
		bool bScanning = false;

		void Rebuild()
		{
			BuildForest(Roots, Blueprints, bScanning);
			Visible = Roots;
		}

		void UpdateSummary()
		{
			if (!Summary.IsValid()) { return; }
			FString S = FString::Printf(TEXT("%d blueprints under %d native roots"),
				Blueprints, Roots.Num());
			if (bScanning)
			{
				// The registry still scanning is indistinguishable from a small project unless it says
				// so. Same caveat the endpoint reports as registryStillScanning.
				S += TEXT("   -   ASSET REGISTRY STILL SCANNING, this tree is incomplete");
			}
			S += TEXT("   -   nothing was loaded; registry tags only");
			Summary->SetText(FText::FromString(S));
		}

		FReply OnRefresh()
		{
			Rebuild();
			ApplyFilter(FilterBox.IsValid() ? FilterBox->GetText().ToString() : FString());
			UpdateSummary();
			return FReply::Handled();
		}

		void OnFilterChanged(const FText& Text) { ApplyFilter(Text.ToString()); }

		/** Keep a node if it matches, OR if any descendant does - otherwise filtering a tree hides the
		 *  ancestors of every hit and the results float free of their context. */
		static bool FilterNode(const FNodePtr& In, const FString& Needle, FNodePtr& Out)
		{
			TArray<FNodePtr> Kept;
			for (const FNodePtr& C : In->Children)
			{
				FNodePtr KeptChild;
				if (FilterNode(C, Needle, KeptChild)) { Kept.Add(KeptChild); }
			}
			const bool bSelf = In->Display.Contains(Needle);
			if (!bSelf && Kept.Num() == 0) { return false; }

			Out = MakeShared<FNode>(*In);
			Out->Children = Kept;
			return true;
		}

		void ApplyFilter(const FString& Needle)
		{
			if (Needle.IsEmpty())
			{
				Visible = Roots;
			}
			else
			{
				Visible.Reset();
				for (const FNodePtr& R : Roots)
				{
					FNodePtr Kept;
					if (FilterNode(R, Needle, Kept)) { Visible.Add(Kept); }
				}
				// A filtered tree with everything collapsed shows nothing but roots, which reads as
				// "no results". Expand what survived.
				for (const FNodePtr& R : Visible) { ExpandAll(R); }
			}
			if (Tree.IsValid()) { Tree->RequestTreeRefresh(); }
		}

		void ExpandAll(const FNodePtr& N)
		{
			if (!Tree.IsValid()) { return; }
			Tree->SetItemExpansion(N, true);
			for (const FNodePtr& C : N->Children) { ExpandAll(C); }
		}

		void GetChildren(FNodePtr In, TArray<FNodePtr>& Out) { Out = In->Children; }

		void OnActivated(FNodePtr In)
		{
			// Native rows have no asset to open. Doing nothing is right; pretending otherwise by
			// flashing an error would be worse.
			if (!In.IsValid() || In->bNative || In->AssetPath.IsEmpty()) { return; }
			if (UObject* Asset = LoadObject<UObject>(nullptr, *In->AssetPath, nullptr,
													LOAD_NoWarn | LOAD_Quiet))
			{
				// THE ONLY PLACE THIS VIEW LOADS ANYTHING, and only because the user double-clicked
				// asking to open it. Building the tree loads nothing; opening one asset on request is
				// a different act with a different risk, and it is the editor's own path.
				if (GEditor)
				{
					GEditor->GetEditorSubsystem<UAssetEditorSubsystem>()->OpenEditorForAsset(Asset);
				}
			}
		}

		TSharedRef<ITableRow> MakeRow(FNodePtr In, const TSharedRef<STableViewBase>& Owner)
		{
			const FLinearColor NameColour = In->bNative ? Native : TextHi;
			FString Suffix;
			if (In->Descendants > 0)
			{
				Suffix = FString::Printf(TEXT("  %d"), In->Descendants);
			}

			return SNew(STableRow<FNodePtr>, Owner)
			[
				SNew(SHorizontalBox)
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0.f, 0.f, 6.f, 0.f)
				[
					SNew(STextBlock)
						.Text(FText::FromString(In->bNative ? TEXT("C++") : TEXT("BP")))
						.ColorAndOpacity(FSlateColor(In->bNative ? Native : Purple))
						.Font(FCoreStyle::GetDefaultFontStyle("Bold", 7))
				]
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
				[
					SNew(STextBlock)
						.Text(FText::FromString(In->Display))
						.ColorAndOpacity(FSlateColor(NameColour))
				]
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
				[
					SNew(STextBlock)
						.Text(FText::FromString(Suffix))
						.ColorAndOpacity(FSlateColor(TextDim))
						.ToolTipText(LOCTEXT("DescTip", "descendants in the whole subtree"))
				]
			];
		}
	};
}

namespace MifBridge
{
	TSharedRef<SWidget> MakeInheritWidget()
	{
		return SNew(MifInherit::SInheritView);
	}
}

#undef LOCTEXT_NAMESPACE
