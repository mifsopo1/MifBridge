<!-- MIFBRIDGE-DEV-ONLY -- excluded from release zips by tools/make_release.py.
     Our own Fab submission planning: listing copy, gallery plan, and the decisions still open.
     A buyer has no use for the plan to sell them something. Still version-controlled. -->

# Selling MifBridge on Fab — the submission, and what is still open

**Status as of 2026-09-04.** The package builds and is measured against the agreement on every run
(`python tools/fab_readiness.py --check`). Four things block submission and every one of them is a
decision rather than a defect. Nothing here is guesswork about the contract: the clauses are quoted
from the live [Fab Distribution Agreement](https://www.fab.com/distribution-agreement), last updated
23 February 2026, read on 2026-09-04.

> **Not legal advice.** Reading a contract carefully is not practising law. Everything below is a
> reading you can check against the quoted text, which is why the text is quoted.

---

## 1. The four open decisions

These are Andre's, not something a tool can settle. Each one is recorded in
`tools/fab_listing.json`, where a null is reported as an open decision rather than passing silently.

### 1a. The licence, and what it means for the people who already have it

`LICENSE` is **MIT**, and the repository has been public. MIT is perpetual and irrevocable, so
**every version pushed so far is MIT to everyone who has ever cloned it** — Brando, Huslaa, infected,
and anyone who found the repo. That cannot be undone. It can only be changed going forward.

Two separate questions follow, and they have different answers:

- **Does the Fab package keep an MIT LICENSE inside it?** No, and this is not a preference. §3(f)(v):
  *"content distributed through Fab is licensed only under the Fab End User License Agreement, which
  is not superseded by custom licenses included in Content's distributed files."* The MIT file does
  not win — but a buyer who reads it will believe they may redistribute the plugin publicly, which is
  the one thing a paid listing depends on them not doing. It must be replaced in the package with a
  short notice pointing at the Fab EULA.
- **What licence do future versions carry in the public repo?** Open. If it stays MIT, the paid
  listing is competing with a free, redistributable copy of the same code.

### 1b. The price

§1(d): *"You have complete discretion in setting any Listing Price… You may set the Listing Price for
Content at $0.00 or a value equal to or greater than $0.99."* Nothing in the agreement constrains it
further — there is no price-parity or most-favoured-nation clause anywhere in the document.

### 1c. The `CreatedWithAI` tag

§18(k)(ii): *"When your Content is created using Generative AI Programs, you are required to tag the
Content as 'CreatedWithAI'"*, where *"a material portion of the Content is generated with Generative
AI Programs."* A material portion of this plugin was written by a generative model. This is a
requirement, not a preference, and "material" is a judgement a person has to make and record.

### 1d. Which channels reach an end user

§3(b): *"If your Content is made available to end users through channels other than the Marketplaces,
you will provide any Updates to Epic no later than you provide them to any other third party."*

The obligation attaches to **end users receiving updates**, not to the repository being public. So:

| Arrangement | Parity obligation |
|---|---|
| Private repo, only Andre pulls | **None.** Nobody else is an end user. Auto-push freely. |
| Private repo, collaborators pull | **Yes, on every push they can pull.** Going private does not exempt it. |
| Public repo | **Yes, on every push.** |
| DDS2 SDK installer bundles it | **Yes, on every SDK release carrying a newer build.** |

**The arrangement that keeps the "commit and push as you go" workflow intact:** push `master` to a
private repo only Andre can pull, and give collaborators a branch or tag that advances only when the
Fab listing does. Parity then holds by construction rather than by discipline. Recording the answer
in `fab_listing.json` makes `fab_readiness.py` measure how far behind the listing is instead of
guessing.

---

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
| files | 506 | **166** |
| size | — | **4.8 MB** |

What `--fab` removes and why: 180 test suites that need this repo's fixtures and a live editor; 49
static audits of our own source; 53 files of endpoint-audit working notes from July; and the
top-level dev scripts. That last one was the important find — the exclude patterns had left 48 of
them, **including `report_trust.json`, which describes itself as "the security boundary of the whole
autonomous loop" and names the GitHub logins allowed to have issues auto-processed.** Top-level
`tools/` is now allow-listed instead, so a mistake there loses a utility rather than leaking one.

Four tools ship because a buyer runs them: `verify_install.py`, `scratch_confirm.py`,
`bench_bridge_latency.py`, `make_demo.py`.

---

## 4. Blockers with a technical fix

### 4a. Third-party game references — 16 lines a buyer can see

A 54-agent census of the shipped package, every finding adversarially verified, split the references
by whether a buyer can actually encounter them. The agent-facing half is **fixed**: `tool_help.json`
and `server.py` no longer teach a buyer's model to reach for `DDS2_GameMode` or `DDS2Casino`, and
every measurement in those strings survived verbatim — only the provenance changed.

What remains is 16 emitted strings in `Source/`, and the worst is not cosmetic:

> `MifBridgePipeline.cpp:36` — `MifGameRoot()` falls back to a hardcoded path into one specific
> commercial game in one specific Steam library. With `MIF_GAME_ROOT` unset — the default for every
> buyer — `read_modloader_log` returns that path in its `path` field and its not-found error, and
> `trigger_cook` builds all six of `gameRoot`, `paksDir`, `deployMods`, `deployLogicMods` and
> `ue4ssLog` from it. Both endpoints are bound unconditionally with no gate.

The fix is written and waiting at `scratchpad/pipeline_neutral.py`: the fallback is removed entirely
(a wrong guess is worse than none — it yields a plausible path that silently is not yours), an ini
setting joins the env var so this machine configures it once, and the three callers refuse through
one shared message that says how to set it.

**Blocked on a free editor.** A `Source/` edit that cannot be compiled leaves the tree dirty against
both engine records and red-gates it for everyone.

### 4b. The in-editor Flag button reports to nobody

The panel has a Flag button on every call. It writes a structured report to
`Saved/MifBridge/reports/` in the exact shape `report_intake.parse_report` validates — and **nothing
anywhere reads that directory.** Four mentions of it exist in the whole tree: the writer, its
declaration, and two comments. `report_intake.py` fetches GitHub issues; `report_watch.py` polls the
GitHub API. So every flag ever clicked went into a folder no code opens.

The tooltip says *"for the autonomous loop to pick up, reproduce and fix."* That has never been true.
For a buyer it is worse than a missing feature: a button that looks like reporting, tells nobody, and
leaves them believing they filed something. People do not report a bug twice.

Fix written at `scratchpad/flag_reaches_us.py`: the file is still written, and a notification then
offers to open a **pre-filled GitHub issue** — the reporter sees every character on GitHub's own form
and submits it themselves. Nothing is transmitted automatically, which keeps the property the
original design was right to have. The repo URL is configurable so a fork's users do not file against
ours. **Also blocked on a free editor.**

### 4c. The finiteness guard

`1e999` quoted is refused; `1e999` unquoted is accepted — the same value, opposite outcomes, decided
by quoting. All 233 numeric call sites inherit it. One-line fix preserved at
`scratchpad/MifBridgeCommon.finite-patch.cpp`. **Same blocker.**

---

## 5. The gallery

Fab lists on its images. Nothing here is ready, and the honest reason is that good listing art needs
a scene we own and an editor we are not sharing.

| # | Shows | State |
|---|---|---|
| 1 | An agent wiring and **compiling** a Blueprint, with the real compiler output mapped to node and pin | needs a free editor + an own-IP scene |
| 2 | The in-editor panel mid-session: live call transcript, timings, the Flag button | needs a free editor |
| 3 | Blender before/after: `mesh_quality` findings, then `recipe_game_ready`, with the numbers on it | `make_demo.py` generates this today |
| 4 | Round trip — an asset authored in Blender, landing in Unreal | needs both |
| 5 | `describe_endpoint` answering, to make the point that the API documents itself | needs a free editor |

`tools/make_demo.py` already generates image 3 end to end and checks its own output — but it says of
itself, correctly: *"The subject is a grey cylinder… It is not listing art."* Turning it into listing
art means an actual scene, and **the scene must be ours** — using DDS2 content in marketing for a
commercial product is the same §3(g)(i) problem as shipping its name in a string.

---

## 6. Running the check

```bash
python tools/make_release.py --fab && python tools/fab_readiness.py --check
```

`--selftest` proves every check can both fire and stay quiet. Two of them were wrong within ten
minutes of being written — one false pass, one false failure — which is the whole space of ways to be
wrong, and is why the selftest exists rather than a note saying it was tested.

A clean run removes the mechanical reasons not to publish. It is not permission.
