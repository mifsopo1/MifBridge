// MifBridge — GAMEPLAY TAGS: what tags this project actually has, and where each one came from.
//
// Reopened 2026-08-27. The original decline was evidence-based and still wrong: it checked DDS2, found
// no DefaultGameplayTags.ini, no GameplayTags settings, the plugin not enabled and zero tags on
// DDS2_GameMode, and concluded there was nothing to build against. Every one of those facts is about
// DDS2. Gameplay tags are standard in modern UE5 projects, and Curfew is one.
//
// WHY THIS IS BRIDGE-ONLY WORK, unlike the .cpp/.h reading declined alongside it. The tag table is not
// a file. It is ASSEMBLED AT RUNTIME from several sources - DefaultGameplayTags.ini, any number of
// other ini files, native C++ registration through UE_DEFINE_GAMEPLAY_TAG, and tags added by plugins -
// and only the running editor knows the result. Reading DefaultGameplayTags.ini tells you one input,
// not the answer. An agent about to write code referencing a tag needs the answer.
//
// Verified in BOTH trees before writing:
//   UGameplayTagsManager::Get()                  5.3 FORCEINLINE / 5.7 inline - declaration only
//   ::RequestAllGameplayTags(FGameplayTagContainer&, bool)   GAMEPLAYTAGS_API, identical in both
//   ::FindTagNode(FName)                         inline in both
//   FGameplayTagNode::GetChildTagNodes()         inline in both
//   FGameplayTagNode::GetCompleteTagString()     inline in both
//
// DELIBERATELY NOT USED: UGameplayTagsManager::GetSingleTagContainer, which carries
// UE_DEPRECATED(5.4, "This function is not threadsafe...") in 5.7. A 5.7 deprecation is a future build
// break (docs/02 section 14, direction A), and the deprecation message names FindTagNode as the
// replacement - which is what this uses.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Modules/ModuleManager.h"
#if MIF_WITH_GAMEPLAYTAGSEDITOR
#include "GameplayTagsEditorModule.h"
#endif
#include "GameplayTagsManager.h"
#include "GameplayTagContainer.h"

namespace MifBridge
{
	// --- list_gameplay_tags ---------------------------------------------------------------------
	//   in:  { filter?, onlyExplicit? = true, limit? = 0 }
	//   out: { tags[ { tag, children, source } ], count, matched, explicitOnly }
	// Bucket: READ.
	void H_list_gameplay_tags(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("filter"), TEXT("search"), TEXT("onlyExplicit"), TEXT("limit") },
			TEXT("filter (alias: search) - substring match on the tag string; onlyExplicit (default "
				 "true) - exclude tags that exist only as implied parents; limit (0 = all)"),
			{ { TEXT("tag"), TEXT("this LISTS tags; describe_gameplay_tag takes one") },
			  { TEXT("category"), TEXT("gameplay tags have no categories - the hierarchy IS the grouping, so filter on a prefix like 'Ability.'") } }))
		{
			return;
		}

		const FString Filter = JStrAny(In, { TEXT("filter"), TEXT("search") });
		// Explicit by default. The manager can also report every IMPLIED parent - asking for
		// "Ability.Melee.Heavy" implies "Ability" and "Ability.Melee" exist as nodes - and including
		// them roughly doubles the list with entries nobody declared. A caller who wants the whole
		// tree can ask for it; the useful default is what the project actually defined.
		const bool bOnlyExplicit = JBool(In, TEXT("onlyExplicit"), true);
		const int32 Limit = (int32)JNum(In, TEXT("limit"), 0.0);

		UGameplayTagsManager& Mgr = UGameplayTagsManager::Get();
		FGameplayTagContainer All;
		Mgr.RequestAllGameplayTags(All, bOnlyExplicit);

		TArray<TSharedPtr<FJsonValue>> Tags;
		int32 Matched = 0;
		for (const FGameplayTag& T : All)
		{
			const FString Str = T.ToString();
			if (!Filter.IsEmpty() && !Str.Contains(Filter)) { continue; }
			++Matched;
			if (Limit > 0 && Tags.Num() >= Limit) { continue; }

			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("tag"), Str);
			if (TSharedPtr<FGameplayTagNode> Node = Mgr.FindTagNode(T.GetTagName()))
			{
				J->SetNumberField(TEXT("children"), Node->GetChildTagNodes().Num());
			}
			Tags.Add(MakeShared<FJsonValueObject>(J));
		}

		Out->SetArrayField(TEXT("tags"), Tags);
		Out->SetNumberField(TEXT("count"), Tags.Num());
		// MATCHED is the true total even when limit truncated the list - the same contract
		// list_level_sequences uses, so a caller can tell "there are 12" from "I showed you 12 of 400".
		Out->SetNumberField(TEXT("matched"), Matched);
		Out->SetBoolField(TEXT("explicitOnly"), bOnlyExplicit);

		if (Matched == 0)
		{
			// Said plainly, because an empty tag table is a legitimate and common state - it means the
			// project does not use gameplay tags - and is easy to misread as a broken query.
			Out->SetStringField(TEXT("note"), Filter.IsEmpty()
				? TEXT("this project has NO gameplay tags registered. That is a normal state, not an "
					   "error: tags come from DefaultGameplayTags.ini, other ini files, and native "
					   "UE_DEFINE_GAMEPLAY_TAG registration, and a project can simply not use them.")
				: TEXT("no tag matches that filter. Call with no filter to see what does exist - the "
					   "hierarchy is the grouping, so try a prefix such as 'Ability.'."));
		}
	}

	// --- describe_gameplay_tag ------------------------------------------------------------------
	//   in:  { tag }
	//   out: { tag, exists, parents[], children[], directChildren, isExplicit, devComment }
	// Bucket: READ.
	void H_describe_gameplay_tag(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("tag"), TEXT("name") },
			TEXT("tag (alias: name) - a full tag string such as 'Ability.Melee.Heavy'"),
			{ { TEXT("filter"), TEXT("that is list_gameplay_tags; this describes ONE tag") } }))
		{
			return;
		}

		const FString TagStr = JStrAny(In, { TEXT("tag"), TEXT("name") });
		if (TagStr.IsEmpty())
		{
			Fail(Out, TEXT("tag is required - a full tag string. list_gameplay_tags reports them."));
			return;
		}

		UGameplayTagsManager& Mgr = UGameplayTagsManager::Get();
		// ErrorIfNotFound=false, deliberately. The default TRUE logs an error and puts a red line in
		// the editor's log for what is, here, a perfectly ordinary question with the answer "no". An
		// endpoint that asks whether something exists must not shout when it does not.
		const FGameplayTag Tag = Mgr.RequestGameplayTag(FName(*TagStr), /*ErrorIfNotFound*/ false);

		Out->SetStringField(TEXT("tag"), TagStr);
		Out->SetBoolField(TEXT("exists"), Tag.IsValid());
		if (!Tag.IsValid())
		{
			// Not a Fail. "Does this tag exist?" answered with "no" is a successful call, and making it
			// an error would force a caller to parse an error string to learn something routine.
			Out->SetStringField(TEXT("note"), FString::Printf(
				TEXT("'%s' is not a registered gameplay tag in this project. Tags are assembled at "
					 "runtime from ini files and native UE_DEFINE_GAMEPLAY_TAG registration, so a tag "
					 "present in source may still be absent here if its ini or module did not load."),
				*TagStr));
			return;
		}

		// The parent chain, from the tag upward. GetGameplayTagParents returns the tag itself as well,
		// which is right for containment checks and wrong for a "parents" field, so it is skipped.
		FGameplayTagContainer Parents = Tag.GetGameplayTagParents();
		TArray<TSharedPtr<FJsonValue>> ParentJson;
		for (const FGameplayTag& P : Parents)
		{
			if (P == Tag) { continue; }
			ParentJson.Add(MakeShared<FJsonValueString>(P.ToString()));
		}
		Out->SetArrayField(TEXT("parents"), ParentJson);

		TSharedPtr<FGameplayTagNode> Node = Mgr.FindTagNode(Tag.GetTagName());
		if (Node.IsValid())
		{
			TArray<TSharedPtr<FJsonValue>> Kids;
			for (const TSharedPtr<FGameplayTagNode>& C : Node->GetChildTagNodes())
			{
				if (C.IsValid()) { Kids.Add(MakeShared<FJsonValueString>(C->GetCompleteTagString())); }
			}
			Out->SetArrayField(TEXT("children"), Kids);
			Out->SetNumberField(TEXT("directChildren"), Kids.Num());
			Out->SetStringField(TEXT("simpleName"), Node->GetSimpleTagName().ToString());
		}
		else
		{
			// Reachable: a tag can resolve while its node does not, and reporting the fields as absent
			// rather than empty keeps "no children" distinct from "could not look".
			Out->SetStringField(TEXT("nodeNote"),
				TEXT("the tag resolves but has no node in the manager, so its children could not be "
					 "read. This is unusual and worth reporting if you see it."));
		}
	}

	// add_gameplay_tag WAS ATTEMPTED HERE, 2026-08-29, and DOES NOT EXIST - the compiler, not a
	// design choice, is why. UGameplayTagsManager::AddTagTableRow looked public from the header
	// (GAMEPLAYTAGS_API, no access specifier visible in a plain grep) and is documented as the exact
	// call PopulateTreeFromDataTable makes per ini row - but it sits under a `private:` block
	// (GameplayTagsManager.h:739 in 5.3, gated to `friend class SAddNewGameplayTagSourceWidget` and
	// two others), which only the actual build caught: MifBridgeGameplayTags.cpp(246): error C2248
	// 'cannot access private member'. The other candidate, AddNativeGameplayTag(FName, FString), is
	// ALSO private (same file, :253-370 - a different private block, same result). Checked both
	// before reverting rather than stopping at the first failure.
	// THE LESSON, this project's own repeatedly-learned one, re-learned here in real time: reading a
	// header for a GAMEPLAYTAGS_API-decorated declaration is not the same as knowing it is callable -
	// only the compiler resolves access specifiers reliably, and grepping the file did not surface
	// the `private:` line sitting above the match. There is no public runtime API to add a gameplay
	// tag to the live tree; the entire mutating surface is deliberately gated to the engine's own
	// "Add New Gameplay Tag Source" editor widget and native UE_DEFINE_GAMEPLAY_TAG registration.
	//
	// THE CONCLUSION WAS WRONG, and add_gameplay_tag is built below - 2026-08-30. Every sentence
	// above is TRUE of the RUNTIME module, and the investigation behind it was careful: it checked
	// two separate private blocks before giving up. What it never did was look in a different
	// MODULE. UGameplayTagsManager lives in Runtime/GameplayTags, where the mutators are private by
	// design; the supported way to author a tag is IGameplayTagsEditorModule, in the
	// GameplayTagsEditor PLUGIN, and that interface is entirely public:
	//
	//   AddNewGameplayTagToINI(NewTag, Comment, TagSourceName, bIsRestricted, bAllowNonRestrictedChildren)
	//   AddTransientEditorGameplayTag(NewTransientTag)
	//
	// Verified on BOTH engines before writing a line - D:/UE532/Engine/Plugins/Editor/
	// GameplayTagsEditor/.../GameplayTagsEditorModule.h:48 and :60, UE_5.7 the same at :50 and :66.
	// The generalisable lesson is the inverse of the one recorded above: "the runtime API is
	// private" is not the same as "there is no API", and an editor-only capability living in an
	// editor-only module is the normal shape in this engine, not the exception. A decline is a
	// permanent closure, so it has to survive a wider search than a build does.

	// --- add_gameplay_tag ---------------------------------------------------
	//   in:  { tag, comment?, source?, transient? }
	//   out: { tag, transient, added, resolved, source, note? }
	//
	// TWO MODES, and the difference is where the tag LIVES - which is also why the safety gate has
	// to treat them differently:
	//   transient:false (default) writes into a config .ini on disk - DefaultGameplayTags.ini unless
	//     `source` names another - and survives an editor restart. That is a persistent write to a
	//     file outside /Game, so it is refused unless the write mode is Full. Checked HERE in the
	//     handler rather than by adding the endpoint to UnsafeEndpoints, because gating the NAME
	//     would take the transient mode away with it, and the transient mode is the one an agent
	//     exploring a project actually wants.
	//   transient:true registers the tag for this editor session only. Nothing is written, nothing
	//     survives a restart, and it is safe in every mode.
	void H_add_gameplay_tag(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("tag"), TEXT("comment"), TEXT("source"), TEXT("transient") },
			TEXT("tag (required, e.g. 'Ability.Melee.Heavy'); comment (developer comment stored beside ")
			TEXT("it); source (which .ini owns it - default DefaultGameplayTags.ini); transient (bool, ")
			TEXT("default false - true registers for THIS EDITOR SESSION only and writes nothing to disk)"),
			{ { TEXT("name"), TEXT("spell it tag - the full dotted tag name") },
			  { TEXT("tagName"), TEXT("spell it tag - the full dotted tag name") },
			  { TEXT("temporary"), TEXT("spell it transient - session-only, nothing written to disk") } }))
		{
			return;
		}

		const FString Tag = JStr(In, TEXT("tag"));
		if (Tag.IsEmpty())
		{
			Fail(Out, TEXT("tag is required - the full dotted name, e.g. 'Ability.Melee.Heavy'. ")
				TEXT("NOTHING was added."));
			return;
		}

		UGameplayTagsManager& Manager = UGameplayTagsManager::Get();
		if (Manager.RequestGameplayTag(FName(*Tag), /*ErrorIfNotFound*/ false).IsValid())
		{
			// Not a failure - the end state the caller asked for already holds. Said explicitly,
			// with added:false, so "it is there" and "I put it there" stay distinguishable.
			Out->SetStringField(TEXT("tag"), Tag);
			Out->SetBoolField(TEXT("added"), false);
			Out->SetBoolField(TEXT("resolved"), true);
			Out->SetStringField(TEXT("note"), TEXT("this tag already exists - nothing was added, and ")
				TEXT("nothing needed to be. added:false with resolved:true means the end state you ")
				TEXT("asked for is in place."));
			return;
		}

#if MIF_WITH_GAMEPLAYTAGSEDITOR
		const bool bTransient = JBool(In, TEXT("transient"), false);

		if (!bTransient && GetWriteMode() != EMifWriteMode::Full)
		{
			Fail(Out, FString::Printf(
				TEXT("adding a PERSISTENT gameplay tag writes it into a config .ini on disk, which ")
				TEXT("outlives this session, and the write mode is '%s'. Two ways forward: pass ")
				TEXT("transient:true to register '%s' for THIS EDITOR SESSION only (writes nothing, ")
				TEXT("allowed in every mode, gone on restart), or set the write mode to full. ")
				TEXT("NOTHING was added."),
				WriteModeName(GetWriteMode()), *Tag));
			return;
		}

		IGameplayTagsEditorModule* Editor =
			FModuleManager::GetModulePtr<IGameplayTagsEditorModule>(TEXT("GameplayTagsEditor"));
		if (!Editor)
		{
			Fail(Out, TEXT("the GameplayTagsEditor module is not loaded, so no tag can be authored. ")
				TEXT("It ships with the engine as an editor plugin, so on an editor build it should ")
				TEXT("be present. NOTHING was added."));
			return;
		}

		bool bOk = false;
		if (bTransient)
		{
			bOk = Editor->AddTransientEditorGameplayTag(Tag);
		}
		else
		{
			bOk = Editor->AddNewGameplayTagToINI(Tag, JStr(In, TEXT("comment")),
				FName(*JStr(In, TEXT("source"))));
		}

		// The engine's own bool, CHECKED rather than discarded - and then a READ-BACK on top, because
		// a true return only means the call did not object. Whether the manager now resolves the tag
		// is the postcondition the caller actually cares about, and the two can disagree.
		const bool bResolves = Manager.RequestGameplayTag(FName(*Tag), /*ErrorIfNotFound*/ false).IsValid();

		Out->SetStringField(TEXT("tag"), Tag);
		Out->SetBoolField(TEXT("transient"), bTransient);
		Out->SetBoolField(TEXT("added"), bOk);
		Out->SetBoolField(TEXT("resolved"), bResolves);
		if (!bTransient)
		{
			const FString Source = JStr(In, TEXT("source"));
			Out->SetStringField(TEXT("source"),
				Source.IsEmpty() ? TEXT("DefaultGameplayTags.ini") : Source);
		}

		if (!bOk)
		{
			Fail(Out, FString::Printf(
				TEXT("the engine refused to add '%s'. The usual causes are an invalid tag name - tags ")
				TEXT("are dot-separated with no spaces and no leading or trailing dot - or a source ")
				TEXT("that does not exist. NOTHING was added."), *Tag));
			return;
		}
		if (!bResolves)
		{
			Fail(Out, FString::Printf(
				TEXT("the engine reported adding '%s' but the tag manager still does not resolve it, ")
				TEXT("so it is not usable. Reported rather than passed off as success."), *Tag));
			return;
		}
		if (bTransient)
		{
			Out->SetStringField(TEXT("note"), TEXT("registered for THIS EDITOR SESSION only - nothing ")
				TEXT("was written to disk, and this tag is gone after a restart. Pass transient:false ")
				TEXT("in full write mode to persist it."));
		}
#else
		Fail(Out, TEXT("this engine has no GameplayTagsEditor plugin, so gameplay tags cannot be ")
			TEXT("authored from here. NOTHING was added."));
#endif
	}
}
