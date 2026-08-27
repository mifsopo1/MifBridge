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
}
