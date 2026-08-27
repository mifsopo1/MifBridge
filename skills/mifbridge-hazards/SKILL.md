---
name: mifbridge-hazards
description: Read before driving a live Unreal editor through MifBridge. The calls that can terminate the editor, deadlock the bridge, or destroy unsaved work - each one found the hard way, with the guard that now exists and the reason it is not enough on its own.
---

# What can take the editor down

MifBridge drives a **live Unreal editor**. There is no sandbox and no undo for a crash. Everything
below has actually happened, on this project, to a running editor with work in it.

Most of these now have guards in the plugin. **The guards are not the point.** They cover the shapes
already found, and the reason to know the shapes is that the next one will look like these and will
not be guarded yet.

---

## 1. A modal dialog is a deadlock, not a dialog

Handlers run **synchronously, inline, on the game thread**, inside the HTTP ticker. A handler that
opens a modal dialog stops the game thread. The dialog waits for a click. The bridge stops answering
*every* endpoint — including the ones you would use to find out what happened.

From outside this is indistinguishable from a crash, except the editor is fine and there is a dialog
behind the main window waiting for someone to press OK.

**The trap is that "no dialog" flags do not mean no dialog.** Several engine APIs take a
`bShowDialog`/`bPromptUser` parameter and still raise one on a path that ignores it. There are also
**two separate dialog classes** with **two separate suppression mechanisms**, and silencing one does
nothing about the other.

The backstop: `RunEndpoint` now runs every handler under `GIsRunningUnattendedScript`. That
suppresses one class. It does not suppress the other.

> If the bridge stops answering but the editor is alive, look for a window behind the editor before
> assuming a crash. `docs/02_GOTCHAS.md` §8.

---

## 2. `CastChecked` terminates the process

Not an exception, not `ok:false` — the editor is gone, along with anything unsaved.

Any engine call reached through a cast you did not verify can do this. `Cast<T>` and a null check is
the whole fix, and the reason it keeps mattering is that engine code uses `CastChecked` internally on
arguments you supply.

---

## 3. A pin pointer is invalid after almost anything

`UEdGraphPin*` does not survive graph mutation. Specifically it does not survive **`BreakPinLinks`**,
which is not obvious, because breaking *links* sounds like it leaves the *pins* alone. It does not:
`PinConnectionListChanged` runs on both ends and can remove an orphaned pin outright.

Capture pin **identities** and re-resolve them, never hold the pointer across a call:

```cpp
for (const FMifPinRef& Ref : CapturePins(Matches))   // snapshot of identities
{
    if (UEdGraphPin* Live = ResolvePin(Ref))         // re-resolved every iteration
    { ... }
}
```

Four sites had this wrong at once. It is the single most repeated defect in this codebase.

---

## 4. Cooked assets keep runtime data and lose editor data

DDS2 is a **cooked** game. Its assets have had their editor-only data stripped. The runtime side is
intact, which is why they load and look normal.

Three that crash rather than fail:

- **Duplicating a cooked `UNiagaraSystem`** — refused by the plugin now, and the refusal is the
  feature.
- **`ReloadPackage` on a cooked Blueprint** — this is what "Force Reload" in the Content Browser does.
  It killed Andre's editor and it was not MifBridge that called it.
- **`UMaterialExpression` is `UCLASS(Optional)`** — the class may not exist at all in a cooked build,
  so reflection over material graphs has to handle its absence rather than assume it.

Reading a cooked asset's editor data does not return empty. It dereferences something that was
stripped.

> `docs/02_GOTCHAS.md` §6c.

---

## 5. A cancelled transaction undoes nothing

`FScopedTransaction` cancelled after a mutation does **not** roll the mutation back. It only stops the
entry appearing in the undo stack — which is worse than useless, because the change stays and the user
cannot Ctrl-Z it.

PM-007: a **failed** call permanently added a component override, and the failure path had "cancelled
the transaction" and believed that meant undone.

If an endpoint needs rollback, it has to undo its own work explicitly.

---

## 6. Engine "add" functions that allocate without initialising

PM-009: a public engine `Add...` returns a valid-looking object with uninitialised members, and the
crash lands two lines later somewhere unrelated. The engine's own UI code calls an initialise step
afterwards that is not part of the add.

**Mirror the engine's full path, not the one call that looks like the operation.** PM-010 is the same
lesson from the other side: the CREATE path was mirrored correctly and the DELETE path was not, and
the gap was a hard crash.

---

## 7. Two dispatchers, and a guard on one is not a guard

`RunEndpoint` is not the only place a handler is invoked. `batch` dispatches straight out of
`Handlers()` without recursing through it.

That was a complete bypass of the safety gate: `save_package` refused, `{"op":"save_package"}` inside
a batch ran. Fixed — but the general form matters more than the instance:

> **A control enforced at one choke point is only as good as the claim that there is one choke point.**

The audit is two greps, and it is in `docs/15_SAFETY_GATE_AND_JOURNAL.md`. Re-run it whenever a
dispatcher is added.

---

## 8. Endpoints that reach a save without being a save

The safety gate's unsafe list was built by asking *"does this endpoint mutate?"*. Two that honestly
answer **no** could still reach Save:

- `send_editor_key {key:"S", modifiers:{ctrl:true}}` — delivers a real key event to whatever has focus
- `invoke_editor_command {context:"LevelEditor", command:"Save"}` — executes any registered command

Both gated now. The question to ask of any new endpoint is not "does this write?" but **"can this
reach something that writes?"**

---

## The rule underneath all of these

**A mutation without a read-back is not done.**

Every silent-success bug on this project has the same shape: the endpoint reports what it *asked for*
rather than what *happened*. Two ways it shows up:

1. The endpoint never checks. An engine call that returns `void`, or whose `bool` was discarded.
2. **The endpoint checks, reports the correct numbers, and returns `ok:true` anyway.** This one is
   harder to see, because the response contains the truth — in a field nobody reads.

The second is worth stating as its own rule, because it survives contact with working code:

> **An endpoint that computes an outcome count must decide what that count MEANS**, rather than
> reporting it and leaving the caller to notice.

`delete_material_expression` returned correct `deleted` and `remaining` counts beside `ok:true` while
leaving three expressions behind. `spawn_many` reported `spawned:0, failed:50` and `ok:true`.
`add_simplified_collision` reported `added:0` for a primitive that was never created.

## And do not trust a build

`Build.bat` returns **exit code 0** on a build that prints `Result: Failed`. It also returns 0 when
Live Coding blocked it and it did nothing at all.

Grep the log for `Result: Failed` and for `error`, and check the binary's mtime moved. A "successful"
build that took under a second did nothing.
