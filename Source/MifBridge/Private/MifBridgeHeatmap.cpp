// The complexity heatmap — every asset under a prefix, sorted by how connected it is.
//
// Andre sent the competitor's Project Dashboard as reference. Its two most READABLE views are not
// force-directed graphs at all: the Complexity Heatmap is sorted coloured rectangles, and the
// Inheritance Tree is collapsible lists. That is worth taking seriously rather than treating the graph
// as the goal - a hairball tells you a project is complicated; a sorted list tells you WHICH THING is
// complicated, which is the question anyone actually has.
//
// So this view answers one question directly: what in here has the most connections, and therefore what
// is most expensive to change or most dangerous to delete.
//
// ============================================================================================
// WHAT "CONNECTIONS" MEANS HERE, stated because a number without a definition is worse than none.
// ============================================================================================
//
// connections = referencers + dependencies, both from the Asset Registry.
//
//   referencers  - what would break if this were deleted
//   dependencies - what this needs in order to work
//
// Summing them is a deliberate simplification: it produces one orderable number, which is what a
// heatmap needs. Both halves are shown on the card so the sum is never the only thing on offer - a
// package with 200 referencers and 2 dependencies is a shared foundation, and one with 2 referencers
// and 200 dependencies is a god object, and those are opposite problems that share a total.
//
// COST: GetReferencers and GetDependencies each run PER ASSET. Same bound as everything else in this
// area - a mount root is a stopped game thread, not a slow view. The prefix guard and the cap are not
// optional here.

#include "MifBridgeHandlers.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "Editor.h"
#include "Styling/AppStyle.h"
#include "Styling/CoreStyle.h"
#include "Brushes/SlateRoundedBoxBrush.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/Layout/SWrapBox.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Text/STextBlock.h"

#define LOCTEXT_NAMESPACE "MifHeatmap"

namespace MifHeat
{
	static FLinearColor Hex(const TCHAR* RGB) { return FLinearColor(FColor::FromHex(RGB)); }

	// Heat by RANK, not by absolute count.
	//
	// An absolute scale would paint almost everything green in a project whose busiest package has 224
	// connections and whose median has 3 - the colour would carry no information for 95% of the cards.
	// Ranking makes the top of any list hot, which is the comparison a reader is actually making.
	static FLinearColor HeatFor(int32 Rank, int32 Total)
	{
		const float T = (Total <= 1) ? 0.f : (float)Rank / (float)(Total - 1);
		if (T < 0.04f) { return Hex(TEXT("E5484D")); }   // red    - the top few
		if (T < 0.12f) { return Hex(TEXT("F76B15")); }   // orange
		if (T < 0.28f) { return Hex(TEXT("FFB224")); }   // amber
		if (T < 0.55f) { return Hex(TEXT("46A758")); }   // green
		return Hex(TEXT("30A46C"));                      // deeper green - the long tail
	}

	struct FEntry
	{
		FString Package;
		FString Name;
		FString Class;
		int32   Refs = 0;
		int32   Deps = 0;
		int32   Total() const { return Refs + Deps; }
	};
}

class SMifHeatmap : public SCompoundWidget
{
public:
	SLATE_BEGIN_ARGS(SMifHeatmap) {}
	SLATE_END_ARGS()

	void Construct(const FArguments&)
	{
		ChildSlot
		[
			SNew(SVerticalBox)
			+ SVerticalBox::Slot().AutoHeight().Padding(2, 0, 0, 8)
			[
				SNew(SHorizontalBox)
				+ SHorizontalBox::Slot().AutoWidth()
				[
					SNew(STextBlock)
						.Text(LOCTEXT("Title", "COMPLEXITY HEATMAP"))
						.ColorAndOpacity(FSlateColor(MifHeat::Hex(TEXT("C4B5FD"))))
						.Font(FCoreStyle::GetDefaultFontStyle("Bold", 9))
				]
				+ SHorizontalBox::Slot().FillWidth(1.f).HAlign(HAlign_Right)
				[
					SNew(STextBlock)
						.Text(this, &SMifHeatmap::GetSubtitle)
						.ColorAndOpacity(FSlateColor(MifHeat::Hex(TEXT("7B8296"))))
						.Font(FCoreStyle::GetDefaultFontStyle("Regular", 8))
				]
			]
			+ SVerticalBox::Slot().FillHeight(1.f)
			[
				SNew(SScrollBox)
				+ SScrollBox::Slot()
				[
					SAssignNew(Grid, SWrapBox).UseAllottedSize(true)
				]
			]
		];
		Rebuild(TEXT("/Game/Blueprints"));
	}

	void Rebuild(const FString& InPrefix)
	{
		Prefix = InPrefix;
		Entries.Reset();

		IAssetRegistry& Reg =
			FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
		if (Reg.IsLoadingAssets())
		{
			Subtitle = LOCTEXT("Scanning", "registry still scanning");
			Refresh();
			return;
		}

		TArray<FAssetData> Assets;
		Reg.GetAssetsByPath(FName(*Prefix), Assets, /*bRecursive*/ true);

		// Same cap and same reason as everywhere else in this area: the two registry queries below run
		// PER ASSET.
		const int32 MaxEntries = 400;
		TSet<FName> Seen;
		for (const FAssetData& A : Assets)
		{
			if (Entries.Num() >= MaxEntries) { break; }
			if (Seen.Contains(A.PackageName)) { continue; }
			Seen.Add(A.PackageName);

			TArray<FName> Refs, Deps;
			Reg.GetReferencers(A.PackageName, Refs);
			Reg.GetDependencies(A.PackageName, Deps);

			MifHeat::FEntry E;
			E.Package = A.PackageName.ToString();
			E.Name = A.AssetName.ToString();
			E.Class = A.AssetClassPath.GetAssetName().ToString();
			E.Refs = Refs.Num();
			E.Deps = Deps.Num();
			Entries.Add(E);
		}

		Entries.Sort([](const MifHeat::FEntry& A, const MifHeat::FEntry& B)
		{
			return A.Total() > B.Total();
		});

		Subtitle = FText::FromString(FString::Printf(
			TEXT("%s   -   %d of %d packages%s"), *Prefix, Entries.Num(), Assets.Num(),
			Assets.Num() > Entries.Num() ? TEXT("   (capped)") : TEXT("")));
		Refresh();
	}

private:
	void Refresh()
	{
		if (!Grid.IsValid()) { return; }
		Grid->ClearChildren();

		for (int32 i = 0; i < Entries.Num(); ++i)
		{
			const MifHeat::FEntry& E = Entries[i];
			const FLinearColor Heat = MifHeat::HeatFor(i, Entries.Num());

			Grid->AddSlot().Padding(3)
			[
				SNew(SBox).WidthOverride(228.f).HeightOverride(46.f)
				[
					SNew(SButton)
						.ButtonStyle(FAppStyle::Get(), "NoBorder")
						.ContentPadding(FMargin(0))
						.ToolTipText(FText::FromString(E.Package))
						.OnClicked(this, &SMifHeatmap::OnPick, E.Package)
						[
							SNew(SBorder)
								.BorderImage(Card())
								.BorderBackgroundColor(FSlateColor(Heat))
								.Padding(FMargin(9, 5))
							[
								SNew(SVerticalBox)
								+ SVerticalBox::Slot().AutoHeight()
								[
									SNew(STextBlock)
										.Text(FText::FromString(E.Name))
										.ColorAndOpacity(FSlateColor(FLinearColor::White))
										.Font(FCoreStyle::GetDefaultFontStyle("Bold", 8))
								]
								+ SVerticalBox::Slot().AutoHeight().Padding(0, 2, 0, 0)
								[
									// BOTH halves, never only the sum. 200 referencers with 2
									// dependencies is a shared foundation; 2 with 200 is a god object.
									// Opposite problems, identical total.
									SNew(STextBlock)
										.Text(FText::FromString(FString::Printf(
											TEXT("%d in / %d out  -  %s"), E.Refs, E.Deps, *E.Class)))
										.ColorAndOpacity(FSlateColor(FLinearColor(1, 1, 1, 0.82f)))
										.Font(FCoreStyle::GetDefaultFontStyle("Regular", 7))
								]
							]
						]
				]
			];
		}
	}

	static const FSlateBrush* Card()
	{
		static const FSlateRoundedBoxBrush B(FLinearColor::White, 5.f);
		return &B;
	}

	FReply OnPick(FString Package)
	{
		IAssetRegistry& Reg =
			FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
		TArray<FAssetData> Found;
		// By PACKAGE, and never loaded - the same rule the transcript's subject link follows, and for
		// the same cooked-content reason.
		Reg.GetAssetsByPackageName(FName(*Package), Found);
		if (Found.Num() > 0 && GEditor)
		{
			GEditor->SyncBrowserToObjects(Found);
		}
		return FReply::Handled();
	}

	FText GetSubtitle() const { return Subtitle; }

	TSharedPtr<SWrapBox> Grid;
	TArray<MifHeat::FEntry> Entries;
	FString Prefix;
	FText Subtitle;
};

namespace MifBridge
{
	TSharedRef<SWidget> MakeHeatmapWidget()
	{
		return SNew(SMifHeatmap);
	}
}

#undef LOCTEXT_NAMESPACE
