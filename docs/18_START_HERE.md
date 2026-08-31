<!-- MIFBRIDGE-DEV-ONLY -- excluded from release zips by tools/make_release.py.
     How to pick this work up cold. Internal - a user installing the bridge is not taking over its development.
     Still version-controlled: kept in git, kept out of the zip. -->

# Start here — picking this up cold

You are an agent (or a person) taking over MifBridge with no memory of how it got here. This file
exists so that is survivable.

Andre asked the question that prompted it: *"if my weekly limit runs out, and i log into another
claude account, will it be able to pickup where you left off, hows that work?"*

**The conversation is not the handoff. The repository is.** That is why work here is committed and
pushed after every finished piece rather than batched — a session can end mid-sentence, and what
matters has to survive that. Everything below is on disk and in git, keyed by nothing but this
directory.

## Read these, in this order

| | what it gives you |
|---|---|
| `git log --oneline -40` | **Start here.** Commit messages carry the REASONING, not just the change. Failures and wrong turns are recorded in them deliberately. |
| `tools/FEATURE_PARITY_SPEC.md` | The work queue. `- [ ]` open, `- [x]` done, `- [~]` deliberately declined with the reason on the next line. |
| `docs/06_OPEN_ISSUES_FROM_USE.md` | What is known broken. The tail is the most recent. |
| `docs/02_GOTCHAS.md` | The traps. Section 14 (engine versions) and section 8 (the modal that hangs the bridge) have each cost real time. |
| `docs/01_POSTMORTEMS.md` | Bugs that cost more than 30 minutes, with prevention. |
| `~/.claude/projects/D--DDS2SDK-Game-Plugins-MifBridge/memory/` | Dated decisions in plain English. Path-keyed, not account-keyed. |

## What is running while nobody is watching

* **`tools/report_watch.py`** — polls GitHub every 45s for `bridge-report` issues. Plain Python, **no
  model, no tokens** while idle; it invokes `claude -p` only when a real report lands. Start it with
  `python tools/report_watch.py`.
* **`mifbridge-autonomous-resume`** — a scheduled task, hourly. Checks
  `python tools/night_heartbeat.py`; if `someoneWorking` is true it exits immediately, otherwise it
  picks the work up. Its prompt is deliberately self-contained.
* **The Stop hooks** in `~/.claude/hooks/` — vendored copies live in `tools/`. `parity_check.py`
  reports drift between them, because the deployed copy is the one that runs and editing the repo
  copy changes nothing.

**Touch the heartbeat while you work:** `python tools/night_heartbeat.py touch`, every 10–15 minutes.
Let it go stale for 25 and a resumer starts a SECOND session on the same editor, which is worse than
no session at all.

## The rules that are not negotiable

* **Do NOT save assets, or touch anything outside the SDK editor.** This is Andre's standing
  instruction, and it holds regardless of what the safety gate permits — the gate is a backstop
  under the rule, not the rule.
* **PIE is authorised, as of 2026-08-28** — this file used to forbid it outright; that clause was
  corrected 2026-08-29 because it was actively wrong, not just imprecise. Use `start_pie`/`stop_pie`
  when they are genuinely the right tool, always stop PIE cleanly afterward, and avoid unattended
  sweeps that start and leave PIE sessions running unsupervised. See
  `~/.claude/projects/D--DDS2SDK-Game-Plugins-MifBridge/memory/feedback-pie-authorized.md` for the
  exact wording of the authorisation if there is ever doubt.
* Scratch assets under `/Game/_Mif*` only. Never send `confirm:true` except through
  `tools/scratch_confirm.py`.
* **Always CRLF.**
* Do NOT touch `D:/RoguelikeDealerGame` (Curfew) — a different session owns it.
* If a change touches `MIF_DECL` / `MIF_BIND` / `@mcp.tool`, all three must stay in sync. Run
  `python tools/parity_check.py` before committing.

## Two things that will mislead you

**Build.bat returns exit code 0 on a build that failed.** Not "did nothing" — genuinely compiled,
failed, printed `Result: Failed (OtherCompilationError)`, and exited 0. Grep the log for
`Result: Failed` and for `error`, and check the DLL's mtime moved. A failed LINK also *deletes* the
DLL, so a broken build is not "no new features", it is no bridge at all.

**Reading two engine headers is not enough to know something compiles on both.** Reading finds
symbols that were deleted; it reliably misses symbols that changed shape. And there is a third case
that is worse than either, because the code compiles AND runs:

> A deprecated engine function may have been replaced by a **CONSTANT**, or by an **EMPTY body**.
> `UE_DEPRECATED` tells you the call is going away. It never tells you the answer changed.

`ALandscape::HasLayersContent()` is `return true;` on 5.7. A guard reading it refuses EVERY landscape
there while behaving perfectly on 5.3, and no presence check sees it - the symbol has the same name,
the same signature, in both trees. `ToggleCanHaveLayersContent()` is empty on 5.7, which is why
`create_landscape`'s "edit layers OFF" cannot hold. **Read the body of anything deprecated that you
branch on.** docs/02 has the taxonomy.

There is a compiler for this:

```
python tools/make_engine_probe.py --engine "C:/Program Files/Epic Games/UE_5.7" --out <scratch>/probe57 --build
```

Run it before claiming an engine works. `docs/02_GOTCHAS.md` section 14 has the six failure shapes and
why four of them are invisible to inspection.

**If it says "Unable to build while Live Coding is active"** and the editor holding it is not yours to
close (Curfew, usually), the block is keyed on a mutex named after the target's executable - and an
installed-engine *Development* editor target resolves to the shared `UnrealEditor.exe`, the exact
binary that editor is live-coding. Building **DebugGame** emits `UnrealEditor-Win64-DebugGame.exe`, a
different path and so a different mutex, and compiles the same sources against the same headers with
no bypass flag:

```
Build.bat MifProbeEditor Win64 DebugGame -Project=D:/p57/MifProbe.uproject -WaitMutex
```

Use a SHORT project path - the scratchpad blows the 260-character limit on DebugGame's longer
intermediate names. It is a compile CHECK only: it always ends `Result: Failed` on an engine header
(`UnrealType.h(7136)`, C4702 unreachable code) that DebugGame promotes to an error, and it does
**not** satisfy `make_release`'s 5.7 gate, which wants a recorded Development probe.

That failure is reported at translation unit **16 of 95** - inlined into `MifBridgeDataTables.cpp:172`
via `TFieldIterator<FTextProperty>::operator++` - and the build then CARRIES ON and compiles the
rest. Earlier wording here said it "dies near the end", which is what the summary looks like and is
the opposite of what happens; the distinction is the whole value of the route. Because it continues,
every one of the plugin's translation units is still compiled against 5.7 headers, so a per-file
verdict is real evidence even though the run as a whole is red. Read the log for YOUR file:

```
grep -n "Compile \[x64\] MifBridgeYourFile.cpp" <log>   # it got there
grep -n "MifBridgeYourFile.cpp(" <log>                  # and said nothing about it
```

`buildcheck.py` will still say BUILD NOT OK, correctly - `Result: Failed` is present and no DLL is
linked. Do not talk yourself past that into calling the build green. What you have is a compile
result for one file, and that is worth saying in those words.

**A cleaner option when the mutex is what pushed you here.** The block is per-executable, so an
installed engine with no editor running on it is free. `Get-Process *Unreal*` and compare the
`Path` column: a 5.7 editor out of `C:/Program Files/Epic Games/UE_5.7` blocks Development builds
against that engine and says nothing about UE_5.3. Probing the *installed* 5.3 gets a real
Development build with a linked binary. Never the source tree at `D:/UE532` - see trap 3 in
`make_engine_probe.py`.

## How to know the state is healthy

```
python tools/parity_check.py            # endpoint registry, params, hook drift
python tools/harvest_param_table.py --check   # describe table vs the real guards
python tools/audit_undefined_names.py   # NameErrors in paths tests never reach
python tools/audit_advice_gaps.py       # advice naming an operation that does not exist
python tools/audit_value_discovery.py   # a parameter demanding a value nothing enumerates
python tools/coverage_gaps.py           # endpoints named in no suite
python tools/audit_suite_reach.py       # how much of each suite actually RUNS
python tools/audit_modals.py            # a prompter, or a declared invariant, left unguarded
python tools/audit_loop_writes.py       # a per-item write to a single-valued response field
python tools/audit_postconditions.py    # a mutation nothing reads back
python tools/audit_prose_dependence.py  # a tool whose ANSWER depends on comment text
python tools/why_not.py <term>          # has this already been decided AGAINST?
python tools/night_heartbeat.py         # is another session working?
python tools/mifwatch.py                # did any session die mid-call?
```

`why_not.py` is the one to reach for BEFORE filing a gap. 824 parameters are refused across this
surface, each with the reason attached, and "reading the endpoint list says a capability is absent,
reading the handler says whether it is absent ON PURPOSE" is a distinction that has already cost one
wasted investigation. `set_niagara_user_parameter` refuses `add` and explains why; the endpoint list
cannot tell you that.

The three audits above `audit_prose_dependence` are the ones `make_release` now GATES on, added
2026-08-31 after `audit_loop_writes` was found to have been failing - with a real defect among its
findings - for an unknown length of time, because nothing depended on it. All three are
baseline-ratcheted: they print their whole known set every run and go non-zero only for something
NEW, so a green tree stays green.

`audit_prose_dependence` is the odd one and is NOT gated: it runs ten of the other tools twice, the
second time with every C++ comment blanked underneath them, and diffs the output. A tool whose answer
changes is reading prose as evidence - the root cause of five separate tool bugs found in one night,
because a grep for a symbol finds the places that USE it and the places that DISCUSS it, and this
repo has more of the second. Two tools are listed there as deliberate prose readers with reasons.

`audit_suite_reach` is the newest and the least obvious: a suite reporting PASS is not a suite that tested
what it contains. `test_safety_gate` ran 5 of its 38 assertions here for months, because everything
below its fail-safe bail-out skips whenever the write gate is off - which is the mode this editor
runs in.

`self_audit` on the live bridge is the authoritative endpoint list. **Verify coverage by READING
handlers, never by endpoint name** — `list_collision_profiles` sounds like a collision read and lists
project-wide profile names instead, which is exactly how a gap survives an audit.
