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
				TEXT("run_console"), TEXT("exec_console"), TEXT("trigger_cook"),
				// Destroy or replace the working set.
				TEXT("new_level"), TEXT("load_level"), TEXT("quit_editor"), TEXT("restart_editor"),
				// Long, unsupervised, and writes into the project.
				TEXT("build_navmesh"), TEXT("import_asset"),
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
	EMifWriteMode GetWriteMode()
	{
		static const EMifWriteMode Mode = ReadModeFromEnvironment();
		return Mode;
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
