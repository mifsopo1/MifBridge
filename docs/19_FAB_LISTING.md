<!-- MIFBRIDGE-DEV-ONLY -- excluded from release zips by tools/make_release.py.
     Our own Fab submission planning: listing copy, gallery plan, and the decisions still open.
     A buyer has no use for the plan to sell them something. Still version-controlled. -->

# Selling MifBridge on Fab — the submission, and what is still open

**Status as of 2026-09-05. `fab_readiness --check` PASSES.** Every clause it can judge is green,
all four decisions are made and recorded, and the package is 144 files and 2.1 MB.

**The gallery is no longer the blocker.** Three images generate themselves and check their own
output — the Blender before/after, the API refusal card, and three renders from a real UE 5.7
project. What is left is **one screenshot only Andre can take** (the in-editor panel, image 2), and
two things you do AT upload rather than before it: tick CreatedWithAI in the Fab portal, and put
`git rev-parse HEAD` into `publishedCommit`. Section 5 says which is which and why.

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

## 5. The gallery

Fab lists on its images. Three of the five exist now; the state below is what each one actually is,
not what it was planned to be.

| # | Shows | State |
|---|---|---|
| 1 | An agent wiring and **compiling** a Blueprint, real compiler output mapped to node and pin | needs a scratch project — see below |
| 2 | The in-editor panel mid-session: live call transcript, timings, the Flag button | **DONE** — Andre, 2026-09-05 |
| 3 | Blender before/after: `mesh_quality` findings, then `recipe_game_ready`, with the numbers | **`make_demo.py`** |
| 4 | Round trip — an asset authored in Blender, landing in Unreal | needs a scratch project |
| 5 | The API refusing a wrong call and naming what it accepts | **`make_api_card.py`** |
| 6 | MifBridge driving a real project on stock UE 5.7 | **`make_ue_demo.py`** — 3 kept |

**The plan's central assumption was wrong, and it is worth writing down rather than quietly
fixing.** It said the images would be made by the plugin doing its job — `capture_viewport` is an
endpoint, so no screenshot tool is pointed at anyone's window. That holds for a 3D scene and does
not survive contact with images 1, 2 and 5: the panel, the compiler output and `describe_endpoint`'s
answer are **UI and text**, and no capture endpoint can photograph those. A render of the level
shows *Curfew*. It does not show *MifBridge*.

So the gallery splits three ways by what can honestly produce each image:

**Rendered from the scene** — `make_ue_demo.py`, image 6. Read-only: no actor spawned, no package
dirtied, no viewport moved. Against Curfew on stock 5.7 it kept 3 of 3 shots at 1280×720. It also
excludes scratch fixtures via `is_scratch_fixture`, which is not tidiness — a sweep leaves fixtures
in whatever level is open, and this session left 116 in that map.

**Drawn from captured output** — `make_api_card.py`, image 5, and it is the strongest one on the
page. Every UE automation tool can spawn an actor; what a buyer is choosing between is what happens
when the call is **wrong**, because that is where an agent spends its time. The card sends three
deliberately wrong parameters to read-only endpoints and renders the refusals verbatim — nothing is
hardcoded, and if the bridge is unreachable it refuses rather than falling back to text I typed. It
draws a transcript and does **not** imitate the editor's UI: an image that looked like a panel I
never photographed would be a fabricated screenshot even with genuine text in it.

**Photographed by Andre** — image 2, the panel, delivered 2026-09-05. I do not take self-initiated
screenshots of the editor, and there is no honest way around that for a picture of a UI. Four shots
came back and they cover more than was asked:

- **ACTIVITY** — the live transcript with per-call timings and READ / REFUSED / FAILED colouring,
  header reading `UE 5.7`, `:8791`, 453 endpoints, 10,014 calls. This is the hero shot.
- **PERFORMANCE** — the tick census over `L_Corram_P`, with its own caveat printed on screen
  ("this is not a measurement... for real frame attribution, start a trace"). A tool that argues
  against over-reading its own numbers is worth showing.
- **INHERITANCE** — 219 blueprints under 45 native roots, and the line "nothing was loaded;
  registry tags only", which is the read-only claim made visible.
- **The Flag toast** — "Flagged self_audit. The report is saved locally. File it on GitHub (opens a
  prefilled issue)." That is the bug-reporting path working end to end.

Worth a second take if there is time: the Flag button itself in frame on the ACTIVITY tab, and a
couple of green WROTE rows among the READs so the gallery does not imply a read-only product.

**Images 1 and 4 need a scratch project, and the reason is now measured.** Both write — a Blueprint
to compile, an asset to import — and a session **cannot delete an asset it created**: the editor's
transaction buffer holds it, and the bridge binds undo/redo but nothing that clears the buffer. So
generating them in Curfew would leave undeletable junk in Andre's game. They belong in a throwaway
project, which also makes the shots reproducible.

### Should Curfew appear on a public listing at all?

It is Andre's own IP, so there is no §3(g)(i) problem — that was the reason for choosing it over the
DDS2 fork, whose content is another studio's. The remaining question is not legal: image 6 shows an
**unannounced game's greybox**. That is Andre's call, and the listing does not depend on it — images
3 and 5 carry the argument on their own.

## 6. The listing copy

Written from the numbers the tools compute, not from adjectives. Every figure below is one
`make_release.py --update-badge` and `audit_stale_counts` already keep honest, so if it drifts the
gates go red rather than the store page going stale.

### Title

    MifBridge — drive Unreal and Blender from an AI agent, and read the results back

### Short description

    An MCP server fronting two backends: an in-editor Unreal plugin and a Blender addon. Build,
    wire and compile Blueprints programmatically and get the real compiler output mapped to node
    and pin. 453 UE endpoints, 154 Blender ops.

### Long description

> **The loop this replaces.** Today an agent writes T3D, you paste it, you screenshot the errors,
> it guesses, you repeat. MifBridge closes that: the agent builds the graph, compiles it, and reads
> the compiler's actual output — the node and the pin, not a screenshot of them.
>
> **Two backends, one agent.** On the Unreal side: Blueprint graphs, DataTables, level actors,
> Sequencer, Niagara, landscape, World Partition, IK Rig, Game Features. On the Blender side:
> modelling, booleans, UV unwrapping, rigging, lighting, cameras, keyframes, geometry nodes,
> physics, particles and rendering — as typed, guarded operations rather than arbitrary Python.
> `run_python` exists, is off by default, reports nothing, and is deliberately not how the
> interesting work gets done.
>
> **Every call tells you what actually happened.** Not what it was asked to do. A refusal says
> which parameter, what was sent, what is accepted, and whether anything changed — and "NOTHING was
> changed" is a promise the test suite holds the code to. An endpoint that mutates and then denies
> it is treated as a defect here, and there are 35 ratcheted static checks in the release gate
> making sure new ones do not appear.
>
> **It documents itself.** `describe_endpoint` returns the accepted parameters for any endpoint, and
> a static gate fails the build when that description and the guard that answers it disagree. You do
> not have to guess a parameter name, and neither does your agent.
>
> **Engines.** Built and tested continuously against UE 5.3.2. Against stock UE 5.7 it compiles,
> links, loads and runs — verified on a real 35,725-asset project, healthy, all 453 endpoints
> registered. Blender 3.6 through 5.0, with every op exercised on every version on each release.

### Technical details

    Type            Editor plugin (C++) + Blender addon (Python) + MCP server (Python)
    UE versions     5.3, 5.7          Blender versions  3.6, 4.2 LTS, 4.4, 5.0
    Platforms       Windows 64-bit
    Endpoints       453 UE, 154 Blender ops, 624 MCP tools
    Ships in build  No - editor-time only, nothing links into a packaged game

### Tags

    ai, mcp, blender, automation, blueprint, pipeline, tooling, editor-utility,
    procedural, workflow

### What the description deliberately does NOT say

No "powerful", no "seamless", no "revolutionise". A buyer of a developer tool can check every claim
on this page against the plugin in ten minutes, and one that does not survive that is worse than a
plainer one that does. Every number here is generated; the strongest claims — the compiler output,
the self-documenting API, the refusal contract — are the ones easiest to verify and hardest to fake.

## 7. Running the check

```bash
python tools/make_release.py --fab && python tools/fab_readiness.py --check
```

`--selftest` proves every check can both fire and stay quiet. Two of them were wrong within ten
minutes of being written — one false pass, one false failure — which is the whole space of ways to be
wrong, and is why the selftest exists rather than a note saying it was tested.

A clean run removes the mechanical reasons not to publish. It is not permission.
