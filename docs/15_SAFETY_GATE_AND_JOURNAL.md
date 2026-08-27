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
or execute outside the process: `save_package`, `save_blueprint`, `save_dirty_packages`, `save_level_as`,
`save_all`, `start_pie`, `stop_pie`, `run_console`, `exec_console`, `trigger_cook`, `new_level`,
`load_level`, `quit_editor`, `restart_editor`, `build_navmesh`, `import_asset`.

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
