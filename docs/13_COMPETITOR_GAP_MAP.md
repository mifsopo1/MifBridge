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

### Engine capability we have NOTHING for (14)

This is the actual roadmap. Ordered by judged value for DDS2 **and** Curfew, not by their list order.

| Module | Subsystem | Why it matters here | Build.cs cost |
|---|---|---|---|
| `CinematicsExt` | Sequencer / LevelSequence | cutscenes, camera work; both projects want it | needs `LevelSequence` |
| `NiagaraExt` | Niagara authoring | we have ONE read endpoint; effects are everywhere | needs `Niagara`, `NiagaraEditor` |
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

**3. The remaining gap is 14 engine subsystems, and 13 of them are gated on Build.cs.** Almost every one
needs a module dependency MifBridge does not currently declare. That is the real bottleneck — not
writing handlers, but deciding which subsystems are worth pulling in. `MifBridge.Build.cs` is Andre's
file, so that list is his call.

## Suggested order, if the goal is parity

1. **Niagara** and **Sequencer** — both projects want them, both are unambiguous wins.
2. **Game Features** — the one on this list that is *about modding*, which is DDS2's whole case.
3. **GAS** — Curfew-shaped.
4. **Water**, **Vehicles** — DDS2 has both already in content.
5. Everything else on demand.

Nothing here should displace fixing a reported bug. Five of the fixes on 2026-08-26 came from consumers
hitting real problems, which is a cheaper discovery channel than any amount of breadth.
