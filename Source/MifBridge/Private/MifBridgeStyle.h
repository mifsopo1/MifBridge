// MifBridge — the shared panel palette and small style helpers, used by every in-editor view
// (MifBridgePanel.cpp and its sibling tabs: Skeletal Split, Behavior, Inherit, Brainmap, Heatmap,
// Perf). Born here 2026-08-29 out of MifBridgePanel.cpp's own MifPanel namespace, which every other
// view was quietly reinventing with its own, slightly different hex values instead of sharing this
// one - which is exactly why the non-Activity tabs read as plainer and less deliberate than Activity
// itself. ONE definition of the palette, same reasoning MifBridgeHandlers.h's own PerfTrianglesFor
// comment gives for sharing a definition between an endpoint and a panel: two that can drift apart
// are worse than one that is imperfect.
//
// Header-only, no .cpp. The palette is `inline const`, NOT `static const`, and that distinction is
// load-bearing rather than stylistic: `static` at namespace scope is INTERNAL linkage, so every
// including .cpp would get its own CardHi at its own address - while CardBrush below is `inline`,
// so exactly ONE of it survives linking. It would then compare the caller's address against
// whichever TU's palette its surviving copy was compiled against, and every call from the other TU
// would silently miss and fall back to the plain card brush. That is a real ODR violation, and the
// note that used to sit here said the opposite ("ODR is not a concern - every including .cpp gets
// its own copy of a few floats"), which is precisely the mechanism that breaks it. CardBrush now
// also dispatches by VALUE rather than by address, so a caller passing a copy or a temporary cannot
// reintroduce the same silent fallback when the remaining views are migrated here.
#pragma once

#include "Brushes/SlateRoundedBoxBrush.h"
#include "Styling/AppStyle.h"
#include "Styling/CoreStyle.h"
#include "Styling/SlateTypes.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Text/STextBlock.h"

namespace MifStyle
{
	// HEX, converted through FColor - see MifBridgePanel.cpp's original note: FLinearColor's float
	// constructor takes LINEAR values, not the sRGB ones a colour picker shows, and typing sRGB
	// straight into FLinearColor renders everything roughly twice as bright as intended.
	inline FLinearColor Hex(const TCHAR* RGB) { return FLinearColor(FColor::FromHex(RGB)); }

	inline const FLinearColor Ink        = Hex(TEXT("0B0B12"));   // window background
	inline const FLinearColor Card       = Hex(TEXT("16161F"));   // message card
	inline const FLinearColor CardHi     = Hex(TEXT("221A38"));   // newest / selected card, purple-tinted
	inline const FLinearColor HeaderTop  = Hex(TEXT("3B1E6E"));   // header
	inline const FLinearColor Purple     = Hex(TEXT("8B5CF6"));   // THE brand purple
	inline const FLinearColor PurpleSoft = Hex(TEXT("C4B5FD"));   // headings on dark
	inline const FLinearColor Steel      = Hex(TEXT("3A3F52"));   // dividers, pill chips
	inline const FLinearColor TextDim    = Hex(TEXT("7B8296"));
	inline const FLinearColor TextBody   = Hex(TEXT("E2E5EE"));
	inline const FLinearColor Read       = Hex(TEXT("4FADF5"));   // blue   - reads / neutral-good
	inline const FLinearColor Write      = Hex(TEXT("A855F7"));   // purple - mutations
	inline const FLinearColor Blocked    = Hex(TEXT("FBA53E"));   // amber  - gate refused / caution
	inline const FLinearColor Failed     = Hex(TEXT("F1666D"));   // red    - handler said no / shared
	inline const FLinearColor Live       = Hex(TEXT("4FDD8C"));   // green  - in flight / separable-clean

	// Rounded brushes. Function-local statics: FSlateRoundedBoxBrush is procedural and owns no
	// texture, so it has no resource to outlive the module - unlike a registered FSlateStyleSet,
	// which would have to be unregistered in step with DLL unload or leave dangling brush pointers.
	inline const FSlateBrush* CardBrush(const FLinearColor& C, float Radius)
	{
		if (Radius > 7.0f)
		{
			static const FSlateRoundedBoxBrush B8Card (Card,      8.0f);
			static const FSlateRoundedBoxBrush B8Hi   (CardHi,    8.0f);
			static const FSlateRoundedBoxBrush B8Head (HeaderTop, 8.0f);
			// By VALUE, not by address. Address identity additionally requires every caller to pass
			// the palette object itself and never a copy - an invisible constraint no compiler
			// checks. These palette values are distinct constants, so equality is unambiguous.
			if (C == CardHi)    { return &B8Hi; }
			if (C == HeaderTop) { return &B8Head; }
			return &B8Card;
		}
		static const FSlateRoundedBoxBrush B4Steel(Steel, 4.0f);
		return &B4Steel;
	}

	inline const FSlateBrush* Flat() { return FAppStyle::GetBrush("WhiteBrush"); }

	// A small flat-white ROUNDED brush, tintable to any colour via BorderBackgroundColor exactly like
	// Flat() - for a per-item colour swatch (a legend dot, a section-membership square) that should
	// have soft corners like everything else in this panel instead of Flat()'s hard-edged rectangle.
	inline const FSlateBrush* RoundDot()
	{
		static const FSlateRoundedBoxBrush B(FLinearColor::White, 3.0f);
		return &B;
	}

	// A list-row selection style tinted to the brand purple instead of Slate's default blue. Every
	// SListView row in this plugin's views used the engine default until 2026-08-29 - the "select a
	// mesh" highlight in Skeletal Split, for instance, looked like a completely different app's UI
	// sitting inside an otherwise purple panel. One shared style so every view's selection highlight
	// matches, rather than each view (if it ever bothers) inventing its own.
	inline const FTableRowStyle& RowStyle()
	{
		static const FTableRowStyle Style = FTableRowStyle(FAppStyle::Get().GetWidgetStyle<FTableRowStyle>("TableView.Row"))
			.SetActiveBrush(FSlateRoundedBoxBrush(FLinearColor(CardHi.R, CardHi.G, CardHi.B, 1.0f), 3.0f))
			.SetActiveHoveredBrush(FSlateRoundedBoxBrush(FLinearColor(CardHi.R, CardHi.G, CardHi.B, 1.0f), 3.0f))
			.SetInactiveBrush(FSlateRoundedBoxBrush(FLinearColor(CardHi.R, CardHi.G, CardHi.B, 0.7f), 3.0f))
			.SetInactiveHoveredBrush(FSlateRoundedBoxBrush(FLinearColor(Steel.R, Steel.G, Steel.B, 0.6f), 3.0f))
			.SetEvenRowBackgroundHoveredBrush(FSlateRoundedBoxBrush(FLinearColor(Steel.R, Steel.G, Steel.B, 0.35f), 3.0f))
			.SetOddRowBackgroundHoveredBrush(FSlateRoundedBoxBrush(FLinearColor(Steel.R, Steel.G, Steel.B, 0.35f), 3.0f))
			.SetSelectedTextColor(FSlateColor(PurpleSoft));
		return Style;
	}

	// A small rounded status pill: coloured text on a translucent-tinted dark chip. Moved here from
	// MifBridgePanel.cpp unchanged so every view gets the exact same badge language Activity already
	// established, rather than each view inventing its own (SectionColour chips, plain "unused" text,
	// and so on).
	inline TSharedRef<SWidget> Pill(const FText& Text, const FLinearColor& Colour, float FontSize = 7.f)
	{
		return SNew(SBorder)
			.BorderImage(CardBrush(Steel, 4.0f))
			.BorderBackgroundColor(FSlateColor(FLinearColor(Colour.R, Colour.G, Colour.B, 0.16f)))
			.Padding(FMargin(6, 1))
			[
				SNew(STextBlock)
					.Text(Text)
					.ColorAndOpacity(FSlateColor(Colour))
					.Font(FCoreStyle::GetDefaultFontStyle("Bold", FontSize))
			];
	}
}
