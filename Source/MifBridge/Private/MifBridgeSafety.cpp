// The safety gate — making the standing rules structural instead of honoured.
//
// WHAT WAS WRONG. Until this file, "do not save assets, do not start PIE, keep scratch under
// /Game/_Mif*" was enforced by the AGENT's discipline plus tools/scratch_confirm.py on the Python side.
// Nothing in the C++ would refuse a save_package call. That is the one place in this design that
// depended on good behaviour rather than enforcing it: a different agent session, or somebody else
// running the bridge, is subject to no guard at all.
//
// ============================================================================================
// THE TRAP THAT MAKES THIS FILE NECESSARY, rather than a one-line reuse of what already exists.
// ============================================================================================
//
// MifBridge already classifies endpoints — IsReadOnlyEndpoint (MifBridgeCommon.cpp:486),
// IsSelfManagedEndpoint (:623), IsCompileHeavyEndpoint (:1078). It is extremely tempting to write:
//
//     if (!IsReadOnlyEndpoint(Endpoint)) { refuse; }
//
// THAT IS BACKWARDS AND WOULD BE WORSE THAN NO GATE AT ALL. Those buckets are about TRANSACTION
// policy — "does this need an FScopedTransaction" — not about mutation. The read-only set contains:
//
//     save_package, save_blueprint   (MifBridgeCommon.cpp:489)
//     trigger_cook                   (:492)
//     start_pie, stop_pie            (:559)
//     compile, validate, run_console (:567)
//     build_navmesh                  (:598)
//
// They are in that set because they manage their own transactions, not because they are harmless. A
// gate written against IsReadOnlyEndpoint would PERMIT every save and every PIE start while refusing
// harmless transacted edits — a safety feature that protects nothing and blocks everything. So this is
// a third, independent classification that shares no data with the other three.
//
// ============================================================================================
// SCOPE OF THIS FILE, stated plainly so the gap is not mistaken for coverage.
// ============================================================================================
//
// This is the UNSAFE-OPERATION half only. It refuses the operations that cannot be made safe by
// inspecting a path — the ones that persist to disk, escape the process, or take the editor loop.
//
// It does NOT yet enforce the scratch-path rule ("writes must target /Game/_Mif*"). That needs a
// per-endpoint Read/Write classification across all 285 binds plus a payload traversal, and both are
// filed as follow-up work. A partial path check would be worse than none: it would read as coverage.
//
// ============================================================================================
// WHY THE MODE IS AN ENVIRONMENT VARIABLE AND NOT A CVAR OR AN ENDPOINT.
// ============================================================================================
//
// set_cvar is a registered endpoint (bound at MifBridgeCommon.cpp:401). A mode stored in a console
// variable would be unlockable by the very agent being gated — the gate would be decorative. The same
// argument rules out a set_write_mode endpoint. The mode is therefore read ONCE from the environment
// at process start and is immutable for the life of the editor. To change it you restart with a
// different environment, which is a deliberate act outside the bridge's own reach.
//
// The mode IS reported through self_audit, because a caller needs to know why it was refused.

#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"
#include <atomic>   // the mode is now runtime-mutable; see GetWriteMode

#include "HAL/PlatformMisc.h"

namespace MifBridge
{
	namespace
	{
		// Operations that no path check can make safe. Each one either writes to disk outside our
		// control, hands control to something we cannot supervise, or takes the editor loop.
		//
		// This list is INDEPENDENT of the transaction buckets by design — see the header comment. It is
		// deliberately a literal set rather than derived from anything, because deriving it from an
		// existing classification is exactly the mistake this file exists to avoid.
		const TSet<FString>& UnsafeEndpoints()
		{
			static const TSet<FString> Unsafe = {
				// Persist to disk. The standing rule on this project is that the bridge never saves;
				// before this, nothing enforced it.
				TEXT("save_package"), TEXT("save_blueprint"), TEXT("save_dirty_packages"),
				TEXT("save_level_as"), TEXT("save_all"),
				// Take the editor loop. PM-011 is about a modal deadlocking the bridge; PIE is the
				// same hazard with a longer fuse.
				TEXT("start_pie"), TEXT("stop_pie"),
				// Escape the process entirely - arbitrary command execution and a full cook.
				//
				// ALL THREE Exec endpoints, and they move as a SET. MifBridge::RunEngineExec
				// (MifBridgeCommon.cpp:2110) is the single choke point onto UEngine::Exec, and it has
				// exactly three callers: run_console (MifBridgeIntrospect.cpp:2108), exec_console, and
				// run_console_captured (MifBridgePIE.cpp:534).
				//
				// The third was NOT here. So in scratch mode run_console was refused and
				// run_console_captured executed anything you liked - OBJ SAVEPACKAGE, MAP LOAD, EXIT.
				// The name list was maintained by hand and the family grew a member.
				//
				// This is the THIRD time tonight the same failure has appeared: a control enforced at
				// one choke point, with another road to the same place. First batch bypassing the gate
				// entirely, then send_editor_key and invoke_editor_command reaching Save without
				// writing anything, now a third Exec endpoint. test_safety_gate T636 now derives the
				// caller list from the SOURCE rather than trusting this comment, because a comment
				// saying "all three" is exactly what was true before someone added a fourth.
				TEXT("run_console"), TEXT("exec_console"), TEXT("run_console_captured"),
				TEXT("trigger_cook"),
				// Destroy or replace the working set.
				TEXT("new_level"), TEXT("load_level"), TEXT("quit_editor"), TEXT("restart_editor"),
				// Long, unsupervised, and writes into the project.
				TEXT("build_navmesh"), TEXT("import_asset"),
				// THE SIDE DOORS. These do not write anything themselves, which is exactly why they
				// were missed - the list was built by asking "does this endpoint mutate?" when the
				// question is "can this endpoint REACH something that mutates?".
				//
				// send_editor_key sends real key events into whatever currently has focus
				// (FSlateApplication::ProcessKeyDownEvent). In a level editor, Ctrl+S is Save. So with
				// save_package refused, this was permitted:
				//
				//     send_editor_key { "key": "S", "modifiers": { "ctrl": true } }
				//
				// invoke_editor_command executes any registered FUICommandInfo or ToolMenu entry,
				// which includes the editor's own Save, and its deny-list guards against MODAL HANGS
				// rather than against privilege - a different question that happens to look similar.
				//
				// Same shape as the batch bypass fixed earlier tonight: a control enforced at one
				// choke point, with another road around it. Neither is a defect in these endpoints;
				// both are perfectly reasonable tools. They just cannot be reachable while the thing
				// they can reach is refused, or the refusal is theatre.
				//
				// Gated wholesale rather than filtered, for the same reason as exec_console: they take
				// an arbitrary key or command NAME, so there is no subset that is knowably safe, and a
				// denylist over a namespace someone else populates is the guard shape that always
				// loses.
				//
				// invoke_editor_tab and open_asset_editor are deliberately NOT here. They open UI and
				// cannot execute anything, so they stay available - diagnosis in scratch mode is the
				// point of scratch mode.
				TEXT("send_editor_key"), TEXT("invoke_editor_command"),
			};
			return Unsafe;
		}

		EMifWriteMode ReadModeFromEnvironment()
		{
			const FString Raw = FPlatformMisc::GetEnvironmentVariable(TEXT("MIF_BRIDGE_WRITE_MODE"));
			if (Raw.Equals(TEXT("full"), ESearchCase::IgnoreCase))
			{
				// Announced loudly. An unlocked bridge should never be a quiet surprise to whoever
				// reads the log afterwards wondering how a save happened.
				UE_LOG(LogMifBridge, Warning,
					TEXT("MIF_BRIDGE_WRITE_MODE=full - the safety gate is OFF. Saves, PIE, cooks and "
						 "console execution are all permitted for this session."));
				return EMifWriteMode::Full;
			}
			if (Raw.Equals(TEXT("read"), ESearchCase::IgnoreCase))
			{
				return EMifWriteMode::Read;
			}
			if (!Raw.IsEmpty())
			{
				// Falling back silently after being ASKED for a mode is how a gate ends up in a state
				// nobody intended. Say so, and keep the safe default.
				UE_LOG(LogMifBridge, Warning,
					TEXT("MIF_BRIDGE_WRITE_MODE='%s' is not one of read|scratch|full - staying on "
						 "'scratch'."), *Raw);
			}
			return EMifWriteMode::Scratch;
		}
	}

	// Read once. Immutable for the process lifetime, on purpose: see the header comment on why this
	// cannot be a CVar or a settable endpoint.
	// THE MODE IS RUNTIME-MUTABLE NOW, AND IT STILL HAS NO NAME.
	//
	// Andre asked for a dropdown in the panel so the mode is not an environment variable plus a
	// restart. That means it can no longer be a read-once static - but making it settable is exactly
	// where a gate stops being a gate, so the property that has to survive is this one:
	//
	//   EVERY write primitive this bridge exposes addresses its target BY NAME - a property path, a
	//   cvar name, a console command, an endpoint name. The write mode has no name. It is a file-local
	//   atomic in an anonymous namespace with no FProperty, no UObject outer, no IConsoleVariable and
	//   no FAutoConsoleCommand. There is nothing to address.
	//
	// That is a stronger claim than "no endpoint sets it", because it does not require anyone to have
	// enumerated the endpoints correctly - which is precisely the enumeration that was wrong three
	// times tonight.
	namespace
	{
		// -1 means "not read from the environment yet". Read once, then owned by the panel.
		std::atomic<int8> GModeCache{ -1 };

		// How many bridge calls are on the stack. See SetWriteModeFromPanel.
		std::atomic<int32> GBridgeCallDepth{ 0 };
	}

	EMifWriteMode GetWriteMode()
	{
		const int8 Cached = GModeCache.load(std::memory_order_relaxed);
		if (Cached >= 0)
		{
			return static_cast<EMifWriteMode>(Cached);
		}
		const EMifWriteMode FromEnv = ReadModeFromEnvironment();
		// Benign race: two threads may both read the environment and store the same answer.
		GModeCache.store(static_cast<int8>(FromEnv), std::memory_order_relaxed);
		return FromEnv;
	}

	FMifBridgeCallScope::FMifBridgeCallScope()
	{
		GBridgeCallDepth.fetch_add(1, std::memory_order_relaxed);
	}

	FMifBridgeCallScope::~FMifBridgeCallScope()
	{
		GBridgeCallDepth.fetch_sub(1, std::memory_order_relaxed);
	}

	/** Raise or lower the write mode from the in-editor panel. NOT reachable over the bridge - it is
	 *  not an endpoint, not a cvar and not a UI command, so nothing that takes a NAME can reach it.
	 *
	 *  The depth check is the guard that does not depend on anyone having enumerated the routes. A
	 *  handler can pump Slate without meaning to: several endpoints open a slow-task dialog with
	 *  MakeDialog(true), and a pumped message loop can dispatch a click into this very widget while a
	 *  bridge call is still on the stack. So while ANY call is executing, the mode cannot be RAISED.
	 *
	 *  Lowering is always allowed. Making the gate stricter is never the dangerous direction, and a
	 *  human reaching for 'scratch' mid-operation is someone trying to stop something. */
	bool SetWriteModeFromPanel(EMifWriteMode Wanted, FString& OutRefusal)
	{
		const EMifWriteMode Current = GetWriteMode();
		if (Wanted == Current)
		{
			return true;
		}
		const bool bRaising = static_cast<uint8>(Wanted) > static_cast<uint8>(Current);
		if (bRaising && GBridgeCallDepth.load(std::memory_order_relaxed) > 0)
		{
			OutRefusal = TEXT("a bridge call is currently executing, so the write mode cannot be "
							  "raised right now. This is deliberate: an endpoint that pumps Slate - a "
							  "slow-task dialog, for instance - could otherwise dispatch a click into "
							  "this control while its own call is still on the stack. Try again once "
							  "the bridge is idle.");
			UE_LOG(LogMifBridge, Warning,
				TEXT("MifBridge: refused a panel write-mode RAISE to '%s' - %d bridge call(s) on the stack."),
				WriteModeName(Wanted), GBridgeCallDepth.load(std::memory_order_relaxed));
			return false;
		}

		GModeCache.store(static_cast<int8>(Wanted), std::memory_order_relaxed);
		// Loudly, and at Warning for a raise. Whoever reads this log afterwards wondering how a save
		// happened deserves to find the moment the gate opened.
		// Two calls rather than a ternary: UE_LOG's verbosity must be a compile-time constant, so
		// `bRaising ? Warning : Log` is a C2131 rather than the concise thing it looks like.
		if (bRaising)
		{
			UE_LOG(LogMifBridge, Warning,
				TEXT("MifBridge: write mode RAISED from '%s' to '%s' FROM THE EDITOR PANEL. The safety "
					 "gate is now looser than it was."),
				WriteModeName(Current), WriteModeName(Wanted));
		}
		else
		{
			UE_LOG(LogMifBridge, Log,
				TEXT("MifBridge: write mode lowered from '%s' to '%s' from the editor panel."),
				WriteModeName(Current), WriteModeName(Wanted));
		}
		return true;
	}

	const TCHAR* WriteModeName(EMifWriteMode Mode)
	{
		switch (Mode)
		{
		case EMifWriteMode::Read:  return TEXT("read");
		case EMifWriteMode::Full:  return TEXT("full");
		default:                   return TEXT("scratch");
		}
	}

	bool IsUnsafeEndpoint(const FString& Endpoint)
	{
		return UnsafeEndpoints().Contains(Endpoint);
	}

	// Returns true when the endpoint must NOT run, and fills Out with a refusal that says why and how
	// to proceed. A refusal a caller cannot act on is only half an answer.
	bool RefuseIfGated(const FString& Endpoint, const TSharedRef<FJsonObject>& Out)
	{
		const EMifWriteMode Mode = GetWriteMode();
		if (Mode == EMifWriteMode::Full)
		{
			return false;
		}
		if (!IsUnsafeEndpoint(Endpoint))
		{
			// NOTE: in 'read' mode this currently still allows ordinary writes. The per-endpoint
			// Read/Write classification that would make 'read' fully meaningful is filed follow-up
			// work; claiming it here would be claiming coverage this file does not have.
			return false;
		}

		Fail(Out, FString::Printf(
			TEXT("'%s' is refused: the MifBridge safety gate is in '%s' mode. This endpoint either "
				 "persists to disk, takes the editor loop, or executes outside the process, so no path "
				 "check can make it safe. Restart the editor with MIF_BRIDGE_WRITE_MODE=full to permit "
				 "it, and use `setx MIF_BRIDGE_WRITE_MODE full` to make that stick across launches - "
				 "the variable is read from the environment ONCE at startup, so a value set only in a "
				 "shell dies with that shell and the next launch is back to '%s'. It is deliberately "
				 "NOT settable over the bridge - an agent that could unlock its own gate is not "
				 "gated - but that is about the BRIDGE, not about you."),
			*Endpoint, WriteModeName(Mode), WriteModeName(Mode)));
		// Reported as structured fields too, so a caller can branch without parsing prose.
		Out->SetStringField(TEXT("refusedBy"), TEXT("safety-gate"));
		Out->SetStringField(TEXT("writeMode"), WriteModeName(Mode));
		Out->SetStringField(TEXT("unlock"), TEXT("MIF_BRIDGE_WRITE_MODE=full (environment, read once at startup)"));
		// The PERSISTENT form, as its own field. Infected asked "Why dont it remain full every time i
		// launch?" - the refusal said how to unlock and never how to make it stick, so anyone reading
		// only the refusal would reasonably conclude it was per-session.
		Out->SetStringField(TEXT("unlockPersistent"), TEXT("setx MIF_BRIDGE_WRITE_MODE full"));
		return true;
	}
}
