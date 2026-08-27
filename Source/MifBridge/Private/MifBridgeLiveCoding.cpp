// MifBridge — LIVE CODING: see whether C++ can be recompiled right now, and start a compile.
//
// The useful half of the reopened "C++ & Modules" item. That was declined under the old cooked-only
// rule ("a DDS2 mod is Blueprint plus a _P pak; cooked-game mods cannot add C++ modules"), which was
// true and is beside the point now that Curfew is an UNCOOKED 5.7 project where C++ is ordinary work.
//
// READING AND WRITING .cpp/.h IS NOT WHAT A BRIDGE IS FOR. An agent already has file tools; adding
// endpoints that open files would be duplicating something it can do better without a round trip.
// What only the editor can do is CLOSE THE LOOP - trigger a compile inside the running process and
// report whether it took. That is the same argument MifBridge is built on for Blueprints: the value
// was never "write the graph", it was "read the compiler's answer back".
//
// AND IT ANSWERS A QUESTION THAT COST THIS PROJECT REAL TIME. docs/01 records builds that reported
// success and did nothing because Live Coding was holding the DLL while the editor was open. There
// was no way to ASK. live_coding_status is that question, and an agent about to run Build.bat can now
// check first instead of discovering it from a sub-second "success".
//
// NEVER WaitForCompletion. THIS IS THE WHOLE SAFETY ARGUMENT.
//
// ILiveCodingModule::Compile(ELiveCodingCompileFlags, ELiveCodingCompileResult*) accepts a
// WaitForCompletion flag that blocks until the compile finishes. Handlers run SYNCHRONOUSLY ON THE
// GAME THREAD inside the HTTP ticker, so blocking there does not slow the bridge down - it takes the
// bridge OFF THE AIR for the length of a C++ compile, exactly like the modal dialog in PM-011. It
// would also block the very tick that a caller would need in order to ask whether it was done.
//
// So the flag is hardcoded to None, the call returns InProgress immediately, and polling is the
// caller's job. There is no parameter to change this, deliberately: an option that can hang the
// bridge should not be one typo away.
//
// The interface is BYTE-IDENTICAL in both engines - ILiveCodingModule.h, same twelve virtuals, same
// enums, same LIVE_CODING_MODULE_NAME. No version guard is needed, which is rare enough here to be
// worth stating.
//
// WINDOWS ONLY: the module lives under Source/Developer/WINDOWS/LiveCoding. Reached through
// FModuleManager::GetModulePtr rather than a link dependency, so a platform or build without it is a
// null pointer and a named refusal rather than a link error.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Modules/ModuleManager.h"

#if MIF_WITH_LIVECODING
#include "ILiveCodingModule.h"
#endif

namespace MifBridge
{
#if MIF_WITH_LIVECODING
	namespace
	{
		/** The module, or null. GetModulePtr does NOT load it: if Live Coding has never been started
		 *  this returns null, which is the honest answer rather than a reason to force it up. */
		ILiveCodingModule* MifLiveCoding()
		{
			return FModuleManager::GetModulePtr<ILiveCodingModule>(LIVE_CODING_MODULE_NAME);
		}

		const TCHAR* CompileResultName(ELiveCodingCompileResult R)
		{
			switch (R)
			{
			case ELiveCodingCompileResult::Success:            return TEXT("Success");
			case ELiveCodingCompileResult::NoChanges:          return TEXT("NoChanges");
			case ELiveCodingCompileResult::InProgress:         return TEXT("InProgress");
			case ELiveCodingCompileResult::CompileStillActive: return TEXT("CompileStillActive");
			case ELiveCodingCompileResult::NotStarted:         return TEXT("NotStarted");
			case ELiveCodingCompileResult::Failure:            return TEXT("Failure");
			case ELiveCodingCompileResult::Cancelled:          return TEXT("Cancelled");
			default:                                          return TEXT("(unrecognised)");
			}
		}
	}
#endif

	// --- live_coding_status ---------------------------------------------------------------------
	//   in:  {}
	//   out: { available, started, enabledForSession, canEnableForSession, compiling, blocksBuilds }
	// Bucket: READ.
	void H_live_coding_status(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, {},
			TEXT("no parameters"),
			{ { TEXT("enable"), TEXT("this only READS the state. Enabling Live Coding mid-session changes how the editor holds its DLLs and is a decision for a person at the keyboard.") } }))
		{
			return;
		}
#if !MIF_WITH_LIVECODING
		// Registered either way, per the MIF_WITH_* contract: a missing endpoint tells a caller
		// nothing, a refusal naming the reason tells them everything.
		Out->SetBoolField(TEXT("available"), false);
		Out->SetStringField(TEXT("note"),
			TEXT("this MifBridge was built without the LiveCoding module. It lives under "
				 "Source/Developer/Windows/LiveCoding, so a non-Windows build does not have it."));
#else
		ILiveCodingModule* LC = MifLiveCoding();
		Out->SetBoolField(TEXT("available"), LC != nullptr);
		if (!LC)
		{
			Out->SetStringField(TEXT("note"),
				TEXT("the LiveCoding module is not loaded in this process. That is normal when Live "
					 "Coding has never been started this session - it is NOT an error, and it means "
					 "nothing is holding the editor's DLLs."));
			Out->SetBoolField(TEXT("blocksBuilds"), false);
			return;
		}

		const bool bStarted   = LC->HasStarted();
		const bool bEnabled   = LC->IsEnabledForSession();
		const bool bCompiling = LC->IsCompiling();

		Out->SetBoolField(TEXT("started"), bStarted);
		Out->SetBoolField(TEXT("enabledForSession"), bEnabled);
		Out->SetBoolField(TEXT("canEnableForSession"), LC->CanEnableForSession());
		Out->SetBoolField(TEXT("enabledByDefault"), LC->IsEnabledByDefault());
		Out->SetBoolField(TEXT("compiling"), bCompiling);
		Out->SetBoolField(TEXT("automaticallyCompilesNewClasses"), LC->AutomaticallyCompileNewClasses());

		// THE FIELD THIS ENDPOINT EXISTS FOR. docs/01 has more than one entry about a build that
		// reported success and produced nothing, because Live Coding was holding the DLLs while the
		// editor was open. Until now there was no way to ask before running Build.bat.
		Out->SetBoolField(TEXT("blocksBuilds"), bStarted);
		Out->SetStringField(TEXT("buildNote"), bStarted
			? TEXT("Live Coding HAS STARTED and is holding this editor's DLLs. An external Build.bat "
				   "will not be able to replace them, and has been observed REPORTING SUCCESS anyway "
				   "while changing nothing - check the binary's mtime, or close the editor first.")
			: TEXT("Live Coding has not started, so nothing is holding the editor's DLLs and an "
				   "external build can replace them normally."));
#endif
	}

	// --- live_coding_compile --------------------------------------------------------------------
	//   in:  { confirm }
	//   out: { requested, result, compiling }
	// Bucket: MUTATES the running process - it patches compiled code into the live editor.
	void H_live_coding_compile(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("confirm") },
			TEXT("confirm:true"),
			{ { TEXT("wait"), TEXT("there is deliberately NO wait option - blocking here takes the whole bridge off the air for the length of a C++ compile. Poll live_coding_status instead.") },
			  { TEXT("target"), TEXT("Live Coding compiles whatever changed; it does not take a target") } }))
		{
			return;
		}
#if !MIF_WITH_LIVECODING
		Fail(Out, TEXT("live_coding_compile is unavailable: this MifBridge was built without the "
					   "LiveCoding module, which lives under Source/Developer/Windows/LiveCoding."));
#else
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("live_coding_compile needs confirm:true. It PATCHES NEWLY COMPILED CODE "
						   "INTO THE RUNNING EDITOR - that is what Live Coding is - and a bad patch "
						   "can destabilise the process holding your unsaved work. NOTHING was "
						   "compiled."));
			return;
		}
		ILiveCodingModule* LC = MifLiveCoding();
		if (!LC)
		{
			Fail(Out, TEXT("the LiveCoding module is not loaded in this process, so there is nothing "
						   "to compile with. Start Live Coding from the editor (the toolbar button, "
						   "or Ctrl+Alt+F11) and call again. NOTHING was compiled."));
			return;
		}
		if (!LC->HasStarted())
		{
			// Refused rather than started for them. Starting Live Coding changes how the editor holds
			// its DLLs for the rest of the session, which is a decision for a person.
			Fail(Out, TEXT("Live Coding has not been started for this session. Starting it changes how "
						   "the editor holds its DLLs for the rest of the session, so it is left to a "
						   "person at the keyboard rather than done from here. NOTHING was compiled."));
			return;
		}
		if (LC->IsCompiling())
		{
			Fail(Out, TEXT("a Live Coding compile is ALREADY RUNNING. Poll live_coding_status until "
						   "compiling is false. NOTHING was started."));
			return;
		}

		// None, never WaitForCompletion - see the file header. This returns immediately.
		ELiveCodingCompileResult Result = ELiveCodingCompileResult::NotStarted;
		const bool bRequested = LC->Compile(ELiveCodingCompileFlags::None, &Result);

		Out->SetBoolField(TEXT("requested"), bRequested);
		Out->SetStringField(TEXT("result"), CompileResultName(Result));
		Out->SetBoolField(TEXT("compiling"), LC->IsCompiling());
		Out->SetStringField(TEXT("note"),
			TEXT("this does NOT wait - blocking would take the bridge off the air for the length of a "
				 "C++ compile, and would block the very tick you would need to ask whether it had "
				 "finished. 'InProgress' means it started. Poll live_coding_status until compiling is "
				 "false, then read the editor's Live Coding console for the compiler output."));
		UE_LOG(LogMifBridge, Log, TEXT("live_coding_compile: requested=%d result=%s"),
			bRequested ? 1 : 0, CompileResultName(Result));
#endif
	}
}
