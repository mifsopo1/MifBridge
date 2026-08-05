// MifBridge — console command + console variable endpoints.
//
// WHY THIS FILE EXISTS (2026-08-04): the bridge could edit assets but could not touch the editor's
// own console. That blocked a concrete investigation: MifKismetReconstructor gates event/function
// body reconstruction behind `mif.kr.Events` and `mif.kr.LatentResume`, both of which ship
// DEFAULT-OFF. Without a way to read or flip a cvar from the bridge, "the reconstructor cannot
// recover widget function bodies" was untestable — the flags might simply have been off. A
// community report (uncooked FAS_PackagingTrayWidget keeps its Designer but loses TryUnpackItems /
// PlayerConfirmedAction / SourceUpdate / PlayerUpdate / HideoutUpdate) could not be answered
// without it. Answering "is this a tool bug or a tool setting" needs exactly these three calls.
//
// Scope note: this is an EDITOR module. `exec_console` runs against the editor world, not a PIE or
// shipping world, so it is a development affordance — not a way to drive the packaged game.

#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Containers/UnrealString.h" // FStringOutputDevice (UnrealString.h:2387 - it IS an FString)
#include "Editor.h"                 // GEditor
#include "Engine/Engine.h"          // GEngine->Exec
#include "Engine/World.h"
#include "HAL/IConsoleManager.h"

namespace MifBridge
{
	// Resolve the world an editor console command should run against. GEditor's editor world
	// context is the right target: PIE may not be running, and we must never silently fall back
	// to a PIE world (a command would then hit a throwaway world and appear to do nothing).
	static UWorld* EditorWorldForExec()
	{
		if (GEditor)
		{
			if (UWorld* W = GEditor->GetEditorWorldContext().World())
			{
				return W;
			}
		}
		return nullptr;
	}

	// --- exec_console ---------------------------------------------------------
	// Runs an arbitrary console command in the EDITOR and returns whatever it printed.
	// Output capture matters: half the useful commands (stat, dumpconsolecommands, a cvar echo)
	// communicate only through the log, so a bare "ok:true" would be near useless.
	void H_exec_console(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("command") },
			TEXT("command - the console command to run in the editor, e.g. \"mif.kr.Events 1\" or \"stat unit\""),
			{ { TEXT("cmd"),     TEXT("spell it command") },
			  { TEXT("cvar"),    TEXT("to READ a cvar use get_cvar {name}; to SET one use set_cvar {name, value}") },
			  { TEXT("console"), TEXT("spell it command") } }))
		{
			return;
		}

		const FString Command = JStr(In, TEXT("command"));
		if (Command.IsEmpty())
		{
			Fail(Out, TEXT("command is required and must not be empty"));
			return;
		}

		UWorld* World = EditorWorldForExec();
		if (!World)
		{
			Fail(Out, TEXT("no editor world - is a map open?"));
			return;
		}

		// FStringOutputDevice captures what the command prints; without it the caller gets nothing
		// back for the many commands whose entire result IS their log output.
		FStringOutputDevice Captured;
		Captured.SetAutoEmitLineTerminator(true);
		const bool bHandled = GEngine->Exec(World, *Command, Captured);

		Out->SetStringField(TEXT("command"), Command);
		Out->SetBoolField(TEXT("handled"), bHandled);
		Out->SetStringField(TEXT("output"), Captured);
		if (!bHandled)
		{
			// Not an error - UE returns false for anything it did not recognise as an Exec command,
			// including every cvar assignment. Say so rather than letting the caller read it as failure.
			Out->SetStringField(TEXT("note"),
				TEXT("handled=false means no Exec handler claimed it. Cvar assignments normally return false ")
				TEXT("and still take effect - verify with get_cvar."));
		}
		UE_LOG(LogMifBridge, Log, TEXT("exec_console: %s (handled=%d, %d chars out)"),
			*Command, bHandled ? 1 : 0, Captured.Len());
	}

	// --- get_cvar -------------------------------------------------------------
	void H_get_cvar(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("name") },
			TEXT("name - the console variable to read, e.g. \"mif.kr.Events\""),
			{ { TEXT("cvar"),  TEXT("spell it name") },
			  { TEXT("var"),   TEXT("spell it name") },
			  { TEXT("value"), TEXT("get_cvar only reads; use set_cvar {name, value} to write") } }))
		{
			return;
		}

		const FString Name = JStr(In, TEXT("name"));
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("name is required"));
			return;
		}

		IConsoleVariable* Var = IConsoleManager::Get().FindConsoleVariable(*Name);
		if (!Var)
		{
			Fail(Out, FString::Printf(
				TEXT("no console variable named '%s' - it may be spelled differently, or its owning module ")
				TEXT("may not be loaded yet (a plugin cvar only exists once that plugin has started)"), *Name));
			return;
		}

		Out->SetStringField(TEXT("name"), Name);
		Out->SetStringField(TEXT("value"), Var->GetString());
		Out->SetNumberField(TEXT("asInt"), Var->GetInt());
		Out->SetNumberField(TEXT("asFloat"), Var->GetFloat());
		Out->SetBoolField(TEXT("asBool"), Var->GetBool());
		if (IConsoleObject* Obj = IConsoleManager::Get().FindConsoleObject(*Name))
		{
			if (const TCHAR* Help = Obj->GetHelp())
			{
				Out->SetStringField(TEXT("help"), Help);
			}
		}
	}

	// --- set_cvar -------------------------------------------------------------
	void H_set_cvar(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("name"), TEXT("value") },
			TEXT("name, value - sets a console variable, e.g. {name:\"mif.kr.Events\", value:\"1\"}"),
			{ { TEXT("cvar"), TEXT("spell it name") },
			  { TEXT("var"),  TEXT("spell it name") } }))
		{
			return;
		}

		const FString Name = JStr(In, TEXT("name"));
		if (Name.IsEmpty())
		{
			Fail(Out, TEXT("name is required"));
			return;
		}
		IConsoleVariable* Var = IConsoleManager::Get().FindConsoleVariable(*Name);
		if (!Var)
		{
			Fail(Out, FString::Printf(TEXT("no console variable named '%s'"), *Name));
			return;
		}

		const FString Before = Var->GetString();
		const FString Value  = JStr(In, TEXT("value"));
		// ECVF_SetByConsole matches what a user typing in the console would do, so a project's
		// ini-set values do not silently win over ours.
		Var->Set(*Value, ECVF_SetByConsole);
		const FString After = Var->GetString();

		Out->SetStringField(TEXT("name"), Name);
		Out->SetStringField(TEXT("before"), Before);
		Out->SetStringField(TEXT("after"), After);
		Out->SetBoolField(TEXT("changed"), Before != After);
		if (After != Value)
		{
			// Never report a write as done when the readback disagrees — the caller must be able
			// to trust "changed" without re-reading. Common cause: a cvar with a stricter setter,
			// or one guarded at a higher ECVF priority.
			Out->SetStringField(TEXT("warning"), FString::Printf(
				TEXT("readback is '%s', not the '%s' that was requested - the cvar may clamp, coerce, ")
				TEXT("or be locked at a higher priority than SetByConsole"), *After, *Value));
		}
		UE_LOG(LogMifBridge, Log, TEXT("set_cvar: %s '%s' -> '%s'"), *Name, *Before, *After);
	}
}
