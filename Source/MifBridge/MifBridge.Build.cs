// MifBridge — editor-only localhost HTTP bridge for programmatic Blueprint graph edits.
// Editor module only; never a runtime dependency of any cooked mod.

using UnrealBuildTool;

public class MifBridge : ModuleRules
{
	public MifBridge(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine"
		});

		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"UnrealEd",          // FKismetEditorUtilities, FBlueprintEditorUtils, editor subsystems
			"BlueprintGraph",    // UK2Node_* classes
			"GraphEditor",       // graph helpers
			"UMGEditor",         // UK2Node_CreateWidget (private header, see PrivateIncludePaths)
			"UMG",               // UUserWidget runtime class + Blueprint/UserWidget.h
			"Kismet",            // blueprint editor helpers (kept for safety)
			"KismetCompiler",    // compile results struct
			"HTTPServer",        // FHttpServerModule / IHttpRouter
			"Json",
			"JsonUtilities",
			"AssetRegistry",     // find/open blueprints by path
			"EditorSubsystem",
			"ToolMenus",         // Start/Stop menu toggle
			"Slate",
			"SlateCore",
			"Projects",
			"Sockets"            // FInternetAddr for loopback peer enforcement
		});

		// UK2Node_CreateWidget.h is a UMGEditor PRIVATE header (Nodes/); the module
		// dependency isn't enough — the private folder must be on the include path.
		PrivateIncludePaths.Add(System.IO.Path.Combine(GetModuleDirectory("UMGEditor"), "Private"));
	}
}
