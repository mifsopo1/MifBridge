// MifBridge — the SKELETAL SPLIT tab: which bones map to which material section, and which of them
// could be split out cleanly.
//
// Andre's ask, after the other three in-editor asks (inheritance tree, behavior tree diagram, write-mode
// dropdown) each got a panel tab and this one did not - the mesh splitter's ANALYSIS half
// (analyze_skeletal_split) has existed as a bridge endpoint since 2026-08-27, but nothing showed it
// inside the editor itself. This is that: a "material splitting map" - a visual, colour-coded view of
// which bones drive which section (and therefore which material), and whether a given bone could be
// pulled into its own mesh without touching any other section's geometry.
//
// IT CALLS THE ENDPOINT'S HANDLER, not a copy of its logic - same rule as MifBridgeBehaviorView.cpp and
// MifBridgeInheritView.cpp, and the same reasons: one implementation, the view is a live test of the
// endpoint, and anything the endpoint learns to report shows here for free.
//
// FAILURE IS THE PRESENCE OF `error`, NOT THE ABSENCE OF `ok` - H_analyze_skeletal_split never sets
// `ok:true` itself (RunEndpoint does that, after the handler returns, and this view calls the handler
// directly). See MifBridgeBehaviorView.cpp's header for the full explanation; the same gotcha applies
// here unchanged.
//
// COLOUR IS BY MATERIAL INDEX, HASHED, because the point of this view is answering "if I split THIS
// bone out, what draw calls does it touch" - and a section IS a material's draw call. A bone shown in
// exactly one colour touches exactly one section and can be split cleanly; a bone showing several
// colours is shared across sections and splitting it would cut every one of them, which is exactly the
// distinction analyze_skeletal_split's own `cleanlySeparableBones` field exists to draw.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "Dom/JsonObject.h"
#include "Styling/AppStyle.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/Layout/SSplitter.h"
#include "Widgets/Layout/SWrapBox.h"
#include "Widgets/Text/STextBlock.h"
#include "Widgets/Views/SListView.h"
#include "Widgets/SBoxPanel.h"

#define LOCTEXT_NAMESPACE "MifBridgeSkeletalSplit"

namespace MifSkeletalSplit
{
	// sRGB hex through FColor::FromHex - see MifBridgeInheritView.cpp's note on why the float
	// constructor renders everything twice as bright.
	static const FLinearColor TextDim   = FLinearColor(FColor::FromHex(TEXT("9CA3AF")));
	static const FLinearColor TextHi    = FLinearColor(FColor::FromHex(TEXT("E5E7EB")));
	static const FLinearColor Green     = FLinearColor(FColor::FromHex(TEXT("34D399")));
	static const FLinearColor Amber     = FLinearColor(FColor::FromHex(TEXT("F59E0B")));
	static const FLinearColor Red       = FLinearColor(FColor::FromHex(TEXT("F87171")));

	/** One colour per material/section index, deterministic and stable across a call - the same index
	 *  always renders the same colour within one response, which is what makes the bone list and the
	 *  section strip readable as ONE map rather than two unrelated lists. Hashed round the hue wheel
	 *  rather than picked from a fixed palette, because the section count is not known in advance -
	 *  the mesh with the most sections seen so far in this project is 12, and a fixed palette runs out. */
	static FLinearColor SectionColour(int32 Index)
	{
		const uint8 Hue = static_cast<uint8>((Index * 47) % 256);   // 47 is coprime with 256: no early repeat
		return FLinearColor::MakeFromHSV8(Hue, 200, 235);
	}

	struct FSectionRow
	{
		int32 Index = 0, MaterialIndex = 0, Vertices = 0, Triangles = 0, BoneCount = 0;
	};

	struct FBoneRow
	{
		FString Name;
		TArray<int32> Sections;   // which section indices this bone's vertices belong to
		bool bInfluencesGeometry = false;
	};
	using FBonePtr = TSharedPtr<FBoneRow>;

	struct FMeshAsset
	{
		FString Path, Display;
	};
	using FMeshAssetPtr = TSharedPtr<FMeshAsset>;

	class SSkeletalSplitView : public SCompoundWidget
	{
	public:
		SLATE_BEGIN_ARGS(SSkeletalSplitView) {}
		SLATE_END_ARGS()

		void Construct(const FArguments&)
		{
			RefreshAssetList();

			ChildSlot
			[
				SNew(SSplitter)
				+ SSplitter::Slot().Value(0.3f)
				[
					SNew(SVerticalBox)
					+ SVerticalBox::Slot().AutoHeight().Padding(6.f)
					[
						SNew(STextBlock)
							.Text(FText::Format(LOCTEXT("Count", "{0} skeletal meshes"),
								FText::AsNumber(Assets.Num())))
							.ColorAndOpacity(FSlateColor(TextDim))
					]
					+ SVerticalBox::Slot().FillHeight(1.f)
					[
						SAssignNew(AssetList, SListView<FMeshAssetPtr>)
							.ListItemsSource(&Assets)
							.OnGenerateRow(this, &SSkeletalSplitView::MakeAssetRow)
							.OnSelectionChanged(this, &SSkeletalSplitView::OnAssetPicked)
							.SelectionMode(ESelectionMode::Single)
					]
				]
				+ SSplitter::Slot().Value(0.7f)
				[
					SNew(SVerticalBox)
					+ SVerticalBox::Slot().AutoHeight().Padding(8.f, 6.f)
					[
						SAssignNew(Header, STextBlock)
							.Text(LOCTEXT("Pick", "pick a skeletal mesh on the left"))
							.ColorAndOpacity(FSlateColor(TextDim))
							.AutoWrapText(true)
					]
					+ SVerticalBox::Slot().AutoHeight().Padding(8.f, 0.f, 8.f, 6.f)
					[
						SAssignNew(Verdict, STextBlock)
							.ColorAndOpacity(FSlateColor(TextDim))
							.AutoWrapText(true)
					]
					+ SVerticalBox::Slot().AutoHeight().Padding(8.f, 0.f, 8.f, 4.f)
					[
						SAssignNew(SectionStrip, SWrapBox)
							.UseAllottedSize(true)
					]
					+ SVerticalBox::Slot().FillHeight(1.f).Padding(4.f)
					[
						SAssignNew(BoneList, SListView<FBonePtr>)
							.ListItemsSource(&Bones)
							.OnGenerateRow(this, &SSkeletalSplitView::MakeBoneRow)
							.SelectionMode(ESelectionMode::None)
					]
				]
			];
		}

	private:
		TArray<FMeshAssetPtr> Assets;
		TArray<FSectionRow> Sections;
		TArray<FBonePtr> Bones;
		TSharedPtr<SListView<FMeshAssetPtr>> AssetList;
		TSharedPtr<SListView<FBonePtr>> BoneList;
		TSharedPtr<STextBlock> Header;
		TSharedPtr<STextBlock> Verdict;
		TSharedPtr<SWrapBox> SectionStrip;

		void RefreshAssetList()
		{
			Assets.Reset();
			IAssetRegistry& Registry =
				FModuleManager::LoadModuleChecked<FAssetRegistryModule>("AssetRegistry").Get();
			TArray<FAssetData> Found;
			// By CLASS PATH, not the FName overload: GetAssetsByClass(FName) is UE_DEPRECATED(5.1) in
			// 5.3 and DELETED in 5.7 (docs/02 section 14, direction A) - same fix MifBridgeBehaviorView
			// already applies for BehaviorTree.
			Registry.GetAssetsByClass(
				FTopLevelAssetPath(TEXT("/Script/Engine"), TEXT("SkeletalMesh")), Found, true);
			for (const FAssetData& A : Found)
			{
				FMeshAssetPtr P = MakeShared<FMeshAsset>();
				P->Path = A.GetObjectPathString();
				P->Display = A.AssetName.ToString();
				Assets.Add(P);
			}
			Assets.Sort([](const FMeshAssetPtr& A, const FMeshAssetPtr& B)
				{ return A->Display < B->Display; });
		}

		TSharedRef<ITableRow> MakeAssetRow(FMeshAssetPtr In, const TSharedRef<STableViewBase>& Owner)
		{
			return SNew(STableRow<FMeshAssetPtr>, Owner)
			[
				SNew(STextBlock)
					.Text(FText::FromString(In->Display))
					.ToolTipText(FText::FromString(In->Path))
					.ColorAndOpacity(FSlateColor(TextHi))
			];
		}

		void OnAssetPicked(FMeshAssetPtr In, ESelectInfo::Type)
		{
			Sections.Reset();
			Bones.Reset();
			if (SectionStrip.IsValid()) { SectionStrip->ClearChildren(); }
			if (!In.IsValid()) { return; }

			// THE ENDPOINT'S OWN HANDLER. Same code path as an agent over HTTP - see the file header.
			TSharedRef<FJsonObject> Req = MakeShared<FJsonObject>();
			TSharedRef<FJsonObject> Res = MakeShared<FJsonObject>();
			Req->SetStringField(TEXT("path"), In->Path);
			MifBridge::H_analyze_skeletal_split(Req, Res);

			// See the file header: `error` present means failure, `ok` is never set true by the handler
			// itself when called directly like this.
			FString Err;
			const bool bFailed = Res->TryGetStringField(TEXT("error"), Err) && !Err.IsEmpty();
			if (bFailed)
			{
				Header->SetText(FText::FromString(Err));
				Verdict->SetText(FText::GetEmpty());
				if (BoneList.IsValid()) { BoneList->RequestListRefresh(); }
				return;
			}

			const TArray<TSharedPtr<FJsonValue>>* SectionsJson = nullptr;
			if (Res->TryGetArrayField(TEXT("sections"), SectionsJson) && SectionsJson)
			{
				BuildSections(*SectionsJson);
			}

			const TArray<TSharedPtr<FJsonValue>>* BonesJson = nullptr;
			if (Res->TryGetArrayField(TEXT("bones"), BonesJson) && BonesJson)
			{
				BuildBones(*BonesJson);
			}

			int32 SectionCount = 0, TotalVerts = 0, TotalTris = 0, BoneCount = 0;
			Res->TryGetNumberField(TEXT("sectionCount"), SectionCount);
			Res->TryGetNumberField(TEXT("totalVertices"), TotalVerts);
			Res->TryGetNumberField(TEXT("totalTriangles"), TotalTris);
			Res->TryGetNumberField(TEXT("boneCount"), BoneCount);
			Header->SetText(FText::FromString(FString::Printf(
				TEXT("%s   -   %d section(s), %d bone(s), %d verts, %d tris"),
				*In->Display, SectionCount, BoneCount, TotalVerts, TotalTris)));

			FString VerdictStr;
			Res->TryGetStringField(TEXT("verdict"), VerdictStr);
			FString BuildNote;
			Res->TryGetStringField(TEXT("buildNote"), BuildNote);
			bool bCooked = false;
			Res->TryGetBoolField(TEXT("cooked"), bCooked);
			Verdict->SetText(FText::FromString(FString::Printf(
				TEXT("%s   %s"), *VerdictStr, *BuildNote)));
			// Cooked meshes cannot actually be split (see the endpoint's own comment on ImportedModel) -
			// coloured to say so at a glance rather than requiring the text to be read every time.
			Verdict->SetColorAndOpacity(FSlateColor(bCooked ? Amber : TextDim));

			RebuildSectionStrip();
			if (BoneList.IsValid()) { BoneList->RequestListRefresh(); }
		}

		void BuildSections(const TArray<TSharedPtr<FJsonValue>>& Json)
		{
			for (const TSharedPtr<FJsonValue>& V : Json)
			{
				const TSharedPtr<FJsonObject>* Obj = nullptr;
				if (!V.IsValid() || !V->TryGetObject(Obj) || !Obj) { continue; }
				const TSharedPtr<FJsonObject>& O = *Obj;

				FSectionRow S;
				O->TryGetNumberField(TEXT("index"), S.Index);
				O->TryGetNumberField(TEXT("materialIndex"), S.MaterialIndex);
				O->TryGetNumberField(TEXT("vertices"), S.Vertices);
				O->TryGetNumberField(TEXT("triangles"), S.Triangles);
				O->TryGetNumberField(TEXT("boneCount"), S.BoneCount);
				Sections.Add(S);
			}
		}

		void BuildBones(const TArray<TSharedPtr<FJsonValue>>& Json)
		{
			for (const TSharedPtr<FJsonValue>& V : Json)
			{
				const TSharedPtr<FJsonObject>* Obj = nullptr;
				if (!V.IsValid() || !V->TryGetObject(Obj) || !Obj) { continue; }
				const TSharedPtr<FJsonObject>& O = *Obj;

				FBonePtr B = MakeShared<FBoneRow>();
				O->TryGetStringField(TEXT("name"), B->Name);
				O->TryGetBoolField(TEXT("influencesGeometry"), B->bInfluencesGeometry);
				const TArray<TSharedPtr<FJsonValue>>* SecArr = nullptr;
				if (O->TryGetArrayField(TEXT("sections"), SecArr) && SecArr)
				{
					for (const TSharedPtr<FJsonValue>& S : *SecArr)
					{
						int32 Idx = 0;
						if (S.IsValid() && S->TryGetNumber(Idx)) { B->Sections.Add(Idx); }
					}
				}
				Bones.Add(B);
			}
		}

		/** The top strip: one chip per SECTION, in the same colour BuildBoneRow uses for that section's
		 *  badges below - this is the "map" half. A section with a single, unshared bone reads as
		 *  splittable at a glance; one whose bones all reappear elsewhere does not. */
		void RebuildSectionStrip()
		{
			if (!SectionStrip.IsValid()) { return; }
			SectionStrip->ClearChildren();
			for (const FSectionRow& S : Sections)
			{
				SectionStrip->AddSlot()
				.Padding(2.f)
				[
					SNew(SBox)
					.Padding(FMargin(6.f, 3.f))
					[
						// BorderImage MUST be set to a solid brush - SBorder's DEFAULT BorderImage is
						// FCoreStyle's "Border", a thin FRAME rather than a fill, so BorderBackgroundColor
						// alone tints only that outline and the chip reads as the panel's own dark
						// background with a faint coloured edge. WhiteBrush is the standard flat-fill
						// brush this codebase already uses for the same reason (MifBridgeBrainmap.cpp,
						// MifBridgePanel.cpp's own Flat() helper).
						SNew(SBorder)
						.BorderImage(FAppStyle::GetBrush("WhiteBrush"))
						.BorderBackgroundColor(SectionColour(S.MaterialIndex))
						.Padding(FMargin(6.f, 3.f))
						[
							SNew(STextBlock)
								.Text(FText::FromString(FString::Printf(
									TEXT("sec %d  (mat %d)   %d bones   %d verts"),
									S.Index, S.MaterialIndex, S.BoneCount, S.Vertices)))
								.ColorAndOpacity(FSlateColor(FLinearColor::Black))
								.Font(FCoreStyle::GetDefaultFontStyle("Bold", 8))
						]
					]
				];
			}
		}

		TSharedRef<ITableRow> MakeBoneRow(FBonePtr In, const TSharedRef<STableViewBase>& Owner)
		{
			TSharedRef<SHorizontalBox> Row = SNew(SHorizontalBox)
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0.f, 0.f, 8.f, 0.f)
				[
					SNew(STextBlock)
						.Text(FText::FromString(In->Name))
						.ColorAndOpacity(FSlateColor(In->bInfluencesGeometry ? TextHi : TextDim))
						.MinDesiredWidth(160.f)
				];

			// One coloured dot per section this bone touches - the same colours the strip above uses,
			// so a bone showing two dots is visibly the same bone the strip already flagged as shared.
			for (int32 Idx : In->Sections)
			{
				// Looked up by SECTION index to find its MATERIAL index, because colour is keyed by
				// material (two sections sharing a material should read as one colour, not two).
				int32 MatIdx = Idx;
				for (const FSectionRow& S : Sections)
				{
					if (S.Index == Idx) { MatIdx = S.MaterialIndex; break; }
				}
				Row->AddSlot().AutoWidth().VAlign(VAlign_Center).Padding(1.f, 0.f)
				[
					SNew(SBox).WidthOverride(14.f).HeightOverride(14.f)
					[
						SNew(SBorder)
						.BorderImage(FAppStyle::GetBrush("WhiteBrush"))
						.BorderBackgroundColor(SectionColour(MatIdx))
						.ToolTipText(FText::FromString(FString::Printf(TEXT("section %d"), Idx)))
					]
				];
			}

			const bool bSeparable = In->Sections.Num() == 1;
			const FLinearColor StatusColour = !In->bInfluencesGeometry ? TextDim
				: bSeparable ? Green : (In->Sections.Num() > 1 ? Red : Amber);
			const FString StatusText = !In->bInfluencesGeometry ? TEXT("unused")
				: bSeparable ? TEXT("separable") : FString::Printf(TEXT("shared x%d"), In->Sections.Num());

			Row->AddSlot().FillWidth(1.f).VAlign(VAlign_Center).HAlign(HAlign_Right).Padding(8.f, 0.f, 4.f, 0.f)
			[
				SNew(STextBlock)
					.Text(FText::FromString(StatusText))
					.ColorAndOpacity(FSlateColor(StatusColour))
					.Font(FCoreStyle::GetDefaultFontStyle("Bold", 8))
			];

			return SNew(STableRow<FBonePtr>, Owner)[ Row ];
		}
	};
}

namespace MifBridge
{
	TSharedRef<SWidget> MakeSkeletalSplitWidget()
	{
		return SNew(MifSkeletalSplit::SSkeletalSplitView);
	}
}

#undef LOCTEXT_NAMESPACE
