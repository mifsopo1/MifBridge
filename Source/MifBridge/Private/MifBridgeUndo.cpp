// MifBridge — UNDO introspection/rollback + dirty-package flows: what did I just do, take it
// back, what is unsaved, save it all.
//
// All five endpoints operate on EDITOR-SESSION state (the transaction buffer, the dirty-package
// set) rather than on any one named asset, which is why the two package endpoints live here and
// not in MifBridgeAssetOps.cpp: that file's contract is single-asset lifecycle ops (/Game/-only
// delete/rename/duplicate through AssetTools), while these enumerate and flush the WHOLE session.
// The pairing with undo is deliberate — "what would a crash lose" and "what can I roll back" are
// the same agent question asked of two different buffers.
//
// Transaction buckets (MifBridgeCommon.cpp):
//   list_transactions, list_dirty_packages — read-only: pure queries; transacting a read pushes
//     an empty entry onto the very stack list_transactions exists to report.
//   undo_transactions, redo_transactions — SELF-MANAGED: running undo while a transaction is
//     open violates the engine's own invariant (ensure(!GIsTransacting), TransBuffer.h:74), and
//     an "undo the undo" entry is nonsense. Self-managed also makes IsCompileHeavyEndpoint true,
//     which keeps both out of batch's single open transaction for the same reason.
//   save_dirty_packages — SELF-MANAGED: saving is not undoable, and a wrapping transaction would
//     record package dirty-flag state into the undo stack (FTransaction::FPackageRecord,
//     Transactor.h:240-254).
//
// Specs: docs/audit/work/A_editor_core.md (transaction trio + save_dirty_packages, Phase-2
// verified) and docs/audit/work/B_assets_registry.md (list_dirty_packages — B owns it per the
// ratified dedup; FEditorFileUtils trio at FileHelpers.h:402/:409/:417).
#include "MifBridgeHandlers.h"
#include "UObject/UObjectHash.h"   // ForEachObjectWithPackage - save_dirty_packages tells an empty package from a failed save
#include "MifBridgeLog.h"

#include "Editor.h"                 // GEditor (Editor.h pulls in UEditorEngine)
#include "Editor/Transactor.h"      // UTransactor virtuals + FTransaction introspection getters
#include "Engine/World.h"           // UWorld::FindWorldInPackage
#include "FileHelpers.h"            // FEditorFileUtils::GetDirty*Packages / SaveLevel
#include "HAL/FileManager.h"        // IFileManager::IsReadOnly
#include "Misc/PackageName.h"
#include "Misc/PackagePath.h"
#include "Misc/Paths.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"    // FSavePackageArgs
#include "UObject/UObjectGlobals.h" // GIsSavingPackage / IsGarbageCollecting

namespace MifBridge
{
	namespace
	{
		// (JIntAnyLocal lived here until Batch D — promoted to the shared JIntAny in
		// MifBridgeHandlers.h/MifBridgeCommon.cpp when add_material_expression became the
		// second caller, exactly per the original "local until a second file needs it" note.)

		// GEditor->Trans is null under commandlets / -NoTransBuffer; every transaction endpoint
		// must say so instead of crashing or silently reporting an empty queue.
		UTransactor* TransactorOrFail(const TSharedRef<FJsonObject>& Out)
		{
			UTransactor* Trans = GEditor ? GEditor->Trans.Get() : nullptr;
			if (!Trans)
			{
				Fail(Out, TEXT("no transaction buffer available (editor running without undo?)"));
			}
			return Trans;
		}

		// queueLength/undoCount/currentIndex written by all three transaction endpoints, AFTER any
		// mutation, so "undo N is verifiable" holds: currentIndex is the queue position the NEXT
		// undo would remove (queueLength - undoCount - 1).
		void WriteQueueState(const TSharedRef<FJsonObject>& Out, UTransactor* Trans)
		{
			const int32 QueueLength = Trans->GetQueueLength();
			const int32 UndoCount = Trans->GetUndoCount();
			Out->SetNumberField(TEXT("queueLength"), QueueLength);
			Out->SetNumberField(TEXT("undoCount"), UndoCount);
			Out->SetNumberField(TEXT("currentIndex"), QueueLength - UndoCount - 1);
		}

		// Where does this dirty package live? "loose" (file on disk), "container" (only inside a
		// mounted IoStore container — permanently unsaveable), or "new" (never saved anywhere).
		// Deliberately NOT MifBridgeCooked.cpp's IsContainerOnlyPackage: that helper asks "no
		// loose file?" of packages known to exist SOMEWHERE (asset-registry hits). A dirty
		// never-saved package also has no loose file, and calling it "container" would tell the
		// caller their brand-new asset can never be saved — the opposite of the truth.
		FString DirtyPackageOrigin(const UPackage* Package)
		{
			const FPackagePath Path = FPackagePath::FromPackageNameUnchecked(Package->GetFName());
			if (FPackageName::DoesPackageExistEx(Path, FPackageName::EPackageLocationFilter::FileSystem)
				!= FPackageName::EPackageLocationFilter::None)
			{
				return TEXT("loose");
			}
			if (FPackageName::DoesPackageExistEx(Path, FPackageName::EPackageLocationFilter::IoDispatcher)
				!= FPackageName::EPackageLocationFilter::None)
			{
				return TEXT("container");
			}
			return TEXT("new");
		}

		// Mirror of UEditorEngine::UndoTransaction/RedoTransaction's own silent-false guard
		// (EditorServer.cpp:1414/:1425): if the editor is mid-save or mid-GC those calls return
		// false with no explanation. Pre-check so the caller gets a reason instead of undone=0.
		bool RefuseIfSavingOrCollecting(const TSharedRef<FJsonObject>& Out)
		{
			if (GIsSavingPackage || IsGarbageCollecting())
			{
				Fail(Out, TEXT("editor is saving a package or collecting garbage — retry when the current operation completes"));
				return true;
			}
			return false;
		}
	}

	// --- list_transactions ----------------------------------------------------
	//   in:  { limit? (count,max) = 20, offset? (start) = 0, includeObjects? (include_objects) = false }
	//   out: { queueLength, undoCount, currentIndex, canUndo, canRedo, undoBarrier, nextUndoTitle?,
	//          transactions: [{ index, id, title, context, primaryObject, recordCount,
	//                           dataSizeBytes, objects?: [paths] }] }
	// The bridge mutates constantly and had NO undo visibility — an agent could not see what its
	// mutations did to the stack, so "undo the bad one" meant a human at the keyboard. Listing is
	// newest-first (offset 0 = newest end) because rollback always asks "what did I JUST do".
	// Everything read here goes through UTransactor's UNREALED_API virtuals (Transactor.h:539-626)
	// and FTransaction's exported/inline getters — no UTransBuffer cast needed.
	void H_list_transactions(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("limit"), TEXT("count"), TEXT("max"), TEXT("offset"), TEXT("start"),
			  TEXT("includeObjects"), TEXT("include_objects") },
			TEXT("limit (aliases: count, max), offset (alias: start), includeObjects (alias: include_objects)")))
		{
			return;
		}

		UTransactor* Trans = TransactorOrFail(Out);
		if (!Trans)
		{
			return;
		}

		const int32 Limit = FMath::Max(0, JIntAny(In, { TEXT("limit"), TEXT("count"), TEXT("max") }, 20));
		const int32 Offset = FMath::Max(0, JIntAny(In, { TEXT("offset"), TEXT("start") }, 0));
		const bool bIncludeObjects = JBoolAny(In, { TEXT("includeObjects"), TEXT("include_objects") }, false);

		const int32 QueueLength = Trans->GetQueueLength();

		FText CanUndoReason, CanRedoReason;
		Out->SetBoolField(TEXT("canUndo"), Trans->CanUndo(&CanUndoReason));
		Out->SetBoolField(TEXT("canRedo"), Trans->CanRedo(&CanRedoReason));
		Out->SetNumberField(TEXT("undoBarrier"), Trans->GetCurrentUndoBarrier());
		if (Trans->CanUndo())
		{
			Out->SetStringField(TEXT("nextUndoTitle"), Trans->GetUndoContext(/*bCheckWhetherUndoPossible*/ true).Title.ToString());
		}

		// offset past the end is NOT an error — a poller draining the queue must be able to walk
		// off it and get an empty page plus the (possibly grown) queueLength back.
		TArray<TSharedPtr<FJsonValue>> Rows;
		for (int32 k = 0; k < Limit; ++k)
		{
			const int32 QueueIndex = QueueLength - 1 - Offset - k;
			if (QueueIndex < 0)
			{
				break;
			}
			const FTransaction* Tx = Trans->GetTransaction(QueueIndex);
			if (!Tx)
			{
				continue; // defensive: GetTransaction is documented null only out-of-range
			}

			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			Row->SetNumberField(TEXT("index"), QueueIndex);
			Row->SetStringField(TEXT("id"), Tx->GetId().ToString(EGuidFormats::DigitsWithHyphens));
			Row->SetStringField(TEXT("title"), Tx->GetTitle().ToString());
			const FTransactionContext Ctx = Tx->GetContext();
			Row->SetStringField(TEXT("context"), Ctx.Context);
			const UObject* Primary = Tx->GetPrimaryObject();
			Row->SetStringField(TEXT("primaryObject"), Primary ? Primary->GetPathName() : FString());
			Row->SetNumberField(TEXT("recordCount"), Tx->GetRecordCount());
			Row->SetNumberField(TEXT("dataSizeBytes"), (double)Tx->DataSize());
			if (bIncludeObjects)
			{
				TArray<UObject*> Objects;
				Tx->GetTransactionObjects(Objects);
				TArray<TSharedPtr<FJsonValue>> Paths;
				for (const UObject* Object : Objects)
				{
					if (Object)
					{
						Paths.Add(MakeShared<FJsonValueString>(Object->GetPathName()));
					}
				}
				Row->SetArrayField(TEXT("objects"), Paths);
			}
			Rows.Add(MakeShared<FJsonValueObject>(Row));
		}
		Out->SetArrayField(TEXT("transactions"), Rows);
		WriteQueueState(Out, Trans);
	}

	// --- undo_transactions ------------------------------------------------------
	//   in:  { count? (n,steps) = 1 | toIndex? (to_index) — mutually exclusive,
	//          allowRedo? (allow_redo,canRedo) = true }
	//   out: { undone, stoppedEarly, reason?, titlesUndone: [...], queueLength, undoCount, currentIndex }
	// Goes through UEditorEngine::UndoTransaction (EditorEngine.h:934) rather than raw
	// UTransactor::Undo so editor-side notification runs. Its PostUndo path re-instances actors
	// via FBlueprintCompileReinstancer::BatchReplaceInstancesOfClass (EditorServer.cpp:1406), so
	// callers must RE-RESOLVE cached object paths after an undo that touched a Blueprint.
	// Capped at 50 steps per call: each undo applies synchronously on the game thread — this
	// handler's thread — and an unbounded loop would hold the frame hostage.
	void H_undo_transactions(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("count"), TEXT("n"), TEXT("steps"), TEXT("toIndex"), TEXT("to_index"),
			  TEXT("allowRedo"), TEXT("allow_redo"), TEXT("canRedo") },
			TEXT("count (aliases: n, steps), toIndex (alias: to_index), allowRedo (aliases: allow_redo, canRedo)")))
		{
			return;
		}

		UTransactor* Trans = TransactorOrFail(Out);
		if (!Trans)
		{
			return;
		}
		// Undoing while a transaction is open violates the engine's own invariant
		// (ensure(!GIsTransacting) in UTransBuffer::BeginInternal, TransBuffer.h:74). RunEndpoint
		// never wraps this endpoint (self-managed) — hitting this means a dispatch bug upstream.
		if (GEditor->IsTransactionActive() || GIsTransacting)
		{
			Fail(Out, TEXT("transactor is active; cannot undo mid-transaction"));
			return;
		}
		if (RefuseIfSavingOrCollecting(Out))
		{
			return;
		}

		const bool bHasCount = JHasAny(In, { TEXT("count"), TEXT("n"), TEXT("steps") });
		const bool bHasToIndex = JHasAny(In, { TEXT("toIndex"), TEXT("to_index") });
		if (bHasCount && bHasToIndex)
		{
			Fail(Out, TEXT("pass either count or toIndex, not both"));
			return;
		}

		constexpr int32 MaxStepsPerCall = 50;
		int32 Steps = 0;
		bool bCappedByCall = false;
		if (bHasToIndex)
		{
			const int32 ToIndex = JIntAny(In, { TEXT("toIndex"), TEXT("to_index") }, 0);
			const int32 CurrentIndex = Trans->GetQueueLength() - Trans->GetUndoCount() - 1;
			if (ToIndex < -1)
			{
				Fail(Out, TEXT("toIndex must be >= -1 (-1 = undo everything)"));
				return;
			}
			if (ToIndex > CurrentIndex)
			{
				Fail(Out, FString::Printf(
					TEXT("toIndex %d is above currentIndex %d — those transactions are already undone; use redo_transactions"),
					ToIndex, CurrentIndex));
				return;
			}
			Steps = CurrentIndex - ToIndex;
			if (Steps > MaxStepsPerCall)
			{
				Steps = MaxStepsPerCall;
				bCappedByCall = true;
			}
		}
		else
		{
			Steps = JIntAny(In, { TEXT("count"), TEXT("n"), TEXT("steps") }, 1);
			if (Steps < 1 || Steps > MaxStepsPerCall)
			{
				Fail(Out, TEXT("count must be 1..50"));
				return;
			}
		}
		const bool bAllowRedo = JBoolAny(In, { TEXT("allowRedo"), TEXT("allow_redo"), TEXT("canRedo") }, true);

		int32 Undone = 0;
		bool bStoppedEarly = false;
		FString StopReason;
		TArray<TSharedPtr<FJsonValue>> TitlesUndone;
		for (int32 i = 0; i < Steps; ++i)
		{
			FText CanUndoReason;
			if (!Trans->CanUndo(&CanUndoReason))
			{
				bStoppedEarly = true;
				// World-affecting undo is unreliable during PIE; say so instead of parroting the
				// engine's generic refusal text.
				StopReason = (GEditor->PlayWorld != nullptr)
					? TEXT("blocked during PIE — stop_pie first")
					: (CanUndoReason.IsEmpty() ? TEXT("undo barrier reached or buffer empty") : CanUndoReason.ToString());
				break;
			}
			// Title captured BEFORE the step — afterwards the context describes the next entry.
			const FString Title = Trans->GetUndoContext(/*bCheckWhetherUndoPossible*/ true).Title.ToString();
			if (!GEditor->UndoTransaction(bAllowRedo))
			{
				bStoppedEarly = true;
				StopReason = TEXT("UndoTransaction returned false (editor entered save/GC mid-call?)");
				break;
			}
			++Undone;
			TitlesUndone.Add(MakeShared<FJsonValueString>(Title));
		}
		if (!bStoppedEarly && bCappedByCall)
		{
			bStoppedEarly = true;
			StopReason = TEXT("step cap (50 per call) reached — call again to continue toward toIndex");
		}

		// Spec: a partial undo (k > 0) is a success with stoppedEarly; ONLY a zero-progress stop
		// is an error — silence here would be the 02_GOTCHAS "never silence a mutating call" bug.
		if (Undone == 0 && bStoppedEarly)
		{
			Fail(Out, FString::Printf(TEXT("nothing undone: %s"), *StopReason));
			WriteQueueState(Out, Trans);
			return;
		}

		Out->SetNumberField(TEXT("undone"), Undone);
		Out->SetBoolField(TEXT("stoppedEarly"), bStoppedEarly);
		if (bStoppedEarly)
		{
			Out->SetStringField(TEXT("reason"), StopReason);
		}
		Out->SetArrayField(TEXT("titlesUndone"), TitlesUndone);
		WriteQueueState(Out, Trans);
	}

	// --- redo_transactions ------------------------------------------------------
	//   in:  { count? (n,steps) = 1 | toIndex? (to_index) — redo while currentIndex < toIndex }
	//   out: { redone, stoppedEarly, reason?, titlesRedone: [...], queueLength, undoCount, currentIndex }
	// UEditorEngine::RedoTransaction (EditorEngine.h:935). The redo stack is FRAGILE: any new
	// transaction wipes it (UTransBuffer::BeginInternal removes redoable entries on Begin,
	// TransBuffer.h:80-90) — so ANY bridge mutation between undo and redo kills the redo. The
	// A/B-measure loop (measure → undo → re-measure → redo) only works if the middle is reads.
	void H_redo_transactions(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("count"), TEXT("n"), TEXT("steps"), TEXT("toIndex"), TEXT("to_index") },
			TEXT("count (aliases: n, steps), toIndex (alias: to_index)")))
		{
			return;
		}

		UTransactor* Trans = TransactorOrFail(Out);
		if (!Trans)
		{
			return;
		}
		if (GEditor->IsTransactionActive() || GIsTransacting)
		{
			Fail(Out, TEXT("transactor is active; cannot redo mid-transaction"));
			return;
		}
		if (RefuseIfSavingOrCollecting(Out))
		{
			return;
		}

		const bool bHasCount = JHasAny(In, { TEXT("count"), TEXT("n"), TEXT("steps") });
		const bool bHasToIndex = JHasAny(In, { TEXT("toIndex"), TEXT("to_index") });
		if (bHasCount && bHasToIndex)
		{
			Fail(Out, TEXT("pass either count or toIndex, not both"));
			return;
		}

		constexpr int32 MaxStepsPerCall = 50;
		int32 Steps = 0;
		bool bCappedByCall = false;
		if (bHasToIndex)
		{
			const int32 ToIndex = JIntAny(In, { TEXT("toIndex"), TEXT("to_index") }, 0);
			const int32 QueueLength = Trans->GetQueueLength();
			const int32 CurrentIndex = QueueLength - Trans->GetUndoCount() - 1;
			if (ToIndex > QueueLength - 1)
			{
				Fail(Out, FString::Printf(
					TEXT("toIndex %d is beyond the end of the queue (queueLength %d)"), ToIndex, QueueLength));
				return;
			}
			if (ToIndex < CurrentIndex)
			{
				Fail(Out, FString::Printf(
					TEXT("toIndex %d is below currentIndex %d — use undo_transactions"), ToIndex, CurrentIndex));
				return;
			}
			Steps = ToIndex - CurrentIndex;
			if (Steps > MaxStepsPerCall)
			{
				Steps = MaxStepsPerCall;
				bCappedByCall = true;
			}
		}
		else
		{
			Steps = JIntAny(In, { TEXT("count"), TEXT("n"), TEXT("steps") }, 1);
			if (Steps < 1 || Steps > MaxStepsPerCall)
			{
				Fail(Out, TEXT("count must be 1..50"));
				return;
			}
		}

		int32 Redone = 0;
		bool bStoppedEarly = false;
		FString StopReason;
		TArray<TSharedPtr<FJsonValue>> TitlesRedone;
		for (int32 i = 0; i < Steps; ++i)
		{
			FText CanRedoReason;
			if (!Trans->CanRedo(&CanRedoReason))
			{
				bStoppedEarly = true;
				StopReason = CanRedoReason.IsEmpty()
					? TEXT("nothing to redo (redo stack empty, or a new transaction since the undo wiped it)")
					: CanRedoReason.ToString();
				break;
			}
			const FString Title = Trans->GetRedoContext().Title.ToString();
			if (!GEditor->RedoTransaction())
			{
				bStoppedEarly = true;
				StopReason = TEXT("RedoTransaction returned false (editor entered save/GC mid-call?)");
				break;
			}
			++Redone;
			TitlesRedone.Add(MakeShared<FJsonValueString>(Title));
		}
		if (!bStoppedEarly && bCappedByCall)
		{
			bStoppedEarly = true;
			StopReason = TEXT("step cap (50 per call) reached — call again to continue toward toIndex");
		}

		if (Redone == 0 && bStoppedEarly)
		{
			Fail(Out, FString::Printf(TEXT("nothing redone: %s"), *StopReason));
			WriteQueueState(Out, Trans);
			return;
		}

		Out->SetNumberField(TEXT("redone"), Redone);
		Out->SetBoolField(TEXT("stoppedEarly"), bStoppedEarly);
		if (bStoppedEarly)
		{
			Out->SetStringField(TEXT("reason"), StopReason);
		}
		Out->SetArrayField(TEXT("titlesRedone"), TitlesRedone);
		WriteQueueState(Out, Trans);
	}

	// --- list_dirty_packages ----------------------------------------------------
	//   in:  { kind?: "content" | "world" | "all" (default all) }
	//   out: { count, counts: { world, content }, packages: [{ name, kind, origin
	//          (loose|container|new), saveable, assetClass? }] }
	// "What would a crash lose" / "what will save_dirty_packages touch". Uses the FEditorFileUtils
	// trio the Phase-2 verdict preferred (FileHelpers.h:402/:409 — the :144 overload belongs to
	// UEditorLoadingAndSavingUtils, corrected in B_assets_registry.md) — both are plain
	// TObjectIterator scans, no GC, no dialogs. NOTE: GetDirtyWorldPackages also returns each
	// dirty world's MapBuildData package (FileHelpers.cpp:5195-5221) — reported under kind
	// "world" because that is exactly the set a maps-only save would write.
	void H_list_dirty_packages(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, { TEXT("kind") }, TEXT("kind (content|world|all)")))
		{
			return;
		}
		const FString Kind = JStr(In, TEXT("kind"), TEXT("all")).ToLower();
		if (Kind != TEXT("content") && Kind != TEXT("world") && Kind != TEXT("all"))
		{
			Fail(Out, FString::Printf(TEXT("unknown kind '%s' — use content, world, or all"), *Kind));
			return;
		}

		TArray<UPackage*> WorldPackages, ContentPackages;
		if (Kind != TEXT("content"))
		{
			FEditorFileUtils::GetDirtyWorldPackages(WorldPackages);
		}
		if (Kind != TEXT("world"))
		{
			FEditorFileUtils::GetDirtyContentPackages(ContentPackages);
		}

		TArray<TSharedPtr<FJsonValue>> Rows;
		auto EmitRow = [&Rows](UPackage* Package, const TCHAR* RowKind)
		{
			if (!Package)
			{
				return;
			}
			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			const FString Name = Package->GetName();
			Row->SetStringField(TEXT("name"), Name);
			Row->SetStringField(TEXT("kind"), RowKind);
			const FString Origin = DirtyPackageOrigin(Package);
			Row->SetStringField(TEXT("origin"), Origin);
			// A dirtied container-backed package is the red flag this endpoint exists to raise: it
			// can NEVER be saved (docs/audit/06 — cooked base-game content is save-IMPOSSIBLE).
			// An invalid long package name (/Temp/Untitled...) has no on-disk destination either.
			Row->SetBoolField(TEXT("saveable"),
				Origin != TEXT("container") && FPackageName::IsValidLongPackageName(Name, /*bIncludeReadOnlyRoots*/ false));
			if (const UObject* Asset = Package->FindAssetInPackage())
			{
				Row->SetStringField(TEXT("assetClass"), Asset->GetClass()->GetName());
			}
			Rows.Add(MakeShared<FJsonValueObject>(Row));
		};
		for (UPackage* Package : WorldPackages)
		{
			EmitRow(Package, TEXT("world"));
		}
		for (UPackage* Package : ContentPackages)
		{
			EmitRow(Package, TEXT("content"));
		}

		Out->SetNumberField(TEXT("count"), Rows.Num());
		TSharedRef<FJsonObject> Counts = MakeShared<FJsonObject>();
		Counts->SetNumberField(TEXT("world"), WorldPackages.Num());
		Counts->SetNumberField(TEXT("content"), ContentPackages.Num());
		Out->SetObjectField(TEXT("counts"), Counts);
		Out->SetArrayField(TEXT("packages"), Rows);
	}

	// --- save_dirty_packages ------------------------------------------------------
	//   in:  { maps? (saveMaps,save_maps) = true, content? (saveContent,save_content) = true,
	//          dryRun? (dry_run) = false }
	//   out: { neededSaving, dryRun, saved: [names] | wouldSave: [names],
	//          failed: [{ package, reason }], skipped: [{ package, reason }],
	//          skippedCookedOrigin: [{ package, reason }] }
	// One-call checkout-free, prompt-free "save everything". This handler deliberately does NOT
	// call FEditorFileUtils::SaveDirtyPackages — the Phase-2 verdict found three hazards in it:
	//   1. MODAL on any failed save even with bFastSave=true: the fast branch hardcodes
	//      bUseDialog=true (FileHelpers.cpp:3822-3828) and routes failures to
	//      FMessageDialog::Open (:3620-3640) — a blocking modal on the game thread, which is also
	//      the thread this HTTP server answers on: a deadlock, not a dialog.
	//   2. SILENT skips: its fast path pre-filters to packages that already exist on disk AND are
	//      writable (:3703-3746), so read-only files and never-saved packages vanish without a
	//      failure entry. Everything the engine would drop silently is ECHOED below as an
	//      explicit failed/skipped row with a reason (02_GOTCHAS: never silence a mutating call).
	//   3. Mid-frame GC: its dirty-package enumeration runs CollectGarbage whenever content is
	//      included (:3642-3647), killing any unrooted UObject held across the call.
	// Instead: enumerate via the GC-free GetDirtyWorldPackages/GetDirtyContentPackages, pre-scan
	// per package, and save each one through the plugin's proven headless paths —
	// FEditorFileUtils::SaveLevel for world packages (the save_level_as path, runs the editor's
	// pre/post-save-world hooks) and UPackage::SavePackage for everything else (the
	// save_blueprint/save_package pattern, .umap-vs-.uasset extension included).
	// FOURTH HAZARD, not in the enumeration above and not avoidable from here: for each dirty MAP,
	// FEditorFileUtils::SaveLevel -> SaveWorld opens FScopedSlowTask ... MakeDialog(true)
	// (FileHelpers.cpp:767-768). That is a progress window, not a user-blocking modal, but while it is
	// up FFeedbackContextEditor ticks Slate only and never FTSTicker, so the HTTP server is
	// unreachable for the duration of each map save. The read-only pre-check further up is what makes
	// FileHelpers.cpp:756's FMessageDialog unreachable — that one IS closed — and
	// GetDirtyWorldPackages / GetDirtyContentPackages really are GC-free as claimed.
	void H_save_dirty_packages(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("maps"), TEXT("saveMaps"), TEXT("save_maps"),
			  TEXT("content"), TEXT("saveContent"), TEXT("save_content"),
			  TEXT("dryRun"), TEXT("dry_run") },
			TEXT("maps (aliases: saveMaps, save_maps), content (aliases: saveContent, save_content), dryRun (alias: dry_run)")))
		{
			return;
		}
		const bool bMaps = JBoolAny(In, { TEXT("maps"), TEXT("saveMaps"), TEXT("save_maps") }, true);
		const bool bContent = JBoolAny(In, { TEXT("content"), TEXT("saveContent"), TEXT("save_content") }, true);
		const bool bDryRun = JBoolAny(In, { TEXT("dryRun"), TEXT("dry_run") }, false);

		if (!GEditor)
		{
			Fail(Out, TEXT("no editor"));
			return;
		}
		// Saving the editor map out from under a running PIE session is unreliable; per the spec
		// this is a hard error rather than a silent partial save.
		if (bMaps && GEditor->PlayWorld != nullptr)
		{
			Fail(Out, TEXT("cannot save map packages during PIE — stop_pie first (or pass maps=false for a content-only save)"));
			return;
		}

		TArray<UPackage*> Packages;
		if (bMaps)
		{
			FEditorFileUtils::GetDirtyWorldPackages(Packages);
		}
		if (bContent)
		{
			FEditorFileUtils::GetDirtyContentPackages(Packages);
		}

		TArray<TSharedPtr<FJsonValue>> Saved, WouldSave, Failed, Skipped, SkippedCookedOrigin, NeedsDeletion;
		auto AddReasonRow = [](TArray<TSharedPtr<FJsonValue>>& Arr, const FString& Name, const FString& Reason)
		{
			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			Row->SetStringField(TEXT("package"), Name);
			Row->SetStringField(TEXT("reason"), Reason);
			Arr.Add(MakeShared<FJsonValueObject>(Row));
		};

		for (UPackage* Package : Packages)
		{
			if (!Package)
			{
				continue;
			}
			const FString Name = Package->GetName();

			// Pre-scan 1: a dirty cooked-origin package (edited base-game asset living only in a
			// mounted container) can never be written back — report, don't fail the whole call.
			if (DirtyPackageOrigin(Package) == TEXT("container"))
			{
				AddReasonRow(SkippedCookedOrigin, Name, TEXT("cooked package — cannot be saved"));
				continue;
			}
			// Pre-scan 2: no on-disk destination (untitled map in /Temp, transient root). The
			// engine's own save would drop these with no trace — echo them instead.
			if (!FPackageName::IsValidLongPackageName(Name, /*bIncludeReadOnlyRoots*/ false))
			{
				AddReasonRow(Skipped, Name,
					TEXT("no on-disk destination (transient/untitled package) — use save_level_as for an untitled map"));
				continue;
			}
			FString Filename;
			if (!FPackageName::TryConvertLongPackageNameToFilename(Name, Filename,
				Package->ContainsMap() ? FPackageName::GetMapPackageExtension() : FPackageName::GetAssetPackageExtension()))
			{
				AddReasonRow(Skipped, Name, TEXT("cannot resolve a filename (unmounted content root)"));
				continue;
			}
			// Pre-scan 3: the engine fast path silently drops read-only files (:3703-3746); the
			// spec promises an explicit failed[] entry instead.
			if (FPaths::FileExists(Filename) && IFileManager::Get().IsReadOnly(*Filename))
			{
				AddReasonRow(Failed, Name, FString::Printf(TEXT("file is read-only: %s"), *Filename));
				continue;
			}

			if (bDryRun)
			{
				WouldSave.Add(MakeShared<FJsonValueString>(Name));
				continue;
			}

			// Pre-scan 4: A PACKAGE WITH NOTHING LEFT IN IT NEEDS DELETING, NOT SAVING. Destroying actors
			// in a One-File-Per-Actor map leaves their external-actor packages dirty and EMPTY. SavePackage
			// on an empty package fails, and this handler used to attach a guessed reason to that failure -
			// "still referenced by an in-flight operation?" - which was simply untrue. A real session hit
			// 915 of these at once and was told 915 speculative lies; the .uasset files stayed on disk and
			// World Partition would have loaded them back as ghost actors.
			//
			// Reported rather than deleted here. Deleting a package is not what an endpoint called
			// save_dirty_packages should do unasked, and the caller can now see exactly which ones need it.
			int32 LiveObjects = 0;
			ForEachObjectWithPackage(Package, [&LiveObjects](UObject* Obj)
				{
					// IsValid(), not IsPendingKillOrUnreachable(). The latter is UE_DEPRECATED(5.0) and is GONE
					// from 5.7 entirely - I wrote it here this morning and it would have broken the 5.7 build
					// that Curfew depends on. The engine's own deprecation text names IsValid(Object) as the
					// replacement, and IsValid already covers the null check, so the guard gets simpler too.
					if (IsValid(Obj))
					{
						++LiveObjects;
					}
					return LiveObjects == 0;   // stop as soon as one is found
				}, /*bIncludeNestedObjects*/ false);
			if (LiveObjects == 0)
			{
				AddReasonRow(NeedsDeletion, Name,
					TEXT("nothing left in this package to save - its object was destroyed, so it needs "
						 "DELETING rather than saving. Common on One-File-Per-Actor maps after destroying "
						 "actors. The file is still on disk and World Partition will load it back."));
				continue;
			}

			bool bSaved = false;
			if (UWorld* World = UWorld::FindWorldInPackage(Package))
			{
				// World packages go through SaveLevel so OnPreSaveWorld/OnPostSaveWorld run —
				// the same headless path save_level_as already trusts.
				bSaved = World->PersistentLevel
					&& FEditorFileUtils::SaveLevel(World->PersistentLevel, Filename);
			}
			else
			{
				FSavePackageArgs SaveArgs;
				SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
				SaveArgs.SaveFlags = SAVE_NoError;
				bSaved = UPackage::SavePackage(Package, nullptr, *Filename, SaveArgs);
			}

			if (bSaved)
			{
				Saved.Add(MakeShared<FJsonValueString>(Name));
				UE_LOG(LogMifBridge, Log, TEXT("save_dirty_packages: saved %s -> %s"), *Name, *Filename);
			}
			else
			{
				// NO GUESSING. This used to append "still referenced by an in-flight operation?", which read
				// as a diagnosis and was wrong every time it mattered. The empty-package case above is now
				// caught by name; whatever reaches here is genuinely unexplained, and saying so is more
				// useful than a plausible invention.
				AddReasonRow(Failed, Name, TEXT("SavePackage returned false and gave no reason - see the editor log for this package. It is NOT the empty-package case, which is reported separately as needsDeletion."));
			}
		}

		Out->SetBoolField(TEXT("neededSaving"), Packages.Num() > 0);
		Out->SetBoolField(TEXT("dryRun"), bDryRun);
		if (bDryRun)
		{
			Out->SetArrayField(TEXT("wouldSave"), WouldSave);
		}
		else
		{
			Out->SetArrayField(TEXT("saved"), Saved);
		}
		Out->SetArrayField(TEXT("failed"), Failed);
		Out->SetArrayField(TEXT("skipped"), Skipped);
		Out->SetArrayField(TEXT("skippedCookedOrigin"), SkippedCookedOrigin);
		Out->SetArrayField(TEXT("needsDeletion"), NeedsDeletion);
		if (NeedsDeletion.Num() > 0)
		{
			Out->SetStringField(TEXT("needsDeletionNote"), FString::Printf(
				TEXT("%d package(s) have nothing left in them and cannot be SAVED - their objects were ")
				TEXT("destroyed and the files are still on disk. They are listed separately from failed[] ")
				TEXT("because a failed save and a package awaiting deletion are different problems."),
				NeedsDeletion.Num()));
		}
	}
}
