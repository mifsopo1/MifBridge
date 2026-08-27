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

* **Do NOT save assets, start PIE, or touch anything outside the SDK editor.** This is Andre's
  standing instruction, and it holds regardless of what the safety gate permits — the gate is a
  backstop under the rule, not the rule.
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
symbols that were deleted; it reliably misses symbols that changed shape. There is a compiler for
this:

```
python tools/make_engine_probe.py --engine "C:/Program Files/Epic Games/UE_5.7" --out <scratch>/probe57 --build
```

Run it before claiming an engine works. `docs/02_GOTCHAS.md` section 14 has the six failure shapes and
why four of them are invisible to inspection.

## How to know the state is healthy

```
python tools/parity_check.py          # endpoint registry, params, hook drift
python tools/night_heartbeat.py       # is another session working?
python tools/mifwatch.py              # did any session die mid-call?
```

`self_audit` on the live bridge is the authoritative endpoint list. **Verify coverage by READING
handlers, never by endpoint name** — `list_collision_profiles` sounds like a collision read and lists
project-wide profile names instead, which is exactly how a gap survives an audit.
