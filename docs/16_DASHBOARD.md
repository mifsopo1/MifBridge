# The in-editor dashboard

Four tabs in one panel, plus the endpoints behind them. Opened automatically a second after the editor
starts, and available under **Window** or **Tools ▸ Mif Bridge: Live Panel**.

`mif.BridgeAutoOpen 0` turns the auto-open off.

---

## The rule that shapes all of it

**The panel reads the bridge and writes nothing back.**

MifBridge is headless, and that is an *advantage* over an in-editor plugin, not a gap — the bridge opens
and closes the editor, survives its crashes, and runs in processes with no UI at all. A panel the server
depended on would throw that away.

So the dependency runs strictly one way. The server is constructed and started in `StartupModule`,
earlier and entirely independently, and holds no reference to any widget. **Delete every panel file and
the bridge is unchanged.**

Two guards keep the UI out of processes that have none: registration happens in `RegisterMenus` (which
runs from a ToolMenus startup callback that never fires without a UI), and `FSlateApplication::
IsInitialized()` is checked anyway — because `EHostType::Editor` **does** load in commandlets.

---

## ACTIVITY — the transcript

Every bridge call as a card, newest first, colour-coded by work type:

| Pill | Meaning |
|---|---|
| `READ` (blue) | a read |
| `WRITE` (purple) | a mutation |
| `BLOCKED` (amber) | refused by the safety gate |
| `REFUSED` (grey) | the caller asked for something invalid — a contract refusal, **not a defect** |
| `FAILED` (red) | the handler tried and could not |

### Why REFUSED exists, separately from FAILED

Andre watched a regression through this panel, saw a wall of red `FAILED` cards, and reasonably asked
whether something was broken. **The panel could not tell him** — and that ambiguity was the real
defect, not the failures.

Test suites deliberately call endpoints with bad input to prove they refuse properly.
`test_widget_tree.py:158` is literally titled *"bad arguments are refused"* and calls four endpoints
with `widgetName: "NoSuch_zz"`. Those are the system working. Rendering them identically to a broken
endpoint makes the whole transcript untrustworthy.

So a failure whose reason begins `unrecognised parameter`, contains `is required`, or begins `no ` —
the shapes `RejectUnknownParams` and the not-found paths produce — is a **contract refusal** and gets
the quiet grey pill. Anything else keeps the loud red one, so a genuine breakage still stands out.

Failed cards also show the **reason** on their second line, where a successful call shows its subject.
On a failure the reason is what you want; on a success there is no reason and the subject is.

Each card carries the endpoint, its duration (slow calls colour themselves), a live age, and the
**subject** — what the call was *about*, lifted from the payload. `find_assets` alone says nothing;
`find_assets  /Game/FX/NS_Fire` says what happened. When the subject is an asset path it is clickable
and reveals the asset in the Content Browser.

A **live banner** appears while a handler is running. Handlers execute synchronously on the game thread
inside the HTTP ticker, so if that banner sticks, the endpoint named in it is the one wedging the editor.

### The flag button

One click writes a structured report into `Saved/MifBridge/reports/`, in the shape
`report_intake.parse_report` already validates, for the autonomous loop in `12_AUTONOMOUS_REPORT_LOOP.md`
to reproduce and fix.

**The trust model differs from a GitHub issue, and the difference matters.** That document says the
allowlist *is* the security control, because an issue is written by someone outside this machine. A
report written by this button is not — whoever clicked it is already sitting at the editor with full
access. Identity is therefore not the control here. The DENY list and path rewriting still are, because
those guard against *mistakes and collateral damage* rather than adversaries.

The report is still **data**: the button writes a file and executes nothing.

---

## BRAINMAP — the dependency graph

Custom-painted canvas. Zoom with the wheel (toward the cursor), drag with right or middle mouse, click a
node to reveal it in the Content Browser.

- **Colour** is asset type.
- **Size** is referencer count, so a hub looks like a hub.
- **Zoomed out** you read *shape* — clusters and hubs. **Zoomed in** nodes become the engine's own type
  icons and names appear. That is what makes the zoom meaningful rather than decorative.

Not `SGraphPanel`: that class wants `UEdGraphNode`s, a schema and a `UEdGraph` to own them, none of which
exist for an asset dependency graph, and faking them would put UObject lifetime in the paint path.

The force layout is solved **once**, when the data is fetched. Zoom and pan are a transform, not a
re-solve — this paints inside the editor's Slate pass and the whole design rests on the game thread
staying responsive.

**Icons load nothing.** `FSlateIconFinder::FindIcon` takes a *name*, so `ClassThumbnail.Blueprint`
resolves the Content Browser's own icon without touching the asset — which matters doubly on cooked
content (`02_GOTCHAS.md` §6c). Real asset thumbnails need `FAssetThumbnail`, which produces an `SWidget`
a custom-painted leaf cannot host; that is on the backlog as a genuine refactor.

---

## HEATMAP — what is most connected

Every package under a prefix, sorted by connections, as coloured cards.

**Coloured by rank, not absolute count.** DDS2's busiest package has 873 referencers and the median has
about three; an absolute scale would paint 95% of the list green and the colour would carry no
information at all.

Each card shows **both halves, never only the sum**:

- `873 in / 23 out` — a shared foundation. Deleting it breaks the world.
- `2 in / 200 out` — a god object. It depends on everything.

Opposite problems, similar totals. That is why the sum alone is never shown.

> This view took a fraction of the brainmap's effort and is more useful. A hairball tells you a project
> is complicated; a sorted list tells you *which thing* is complicated, which is the question people
> actually have. Worth remembering before investing more in graph rendering.

---

## PERFORMANCE — cost, and the honest limits of it

**Read this before trusting a number here.**

`get_perf_stats` reports editor frame time and RHI draw calls, and its own caveat is blunt: those
describe *the editor* drawing its own viewport, gizmos and selection outlines included. They are not the
game's FPS. Ranking actors by "FPS cost" measured that way would be inventing precision.

So this tab reports a **census** of static content cost — properties of the content, reproducible, and
things an artist can act on:

| Column | Meaning |
|---|---|
| triangles | LOD0 triangles across the actor's mesh components |
| components | primitive components it drags along |
| materials | material slots — a draw-call proxy |
| `[BP TICK]` | the actor runs **Blueprint** code every frame |

`[BP TICK]` is the closest honest static proxy for Blueprint burn. It is **not a measurement**: a cheap
tick and an expensive one look identical here. That caveat is rendered on screen, not just written in
the source, because this panel will be believed.

Bar length is relative to the **heaviest actor**, not an absolute budget — there is no universal triangle
budget, and inventing one would be a number pretending to be a threshold.

The tab **rebuilds every time it is opened**. An earlier version cached it on first switch and went on
describing a level that was no longer loaded. A stale performance number is worse than none, because it
gets acted on.

### For real frame attribution

```
trace_start   →  do the thing you want to measure  →  trace_stop
```

Writes a `.utrace` to `Saved/MifBridge/Traces` on the `cpu,frame,bookmark,stats` channels. Open it in
`UnrealInsights.exe` (Engine/Binaries/Win64) — a Blueprint's Tick appears there **by name** with its real
cost, which is exactly what the census cannot give you.

`trace_stop` reports the file **size**, because a zero-byte trace means the channels captured nothing and
otherwise looks identical to success. Stopping when nothing was started answers `stopped:false` rather
than failing, so the call stays idempotent.

---

## INHERITANCE — the class hierarchy, without loading anything

The fifth tab. On DDS2 it groups **2855 blueprints** under the native classes they derive from —
`Actor`, `ActorComponent`, `AnimInstance`, `BaseNPC` — with each blueprint tree hanging off its root.

**It loads nothing, and that is the design rather than an optimisation.** A blueprint publishes its
parent as an asset registry TAG (`FBlueprintTags::ParentClassPath`, 5.3 `BlueprintSupport.h:38` / 5.7
`:32`), so the whole hierarchy is metadata the registry already holds. Building it by loading every
Blueprint and asking `GeneratedClass->GetSuperClass()` would be correct, far slower, and on a COOKED
project actively dangerous — `docs/06` issue 16 is an editor that died doing exactly that, and DDS2 is
cooked.

Native roots are **synthesised**. A C++ class is not an asset and has no registry entry, so without
them the view is thousands of disconnected blueprints rather than a hierarchy.

| | |
|---|---|
| **descendant counts** on each row | "this class has 200 things under it" is the most useful number in a tree and is invisible until you expand everything |
| **roots sorted by descendant count** | it opens on what matters, not on whatever sorted first alphabetically |
| **the filter keeps a node if IT or any DESCENDANT matches**, then expands what survived | matching on self alone hides the ancestors of every hit, and results float free of the structure that made them meaningful |
| `registryStillScanning` in the summary | at startup a partial tree is indistinguishable from a small project |

Double-click opens the asset. That is the **one** place this view loads anything, and only because
someone asked for it.

Built **once** on first switch, unlike PERFORMANCE which rebuilds every time — and the difference is
what each view is OF. Performance describes the OPEN LEVEL, which is why caching it once showed a
census of IslaSombra while Untitled_1 was loaded. Inheritance describes the PROJECT's assets, which do
not change while the editor idles, and it has its own refresh button.

`STreeView`, not a hand-painted widget: the brainmap needed force layout because a dependency graph is
a mesh with no root, and inheritance is a literal tree that Slate already has a virtualised,
keyboard-navigable widget for.

## BEHAVIOR — behavior trees and their blackboards

The sixth tab. DDS2 ships **17** behavior trees and the editor's own BT graph was the only way to look
at one, an asset at a time.

**It calls the endpoint's handler**, not a copy of its logic. The view builds a request object, calls
`H_describe_behavior_tree`, and reads the response — the same bytes an agent over HTTP gets. One
implementation, so the panel and the endpoint cannot disagree about a project's AI while both looking
authoritative, and the view doubles as a live test of the endpoint.

Colour is by **KIND**, not by class, because kind is what tells you how a node behaves: a composite
routes, a task acts, a decorator gates, a service ticks.

Decorators and services are **counts on the row**, not child rows. They attach to a node and gate or
tick it; drawing them as children would show a tree the AI does not have.

A tree with no blackboard says outright that every blackboard decorator in it is inert, rather than
just showing nothing.

### The trap this tab found, which anything reusing a handler will hit

`ok:true` **is set by the dispatcher, not the handler.** `Fail()` sets `ok:false` and `error`;
`ok:true` is set by `RunEndpoint` at `MifBridgeCommon.cpp:1214`, AFTER the handler returns.

So a handler called **directly** succeeds by leaving `ok` unset entirely. The first version of this
tab tested for `ok` and reported *"could not describe this behavior tree"* for all 17 trees while the
identical call over HTTP returned a full answer.

**Failure is the presence of `error`, never the absence of `ok`.**

## The endpoints, usable without any widget

| Endpoint | Answers |
|---|---|
| `project_dependency_graph` | nodes and edges under a prefix; each node reports `dependsOn` **and** `referencedBy` |
| `project_asset_distribution` | counts by class and folder |
| `perf_heavy_actors` | the static cost census, sortable |
| `trace_start` / `trace_stop` | Unreal Insights capture |

**Everything here is bounded, and says so when it bounded something.** `GetReferencers` runs *per asset*
and this project has 32,265 of them, so an unbounded graph is a stopped game thread, not a slow answer —
and a handler that blocks the game thread takes the whole bridge offline for its duration. A caller can
retry an error; it cannot cancel a stall.

`project_dependency_graph` therefore refuses a prefix shallower than two segments, and points at
`project_asset_distribution` as the cheap alternative rather than just saying no. `project_asset_
distribution` accepts a bare `/Game` precisely because it never touches referencers.

Every capped result reports both the cap **and** the true total, so a truncated answer can never read as
a complete one.

---

## Related

- `15_SAFETY_GATE_AND_JOURNAL.md` — the gate the ACTIVITY tab displays, and the journal behind it
- `12_AUTONOMOUS_REPORT_LOOP.md` — where the flag button's reports go
- `02_GOTCHAS.md` §6c (cooked assets), §14 (engine-version differences)
- `13_COMPETITOR_GAP_MAP.md`
