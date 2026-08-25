# UE 4.27 port feasibility — what the evidence says

Written 2026-08-25 in answer to "could MifBridge work for UE4 as well as UE5".

This is a **static** assessment: headers and module lists on this machine, not an attempted build.
That distinction matters and is revisited at the end, because it is the part that decides the real
cost.

**Target: UE 4.27.** Both 4.21 and 4.27 are installed here. 4.27 is the last UE4 and the only sensible
target; 4.21 predates too much of what the bridge relies on to be worth costing.

---

## The headline

**The port is plausible and is not a rewrite.** The architecture survives, all module dependencies
exist, and the UE5-only API surface is about **40 call sites across 8 files out of 46,784 lines** of
C++.

That is a much better answer than expected, and it should still be read with the caveat at the bottom.

---

## 1. The transport survives — this was the biggest risk

`FHttpServerModule` **exists in 4.27**:

```
Engine/Source/Runtime/Online/HTTPServer/Public/HttpServerModule.h
```

If this had been absent the whole design would have needed a different transport, and the answer would
have been "no, not without rebuilding the foundation". It is present, so the core is portable.

One catch, and it is only a catch if you do not know about it: the module is called **`HttpServer`** in
4.27 and **`HTTPServer`** in UE5. A casing difference in the `.Build.cs` string, nothing more.

## 2. Every module dependency exists

MifBridge names **38** modules across its public and private dependency lists. Checked against every
`*.Build.cs` in 4.27's `Source` and `Plugins` trees (1,242 modules):

**38 of 38 present**, with the one casing change above.

That includes the ones worth worrying about — `UMGEditor`, `MovieScene`, `MovieSceneTracks`,
`BlueprintGraph`, `AnimGraph`, `Landscape`, `AssetTools`, `EnhancedInput`, `InputBlueprintNodes`.

## 3. The UE5-only API surface, counted

| API | Uses | Files | 4.27 status |
|---|---|---|---|
| `UEditorActorSubsystem` | 14 | 2 | **Absent.** Its predecessor `UEditorLevelLibrary` is in the `EditorScriptingUtilities` plugin — Epic moved that functionality *into* a subsystem for UE5, so this is a documented migration in reverse. |
| `TObjectPtr` | 13 | 3 | Absent — but it is a raw pointer wrapper. Mechanical. |
| `UAssetImportTask` | 11 | 4 | **Present**, at `Editor/UnrealEd/Public/AssetImportTask.h` rather than UE5's `Runtime/Engine/Classes/Factories/`. An include-path change. |
| `FTSTicker` | 10 | 4 | Named **`FTicker`** in 4.27. A rename. |
| `GetEditorSubsystem` | 5 | 3 | `EditorSubsystem` module exists in 4.27. |
| `UWorldPartition` | 0 | 0 | Absent in 4.27, and MifBridge does not use it. |
| `FMovieSceneDoubleChannel` | 0 | 0 | Not used — the UMG animation work uses `FMovieSceneFloatChannel`, which exists in 4.27. |

Roughly 53 call sites, concentrated in 8 files. None of it is architectural.

## 4. The one pervasive difference: Large World Coordinates

In 4.27, `FVector::X` is a **`float`**. In UE5 it is a **`double`**.

MifBridge passes locations around as `double` and serialises them through JSON. Most of that survives
as implicit conversion, but it is the one difference that is *everywhere* rather than in 8 files, and
it is the kind that compiles with warnings and loses precision quietly. It needs a deliberate pass,
not a find-and-replace.

---

## What this assessment does NOT establish

**Nobody has tried to compile it.** Everything above is header and module existence. What it cannot
measure is **API drift across six minor versions** of `UEdGraphSchema_K2`, `UK2Node_*`,
`FBlueprintEditorUtils`, `IAssetTools` and the rest — signatures that gained or lost parameters,
methods that were renamed, behaviour that changed underneath an unchanged signature.

That last category is the dangerous one, and this whole document cannot see it. This session alone
found three places where an unchanged-looking API did something other than what the code assumed
(`PostPlacedNewNode` on a timeline node, `LineTraceMultiByChannel`'s single blocking hit,
`DuplicateAsset`'s "no dialog" flag). A version jump multiplies that class of problem, and headers do
not reveal it.

**The honest next step** is not more reading. It is: copy the plugin, change the two names above
(`HttpServer`, `FTicker`), stub the `UEditorActorSubsystem` call sites, and **attempt a compile against
4.27**. The first error list is worth more than any amount of further static analysis, and it is a
day's work to obtain rather than a week's.

## A scoping opinion

Even if it compiles, "MifBridge on UE4" means maintaining two builds of a 46,784-line plugin against
engines that disagree about pointer types, coordinate precision and half the editor subsystems. That
is an ongoing tax, not a one-time cost.

Worth asking first: **which endpoints do you actually need on 4.27?** If it is the Blueprint graph
editing, that is the most portable part of the surface and the most valuable. If it is the level and
actor work, that is where `UEditorActorSubsystem` sits and where the port costs most. A subset
targeted at what you use would be a fraction of the work and carry a fraction of the maintenance.
