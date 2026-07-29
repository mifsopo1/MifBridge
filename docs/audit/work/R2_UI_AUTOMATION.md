# R2 — A viable UI-automation escape hatch for MifBridge

**Scope.** Reaching editor affordances that have no callable API: a third-party plugin's toolbar
button, a custom editor window, a Details-panel row nobody exposed. Everything below is verified
against `D:/UE532/Engine/Source` (UE 5.3 source fork) with file:line, the export macro, and the
access specifier for every symbol. Anything not verified is in **§9 UNVERIFIED** and nowhere else.

**Read-only session.** No source was edited and no build was run.

---

## 0. Bottom line

There are three technically distinct routes. They are *not* equally good, and the one the question
names first is the worst of the three.

| # | Route | Reaches | Safety | Verdict |
|---|---|---|---|---|
| **A** | **Invoke the ACTION** — `UToolMenus` entries, `FUICommandList::TryExecuteAction`, `FTabManager::TryInvokeTab`, `GEditor->Exec` | Every menu/toolbar entry registered through ToolMenus, every command whose owning `FUICommandList` is reachable, every registered tab, every exec/console command | **High.** Synchronous, no input state touched, no cursor moved, no focus stolen. Fails closed with a structured error. | **Build this. It is the escape hatch.** |
| **B** | **Synthetic Slate input** — `FSlateApplication::ProcessKeyDownEvent` / `ProcessKeyUpEvent` | Anything driven by an `IInputProcessor` (this is how BlueprintAssist's ~150 commands actually run) and anything bound to the focused widget | **Medium.** No platform-application swap, no cursor warp, callable directly on the game thread. But it depends on focus, and modified chords are unreliable (§4.6). | **Build this second, behind the same request+poll shell.** |
| **C** | **AutomationDriver pixel clicking** | Only widgets somebody explicitly tagged with `FDriverMetaData::Id`, `.Tag(...)`, or a `<SType>` path that happens to be unique | **Low.** Warps the user's real OS mouse pointer, steals window focus, cannot run while the editor is minimised, deadlocks the bridge if used naively, and cannot address a single engine Details-panel row. | **Last resort, behind an explicit opt-in flag. Guardrails in §7.** |

**Honest conclusion, stated plainly:** pixel-clicking should stay a last resort behind an explicit
opt-in flag. Route C is not "the escape hatch with extra steps" — for the two motivating cases (a
third-party plugin's toolbar button, a custom editor window) Route A or B reaches them *better*, and
Route C mostly cannot reach them at all because nothing in the engine or in a typical plugin is
tagged with a driver Id (§4.4). Route C's remaining honest use is: a widget that a MifBridge-authored
or MifBridge-patched Slate widget has deliberately tagged, plus scroll/drag gestures that have no
action equivalent.

---

## 1. Correction to the brief's premise (verify this before designing on top of it)

The task statement says handlers are *"dispatched via `AsyncTask(ENamedThreads::GameThread,...)` from
the HTTP listener"* and run *"mid-frame"*. **That is not what the code does today**, and the current
comment says it was deliberately changed away from exactly that:

`D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private/MifBridgeServer.cpp:229-265`

```cpp
// --- Run the endpoint on the game thread, at a tick-safe point ----------
//
// Do NOT reach for AsyncTask(ENamedThreads::GameThread, ...) here. That enqueues onto the
// game thread's NAMED-THREAD task queue, which is pumped not only between frames but also
// from inside FTickTaskSequencer::ReleaseTickGroup() -> WaitUntilTasksComplete(): ...
// FHttpServerModule derives from FTSTickerObjectBase, so this handler is ALREADY on the game
// thread, called from FTSTicker::GetCoreTicker().Tick() - which FEngineLoop::Tick() runs
// after GEngine->Tick() has completed the entire world tick, outside every tick group.
    if (IsInGameThread())
    {
        TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
        MifBridge::RunEndpoint(Endpoint, InRef, Out);
        RunAndReply(OnComplete, Out);
        return true;
    }
```

Confirmed upstream: `D:/UE532/Engine/Source/Runtime/Online/HTTPServer/Public/HttpServerModule.h:23-25`

```cpp
class FHttpServerModule :
     public IModuleInterface
    ,public FTSTickerObjectBase
```

**Why this matters for this design, and it makes things worse not better:** the handler runs *inside*
`FTSTicker::GetCoreTicker().Tick()`. The AutomationDriver's step engine *also* schedules onto
`FTSTicker::GetCoreTicker()` (§4.3). So a handler that blocks waiting for a driver sequence is
blocking the very ticker that must advance it. The conclusion the brief reached is right; the stated
mechanism is stale and should be corrected in `docs/02_GOTCHAS.md` §8 when someone next touches it.

The rest of §8 is intact and still governs: a modal window stops the tick, the socket stops being
read, and every call times out with no response.

---

## 2. Route A — invoke the ACTION, not the pixel

### 2.1 Why this is the right primitive

Almost every "button" in the editor is a bound `FUIAction`. `FUIAction` is a plain struct with public
delegate members and public inline accessors:

`D:/UE532/Engine/Source/Runtime/Slate/Public/Framework/Commands/UIAction.h:36-51` (struct → default
public), `:124` and `:133`:

```cpp
struct FUIAction
{
    /** Holds a delegate that is executed when this action is activated. */
    FExecuteAction ExecuteAction;
    /** Holds a delegate that is executed when determining whether this action can execute. */
    FCanExecuteAction CanExecuteAction;
    ...
    bool CanExecute( ) const           // :124  public, inline, no export needed
    bool Execute( ) const              // :133  public, inline, no export needed
    bool IsBound( ) const              // :165
```

Executing that delegate is *the same code path* a mouse click ends in, minus the hit-testing,
minus the focus change, minus the cursor. There is no scenario where clicking the pixel is more
correct than executing the action; there are many where it is less correct.

### 2.2 Discovery half — enumerate what exists

Four independent registries, all publicly readable.

**(a) Every command in the editor, including third-party plugins' commands.**

`D:/UE532/Engine/Source/Runtime/Slate/Public/Framework/Commands/InputBindingManager.h`

```cpp
class FInputBindingManager                                                   // :25  NO class export macro
{
public:                                                                       // :27
    static SLATE_API FInputBindingManager& Get();                             // :32
    SLATE_API void GetKnownInputContexts( TArray< TSharedPtr<FBindingContext> >& OutInputContexts ) const;   // :45
    SLATE_API TSharedPtr<FBindingContext> GetContextByName( const FName& InContextName );                    // :50
    SLATE_API const TSharedPtr<FUICommandInfo> FindCommandInContext( const FName InBindingContext, const FName CommandName ) const;  // :101
    SLATE_API void GetCommandInfosFromContext( const FName InBindingContext, TArray< TSharedPtr<FUICommandInfo> >& OutCommandInfos ) const;  // :126
```

- Export-macro check: the **class** carries no export macro; every method we need carries
  `SLATE_API` individually. That is sufficient. (`private:` begins at `:209` — everything above is
  public.)
- Access check: all four are above `:209`, i.e. `public:` from `:27`.

Per-command metadata, all public:
`D:/UE532/Engine/Source/Runtime/Slate/Public/Framework/Commands/UICommandInfo.h` — `class
FUICommandInfo` at `:183`, `public:` at `:188`, `private:` at `:280`:

```cpp
SLATE_API const FText GetInputText() const;                                                     // :202
const TSharedRef<const FInputChord> GetActiveChord(const EMultipleKeyBindingIndex InChordIndex) const;  // :207 inline
const FText& GetLabel() const { return Label; }                                                 // :241 inline
const FText& GetDescription() const { return Description; }                                     // :244 inline
FName GetCommandName() const { return CommandName; }                                            // :253 inline
FName GetBindingContext() const { return BindingContext; }                                      // :256 inline
```

and `class FBindingContext` at `:83`, `public:` at `:86`:

```cpp
FName GetContextName() const { return ContextName; }        // :117 inline
const FText& GetContextDesc() const { return ContextDesc; } // :132 inline
```

**Worked example, live in this project.** BlueprintAssist registers a full `TCommands<>` context:
`D:/DDS2SDK/Game/Plugins/BlueprintAssist/Source/BlueprintAssist/Public/BlueprintAssistCommands.h:13-21`

```cpp
class FBACommandsImpl : public TCommands<FBACommandsImpl>
{
public:
    FBACommandsImpl()
        : TCommands<FBACommandsImpl>(
            TEXT("BlueprintAssistCommands"),
```

So `GetCommandInfosFromContext("BlueprintAssistCommands", Out)` enumerates every BA command with its
label, description and current chord — **without linking against BlueprintAssist at all.** That is
the discovery half working on exactly the class of thing the user asked about.

**(b) Every registered menu and toolbar.** `UToolMenus` keeps its registry in a private C++ member
that is nevertheless a `UPROPERTY`, so the reflection walker MifBridge already has
(`MifBridgeNodes5.cpp`, generic property get/set) can read it:

`D:/UE532/Engine/Source/Developer/ToolMenus/Public/ToolMenus.h`

```cpp
class TOOLMENUS_API UToolMenus : public UObject      // :47  whole-class export
{
public:                                              // :51
    static UToolMenus* Get();                        // :56
    UToolMenu* FindMenu(const FName Name);           // :132
    bool IsMenuRegistered(const FName Name) const;   // :140
    UToolMenu* GenerateMenu(const FName Name, const FToolMenuContext& InMenuContext);  // :204
    TArray<UToolMenu*> CollectHierarchy(const FName Name);                             // :216
private:                                             // :381
    UPROPERTY()
    TMap<FName, TObjectPtr<UToolMenu>> Menus;        // :390-391
};
```

C++ `private` does not gate reflection: `FindFProperty<FMapProperty>(UToolMenus::StaticClass(),
"Menus")` + `ContainerPtrToValuePtr` enumerates every registered menu name. There is **no public
enumerator** for `Menus` — this is the only route, and it is a legitimate one because the property is
reflected.

**(c) Menu contents.** `UToolMenu` at `ToolMenu.h:17` is `TOOLMENUS_API` (whole class). Access
specifiers matter here and this is one of the traps the project has been bitten by before:

```cpp
    FToolMenuSection* FindSection(const FName SectionName);   // :52   PUBLIC
    FName GetMenuName() const { return MenuName; }            // :56   PUBLIC
    virtual bool ContainsEntry(const FName InName) const;     // :73   PUBLIC
private:                                                      // :98
    bool FindEntry(const FName EntryName, int32&, int32&) const;  // :102  PRIVATE
    FToolMenuEntry* FindEntry(const FName EntryName);             // :104  PRIVATE  <-- exported, unusable
    const FToolMenuEntry* FindEntry(const FName EntryName) const; // :106  PRIVATE
public:                                                       // :116
    UPROPERTY()
    TArray<FToolMenuSection> Sections;                        // :161-162  PUBLIC
```

> **`UToolMenu::FindEntry` is PRIVATE** (only `friend class UToolMenus;` at `:96` can call it). Do not
> reach for it. Use `Sections` (public member, also a `UPROPERTY`) plus
> `FToolMenuSection::FindEntry`, which *is* public:
> `ToolMenuSection.h:25` `struct TOOLMENUS_API FToolMenuSection`, `public:` at `:29`,
> `FToolMenuEntry* FindEntry(const FName InName);` at `:59-60`, `private:` at `:62`,
> `UPROPERTY(...) TArray<FToolMenuEntry> Blocks;` at `:88-89` (public).

**(d) Tabs.** `FTabManager::HasTabSpawner` probes a candidate id:
`D:/UE532/Engine/Source/Runtime/Slate/Public/Framework/Docking/TabManager.h:981`, `SLATE_API`,
public (`public:` at `:786`):

```cpp
SLATE_API bool HasTabSpawner(FName TabId) const;
```

Implementation checks both registries —
`D:/UE532/Engine/Source/Runtime/Slate/Private/Framework/Docking/TabManager.cpp:1912-1922`:

```cpp
bool FTabManager::HasTabSpawner(FName TabId) const
{
    const TSharedRef<FTabSpawnerEntry>* Spawner = TabSpawner.Find(TabId);
    if (Spawner == nullptr) { Spawner = NomadTabSpawner->Find(TabId); }
    return Spawner != nullptr;
}
```

> **Trap, same class as the `ENGINE_API`-but-`protected` one this project already hit:** the actual
> registry `FTabSpawner TabSpawner;` is at `TabManager.h:1114` under `protected:` (`:1113`), and
> `SLATE_API bool HasTabSpawnerFor(FName TabId) const;` at `:1117` is **also protected despite
> carrying the export macro**. So tab ids **cannot be enumerated** from a plugin — only probed one at
> a time with the public `HasTabSpawner`. Design accordingly: the tab endpoint takes a name, it
> cannot offer a list.

**(e) Console/exec commands.**
`D:/UE532/Engine/Source/Runtime/Core/Public/HAL/IConsoleManager.h:984` and `:991`:

```cpp
virtual void ForEachConsoleObjectThatStartsWith( const FConsoleObjectVisitor& Visitor, const TCHAR* ThatStartsWith = TEXT("")) const = 0;
virtual void ForEachConsoleObjectThatContains(const FConsoleObjectVisitor& Visitor, const TCHAR* ThatContains) const = 0;
```

Pure virtuals on the `IConsoleManager` interface, public — reached via `IConsoleManager::Get()`.

### 2.3 Invoke half — what actually executes

**Commands via a command list.**
`D:/UE532/Engine/Source/Runtime/Slate/Public/Framework/Commands/UICommandList.h`, `class
FUICommandList` at `:14`, `public:` at `:17`, `protected:` at `:207` — so all of these are public:

```cpp
SLATE_API bool IsActionMapped( const TSharedPtr< const FUICommandInfo > InUICommandInfo ) const;   // :125
SLATE_API virtual bool ExecuteAction( const TSharedRef< const FUICommandInfo > InUICommandInfo ) const; // :133
SLATE_API bool CanExecuteAction( const TSharedRef< const FUICommandInfo > InUICommandInfo ) const;      // :140
SLATE_API bool TryExecuteAction( const TSharedRef< const FUICommandInfo > InUICommandInfo ) const;      // :148
SLATE_API const FUIAction* GetActionForCommand(TSharedPtr<const FUICommandInfo> Command) const;         // :198
```

`TryExecuteAction` is the correct one: it checks `CanExecute` first. `ExecuteAction`'s own comment at
`:129` says *"It is assumed at this point that CanExecuteAction was already checked"*.

**Getting hold of a live `FUICommandList`.** This is the real constraint on Route A and it must be
stated honestly. Three sources:

1. **The level editor's global list.**
   `D:/UE532/Engine/Source/Editor/LevelEditor/Public/LevelEditor.h:167`, `public:` at `:71`:
   ```cpp
   virtual const TSharedRef<FUICommandList> GetGlobalLevelEditorActions() const { return GlobalLevelEditorActions.ToSharedRef(); }
   ```
   > **Export-macro check with a twist worth recording.** `class FLevelEditorModule : public
   > IModuleInterface, ...` at `:68` carries **no export macro at all**, and neither do its methods.
   > It is still usable cross-module — 16 other engine modules call into it (Blutility, SceneOutliner,
   > Sequencer, UnrealEd, VREditor, WorldPartitionEditor…) — **because every method they call is
   > `virtual`**, so the call goes through the vtable and needs no imported symbol.
   > `GetGlobalLevelEditorActions` is both `virtual` and defined inline, so it is doubly safe. Any
   > *non-virtual, non-inline* member of `FLevelEditorModule` would be a link error. Do not assume the
   > whole class is reachable.
   >
   > MifBridge does not currently depend on the `LevelEditor` module
   > (`Source/MifBridge/MifBridge.Build.cs` — `ToolMenus`, `Slate`, `SlateCore`, `UnrealEd` are there;
   > `LevelEditor` is not). Adding it is a Build.cs change and therefore a build, which is out of
   > scope for this session.

2. **The command list attached to a ToolMenus entry.** `FToolMenuEntry` at `ToolMenuEntry.h:103` is
   `TOOLMENUS_API` (whole struct); `private:` starts at `:153`, so these two are public:
   ```cpp
   const FUIAction* GetActionForCommand(const FToolMenuContext& InContext, TSharedPtr<const FUICommandList>& OutCommandList) const;  // :138
   bool TryExecuteToolUIAction(const FToolMenuContext& InContext);                                                                   // :149
   ```
   Implementation of the first —
   `D:/UE532/Engine/Source/Developer/ToolMenus/Private/ToolMenuEntry.cpp:47-67`:
   ```cpp
   const FUIAction* FToolMenuEntry::GetActionForCommand(const FToolMenuContext& InContext, TSharedPtr<const FUICommandList>& OutCommandList) const
   {
       if (Command.IsValid())
       {
           if (CommandList.IsValid())
           {
               const FUIAction* Result = CommandList->GetActionForCommand(Command);
               if (Result) { OutCommandList = CommandList; return Result; }
           }
           else { return InContext.GetActionForCommand(Command, OutCommandList); }
       }
       return nullptr;
   }
   ```
   So a command-backed entry hands you both the action and its list. `Command` and `CommandList`
   themselves are private (`:218-219`) and are `TSharedPtr`, hence not `UPROPERTY` and **not**
   reachable by reflection either — the public accessor is the only door.

3. **`FToolMenuContext`.** `ToolMenuContext.h:32` `struct TOOLMENUS_API FToolMenuContext`, `public:`
   at `:35`, `private:` at `:92`:
   ```cpp
   void AppendCommandList(const TSharedRef<FUICommandList>& InCommandList);                                              // :68
   const FUIAction* GetActionForCommand(TSharedPtr<const FUICommandInfo> Command, TSharedPtr<const FUICommandList>& OutCommandList) const;  // :70
   void AddObject(UObject* InObject);                                                                                    // :78
   ```

**Non-command menu entries.** `FToolMenuEntry::TryExecuteToolUIAction` only covers entries whose
action is an `FToolUIAction` —
`D:/UE532/Engine/Source/Developer/ToolMenus/Private/ToolMenuEntry.cpp:281-297`:

```cpp
bool FToolMenuEntry::TryExecuteToolUIAction(const FToolMenuContext& InContext)
{
    bool bCanExecute = false;
    if (Action.GetToolUIAction() && Action.GetToolUIAction()->ExecuteAction.IsBound())
    { ... Action.GetToolUIAction()->ExecuteAction.Execute(InContext); }
    return bCanExecute;
}
```

`FToolUIActionChoice` (`ToolMenuDelegates.h:86`, `TOOLMENUS_API`, `public:` at `:88`) does expose
`const FUIAction* GetUIAction() const` at `:97` — but the entry's `Action` member is **private**
(`ToolMenuEntry.h:214`) and not a `UPROPERTY`, so entries built from a raw `FUIAction` lambda and
entries built from a `FToolMenuStringCommand` (`:216`, private; `UToolMenus::ExecuteStringCommand` at
`ToolMenus.h:356` is private static) **cannot be invoked through the public ToolMenus surface**. State
that as a documented limitation, and report it in the endpoint's error rather than silently doing
nothing.

**Tabs.** `TabManager.h:912`, `SLATE_API`, `virtual`, public:
```cpp
SLATE_API virtual TSharedPtr<SDockTab> TryInvokeTab(const FTabId& TabId, bool bInvokeAsInactive = false);
```
`FGlobalTabmanager` at `:1199`, `public:` at `:1201`, `static SLATE_API const TSharedRef<FGlobalTabmanager>& Get();` at `:1203`.
Also public: `SLATE_API TSharedPtr<SDockTab> FindExistingLiveTab(const FTabId& TabId) const;` at `:920`.

BlueprintAssist opens all three of its custom windows exactly this way —
`BlueprintAssistGlobalActions.cpp:147`, `BlueprintAssistModule.cpp:117`,
`BlueprintAssistToolbar.cpp:533/546/557` all call `FGlobalTabmanager::Get()->TryInvokeTab(...)`. So
**"a custom editor window" — the second motivating case — is a solved problem via Route A**, one
public call, no pixels.

**Exec commands.**
`D:/UE532/Engine/Source/Runtime/Engine/Classes/Engine/Engine.h:2224` (`public:` at `:2222`):
```cpp
ENGINE_API virtual bool Exec( UWorld* InWorld, const TCHAR* Cmd, FOutputDevice& Out=*GLog ) override;
```
> **Trap:** `ENGINE_API virtual bool Exec_Editor(...)` at `Engine.h:2229` is under `protected:`
> (`:2227`), and `UNREALED_API virtual bool Exec_Editor(...)` at
> `D:/UE532/Engine/Source/Editor/UnrealEd/Classes/Editor/EditorEngine.h:817` is under `protected:`
> (`:816`). Exported and unusable — the same shape as the `UClass::IsA` and the protected-`ENGINE_API`
> incidents. Call `GEditor->Exec(World, Cmd, Ar)` (the public one), never `Exec_Editor`.

### 2.4 What Route A does **not** reach

Stated up front so nobody discovers it in the field:

- **Legacy `FExtender`-based toolbar buttons that are never merged into a ToolMenu.** BlueprintAssist's
  asset-editor toolbar widget is built this way —
  `D:/DDS2SDK/Game/Plugins/BlueprintAssist/Source/BlueprintAssist/Private/BlueprintAssistToolbar.cpp:184-191`:
  ```cpp
  TSharedRef<const FExtensionBase> Extension = ToolbarExtender->AddToolBarExtension(
      "Asset", EExtensionHook::After, ToolkitCommands,
      FToolBarExtensionDelegate::CreateRaw(this, &FBAToolbar::ExtendToolbar));
  ...
  AssetEditorToolkit->AddToolbarExtender(ToolbarExtender);
  ```
  The extender is owned by the toolkit instance, not by `UToolMenus::Menus`.
- **Commands dispatched only by a plugin's own `IInputProcessor`.** BlueprintAssist routes every
  command through `FBAInputProcessor::ProcessCommandBindings(TSharedPtr<FUICommandList>, const
  FKeyEvent&)` (`BlueprintAssistInputProcessor.cpp:1111`) against command lists that are private
  members of BA singletons (`GlobalActions.GlobalCommands`, `FBAToolbar::Get().BlueprintAssistToolbarActions`,
  … — `:145-359`). MifBridge can enumerate the *commands* (§2.2a) but cannot obtain the *lists*.
  **This is exactly the gap Route B closes.**
- Entries with a raw `FUIAction` or a `FToolMenuStringCommand` (§2.3).

---

## 3. Route B — synthetic Slate input

The cheapest thing that reaches an `IInputProcessor`.

`D:/UE532/Engine/Source/Runtime/Slate/Public/Framework/Application/SlateApplication.h`, all under
`public:` at `:1159`, all `SLATE_API`:

```cpp
SLATE_API bool ProcessMouseButtonDownEvent(const TSharedPtr< FGenericWindow >& PlatformWindow, const FPointerEvent& InMouseEvent);  // :1178
SLATE_API bool ProcessMouseButtonUpEvent( const FPointerEvent& MouseEvent );   // :1186
SLATE_API bool ProcessKeyCharEvent( const FCharacterEvent& InCharacterEvent ); // :1211
SLATE_API bool ProcessKeyDownEvent( const FKeyEvent& InKeyEvent );             // :1219
SLATE_API bool ProcessKeyUpEvent( const FKeyEvent& InKeyEvent );               // :1227
```

Pre-processors get first refusal —
`D:/UE532/Engine/Source/Runtime/Slate/Private/Framework/Application/SlateApplication.cpp:4624-4648`:

```cpp
bool FSlateApplication::ProcessKeyDownEvent( const FKeyEvent& InKeyEvent )
{
    ...
    // Analog cursor gets first chance at the input
    if (InputPreProcessors.HandleKeyDownEvent(*this, InKeyEvent))   // :4645
    {
        return true;
    }
```

So injecting `FKeyEvent(EKeys::Tab, FModifierKeysState(), 0, false, 0, 0)` reaches BlueprintAssist's
`Open Blueprint Creation Menu` (default chord `EKeys::Tab`,
`BlueprintAssistCommands.cpp:11-16`) — no AutomationDriver, no platform-application swap, no cursor
movement, callable straight from the game thread.

`FKeyEvent`'s constructor is public —
`D:/UE532/Engine/Source/Runtime/SlateCore/Public/Input/Events.h:406-436` (`struct FKeyEvent`,
`public:` at `:410`):

```cpp
FKeyEvent( const FKey InKey, const FModifierKeysState& InModifierKeys, const uint32 InUserIndex,
           const bool bInIsRepeat, const uint32 InCharacterCode, const uint32 InKeyCode )
```

**The modifier trap — this is real and it also breaks Route C.** The `FModifierKeysState` you put in
the event is *not* what every consumer reads. BlueprintAssist reads the live application state
instead — `BlueprintAssistInputProcessor.cpp:1118-1123`:

```cpp
FModifierKeysState ModifierKeysState = FSlateApplication::Get().GetModifierKeys();
const FInputChord CheckChord(KeyEvent.GetKey(), EModifierKey::FromBools(
    ModifierKeysState.IsControlDown(), ModifierKeysState.IsAltDown(),
    ModifierKeysState.IsShiftDown(), ModifierKeysState.IsCommandDown()));
```

and `FSlateApplication::GetModifierKeys()` goes straight to the platform —
`SlateApplication.cpp:3034-3037`:

```cpp
FModifierKeysState FSlateApplication::GetModifierKeys() const
{
    return PlatformApplication->GetModifierKeys();
}
```

**Consequence:** a synthetic `Ctrl+H` is evaluated by BA as bare `H`. Unmodified chords work;
modified chords do not, for any consumer written this way. Report this as a hard capability boundary
on the endpoint (`modifiers: "unreliable"`) rather than pretending otherwise. See §4.6 for why Route
C does not fix it either.

---

## 4. Route C — the AutomationDriver, examined properly

### 4.1 The module exists and is built

- Source: `D:/UE532/Engine/Source/Developer/AutomationDriver/` (24 headers/sources listed).
- Binary: `D:/UE532/Engine/Binaries/Win64/UnrealEditor-AutomationDriver.dll` — present.
- `AutomationDriver.Build.cs` — everything is in `PrivateDependencyModuleNames` (`Core`,
  `CoreUObject`, `ApplicationCore`, `InputCore`, `Json`, `Slate`, `SlateCore`); there is no
  `PublicDependencyModuleNames` block at all. A consumer adds `"AutomationDriver"` to its own
  `PrivateDependencyModuleNames`. The only in-tree consumer is
  `D:/UE532/Engine/Plugins/Tests/AutomationDriverTests/Source/AutomationDriverTests/AutomationDriverTests.Build.cs`.

**Export macros and access, all verified:**

| Symbol | File:line | Export | Access |
|---|---|---|---|
| `class AUTOMATIONDRIVER_API IAutomationDriverModule : public IModuleInterface` | `Public/IAutomationDriverModule.h:11-12` | class-wide | `public:` `:14` |
| `static inline IAutomationDriverModule& Get()` | `:16-19` | header-inline | public |
| `virtual TSharedRef<IAutomationDriver, ESPMode::ThreadSafe> CreateDriver() const = 0;` | `:24` | pure virtual | public |
| `virtual bool IsEnabled() const = 0;` | `:44` | pure virtual | public |
| `virtual void Enable() = 0;` / `virtual void Disable() = 0;` | `:52` / `:59` | pure virtual | public |
| `class IAutomationDriver` | `Public/IAutomationDriver.h:82` | **none** — pure-abstract, vtable only | `public:` `:84` |
| `class IDriverSequence` / `virtual bool Perform() = 0;` | `Public/IDriverSequence.h:871` / `:883` | **none** | `public:` `:873` |
| `class IActionSequence` (Click/Type/Focus/Scroll…) | `Public/IDriverSequence.h:449` | **none** | public |
| `class IDriverElement` / `IElementLocator` | `Public/IDriverElement.h:378` / `Public/IElementLocator.h:12` | **none** | public |
| `class AUTOMATIONDRIVER_API By` | `Public/LocateBy.h:16` | class-wide | `public:` `:18` |
| `FAutomationDriverPtr` / `FDriverSequenceRef` typedefs | `Public/AutomationDriverTypeDefs.h:12-21` | n/a | n/a |
| `class AUTOMATIONDRIVER_API Until` | `Public/WaitUntil.h:96` | class-wide | public |
| `class FDriverConfiguration` | `Public/DriverConfiguration.h:10` | **none** — all members public data, ctor inline | public |

The unexported interface classes are fine: they are pure-abstract with no out-of-line definitions, so
only the vtable (produced by the module) is ever needed.

Defaults that govern behaviour — `Public/DriverConfiguration.h:26-29`:
```cpp
FDriverConfiguration()
    : ImplicitWait(FTimespan::FromSeconds(3))
    , ExecutionSpeedMultiplier(1.0)
```

### 4.2 `Enable()` does **not** block physical input — the header comment is wrong for 5.3

The doc comment says it does — `Public/IAutomationDriverModule.h:47-51`:

> *"Enabling the automation driver module causes most traditional input messages from the platform to
> stop being received, and instead only input simulated via an actual automation driver is received."*

The implementation does the opposite —
`Private/AutomationDriverModule.cpp:49-68`:

```cpp
virtual void Enable() override
{
    if (IsEnabled()) { return; }
    RealApplication = FSlateApplication::Get().GetPlatformApplication();
    RealMessageHandler = RealApplication->GetMessageHandler();
    AutomatedApplication = FAutomatedApplicationFactory::Create(
        RealApplication.ToSharedRef(), FPassThroughMessageHandlerFactoryFactory::Create());
    if (AutomatedApplication.IsValid())
    {
        FSlateApplication::Get().SetPlatformApplication(AutomatedApplication.ToSharedRef());
        AutomatedApplication->AllowPlatformMessageHandling();      // :66  <-- pass-through ON
    }
}
```

and `AllowPlatformMessageHandling()` turns pass-through **on** for both the message handler and the
cursor — `Private/AutomatedApplication.cpp:178-189`:

```cpp
virtual void AllowPlatformMessageHandling() override
{
    if (PassThroughMessageHandler.IsValid()) { PassThroughMessageHandler->SetAllowMessageHandling(true); }
    if (AutomatedCursor.IsValid())           { AutomatedCursor->SetAllowMessageHandling(true); }
}
```

**What that means for a user whose editor is open — concretely:**

1. **Their keyboard and mouse keep working.** Every `FPassThroughMessageHandlerImpl` override
   forwards to the real handler while `bAllowMessageHandling` is true
   (`Private/PassThroughMessageHandler.cpp:33-41`, `:43-51`, `:71-79`, …). So the user and the driver
   are both driving the editor at the same time, and the user can defeat any sequence by moving the
   mouse.
2. **The user's physical OS cursor gets warped.** The driver moves the cursor via
   `Application->Cursor->SetPosition(...)` (`Private/DriverSequence.cpp:825`, `:894`, `:907`), and
   `FAutomatedCursor::SetPosition` forwards to the real cursor while pass-through is on —
   `Private/AutomatedApplication.cpp:33-44`:
   ```cpp
   virtual void SetPosition(const int32 X, const int32 Y) override
   {
       FakePosition = FVector2D(X, Y);
       if (bAllowMessageHandling)
       {
           if (RealCursor.IsValid()) { RealCursor->SetPosition(X, Y); }
       }
   }
   ```
   The mouse pointer physically jumps on the user's desk.
3. **The only way to turn pass-through off is a physical ScrollLock press.** There is no public API
   for it: `DisablePlatformMessageHandling()` is declared on `FAutomatedApplication`
   (`Private/AutomatedApplication.h:20`) which is a **Private** header and unexported, and the
   module's `Disable()` calls it only as part of tearing the whole thing down
   (`AutomationDriverModule.cpp:77`). What remains is a hard-coded hotkey —
   `Private/PassThroughMessageHandler.cpp:53-69`:
   ```cpp
   virtual bool OnKeyUp(const int32 KeyCode, const uint32 CharacterCode, const bool IsRepeat) override
   {
       const FKey Key = FInputKeyManager::Get().GetKeyFromCodes(KeyCode, CharacterCode);
       if (Key == EKeys::ScrollLock)
       {
           // Allow scroll lock to toggle whether platform input can be processed by the application
           bAllowMessageHandling = !bAllowMessageHandling;
       }
   ```
   This is genuinely useful as a **human panic switch** (§7) — but it also means the user can silently
   put the driver into a state where sequences abort (§4.5), and can do so by fat-fingering ScrollLock.
4. **`Enable()` swaps `FSlateApplication`'s platform application process-wide.** It is not scoped to
   MifBridge. Anything that captured `GetPlatformApplication()` earlier now holds a stale pointer.
   That is why §7 requires Enable/Disable to be bracketed tightly around one sequence, never left on.

### 4.3 `Perform()` blocks, and inside a MifBridge handler it deadlocks — proof

The synchronous `IDriverSequence::Perform()` bottoms out in a blocking future wait —
`Private/DriverSequence.cpp:1881-1884` → `:1835-1838`:

```cpp
    virtual bool Perform()                       // FDriverSequence :1881
    {
        return ActionSequence->Perform();
    }
...
    bool Perform()                               // FActionSequence :1835
    {
        return ActionSequence->Perform().GetFuture().Get();
    }
```

`TFutureBase::Wait()`/`TFuture::Get()` are `public` blocking calls
(`D:/UE532/Engine/Source/Runtime/Core/Public/Async/Future.h:211 public:`, `:243 void Wait() const`,
`:417 ResultType Get() const`; `TAsyncResult::GetFuture()` public at
`Async/AsyncResult.h:78 public:`, `:85`).

That promise is fulfilled **only** by the step engine, which runs on the core ticker —
`Private/StepExecutor.cpp:57-80`:

```cpp
virtual TAsyncResult<bool> Execute() override
{
    check(!Promise.IsValid());
    CurrentStepIndex = 0;
    TWeakPtr<FStepExecutor, ESPMode::ThreadSafe> LocalWeakThis(SharedThis(this));
    AsyncTask( ENamedThreads::GameThread,
        [LocalWeakThis]() {
            TSharedPtr<FStepExecutor, ESPMode::ThreadSafe> Executor = LocalWeakThis.Pin();
            if (Executor.IsValid())
            {
                const int32 StepIndex = 0;
                FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateThreadSafeSP(Executor.ToSharedRef(), &FStepExecutor::ExecuteStep, StepIndex), 0);
            }
        } );
    Promise = MakeShareable(new TPromise<bool>());
    return TAsyncResult<bool>(Promise->GetFuture(), nullptr, nullptr);
}
```

and each step re-arms itself for a **later** tick — `Private/StepExecutor.cpp:101-153`:

```cpp
bool FStepExecutor::ExecuteStep(float Delta, int32 StepIndex)
{
    check(IsInGameThread());                                                    // :105
    ...
    float Delay = FMath::Max(SMALL_NUMBER, (Milliseconds / 1000) * Configuration->ExecutionSpeedMultiplier);   // :142
    ...
    FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateThreadSafeSP(this, &FStepExecutor::ExecuteStep, StepIndex), Delay);   // :151
    return false;
}
```

Now line up the ticker semantics —
`D:/UE532/Engine/Source/Runtime/Core/Private/Containers/Ticker.cpp:13-18`:

```cpp
FTSTicker::FDelegateHandle FTSTicker::AddTicker(const FTickerDelegate& InDelegate, float InDelay)
{
    FElementPtr NewElement{ new FElement{ CurrentTime + InDelay, InDelay, InDelegate } };
    AddedElements.Enqueue(NewElement);
    return NewElement;
}
```

and `:82-84`, `:103`, `:127-129`:

```cpp
    // ticking delegates can add more tickers that must be executed in the same tick call to be backward compatible with the old
    // implementation. keep transfering new tickers to the main list and executing them
    do {
        for (; ElementIdx < Elements.Num(); ++ElementIdx) {
            ...
            if (Element->FireTime > CurrentTime) { ClearExecutionFlag(Element); TickedElements.Add(MoveTemp(Element)); continue; }   // :103
    ...
        // See if there were new elements added while ticking. If so, tick them this frame as well
        PumpAddedElementsQueue();                                                                                                   // :128
    } while (ElementIdx < Elements.Num());
```

**Therefore:**

- `Delay = FMath::Max(SMALL_NUMBER, …)` is strictly > 0, so `FireTime > CurrentTime` and **step N+1
  cannot run in the same `FTSTicker::Tick()` pass as step N.** It needs the *next frame*. (This is by
  design and is why the driver works at all: Slate ticks in between, so hover states and geometry
  update.)
- A MifBridge handler runs *inside* `FTSTicker::GetCoreTicker().Tick()` (§1). If it calls
  `Perform()`, it blocks the game thread inside that tick. The next frame never arrives.
- Worse, it never even reaches step 0: `Execute()` posts an `AsyncTask(ENamedThreads::GameThread, …)`
  to add the first ticker, and the named-thread task queue is not pumped while the game thread is
  parked in `TFuture::Get()`. **Deadlock before the first click.** The editor stops rendering, the
  HTTP socket stops being read, and the failure is indistinguishable from the modal-window failure in
  `docs/02_GOTCHAS.md` §8.

**The engine's own tests confirm the intended usage.** Every single case in
`D:/UE532/Engine/Plugins/Tests/AutomationDriverTests/Source/AutomationDriverTests/Private/AutomationDriver.spec.cpp`
is declared `EAsyncExecution::ThreadPool` — e.g. `:80`, `:85`, `:92`, `:100`, `:107` — i.e. the
synchronous driver API is only ever called **from a worker thread**, never the game thread. `Enable()`
itself is called from `BeforeEach` at `:42`, on the test thread.

**Design consequence, non-negotiable:** MifBridge must use `IAsyncAutomationDriver` /
`IAsyncDriverSequence` (`Public/IAutomationDriver.h:20`, `Public/IDriverSequence.h:434`, `Perform()`
returns `TAsyncResult<bool>` at `:446`), kick it from the handler, return an `opId`, and poll
`GetFuture().IsReady()` (`Future.h:219`, public) from a ticker across frames. Never `IAutomationDriver`.

### 4.4 How elements are located — and why engine Details rows are unreachable

`By::Id` is sugar over the path locator — `Private/LocateBy.cpp:29-32`:

```cpp
TSharedRef<IElementLocator, ESPMode::ThreadSafe> By::Id(const FString& Value)
{
    return FSlateWidgetLocatorByPathFactory::Create(TEXT("#") + Value);
}
```

and `#`-prefixed segments match **only** `FDriverIdMetaData` —
`Private/Locators/SlateWidgetLocatorByPath.cpp:36-54` and `:295-309`:

```cpp
virtual bool IsMatch(const TSharedRef<SWidget>& Widget) const override      // FIdMatcher
{
    const TArray<TSharedRef<FDriverIdMetaData>> AllIdMetaData = Widget->GetAllMetaData<FDriverIdMetaData>();
    ...
}
...
void AddMatcher(const FString& PathPiece)
{
    if (PathPiece[0] == TEXT('#'))      { Matchers.Add(MakeShareable(new FIdMatcher(*PathPiece.Right(PathPiece.Len() - 1)))); }
    else if (PathPiece[0] == TEXT('<')) { Matchers.Add(MakeShareable(new FTypeMatcher(*PathPiece.Mid(1, PathPiece.Len() - 2)))); }
    else                                { Matchers.Add(MakeShareable(new FTagMatcher(*PathPiece))); }
}
```

**The decisive finding.** `FDriverIdMetaData`
(`D:/UE532/Engine/Source/Runtime/Slate/Public/Framework/MetaData/DriverIdMetaData.h:6-17`) is applied
by exactly one thing in the whole tree. A ripgrep for `FDriverIdMetaData` over
`D:/UE532/Engine/Source/Editor` returns **no matches**; over `D:/UE532/Engine/Source/Runtime` it
returns only the type's own declaration and its factory
(`Runtime/Slate/Private/Framework/MetaData/DriverMetaData.cpp:8`). The only widgets that carry one are
the test-suite's own —
`D:/UE532/Engine/Plugins/Tests/AutomationDriverTests/.../SAutomationDriverSpecSuite.cpp:26,34,41,47,56,…`:

```cpp
AddMetadata(FDriverMetaData::Id("Suite"));
...
.AddMetaData(FDriverMetaData::Id("KeySequence"))
.Tag("Duplicate")
```

> **Answer to the question asked: there is no stable ID on ENGINE Details-panel rows. There is no
> stable ID on any engine editor widget at all.** `By::Id` is only usable against widgets that
> somebody deliberately tagged, and nobody in UnrealEd did.

The public way to add one, if MifBridge ever authors or patches a Slate widget:
`D:/UE532/Engine/Source/Runtime/Slate/Public/Framework/MetaData/DriverMetaData.h:20` —
`static SLATE_API TSharedRef<ISlateMetaData> Id(FName Tag);` (class `FDriverMetaData` at `:13`,
`public:` at `:15`).

What is left for engine widgets:

- **`.Tag(...)`** — `SWidget::GetTag()` is `SLATECORE_API virtual FName GetTag() const;` at
  `D:/UE532/Engine/Source/Runtime/SlateCore/Public/Widgets/SWidget.h:1478`, public (`public:` at
  `:1458`); backed by `SLATE_PRIVATE_ARGUMENT_FUNCTION(FName, Tag)` at
  `SlateCore/Public/Widgets/DeclarativeSyntaxSupport.h:699`. Sparsely used in the editor.
- **`<SType>` paths** — matches `SWidget::GetType()`. Works, but a Details panel contains dozens of
  identical `SPropertyValueWidget`/`STextBlock` instances, and…
- **Ambiguity is a hard failure, not a "pick the first".**
  `Private/DriverSequence.cpp:102-126`:
  ```cpp
  static FStepResult LocateElement(..., TSharedPtr<IApplicationElement>& OutElement)
  {
      TArray<TSharedRef<IApplicationElement>> Elements;
      ElementLocator->Locate(Elements);
      if (Elements.Num() > 1)
      {
          FAutomationDriverLogging::TooManyElementsFound(Elements);
          return FStep::Failed();
      }
      ...
  ```
  So `By::Path("<STextBlock>")` in a real editor window fails outright.

Search cost: each `Locate()` walks every visible window's arranged widget tree from scratch, filtered
to `EVisibility::Visible` (`SlateWidgetLocatorByPath.cpp:255 VisibilityFilter(EVisibility::Visible)`,
`:171-172 GetAllVisibleWindowsOrdered`, `:207-208 ArrangeChildren`), and this runs **once per step,
per frame, until the implicit wait expires**.

### 4.5 Focus, minimising, and occlusion

- **Minimised editor ⇒ nothing is findable.** The locator's root set is
  `FSlateApplication::Get().GetAllVisibleWindowsOrdered(Windows)`
  (`SlateWidgetLocatorByPath.cpp:171-172`), and that function excludes minimised windows —
  `D:/UE532/Engine/Source/Runtime/Slate/Private/Framework/Application/SlateApplication.cpp:3602-3612`:
  ```cpp
  void FSlateApplication::GetAllVisibleWindowsOrdered(TArray< TSharedRef<SWindow> >& OutWindows)
  {
      for( ... ) {
          TSharedRef<SWindow> CurrentWindow = *CurrentWindowIt;
          if ( CurrentWindow->IsVisible() && !CurrentWindow->IsWindowMinimized() )
          { GetAllVisibleChildWindows(OutWindows, CurrentWindow); }
      }
  }
  ```
  A minimised editor produces `CannotFindElement` after the 3 s implicit wait, then `FStep::Failed()`.
  **This is the single most likely field failure for an unattended run.**
- **Clicking steals window focus.** `InternalActivateWindow` is prepended to every click/double-click
  — `Private/DriverSequence.cpp:1112-1139`:
  ```cpp
  TSharedPtr<SWindow> ActiveWindow = FSlateApplication::Get().GetActiveTopLevelWindow();
  if (!ActiveWindow.IsValid() || ActiveWindow->GetNativeWindow() != Window)
  {
      Window->SetWindowFocus();
  }
  ```
  The editor window is raised in front of whatever the user was doing.
- **Occluded targets fail.** A click requires the element to be genuinely hovered and hit-testable —
  `Private/DriverSequence.cpp:1039-1048`:
  ```cpp
  if (!Element->IsHovered())
  {
      if (TotalProcessTime >= AsyncDriver->GetConfiguration()->ImplicitWait)
      { FAutomationDriverLogging::CannotClickUnhoveredElement(ElementLocator); return FStep::Failed(); }
      return FStep::Wait(1);
  }
  ```
  and `IsVisible`/`IsInteractable` both re-run `FSlateApplication::Get().LocateWindowUnderMouse(...)`
  and require the target to be in the resulting path
  (`Private/SlateWidgetElement.cpp:106-143`, `:145-182`; `IsHovered` at `:371-375`). A tooltip, a
  popup, or a partially-scrolled row ⇒ failure.
- **A ScrollLock press mid-sequence silently ends the sequence as "succeeded".**
  `Private/StepExecutor.cpp:113-119`:
  ```cpp
  if ((StepIndex > 0 && !Steps.IsValidIndex(StepIndex)) || !Application->IsHandlingMessages())
  {
      Promise->SetValue(true);      // <-- true, i.e. "done", not "aborted"
      Promise.Reset();
      ...
  ```
  The endpoint must therefore verify a post-condition itself and must not treat `Perform()==true` as
  proof the click happened (§5.4).

### 4.6 The driver does not fix the modifier problem either

`FAutomatedApplicationImpl::GetModifierKeys()` returns the faked state **only when pass-through is
off** — `Private/AutomatedApplication.cpp:278-286`:

```cpp
virtual FModifierKeysState GetModifierKeys() const override
{
    if (!PassThroughMessageHandler.IsValid() || !PassThroughMessageHandler->IsHandlingMessages())
    { return FakeModifierKeys; }
    return RealApplication->GetModifierKeys();
}
```

After `Enable()`, pass-through is **on** (§4.2), so `SetFakeModifierKeys` (set by
`FAsyncAutomationDriver::TrackPress`, `Private/AutomationDriver.cpp:105-120`) is ignored and
`FSlateApplication::GetModifierKeys()` reports the user's real, unmodified keyboard.
**`TypeChord(EKeys::LeftControl, 'H')` therefore does not deliver Ctrl+H to any consumer that reads
`FSlateApplication::Get().GetModifierKeys()` — which includes BlueprintAssist (§3).** It only works
after a human presses ScrollLock. Do not advertise chord support.

---

## 5. Endpoint design

Reuse the shape MifBridge already has, do not invent a second one. `MifBridgeStreaming.cpp:59-63`
already established it:

> *"A deferred mutation cannot put its result in its own HTTP response. Silently dropping it would
> reproduce the failure `docs/02_GOTCHAS.md` warns about ("Never silence a mutating call"), so every
> deferred verb returns an `opId` and records its outcome into a small ring that `list_sublevels`
> reports as `ops[]`. Poll until the entry for your opId has `completed:true`, then read its ok/error."*

with the ring at `MifBridgeStreaming.cpp:273-300` (capped at 16 entries) and the deferral primitive
at `MifBridgeWorld.cpp:143` / `MifBridgeStreaming.cpp:598` (`GEditor->GetTimerManager()->SetTimerForNextTick`).

### 5.1 Route A endpoints — synchronous, no state machine needed

Route A executes an `FUIAction` in-place. It needs no ticker, no poll, no timeout — **as long as the
action itself does not open a modal.** That caveat is the whole risk and §5.5 handles it.

```
list_editor_commands                                     [read-only bucket]
  in : { context?: string, filter?: string, includeUnbound?: bool }
  out: { ok, contexts: [ { context, description, commands: [
           { name, label, description, chord, chordText, hasAction, canExecute } ] } ] }
```
- `contexts` from `FInputBindingManager::Get().GetKnownInputContexts` (`InputBindingManager.h:45`);
  commands from `GetCommandInfosFromContext` (`:126`).
- `hasAction` / `canExecute` are only answerable when a command list is in hand (level-editor global
  list, or the entry's own). Report `null` when unknown — **never guess**.

```
list_editor_menus                                        [read-only bucket]
  in : { menu?: string, depth?: int }
  out: { ok, menus: [ { name, parent, type, sections: [ { name, entries: [
           { name, label, type, invokeKind: "command"|"tooluiaction"|"unreachable" } ] } ] } ] }
```
- Menu names by reflecting `UToolMenus::Menus` (`ToolMenus.h:390-391`).
- Contents via `UToolMenus::Get()->CollectHierarchy(Name)` (`:216`, public) then
  `Menu->Sections` (`ToolMenu.h:161-162`, public) → `Section.Blocks` (`ToolMenuSection.h:88-89`).
- **Prefer `CollectHierarchy` over `GenerateMenu` for listing.** `GenerateMenu` allocates a new
  `UToolMenu` UObject and runs the assembly path —
  `D:/UE532/Engine/Source/Developer/ToolMenus/Private/ToolMenus.cpp:1881-1901`:
  ```cpp
  UToolMenu* UToolMenus::GenerateMenu(const FName Name, const FToolMenuContext& InMenuContext)
  { return GenerateMenuFromHierarchy(CollectHierarchy(Name), InMenuContext); }
  ...
  UToolMenu* GeneratedMenu = NewToolMenuObject(FName(TEXT("GeneratedMenuFromHierarchy")), NAME_None);
  ... AssembleMenuHierarchy(GeneratedMenu, Hierarchy);
  ```
  which fires third-party dynamic-section construct delegates. Listing must not have side effects;
  invoking may.
- `invokeKind: "unreachable"` is the honest label for raw-`FUIAction` and string-command entries
  (§2.3).

```
invoke_editor_command                                    [transacted bucket]
  in : { context: string, command: string,
         commandList?: "levelEditor"|"menuEntry",
         menu?: string, section?: string, entry?: string,
         dryRun?: bool }
  out: { ok, invoked: bool, resolvedVia, context, command,
         canExecuteChecked: bool, canExecute: bool,
         label, note }
```
Resolution order, each step failing closed with a distinct error:
1. `FInputBindingManager::Get().FindCommandInContext(context, command)` (`:101`) → `FUICommandInfo`.
2. Find a list: if `menu`/`entry` given, `CollectHierarchy` → `Sections` → `FToolMenuSection::FindEntry`
   (`ToolMenuSection.h:59`) → `FToolMenuEntry::GetActionForCommand(Ctx, OutList)`
   (`ToolMenuEntry.h:138`). Else `FLevelEditorModule::GetGlobalLevelEditorActions()`
   (`LevelEditor.h:167`).
3. `List->CanExecuteAction(Cmd)` (`UICommandList.h:140`), then `List->TryExecuteAction(Cmd)` (`:148`).
4. If no command but the entry has an `FToolUIAction`: `Entry->TryExecuteToolUIAction(Ctx)`
   (`ToolMenuEntry.h:149`).
5. Otherwise: `ok:false, error:"entry has no reachable action (raw FUIAction or string command)"`.

`dryRun:true` performs 1–3 minus the final `TryExecuteAction` and reports `canExecute`. **The MCP
wrapper should default to dryRun for discovery.**

```
invoke_editor_tab                                        [transacted bucket]
  in : { tabId: string, asInactive?: bool }
  out: { ok, hasSpawner, invoked, alreadyOpen }
```
`FGlobalTabmanager::Get()->HasTabSpawner(tabId)` (`TabManager.h:981`) → refuse if false (there is no
enumeration, §2.2d) → `FindExistingLiveTab` (`:920`) for `alreadyOpen` →
`TryInvokeTab(FTabId(tabId), asInactive)` (`:912`).

```
run_editor_exec                                          [transacted bucket]
  in : { command: string }
  out: { ok, handled, output }
```
`GEditor->Exec(GEditor->GetEditorWorldContext().World(), *Cmd, Ar)` — `Engine.h:2224`, `ENGINE_API`,
**public**. Capture `Ar` as an `FStringOutputDevice`. Never `Exec_Editor` (protected, §2.3).
Pair with a discovery half over `IConsoleManager::Get().ForEachConsoleObjectThatStartsWith`
(`IConsoleManager.h:984`).

### 5.2 Route B endpoint — one frame, still worth deferring

```
send_editor_key                                          [transacted bucket]
  in : { key: string, userIndex?: int,
         modifiers?: { ctrl, alt, shift, cmd },   // best-effort, see note
         focusWidgetPath?: string }
  out: { ok, opId, deferred: true, pollWith: "ui_automation_status",
         note: "modifiers are advisory; consumers that read FSlateApplication::GetModifierKeys() will not see them" }
```
Implementation: `SetTimerForNextTick` → build `FKeyEvent` (`Events.h:429`) →
`FSlateApplication::Get().ProcessKeyDownEvent(Ev)` (`SlateApplication.h:1219`) → next tick →
`ProcessKeyUpEvent` (`:1227`). Two frames, reported through the op ring.

Deferring by one tick rather than running inline is deliberate: the key may open a menu, and doing
that from inside the core-ticker tick while our own HTTP frame is on the stack is the same hazard
class as the level-swap deferrals already documented in `MifBridgeStreaming.cpp:47-57`.

### 5.3 Route C endpoints — the request + poll state machine

```
ui_click                                                 [transacted, GATED]
  in : { enable: true,                       // explicit opt-in, required, no default
         locator: { by: "id"|"path"|"tag"|"type", value: string },
         action: "click"|"doubleClick"|"rightClick"|"moveTo"|"scrollTo"|"type",
         text?: string,
         timeoutMs?: int (default 15000, hard cap 60000),
         implicitWaitMs?: int (default 3000),
         restoreCursor?: bool (default true) }
  out: { ok, opId, deferred: true, pollWith: "ui_automation_status",
         warnings: [ ... ] }

ui_automation_status                                     [read-only bucket]
  in : { opId?: int }
  out: { ok, driverEnabled, busy, ops: [ {
           opId, phase, completed, ok, error,
           locator, action, elapsedMs,
           resolvedElement: { type, tag, ids[], readableLocation, absolutePos, size },
           matchCount, focusStolenFrom, cursorMovedFrom, cursorMovedTo,
           passThroughWasDisabled } ], pendingOps }

ui_automation_abort                                      [transacted bucket]
  in : { opId?: int }         // omitted = abort all
  out: { ok, aborted: [opId], driverDisabled }
```

**The state machine** (one `FTSTicker::GetCoreTicker()` ticker, or
`GEditor->GetTimerManager()->SetTimerForNextTick` re-armed each frame to match the existing house
style; the ticker is closer to where the driver itself lives):

| Phase | Runs on | Does |
|---|---|---|
| `queued` | handler frame | validate; refuse if `driverEnabled` already true from someone else; record op; return `opId` |
| `preflight` | tick N | `FSlateApplication::Get().GetAllVisibleWindowsOrdered` non-empty and the target window not minimised; snapshot cursor pos and active window; run the locator **once** and record `matchCount` — abort now if 0 or >1, before any input is faked |
| `enabling` | tick N+1 | `IAutomationDriverModule::Get().Enable()`; assert `IsEnabled()` |
| `running` | tick N+2 | build the `IAsyncDriverSequence`, call `Perform()`, keep the `TAsyncResult<bool>` |
| `awaiting` | every tick | `Result.GetFuture().IsReady()` (`Future.h:219`); also re-check the deadline |
| `verifying` | on ready | re-run the post-condition probe (§5.4) |
| `disabling` | next tick | `IAutomationDriverModule::Get().Disable()`; restore cursor if `restoreCursor` |
| `done` / `failed` / `timedOut` / `aborted` | — | write the ring entry, never overwrite an existing one |

**Hard timeout.** The `awaiting` phase compares `FPlatformTime::Seconds()` against a deadline stamped
at `running`. On expiry: go straight to `disabling`, record `timedOut`, and **do not** touch the
`TAsyncResult` — the ticker chain inside the driver may still fire. Hold the shared ref (the same
discipline `FMifPendingCall` uses at `MifBridgeServer.cpp:64-86`) so nothing is freed under it.

**Abort.** `ui_automation_abort` sets an abort flag the supervising ticker reads on its next tick;
the ticker then jumps to `disabling`. There is **no** way to cancel an in-flight
`FAsyncActionSequence` — `IAsyncDriverSequence` (`IDriverSequence.h:434-447`) has `Actions()` and
`Perform()` and nothing else. `Disable()` is the only lever, and it works *because* of
`StepExecutor.cpp:113` — once `IsHandlingMessages()` goes false the step chain stops. Report abort as
`aborted`, never as `ok`.

**Single-flight.** One driver op at a time, process-wide. `Enable()` mutates
`FSlateApplication`'s platform application globally (`AutomationDriverModule.cpp:56-67`), so two
concurrent ops would corrupt each other's save/restore. `ui_click` returns
`ok:false, error:"ui automation busy (opId N)"` rather than queueing.

### 5.4 Reporting what was actually clicked

`Perform()==true` is not proof (§4.5, `StepExecutor.cpp:115`). Every op record must carry:

- `matchCount` from the preflight `Locate()`.
- `resolvedElement.readableLocation` — `SWidget::GetReadableLocation()`
  (`SWidget.h:1471`, `SLATECORE_API`, public), which is `"BaseFileName(LineNumber)"` of the `SNew`.
  That is the single most useful field for a human reading the log: it names the source file that
  built the widget you hit.
- `resolvedElement.type` / `.tag` / `.ids[]` — the same fields `FSlateWidgetElement::ToDebugString()`
  assembles (`Private/SlateWidgetElement.cpp:28-83`).
- `cursorMovedFrom` / `cursorMovedTo` and `focusStolenFrom` — so the user can see what the bridge did
  to their desktop.
- `passThroughWasDisabled` — snapshot of whether ScrollLock had been pressed, since it changes the
  meaning of everything else.

### 5.5 The modal hazard, which none of these routes escapes

Invoking an action can open a modal dialog. `docs/02_GOTCHAS.md` §8 is unambiguous about what that
costs: the tick stops, the socket stops being read, every call times out, and *"in an unattended run
that means forever"*. `docs/audit/03_GAPS_AND_RISKS.md` §2 already holds the inventory of engine calls
that do this, and `MifBridgeStreaming.cpp:19-45` shows the house pattern — enumerate the modal
branches and pre-check so the engine's dialog branch is never entered.

**For `invoke_editor_command` that inventory cannot be built**, because the action is arbitrary
third-party code. So the mitigation has to be different in kind:

1. `invoke_editor_command` must default to `dryRun:true` in the MCP wrapper, so an agent exploring
   the command space never fires anything by accident.
2. Maintain a deny-list of known-modal command names in `MifBridgeHandlers.h` alongside the endpoint
   set, refused with a structured error. Seed it from `03_GAPS_AND_RISKS.md` §2.
3. Document in the endpoint's own `note` field that a hang after `invoke_editor_command` means a
   modal, and repeat the `Get-Process UnrealEditor | Select-Object Id,MainWindowTitle` recipe from
   §8. An op that never reports is the signature.

There is no way to make this safe from inside the process. Say so in the tool description.

### 5.6 Files that must stay in sync

Per `docs/00_ARCHITECTURE.md`, adding these endpoints touches, in this order:

1. `MifBridgeHandlers.h` — `MIF_DECL(list_editor_commands)` … (currently 193 `MIF_DECL`s)
2. `MifBridgeCommon.cpp` — matching `MIF_BIND` in `Handlers()`
3. `MifBridgeCommon.cpp` — `IsReadOnlyEndpoint` gets `list_editor_commands`, `list_editor_menus`,
   `ui_automation_status`; nothing here belongs in `IsSelfManagedEndpoint` (no full compile)
4. a new `MifBridgeUIAutomation.cpp`
5. `server.py` in `Eddie_v2/tools/ue5-mcp-bridge/` — the known-drifting one
6. `README.md` + `docs/02_GOTCHAS.md`

`MifBridge.Build.cs` would need `LevelEditor` (Route A step 2b) and, only if Route C is built,
`AutomationDriver`. Both are build-affecting and were not attempted.

---

## 6. What CANNOT be made safe

Stated plainly, as requested.

1. **A modal opened by an invoked action.** Not preventable in general (§5.5). Only detectable from
   outside the process.
2. **Chorded (modified) input, on either Route B or Route C.** `FSlateApplication::GetModifierKeys()`
   reads the real platform state (`SlateApplication.cpp:3034-3037`) and the driver's fake state is
   ignored while pass-through is on (`AutomatedApplication.cpp:278-286`). Any consumer written like
   BlueprintAssist's (`BlueprintAssistInputProcessor.cpp:1118`) sees the wrong chord. Do not ship
   chord support; ship single-key support and say why.
3. **Clicking an engine Details-panel row by identity.** No `FDriverIdMetaData` exists anywhere in
   `Engine/Source/Editor` (§4.4), and `<SType>` paths hit
   `TooManyElementsFound → FStep::Failed()` (`DriverSequence.cpp:107-111`). This request should be
   refused with an explanation, not attempted with a coordinate hack.
4. **Cancelling a driver sequence cleanly.** The only lever is `Disable()`, which tears down the
   platform-application swap under a running step chain. It works, but it is a teardown, not a cancel.
5. **Running any of Route C while the editor is minimised.** `GetAllVisibleWindowsOrdered` filters
   minimised windows (`SlateApplication.cpp:3607`). Preflight must refuse, not wait 3 s and fail.
6. **Guaranteeing the user does not fight the automation.** Pass-through stays on after `Enable()`
   (§4.2); real input keeps flowing.
7. **Enumerating tab ids.** `TabSpawner` and `HasTabSpawnerFor` are both `protected`
   (`TabManager.h:1113-1117`). Probe-only.
8. **Invoking raw-`FUIAction` and string-command ToolMenus entries.** No public accessor
   (`ToolMenuEntry.h:214`, `:216`, both private, neither reflected).

---

## 7. Guardrails if Route C is built anyway

The opt-in flag should be a real gate, not a boolean parameter that an agent will pass by reflex.

1. **Two-key gate.** A CVar (`mif.UIAutomation.Enabled`, default 0, set from the editor console by a
   human) **and** `enable:true` in the request body. Either alone refuses. The CVar means an agent
   cannot turn it on over the bridge.
2. **Never leave the driver enabled.** `Enable()` in `enabling`, `Disable()` in `disabling`, both in
   the same op. On timeout, on abort, and on any failure path. If `IsEnabled()` is still true when the
   ring entry is written, that is a bug and should be logged as one.
3. **Preflight refuses before faking anything:** editor minimised; zero or multiple locator matches;
   another op in flight; target window is not the active top-level window and `action` is a click
   (because `InternalActivateWindow` will raise it, `DriverSequence.cpp:1131-1135`).
4. **Hard cap the timeout at 60 s** regardless of what the caller asks for, and keep it well under
   `MifOffThreadTimeoutSeconds = 120.0f` (`MifBridgeServer.cpp:89`) so the HTTP layer is never the
   thing that gives up first.
5. **Restore the cursor.** Snapshot `FSlateApplication::Get().GetCursorPos()` in preflight; restore in
   `disabling` unless `restoreCursor:false`. The pointer physically moved on the user's desk
   (`AutomatedApplication.cpp:33-44`).
6. **Tell the user about ScrollLock.** It is the only in-editor panic switch
   (`PassThroughMessageHandler.cpp:57-61`) and it silently changes semantics. Put it in the endpoint
   note and in `02_GOTCHAS.md`.
7. **Log every op at `Log` verbosity, not `Verbose`.** Faking input into a user's editor is not a
   debug-level event.
8. **Refuse `type` actions containing anything that could be a destructive keystroke** unless the
   caller passes a second explicit flag. `IActionSequence::Type` synthesises real key events
   (`DriverSequence.cpp:1163-1180`) into whatever currently has focus.

---

## 8. Ranking, restated with the reasoning

| Rank | Route | Usefulness | Safety | Why |
|---|---|---|---|---|
| 1 | `invoke_editor_tab` (`TryInvokeTab`) | High | High | One public call. Solves "open a custom editor window" completely. Verified in use by BlueprintAssist itself. |
| 2 | `list_editor_commands` + `invoke_editor_command` | High | High | Reaches every ToolMenus entry and every command whose list is reachable. Discovery half works for third-party plugins with zero coupling. Only risk is a modal, which is unavoidable everywhere. |
| 3 | `run_editor_exec` + console discovery | Medium-High | High | Large, already-documented surface; `GEditor->Exec` is public and synchronous. |
| 4 | `send_editor_key` (`ProcessKeyDownEvent`) | Medium-High | Medium | The only route to `IInputProcessor`-driven plugin commands — i.e. most of BlueprintAssist. Depends on focus; modifiers unreliable. |
| 5 | `ui_click` via AutomationDriver | Low | Low | Cannot address engine widgets by identity; warps the real cursor; steals focus; dies when minimised; deadlocks if used naively. Genuinely useful only for widgets we tagged ourselves and for scroll/drag gestures with no action equivalent. |

---

## 9. UNVERIFIED

Everything here is a gap I did not close in this session. None of it is relied on above.

1. **Whether `AssembleMenuHierarchy` can itself open a modal or create Slate widgets.** I read
   `GenerateMenu`/`GenerateMenuFromHierarchy` (`ToolMenus.cpp:1881-1901`) and confirmed it runs
   construct delegates, but I did not audit `AssembleMenuHierarchy`/`AssembleMenuSection` for
   widget-creating or dialog-opening paths. §5.1's recommendation to list via `CollectHierarchy` is
   the conservative choice precisely because of this gap.
2. **How many editor widgets actually set `.Tag(...)`.** I verified `SWidget::GetTag()` exists and is
   public/exported, and that `FDriverIdMetaData` is unused in the editor, but I did not count `.Tag(`
   call sites across `Engine/Source/Editor` (the tree-wide ripgrep timed out twice at 20 s and once at
   120 s). The claim in §4.4 is about `FDriverIdMetaData` only, which I did verify by exhaustive search
   of `Engine/Source/Editor` and `Engine/Source/Runtime`.
3. **Whether `FInputBindingManager::OnRegisterCommandList` (`InputBindingManager.h:204`) is actually
   broadcast by anything.** If it is, subscribing at module startup would give MifBridge a cache of
   live `FUICommandList`s and would substantially widen Route A. I found the declaration and
   `RegisterCommandList` (`:185`) but did not find the callers.
4. **Whether `UToolMenus::Menus` reflection actually enumerates cleanly at runtime.** The `UPROPERTY`
   is present (`ToolMenus.h:390-391`) and MifBridge has a generic property walker, but I did not run
   a live probe against it (the bridge answered `list_blueprints` and `pie_status`, so it is up, but
   no existing endpoint reads that property).
5. **Whether `FSlateApplication::ProcessKeyDownEvent` is safe to call from inside
   `FTSTicker::Tick()`.** §5.2 defers by one tick out of caution, matching the house pattern, rather
   than because I proved inline is unsafe.
6. **`FTabId` construction from a bare `FName`** — I verified `TryInvokeTab(const FTabId&, bool)` but
   did not read `FTabId`'s constructors.
7. **Whether the `LevelEditor` module dependency is legal for MifBridge's build graph.** It is an
   editor module and MifBridge is editor-only, so it should be, but this was not built or tested.
8. **`FToolMenuContext` default construction sufficiency.** Several editor menus expect specific
   context objects (`FindContext<T>()`); an empty context may make `CanExecute` return false or make
   a dynamic section throw. Not tested.

---

## 10. Citation index

Engine (`D:/UE532/Engine/Source/`):

- `Developer/AutomationDriver/AutomationDriver.Build.cs`
- `Developer/AutomationDriver/Public/IAutomationDriverModule.h:11,14,16,24,44,47-51,52,59`
- `Developer/AutomationDriver/Public/IAutomationDriver.h:20,82,84,90-132`
- `Developer/AutomationDriver/Public/IDriverSequence.h:434,446,449,871,873,878,883`
- `Developer/AutomationDriver/Public/LocateBy.h:16,18,57,139,315`
- `Developer/AutomationDriver/Public/DriverConfiguration.h:10,26-29`
- `Developer/AutomationDriver/Public/AutomationDriverTypeDefs.h:12-21`
- `Developer/AutomationDriver/Public/WaitUntil.h:16,62,79,96`
- `Developer/AutomationDriver/Private/AutomationDriverModule.cpp:49-68,77`
- `Developer/AutomationDriver/Private/AutomatedApplication.h:19-21`
- `Developer/AutomationDriver/Private/AutomatedApplication.cpp:33-44,137,178-189,191-202,204-217,278-286`
- `Developer/AutomationDriver/Private/PassThroughMessageHandler.cpp:16-24,33-41,53-69`
- `Developer/AutomationDriver/Private/StepExecutor.cpp:57-80,101-153,105,113-119,142,151`
- `Developer/AutomationDriver/Private/DriverSequence.cpp:102-126,107-111,729-736,825-826,894-895,1030-1048,1112-1139,1163-1180,1835-1838,1881-1884`
- `Developer/AutomationDriver/Private/SlateWidgetElement.cpp:28-83,85-93,106-143,145-182,371-375`
- `Developer/AutomationDriver/Private/LocateBy.cpp:29-32,69-72,109-121`
- `Developer/AutomationDriver/Private/Locators/SlateWidgetLocatorByPath.cpp:36-54,171-172,207-208,255,295-309`
- `Runtime/Slate/Public/Framework/MetaData/DriverMetaData.h:13,15,20`
- `Runtime/Slate/Public/Framework/MetaData/DriverIdMetaData.h:6-17`
- `Runtime/Slate/Private/Framework/MetaData/DriverMetaData.cpp:6-9`
- `Developer/ToolMenus/Public/ToolMenus.h:47,51,56,132,140,204,216,356,381,390-391`
- `Developer/ToolMenus/Public/ToolMenu.h:16-17,21,52,56,73,96,98,102-106,116,161-162`
- `Developer/ToolMenus/Public/ToolMenuSection.h:25,29,59-60,62,88-89`
- `Developer/ToolMenus/Public/ToolMenuEntry.h:103,138,149,153,165-166,214,216,218-219`
- `Developer/ToolMenus/Public/ToolMenuContext.h:32,35,68-71,78,92`
- `Developer/ToolMenus/Public/ToolMenuDelegates.h:86-116,97,102`
- `Developer/ToolMenus/Private/ToolMenuEntry.cpp:47-67,281-297`
- `Developer/ToolMenus/Private/ToolMenus.cpp:1881-1901`
- `Runtime/Slate/Public/Framework/Commands/InputBindingManager.h:25,27,32,45,50,93,101,126,185,204,209`
- `Runtime/Slate/Public/Framework/Commands/UICommandList.h:14,17,125,129,133,140,148,198,207`
- `Runtime/Slate/Public/Framework/Commands/UICommandInfo.h:83,86,117,132,183,188,202,207,241,244,253,256,280`
- `Runtime/Slate/Public/Framework/Commands/UIAction.h:36,39,42,53,124,133,165`
- `Runtime/Slate/Public/Framework/Docking/TabManager.h:364,786,912,920,981,1113,1114,1117,1199,1201,1203`
- `Runtime/Slate/Private/Framework/Docking/TabManager.cpp:1912-1922`
- `Runtime/Slate/Public/Framework/Application/SlateApplication.h:1159,1178,1186,1211,1219,1227`
- `Runtime/Slate/Private/Framework/Application/SlateApplication.cpp:3034-3037,3602-3623,4624-4648`
- `Runtime/SlateCore/Public/Widgets/SWidget.h:1458,1471,1478`
- `Runtime/SlateCore/Public/Widgets/DeclarativeSyntaxSupport.h:673,699`
- `Runtime/SlateCore/Public/Input/Events.h:406,410,429-436`
- `Runtime/Engine/Classes/Engine/Engine.h:2222,2224,2227,2229`
- `Editor/UnrealEd/Classes/Editor/EditorEngine.h:816,817`
- `Editor/LevelEditor/Public/LevelEditor.h:68,71,135,167,191`
- `Runtime/Core/Public/HAL/IConsoleManager.h:984,991`
- `Runtime/Core/Public/Containers/Ticker.h:45,90,93,113`
- `Runtime/Core/Private/Containers/Ticker.cpp:13-18,50-133,71,82-84,103,127-129`
- `Runtime/Core/Public/Async/Future.h:209,211,219,243,417`
- `Runtime/Core/Public/Async/AsyncResult.h:78,85`
- `Runtime/Online/HTTPServer/Public/HttpServerModule.h:23-25`
- `D:/UE532/Engine/Plugins/Tests/AutomationDriverTests/Source/AutomationDriverTests/AutomationDriverTests.Build.cs`
- `D:/UE532/Engine/Plugins/Tests/AutomationDriverTests/.../AutomationDriver.spec.cpp:28-33,36-76,80,85,92,100,107,115,122,129,134`
- `D:/UE532/Engine/Plugins/Tests/AutomationDriverTests/.../SAutomationDriverSpecSuite.cpp:26,34-35,41-42,47-48,56-58`
- `D:/UE532/Engine/Binaries/Win64/UnrealEditor-AutomationDriver.dll` (present)

Project:

- `D:/DDS2SDK/Game/Plugins/MifBridge/docs/00_ARCHITECTURE.md`
- `D:/DDS2SDK/Game/Plugins/MifBridge/docs/02_GOTCHAS.md` §8 (lines 492-515)
- `D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/MifBridge.Build.cs`
- `D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private/MifBridgeServer.cpp:64-90,229-265,245-248,276-301`
- `D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private/MifBridgeStreaming.cpp:19-63,273-300,479-497,598`
- `D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private/MifBridgeWorld.cpp:143,203`
- `D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private/MifBridgeCommon.cpp:351,438`
- `D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private/MifBridgeHandlers.h:307-310` (193 `MIF_DECL`)
- `D:/DDS2SDK/Game/Plugins/MifBridge/Source/MifBridge/Private/MifBridge.cpp:64,73-74,125`

Third-party (worked example):

- `D:/DDS2SDK/Game/Plugins/BlueprintAssist/Source/BlueprintAssist/Public/BlueprintAssistCommands.h:13-21`
- `D:/DDS2SDK/Game/Plugins/BlueprintAssist/Source/BlueprintAssist/Private/BlueprintAssistCommands.cpp:9-37`
- `D:/DDS2SDK/Game/Plugins/BlueprintAssist/Source/BlueprintAssist/Private/BlueprintAssistInputProcessor.cpp:109,145-359,1111-1150,1118-1123`
- `D:/DDS2SDK/Game/Plugins/BlueprintAssist/Source/BlueprintAssist/Private/BlueprintAssistToolbar.cpp:184-191,533,546,557`
- `D:/DDS2SDK/Game/Plugins/BlueprintAssist/Source/BlueprintAssist/Private/BlueprintAssistActions/BlueprintAssistGlobalActions.cpp:147`
- `D:/DDS2SDK/Game/Plugins/BlueprintAssist/Source/BlueprintAssist/Private/BlueprintAssistModule.cpp:117`

Live probes (read-only, `127.0.0.1:8791`, this session):

- `POST /api/list_blueprints` → `{"ok":true,"count":964,...}` — bridge alive
- `POST /api/pie_status` → `{"ok":true,"running":false,...,"editorWorld":"Untitled"}`
- `POST /api/session_info`, `POST /api/list_endpoints` → `route_handler_not_found` (endpoint names do
  not exist; not a bridge fault)
