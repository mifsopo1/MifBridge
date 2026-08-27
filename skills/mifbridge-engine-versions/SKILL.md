---
name: mifbridge-engine-versions
description: Writing MifBridge code that compiles on both UE 5.3 and UE 5.7. The six ways an engine API differs between versions, four of which reading the headers cannot find, and the compile probe that settles it.
---

# Two engines, six kinds of difference

MifBridge is built on a **cooked UE 5.3.2 editor** (DDS2) and also runs on **stock UE 5.7** (Curfew).
Every handler has to compile on both.

The failure is always the same shape — a symbol that behaves differently between trees — but it
arrives from six directions, and **only the first two are findable by reading the headers.**

---

## A: 5.3 has it, 5.7 deleted it

The familiar one. You write against the editor in front of you, it compiles, the 5.7 build breaks.

| Symbol | What happened |
|---|---|
| `IAssetRegistry::GetAssetsByClass(FName, ...)` | deprecated in 5.3, **deleted** in 5.7. Pass `GetClassPathName()`. |
| `UObject::IsPendingKillOrUnreachable()` | gone in 5.7. Use `IsValid(Obj)`. |
| `UDataLayerSubsystem::GetDataLayerInstances` | deprecated 5.3. Use `UDataLayerManager`. |

**A 5.3 deprecation warning is a 5.7 build break.** Treat every one as an error.

## B: 5.7 has it, 5.3 never did

Easy to miss because *nothing warns you*. Verify an API by reading the 5.7 tree — or by remembering
how a subsystem works in a modern engine — and you can pick a member 5.3 never had. Nothing was
deprecated, so there is no notice. It compiles on 5.7 and fails outright on 5.3.

`UGameFeaturesSubsystem::GetPluginState` is the instructive case: it returns the exact state enum in
one call and is precisely what you want. It would not build on 5.3. The endpoint instead **derives**
state from four predicates that exist in both — and says so in its own response, so no caller mistakes
a derived answer for the engine's.

---

## The four that reading cannot find

### C: both have it, and 5.7 rejects it anyway

Identical, legal, unchanged code. The newer compiler is stricter.

```cpp
// C2445 on 5.7, fine on 5.3
UClass* C = Blueprint->ParentClass ? Blueprint->ParentClass : UObject::StaticClass();
```

`ParentClass` is a `TSubclassOf<UObject>`; the other branch is a raw `UClass*`. 5.7 will not pick a
common type. `.Get()` fixes it and is a no-op on 5.3.

Nothing to grep for. The only signal is a compiler that has not run.

### D: same name, DIFFERENT KIND of thing

```cpp
// HttpRequestHandler.h:19
5.3: typedef TFunction<bool(...)>  FHttpRequestHandler;
5.7: using FHttpRequestHandler = TDelegate<bool(...)>;
```

`TFunction` converts from a bare lambda. `TDelegate` needs `CreateLambda`. What makes it nasty is
where it does *not* appear: `IHttpRouter.h` — the header the error points at — is **byte-identical**
between the trees.

> If you shim this with a macro, **make it variadic**. A lambda's parameter list contains commas, so a
> single-parameter macro sees two arguments and expands to garbage that corrupts everything after it.
> One such mistake produced seven errors including an "illegal else without matching if" ninety lines
> above anything that had changed.

### E: same type, same name, DIFFERENT HEADER

```cpp
FStringOutputDevice   // 5.3: Containers/UnrealString.h, free via CoreMinimal
                      // 5.7: Misc/StringOutputDevice.h, no longer transitive
```

The include is **required on 5.7 and a fatal C1083 on 5.3**. Nothing deprecated, nothing deleted — a
type moved house. `FInstancedStruct` did the same: an **experimental plugin** in 5.3
(`Plugins/Experimental/StructUtils`), core in 5.7.

### F: same name, same arity, same return type, DIFFERENT PARAMETER TYPE

The sharpest, because every check short of a compiler passes it.

```cpp
5.3: int32 AddSolver(TSubclassOf<UIKRigSolver> InSolverClass) const;
5.7: int32 AddSolver(const FString InIKRigSolverType) const;
```

Grep finds it in both. A presence check passes. It cannot compile.

Underneath was an **architecture change**: UE 5.6 moved IK Rig solvers from `UObject` to `UStruct`.
`UIKRigSolver` survives only as a legacy loading shim.

> **Treat a renamed header in a newer engine as a question — *what moved, and why did it need a new
> name* — rather than a mechanical substitution.**

---

## The rule this reduces to

**Reading finds symbols that were deleted. It reliably misses symbols that changed shape.**

So:

1. **Verify every engine symbol in BOTH trees, and record the line numbers in the file.** Not one
   tree. Not the tree the editor in front of you happens to be.
2. **Grep tells you a member exists. It does not tell you which class owns it, or whether it is
   public.** Two `UWaterBodyComponent` members were attributed to `AWaterBody` this way, in a file
   whose own header cited this rule. Locate the declaration and its access specifier.
3. **Then run a compiler.**

```
python tools/make_engine_probe.py --engine "C:/Program Files/Epic Games/UE_5.7" --out <scratch> --build
```

It generates a throwaway one-module project, junctions the plugin **source** into it, and builds.
Run it before claiming an engine in `RELEASE_MANIFEST.json` — that row said "built" for weeks on the
strength of careful reading, and when someone finally compiled it, six real defects fell out in an
hour.

---

## Three traps in the tooling itself

- **`Build.bat` returns exit code 0 on a build that prints `Result: Failed`.** Grep the log for
  `Result: Failed` and for `error`, and check the binary's mtime moved.
- **UE compiles `C4668` — undefined macro in an `#if` — as an ERROR.** A `MIF_WITH_*` guard is only
  safe because `Build.cs` defines it on *both* branches. Adding a guard to a `.cpp` without adding the
  definition fails the build; it does not take the false branch.
- **Junction the plugin's `Source/` only, never its root.** UBT writes plugin output to
  `<Project>/Plugins/<Name>/Binaries`, so a root junction sends one engine's binaries into the other's
  folder. That replaced a working 5.3 DLL with a 5.7 one and reported success throughout.

## When a guard is unavoidable

`MifBridgeVersion.h` provides `MIF_ENGINE_AT_LEAST(Major, Minor)`, `MIF_ENGINE_BEFORE`, and
`MIF_ENGINE_5_7_PLUS`. **Use them sparingly** — a guarded branch is code only one build ever compiles.

Preference order:

1. Use an API present in both. Verify in both trees, record the line numbers.
2. Keep the common subset as the baseline and let the guard **add** to it. **Never let the two
   branches produce differently-shaped output** — if 5.3 returns one set of fields and 5.7 another,
   every caller has to branch on engine version and the bridge has exported its problem.
3. Only guard a whole feature out when it is impossible on the older engine, and make that build
   **refuse by name** rather than silently omit the endpoint.

Where the two engines genuinely disagree about a concept, **report which model answered** rather than
hiding it: `list_ik_solver_types` returns a `solverModel` field, and `describe_ik_retargeter` a
`runtimeProbeModel`, because a caller carrying an id between engines otherwise learns the difference
from a refusal.

## And one that is not a version problem at all

`MifBridgeReconstruct.cpp` needs `CompiledBlueprintReconstructor.h`, which exists **only in the DDS2
engine fork** — no stock Unreal of any version has it. From a 5.3 machine it looks like ordinary
editor code, because on that engine the header sits exactly where an engine header belongs.

It is behind `MIF_WITH_RECONSTRUCTOR`, and its refusal says that no newer engine will help. Unlike
every other `MIF_WITH_*`, that one cannot be enabled.
