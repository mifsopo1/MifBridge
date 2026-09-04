<!-- MIFBRIDGE-DEV-ONLY -- excluded from release zips by tools/make_release.py.
     How to cut a MifBridge release and keep a second copy honest. Not a thing a customer does.
     Internal.
     Still version-controlled: kept in git, kept out of the zip. -->
# Releasing MifBridge, and keeping a second copy honest

## The problem this solves, measured

MifBridge is **vendored** into `D:/RoguelikeDealerGame` (Curfew) — the source is committed into that
project's repo rather than linked to this one. On 2026-08-26 the drift was measured:

| | this tree | Curfew's copy |
|---|---|---|
| distinct endpoints (`MIF_BIND`) | **284** | **222** |
| source files | 58 | 47 |

Curfew was **62 endpoints behind**, missing 11 whole source files including all of the IK Rig,
Landscape, Sequencer, Niagara and Game Features work. Nobody noticed for weeks, because nothing ever
compared the two. Work was being lost in both directions until the field reports were merged back by
hand.

**The thing that made it invisible is worth naming: both copies said they were MifBridge, and there was
no way to ask which one was newer.** A version number would not have helped either — both were 0.4.x
the whole time.

## The tool

```bash
python tools/make_release.py
```

Produces `tools/dist/MifBridge-<version>.zip` containing one top-level `MifBridge/` directory, so it
extracts straight into a project's `Plugins/` folder. Alongside the source it writes
`RELEASE_MANIFEST.json`:

| Field | Where it comes from | Why |
|---|---|---|
| `versionName`, `version` | read from `MifBridge.uplugin` | one source of truth, never retyped |
| `endpointCount` | `MIF_BIND` count in the C++ | the number `parity_check` already treats as authoritative, so it can be compared against a live editor's `self_audit` |
| `contentSha256` | path + content of every shipped file | distinguishes *same version* from *same version, locally modified* |
| `engineCompatibility` | stated in the script | what has actually been built, not what might work |

### Checking a copy

```bash
python tools/make_release.py --check tools/dist/MifBridge-0.4.1.zip
```

Three outcomes, and **the middle one is what the Curfew drift actually was**:

- `IDENTICAL` — same version, same content.
- `SAME VERSION, DIFFERENT CONTENT` — one side has local edits. A version number alone calls these
  equal. This is the case that went unnoticed for weeks.
- `DIFFERENT VERSIONS` — reported with the endpoint delta.

This was verified by actually doing it, not by reading the code: a local edit was introduced, `--check`
reported *same version, different content*, the edit was reverted, and `--check` reported `IDENTICAL`
again.

## What ships, and why the list is not written down

The file list comes from `git ls-files`. It is **not** a hand-maintained array, deliberately — a
hand-written manifest is a second source of truth that drifts from the first, which is the exact failure
this whole document is about.

`.gitignore` already excludes `Binaries/`, `Intermediate/`, `Saved/` and `DerivedDataCache/`, so
anything git tracks is by definition source rather than build output. Two categories are dropped on top
of that because they are tracked but are not part of a deployable plugin:

- `.github/` — this repo's CI, not the consumer's.
- test-run logs and result JSON under `tools/` — evidence of one particular night's run.

## The engine compatibility matrix

| Engine | Status | Notes |
|---|---|---|
| 5.3.2 | **built and tested** | the cooked DDS2 SDK — the primary target. **As of 2026-08-31: 434 built-in endpoints, 159 test suites, and a full double-pass sweep of 312 runs across 156 suites — 1 suite failed, 10 skipped, 0 editor deaths**, on a freshly restarted editor. The single failure was a test-side bug in `test_thumbnails`: it took whatever `find_assets` returned first, drew a rotationally symmetric sky mesh, and reported that `render_thumbnail` ignored `orbitYaw`/`orbitZoom` — measured side by side, the endpoint was correct on all three parameters and the fixture could not show them. Fixed by selecting a mesh BY the property the test needs (a candidate is accepted only once a yaw render differs from its base), verified twice. The 10 skips are the 8 Blender suites (which need a Blender on 8792, currently held by another editor — see `06` issue 15), `test_niagara_params` (this project has no NiagaraSystem with a user parameter, and they are all cooked), and `test_safety_gate` (which correctly refuses its destructive probes in `full` write mode). **NAMED-NOWHERE IS DOWN TO 14 (2026-08-31)**, and all fourteen are accounted for: 12 foreign `kr_*` provider endpoints, plus `save_dirty_packages` and `save_level_as`, which the standing no-save rule forbids exercising. So no endpoint MifBridge owns and is permitted to drive is unnamed by any suite. **That is a NAME-MATCH claim and not a coverage claim** - `coverage_gaps.py` says so in its own output, and it is worth repeating here because the two get conflated: a suite naming an endpoint may still exercise one branch of it. `audit_suite_reach.py` is the instrument for the second question, and its last clean run showed zero suites claiming a pass while running a fraction of themselves.

RESTART THE EDITOR FIRST, and this now has a second independent confirmation: a `--once` sweep run immediately afterwards on that SAME editor - roughly 25 minutes of accumulated mutation, no restart - lost `test_levelsnapshots` on T1103 ("the actor is REALLY back at the origin, independently read back"). Restarting and running that suite alone gave 20 PASS 0 FAIL including that exact assertion. Same suite the superseded record below names for the same reason, a day apart. The rule is not folklore. <br><br>SUPERSEDED, kept for the reasoning: as of 2026-08-30 (v0.7.0): 421 built-in endpoints, 144 test suites, and the first full double-pass sweep this project has ever completed - **282 runs across 141 suites, 1 failed, 16 skipped, 0 editor deaths**, on a freshly restarted editor. The single failure was a test-side bug in test_move_actors_to_level, fixed after the run. The 3 PIE-driving suites are excluded from unattended sweeps and named in the output rather than counted as passing; the 16 skips are those plus the 7 Blender suites (which need a Blender on 8792) and test_safety_gate (which correctly refuses its destructive probes in `full` write mode). RESTART THE EDITOR FIRST: a long-lived editor fails suites a fresh one passes - a run earlier the same day lost test_material_undo to a transaction buffer that had reached its cap at 1941 entries, and test_levelsnapshots to hours of accumulated level mutation. Both passed clean after a restart. The "148 runs across 74 suites" figure this row used to carry was from 2026-08-26 - superseded, not corrected in place, because the underlying numbers (endpoint count, suite count) both moved and a stale count is worse than no count. |
| 5.7 | **built** | Curfew — compiled on every change via `make_engine_probe.py`. See `02_GOTCHAS.md` §14 for the API splits that differ. |

"Built" means a compiler agreed, not that anyone has used it. 5.7 has no live test run behind it,
and that distinction is the whole reason this table has two words in it rather than one.

This is a claim about what has actually been compiled, not a guess. §14 matters here: the two engines
differ in **both** directions — 5.3 has symbols 5.7 deleted, and 5.7 has symbols 5.3 never had — so
"it builds on one" says nothing about the other.

## What is still a decision, not a task

Switching Curfew from a vendored copy to a released artifact **changes how Andre's other project
consumes this plugin**, so it has not been done unilaterally. The options, briefly:

1. **Tagged zip plus this script** — what the tooling now supports. Curfew extracts a release and
   `--check` answers "am I current?" in one command. No git workflow imposed on a game project.
2. **Git submodule** — stronger guarantee, but forces a submodule workflow onto a project that does not
   currently need one, and submodules are a recurring source of confusion for anyone who did not set
   them up.
3. **Stay vendored, but check** — the cheapest option: keep vendoring, and run `--check` periodically.
   It does not prevent drift, but it makes drift *visible*, which is the property that was missing.

Option 1 is the recommendation. Option 3 is worth doing immediately regardless, because it costs
nothing and would have caught the 62-endpoint gap the week it started.

## Related

- `06_OPEN_ISSUES_FROM_USE.md` issue 15 — the port collision found while investigating this area.
- `02_GOTCHAS.md` §14 — the two-directional engine version trap the matrix above depends on.
## What will stop a release, and why

`make_release.py` refuses on seven separate checks. None of them is advisory - each prints
`REFUSING TO PACKAGE` and exits non-zero, and `--force` overrides only after saying what it is
overriding. Worth knowing BEFORE you cut a release, because several are things you fix in a minute
and one takes a full engine build.

| gate | refuses when | fix |
|---|---|---|
| badge | `README.md`'s badge line disagrees with the tree | `--update-badge` |
| changelog | `CHANGELOG.md`'s TOP row disagrees with the tree | edit the top row, or add an `Unreleased` one |
| 5.3 | no recorded successful 5.3 build for this `Source/` commit | build on 5.3, then `--record-53` |
| 5.7 | the 5.7 compile probe is missing or stale for this `Source/` | re-run `make_engine_probe.py` |
| param table | `describe_endpoint`'s generated table has drifted | `harvest_param_table.py`, then rebuild |
| value discovery | an endpoint demands a value nothing can discover | fix the endpoint or record the exemption |
| static audits | the tools/ checkers are not clean | run them and fix what they say |

**Before you reach for either engine gate, run `tools/syntax_check.py`.** It parses changed `.cpp`
against BOTH engines with `cl /Zs`, which writes nothing at all - no object, no PCH, no touch of any
DLL - so it runs beside a live editor, in seconds, and does not need the editor closed the way a real
build does. It exists because that requirement had a cost: a C++ edit made while somebody was using
the editor could not be verified until they stopped, and `register_landscape_layer` sat uncompilable
on 5.7 for two days behind exactly that.

It is NOT either gate and must never be recorded as one - a parse is not a link, and it says nothing
about unresolved externals. It is the cheap pre-check that catches the whole class gotchas 14 is
about. Run `--plant` first if you doubt it: each engine's checker is shown an error it must see, and
counting errors is not enough, because a stale PCH fails every file identically.

`--record-53` runs BEFORE all seven, and that is deliberate. It used to sit after them, which made
the 5.3 row's own fix instruction fail: you would build on 5.3 successfully, run `--record-53`, and be
refused because the README badge was stale - which is the NORMAL state between releases. Recording a
build is bookkeeping about something that already happened; none of the packaging gates has an opinion
on whether a compiler succeeded twenty minutes ago. Fixed 2026-09-02.

Two of these exist because a release already went out wrong. **5.3** was added after v0.8.0 shipped
not compiling on 5.3 at all - the 5.7 probe passed, nothing checked 5.3, and the break reached a tag.
**changelog** was added 2026-09-02 after the UE column was found one too high in every row since
0.3.0: the badge two files away had the correct number the whole time, and nothing compared them.

The badge and changelog gates are RELEASE-TIME, not always-true. Both legitimately go stale between
releases, because the badge is regenerated at packaging. A red badge on a working tree is the design,
not a defect - `tools/test_release_gates.py` covers this and asserts the gates ANSWER rather than
that today's answer is yes.


- `tools/parity_check.py` — the in-tree gate; `make_release.py` is its cross-tree equivalent.
- `tools/blender-addon/build_zip.py` — the packaging precedent this script follows.
