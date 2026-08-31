// MifBridge — the SETUP tab: how to drive this thing, and how to keep the LLM's knowledge current.
//
// WHY THIS TAB EXISTS. Every other tab in this panel answers "what is happening right now". None of
// them answered "I have just installed this, what do I do", and the answer was living in a README
// on GitHub that nobody reads while the editor is open. Andre asked for it directly on 2026-08-30:
// "add things to mention to users how to properly use and keep claude or llm updated".
//
// THE SECOND HALF IS THE PART PEOPLE GET WRONG. An LLM's built-in knowledge of this plugin is
// whatever was in its training data, which is to say nothing, or worse, a guess shaped like a
// plausible endpoint name. The bridge exposes its own truth at runtime — self_audit,
// describe_endpoint, mif_help — and an agent that reads those is working from fact. One that works
// from memory invents endpoints that do not exist and parameters that are silently ignored. So this
// tab tells the USER what to tell their agent, in copyable form, rather than assuming the agent
// arrives knowing.
//
// EVERYTHING HERE IS STATIC TEXT ON PURPOSE. A setup tab that queried the bridge would be empty
// exactly when it is most needed — when the bridge is not running yet and somebody is trying to work
// out why. The live numbers already have a home: the panel header shows the write mode and endpoint
// count, and self_audit reports them to an agent. Repeating them here would be a second place to
// keep correct for no gain.

#include "MifBridgeHandlers.h"
#include "MifBridgeStyle.h"

#include "Widgets/SBoxPanel.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SSeparator.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/Text/STextBlock.h"
#include "Widgets/Input/SButton.h"
#include "HAL/PlatformApplicationMisc.h"
#include "Styling/AppStyle.h"

#define LOCTEXT_NAMESPACE "MifBridgeSetup"

namespace MifSetup
{
	using namespace MifStyle;

	/** A copyable block — the whole point of the prompt sections, so nobody retypes them. */
	class SCopyBlock : public SCompoundWidget
	{
	public:
		SLATE_BEGIN_ARGS(SCopyBlock) {}
			SLATE_ARGUMENT(FString, Body)
		SLATE_END_ARGS()

		void Construct(const FArguments& InArgs)
		{
			Body = InArgs._Body;
			ChildSlot
			[
				SNew(SBorder)
					.BorderImage(CardBrush(Ink, 4.f))
					.Padding(FMargin(10, 8))
				[
					SNew(SVerticalBox)
					+ SVerticalBox::Slot().AutoHeight()
					[
						SNew(STextBlock)
							.Text(FText::FromString(Body))
							.Font(FCoreStyle::GetDefaultFontStyle("Mono", 8))
							.ColorAndOpacity(FSlateColor(TextBody))
							.AutoWrapText(true)
					]
					+ SVerticalBox::Slot().AutoHeight().Padding(0, 6, 0, 0).HAlign(HAlign_Right)
					[
						SNew(SButton)
							.ContentPadding(FMargin(8, 2))
							.OnClicked(this, &SCopyBlock::OnCopy)
							[
								SNew(STextBlock)
									.Text_Lambda([this]()
									{
										return bCopied ? LOCTEXT("Copied", "copied")
													   : LOCTEXT("Copy", "copy");
									})
									.Font(FCoreStyle::GetDefaultFontStyle("Bold", 7))
									.ColorAndOpacity(FSlateColor(PurpleSoft))
							]
					]
				]
			];
		}

	private:
		FReply OnCopy()
		{
			FPlatformApplicationMisc::ClipboardCopy(*Body);
			bCopied = true;
			return FReply::Handled();
		}
		FString Body;
		bool bCopied = false;
	};

	TSharedRef<SWidget> Heading(const FText& Text)
	{
		return SNew(STextBlock)
			.Text(Text)
			.Font(FCoreStyle::GetDefaultFontStyle("Bold", 11))
			.ColorAndOpacity(FSlateColor(PurpleSoft));
	}

	TSharedRef<SWidget> Body(const FText& Text)
	{
		return SNew(STextBlock)
			.Text(Text)
			.Font(FCoreStyle::GetDefaultFontStyle("Regular", 8))
			.ColorAndOpacity(FSlateColor(TextBody))
			.AutoWrapText(true);
	}

	TSharedRef<SWidget> Dim(const FText& Text)
	{
		return SNew(STextBlock)
			.Text(Text)
			.Font(FCoreStyle::GetDefaultFontStyle("Regular", 7))
			.ColorAndOpacity(FSlateColor(TextDim))
			.AutoWrapText(true);
	}

	/** One numbered rule with a coloured rail, matching the activity cards. */
	TSharedRef<SWidget> Rule(const FText& Title, const FText& Text, const FLinearColor& Rail)
	{
		return SNew(SHorizontalBox)
			+ SHorizontalBox::Slot().AutoWidth().Padding(0, 0, 8, 0)
			[
				SNew(SBox).WidthOverride(3.f)
				[
					SNew(SBorder).BorderImage(CardBrush(Rail, 2.f)).Padding(0)
				]
			]
			+ SHorizontalBox::Slot().FillWidth(1.f)
			[
				SNew(SVerticalBox)
				+ SVerticalBox::Slot().AutoHeight()
				[
					SNew(STextBlock)
						.Text(Title)
						.Font(FCoreStyle::GetDefaultFontStyle("Bold", 8))
						.ColorAndOpacity(FSlateColor(Rail))
				]
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 2, 0, 0)
				[
					Body(Text)
				]
			];
	}

}

TSharedRef<SWidget> MifBridge::MakeSetupWidget()
{
	using namespace MifSetup;
	using namespace MifStyle;

	// The two prompts below are the deliverable of this tab. They are written to be pasted into an
	// agent verbatim, which is why they read as instructions to the agent rather than to the reader.
	const FString StartPrompt =
		TEXT("You are driving a live Unreal Editor through MifBridge (MCP).\n")
		TEXT("Before you do anything else:\n")
		TEXT("  1. call self_audit {summaryOnly:true} - the write mode, what will be refused,\n")
		TEXT("     and the endpoint count. Ask for the FULL form only if you need the\n")
		TEXT("     per-endpoint detail: it is ~24k tokens against ~370 for the compact one.\n")
		TEXT("  2. call describe_endpoint for any endpoint you are about to use - it reads the\n")
		TEXT("     RUNNING build, so it is the authority on what actually exists here\n")
		TEXT("Do not guess endpoint names or parameters from memory. For any endpoint you have\n")
		TEXT("not used before, call mif_help('<name>') for its traps and describe_endpoint('<name>')\n")
		TEXT("for its real accepted parameters as the running editor sees them.\n")
		TEXT("Failure is the presence of an `error` key, never the absence of `ok`.\n")
		TEXT("Never send confirm:true unless I have asked for that specific destructive action.");

	const FString RefreshPrompt =
		TEXT("MifBridge has been updated. Refresh what you know about it:\n")
		TEXT("  self_audit                 - every endpoint this build registers, plus version and\n")
		TEXT("                               write mode. {summaryOnly:true} for the counts alone\n")
		TEXT("  mif_help                   - (no argument) every tool with extended help\n")
		TEXT("Compare against what you were assuming and tell me what changed. If an endpoint you\n")
		TEXT("relied on is gone or renamed, say so before you continue.");

	TSharedRef<SScrollBox> Root = SNew(SScrollBox);

	// ---------------------------------------------------------------- what this is
	Root->AddSlot().Padding(14, 14, 14, 6)
	[
		SNew(SVerticalBox)
		+ SVerticalBox::Slot().AutoHeight()
		[
			Heading(LOCTEXT("H1", "Driving MifBridge"))
		]
		+ SVerticalBox::Slot().AutoHeight().Padding(0, 6, 0, 0)
		[
			Body(LOCTEXT("Intro",
				"MifBridge exposes this editor over HTTP on 127.0.0.1:8791 so an AI agent can read "
				"and change your project while you watch it happen. Every call it makes shows up in "
				"the ACTIVITY tab, colour-coded by whether it read, wrote, or was refused."))
		]
	];

	// ---------------------------------------------------------------- the four rules
	Root->AddSlot().Padding(14, 10, 14, 6)
	[
		SNew(SBorder)
			.BorderImage(CardBrush(MifStyle::Card, 6.f))
			.Padding(FMargin(14, 12))
		[
			SNew(SVerticalBox)
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 10)
			[
				Heading(LOCTEXT("H2", "Four things that save you a bad afternoon"))
			]
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 10)
			[
				Rule(LOCTEXT("R1T", "1.  Pick a write mode before you start, not after"),
					 LOCTEXT("R1B",
						"The MIF_BRIDGE_WRITE_MODE environment variable is read ONCE at editor "
						"start and cannot be changed from inside - deliberately, so an agent cannot "
						"unlock itself. `read` refuses every mutation. `scratch` confines writes to "
						"/Game/_Mif* and refuses saves, PIE and anything that persists to disk. "
						"`full` allows everything. Start in scratch. The current mode is shown in "
						"the header of this panel."),
					 Blocked)
			]
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 10)
			[
				Rule(LOCTEXT("R2T", "2.  Nothing is saved unless you save it"),
					 LOCTEXT("R2B",
						"Endpoints that change an asset mark its package dirty and stop there. That "
						"is on purpose: it means you can undo a session by closing without saving. "
						"If an agent tells you it 'created' something, it exists in memory - check "
						"the asset is what you wanted before you save."),
					 Write)
			]
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 10)
			[
				Rule(LOCTEXT("R3T", "3.  Cooked content can be read and changed, but not saved"),
					 LOCTEXT("R3B",
						"On a packaged/cooked project most assets live in .pak containers. The "
						"bridge will happily edit one in memory and will TELL you it is cooked - "
						"look for `cooked: true` in a response. Those edits vanish on restart. "
						"Endpoints that would crash on cooked content refuse instead, and say why."),
					 Read)
			]
			+ SVerticalBox::Slot().AutoHeight()
			[
				Rule(LOCTEXT("R4T", "4.  Read the errors - they are written for you"),
					 LOCTEXT("R4B",
						"A refusal from this plugin explains what it refused and usually what to do "
						"instead. Several of them exist because the engine call underneath would "
						"otherwise take the whole editor down with an assert. If a response says "
						"NOTHING was changed, nothing was."),
					 Failed)
			]
		]
	];

	// ---------------------------------------------------------------- starting an agent
	Root->AddSlot().Padding(14, 10, 14, 6)
	[
		SNew(SVerticalBox)
		+ SVerticalBox::Slot().AutoHeight()
		[
			Heading(LOCTEXT("H3", "Starting a session"))
		]
		+ SVerticalBox::Slot().AutoHeight().Padding(0, 6, 0, 8)
		[
			Body(LOCTEXT("StartBody",
				"Paste this at the top of a new conversation. It stops the single most common "
				"failure: an agent inventing endpoint names that sound right and do not exist."))
		]
		+ SVerticalBox::Slot().AutoHeight()
		[
			SNew(SCopyBlock).Body(StartPrompt)
		]
	];

	// ---------------------------------------------------------------- keeping it current
	Root->AddSlot().Padding(14, 12, 14, 6)
	[
		SNew(SVerticalBox)
		+ SVerticalBox::Slot().AutoHeight()
		[
			Heading(LOCTEXT("H4", "Keeping your AI's knowledge current"))
		]
		+ SVerticalBox::Slot().AutoHeight().Padding(0, 6, 0, 8)
		[
			Body(LOCTEXT("RefreshBody",
				"An LLM knows nothing about this plugin except what it is told. Its training data "
				"does not contain your build, and a model that works from memory will confidently "
				"call endpoints that were renamed or never existed. The bridge publishes its own "
				"truth at runtime - that is what these four calls are for, and they cost far less "
				"than debugging an invented parameter."))
		]
		+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 8)
		[
			SNew(SCopyBlock).Body(RefreshPrompt)
		]
		+ SVerticalBox::Slot().AutoHeight()
		[
			Dim(LOCTEXT("RefreshWhen",
				"Do this after updating the plugin, after switching engine version, and any time an "
				"agent insists an endpoint exists that the editor rejects. self_audit reports the "
				"build timestamp, so you can tell whether the agent is talking to the build you "
				"think it is."))
		]
	];

	// ---------------------------------------------------------------- the four truth sources
	Root->AddSlot().Padding(14, 10, 14, 6)
	[
		SNew(SBorder)
			.BorderImage(CardBrush(MifStyle::Card, 6.f))
			.Padding(FMargin(14, 12))
		[
			SNew(SVerticalBox)
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 8)
			[
				Heading(LOCTEXT("H5", "Where the truth lives"))
			]
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 6)
			[
				Rule(LOCTEXT("T1T", "self_audit {summaryOnly:true}"),
					 LOCTEXT("T1B", "Build timestamp, write mode, endpoint count, and what the "
									"safety gate will refuse. The first call of any session."),
					 Live)
			]
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 6)
			[
				Rule(LOCTEXT("T2T", "self_audit"),
					 LOCTEXT("T2B", "Every endpoint this build actually registers. If it is not in "
									"here, it does not exist, whatever the agent believes."),
					 Read)
			]
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 6)
			[
				Rule(LOCTEXT("T3T", "describe_endpoint('name')"),
					 LOCTEXT("T3B", "The parameters an endpoint really accepts, its aliases, and "
									"the mistakes people make with it - read from the RUNNING "
									"editor, so it is right even when documentation is not."),
					 Read)
			]
			+ SVerticalBox::Slot().AutoHeight()
			[
				Rule(LOCTEXT("T4T", "mif_help('name')"),
					 LOCTEXT("T4B", "The full write-up for an MCP tool: the traps, the engine "
									"behaviour behind it, and what it refuses. These were moved out "
									"of the tool descriptions so 450 of them would stop costing "
									"~72,000 tokens of context on every single turn - the detail is "
									"still there, it is just fetched when needed."),
					 Purple)
			]
		]
	];

	// ---------------------------------------------------------------- footer
	Root->AddSlot().Padding(14, 10, 14, 18)
	[
		Dim(LOCTEXT("Footer",
			"Safety: the bridge binds to 127.0.0.1 only and requires a token header. It is a "
			"development tool - do not expose it to a network you do not control, and do not run an "
			"agent in `full` write mode against work you have not committed to source control."))
	];

	return Root;
}

#undef LOCTEXT_NAMESPACE
