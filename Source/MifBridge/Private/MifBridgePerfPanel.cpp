// The performance tab — the heaviest actors in the loaded level, with a bar each.
//
// Andre: "a performance dashboard to show whats the most fps consuming and stuff".
//
// READ THE HEADER OF MifBridgePerfView.cpp BEFORE TRUSTING ANY NUMBER HERE. This view renders a CENSUS
// of static content cost - triangles, components, material slots - not a profile. It cannot see a
// Blueprint burning milliseconds in Tick, and it is not frame time. The competitor's Performance tab
// reads Unreal Insights traces; matching the tab is easy and matching what it MEASURES is not, so this
// says what it is rather than implying it is the same thing.
//
// That caveat is on screen, not just in the source. A performance panel that quietly measures the
// wrong thing is worse than no panel, because it gets believed.

#include "MifBridgeHandlers.h"

#include "Brushes/SlateRoundedBoxBrush.h"
#include "Components/PrimitiveComponent.h"
#include "Editor.h"
#include "EngineUtils.h"
#include "GameFramework/Actor.h"
#include "Styling/AppStyle.h"
#include "Styling/CoreStyle.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Text/STextBlock.h"

#define LOCTEXT_NAMESPACE "MifPerfPanel"

namespace MifPerfUI
{
	static FLinearColor Hex(const TCHAR* RGB) { return FLinearColor(FColor::FromHex(RGB)); }

	struct FRow
	{
		FString Name, Class, Path;
		int32 Tris = 0, Comps = 0, Mats = 0;
	};
}

class SMifPerfPanel : public SCompoundWidget
{
public:
	SLATE_BEGIN_ARGS(SMifPerfPanel) {}
	SLATE_END_ARGS()

	void Construct(const FArguments&)
	{
		ChildSlot
		[
			SNew(SVerticalBox)
			+ SVerticalBox::Slot().AutoHeight().Padding(2, 0, 0, 6)
			[
				SNew(SHorizontalBox)
				+ SHorizontalBox::Slot().AutoWidth()
				[
					SNew(STextBlock)
						.Text(LOCTEXT("Title", "HEAVIEST ACTORS"))
						.ColorAndOpacity(FSlateColor(MifPerfUI::Hex(TEXT("C4B5FD"))))
						.Font(FCoreStyle::GetDefaultFontStyle("Bold", 9))
				]
				+ SHorizontalBox::Slot().FillWidth(1.f).HAlign(HAlign_Right)
				[
					SNew(STextBlock).Text(this, &SMifPerfPanel::GetSummary)
						.ColorAndOpacity(FSlateColor(MifPerfUI::Hex(TEXT("7B8296"))))
						.Font(FCoreStyle::GetDefaultFontStyle("Regular", 8))
				]
			]
			// The caveat is ON SCREEN, not only in the source. This panel will be believed, and what it
			// measures is narrower than "fps".
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 8)
			[
				SNew(SBorder)
					.BorderImage(Card())
					.BorderBackgroundColor(FSlateColor(FLinearColor(0.98f, 0.65f, 0.28f, 0.13f)))
					.Padding(FMargin(9, 6))
				[
					SNew(STextBlock)
						.Text(LOCTEXT("Caveat",
							"Static content cost - triangles, components, material slots. NOT frame "
							"time, and it cannot see a Blueprint burning time in Tick. Use Unreal "
							"Insights for real frame attribution."))
						.AutoWrapText(true)
						.ColorAndOpacity(FSlateColor(MifPerfUI::Hex(TEXT("FBA53E"))))
						.Font(FCoreStyle::GetDefaultFontStyle("Regular", 7))
				]
			]
			+ SVerticalBox::Slot().FillHeight(1.f)
			[
				SAssignNew(List, SScrollBox)
			]
		];
		Rebuild();
	}

	void Rebuild()
	{
		Rows.Reset();
		TotalTris = 0;

		UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
		if (!World)
		{
			Summary = LOCTEXT("NoWorld", "no editor world");
			Refresh();
			return;
		}

		for (TActorIterator<AActor> It(World); It; ++It)
		{
			AActor* Actor = *It;
			if (!IsValid(Actor)) { continue; }

			MifPerfUI::FRow R;
			R.Name = Actor->GetActorLabel();
			R.Class = Actor->GetClass()->GetName();
			R.Path = Actor->GetPathName();

			TArray<UPrimitiveComponent*> Prims;
			Actor->GetComponents<UPrimitiveComponent>(Prims);
			for (UPrimitiveComponent* P : Prims)
			{
				if (!IsValid(P)) { continue; }
				++R.Comps;
				R.Mats += P->GetNumMaterials();
				R.Tris += MifBridge::PerfTrianglesFor(P);
			}
			TotalTris += R.Tris;
			// Actors that draw nothing cannot cost drawing time, and listing them buries the ones that
			// can. They still count toward the examined total in the summary.
			if (R.Comps > 0) { Rows.Add(R); }
		}
		Examined = Rows.Num();
		Rows.Sort([](const MifPerfUI::FRow& A, const MifPerfUI::FRow& B) { return A.Tris > B.Tris; });

		Summary = FText::FromString(FString::Printf(
			TEXT("%s   -   %d actors with geometry, %lld triangles"),
			*World->GetName(), Rows.Num(), (long long)TotalTris));
		Refresh();
	}

private:
	static const FSlateBrush* Card()
	{
		static const FSlateRoundedBoxBrush B(FLinearColor::White, 5.f);
		return &B;
	}

	void Refresh()
	{
		if (!List.IsValid()) { return; }
		List->ClearChildren();

		const int32 Shown = FMath::Min(Rows.Num(), 60);
		const int32 Peak = Rows.Num() > 0 ? FMath::Max(Rows[0].Tris, 1) : 1;

		for (int32 i = 0; i < Shown; ++i)
		{
			const MifPerfUI::FRow& R = Rows[i];
			// Bar length is relative to the HEAVIEST actor, not to an absolute budget. There is no
			// universal triangle budget to measure against, and inventing one would be a number
			// pretending to be a threshold.
			const float Frac = (float)R.Tris / (float)Peak;
			const float Pct = TotalTris > 0 ? (float)R.Tris * 100.f / (float)TotalTris : 0.f;
			const FLinearColor Heat = Frac > 0.55f ? MifPerfUI::Hex(TEXT("E5484D"))
								   : Frac > 0.22f ? MifPerfUI::Hex(TEXT("F76B15"))
								   : Frac > 0.06f ? MifPerfUI::Hex(TEXT("FFB224"))
												  : MifPerfUI::Hex(TEXT("46A758"));

			List->AddSlot().Padding(0, 0, 6, 3)
			[
				SNew(SButton)
					.ButtonStyle(FAppStyle::Get(), "NoBorder")
					.ContentPadding(FMargin(0))
					.ToolTipText(FText::FromString(R.Path))
					.OnClicked(this, &SMifPerfPanel::OnPick, R.Path)
					[
						SNew(SBorder)
							.BorderImage(Card())
							.BorderBackgroundColor(FSlateColor(MifPerfUI::Hex(TEXT("16161F"))))
							.Padding(FMargin(10, 6))
						[
							SNew(SVerticalBox)
							+ SVerticalBox::Slot().AutoHeight()
							[
								SNew(SHorizontalBox)
								+ SHorizontalBox::Slot().FillWidth(1.f).VAlign(VAlign_Center)
								[
									SNew(STextBlock).Text(FText::FromString(R.Name))
										.ColorAndOpacity(FSlateColor(MifPerfUI::Hex(TEXT("E2E5EE"))))
										.Font(FCoreStyle::GetDefaultFontStyle("Bold", 8))
								]
								+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
								[
									SNew(STextBlock)
										.Text(FText::FromString(FString::Printf(
											TEXT("%d tris  -  %d comp  -  %d mat  -  %.1f%%"),
											R.Tris, R.Comps, R.Mats, Pct)))
										.ColorAndOpacity(FSlateColor(MifPerfUI::Hex(TEXT("7B8296"))))
										.Font(FCoreStyle::GetDefaultFontStyle("Regular", 7))
								]
							]
							+ SVerticalBox::Slot().AutoHeight().Padding(0, 5, 0, 0)
							[
								SNew(SBox).HeightOverride(4.f)
								[
									SNew(SHorizontalBox)
									+ SHorizontalBox::Slot().FillWidth(FMath::Max(Frac, 0.004f))
									[
										SNew(SBorder).BorderImage(Card())
											.BorderBackgroundColor(FSlateColor(Heat))
											[ SNew(SSpacer) ]
									]
									+ SHorizontalBox::Slot().FillWidth(FMath::Max(1.f - Frac, 0.001f))
									[
										SNew(SSpacer)
									]
								]
							]
						]
					]
			];
		}

		if (Rows.Num() > Shown)
		{
			List->AddSlot().Padding(4, 6)
			[
				SNew(STextBlock)
					.Text(FText::FromString(FString::Printf(
						TEXT("... and %d more actors with geometry"), Rows.Num() - Shown)))
					.ColorAndOpacity(FSlateColor(MifPerfUI::Hex(TEXT("7B8296"))))
					.Font(FCoreStyle::GetDefaultFontStyle("Regular", 7))
			];
		}
	}

	FReply OnPick(FString ActorPath)
	{
		// Select the actor in the level rather than the Content Browser: this view is about a PLACEMENT,
		// not an asset, and revealing the asset would answer a different question.
		if (!GEditor) { return FReply::Handled(); }
		if (AActor* Found = FindObject<AActor>(nullptr, *ActorPath))
		{
			GEditor->SelectNone(false, true);
			GEditor->SelectActor(Found, true, true);
			GEditor->MoveViewportCamerasToActor(*Found, false);
		}
		return FReply::Handled();
	}

	FText GetSummary() const { return Summary; }

	TSharedPtr<SScrollBox> List;
	TArray<MifPerfUI::FRow> Rows;
	FText Summary;
	int64 TotalTris = 0;
	int32 Examined = 0;
};

namespace MifBridge
{
	TSharedRef<SWidget> MakePerfWidget()
	{
		return SNew(SMifPerfPanel);
	}
}

#undef LOCTEXT_NAMESPACE
