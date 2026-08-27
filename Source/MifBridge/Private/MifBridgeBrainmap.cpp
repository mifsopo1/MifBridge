// The brainmap — a zoomable, pannable dependency graph drawn in the editor.
//
// Andre: "you should be able to zoom in and out, like a true brainmap and all that, see colors based
// on what an item is etc etc".
//
// This is a custom-painted widget rather than SGraphPanel. SGraphPanel is built for Blueprint graphs:
// it wants UEdGraphNode objects, a schema, and a UEdGraph to own them, none of which exist for an
// asset dependency graph. Constructing fake UObjects to satisfy it would be more code than drawing
// several hundred circles, and would put UObject lifetime in the paint path.
//
// ============================================================================================
// ENGINE APIS, verified in BOTH trees (docs/02_GOTCHAS.md section 14).
// ============================================================================================
//
// Every drawing call is declaration-identical between 5.3.2 and 5.7 - only line numbers move:
//   FSlateDrawElement::MakeBox    5.3 DrawElementTypes.h:93   5.7 :102
//   FSlateDrawElement::MakeText   5.3 :125                    5.7 :134
//   FSlateDrawElement::MakeLines  5.3 :205 (TArray<FVector2d>) 5.7 :228
// MakeLines is called through the FVector2d overload deliberately: the FVector2f one takes its array
// BY VALUE, so every edge would copy its point array.
//
// ============================================================================================
// WHY THE LAYOUT RUNS ONCE, NOT PER FRAME.
// ============================================================================================
//
// A live force simulation is prettier and is the wrong trade here. This widget paints inside the
// editor's Slate pass, and MifBridge's whole design rests on the game thread staying responsive - the
// same reason the dependency endpoint refuses a mount root. The layout is solved when the data is
// fetched, then the paint path only transforms and draws. Zoom and pan are a matrix, not a re-solve.

#include "MifBridgeHandlers.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "Editor.h"
#include "Framework/Application/SlateApplication.h"
#include "Brushes/SlateRoundedBoxBrush.h"
#include "Rendering/DrawElements.h"
#include "Styling/AppStyle.h"
#include "Styling/CoreStyle.h"
#include "Styling/SlateIconFinder.h"
#include "Widgets/SLeafWidget.h"

#define LOCTEXT_NAMESPACE "MifBrainmap"

namespace MifBrain
{
	// Colour BY ASSET TYPE, which is the thing Andre asked to be able to see at a glance. Hex through
	// FColor, not raw FLinearColor components - the panel learned that the hard way: FLinearColor takes
	// LINEAR values, so sRGB numbers typed straight in render about twice as bright.
	static FLinearColor Hex(const TCHAR* RGB) { return FLinearColor(FColor::FromHex(RGB)); }

	static FLinearColor ColourForClass(const FString& Cls)
	{
		if (Cls.Contains(TEXT("Blueprint")))        { return Hex(TEXT("8B5CF6")); }  // purple
		if (Cls.Contains(TEXT("Material")))         { return Hex(TEXT("F0883E")); }  // orange
		if (Cls.Contains(TEXT("Texture")))          { return Hex(TEXT("4FADF5")); }  // blue
		if (Cls.Contains(TEXT("StaticMesh")))       { return Hex(TEXT("4FDD8C")); }  // green
		if (Cls.Contains(TEXT("SkeletalMesh")))     { return Hex(TEXT("35C4A8")); }  // teal
		if (Cls.Contains(TEXT("Sound")) ||
			Cls.Contains(TEXT("Audio")))            { return Hex(TEXT("F1666D")); }  // red
		if (Cls.Contains(TEXT("Anim")))             { return Hex(TEXT("E879C9")); }  // pink
		if (Cls.Contains(TEXT("Niagara")) ||
			Cls.Contains(TEXT("Particle")))         { return Hex(TEXT("FBBF24")); }  // amber
		if (Cls.Contains(TEXT("DataTable")) ||
			Cls.Contains(TEXT("Struct")) ||
			Cls.Contains(TEXT("Enum")))             { return Hex(TEXT("94A3B8")); }  // slate
		if (Cls.Contains(TEXT("World")) ||
			Cls.Contains(TEXT("Level")))            { return Hex(TEXT("C4B5FD")); }  // light purple
		return Hex(TEXT("64748B"));                                                   // unknown
	}

	// The engine's OWN icon for an asset type, found by name.
	//
	// Andre asked for "viewport icons of each item or cached images". The full answer is
	// FAssetThumbnail, which renders the actual asset - but it produces an SWidget, and this is a
	// custom-painted leaf widget with no children to host one. Hosting them means turning this into a
	// panel with a child per node, which is a real refactor and is filed rather than rushed.
	//
	// This is the version that works in a paint path: FSlateIconFinder::FindIcon takes a NAME, so
	// "ClassThumbnail.Blueprint" resolves the same icon the Content Browser uses WITHOUT touching the
	// asset. Nothing is loaded, which matters doubly on cooked content (gotchas 6c).
	//
	// FindIcon(FName) is declaration-identical in both trees (SlateIconFinder.h:64). Its sibling
	// FindIconForClass is NOT - 5.3 takes const UClass*, 5.7 takes const UStruct* - and is avoided
	// here, though a UClass* would in fact convert either way.
	static const FSlateBrush* IconForClass(const FString& Cls)
	{
		// Thumbnail first (64px, the Content Browser tile), then the small icon, then nothing.
		for (const TCHAR* Prefix : { TEXT("ClassThumbnail."), TEXT("ClassIcon.") })
		{
			const FSlateIcon Found = FSlateIconFinder::FindIcon(FName(*(FString(Prefix) + Cls)));
			if (Found.IsSet())
			{
				if (const FSlateBrush* B = Found.GetIcon()) { return B; }
			}
		}
		return nullptr;
	}

	struct FNode
	{
		FString  Package;
		FString  Name;
		FString  Class;
		FVector2D Pos = FVector2D::ZeroVector;
		FVector2D Vel = FVector2D::ZeroVector;
		float     Radius = 6.f;
		int32     Refs = 0;
		FLinearColor Colour = FLinearColor::White;
		/** The engine's own icon for this asset type, or null. Looked up BY NAME so nothing is
		 *  loaded - see IconForClass. */
		const FSlateBrush* Icon = nullptr;
	};

	struct FEdge { int32 A = 0; int32 B = 0; };
}

/**
 * The graph canvas. SLeafWidget because it owns its whole visual - there are no child widgets, and
 * every pixel is drawn in OnPaint.
 */
class SMifBrainmap : public SLeafWidget
{
public:
	SLATE_BEGIN_ARGS(SMifBrainmap) {}
	SLATE_END_ARGS()

	void Construct(const FArguments&)
	{
		SetCanTick(false);          // nothing animates; a repaint only follows input
		Rebuild(TEXT("/Game/Blueprints"));
	}

	// ------------------------------------------------------------------ data + layout
	void Rebuild(const FString& InPrefix)
	{
		Prefix = InPrefix;
		Nodes.Reset();
		Edges.Reset();
		Status = FText::GetEmpty();

		IAssetRegistry& Reg =
			FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
		if (Reg.IsLoadingAssets())
		{
			// Same refusal the endpoint makes, and for the same reason: a partial graph presented as a
			// whole one is worse than no graph.
			Status = LOCTEXT("Scanning", "the asset registry is still scanning - try again shortly");
			return;
		}

		TArray<FAssetData> Assets;
		Reg.GetAssetsByPath(FName(*Prefix), Assets, /*bRecursive*/ true);
		if (Assets.Num() == 0)
		{
			Status = FText::FromString(FString::Printf(TEXT("nothing under %s"), *Prefix));
			return;
		}

		// HARD CAP. This is the same bound the endpoint enforces and for the same reason -
		// GetReferencers runs per asset - but it matters doubly here because every node is also drawn
		// every frame. 250 is about where a force layout still reads as structure rather than fog.
		const int32 MaxNodes = 250;
		const bool bTruncated = Assets.Num() > MaxNodes;

		TMap<FName, int32> IndexOf;
		for (const FAssetData& A : Assets)
		{
			if (Nodes.Num() >= MaxNodes) { break; }
			if (IndexOf.Contains(A.PackageName)) { continue; }   // one node per PACKAGE, not per asset
			MifBrain::FNode N;
			N.Package = A.PackageName.ToString();
			N.Name = A.AssetName.ToString();
			N.Class = A.AssetClassPath.GetAssetName().ToString();
			N.Colour = MifBrain::ColourForClass(N.Class);
			N.Icon = MifBrain::IconForClass(N.Class);
			IndexOf.Add(A.PackageName, Nodes.Num());
			Nodes.Add(N);
		}

		for (int32 i = 0; i < Nodes.Num(); ++i)
		{
			TArray<FName> Deps, Refs;
			Reg.GetDependencies(FName(*Nodes[i].Package), Deps);
			Reg.GetReferencers(FName(*Nodes[i].Package), Refs);
			Nodes[i].Refs = Refs.Num();
			// SIZE IS HEAT. A package with 224 referencers is a hub and should look like one; that is
			// the whole point of a heatmap. sqrt so one enormous node does not swamp the rest.
			// SIZE IS HEAT, but the first version let it run to a 29px radius for a 224-referencer hub,
			// and at 250 nodes that merged the whole graph into one purple blob. Clamped, and much
			// flatter: the biggest node is now about three times the smallest, which still reads as
			// hierarchy without swallowing its neighbours.
			Nodes[i].Radius = FMath::Clamp(4.f + FMath::Sqrt((float)Refs.Num()) * 0.7f, 4.f, 13.f);
			for (const FName& D : Deps)
			{
				if (const int32* J = IndexOf.Find(D))
				{
					// Edges only BETWEEN drawn nodes. An edge to something off-canvas has nowhere to
					// land and would render as a line into empty space.
					if (*J != i) { Edges.Add({ i, *J }); }
				}
			}
		}

		SolveLayout();

		// Record the solved extent so the first paint can FRAME the graph. The layout area scales with
		// node count, so a fixed default zoom framed 40 nodes and left 250 mostly off-canvas - which is
		// exactly what it did. Fit is computed from the data, not guessed.
		Bounds = FBox2D(ForceInit);
		for (const MifBrain::FNode& N : Nodes) { Bounds += N.Pos; }
		bNeedsFit = true;
		Status = FText::FromString(FString::Printf(
			TEXT("%s  -  %d nodes, %d edges%s"), *Prefix, Nodes.Num(), Edges.Num(),
			bTruncated ? TEXT("   (capped at 250 of ") : TEXT("")));
		if (bTruncated)
		{
			// Truncation is always reported. A capped graph that looks complete is the defect this
			// project keeps finding.
			Status = FText::FromString(FString::Printf(
				TEXT("%s  -  %d of %d packages, %d edges  (CAPPED - narrow the path for the full picture)"),
				*Prefix, Nodes.Num(), Assets.Num(), Edges.Num()));
		}
	}

private:
	// Fruchterman-Reingold, solved ONCE. Deterministic: nodes seed on a circle by index rather than
	// randomly, so the same prefix always produces the same picture - a graph that reshuffles every
	// time it opens is impossible to build a mental model of.
	void SolveLayout()
	{
		const int32 N = Nodes.Num();
		if (N == 0) { return; }
		// Area SCALES with node count. A fixed 900 was fine for 40 nodes and far too cramped for 250 -
		// the layout was solving correctly and simply had nowhere to put anything.
		const float Area = FMath::Clamp(180.f * FMath::Sqrt((float)N), 400.f, 4000.f);
		const float K = Area / FMath::Sqrt((float)N);

		for (int32 i = 0; i < N; ++i)
		{
			const float A = (2.f * PI * i) / N;
			Nodes[i].Pos = FVector2D(FMath::Cos(A), FMath::Sin(A)) * (Area * 0.45f);
		}

		const int32 Iterations = 220;
		float Temp = Area * 0.12f;
		for (int32 Step = 0; Step < Iterations; ++Step)
		{
			for (int32 i = 0; i < N; ++i) { Nodes[i].Vel = FVector2D::ZeroVector; }

			// Repulsion, all pairs. O(n^2) at n=250 is 62k operations once - trivial, and a Barnes-Hut
			// tree here would be optimising something that runs a single time.
			for (int32 i = 0; i < N; ++i)
			{
				for (int32 j = i + 1; j < N; ++j)
				{
					FVector2D D = Nodes[i].Pos - Nodes[j].Pos;
					float Dist = FMath::Max(D.Size(), 0.01f);
					const FVector2D Push = (D / Dist) * (K * K / Dist);
					Nodes[i].Vel += Push;
					Nodes[j].Vel -= Push;
				}
			}
			// Attraction along edges.
			for (const MifBrain::FEdge& E : Edges)
			{
				FVector2D D = Nodes[E.A].Pos - Nodes[E.B].Pos;
				float Dist = FMath::Max(D.Size(), 0.01f);
				const FVector2D Pull = (D / Dist) * (Dist * Dist / K);
				Nodes[E.A].Vel -= Pull;
				Nodes[E.B].Vel += Pull;
			}
			for (int32 i = 0; i < N; ++i)
			{
				const float Speed = FMath::Max(Nodes[i].Vel.Size(), 0.01f);
				Nodes[i].Pos += (Nodes[i].Vel / Speed) * FMath::Min(Speed, Temp);
			}
			Temp *= 0.975f;   // cool, so late iterations settle rather than jitter
		}
	}

	// ------------------------------------------------------------------ view transform
	FVector2D GraphToScreen(const FVector2D& P, const FGeometry& Geo) const
	{
		return (P * Zoom) + Pan + (Geo.GetLocalSize() * 0.5f);
	}

public:
	// One oversized-radius rounded brush, shared by every node. See the call site for why one is enough.
	static const FSlateBrush* Disc()
	{
		static const FSlateRoundedBoxBrush B(FLinearColor::White, 400.f);
		return &B;
	}

	virtual FVector2D ComputeDesiredSize(float) const override { return FVector2D(600, 400); }

	virtual int32 OnPaint(const FPaintArgs& Args, const FGeometry& Geo, const FSlateRect& Clip,
						  FSlateWindowElementList& Out, int32 Layer, const FWidgetStyle& Style,
						  bool bEnabled) const override
	{
		const FSlateBrush* Fill = FAppStyle::GetBrush("WhiteBrush");

		// Framing has to happen HERE, not in Rebuild: the widget's size is not known until it is laid
		// out, and fitting to a size you do not have yet is guesswork. Mutable because OnPaint is const -
		// this is view state, not data.
		if (bNeedsFit && Nodes.Num() > 0 && Geo.GetLocalSize().X > 8.f)
		{
			const FVector2D Span = Bounds.GetSize() + FVector2D(80.f, 80.f);
			Zoom = FMath::Clamp(FMath::Min(Geo.GetLocalSize().X / FMath::Max(Span.X, 1.f),
											Geo.GetLocalSize().Y / FMath::Max(Span.Y, 1.f)), 0.05f, 2.f);
			Pan = -Bounds.GetCenter() * Zoom;
			bNeedsFit = false;
		}

		// Background.
		FSlateDrawElement::MakeBox(Out, Layer, Geo.ToPaintGeometry(), Fill, ESlateDrawEffect::None,
								   MifBrain::Hex(TEXT("0B0B12")));

		if (Nodes.Num() == 0)
		{
			FSlateDrawElement::MakeText(Out, Layer + 1,
				Geo.ToPaintGeometry(FVector2f(Geo.GetLocalSize()), FSlateLayoutTransform(FVector2f(16.f, 16.f))),
				Status.IsEmpty() ? LOCTEXT("Empty", "no graph loaded") : Status,
				FCoreStyle::GetDefaultFontStyle("Regular", 10), ESlateDrawEffect::None,
				MifBrain::Hex(TEXT("7B8296")));
			return Layer + 1;
		}

		// EDGES FIRST, so nodes sit on top of their own connections. Drawn dim: at 250 nodes the edges
		// are the majority of the ink, and at full strength they read as noise rather than structure.
		for (const MifBrain::FEdge& E : Edges)
		{
			TArray<FVector2d> Pts;
			Pts.Add(GraphToScreen(Nodes[E.A].Pos, Geo));
			Pts.Add(GraphToScreen(Nodes[E.B].Pos, Geo));
			const bool bHot = (Hovered == E.A || Hovered == E.B);
			FSlateDrawElement::MakeLines(Out, Layer + 1, Geo.ToPaintGeometry(), Pts,
				ESlateDrawEffect::None,
				// A hovered node lights up ITS edges, which is how you trace what something touches.
				bHot ? MifBrain::Hex(TEXT("8B5CF6")) : FLinearColor(1, 1, 1, 0.07f),
				true, bHot ? 1.6f : 1.0f);
		}

		// Nodes.
		for (int32 i = 0; i < Nodes.Num(); ++i)
		{
			const MifBrain::FNode& N = Nodes[i];
			const FVector2D C = GraphToScreen(N.Pos, Geo);
			const float R = FMath::Max(N.Radius * Zoom, 2.f);
			if (C.X < -R || C.Y < -R || C.X > Geo.GetLocalSize().X + R ||
				C.Y > Geo.GetLocalSize().Y + R)
			{
				continue;   // off-screen: culled, so panning stays cheap at any zoom
			}
			const bool bHot = (i == Hovered);

			// ZOOMED OUT: a coloured dot. ZOOMED IN: the engine's own type icon.
			//
			// This is what makes zoom mean something rather than just scale. Far out you are reading
			// SHAPE - clusters, hubs, how the colours group - and 250 icons at that size would be an
			// unreadable mosaic. Close in you are reading INDIVIDUAL assets, and that is where the
			// icon earns its place.
			const bool bIcons = (Zoom > 0.9f) && (N.Icon != nullptr);
			if (bIcons)
			{
				const float S = FMath::Clamp(R * 2.4f, 12.f, 64.f);
				// A tinted disc behind the icon: keeps the type COLOUR readable at icon zoom, and
				// gives a hovered node something to brighten.
				FSlateDrawElement::MakeBox(Out, Layer + 2,
					Geo.ToPaintGeometry(FVector2f(S + 6.f, S + 6.f),
						FSlateLayoutTransform(FVector2f(C - FVector2D((S + 6.f) * 0.5f, (S + 6.f) * 0.5f)))),
					// A ROUNDED brush, so the disc is a disc. The rounded-box shader clamps its corner
					// radius to half the smaller dimension, so one brush with an oversized radius reads
					// as a circle at every node size - no brush-per-size cache needed.
					Disc(), ESlateDrawEffect::None,
					bHot ? FLinearColor(N.Colour.R, N.Colour.G, N.Colour.B, 0.85f)
						 : FLinearColor(N.Colour.R, N.Colour.G, N.Colour.B, 0.30f));
				FSlateDrawElement::MakeBox(Out, Layer + 3,
					Geo.ToPaintGeometry(FVector2f(S, S),
						FSlateLayoutTransform(FVector2f(C - FVector2D(S * 0.5f, S * 0.5f)))),
					N.Icon, ESlateDrawEffect::None,
					bHot ? FLinearColor::White : FLinearColor(1, 1, 1, 0.92f));
			}
			else
			{
				FSlateDrawElement::MakeBox(Out, Layer + 2,
					Geo.ToPaintGeometry(FVector2f(R * 2.f, R * 2.f),
						FSlateLayoutTransform(FVector2f(C - FVector2D(R, R)))),
					Disc(), ESlateDrawEffect::None,
					bHot ? FLinearColor::White : N.Colour);
			}
		}

		// LABELS ONLY WHEN ZOOMED IN. This is what makes zoom meaningful rather than decorative: far
		// out you read the SHAPE - clusters and hubs - and close in you read the names. Drawing 250
		// labels at every zoom level would be an unreadable smear and would cost the most expensive
		// part of the paint.
		if (Zoom > 0.85f)
		{
			for (int32 i = 0; i < Nodes.Num(); ++i)
			{
				const MifBrain::FNode& N = Nodes[i];
				// Below the label threshold, only hubs and whatever is hovered are named.
				// Labels are the densest ink on the canvas and the first thing to become soup. Only the
				// hovered node and genuine hubs are named until the view is close enough that names can
				// actually be read side by side.
				if (i != Hovered && (Zoom < 2.0f && N.Refs < 12)) { continue; }
				const FVector2D C = GraphToScreen(N.Pos, Geo);
				if (C.X < 0 || C.Y < 0 || C.X > Geo.GetLocalSize().X || C.Y > Geo.GetLocalSize().Y)
				{
					continue;
				}
				FSlateDrawElement::MakeText(Out, Layer + 3,
					Geo.ToPaintGeometry(FVector2f(240.f, 16.f),
						FSlateLayoutTransform(FVector2f(C + FVector2D(N.Radius * Zoom + 4.f, -7.f)))),
					N.Name, FCoreStyle::GetDefaultFontStyle("Regular", 8), ESlateDrawEffect::None,
					(i == Hovered) ? FLinearColor::White : FLinearColor(1, 1, 1, 0.62f));
			}
		}

		// Status line, and the hovered node's detail. Always legible: drawn last, on its own layer.
		FText Line = Status;
		if (Nodes.IsValidIndex(Hovered))
		{
			Line = FText::FromString(FString::Printf(TEXT("%s   [%s]   %d referencers   %s"),
				*Nodes[Hovered].Name, *Nodes[Hovered].Class, Nodes[Hovered].Refs,
				*Nodes[Hovered].Package));
		}
		FSlateDrawElement::MakeBox(Out, Layer + 4,
			Geo.ToPaintGeometry(FVector2f(Geo.GetLocalSize().X, 22.f),
				FSlateLayoutTransform(FVector2f(0.f, Geo.GetLocalSize().Y - 22.f))),
			Fill, ESlateDrawEffect::None, FLinearColor(0.04f, 0.04f, 0.07f, 0.92f));
		FSlateDrawElement::MakeText(Out, Layer + 5,
			Geo.ToPaintGeometry(FVector2f(Geo.GetLocalSize().X, 18.f),
				FSlateLayoutTransform(FVector2f(8.f, Geo.GetLocalSize().Y - 18.f))),
			Line, FCoreStyle::GetDefaultFontStyle("Regular", 8), ESlateDrawEffect::None,
			MifBrain::Hex(TEXT("C4B5FD")));

		return Layer + 5;
	}

	// ------------------------------------------------------------------ input
	virtual FReply OnMouseWheel(const FGeometry& Geo, const FPointerEvent& E) override
	{
		const float Old = Zoom;
		Zoom = FMath::Clamp(Zoom * (E.GetWheelDelta() > 0 ? 1.15f : 1.f / 1.15f), 0.08f, 6.f);
		// ZOOM TOWARD THE CURSOR, not the centre. Centre-zoom means the thing you were looking at slides
		// away as you close in, and you spend the whole time panning it back.
		const FVector2D Local = Geo.AbsoluteToLocal(E.GetScreenSpacePosition());
		const FVector2D Mid = Geo.GetLocalSize() * 0.5f;
		// Derived, not fiddled. The forward transform is
		//     Screen = Graph * Zoom + Pan + Mid
		// so the graph point currently under the cursor is
		//     Graph = (Local - Mid - Pan) / OldZoom
		// and holding that point still through the zoom gives
		//     NewPan = (Local - Mid) - Graph * NewZoom
		// which simplifies to the line below. Worth writing out: the first version of this was two
		// lines of algebra that cancelled to nonsense and drifted the graph on every scroll.
		Pan = (Local - Mid) - ((Local - Mid - Pan) / Old) * Zoom;
		return FReply::Handled();
	}

	virtual FReply OnMouseButtonDown(const FGeometry& Geo, const FPointerEvent& E) override
	{
		if (E.GetEffectingButton() == EKeys::RightMouseButton ||
			E.GetEffectingButton() == EKeys::MiddleMouseButton)
		{
			bDragging = true;
			DragFrom = Geo.AbsoluteToLocal(E.GetScreenSpacePosition());
			return FReply::Handled().CaptureMouse(SharedThis(this));
		}
		if (E.GetEffectingButton() == EKeys::LeftMouseButton && Nodes.IsValidIndex(Hovered))
		{
			// Left-click reveals the asset, the same action the transcript panel's subject link takes -
			// FAssetData, never a load, because on cooked content loading is the gotchas 6c hazard.
			IAssetRegistry& Reg =
				FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
			TArray<FAssetData> Found;
			Reg.GetAssetsByPackageName(FName(*Nodes[Hovered].Package), Found);
			if (Found.Num() > 0 && GEditor)
			{
				GEditor->SyncBrowserToObjects(Found);
			}
			return FReply::Handled();
		}
		return FReply::Unhandled();
	}

	virtual FReply OnMouseButtonUp(const FGeometry&, const FPointerEvent&) override
	{
		if (bDragging)
		{
			bDragging = false;
			return FReply::Handled().ReleaseMouseCapture();
		}
		return FReply::Unhandled();
	}

	virtual FReply OnMouseMove(const FGeometry& Geo, const FPointerEvent& E) override
	{
		const FVector2D Local = Geo.AbsoluteToLocal(E.GetScreenSpacePosition());
		if (bDragging)
		{
			Pan += Local - DragFrom;
			DragFrom = Local;
			return FReply::Handled();
		}
		// Hover test in SCREEN space, so the hit area matches what is drawn at the current zoom - a
		// graph-space test would make far-out nodes impossible to hit.
		int32 Best = INDEX_NONE;
		float BestDist = TNumericLimits<float>::Max();
		for (int32 i = 0; i < Nodes.Num(); ++i)
		{
			const float R = FMath::Max(Nodes[i].Radius * Zoom, 3.f) + 3.f;
			const float D = FVector2D::Distance(GraphToScreen(Nodes[i].Pos, Geo), Local);
			if (D <= R && D < BestDist) { Best = i; BestDist = D; }
		}
		Hovered = Best;
		return FReply::Handled();
	}

	virtual FCursorReply OnCursorQuery(const FGeometry&, const FPointerEvent&) const override
	{
		return FCursorReply::Cursor(bDragging ? EMouseCursor::GrabHandClosed
							: (Nodes.IsValidIndex(Hovered) ? EMouseCursor::Hand
														   : EMouseCursor::Default));
	}

private:
	TArray<MifBrain::FNode> Nodes;
	TArray<MifBrain::FEdge> Edges;
	FString Prefix;
	FText   Status;
	mutable float   Zoom = 0.55f;
	mutable FVector2D Pan = FVector2D::ZeroVector;
	bool    bDragging = false;
	FVector2D DragFrom = FVector2D::ZeroVector;
	int32   Hovered = INDEX_NONE;
	FBox2D  Bounds = FBox2D(ForceInit);
	mutable bool bNeedsFit = false;
	mutable float ZoomStore = 0.f;
};

namespace MifBridge
{
	TSharedRef<SWidget> MakeBrainmapWidget()
	{
		return SNew(SMifBrainmap);
	}
}

#undef LOCTEXT_NAMESPACE
