# The safety gate, and the crash journal

Two subsystems added on 2026-08-26. They are unrelated in mechanism and related in purpose: one stops
the bridge doing something irreversible, the other records what it was doing when it died.

---

## 1. The safety gate

### What was wrong

"Do not save assets, do not start PIE, keep scratch under `/Game/_Mif*`" was enforced by the **agent's
discipline** plus `tools/scratch_confirm.py` on the Python side. Nothing in the C++ would refuse a
`save_package` call.

That is the one place this design depended on good behaviour rather than enforcing it. A different agent
session, or anyone else running the bridge, was subject to no guard at all.

### The trap — read this before changing anything here

MifBridge already classifies endpoints: `IsReadOnlyEndpoint` (`MifBridgeCommon.cpp:486`),
`IsSelfManagedEndpoint` (`:623`), `IsCompileHeavyEndpoint` (`:1078`). The obvious implementation is:

```cpp
if (!IsReadOnlyEndpoint(Endpoint)) { refuse; }     // WRONG
```

**That is backwards.** Those buckets answer "does this need an `FScopedTransaction`", not "does this
mutate anything". The read-only set contains:

| Endpoint | Line |
|---|---|
| `save_package`, `save_blueprint` | `MifBridgeCommon.cpp:489` |
| `trigger_cook` | `:492` |
| `start_pie`, `stop_pie` | `:559` |
| `compile`, `validate`, `run_console` | `:567` |
| `build_navmesh` | `:598` |

They are there because they manage their own transactions, not because they are harmless. A gate
written against that predicate would **permit every save and every PIE start** while refusing harmless
transacted edits — a safety feature that protects nothing and blocks everything.

So `MifBridgeSafety.cpp` carries a **third, independent classification** that shares no data with the
other three. `test_safety_gate.py` T632 asserts **both** directions — unsafe refused *and* ordinary work
still running — because either half alone passes while the gate is inverted.

### How it works

One `if` at the single dispatcher, `MifBridgeCommon.cpp:1223`, inside `RunEndpoint`. Placed after the
unknown-endpoint block so `didYouMean` still wins (being refused by the gate for an endpoint that does
not exist would be a confusing lie), and before everything else — no handler, no
`FEditorScriptExecutionGuard`, no `GIsRunningUnattendedScript` flip, no `FScopedTransaction`.

| Mode | Behaviour |
|---|---|
| `scratch` | **default.** Reads and writes run; unsafe operations refused. |
| `read` | as above today — the per-endpoint Read/Write split needed to make this fully meaningful is not built yet. |
| `full` | everything, i.e. the pre-2026-08-26 behaviour. |

Refused operations are those no path check can make safe — they persist to disk, take the editor loop,
execute outside the process, **or can reach one of those without doing it themselves**:

| Group | Endpoints |
|---|---|
| Persist to disk | `save_package`, `save_blueprint`, `save_dirty_packages`, `save_level_as`, `save_all` |
| Take the editor loop | `start_pie`, `stop_pie` |
| Execute outside the process | `run_console`, `exec_console`, `run_console_captured`, `trigger_cook` |
| Destroy or replace the working set | `new_level`, `load_level`, `quit_editor`, `restart_editor` |
| Long, unsupervised, writes into the project | `build_navmesh`, `import_asset` |
| **Reach a save without being one** | `send_editor_key`, `invoke_editor_command` |

**This list had drifted, and the last two groups are why it should not be read as authoritative.**

The originally documented sixteen were chosen by asking *"does this endpoint mutate?"*.
`send_editor_key` and `invoke_editor_command` both honestly answer **no** — one delivers a key event,
the other runs a registered UI command — and both reach Save. The question that matters is **"can this
reach something that does?"**

`run_console_captured` is a different failure again, and the more instructive one: it was **not** an
oversight in judgement. `MifBridge::RunEngineExec` is the single choke point onto `UEngine::Exec` and
the list named two of its three callers. The claim was true when it was written; the family grew a
member and the hand-maintained list did not.

So the authority moved out of this table and into the tests. **`test_safety_gate.py` T636 derives the
Exec-reaching endpoints from the source** — it finds every handler that calls `RunEngineExec` and
asserts each is refused. A fourth one fails the test the day it is written, with nobody needing to
remember this document exists.

Treat the table above as orientation. `MifBridgeSafety.cpp` is the source of truth, and `self_audit`
reports the live mode.

### Why it is an environment variable

`set_cvar` is a registered endpoint (`MifBridgeCommon.cpp:401`). A mode stored in a console variable
would be **unlockable by the very agent being gated** — the gate would be decorative. The same argument
rules out a `set_write_mode` endpoint.

The mode is read **once** from `MIF_BRIDGE_WRITE_MODE` at process start and is immutable thereafter.
Changing it means restarting the editor with a different environment — a deliberate act outside the
bridge's own reach. `test_safety_gate.py` T633 asserts both that no `set_write_mode` endpoint exists and
that `set_cvar` cannot move the mode.

```bash
MIF_BRIDGE_WRITE_MODE=full
```

`self_audit` reports `writeMode` and `safetyGateActive`, so a caller can know before it tries rather
than learning from a refusal.

### What is NOT covered

**The scratch-path rule is not enforced.** This is the unsafe-operation half only. A write to a
non-scratch path still succeeds. Enforcing it needs a per-endpoint Read/Write classification across all
285 binds plus a payload traversal, both filed as follow-up. A partial path check would be worse than
none, because it would read as coverage.

**`batch` bypasses the gate for its inner ops.** `batch` dispatches straight out of
`Handlers()`/`FindExternalHandler` (`MifBridgeNodes.cpp:2462`, `:2490`) and does not recurse through
`RunEndpoint`, so its inner ops do not cross the choke point. `batch` itself does. Also filed.

---

## 2. The crash journal

### What was wrong

`add_anim_node` crash-killed this editor (PM-013). There was no in-editor signal and no record of which
call did it, so the culprit had to be reconstructed from what had recently been attempted — which cost
far more than the fix.

The bridge emitted almost nothing per call. `LogMifBridge` carried lifecycle lines only; the sole
per-request logging was two `MIF_DBG` calls, both behind the `mif.BridgeDebug` CVar which **defaults to
false**.

### The one property that matters

**The record must be on disk before the handler runs.**

A journal written after a call completes describes every call *except the one that killed the process* —
the only one anybody wanted. So `start` is written and flushed before dispatch, `end` after. At the next
launch:

- a `start` with no matching `end` → **that endpoint was running when the process stopped**
- a `session` with no `shutdown` → **that editor died rather than being closed**

The diagnostic is an **absence**. That is why this cannot ride on `UE_LOG`: `FOutputDeviceFile` hands
lines to a background `FAsyncWriter` ring buffer and does not flush per line without `-FORCELOGFLUSH`,
losing exactly the tail you need.

`MifBridgeJournal.cpp` holds one `FArchive` open and calls `Flush()` per record — on Windows an
unconditional `FlushFileBuffers`. APIs verified in both engine trees: `CreateFileWriter` (5.3
`FileManager.h:97`, 5.7 `:96`), `FArchive::Flush` (5.3 `Archive.h:1725`, 5.7 `:1842`).

Output: `Saved/MifBridge/journal.jsonl`, one JSON record per line, append-only across runs. Session
records carry the PID because several editors can share a project and interleaved sessions would
otherwise be one unreadable stream.

### Reading it

```bash
python tools/mifwatch.py
```

Reports each recent session, its call count, its slowest call, and — the payoff — any call that started
and never returned. `--watch` keeps the editor alive and relaunches on death, reusing `mifaudit`'s
`ensure_editor` / `launch_editor` and respecting `SWEEP_LOCK` rather than re-deriving the launcher. Every
part of that launcher was learned from a failure, including the pipe leak that hung a regression for 17
minutes.

### Cost

One `FlushFileBuffers` per bridge call. `mif.BridgeJournal` turns it off, but it **defaults to on**
deliberately: a crash journal that must be enabled before the crash is off when it matters, which was
precisely the problem with `MIF_DBG`.

---

## 3. The scratch-path watch — which REAL assets a call dirtied

Added 2026-08-27. **Detection, not prevention**, and the response field is called `scratchClean`
rather than `scratchSafe` because that difference is real.

### What it closes

In a gated mode an agent cannot **save**, so a modified real asset is not permanent — until a human
presses Ctrl+S in the editor, at which point it silently is. Between those two moments nothing
anywhere said the asset had been touched. Now the response that touched it names it, in the same
call:

```json
{
  "scratchClean": false,
  "dirtiedRealPackages": ["/Game/Maps/IslaSombra"],
  "scratchWarning": "this call modified 1 package(s) OUTSIDE /Game/_Mif. Nothing was saved ..."
}
```

`scratchClean: true` is reported on a clean call too, so its **absence** is never mistaken for the
watch not running.

### Why it is not prevention

Blocking would need a per-endpoint Read/Write classification — roughly 300 mechanical `MIF_BIND`
edits, which also break `parity_check.py` and `make_release.py` since both match
`MIF_BIND\(([a-z_0-9]+)\)` and would silently report ZERO endpoints. That remains filed and remains
real work. This is not it and does not pretend to be.

### Three limitations, all from the engine

1. **`OnObjectModified` fires once per object per FRAME.** `UObjectGlobals` keeps a per-frame set "to
   prevent multiple triggerings". An object already modified earlier in the same frame is invisible.
   Handlers run inline on the game thread, usually one per tick, so this is rare rather than never.
2. **It fires on `Modify()`**, the transaction hook. An endpoint that mutates without calling it —
   a bug in that endpoint, since it also breaks undo — is not seen.
3. **Creation is invisible.** Found by testing, not by reading: `create_asset` at a non-scratch
   `/Game` path reports `scratchClean`, because `NewObject` has no prior state to record.
   `set_property` on that same asset reports it immediately.

   That third one is arguably the RIGHT scope. This answers *"did the agent touch one of YOUR
   assets"*, and an asset the agent just created is not yet one of yours. Creating unsaved clutter is
   a mess; modifying an existing asset that a human then saves is a loss. Only the second is watched
   for. Worth knowing anyway, because `scratchClean` on a call that clearly wrote something otherwise
   looks broken.

**So a clean report is good evidence and not a proof**, and the field name says so.

`batch` dispatches inner ops through `RunEndpoint`, so a watch can already be active — the OUTER one
owns the report. An inner watch stealing the pointer would hand its findings to the wrong response
and lose the rest. Reported on failure paths too, since an endpoint that fails HALFWAY is exactly the
one worth knowing about.

## 4. Where file OUTPUT may land

Andre's question was *"does the safety gate cover EXPORT?"* — filed because `export_asset` writes
files and is not on the unsafe list, while the gate's documented premise is that nothing reaches disk.

Two options were written down: gate export entirely (which kills the Blender mesh round trip, the
whole point of that pipeline) or reword the contract to admit an exported FBX is not a package.

**A third option existed**, visible only after checking one fact: `export_asset` already defaults to
`<ProjectSaved>/MifBridge/Export`, and the MCP wrapper sends no explicit `file` — so the Blender
pipeline uses that default.

> In a gated mode, an **explicitly named path outside the project directory** is refused. The default
> is inside it. The pipeline costs nothing and the contract becomes literal again.

```
file: "C:/Temp/evil.fbx"  ->  refused, refusedRule "file-outside-project"
file: "tile.fbx"          ->  allowed, resolves under the export root
no file parameter         ->  D:/DDS2SDK/Game/Saved/MifBridge/Export/<Name>.fbx
```

**This is a smaller claim than "the gate covers export."** It covers WHERE output may land, not
whether an export may happen — an FBX in the project's own `Saved` folder destroys nothing.

### The bug this shipped with, for one night

The guard was first checked on the **raw request**. A relative file is resolved against
`MifExportRootDir()` (inside the project) a few lines later, but the early check called
`ConvertRelativePathToFull` on the raw string — which resolves against the **process CWD**, and the
editor's CWD is its own binaries directory, outside the project.

So `"tile.fbx"` would have been refused for being outside the project it was about to be written
into. The test passed the whole time: it exercised an absolute path and the no-file default — two of
three branches, and not the broken one. **A guard verified on the cases that work is not verified.**

Now checked on the resolved path. `T637` covers all three shapes.

### Not applied to the other file writers, and that is deliberate

`capture_viewport`, `capture_camera`, `render_thumbnail` and `backup_blueprint` all write files, and
none of them accepts a free-form destination — they resolve into `ProjectSavedDir()/MifBridge` or
beside the original package. Adding the guard call would be a **no-op that looks like coverage**,
which is worse than leaving them alone: the next reader sees a guard and believes it is doing work.

## Related

- `01_POSTMORTEMS.md` PM-011 (modal deadlock), PM-013 (`add_anim_node`)
- `02_GOTCHAS.md` §14 — engine-version differences; both subsystems verify their APIs in both trees
- `14_RELEASE_AND_SYNC.md`
- `tools/test_safety_gate.py`, `tools/test_crash_journal.py`


## The gate is enforced in TWO dispatchers, because there are two

`RefuseIfGated` is called from `RunEndpoint` (`MifBridgeCommon.cpp:1233`) **and** from inside `batch`
(`MifBridgeNodes.cpp`). The second one is not belt-and-braces - it closes a hole.

`batch` deliberately does not recurse through `RunEndpoint`; it dispatches straight out of
`Handlers()`. That is documented in several places in this codebase as an *attribution* problem (each
op's parameter guard was being filed under `batch`). It was also a complete **bypass of the safety
gate**:

```
save_package                          ->  refused, safety-gate, scratch mode
batch {"ops":[{"op":"save_package"}]} ->  ran
```

Every endpoint on the unsafe list was one JSON object away - `save_all`, `run_console`, `start_pie`,
`load_level`, `quit_editor`. And this was not an obscure bypass: **`batch` takes an endpoint name as
data**, so reaching it required no cleverness at all.

Found by reading the dispatcher while closing out the gate's second half, not by an incident.

### Three decisions in the fix worth keeping

**Checked before the compile-heavy ban**, so an endpoint that is both gated and compile-heavy reports
the reason that actually matters.

**Checked per op, not once for the batch.** Ops are independent; refusing `ops[3]` must not silently
drop `ops[4]`. `T634` asserts exactly that - a batch of three with a gated op in the middle must
return three results, with the outer two having run.

**The transaction still commits and the remaining ops still run.** That is the existing `batch`
contract - each op reports its own outcome, `bAllOk` goes false - and it is right here too. A refusal
is a decision, not a crash, and rolling back work that *was* permitted because a later op was not
would be a second surprise on top of the first.

### The general lesson

**A control enforced at one choke point is only as good as the claim that there is one choke point.**
The gate was correct. The claim was wrong, and it was wrong in a file that says so about itself,
twice, for an unrelated reason. When adding a check to a dispatcher, grep for the other dispatchers
first - here, `Handlers()` had exactly two callers and only one of them was guarded.

### The audit, so this is a checked fact and not a claim

A handler is invoked at exactly **four** sites, in **two** functions. Both functions gate before
every site they own:

| invoked at | in | gated at |
|---|---|---|
| `MifBridgeCommon.cpp:1293` (built-in) | `RunEndpoint` (declared :1202) | `:1233` |
| `MifBridgeCommon.cpp:1305` (external) | `RunEndpoint` (declared :1202) | `:1233` |
| `MifBridgeNodes.cpp:2521` (built-in) | `H_batch` | `:2492` |
| `MifBridgeNodes.cpp:2534` (external) | `H_batch` | `:2492` |

Both dispatchers resolve built-ins first and then provider-registered (`kr_*`) endpoints, so each
needs its gate to cover *both* lookups - a check placed between them would leave external endpoints
open. Both gates sit above both lookups.

**Re-run this audit whenever a dispatcher is added.** It is two greps:

```
grep -nE '\(\*(Fn|ExtFn)\)\(|->Handler\(In, Out\)' Source/MifBridge/Private/*.cpp
grep -n 'RefuseIfGated' Source/MifBridge/Private/*.cpp
```

Every line the first produces must be downstream of a line the second produces, in the same
function.


## Making a mode STICK across launches

Infected, on Discord, after the gate shipped: *"Why dont it remain full every time i launch?"*

A fair question with a boring answer, and the fact that it had to be asked is a documentation
failure rather than a design one.

`MIF_BRIDGE_WRITE_MODE` is read from the **process environment, once, at startup**. Set it in a
shell and it dies with that shell. Set it in a launcher script and it applies to editors that script
starts and to nothing else. Every other launch falls back to the default, which is `scratch`.

To make it persist, set it at **User scope**, once:

```
setx MIF_BRIDGE_WRITE_MODE full
```

Every editor launched *after* that starts in `full`. `setx` writes the registry rather than the
current shell, so the shell you typed it in still will not see it - open a new one, or just launch
the editor normally.

To go back:

```
setx MIF_BRIDGE_WRITE_MODE scratch
```

### Why this is not a hole in the gate

The gate is deliberately not settable over the bridge, because an agent that can unlock its own gate
is not a gate. That is about the BRIDGE, not about the human at the keyboard - the whole point of
putting it in the environment is that setting it is something a person does outside the process an
agent is driving.

"Deliberately not settable over the bridge" was never meant to imply "retype it every launch", and
the refusal message says *"Restart the editor with MIF_BRIDGE_WRITE_MODE=full"* without ever saying
how to make that stick. Anyone reading only the refusal would reasonably conclude it is per-session.

### Check what mode you are actually in

`self_audit` reports `writeMode` and `safetyGateActive`, and the in-editor panel shows the mode in
its header. Worth checking after a `setx`, because a `setx` in one shell and an editor already
running in another is exactly the case where the two disagree.
