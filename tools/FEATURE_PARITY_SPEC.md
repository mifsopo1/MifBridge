# MifBridge feature parity spec

What this is: MifBridge's surface measured against the ~50 categories Ultimate Engine CoPilot
advertises (gamedevcore.com/features, "1,450+ tools across 56 categories"). The Stop hook
`~/.claude/hooks/autopilot-continue.js` reads this file and keeps working while anything is still
`- [ ]`.

**Three states, and the third one matters:**

- `- [x]` — covered. Endpoints exist and are tested.
- `- [ ]` — a gap worth closing. The hook blocks on these.
- `- [~]` — deliberately NOT pursuing, with the reason on the next line. The hook ignores these.

Without `- [~]` this spec would be an infinite loop, because several of the competitor's categories
are structurally irrelevant to modding a cooked game and no amount of work would ever tick them off.
Declining something explicitly, with a reason, is a finished decision — not an unfinished task.

**The measuring stick.** Value is judged for *DDS2 cooked-game modding on CookedEditorModKit*, not for
general Unreal development and not for how impressive a feature list looks. A category the competitor
wins on that a DDS2 modder would never reach for is not a gap worth money or time.

**Coverage is judged by reading handlers, not by endpoint names.** Names mislead. The authoritative
endpoint list is `tools/endpoints_current.json`, regenerated from the live editor's `self_audit`.
Never work from a typed list — one was fabricated once and a third of it was invented.

---

## Covered

- [x] **Blueprints** — 66 endpoints. Graph editing, nodes, pins, variables, functions, macros,
      events, casts, branches, switches, interfaces, compile, plus `apply_graph_patch` with real
      rollback. This is the deepest part of the surface and almost certainly deeper than the
      competitor's, because it is verification-heavy rather than generation-heavy.
- [x] **Actors** — 15. Spawn, transform, select, duplicate, delete, bounds, snap-to-ground, tags.
- [x] **Materials** — 11. Expressions, connections, functions, instances, recompile.
- [x] **Widgets/UMG** — 16. Widget tree, bindings, properties, and full WidgetAnimation authoring
      (create/track/key/remove across translation, opacity, colour and visibility).
- [x] **Data Tables** — 7. Create, read, write, delete rows.
- [x] **Structs** — create, members, make/break nodes.
- [x] **Enums** — create, values, literals, switch nodes.
- [x] **Landscape** — 7. Create, sculpt, paint, RVT binding, draw diagnostics.
- [x] **Content Browser** — 11. Find, import, export, rename, duplicate, delete, folders, metadata.
- [x] **Camera / Level Viewport** — get/set viewport camera, focus, screenshot capture.
- [x] **Nav Mesh** — nav volume, build, status.
- [x] **Cook & Package** — dirty packages, save, trigger cook, cooked-blueprint listing.
- [x] **Runtime Console** — `exec_console`, `run_console`, `run_console_captured`.
- [x] **Editor Utilities** — editor commands, tabs, key sends.

## Gaps worth closing

Ordered by value for DDS2 modding. Each needs: endpoints, engine APIs, and an answer to "what would
fail silently if this were done carelessly".

- [ ] **Gameplay Tags** — no endpoints at all. DDS2 is a systems-heavy game and tags are how such
      games gate behaviour; a modder adding an item or interaction will hit this. Needs tag listing,
      tag-container get/set on assets and actors, and the tag-literal graph node.
- [ ] **Sound** — no endpoints. Playing, finding and assigning SoundBase/SoundCue on actors and
      components is ordinary mod work (a new item that makes a noise). Needs listing, assignment,
      and the PlaySound2D/AtLocation nodes.
- [ ] **Data Assets** — no endpoints. `UPrimaryDataAsset` subclasses are a normal way for a game like
      DDS2 to carry item/recipe definitions, and a modder needs to read and create them.
- [ ] **Physics** — only collision add/remove. Missing: simulate-physics toggles, mass/damping,
      constraints, and the physics-body property surface on components.
- [ ] **Traces** — only `trace_ground`. A general line/sphere/box trace with channel selection is
      broadly useful and cheap, and `trace_ground` already proves the pattern.
- [ ] **Debug Draws** — nothing. Draw-debug-line/sphere/box/string in the editor world is how an
      agent SHOWS its work; it is also how a modder verifies placement without a screenshot.
- [ ] **Insights & Profiling** — nothing beyond `diagnose_landscape_draws`. Basic frame/draw-call/
      memory stats would let an agent answer "is this mod expensive?" instead of guessing.
- [ ] **Behavior Trees / Blackboard** — nothing. DDS2 has AI (dealers, police, NPCs) and a modder
      changing NPC behaviour would need at least to READ existing trees and blackboards. Read-first
      is the sensible scope; authoring BT nodes is a much larger job.
- [ ] **Skeletal / Sockets** — nothing. Attaching a mod's mesh to a character socket is common, and
      currently there is no way to even list sockets.
- [ ] **Character Movement** — nothing. Speed, jump, crouch and gravity on a CharacterMovementComponent
      are the most-modded numbers in this genre.

## Deliberately not pursuing

- [~] **C++ & Modules** — a DDS2 mod is Blueprint plus a `_P` pak. Cooked-game mods cannot add
      C++ modules, so "read and write .cpp/.h and modify the codebase" has no target here. This is a
      real competitor advantage for general UE development and a non-feature for this use case.
- [~] **Build Config** — same reason. The mod build is `trigger_cook` plus pak, which is already
      covered; there is no per-mod build configuration to edit.
- [~] **MetaHuman** — requires MetaHuman assets and the plugin pipeline; not present in DDS2 and not
      something a mod ships.
- [~] **Chaos Vehicles** — DDS2 has no Chaos vehicle setup to mod.
- [~] **Control Rig / IK & Retarget / Vertex Animation** — animation *authoring* pipelines. A mod
      reuses the base game's rigs and animations; authoring new ones is a content-creation workflow
      done in the full editor, not through a bridge.
- [~] **MetaSound** — MetaSound authoring is a graph editor of its own. Assigning existing sounds is
      the modding need, and that is covered by the Sound gap above.
- [~] **PCG** — procedural world generation. A DDS2 mod does not regenerate the world.
- [~] **Slate** — Slate is C++ UI. Mods use UMG, which is covered.
- [~] **Async Tasks** — a Blueprint-graph concern already reachable through the normal node endpoints;
      there is no separate authoring surface to add.

## Needs a decision before it can be scoped

- [ ] **Niagara** — decide whether DDS2 mods realistically ship new VFX. If they do, READ endpoints
      (list systems, describe emitters, set user parameters) are the useful half and authoring is not.
      If they do not, move this to "not pursuing" with that reason. Do not build authoring on spec.
- [ ] **Sequencer** — same shape. Level sequences matter if mods add cutscenes; if DDS2 mods do not,
      decline it. The UMG WidgetAnimation work already covers the MovieScene plumbing that would be
      reused, so the incremental cost is lower than it looks.
- [ ] **GAS / Attribute Sets** — establish whether DDS2 uses the Gameplay Ability System at all.
      Grep the cooked content for `UAbilitySystemComponent` / `UGameplayAbility` subclasses. If it
      does not use GAS, this whole category is a non-feature and should be declined outright.
- [ ] **Multiplayer / Replication** — DDS2 has co-op. Establish whether mod work touches replication
      (replicated variables, RPCs, `Switch Has Authority`) beyond what the Blueprint endpoints already
      do. `set_function_flags` already covers replicates/reliable, so this may already be covered.

## Method note

Every gap above was seeded from a mechanical map of the competitor's categories onto
`endpoints_current.json`. A 13-agent workflow is separately auditing the same question by READING the
handlers, with each claimed gap adversarially verified before it counts. Where that analysis
contradicts this file, the analysis wins — it read the code and this file matched substrings.
