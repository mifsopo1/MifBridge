// MifBridge — the BEHAVIOR tab: a Behavior Tree's structure, and its blackboard, as a real tree.
//
// The third of Andre's in-editor asks. DDS2 ships 17 behavior trees and the editor's own BT graph is
// the only way to look at one, which means opening each asset in turn.
//
// IT CALLS THE ENDPOINT'S HANDLER, not a copy of its logic.
//
// H_describe_behavior_tree already walks the tree and produces exactly the JSON this view needs, so
// the view builds a request object, calls the handler, and reads the response - the same bytes an
// agent over HTTP would get. That is deliberate rather than lazy:
//
//   * One implementation. A second walk of UBehaviorTree would drift from the first, and the two
//     would disagree about a project's AI while both looking authoritative.
//   * The view is a live test of the endpoint. If the panel renders it, an agent can read it.
//   * Anything the endpoint learns to report, this shows for free.
//
// The cost is a JSON round trip in memory, which against loading and walking a behavior tree asset is
// not measurable.
//
// THIS ONE LOADS. Unlike the inheritance tab, which reads registry tags and touches nothing, describing
// a behavior tree requires the actual UBehaviorTree - the node structure is not published as metadata.
// So it loads ONE asset, when a person clicks it, and never speculatively.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "Dom/JsonObject.h"
#include "Misc/PackageName.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SSearchBox.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SSplitter.h"
#include "Widgets/Text/STextBlock.h"
#include "Widgets/Views/SListView.h"
#include "Widgets/Views/STreeView.h"
#include "Widgets/SBoxPanel.h"

#define LOCTEXT_NAMESPACE "MifBridgeBehavior"

namespace MifBehavior
{
	// sRGB hex through FColor::FromHex - see the note in MifBridgeInheritView.cpp about why the float
	// constructor renders everything twice as bright.
	static const FLinearColor Purple  = FLinearColor(FColor::FromHex(TEXT("A78BFA")));
	static const FLinearColor TextDim = FLinearColor(FColor::FromHex(TEXT("9CA3AF")));
	static const FLinearColor TextHi  = FLinearColor(FColor::FromHex(TEXT("E5E7EB")));

	/** Colour by KIND, because kind is what tells you how a node behaves. A composite routes, a task
	 *  acts, a decorator gates, a service ticks - and mixing them up is how a tree gets misread. */
	static FLinearColor KindColour(const FString& Kind)
	{
		if (Kind == TEXT("root"))      { return FLinearColor(FColor::FromHex(TEXT("F59E0B"))); }
		if (Kind == TEXT("composite")) { return FLinearColor(FColor::FromHex(TEXT("A78BFA"))); }
		if (Kind == TEXT("task"))      { return FLinearColor(FColor::FromHex(TEXT("34D399"))); }
		if (Kind == TEXT("decorator")) { return FLinearColor(FColor::FromHex(TEXT("60A5FA"))); }
		if (Kind == TEXT("service"))   { return FLinearColor(FColor::FromHex(TEXT("F472B6"))); }
		return TextDim;
	}

	struct FBtNode
	{
		FString Name, Class, Kind;
		int32 Decorators = 0;
		int32 Services = 0;
		TArray<TSharedPtr<FBtNode>> Children;
	};
	using FBtPtr = TSharedPtr<FBtNode>;

	struct FBtAsset
	{
		FString Path, Display;
	};
	using FBtAssetPtr = TSharedPtr<FBtAsset>;

	class SBehaviorView : public SCompoundWidget
	{
	public:
		SLATE_BEGIN_ARGS(SBehaviorView) {}
		SLATE_END_ARGS()

		void Construct(const FArguments&)
		{
			RefreshAssetList();

			ChildSlot
			[
				SNew(SSplitter)
				+ SSplitter::Slot().Value(0.34f)
				[
					SNew(SVerticalBox)
					+ SVerticalBox::Slot().AutoHeight().Padding(6.f)
					[
						SNew(STextBlock)
							.Text(FText::Format(LOCTEXT("Count", "{0} behavior trees"),
								FText::AsNumber(Assets.Num())))
							.ColorAndOpacity(FSlateColor(TextDim))
					]
					+ SVerticalBox::Slot().FillHeight(1.f)
					[
						SAssignNew(AssetList, SListView<FBtAssetPtr>)
							.ListItemsSource(&Assets)
							.OnGenerateRow(this, &SBehaviorView::MakeAssetRow)
							.OnSelectionChanged(this, &SBehaviorView::OnAssetPicked)
							.SelectionMode(ESelectionMode::Single)
					]
				]
				+ SSplitter::Slot().Value(0.66f)
				[
					SNew(SVerticalBox)
					+ SVerticalBox::Slot().AutoHeight().Padding(8.f, 6.f)
					[
						SAssignNew(Header, STextBlock)
							.Text(LOCTEXT("Pick", "pick a behavior tree on the left"))
							.ColorAndOpacity(FSlateColor(TextDim))
					]
					+ SVerticalBox::Slot().FillHeight(1.f).Padding(4.f)
					[
						SAssignNew(Tree, STreeView<FBtPtr>)
							.TreeItemsSource(&Roots)
							.OnGenerateRow(this, &SBehaviorView::MakeNodeRow)
							.OnGetChildren(this, &SBehaviorView::GetChildren)
							.SelectionMode(ESelectionMode::Single)
					]
					+ SVerticalBox::Slot().AutoHeight().Padding(8.f, 4.f, 8.f, 8.f)
					[
						SAssignNew(Blackboard, STextBlock)
							.ColorAndOpacity(FSlateColor(TextDim))
							.AutoWrapText(true)
					]
				]
			];
		}

	private:
		TArray<FBtAssetPtr> Assets;
		TArray<FBtPtr> Roots;
		TSharedPtr<SListView<FBtAssetPtr>> AssetList;
		TSharedPtr<STreeView<FBtPtr>> Tree;
		TSharedPtr<STextBlock> Header;
		TSharedPtr<STextBlock> Blackboard;

		void RefreshAssetList()
		{
			Assets.Reset();
			IAssetRegistry& Registry =
				FModuleManager::LoadModuleChecked<FAssetRegistryModule>("AssetRegistry").Get();
			TArray<FAssetData> Found;
			// By CLASS PATH, not the FName overload: GetAssetsByClass(FName) is UE_DEPRECATED(5.1) in
			// 5.3 and DELETED in 5.7 (docs/02 section 14, direction A).
			Registry.GetAssetsByClass(
				FTopLevelAssetPath(TEXT("/Script/AIModule"), TEXT("BehaviorTree")), Found, true);
			for (const FAssetData& A : Found)
			{
				FBtAssetPtr P = MakeShared<FBtAsset>();
				P->Path = A.GetObjectPathString();
				P->Display = A.AssetName.ToString();
				Assets.Add(P);
			}
			Assets.Sort([](const FBtAssetPtr& A, const FBtAssetPtr& B)
				{ return A->Display < B->Display; });
		}

		TSharedRef<ITableRow> MakeAssetRow(FBtAssetPtr In, const TSharedRef<STableViewBase>& Owner)
		{
			return SNew(STableRow<FBtAssetPtr>, Owner)
			[
				SNew(STextBlock)
					.Text(FText::FromString(In->Display))
					.ToolTipText(FText::FromString(In->Path))
					.ColorAndOpacity(FSlateColor(TextHi))
			];
		}

		void OnAssetPicked(FBtAssetPtr In, ESelectInfo::Type)
		{
			Roots.Reset();
			if (!In.IsValid()) { return; }

			// THE ENDPOINT'S OWN HANDLER. Same code path as an agent over HTTP - see the file header.
			TSharedRef<FJsonObject> Req = MakeShared<FJsonObject>();
			TSharedRef<FJsonObject> Res = MakeShared<FJsonObject>();
			Req->SetStringField(TEXT("path"), In->Path);
			MifBridge::H_describe_behavior_tree(Req, Res);

			// FAILURE IS THE PRESENCE OF `error`, NOT THE ABSENCE OF `ok`.
			//
			// This read `ok` first and every tree came back "could not describe this behavior tree"
			// while the same call over HTTP returned a full answer. The handler never sets `ok:true` -
			// RunEndpoint does, at MifBridgeCommon.cpp:1214, AFTER the handler returns. Only Fail()
			// touches `ok`, and only to set it false.
			//
			// So a handler called DIRECTLY, as this view does, succeeds by leaving `ok` unset. Testing
			// for it inverts every result: total success reads as total failure.
			//
			// Anything else calling a handler outside RunEndpoint has to know this. It is the price of
			// reusing the endpoint instead of duplicating its logic, and still much cheaper than two
			// implementations that disagree about a project's AI.
			FString Err;
			const bool bFailed = Res->TryGetStringField(TEXT("error"), Err) && !Err.IsEmpty();
			if (bFailed)
			{
				// The endpoint's refusal, verbatim. Rewording it here would mean two explanations of
				// the same failure that drift apart.
				Header->SetText(FText::FromString(Err));
				if (Tree.IsValid()) { Tree->RequestTreeRefresh(); }
				return;
			}

			const TArray<TSharedPtr<FJsonValue>>* Nodes = nullptr;
			if (Res->TryGetArrayField(TEXT("nodes"), Nodes) && Nodes)
			{
				BuildFromDepths(*Nodes);
			}

			int32 NodeCount = 0;
			Res->TryGetNumberField(TEXT("nodeCount"), NodeCount);
			FString Bb;
			Res->TryGetStringField(TEXT("blackboard"), Bb);
			Header->SetText(FText::FromString(FString::Printf(
				TEXT("%s   -   %d nodes"), *In->Display, NodeCount)));

			DescribeBlackboard(Bb);

			if (Tree.IsValid())
			{
				Tree->RequestTreeRefresh();
				for (const FBtPtr& R : Roots) { ExpandAll(R); }
			}
		}

		/** The endpoint returns a FLAT list carrying a `depth`, which is a tree in disguise. Rebuild
		 *  the parent links from it with a stack: a node at depth D is a child of the last node seen at
		 *  depth D-1.
		 *
		 *  Defensive about depth JUMPS. A well-formed walk never skips a level, but this is parsing a
		 *  response rather than trusting an invariant, and a jump would otherwise index past the end of
		 *  the stack. Anything unattachable becomes a root instead - visible and wrong beats invisible
		 *  and wrong. */
		void BuildFromDepths(const TArray<TSharedPtr<FJsonValue>>& Nodes)
		{
			TArray<FBtPtr> Stack;
			for (const TSharedPtr<FJsonValue>& V : Nodes)
			{
				const TSharedPtr<FJsonObject>* Obj = nullptr;
				if (!V.IsValid() || !V->TryGetObject(Obj) || !Obj) { continue; }
				const TSharedPtr<FJsonObject>& O = *Obj;

				FBtPtr N = MakeShared<FBtNode>();
				O->TryGetStringField(TEXT("name"), N->Name);
				O->TryGetStringField(TEXT("class"), N->Class);
				O->TryGetStringField(TEXT("kind"), N->Kind);
				O->TryGetNumberField(TEXT("decorators"), N->Decorators);
				O->TryGetNumberField(TEXT("services"), N->Services);

				int32 Depth = 0;
				O->TryGetNumberField(TEXT("depth"), Depth);
				if (Depth < 0) { Depth = 0; }

				if (Depth == 0 || Depth > Stack.Num())
				{
					Roots.Add(N);
					Stack.SetNum(0);
					Stack.Add(N);
				}
				else
				{
					Stack.SetNum(Depth);
					Stack.Last()->Children.Add(N);
					Stack.Add(N);
				}
			}
		}

		void DescribeBlackboard(const FString& BbPath)
		{
			if (!Blackboard.IsValid()) { return; }
			if (BbPath.IsEmpty())
			{
				// Not a formatting nicety. A behavior tree with no blackboard cannot read or write any
				// key, so every Blackboard-based decorator in it is inert - worth saying outright.
				Blackboard->SetText(LOCTEXT("NoBb",
					"no blackboard asset - every blackboard decorator in this tree is inert"));
				return;
			}
			TSharedRef<FJsonObject> Req = MakeShared<FJsonObject>();
			TSharedRef<FJsonObject> Res = MakeShared<FJsonObject>();
			Req->SetStringField(TEXT("path"), BbPath);
			MifBridge::H_list_blackboard_keys(Req, Res);

			const TArray<TSharedPtr<FJsonValue>>* Keys = nullptr;
			TArray<FString> Names;
			if (Res->TryGetArrayField(TEXT("keys"), Keys) && Keys)
			{
				for (const TSharedPtr<FJsonValue>& V : *Keys)
				{
					const TSharedPtr<FJsonObject>* O = nullptr;
					FString Name;
					if (V.IsValid() && V->TryGetObject(O) && O && (*O)->TryGetStringField(TEXT("name"), Name))
					{
						Names.Add(Name);
					}
				}
			}
			Blackboard->SetText(FText::FromString(FString::Printf(
				TEXT("blackboard: %s   -   %d keys: %s"),
				*FPackageName::ObjectPathToObjectName(BbPath), Names.Num(),
				*FString::Join(Names, TEXT(", ")))));
		}

		void ExpandAll(const FBtPtr& N)
		{
			if (!Tree.IsValid()) { return; }
			Tree->SetItemExpansion(N, true);
			for (const FBtPtr& C : N->Children) { ExpandAll(C); }
		}

		void GetChildren(FBtPtr In, TArray<FBtPtr>& Out) { Out = In->Children; }

		TSharedRef<ITableRow> MakeNodeRow(FBtPtr In, const TSharedRef<STableViewBase>& Owner)
		{
			// Decorators and services are counts on the node rather than rows of their own, because
			// they are not children - they ATTACH to a node and gate or tick it. Rendering them as
			// children would draw a tree the AI does not actually have.
			FString Attach;
			if (In->Decorators > 0) { Attach += FString::Printf(TEXT("  %dd"), In->Decorators); }
			if (In->Services > 0)   { Attach += FString::Printf(TEXT("  %ds"), In->Services); }

			return SNew(STableRow<FBtPtr>, Owner)
			[
				SNew(SHorizontalBox)
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0.f, 0.f, 6.f, 0.f)
				[
					SNew(STextBlock)
						.Text(FText::FromString(In->Kind.IsEmpty() ? TEXT("?") : In->Kind.Left(4).ToUpper()))
						.ColorAndOpacity(FSlateColor(KindColour(In->Kind)))
						.Font(FCoreStyle::GetDefaultFontStyle("Bold", 7))
						.MinDesiredWidth(34.f)
				]
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
				[
					SNew(STextBlock)
						.Text(FText::FromString(In->Name))
						.ColorAndOpacity(FSlateColor(TextHi))
						.ToolTipText(FText::FromString(In->Class))
				]
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
				[
					SNew(STextBlock)
						.Text(FText::FromString(Attach))
						.ColorAndOpacity(FSlateColor(Purple))
						.ToolTipText(LOCTEXT("AttachTip",
							"decorators and services attached to this node - they gate or tick it, "
							"they are not its children"))
				]
			];
		}
	};
}

namespace MifBridge
{
	TSharedRef<SWidget> MakeBehaviorWidget()
	{
		return SNew(MifBehavior::SBehaviorView);
	}
}

#undef LOCTEXT_NAMESPACE
