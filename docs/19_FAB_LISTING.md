<!-- MIFBRIDGE-DEV-ONLY -- excluded from release zips by tools/make_release.py.
     Our own Fab submission planning: listing copy, gallery plan, and the decisions still open.
     A buyer has no use for the plan to sell them something. Still version-controlled. -->

# Selling MifBridge on Fab — the submission, and what is still open

**Status as of 2026-09-05. `fab_readiness --check` PASSES.** Every clause it can judge is green,
all four decisions are made and recorded, and the package is 144 files and 2.1 MB. What remains
before posting is the gallery, and two things you do AT upload rather than before it: tick
CreatedWithAI in the Fab portal, and put `git rev-parse HEAD` into `publishedCommit`.

Getting to a passing run meant correcting two checks rather than changing the package, and both are
worth knowing about because either would have looked like a package defect:

- **3(b) blocked before anything was published**, which cannot be satisfied. Nothing can be LATER
  than a listing that does not exist, and the only way to set `publishedCommit` is to post - so the
  check was refusing the act that would clear it. It is a note before publication and blocks after.
- **3(g)(i) blocked on comments while the buyer-visible count was zero.** The clause is about what
  reaches a buyer; emitted strings are at 0, down from 28. The 119 that remain are in comments and
  internal docs, still counted and still reported, just no longer grounds for refusing a package. Nothing here is guesswork about the contract: the clauses are quoted
from the live [Fab Distribution Agreement](https://www.fab.com/distribution-agreement), last updated
23 February 2026, read on 2026-09-04.

> **Not legal advice.** Reading a contract carefully is not practising law. Everything below is a
> reading you can check against the quoted text, which is why the text is quoted.

---

## 1. The decisions, and what they came out as

All four were answered on 2026-09-04 and are recorded in `tools/fab_listing.json`, where
`fab_readiness` reads them. A field left null there is reported as an open decision rather than
passing silently, so the file is the record and not a summary of one.

| | decided | note |
|---|---|---|
| price | **$59.99** | §1(d): $0.00 or ≥$0.99, complete discretion. No price-parity clause exists anywhere in the agreement, so nothing constrains what you charge elsewhere. |
| `CreatedWithAI` | **true** | §18(k)(ii) requires it where a material portion is generated. The reason is written out in the file. **Recording it here is not the same as tagging it** — the box still has to be ticked in the Fab portal, which is what the clause actually asks for. |
| licence in the payload | **`fab-eula-notice`** | The MIT file does not ship inside the Fab zip. |
| channels reaching end users | **public GitHub, and the Nexus SDK installer** | Both confirmed: anyone can clone the repo today, and the DDS2 SDK installer bundles MifBridge and the Blender addon. |

### The licence changed, and what that does and does not do

`LICENSE` is now **proprietary and non-redistributable**. Buy it once, use it on any number of your
own machines including commercially, modify it for yourself — and everything you MAKE with it is
yours, with nothing attaching to your assets, levels, Blueprints or builds. What is forbidden is
passing MifBridge itself on.

**Versions already published under MIT stay MIT for everyone who received them.** That grant is
perpetual and irrevocable; the new licence says so in its own section rather than leaving it to be
discovered. Brando, infected and anyone who cloned the repo keep what they have, permanently, and
they do not need adding to anything for that to be true.

**The relicense turned up an actual compliance gap first**, which mattered more than the relicense
itself. The Blender backend adapts blender-mcp under MIT, and MIT's condition — *"the above copyright
notice and this permission notice shall be included in all copies or substantial portions"* — **was
not being met**: `NOTICE.md` named the licence and gave the copyright line, and the permission notice
appeared nowhere in the repository. A technical gap while we were MIT; a real breach in a paid
product. The full text is now reproduced, and the reasoning that used to read "MIT-to-MIT, so the
adaptation is permitted" is corrected — MIT permits proprietary use, on that condition, which now
carries the whole weight.

### Update parity is now measurable, and currently outstanding

§3(b): *"If your Content is made available to end users through channels other than the
Marketplaces, you will provide any Updates to Epic no later than you provide them to any other third
party."*

With both channels declared and nothing published yet, `fab_readiness` reports **FINDING** rather
than UNKNOWN — it cannot measure how far behind Fab is, and says so instead of passing. It resolves
at the first upload, when `git rev-parse HEAD` goes into `publishedCommit`.

The arrangement that keeps a commit-and-push workflow intact is a private `master` only Andre pulls,
with collaborators on a branch or tag that advances when the listing does. Parity then holds by
construction rather than by discipline.

### Going private: what it stops, measured rather than assumed

| | after going private |
|---|---|
| watchdog polling | keeps working — `report_watch.py` shells out to `gh issue list`, and `gh` is authenticated as the repo owner |
| Discord postings | unaffected — a webhook knows nothing about repo visibility |
| the auto-repair loop | keeps working; it reads what the watchdog fetches |
| **strangers filing issues** | **stops.** Only collaborators can open issues on a private repo. |

**Do not flip to private while a collaborator invite is pending** — an unaccepted invitee loses
access entirely.

## 2. What is already settled

**There is no exclusivity clause.** §2(a) is titled *"Your Content is Yours"* — *"you own all rights
in your Content… The rights you grant to Epic and Customers herein is not an ownership right, but a
license."* The licence to Epic (§2(b)) is limited to operating the marketplace. Nothing prevents
distributing the same code elsewhere, giving it away, or bundling it in another product.

**So the DDS2 SDK bundle is permitted.** Andre owns the copyright and can license it separately —
"free when used with the SDK, purchased otherwise" is an ordinary dual licence. Three conditions:

1. It triggers §3(b) parity (see the table above).
2. §3(g)(iii) — content must not *"violate contracts or terms you entered into with any party"* — so
   the SDK's own licence text must not contradict this one. It needs writing down, not assuming.
3. §3(f)(v) still governs the **Fab** copy: the SDK-only terms live with the SDK, never inside the
   Fab package.

**Buyers keep what they bought, permanently.** §10(b)(ii): *"Customer Licenses granted pursuant to
Section 2(c) will be unaffected by Content being withdrawn… Customers will have no obligation to
delete previously acquired Content."*

**The revenue share is 88%** (§5(a)).

**No copyleft ships.** §3(f)(vi) forbids GPL, LGPL, EPL and MSPL outright. MifKismetReconstructor is
GPL-3.0, is reached through an engine-provided delegate, and **zero of its files are in the package** —
checked on every build. This separation is the condition the whole listing's legality rests on.

---

## 3. The package

Built with `python tools/make_release.py --fab`. The default zip is unchanged; what ships to a store
is a product decision, so it is opt-in.

|  | default | `--fab` |
|---|---:|---:|
| files | 506 | **144** |
| size | — | **2.1 MB** |

What `--fab` removes and why: 180 test suites that need this repo's fixtures and a live editor; 49
static audits of our own source; 53 files of endpoint-audit working notes from July; and the
top-level dev scripts; and `tools/blender-showcase/`, 2.8 MB of renders and lab scripts from the
sessions where these features were built, which escaped because the allow-list governs top-level
`tools/` files and a subdirectory falls through to the pattern list. The dev scripts were the
important find — the exclude patterns had left 48 of
them, **including `report_trust.json`, which describes itself as "the security boundary of the whole
autonomous loop" and names the GitHub logins allowed to have issues auto-processed.** Top-level
`tools/` is now allow-listed instead, so a mistake there loses a utility rather than leaking one.

Four tools ship because a buyer runs them: `verify_install.py`, `scratch_confirm.py`,
`bench_bridge_latency.py`, `make_demo.py`.

---

## 4. Blockers with a technical fix

### 4a. Third-party game references — zero a buyer can reach, 119 in comments

A 54-agent census of the shipped package, every finding adversarially verified, split the references
by whether a buyer can actually encounter them. **The runtime half is fixed: 28 emitted strings this
morning, 0 now.**

The worst was not cosmetic. `MifGameRoot()` fell back to a hardcoded path into one specific
commercial game in one Steam library, and with `MIF_GAME_ROOT` unset — the default for every buyer —
`read_modloader_log` returned it in its `path` field and its not-found error, while `trigger_cook`
built all six of `gameRoot`, `paksDir`, `deployMods`, `deployLogicMods` and `ue4ssLog` from it. Both
endpoints were bound unconditionally with no gate. **The fallback is now removed entirely** — a wrong
guess is worse than none, because it yields a plausible path that silently is not yours — and the
endpoints refuse while naming both ways to configure it, `MIF_GAME_ROOT` or `[MifBridge] GameRoot`.

The agent-facing half is fixed too: `tool_help.json` no longer teaches a buyer's model to reach for
`DDS2_GameMode` or `DDS2Casino`. Every measurement in those strings survived verbatim; only the
provenance changed.

**What remains is 119 references in comments and docs, and they are staying.** A fan-out to sweep
them was run and **reverted in full**: the instruction to replace asset names with "a plausible
generic name" produced `S_Rock_02` and `M_Landscape_MasterMat`, which contradicted seven other files
still using the real names, falsified sentences like *"re-ran the EXACT call that crashed the
editor"*, rewrote runnable commands into paths that do not exist, and edited verbatim captured UBT
output. An asset name is a fact and a path in a runnable command is a fact, so "remove the name, keep
the facts" was not a coherent instruction for this corpus. Desynchronising docs from code is a worse
outcome than a studio name in a `//` line.

### 4b. Fixed: the in-editor Flag button reported to nobody

The panel's Flag button wrote a structured report to `Saved/MifBridge/reports/` and **nothing
anywhere read that directory** — four mentions in the whole tree, all of them the writer and its own
comments, while `report_intake.py` fetches GitHub issues. Every flag ever clicked went into a folder
no code opens, under a tooltip promising *"for the autonomous loop to pick up, reproduce and fix."*
For a buyer that is worse than a missing feature: a button that looks like reporting and tells
nobody.

It now offers a **pre-filled GitHub issue** — the reporter sees every character on GitHub's own form
and submits it themselves. Nothing is transmitted automatically, which keeps the property the
original design was right to have.

### 4c. Fixed: the finiteness guard, and three mutate-then-deny handlers

`1e999` quoted was refused and `1e999` unquoted accepted — the same value, opposite outcomes, decided
by quoting, inherited by all 233 numeric call sites. Fixed and **verified against a live editor**:
`1e999` and `-1e999` are now refused, a finite `5.0` still passes.

And three handlers that mutated and then claimed they had not — `set_sequence_keys` (which cleared
every authored key on a channel and then said *"NOTHING was changed"*), `set_material_parameter`, and
`set_collision`. All three fixed by moving validation above the first mutation rather than trying to
undo one afterwards. The detector went from 66 findings to 59.

## 5. The gallery — the only thing left before posting

Fab lists on its images, and this is now the sole blocker.

| # | Shows | State |
|---|---|---|
| 1 | An agent wiring and **compiling** a Blueprint, with the real compiler output mapped to node and pin | needs a free editor |
| 2 | The in-editor panel mid-session: live call transcript, timings, the Flag button | needs a free editor |
| 3 | Blender before/after: `mesh_quality` findings, then `recipe_game_ready`, with the numbers on it | **`make_demo.py` generates this today** |
| 4 | Round trip — an asset authored in Blender, landing in Unreal | needs both |
| 5 | `describe_endpoint` answering, to make the point that the API documents itself | needs a free editor |

**Two constraints settled on 2026-09-05, and they resolve each other.**

The scene must be **our own IP** — using another studio's content in marketing is the same §3(g)(i)
problem as shipping its name in a string. And I do not take self-initiated screenshots of the
editor.

**Curfew answers both.** It is Andre's own uncooked 5.7 project with 35,725 assets, so shots from it
are ours to publish, and they show MifBridge as a **general UE5 tool** rather than a DDS2 mod utility
— which is the listing's whole argument. `capture_viewport` is an endpoint, so the images are made
by the plugin doing its job rather than by pointing a screenshot tool at a window. Demoing the
product by using the product is the honest version of a gallery.

`make_demo.py` produces image 3 end to end and checks its own output. Its subject is now a bevelled
barrel with a steel material rather than a grey cylinder — a shape a buyer recognises — while the
defects it measures (ngon caps, unapplied non-uniform scale) are untouched, because a demo that
tidied away its own defects would be showing a tool solving a problem it had already removed.

## 6. Running the check

```bash
python tools/make_release.py --fab && python tools/fab_readiness.py --check
```

`--selftest` proves every check can both fire and stay quiet. Two of them were wrong within ten
minutes of being written — one false pass, one false failure — which is the whole space of ways to be
wrong, and is why the selftest exists rather than a note saying it was tested.

A clean run removes the mechanical reasons not to publish. It is not permission.
