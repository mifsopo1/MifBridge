// The in-editor panel — MifBridge purple, chat-log style, live.
//
// Andre asked for this and then for it properly: "i want their FULL in engine style, like a full chat
// thing that shows recent calls, when working, color coded by work type and all kinds of stuff".
//
// The first version was a flat table of grey bars and read as a debug dump. This is the rewrite: each
// call is a rounded CARD with a coloured accent bar down its left edge, a status pill, and generous
// spacing — a transcript you can read, not a readout you have to decode.
//
// ============================================================================================
// THE CONSTRAINT THAT SHAPES EVERYTHING: THIS MUST NEVER BECOME LOAD-BEARING.
// ============================================================================================
//
// MifBridge is HEADLESS, and that is an ADVANTAGE over an in-editor plugin, not a gap — the bridge
// opens and closes the editor, survives its crashes, and runs in processes with no UI. A panel the
// server depended on would throw that away.
//
// So the dependency runs strictly one way: this READS the bridge and writes nothing back. The server
// is constructed and started in StartupModule, earlier and entirely independently, and holds no
// reference to any widget. Delete this file and the bridge is unchanged.
//
// Two guards keep it out of UI-less processes: registration happens in RegisterMenus (which runs from
// UToolMenus' startup callback and never fires without a UI), and FSlateApplication::IsInitialized()
// is checked anyway — because EHostType::Editor DOES load in commandlets, which is why StartupModule
// already carries IsRunningCommandlet() guards.
//
// ============================================================================================
// ENGINE-VERSION NOTES — verified in both trees, not assumed.
// ============================================================================================
//
// The tab-spawner chain and FSlateRoundedBoxBrush are byte-identical between 5.3.2 and 5.7, differing
// only in line number. FSlateRoundedBoxBrush's (Color, Radius, Size) constructor is at :67 in BOTH
// (Brushes/SlateRoundedBoxBrush.h). Three known deltas exist and this file avoids all three:
//   * ISlateStyle::GetVector changed return type in 5.7 — NOT USED.
//   * SDockTab gained LabelOverflowPolicy in 5.7 — NOT USED.
//   * FWorkspaceItem gained FName-keyed overloads in 5.7 — avoided by not filing the tab under a
//     workspace group, which also avoids the WorkspaceMenuStructure dependency (on 5.7 that
//     transitively drags in EditorStyle).
// FAppStyle is correct on both: FEditorStyle survives in 5.7 with accessors UE_DEPRECATED(5.1)
// forwarding to it, so there is no rename to gate.
//
// REFRESH: RegisterActiveTimer, not Tick. SWidget::Tick only fires when the widget sets
// EWidgetUpdateFlags::NeedsTick and does not keep Slate awake; an active timer does.

#include "MifBridgeHandlers.h"
#include "Styling/CoreStyle.h"
#include "Widgets/Input/SComboBox.h"
#include "MifBridge.h"
#include "MifBridgeLog.h"

#include "Editor.h"
#include "AssetRegistry/AssetRegistryModule.h"

#include "Brushes/SlateRoundedBoxBrush.h"
#include "Containers/Ticker.h"
#include "HAL/IConsoleManager.h"
#include "Framework/Application/SlateApplication.h"
#include "Framework/Docking/TabManager.h"
#include "Styling/AppStyle.h"
#include "Widgets/Docking/SDockTab.h"
#include "Widgets/Images/SImage.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/Layout/SSpacer.h"
#include "Widgets/Layout/SWidgetSwitcher.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Text/STextBlock.h"

static TAutoConsoleVariable<bool> CVarMifBridgeAutoOpen(
	TEXT("mif.BridgeAutoOpen"),
	true,
	TEXT("Open the MifBridge panel automatically a couple of seconds after the editor starts. ")
	TEXT("On by default - the panel used to be reachable only from a Tools menu entry that was easy ")
	TEXT("to miss."),
	ECVF_Default);

#define LOCTEXT_NAMESPACE "MifBridgePanel"

namespace MifPanel
{
	// ---------------------------------------------------------------- palette
	// MifBridge purple and grey. The first version tinted a white brush with a mid purple over a light
	// background and came out washed-out lavender; these are the real values, dark-first.
	// HEX, converted through FColor. The first version wrote these as raw FLinearColor components
	// picked by eye from a design palette - but those numbers were sRGB values, and FLinearColor takes
	// LINEAR ones. Slate rendered every colour roughly twice as bright as intended, which is why the
	// deep header purple came out as washed-out lavender. FLinearColor(FColor) runs the sRGB->linear
	// conversion, so these are the values you would type into a colour picker and get back on screen.
	static FLinearColor Hex(const TCHAR* RGB) { return FLinearColor(FColor::FromHex(RGB)); }

	static const FLinearColor Ink        = Hex(TEXT("0B0B12"));   // window background
	static const FLinearColor Card       = Hex(TEXT("16161F"));   // message card
	static const FLinearColor CardHi     = Hex(TEXT("221A38"));   // newest card, purple-tinted
	static const FLinearColor HeaderTop  = Hex(TEXT("3B1E6E"));   // header
	static const FLinearColor Purple     = Hex(TEXT("8B5CF6"));   // THE brand purple
	static const FLinearColor PurpleSoft = Hex(TEXT("C4B5FD"));   // headings on dark
	static const FLinearColor Steel      = Hex(TEXT("3A3F52"));   // dividers, pill chips
	static const FLinearColor TextDim    = Hex(TEXT("7B8296"));
	static const FLinearColor TextBody   = Hex(TEXT("E2E5EE"));
	static const FLinearColor Read       = Hex(TEXT("4FADF5"));   // blue   - reads
	static const FLinearColor Write      = Hex(TEXT("A855F7"));   // purple - mutations
	static const FLinearColor Blocked    = Hex(TEXT("FBA53E"));   // amber  - gate refused
	static const FLinearColor Failed     = Hex(TEXT("F1666D"));   // red    - handler said no
	static const FLinearColor Live       = Hex(TEXT("4FDD8C"));   // green  - in flight
	// Rounded brushes. Function-local statics: FSlateRoundedBoxBrush is procedural and owns no texture,
	// so it has no resource to outlive the module — unlike a registered FSlateStyleSet, which would
	// have to be unregistered in step with DLL unload or leave dangling brush pointers behind.
	static const FSlateBrush* CardBrush(const FLinearColor& C, float Radius)
	{
		// One static per (colour, radius) pair used below. Deliberately explicit rather than a map:
		// there are five, and a cache keyed on a float is more machinery than the problem deserves.
		if (Radius > 7.0f)
		{
			static const FSlateRoundedBoxBrush B8Card (Card,      8.0f);
			static const FSlateRoundedBoxBrush B8Hi   (CardHi,    8.0f);
			static const FSlateRoundedBoxBrush B8Head (HeaderTop, 8.0f);
			if (&C == &CardHi)    { return &B8Hi; }
			if (&C == &HeaderTop) { return &B8Head; }
			return &B8Card;
		}
		static const FSlateRoundedBoxBrush B4Steel(Steel, 4.0f);
		return &B4Steel;
	}

	static const FSlateBrush* Flat() { return FAppStyle::GetBrush("WhiteBrush"); }

	// ---------------------------------------------------------------- work-type colour coding
	//
	// A DISPLAY HINT, and labelled as one. This is NOT the safety gate's classification and must never
	// be mistaken for it: the gate uses IsUnsafeEndpoint (MifBridgeSafety.cpp), which is an explicit
	// audited list. This is a name-shape heuristic whose only job is to pick a colour, so being wrong
	// about an unusual name costs a slightly-off hue and nothing else.
	//
	// Deliberately NOT reusing IsReadOnlyEndpoint: that is a TRANSACTION bucket and contains
	// save_package and start_pie, so colouring by it would paint saves as harmless reads — the same
	// inversion that would have made the safety gate useless.
	static bool LooksLikeRead(const FString& Ep)
	{
		return Ep.StartsWith(TEXT("list_")) || Ep.StartsWith(TEXT("get_"))
			|| Ep.StartsWith(TEXT("describe_")) || Ep.StartsWith(TEXT("find_"))
			|| Ep.StartsWith(TEXT("read_")) || Ep.StartsWith(TEXT("self_"))
			|| Ep.StartsWith(TEXT("audit_")) || Ep.StartsWith(TEXT("capture_"))
			|| Ep.StartsWith(TEXT("trace_")) || Ep.StartsWith(TEXT("diagnose_"));
	}

	struct FKind
	{
		FLinearColor Accent;
		FText        Label;
	};

	static FKind ClassifyForDisplay(const MifBridge::FMifCallRecord& R)
	{
		if (!R.bOk)
		{
			// The gate's refusals get their own colour, because "we would not let you" and "it tried
			// and failed" are different events and reading them as one hides both.
			if (MifBridge::IsUnsafeEndpoint(R.Endpoint))
			{
				return { Blocked, LOCTEXT("KindBlocked", "BLOCKED") };
			}
			// A CONTRACT refusal is not a defect. Andre saw a wall of red FAILED cards during a
			// regression and reasonably asked whether something was wrong - and the panel could not
			// tell him, because a suite deliberately probing "does this refuse a bad argument" looked
			// exactly like a broken endpoint.
			//
			// These two prefixes are what RejectUnknownParams and the not-found paths produce, which
			// together are the overwhelming majority of intentional refusals. Anything else keeps the
			// louder FAILED colour, so a real breakage still stands out.
			if (R.Error.StartsWith(TEXT("unrecognised parameter"))
				|| R.Error.Contains(TEXT("is required"))
				|| R.Error.StartsWith(TEXT("no ")))
			{
				return { Steel, LOCTEXT("KindRefused", "REFUSED") };
			}
			return { Failed, LOCTEXT("KindFailed", "FAILED") };
		}
		return LooksLikeRead(R.Endpoint)
			? FKind{ Read,  LOCTEXT("KindRead",  "READ") }
			: FKind{ Write, LOCTEXT("KindWrite", "WRITE") };
	}
}

// A small rounded status pill: coloured text on a dark chip.
static TSharedRef<SWidget> MifPill(const FText& Text, const FLinearColor& Colour)
{
	return SNew(SBorder)
		.BorderImage(MifPanel::CardBrush(MifPanel::Steel, 4.0f))
		.BorderBackgroundColor(FSlateColor(FLinearColor(Colour.R, Colour.G, Colour.B, 0.16f)))
		.Padding(FMargin(6, 1))
		[
			SNew(STextBlock)
				.Text(Text)
				.ColorAndOpacity(FSlateColor(Colour))
				.Font(FCoreStyle::GetDefaultFontStyle("Bold", 7))
		];
}

// One labelled stat in the header strip.
static TSharedRef<SWidget> MifStat(const FText& Label, TAttribute<FText> Value,
								   TAttribute<FSlateColor> Colour)
{
	return SNew(SVerticalBox)
		+ SVerticalBox::Slot().AutoHeight()
		[
			SNew(STextBlock).Text(Label)
				.ColorAndOpacity(FSlateColor(MifPanel::TextDim))
				.Font(FCoreStyle::GetDefaultFontStyle("Regular", 7))
		]
		+ SVerticalBox::Slot().AutoHeight().Padding(0, 1, 0, 0)
		[
			SNew(STextBlock).Text(Value).ColorAndOpacity(Colour)
				.Font(FCoreStyle::GetDefaultFontStyle("Bold", 10))
		];
}

// One tab in the panel's own tab strip.
//
// Andre: "our main mifbridgfe widget should have tabs to open all of our other widgets". These are
// INTERNAL tabs over a WidgetSwitcher rather than more nomad tabs: a nomad tab per view scatters the
// tool across the editor's docking layout and makes it somebody's job to arrange them, where a strip
// keeps one MifBridge window that owns its own navigation.
static TSharedRef<SWidget> MifTabButton(const FText& Label, int32 Index,
										TAttribute<int32> Active, FSimpleDelegate OnPick)
{
	return SNew(SButton)
		.ButtonStyle(FAppStyle::Get(), "NoBorder")
		.ContentPadding(FMargin(14, 7))
		.OnClicked_Lambda([OnPick]() { OnPick.ExecuteIfBound(); return FReply::Handled(); })
		[
			SNew(SVerticalBox)
			+ SVerticalBox::Slot().AutoHeight()
			[
				SNew(STextBlock).Text(Label)
					.Font(FCoreStyle::GetDefaultFontStyle("Bold", 8))
					.ColorAndOpacity_Lambda([Active, Index]()
					{
						return FSlateColor(Active.Get() == Index ? MifPanel::PurpleSoft : MifPanel::TextDim);
					})
			]
			// The underline IS the selection indicator - a coloured label alone is too weak to find at a
			// glance, and a full button background fights the cards below it.
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 5, 0, 0)
			[
				SNew(SBox).HeightOverride(2.f)
				[
					SNew(SBorder)
						.BorderImage(MifPanel::Flat())
						.BorderBackgroundColor_Lambda([Active, Index]()
						{
							return FSlateColor(Active.Get() == Index ? MifPanel::Purple
																	 : FLinearColor::Transparent);
						})
						[ SNew(SSpacer) ]
				]
			]
		];
}

/**
 * The panel. Reads; never writes.
 *
 * Every value on screen is a TAttribute bound to a lambda, so there is no cached state to invalidate —
 * the widget cannot show something the bridge has stopped believing. The active timer only forces a
 * repaint; it never pushes data in.
 */
class SMifBridgePanel : public SCompoundWidget
{
public:
	SLATE_BEGIN_ARGS(SMifBridgePanel) {}
	SLATE_END_ARGS()

	void Construct(const FArguments&)
	{
		// 10 Hz. Fast enough that the in-flight indicator reads as live rather than as a stutter, and
		// this is also the call that keeps Slate awake so the numbers actually move.
		RegisterActiveTimer(0.1f, FWidgetActiveTimerDelegate::CreateSP(this, &SMifBridgePanel::Refresh));

		ChildSlot
		[
			SNew(SBorder)
				.BorderImage(MifPanel::Flat())
				.BorderBackgroundColor(FSlateColor(MifPanel::Ink))
				.Padding(FMargin(10))
			[
				SNew(SVerticalBox)

				// ------------------------------------------------------ header card
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 8)
				[
					SNew(SBorder)
						.BorderImage(MifPanel::CardBrush(MifPanel::HeaderTop, 8.0f))
						.BorderBackgroundColor(FSlateColor(FLinearColor::White))
						.Padding(FMargin(14, 11))
					[
						SNew(SVerticalBox)
						+ SVerticalBox::Slot().AutoHeight()
						[
							SNew(SHorizontalBox)
							+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
							[
								SNew(STextBlock)
									.Text(LOCTEXT("Brand", "MIFBRIDGE"))
									.ColorAndOpacity(FSlateColor(FLinearColor::White))
									.Font(FCoreStyle::GetDefaultFontStyle("Bold", 16))
							]
							+ SHorizontalBox::Slot().FillWidth(1.f).HAlign(HAlign_Right)
								.VAlign(VAlign_Center)
							[
								SNew(STextBlock)
									.Text(this, &SMifBridgePanel::GetEngineLine)
									.ColorAndOpacity(FSlateColor(MifPanel::PurpleSoft))
									.Font(FCoreStyle::GetDefaultFontStyle("Regular", 8))
							]
						]
						// The stat strip: the four things you actually want at a glance.
						+ SVerticalBox::Slot().AutoHeight().Padding(0, 10, 0, 0)
						[
							SNew(SHorizontalBox)
							+ SHorizontalBox::Slot().FillWidth(1.f)
							[
								MifStat(LOCTEXT("SLis", "LISTENING"),
									TAttribute<FText>(this, &SMifBridgePanel::GetListening),
									TAttribute<FSlateColor>(this, &SMifBridgePanel::GetListeningColour))
							]
							+ SHorizontalBox::Slot().FillWidth(1.f)
							[
								// The safety gate, on screen AND changeable. It used to be a read-only
								// pill; Andre asked for a dropdown so the mode is not an environment
								// variable plus a restart. See BuildWriteModeControl for why exposing
								// it does not hand an agent the key to its own gate.
								BuildWriteModeControl()
							]
							+ SHorizontalBox::Slot().FillWidth(1.f)
							[
								MifStat(LOCTEXT("SEps", "ENDPOINTS"),
									TAttribute<FText>(this, &SMifBridgePanel::GetEndpoints),
									FSlateColor(MifPanel::TextBody))
							]
							+ SHorizontalBox::Slot().FillWidth(1.f)
							[
								MifStat(LOCTEXT("SCalls", "CALLS"),
									TAttribute<FText>(this, &SMifBridgePanel::GetCalls),
									FSlateColor(MifPanel::TextBody))
							]
						]
					]
				]

				// ------------------------------------------------------ in-flight banner
				// Visible ONLY while a handler is running. Handlers execute synchronously on the game
				// thread inside the HTTP ticker, so if this sticks, that is the call wedging the
				// editor - and the panel says so while it happens, not only in the post-mortem.
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 8)
				[
					SNew(SBorder)
						.BorderImage(MifPanel::CardBrush(MifPanel::CardHi, 8.0f))
						.BorderBackgroundColor(FSlateColor(FLinearColor::White))
						.Padding(FMargin(12, 8))
						.Visibility(this, &SMifBridgePanel::GetWorkingVisibility)
					[
						SNew(SHorizontalBox)
						+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 8, 0)
						[
							SNew(STextBlock)
								.Text(this, &SMifBridgePanel::GetSpinner)
								.ColorAndOpacity(FSlateColor(MifPanel::Live))
								.Font(FCoreStyle::GetDefaultFontStyle("Bold", 11))
						]
						+ SHorizontalBox::Slot().FillWidth(1.f).VAlign(VAlign_Center)
						[
							SNew(STextBlock)
								.Text(this, &SMifBridgePanel::GetWorkingText)
								.ColorAndOpacity(FSlateColor(MifPanel::Live))
						]
					]
				]

				// ------------------------------------------------------ tab strip
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 6)
				[
					SNew(SHorizontalBox)
					+ SHorizontalBox::Slot().AutoWidth()
					[
						MifTabButton(LOCTEXT("TabActivity", "ACTIVITY"), 0,
							TAttribute<int32>::CreateSP(this, &SMifBridgePanel::GetActiveTab),
							FSimpleDelegate::CreateSP(this, &SMifBridgePanel::SetTab, 0))
					]
					+ SHorizontalBox::Slot().AutoWidth()
					[
						MifTabButton(LOCTEXT("TabBrain", "BRAINMAP"), 1,
							TAttribute<int32>::CreateSP(this, &SMifBridgePanel::GetActiveTab),
							FSimpleDelegate::CreateSP(this, &SMifBridgePanel::SetTab, 1))
					]
					+ SHorizontalBox::Slot().AutoWidth()
					[
						MifTabButton(LOCTEXT("TabHeat", "HEATMAP"), 2,
							TAttribute<int32>::CreateSP(this, &SMifBridgePanel::GetActiveTab),
							FSimpleDelegate::CreateSP(this, &SMifBridgePanel::SetTab, 2))
					]
					+ SHorizontalBox::Slot().AutoWidth()
					[
						MifTabButton(LOCTEXT("TabPerf", "PERFORMANCE"), 3,
							TAttribute<int32>::CreateSP(this, &SMifBridgePanel::GetActiveTab),
							FSimpleDelegate::CreateSP(this, &SMifBridgePanel::SetTab, 3))
					]
					+ SHorizontalBox::Slot().FillWidth(1.f)
					[
						SNew(SSpacer)
					]
				]

				// ------------------------------------------------------ the views
				+ SVerticalBox::Slot().FillHeight(1.f)
				[
					SAssignNew(Views, SWidgetSwitcher)
					+ SWidgetSwitcher::Slot()
					[
						SAssignNew(Log, SScrollBox)
					]
					+ SWidgetSwitcher::Slot()
					[
						// The brainmap is built LAZILY, on first switch. It solves a force layout over
						// every package under its prefix, and paying that on panel open - when most
						// people want the transcript - would make the whole tool feel slow.
						SAssignNew(BrainHost, SBox)
						[
							SNew(STextBlock)
								.Text(LOCTEXT("BrainLazy", "loading graph..."))
								.ColorAndOpacity(FSlateColor(MifPanel::TextDim))
						]
					]
					+ SWidgetSwitcher::Slot()
					[
						// Lazy for the same reason as the brainmap: two registry queries PER ASSET.
						SAssignNew(HeatHost, SBox)
						[
							SNew(STextBlock)
								.Text(LOCTEXT("HeatLazy", "measuring..."))
								.ColorAndOpacity(FSlateColor(MifPanel::TextDim))
						]
					]
					+ SWidgetSwitcher::Slot()
					[
						SAssignNew(PerfHost, SBox)
						[
							SNew(STextBlock)
								.Text(LOCTEXT("PerfLazy", "counting geometry..."))
								.ColorAndOpacity(FSlateColor(MifPanel::TextDim))
						]
					]
				]
			]
		];
	}

private:
	TSharedPtr<SScrollBox> Log;
	TSharedPtr<SWidgetSwitcher> Views;
	TSharedPtr<SBox> BrainHost;
	TSharedPtr<SBox> HeatHost;
	TSharedPtr<SBox> PerfHost;
	int32 ActiveTab = 0;
	bool  bBrainBuilt = false;
	bool  bHeatBuilt = false;

	int32 GetActiveTab() const { return ActiveTab; }

	void SetTab(int32 Index)
	{
		ActiveTab = Index;
		if (Views.IsValid()) { Views->SetActiveWidgetIndex(Index); }
		if (Index == 1 && !bBrainBuilt && BrainHost.IsValid())
		{
			bBrainBuilt = true;
			BrainHost->SetContent(MifBridge::MakeBrainmapWidget());
		}
		if (Index == 2 && !bHeatBuilt && HeatHost.IsValid())
		{
			bHeatBuilt = true;
			HeatHost->SetContent(MifBridge::MakeHeatmapWidget());
		}
		// REBUILT EVERY TIME, not once. The first version cached these on first switch, and Andre
		// opened IslaSombra to find the panel still describing Untitled_1 - a census of a level that
		// was no longer loaded, presented as current. A stale performance number is worse than none,
		// because it gets acted on.
		//
		// The brainmap and heatmap stay lazy-but-cached: they describe ASSETS under a path prefix,
		// which do not change when the world does. The perf view describes the LOADED LEVEL, which is
		// exactly what changed.
		if (Index == 3 && PerfHost.IsValid())
		{
			PerfHost->SetContent(MifBridge::MakePerfWidget());
		}
	}
	int64 LastCount = -1;
	int32 SpinnerFrame = 0;
	// Which endpoints have been flagged since this panel opened, so the icon can show it took. Not
	// persisted: the report file on disk is the durable record, this is only feedback.
	TSet<FString> FlaggedThisSession;

	// Clicking an asset subject SYNCS THE CONTENT BROWSER rather than opening the asset editor.
	// Opening an editor is heavier, pulls in an asset the reader may not have wanted loaded, and on
	// COOKED content that is exactly what docs/02_GOTCHAS.md section 6c warns about. Revealing it is
	// what somebody reading a transcript actually wants.
	FReply OnOpenSubject(FString Subject)
	{
		if (Subject.IsEmpty() || !GEditor) { return FReply::Handled(); }

		// FAssetData, not UObject*. SyncBrowserToObjects takes either, but the UObject* form would mean
		// LOADING the asset just to reveal it - and on cooked content that is the operation
		// docs/02_GOTCHAS.md section 6c records as fatal. The registry knows where it is without opening
		// it.
		//
		// FSoftObjectPath overload, never the FName one: GetAssetByObjectPath(FName) is the same
		// deprecated shape as GetAssetsByClass(FName) - present in 5.3, and the trap that section 14 is
		// about. Verified: FSoftObjectPath overload at 5.3 IAssetRegistry.h:289, 5.7 :423.
		FAssetRegistryModule& Module =
			FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
		const FAssetData Data =
			Module.Get().GetAssetByObjectPath(FSoftObjectPath(Subject));
		if (!Data.IsValid())
		{
			// The subject was a path-shaped string that is not an asset - a class path, or an asset that
			// has since been deleted. Doing nothing is right; guessing is not.
			return FReply::Handled();
		}
		TArray<FAssetData> Sync;
		Sync.Add(Data);
		GEditor->SyncBrowserToObjects(Sync);
		return FReply::Handled();
	}

	FReply OnFlag(FString Endpoint, bool bWasOk, double Ms)
	{
		// The panel does not know the original payload - the ring holds what a transcript needs, not a
		// full request log - so the report carries an empty payload and says so. A human or the loop
		// fills it in when reproducing. Claiming a payload we do not have would be worse than omitting it.
		const FString Actual = bWasOk
			? FString::Printf(TEXT("returned ok:true in %.0f ms, but the result was wrong"), Ms)
			: FString::Printf(TEXT("returned ok:false after %.0f ms"), Ms);
		FString Path;
		if (MifBridge::WriteLocalReport(Endpoint, FString(),  Actual,
				TEXT("flagged from the MifBridge editor panel; payload not captured - fill it in when reproducing"),
				Path))
		{
			FlaggedThisSession.Add(Endpoint);
			Rebuild();
		}
		return FReply::Handled();
	}

	EActiveTimerReturnType Refresh(double, float)
	{
		SpinnerFrame = (SpinnerFrame + 1) % 8;
		// Rebuild ONLY when the call count moved. Slate would happily rebuild ten times a second, but
		// that churns widgets for nothing and yanks the scroll position out from under anyone reading.
		const int64 Total = MifBridge::GetTotalCallCount();
		if (Total != LastCount)
		{
			LastCount = Total;
			Rebuild();
		}
		return EActiveTimerReturnType::Continue;
	}

	void Rebuild()
	{
		if (!Log.IsValid()) { return; }
		Log->ClearChildren();

		TArray<MifBridge::FMifCallRecord> Recent;
		MifBridge::GetRecentCalls(Recent, 50);

		if (Recent.Num() == 0)
		{
			Log->AddSlot().Padding(4, 6)
			[
				SNew(STextBlock)
					.Text(LOCTEXT("Idle", "waiting for the first call"))
					.ColorAndOpacity(FSlateColor(MifPanel::TextDim))
			];
			return;
		}

		for (int32 i = 0; i < Recent.Num(); ++i)
		{
			const MifBridge::FMifCallRecord& R = Recent[i];
			const MifPanel::FKind Kind = MifPanel::ClassifyForDisplay(R);

			Log->AddSlot().Padding(0, 0, 6, 5)
			[
				SNew(SHorizontalBox)

				// The accent bar. Colour IS the work type - it reads at a glance and costs no width,
				// which a text label would.
				+ SHorizontalBox::Slot().AutoWidth()
				[
					SNew(SBox).WidthOverride(3.f)
					[
						SNew(SBorder)
							.BorderImage(MifPanel::CardBrush(MifPanel::Steel, 4.f))
							.BorderBackgroundColor(FSlateColor(Kind.Accent))
							[ SNew(SSpacer) ]
					]
				]

				+ SHorizontalBox::Slot().FillWidth(1.f)
				[
					SNew(SBorder)
						.BorderImage(MifPanel::CardBrush(i == 0 ? MifPanel::CardHi : MifPanel::Card, 8.f))
						.BorderBackgroundColor(FSlateColor(FLinearColor::White))
						.Padding(FMargin(11, 7))
					[
						SNew(SVerticalBox)
						+ SVerticalBox::Slot().AutoHeight()
						[
							SNew(SHorizontalBox)
							+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
								.Padding(0, 0, 7, 0)
							[
								MifPill(Kind.Label, Kind.Accent)
							]
							+ SHorizontalBox::Slot().FillWidth(1.f).VAlign(VAlign_Center)
							[
								SNew(STextBlock)
									.Text(FText::FromString(R.Endpoint))
									.ColorAndOpacity(FSlateColor(i == 0 ? MifPanel::PurpleSoft
																		: MifPanel::TextBody))
									.Font(FCoreStyle::GetDefaultFontStyle("Bold", 9))
							]
							+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
							[
								SNew(STextBlock)
									// Slow calls colour themselves. 250ms is roughly where a call stops
									// feeling instant in the transcript.
									.Text(FText::FromString(FString::Printf(TEXT("%.0f ms"),
										R.Milliseconds)))
									.ColorAndOpacity(FSlateColor(R.Milliseconds > 250.0
										? MifPanel::Blocked : MifPanel::TextDim))
									.Font(FCoreStyle::GetDefaultFontStyle("Regular", 8))
							]
							+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
								.Padding(9, 0, 0, 0)
							[
								// BOUND, not baked. The first version formatted the age into a fixed
								// string here, and Rebuild() only runs when a NEW call arrives - so
								// every row's clock froze at whatever it said when it was created and
								// read "now" indefinitely. Andre caught it within minutes. The age is
								// the one value on a card that changes without any new data, so it has
								// to recompute on each paint; the timestamp is captured by value and
								// the lambda does the arithmetic every tick.
								SNew(STextBlock)
									.Text_Lambda([When = R.WhenSeconds]()
									{
										const double A = FPlatformTime::Seconds() - When;
										return FText::FromString(
											A < 1.0   ? FString(TEXT("now")) :
											A < 90.0  ? FString::Printf(TEXT("%.0fs"), A) :
											A < 5400.0 ? FString::Printf(TEXT("%.0fm"), A / 60.0)
													   : FString::Printf(TEXT("%.1fh"), A / 3600.0));
									})
									.ColorAndOpacity(FSlateColor(MifPanel::TextDim))
									.Font(FCoreStyle::GetDefaultFontStyle("Regular", 8))
							]

							// FLAG. One click files a structured report into Saved/MifBridge/reports/,
							// in the shape report_intake.parse_report already validates, for the
							// autonomous loop in docs/12 to reproduce and fix. Writing a file is all it
							// does - a report is DATA, never an instruction, and that rule does not
							// relax just because the reporter is local.
							+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
								.Padding(8, 0, 0, 0)
							[
								SNew(SButton)
									.ButtonStyle(FAppStyle::Get(), "NoBorder")
									.ContentPadding(FMargin(4, 0))
									.ToolTipText(LOCTEXT("FlagTip",
										"Flag this call as wrong. Writes a bug report to "
										"Saved/MifBridge/reports/ for the autonomous loop to pick up, "
										"reproduce and fix. Nothing is sent anywhere by itself."))
									.OnClicked(this, &SMifBridgePanel::OnFlag, R.Endpoint, R.bOk,
											   R.Milliseconds)
									[
										// A REAL brush, not a glyph. U+2691 (the flag character) is not in the
										// editor's font and rendered as an empty box - the second time on
										// this panel that an exotic code point did not survive contact with
										// the font. Icons.Warning is core Slate style, present in both
										// trees (StarshipCoreStyle.cpp:311 in 5.3, same name in 5.7).
										SNew(SImage)
											.Image(FAppStyle::GetBrush("Icons.Warning"))
											.ColorAndOpacity(FSlateColor(
												FlaggedThisSession.Contains(R.Endpoint)
													? MifPanel::Blocked : MifPanel::Steel))
									]
							]
						]

						// SECOND LINE: what the call was ABOUT. "find_assets" on its own says nothing;
						// "find_assets  /Game/FX/NS_Fire" says what happened. Rendered as a clickable
						// link when the subject is an asset path, so the transcript becomes a way INTO
						// the project rather than only a record of it.
						+ SVerticalBox::Slot().AutoHeight().Padding(0, 3, 0, 0)
						[
							SNew(SHorizontalBox)
								.Visibility((R.Subject.IsEmpty() && R.Error.IsEmpty())
									? EVisibility::Collapsed : EVisibility::Visible)
							+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
							[
								SNew(SButton)
									.ButtonStyle(FAppStyle::Get(), "NoBorder")
									.ContentPadding(FMargin(0))
									// Only assets are clickable. A class name or an actor label has
									// nowhere to go, and a link that does nothing is worse than text.
									.IsEnabled(R.bSubjectIsAsset && R.Error.IsEmpty())
									.ToolTipText(R.bSubjectIsAsset
										? LOCTEXT("OpenTip", "Show this asset in the Content Browser")
										: FText::GetEmpty())
									.OnClicked(this, &SMifBridgePanel::OnOpenSubject, R.Subject)
									[
										SNew(STextBlock)
											// The REASON when there is one, the subject otherwise. On a
											// failed call the reason is what you want; on a successful
											// one there is no reason and the subject is.
											.Text(FText::FromString(
												R.Error.IsEmpty() ? R.Subject : R.Error))
											.ColorAndOpacity(FSlateColor(
												!R.Error.IsEmpty() ? MifPanel::TextDim
												: (R.bSubjectIsAsset ? MifPanel::Read
																	 : MifPanel::TextDim)))
											.Font(FCoreStyle::GetDefaultFontStyle(
												R.bSubjectIsAsset ? "Bold" : "Regular", 8))
									]
							]
						]
					]
				]
			];
		}
	}

	// ------------------------------------------------------------------ bound getters
	FText GetEngineLine() const
	{
		return FText::FromString(FString::Printf(TEXT("UE %d.%d"),
			ENGINE_MAJOR_VERSION, ENGINE_MINOR_VERSION));
	}

	FText GetEndpoints() const { return FText::AsNumber(MifBridge::EndpointCount()); }
	FText GetCalls() const     { return FText::AsNumber(MifBridge::GetTotalCallCount()); }

	const FMifBridgeModule* Mod() const
	{
		return FModuleManager::GetModulePtr<FMifBridgeModule>(TEXT("MifBridge"));
	}

	FText GetListening() const
	{
		const FMifBridgeModule* M = Mod();
		return (M && M->IsRunning())
			? FText::FromString(FString::Printf(TEXT(":%d"), M->GetPort()))
			: LOCTEXT("Stopped", "stopped");
	}

	FSlateColor GetListeningColour() const
	{
		const FMifBridgeModule* M = Mod();
		return FSlateColor((M && M->IsRunning()) ? MifPanel::Live : MifPanel::Failed);
	}

	// --- the write-mode control -----------------------------------------------------------------
	//
	// Andre: "make it a drop down toggleable". Before this the mode was MIF_BRIDGE_WRITE_MODE plus a
	// restart, and that cost two people an evening each - the variable is read ONCE at startup, so a
	// value set in a shell dies with the shell, setx does not touch a running editor, and the mode is
	// cached for the process lifetime. Three traps stacked, and all three look identical from here:
	// "it still says scratch".
	//
	// WHY THIS IS SAFE TO EXPOSE, which is not obvious and was worth designing rather than assuming.
	//
	// The rule the gate rests on is that an agent must not be able to unlock it. That rule is about
	// the BRIDGE, not about the human at the keyboard - and this control is only reachable by a human
	// at the keyboard, for three independent reasons:
	//
	//   1. It is a PLAIN SLATE WIDGET with a direct lambda. It is deliberately NOT an FUICommandInfo
	//      and NOT a UToolMenus entry, because invoke_editor_command executes exactly those. Anything
	//      registered in a command namespace is addressable by name over the bridge; this is not.
	//   2. send_editor_key is now on the unsafe list, so an agent in scratch mode cannot deliver
	//      keystrokes at all and therefore cannot drive a focused combo box with arrow keys.
	//   3. SetWriteModeFromPanel refuses to RAISE the mode while any bridge call is on the stack. That
	//      closes the case nobody enumerates: an endpoint that pumps Slate - a slow-task dialog, say -
	//      dispatching a click into this control while its own call is still running.
	//
	// The third is the one that does not depend on anyone having listed the routes correctly, which
	// matters because that enumeration has been wrong three times in one night.
	//
	// LOWERING is always permitted, including mid-call. Making the gate stricter is never the
	// dangerous direction, and someone reaching for 'scratch' during an operation is trying to stop
	// something.
	TSharedRef<SWidget> BuildWriteModeControl()
	{
		ModeOptions.Empty();
		ModeOptions.Add(MakeShared<FString>(TEXT("read")));
		ModeOptions.Add(MakeShared<FString>(TEXT("scratch")));
		ModeOptions.Add(MakeShared<FString>(TEXT("full")));

		return SNew(SVerticalBox)
			+ SVerticalBox::Slot().AutoHeight()
			[
				SNew(STextBlock)
					.Text(LOCTEXT("SModeLbl", "WRITE MODE"))
					.ColorAndOpacity(FSlateColor(MifPanel::TextDim))
					.Font(FCoreStyle::GetDefaultFontStyle("Bold", 7))
			]
			+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 2.f, 0.f, 0.f)
			[
				SAssignNew(ModeCombo, SComboBox<TSharedPtr<FString>>)
					.OptionsSource(&ModeOptions)
					.OnGenerateWidget_Lambda([](TSharedPtr<FString> In)
					{
						return SNew(STextBlock).Text(FText::FromString(In.IsValid() ? *In : FString()));
					})
					.OnSelectionChanged(this, &SMifBridgePanel::OnWriteModePicked)
					.ToolTipText(LOCTEXT("SModeTip",
						"Change the safety gate without restarting.\n\n"
						"scratch (default) - assets and graphs can be edited in memory, but saving, "
						"PIE, console execution and level loading are refused.\n"
						"full - nothing is refused.\n"
						"read - as scratch today; the per-endpoint read/write split is unfinished.\n\n"
						"This control is in the EDITOR and is not reachable over the bridge - an agent "
						"cannot unlock its own gate. Raising the mode is refused while a bridge call is "
						"mid-flight; lowering it is always allowed."))
					[
						SNew(STextBlock)
							.Text(this, &SMifBridgePanel::GetWriteMode)
							.ColorAndOpacity(this, &SMifBridgePanel::GetWriteModeColour)
							.Font(FCoreStyle::GetDefaultFontStyle("Bold", 9))
					]
			];
	}

	void OnWriteModePicked(TSharedPtr<FString> Picked, ESelectInfo::Type Info)
	{
		// Direct means "a human clicked or used the keyboard in this widget". A programmatic
		// SetSelectedItem arrives as ESelectInfo::Direct too, which is why the depth guard below is
		// the real control rather than this check - but ignoring OnMouseClick-less notifications
		// still avoids acting on a rebuild setting the initial selection.
		if (!Picked.IsValid() || Info == ESelectInfo::Direct)
		{
			return;
		}
		MifBridge::EMifWriteMode Wanted = MifBridge::EMifWriteMode::Scratch;
		if (*Picked == TEXT("full")) { Wanted = MifBridge::EMifWriteMode::Full; }
		else if (*Picked == TEXT("read")) { Wanted = MifBridge::EMifWriteMode::Read; }

		FString Refusal;
		if (!MifBridge::SetWriteModeFromPanel(Wanted, Refusal))
		{
			ModeRefusal = Refusal;
			return;
		}
		ModeRefusal.Reset();
	}

	FText GetModeRefusal() const { return FText::FromString(ModeRefusal); }
	EVisibility GetModeRefusalVisibility() const
	{
		return ModeRefusal.IsEmpty() ? EVisibility::Collapsed : EVisibility::Visible;
	}

	TArray<TSharedPtr<FString>> ModeOptions;
	TSharedPtr<SComboBox<TSharedPtr<FString>>> ModeCombo;
	FString ModeRefusal;

	FText GetWriteMode() const
	{
		return FText::FromString(MifBridge::WriteModeName(MifBridge::GetWriteMode()));
	}

	FSlateColor GetWriteModeColour() const
	{
		// 'full' is the UNGATED mode. Amber, because an unlocked bridge must never look the same as a
		// safe one.
		return FSlateColor(MifBridge::GetWriteMode() == MifBridge::EMifWriteMode::Full
			? MifPanel::Blocked : MifPanel::Live);
	}

	EVisibility GetWorkingVisibility() const
	{
		FString Ep; double Secs = 0.0;
		return MifBridge::GetInFlight(Ep, Secs) ? EVisibility::Visible : EVisibility::Collapsed;
	}

	FText GetSpinner() const
	{
		static const int32 Cp[8] = { 0x2802, 0x2806, 0x2807, 0x280F,
									 0x2839, 0x2838, 0x2830, 0x2820 };
		return FText::FromString(FString::Chr(Cp[SpinnerFrame]));
	}

	FText GetWorkingText() const
	{
		FString Ep; double Secs = 0.0;
		if (!MifBridge::GetInFlight(Ep, Secs)) { return FText::GetEmpty(); }
		return FText::FromString(Secs < 1.0
			? FString::Printf(TEXT("running  %s"), *Ep)
			: FString::Printf(TEXT("running  %s   %.1fs"), *Ep, Secs));
	}
};

namespace MifBridge
{
	const FName BridgePanelTabName("MifBridgePanel");
	const FName BrainmapTabName("MifBridgeBrainmap");

	void RegisterPanel()
	{
		// The guard that keeps a UI out of a UI-less process. EHostType::Editor loads in commandlets
		// too, so GIsEditor alone is not enough.
		if (!FSlateApplication::IsInitialized() || IsRunningCommandlet())
		{
			return;
		}

		FGlobalTabmanager::Get()->RegisterNomadTabSpawner(BridgePanelTabName,
			FOnSpawnTab::CreateLambda([](const FSpawnTabArgs&) -> TSharedRef<SDockTab>
			{
				return SNew(SDockTab).TabRole(ETabRole::NomadTab)
					[
						SNew(SMifBridgePanel)
					];
			}))
			.SetDisplayName(LOCTEXT("PanelTitle", "Mif Bridge"))
			.SetTooltipText(LOCTEXT("PanelTooltip",
				"Live transcript of the MifBridge HTTP bridge: port, safety-gate mode, and recent "
				"calls colour-coded by work type. Read-only - the bridge does not depend on this "
				"panel and runs headless without it."))
			// VISIBLE, not Hidden. It WAS Hidden, and Andre could not find it in his other editor - which
			// is exactly what Hidden means: no Window-menu entry, so the only way in was a Tools menu item
			// he did not know about. A tool nobody can find is a tool that does not exist.
			.SetMenuType(ETabSpawnerMenuType::Enabled);


		// The brainmap is a SECOND tab rather than a page inside the first. They answer different
		// questions - one is 'what is the bridge doing right now', the other is 'how is this project
		// wired' - and a docked tab each means both can be open at once, which a tabbed pane would
		// prevent.
		FGlobalTabmanager::Get()->RegisterNomadTabSpawner(BrainmapTabName,
			FOnSpawnTab::CreateLambda([](const FSpawnTabArgs&) -> TSharedRef<SDockTab>
			{
				return SNew(SDockTab).TabRole(ETabRole::NomadTab)
					[
						MifBridge::MakeBrainmapWidget()
					];
			}))
			.SetDisplayName(LOCTEXT("BrainTitle", "Mif Brainmap"))
			.SetTooltipText(LOCTEXT("BrainTip",
				"The project dependency graph: zoom with the wheel, drag with right or middle mouse, "
				"click a node to reveal it in the Content Browser. Colour is asset type, size is how "
				"many things reference it."))
			// VISIBLE, not Hidden. It WAS Hidden, and Andre could not find it in his other editor - which
			// is exactly what Hidden means: no Window-menu entry, so the only way in was a Tools menu item
			// he did not know about. A tool nobody can find is a tool that does not exist.
			.SetMenuType(ETabSpawnerMenuType::Enabled);
	}

	// CONSOLE COMMANDS: the path that does not depend on finding a menu.
	//
	// Andre, on his second editor: "whats the command or how to open the mifbridge gui incase it"
	// "doesnt auto open ? i still never saw it". Both existing routes had already failed him -
	// the tab spawner was registered Hidden at first (no Window-menu entry at all), and when auto-open
	// was added it did not fire on that editor either. Two routes, both invisible, and no way to ask.
	//
	// A console command is the right floor because it needs no discovery: it is typeable, it appears in
	// the console's autocomplete under "Mif.", and it works identically on every engine and every
	// project regardless of what the Window menu looks like or whether a saved layout swallowed the tab.
	// Registered here rather than as a bridge endpoint on purpose - when the panel is what you cannot
	// find, the bridge may well be the thing you are trying to check on.
	static FAutoConsoleCommand GMifOpenPanelCmd(
		TEXT("Mif.Panel"),
		TEXT("Open the MifBridge panel (live call transcript, brainmap, heatmap, performance)."),
		FConsoleCommandDelegate::CreateStatic(&MifBridge::OpenPanel));

	static FAutoConsoleCommand GMifOpenBrainmapCmd(
		TEXT("Mif.Brainmap"),
		TEXT("Open the MifBridge project dependency graph in its own tab."),
		FConsoleCommandDelegate::CreateStatic(&MifBridge::OpenBrainmap));
	void UnregisterPanel()
	{
		if (FSlateApplication::IsInitialized())
		{
			FGlobalTabmanager::Get()->UnregisterNomadTabSpawner(BridgePanelTabName);
			FGlobalTabmanager::Get()->UnregisterNomadTabSpawner(BrainmapTabName);
		}
	}

	// AUTO-OPEN, once, shortly after startup.
	//
	// Andre: "can you also make it auto open the window for mifbridge, i dont know how to open it in
	// my other editor". A one-shot ticker rather than opening straight from RegisterMenus: the editor
	// restores its saved layout AFTER module startup, and a tab invoked before that can be closed again
	// by the restore. Two seconds clears it without being a visible delay.
	//
	// TryInvokeTab focuses the tab if the restored layout already contains it, so this is not a fight
	// with the user's layout - it is a floor. mif.BridgeAutoOpen turns it off.
	void ScheduleAutoOpen()
	{
		static bool bScheduled = false;
		if (bScheduled || !FSlateApplication::IsInitialized() || IsRunningCommandlet()) { return; }
		bScheduled = true;

		// LOGGED, and it RETRIES. The first version fired once at 2s and left no trace, so when it did
		// not appear there was no way to tell whether the timer never ran, ran too early, or ran and
		// was undone by the layout restore. Now it says what it did, and it keeps trying for a few
		// seconds because the moment the tab manager is ready is not something to guess at.
		FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateLambda([](float) -> bool
		{
			static int32 Attempts = 0;
			++Attempts;

			if (!CVarMifBridgeAutoOpen.GetValueOnAnyThread())
			{
				UE_LOG(LogMifBridge, Log, TEXT("auto-open disabled by mif.BridgeAutoOpen"));
				return false;
			}
			if (!FSlateApplication::IsInitialized())
			{
				return Attempts < 12;
			}

			const TSharedPtr<SDockTab> Tab =
				FGlobalTabmanager::Get()->TryInvokeTab(BridgePanelTabName);
			if (Tab.IsValid())
			{
				UE_LOG(LogMifBridge, Log,
					TEXT("auto-opened the MifBridge panel (attempt %d). Turn this off with "
						 "mif.BridgeAutoOpen 0; the tab also lives under Window and under "
						 "Tools > Mif Bridge: Live Panel."), Attempts);
				return false;
			}
			// Not ready yet. Twelve attempts at 1s is twelve seconds, which is longer than any editor
			// start observed here and still bounded - a ticker that never returns false is a leak.
			if (Attempts >= 12)
			{
				UE_LOG(LogMifBridge, Warning,
					TEXT("could not auto-open the MifBridge panel after %d attempts. Open it from "
						 "Window, or Tools > Mif Bridge: Live Panel."), Attempts);
				return false;
			}
			return true;
		}), 1.0f);
	}

	void OpenPanel()
	{
		if (FSlateApplication::IsInitialized())
		{
			FGlobalTabmanager::Get()->TryInvokeTab(BridgePanelTabName);
		}
	}

	void OpenBrainmap()
	{
		if (FSlateApplication::IsInitialized())
		{
			FGlobalTabmanager::Get()->TryInvokeTab(BrainmapTabName);
		}
	}
}

#undef LOCTEXT_NAMESPACE
