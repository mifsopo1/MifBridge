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
| 5.3.2 | **built and tested** | the cooked DDS2 SDK — the primary target. As of 2026-08-30 (v0.7.0): 421 built-in endpoints, 144 test suites, and the first full double-pass sweep this project has ever completed - **282 runs across 141 suites, 1 failed, 16 skipped, 0 editor deaths**, on a freshly restarted editor. The single failure was a test-side bug in test_move_actors_to_level, fixed after the run. The 3 PIE-driving suites are excluded from unattended sweeps and named in the output rather than counted as passing; the 16 skips are those plus the 7 Blender suites (which need a Blender on 8792) and test_safety_gate (which correctly refuses its destructive probes in `full` write mode). RESTART THE EDITOR FIRST: a long-lived editor fails suites a fresh one passes - a run earlier the same day lost test_material_undo to a transaction buffer that had reached its cap at 1941 entries, and test_levelsnapshots to hours of accumulated level mutation. Both passed clean after a restart. The "148 runs across 74 suites" figure this row used to carry was from 2026-08-26 - superseded, not corrected in place, because the underlying numbers (endpoint count, suite count) both moved and a stale count is worse than no count. |
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
- `tools/parity_check.py` — the in-tree gate; `make_release.py` is its cross-tree equivalent.
- `tools/blender-addon/build_zip.py` — the packaging precedent this script follows.
