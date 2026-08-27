# Skills

Agent-ingestible knowledge that ships with the plugin.

## Why these exist separately from `docs/`

`docs/` is ~7,800 lines and is a **reference**: it answers a question you already know to ask. Every
trap in it was found expensively, and an agent driving this bridge for the first time only finds the
one about to bite it by already knowing to grep for it.

These are the other shape. Small, task-triggered, and written to be read **before** the first call
rather than after the first failure.

## What is here

| Skill | Read it when |
|---|---|
| `mifbridge-hazards` | before driving a live editor at all — what can terminate it, deadlock the bridge, or destroy unsaved work |
| `mifbridge-driving` | writing calls — the grammar, discovering the surface, and the verification rules that decide whether a result can be trusted |
| `mifbridge-engine-versions` | touching C++ that must compile on both UE 5.3 and UE 5.7 |

`hazards` and `driving` are a pair: one is what breaks, the other is how to work correctly. Neither is
complete without the other.

## The rule they all reduce to

Everything in `hazards` is a specific instance. This is the general form, and it has found more real
defects on this project than any other single idea:

> **A mutation without a read-back is not done.**

With a sharper corollary that took longer to see, because it survives contact with working code:

> **An endpoint that computes an outcome count must decide what that count MEANS**, rather than
> reporting it and leaving the caller to notice.

The second one explains why these bugs last. The response contains the truth — `deleted: 0`,
`spawned: 0`, `added: 0` — sitting beside `ok: true`, in a field nobody reads.

## Keeping them honest

A skill that has drifted from the code is worse than no skill, because it is trusted. So:

- **Cite something checkable** — a file, a line number, an endpoint name — not a recollection.
- **Say when a guard exists**, and say why the guard is not the point: it covers the shape already
  found, and the next one will look like it and not be guarded yet.
- **Prefer the general form over the instance.** "A control enforced at one choke point is only as
  good as the claim that there is one choke point" outlives the specific bypass that taught it.
- **Update in the same commit as the code.** After never comes.

## Format

One directory per skill, containing `SKILL.md` with YAML frontmatter carrying `name` and
`description`. The description is what a reader sees when deciding whether this is the skill they
need, so it should say **when to read it**, not just what it covers.
