# Capability gap map — Ultimate Engine CoPilot v1.7.3

**Compiled 2026-08-26 from the trial archive's FILE LISTING only.** No installation, no extraction, no
source. The trial ships no C++ source in any case (their own trial/full comparison table says so), and
their FAB listing is marked "Allows usage with AI: No" — so nothing here comes from reading their code.
Every fact below is derived from 42 DLL filenames in `UECP-Trial-Windows-UE5.7-v1.7.3.zip`, which name
their modules, plus their public listing.

## The number that matters first

**Their plugin does not support UE 5.3.** The trial ships for 5.5, 5.6, 5.7 and 5.8 only. The DDS2 SDK
is 5.3.2, so Ultimate Engine CoPilot *cannot run on it at all*. For cooked-DDS2 work there is no
competitor — the comparison exists only on 5.5+, which is where Curfew lives.

## Their 42 modules, sorted by what they mean for us

### Not engine capability — their PRODUCT (13)

These are the application around the tools, not tools themselves. They do not translate into endpoints
and should not be counted when sizing parity.

| Module | What it is |
|---|---|
| `Core`, `Shell`, `Tools` | plugin scaffolding and tool dispatch |
| `AiMemory`, `Gdd` | their memory / design-document features |
| `Crew` | multi-agent orchestration |
| `Voice` | voice input |
| `Learning`, `Feedback` | in-editor tutorial and telemetry |
| `Architect`, `Analyst`, `ProjectScanner` | their analysis UIs (dependency graph, heatmaps) |
| `MCPBridge` | their equivalent of *this entire project* |

`MCPBridge` is worth dwelling on: one of their 42 modules is the thing MifBridge is. The other 41 are
what they built on top of it.

### Engine capability we already cover (12)

| Module | MifBridge equivalent |
|---|---|
| `BlueprintExt` | the largest family here — nodes, pins, graphs, variables, functions, dispatchers |
| `AnimationExt` | describe_animation, list_bones, the IK Rig family, blendspace samples, retargeting |
| `MaterialExt` | expressions, parameters, instances, layers, recompile |
| `DataExt` | DataTables, structs, enums |
| `LevelDesignExt` | spawn_many, foliage, landscape sculpt/paint, snap_actors_to_ground |
| `UIExt` | UMG widget tree, widget animations, bindings |
| `SearchExt` | find_assets, find_nodes, list_* |
| `AssetGen` | create_asset, create_blueprint, create_struct, create_enum, import_asset |
| `EditorUIExt` | invoke_editor_command, editor tabs, viewport capture |
| `CppExt` | *partially* — we read C++ classes but do not generate them |
| `PythonExt` | not needed; the bridge IS the scripting surface |
| `InputExt` | add_enhanced_input_action, list_input_mappings |

### Started since this map was written (2)

| Module | Subsystem | What landed | Still missing |
|---|---|---|---|
| `CinematicsExt` | Sequencer / LevelSequence | `list_level_sequences`, `describe_level_sequence` | tracks, sections, bindings by name; all writes |
| `NiagaraExt` | Niagara | `describe_niagara_system`, `list_niagara_emitters` (+ the pre-existing `list_niagara_user_parameters`) | renderers, per-emitter detail, component overrides, all writes |

Both are READ-ONLY on purpose. Reads are safe, testable against real content and immediately useful;
a write into a MovieScene or a Niagara graph needs a rollback story this project has not built yet, and
PM-007 records that `FTransaction::Cancel()` does not provide one for free. Niagara is additionally
sharp: gotchas section 6c has a cooked `UNiagaraSystem` killing the editor, so nothing here duplicates,
reinitialises or compiles a system.

The Build.cs bottleneck this map called "the real one" is GONE - all 14 subsystems below now have their
module dependencies declared, guarded by `MIF_WITH_*` so a missing plugin refuses by name instead of
stopping the whole plugin from loading. Writing handlers is now the only remaining cost.

### Engine capability we have NOTHING for (12)

This is the actual roadmap. Ordered by judged value for DDS2 **and** Curfew, not by their list order.

| Module | Subsystem | Why it matters here | Build.cs cost |
|---|---|---|---|
| `AudioExt` | MetaSounds, audio | we have `audition_sound` and nothing else | needs `AudioExtensions`/`MetasoundEngine` |
| `GASExt` | Gameplay Ability System | Curfew is a roguelike; GAS is the natural fit | needs `GameplayAbilities` |
| `GeometryScriptExt` | Geometry Script | procedural mesh work | needs `GeometryScriptingCore` |
| `LevelSnapshotExt` | Level Snapshots | capture/restore level state — useful for testing | needs `LevelSnapshots` |
| `GameFeaturesExt` | Game Features / Modular Gameplay | genuinely relevant to MODDING | needs `GameFeatures` |
| `MVVMExt` | UMG ViewModels | modern UI binding | needs `ModelViewViewModel` |
| `WaterExt` | Water | DDS2 has boats and coastline | needs `Water` |
| `VehicleExt` | Chaos Vehicles | DDS2 has vehicles | needs `ChaosVehicles` |
| `MoverExt`, `ChaosMoverExt` | Mover / Chaos movement | newer movement stack | needs `Mover` |
| `MassEntityExt` | Mass Entity | crowds; heavy, niche | needs `MassEntity` |
| `LiveLinkExt` | LiveLink | mocap/external data; niche for both | needs `LiveLink` |
| `MediaExt` | Media framework | video playback; niche | needs `MediaAssets` |

## What this changes about the 1400 target

Three things, all of which make parity cheaper than the headline number suggests.

**1. Their unit is not our unit.** Their own trace screenshot shows `edit_component_property` invoked
four times as four separate tool calls; that is one endpoint here. If their granularity is 3–5× finer,
1,450 tools is on the order of 300–500 endpoints of actual surface — and we are at 277.

**2. A third of their modules are product, not capability.** 13 of 42 are their app: memory, voice,
crew, GDD, scanners, and their own MCP bridge. Nothing to match there unless we decide to build an app,
which is a different argument.

**3. The Build.cs bottleneck is resolved.** When this map was written, 13 of the 14 subsystems needed a
module dependency MifBridge did not declare, and that - not writing handlers - was the bottleneck.
Andre authorised the change on 2026-08-26 and all 14 are now declared, each behind a `MIF_WITH_*` guard
so an absent plugin produces a named refusal rather than stopping MifBridge from loading at all.
Only `Mover` is deliberately left out: it is 5.7-only and needs an `ENGINE_MINOR_VERSION` guard first.

## Suggested order, if the goal is parity

1. ~~**Niagara** and **Sequencer**~~ — both STARTED, read halves delivered 2026-08-26.
2. **Game Features** — the one on this list that is *about modding*, which is DDS2's whole case.
3. **GAS** — Curfew-shaped.
4. **Water**, **Vehicles** — DDS2 has both already in content.
5. Everything else on demand.

Nothing here should displace fixing a reported bug. Five of the fixes on 2026-08-26 came from consumers
hitting real problems, which is a cheaper discovery channel than any amount of breadth.
