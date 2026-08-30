---
name: mifbridge-driving
description: How to drive a live Unreal editor through MifBridge correctly - the call grammar, how to discover the surface instead of guessing at it, and the verification rules that decide whether a result can be trusted. Read alongside mifbridge-hazards, which covers what can take the editor down.
---

# Driving the bridge

`HTTP POST 127.0.0.1:8791/api/<endpoint>`, header `X-Mif-Token`, JSON in and JSON out. Through the
MCP server the wrappers do this for you.

Every response has `ok`. **`ok` is not the answer** — see *Verification* below, which is the part
that matters.

---

## Never guess at the surface. Ask it.

There are several hundred endpoints and the names are not guessable. No count is written here on purpose - nothing checks this file, so a number would drift, and a stale one is worse than none because it tells you the surface is smaller than it is. self_audit reports the real count for the build you are actually talking to. Two endpoints exist so you never have to guess:

| | |
|---|---|
| `self_audit` | the live endpoint list, the engine version as numbers, the write mode, the port |
| `describe_endpoint` | one endpoint's real parameters, aliases, and what it refuses |

`self_audit` is authoritative in a way a file cannot be: it reports what the **running DLL** has, and
the DLL is frequently older than the source. Several confused hours have started with an endpoint
that exists in the repo and not in the binary.

**Guessing a parameter name does not fail quietly.** Endpoints run `RejectUnknownParams`, so an
unrecognised key is refused outright with a list of what *is* accepted and, usually, a hint naming the
right spelling for the wrong one you tried. Read the refusal; it is written to be read.

---

## Nothing is saved, and that is deliberate

The bridge does not write to disk. Assets you create live in the editor's memory until a human saves
them. This is the standing contract on this project, and since the safety gate it is enforced rather
than promised.

Two consequences worth internalising:

- **Your work is not durable.** An editor restart loses it. That is the intended trade.
- **In `scratch` mode you can build a thing and not keep it.** Asset creation, graph editing and
  property writes all work; `save_*`, `load_level`, PIE, console execution and a few others refuse by
  name. The refusal says how to unlock and how to make it stick.

`self_audit` reports `writeMode`. Check it before concluding an endpoint is broken — a gated refusal
and a defect look similar if you only read `ok`.

---

## Verification: the part that actually matters

**A mutation without a read-back is not done.** Not a style preference — the most productive
bug-finding lens on this project, by a wide margin.

Concretely, after any write:

1. **Read it back with a different endpoint** than the one that wrote it. `set_property` reporting
   success and `get_property` returning the old value is the bug you are looking for.
2. **Compare against what you asked for**, not against what the response echoed. An endpoint echoing
   your own request tells you nothing.
3. **Read the numeric fields, not just `ok`.** `deleted:0`, `spawned:0`, `added:0` alongside
   `ok:true` has been a real, shipped bug more than once. Those are fixed; the shape recurs.

### Compilation is not automatic

Blueprint graph edits do not take effect until the blueprint compiles. An edit that reads back
correctly and does nothing at runtime is usually this. Several endpoints say so in their response;
not all of them do.

### A read can be wrong in a way the JSON cannot show

Captured images especially. `docs/02_GOTCHAS.md` §11.

---

## Types are strict, and a wrong type is an error

Numbers are numbers. A string where a number belongs is **refused**, not coerced and not defaulted.
This is deliberate: silently defaulting a malformed value is how a call succeeds while doing something
else.

`set_property` and friends check the value against the property's **type** before importing it, so a
type mismatch fails before it can corrupt the property. That check does not exist everywhere.

---

## Batch, and when not to use it

`batch` runs several ops in **one transaction** — one Ctrl-Z for the whole thing. Good for a set of
related graph edits.

Not for everything:

- **Compile-heavy ops are refused inside batch** and must be called standalone. Batch compiles once at
  the end via `compileAtEnd`.
- **A refused op does not stop the others**, and the transaction still commits. Each op reports its own
  outcome and the batch's own `ok` goes false. That is deliberate — the ops that ran really did run,
  and rolling them back because a later one was refused would be a second surprise.
- Read `results[]` per op. The aggregate `ok` tells you *something* failed, not *what*.

---

## When the bridge stops answering

First: **it is probably a modal dialog, not a crash.** See `mifbridge-hazards` §1. Look behind the
editor window.

If the editor really is gone, `Saved/MifBridge/journal.jsonl` records every call with a start and an
end, flushed **before** the handler runs. A start with no matching end names the call that was in
flight. `python tools/mifwatch.py` reads it.

The journal's honest limit: it cannot tell a crash from an external kill. Both are a session with no
shutdown record. It answers *"what was running"*, not *"why it died"*.

---

## Two engines

This plugin runs on the DDS2 cooked editor (UE 5.3.2) **and** on stock UE 5.7. Endpoints that depend
on an optional plugin stay **registered** on every engine and compile a **named refusal** where the
plugin is absent — because a missing endpoint tells a caller nothing, while a refusal that names the
reason tells them everything.

So a refusal saying *"this engine build has no X"* is the system working, not a bug. `self_audit`
reports `engineMajor`/`engineMinor` as numbers for exactly this kind of branching.
