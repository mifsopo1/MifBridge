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


- [x] **Traces** — `trace` added. Line, sphere, box and capsule sweeps in any direction, six named
      channels, multi-hit, actionable hits (actor, label, class, component, impact point, normal,
      distance, bone), an optional `draw` that leaves the ray in the viewport, and a `world` /
      `pieRunning` echo. `ignoreActors` entries that do not resolve are REFUSED, because
      trace_ground's skip-silently version returned confident hits against the very actors a caller
      had excluded.
- [x] **Debug Draws** — `draw_debug` added. Line, arrow, sphere, box, point and string, named
      colours, duration and thickness. Reports which world it drew into and whether PIE is running,
      because a shape drawn into the editor world is invisible during PIE and the call succeeds
      either way. `persistent` is refused on purpose: nothing can clear a persistent shape.
- [x] **Insights & Profiling** — `get_perf_stats` added. Scene census (actors, primitive/static/
      skeletal components, lights, shadow-casting lights, non-opaque material slots, LOD0 triangle
      estimate), RHI draw calls and primitives, process memory, editor frame timing. The census is the
      reliable half and the response says so: editor timings include the editor's own UI and gizmos
      and are NOT the game's performance, so reporting them unqualified would be worse than reporting
      nothing.
- [x] **Behavior Trees / Blackboard** — `describe_behavior_tree` and `list_blackboard_keys` added,
      read-only. The tree walks depth-first with depth/name/class/kind/decorator-count, resolves which
      blackboard it uses, and is bounded at 2000 nodes so a corrupt asset cannot hang the game thread
      (which on this bridge means the whole editor stops answering). Blackboard keys carry their type
      and an `inherited` flag, because an inherited key is usable but not editable on that asset and a
      caller who cannot tell will change one and wonder why nothing happened. Verified against
      PetDogBT (14 nodes) and OponentBB (26 keys). Authoring BT nodes is deliberately not attempted.
- [x] **Skeletal / Sockets** — `list_sockets` added, and the first version was useless: it read
      only the MESH's socket list, and all 12 sampled DDS2 skeletal meshes have zero of those because
      the game keeps sockets on one shared DDS2_CharacterSkeleton. It now reads BOTH lists and tags
      each socket with its `source`, since that decides where you would edit it. Alisha returns 8 real
      sockets - RightHandSocket, LeftHandSocket, FlashlightSocket, RifleSocket, MeleeWeaponSocket,
      headSocket - which is exactly what a mod attaches to.

## Covered by COMPOSITION, not by dedicated endpoints

Four categories that looked like gaps are not. They were judged by category NAME; tested by
capability, the generic endpoints already do the work. Verified against the live editor, not reasoned
about. This is the whole reason the spec insists on reading handlers rather than counting names — and
it is four categories I would otherwise have built redundant surface for.

- [x] **Sound** — every part composes, despite 3771 SoundWaves making it look like the biggest gap:
      discovery with `find_assets {class: SoundWave|SoundCue|MetaSoundSource}`; the graph nodes with
      `add_function_call {function: PlaySound2D|PlaySoundAtLocation, class: GameplayStatics}`, which
      produces a real `SoundBase` pin; assigning an asset to that pin with `set_pin_default`, which
      sets `defaultObject`; and assigning one to an AudioComponent with `set_property`.
- [x] **Character Movement** — `set_property` against the CharacterMovement component sets and reads
      back MaxWalkSpeed, JumpZVelocity, GravityScale and MaxAcceleration. The four numbers this genre
      mods most, all already reachable.
- [x] **Physics** — NESTED property paths work: `BodyInstance.bSimulatePhysics`,
      `BodyInstance.MassScale`, `BodyInstance.LinearDamping`, `BodyInstance.bEnableGravity` all set
      successfully on a StaticMeshComponent.
- [x] **Data Assets** — `find_assets {class: DataAsset}` finds them and `list_object_properties`
      reads them by objectPath.

**The one thing that IS missing here is discoverability, not capability.** Setting a component
property requires the component's `templatePath` from `list_components` — the `_GEN_VARIABLE` path —
and `set_property`'s own parameter help says "objectPath | (blueprintId or path) + widgetName",
mentioning widgets and never components. An agent would reasonably conclude components are
unsupported and go looking for a `set_component_property` that does not need to exist.

- [x] Fixed. `set_property`'s parameter help now says objectPath reaches components via the
      templatePath from `list_components`, and that propertyPath may be nested. Reaching for
      `componentName` or `component` gets a hint naming the exact sequence and three concrete examples
      (an AudioComponent's Sound, a CharacterMovement's MaxWalkSpeed, BodyInstance.bSimulatePhysics).
      The MCP docstring documents the same route. No new endpoint - there does not need to be one.

## From the 13-agent verified audit (2026-08-25)

92 agents, 18 confirmed gaps, **60 refuted**. That refutation rate is the result worth stating: 60
candidate gaps died under adversarial verification, each one an implementation that would have
duplicated something already there. The audit read handlers; the seeding above matched substrings, so
where they disagree this section wins.

- [x] **`list_material_parameters`** — built. The contrast on real shipped content is the whole
      story: `PoleCableMat` reports `numExpressions:0, cooked:true` from the expression listing and
      **7 parameters** from this one; `M_Oceanology_InstBTR` reports **145**, `M_Water_InstBTR` **148**.
      Reports name, type, group, description, sort priority, value, and always ASSOCIATION and INDEX
      (a layer parameter treated as global would make set_material_parameter build the wrong
      FMaterialParameterInfo and silently fail). On an instance each entry carries
      `overriddenOnThisInstance` — 19 of 112 scalars on M_Oceanology_InstBTR are its own rather than
      inherited, which is what decides whether resetting one does anything. Every value read switches
      on the parameter's Type: `FMaterialParameterValue::AsScalar()` is `check()`ed and would
      TERMINATE the editor if asked of a texture. 23 checks in tools/test_material_params.py.
- [x] **Texture and static-switch support in `set_material_parameter`** — done. `textures`
      {name:"/Game/..."} and `switches` {name:true|false} maps, plus singular inference (a bool is a
      switch, a "/Game/..." string is a texture), plus `association`/`index` so LAYER parameters are
      addressable now that list_material_parameters reports both.
      The trap handled: a static switch changes the shader PERMUTATION, not just a stored value.
      Without the `UpdateStaticPermutation` that now follows, the instance reports the new value
      through every read path while rendering exactly as before — ok:true, a correct read-back, and no
      visual change. The response reports `staticPermutationUpdated` and says why it matters, and the
      test asserts THAT rather than the value, because the value reading back correctly is exactly
      what the broken version would also do.
      An unresolvable texture is refused rather than assigned as null, naming whether the path missed
      or hit a non-texture — a null assignment would report success and render black. 22 checks in
      tools/test_material_write.py.
      Note `create_material_instance` still takes scalars/vectors only. Left as-is deliberately: it
      creates and seeds, and the full parameter surface is one `set_material_parameter` call away on
      the instance it just made.
- [x] **`create_asset`** — built. Instantiates a concrete data-asset class at a /Game path, closing
      the asymmetry where create_blueprint could author a UDataAsset subclass nothing could then make
      one of. Bare NewObject rather than IAssetTools::CreateAsset, deliberately: CanCreateAsset raises
      the same FMessageDialog that froze duplicate_asset. Refuses abstract classes (an asset of one
      loads in the editor and fails in the cooked game), Actor/Component classes, and Blueprint
      classes. Registers with AssetCreated + MarkPackageDirty and verifies by path — an unregistered
      object answers every read and evaporates on restart.
      TWO REAL BUGS found by testing rather than reasoning, both cooked-environment specific:
      plain `FPackageName::DoesPackageExist` consults the IoDispatcher, and since /Game resolves
      through a pak container here it answered TRUE for every well-formed path and refused every
      creation — fixed with the FileSystem filter; and `FindObject(nullptr, <package path>)` resolves
      the UPACKAGE, which exists in memory the moment anything touches that path including a previous
      failed attempt, so it now looks for the ASSET inside. 20 checks in tools/test_create_asset.py.
- [x] **`set_struct_member`** — built. Rename, retype and re-default in place, addressed by name or
      GUID. The point is what it PRESERVES: the member's GUID and its position, both of which
      remove + re-add destroys — a new GUID breaks every Make/Break Struct pin bound to it, and the
      append reorders the struct. The tests assert the GUID and order are unchanged, because a version
      that quietly did remove+re-add underneath would pass "the name changed" and fail those.
      The cooked guard held: pointed at a real base-game struct (`UnlockShareData`) it refuses and
      explains, and the test then asserts the editor is still answering — every FStructureEditorUtils
      entry point CastCheckeds the stripped EditorData, so a failed guard is a fatal cast, not an
      error return. Retyping reports `dependentDataTables` and warns, because that column has been
      reset in every row of every table built on the struct and the caller cannot see it from here.
      22 checks in tools/test_set_struct_member.py. This closes docs/audit/work/H_data.md:572, specced
      and CONFIRMED long ago and never built.

## Round two — the confirmed gaps not yet built

The 13-agent audit confirmed 18 gaps. Three are built (`set_struct_member`, `create_asset`,
`list_material_parameters` + the write side). These are the rest that are worth doing, ordered by
value for cooked-game modding. Each still needs its APIs verified against 5.3 before building — the
audit named them, but the audit has been wrong about "cheap" once already (Niagara).

- [x] **Collision profile and channel setup** — and testing first narrowed it sharply. `set_property`
      ALREADY sets `BodyInstance.CollisionProfileName` and `CollisionEnabled`, so "cannot configure
      collision" was wrong. What was missing is VALIDATION and DISCOVERY, and the missing validation
      was the real defect: `set_property` accepts `NoSuchProfile_zz` and reads it straight back,
      leaving the component on its previous collision, configured-looking in every read path, and
      colliding with the wrong things.
      `list_collision_profiles` reports the 19 profiles this project defines with the responses each
      resolves to — and flags a NoCollision profile's responses as MOOT, since that table otherwise
      reads as though NoCollision blocks WorldStatic. `set_collision` checks the name against
      `UCollisionProfile` and reports what it resolved to, because "the profile is set" and "it now
      blocks the player" are different claims. 22 checks in tools/test_collision.py, one of which
      asserts set_property STILL accepts the bogus name — that contrast is why the endpoint exists.
- [x] **Audition a sound in the editor** — `audition_sound` added. Plays any USoundBase (SoundWave,
      SoundCue, MetaSoundSource) through the editor preview device, with `stop:true` to silence it.
      Reports the resolved class and duration, and refuses a non-sound by naming what it actually is.
      A null preview component is reported as "this session has no audio device" rather than as
      success — a silent editor and a quiet asset look identical otherwise.
- [x] **Nav mesh queries** — `nav_project_point` and `nav_find_path` added. Projection reports
      `movedBy` (2cm off the mesh and 300cm off are different problems, and onNavMesh:true hides
      that), and pathing reports `partial` SEPARATELY from `reachable` — a partial path stops at the
      closest reachable point and still looks like a path, which is how "the NPC can get there"
      becomes a lie. "No nav system in this world" is an error, not a "not walkable".
      Tested limitation, stated rather than hidden: the scratch level has no navigable surface (a nav
      volume over a spawned cube built 8 tiles that project to nothing), so the POSITIVE branches are
      unexercised. Worth running against a real DDS2 level before trusting a positive answer — noted
      in the test file's header too.
- [x] **Rename a widget inside the WidgetTree** — `rename_tree_widget` added. The rename is one
      line; the endpoint is the five OTHER places a widget's name is stored, each of which fails
      silently: property bindings (a string), each animation's `FWidgetAnimationBinding` (an FName),
      the MovieScene POSSESSABLE behind it, navigation bindings, and every graph node that gets or
      sets it as a variable (`FBlueprintEditorUtils::ReplaceVariableReferences`).
      The possessable is the sharp one and the test asserts it by number: rename the binding and not
      the possessable and the animation still compiles, still plays, and animates nothing — the same
      two-halves split `add_widget_animation_track` had to handle.
      Replicates `FWidgetBlueprintEditorUtils::RenameWidget` rather than calling it, because that
      needs a live `FWidgetBlueprintEditor` (the asset open in the designer). What is skipped as a
      result — the designer preview and DesiredFocusWidget — is reported, not hidden. 20 checks.
- [x] **Screenshot of what is ACTUALLY rendered** — `capture_viewport`. Reads the real editor
      viewport backbuffer, so it answers "what does this look like right now" with the user's own
      camera, view mode and show flags, where `capture_camera` answers "what would a fresh
      `ASceneCapture2D` see". Synchronous via `FViewport::ReadPixels` rather than
      `FScreenshotRequest`, which resolves at end-of-frame and would hand back a path to a file that
      does not exist yet. 34 checks.

      Two silent failures were found by testing it rather than by reading it, and both are worth
      remembering because both reported `ok: true` with correct-looking JSON:

      * **A stale frame labelled with the wrong camera.** A viewport that is not realtime — which is
        any level viewport once the editor loses focus — does not redraw on its own. Moving the
        camera and capturing returned a BYTE-IDENTICAL image while `cameraLocation` faithfully
        reported the new position. Fixed by forcing `Invalidate` + `Draw` + `FlushRenderingCommands`
        before reading, and the response now says `forcedRedraw: true`.
      * **A fully transparent PNG.** `ReadPixels` returns the backbuffer's alpha, which in the editor
        is leftover renderer state, not coverage — it was 0 on 99.97% of pixels, and
        `PNGCompressImageArray` wrote it out verbatim. Correct RGB underneath, blank page in any
        viewer that honours alpha. It was misread as "the scene is empty" twice before the channel
        was actually measured. `capture_camera` and the thumbnail path were both checked and are
        opaque already; this was ours alone.

      The blank-frame guard started as an all-black check, which the pale frame sailed straight past.
      It now measures UNIFORMITY — the fraction of the frame that is the single most common colour —
      and reports `distinctColours`, `uniformity` and `dominantColour` on EVERY response, not only
      when it decides something is wrong, so a caller can judge for itself.

- [ ] **No endpoint authors an IK Rig or an IK Retargeter.** (Raised by Andre, 2026-08-25.) Confirmed:
      the only endpoint whose name matches is `retarget_variable_node`, which retargets a Blueprint
      variable node and is an unrelated name collision. The registry has zero `IKRigDefinition` and
      zero `IKRetargeter` assets anywhere — and that zero was checked against a class that DOES
      resolve (`ControlRigBlueprint` returns 2, both engine plugin function libraries rather than
      game content), so it is a real absence and not an unregistered-class artifact.

      The demand case is not editing rigs DDS2 already ships, because it ships none. It is IMPORT.
      The project carries several unrelated skeletons — `UE4_Mannequin_Skeleton`, `SK_Mannequin`,
      `DDS2_CharacterSkeleton`, plus vehicle and animal ones (cat, Akita, Dalmatian, Doberman,
      Labrador) — and an animation bought or downloaded against the UE4 Mannequin, which is the most
      common source of both, has no path onto the DDS2 character today. That is a normal thing for a
      modder to want and it is currently impossible through the bridge.

      Before building, settle two things the same way the Foliage item is being settled. First
      whether `IKRig` and `IKRigEditor` are enabled in this project at all — nothing above proves the
      modules are loaded, only that no assets exist. Second whether authoring a retargeter without
      the editor's chain-mapping UI produces anything usable, or whether the honest scope is narrower:
      create the two assets, set source and target meshes, and report the chains for a human to map.
- [x] **Foliage** — `add_foliage_instances` gained a `foliageType` mode. The disagreement is
      settled in the bucket agent's favour, on evidence rather than on either rating: DDS2 ships **42**
      `FoliageType_InstancedStaticMesh` assets and 7 `LandscapeGrassType`, so the game does paint
      through the Foliage system. The synthesiser's "Skip Foliage" was wrong.

      What the existing endpoint did NOT cover turned out to be most of it. Despite the name, it never
      touched the Foliage system — it spawned a bare `AActor` with a
      `HierarchicalInstancedStaticMeshComponent`. Useful (one draw setup instead of 90) but it does not
      appear in Foliage edit mode, and it inherits no cull distance, density, scaling or wind from any
      type. So all 42 of the game's own foliage types were unreachable, and anything added this way
      matched at two metres and culled wrongly at range.

      Extended rather than duplicated, per the "find the existing one and extend it" rule: `mesh` and
      `foliageType` are mutually exclusive, the response reports which `mode` ran, and mesh mode now
      states outright that it is not the Foliage system. Foliage mode reports `requested` alongside
      `instanceCount` so a placement the type itself rejected is visible. 30 checks, including that the
      IFA is findable in the world afterwards and that `totalForType` ACCUMULATES across calls — a
      mode that quietly built a second holder actor would pass a naive check and fail both.

      The read side was deliberately NOT built. `find_assets` + `list_object_properties` already return
      all 125 properties of a cooked FoliageType, and cooked foliage types keep their data (they are in
      the safe half of §6c). That is the audit's own lesson about category-shaped gaps collapsing
      into discoverability, applied to itself.

      Cost one editor crash, recorded as PM-009: `AInstancedFoliageActor::AddFoliageInfo` is public,
      exported and plainly named, and returns an `FFoliageInfo` whose `Implementation` is null, which
      the next `AddInstance` dereferences. `AddFoliageType` is the real API. It also returns a possibly
      DIFFERENT `UFoliageType` than you passed, which would have been the same bug again but silent.
- [x] **Typed read of Niagara User parameters** — `list_niagara_user_parameters`. 36 checks.

      The spec was right that the shape is hostile and my earlier decision-log note claiming
      "reading already works through get_property, with types" was wrong. It does not. `get_property`
      returns 8830 characters in which the NAMES are reachable
      (`ExposedParameters.SortedParameterOffsets[0].Name` resolves fine) and the VALUES are not: they
      are a flat byte array indexed by offset, typed only by `RegisteredTypeIndex`, an index into a C++
      singleton with no reflection surface. So "what is `User.Spawn Rate` set to" was genuinely
      unanswerable by any composition of existing endpoints.

      No Niagara module dependency, on purpose. Linking it to resolve the type registry would mean the
      whole plugin fails to load anywhere Niagara is not compiled in, which is a poor trade for one
      read — of 38 NiagaraSystem assets here, 27 are Ultra Dynamic Sky, 4 are engine templates, 3 are
      Oceanology/Water, and 4 are DDS2's own. The asset is recognised by class NAME, the same string
      discipline the cooked-Niagara duplication guard uses.

      **Nothing is guessed, in either of the two places it would have been easy to.** A byte width
      cannot tell float from int32 from bool, so all three readings are returned side by side rather
      than one being picked — on this project typeIndex 88 holds collision channels whose float
      reading is denormal garbage (1.4e-45) and typeIndex 89 holds bools stored as -1, whose float bits
      are NaN. And `typeIndex` is passed through untranslated, with `valueTypeIndices` reported so a
      caller can cross-reference indices between assets themselves.

      The bug worth remembering, caught by the suite and not by reading: a parameter store has THREE
      parallel arrays (`ParameterData`, `DataInterfaces`, `UObjects`) behind ONE offset list, so an
      Offset is a byte position for a value and an ARRAY INDEX for an object, with nothing saying which.
      Three parameters on one asset all report `Offset=0`. Taking widths across them gave a float a
      width of one byte. The first asset tested had both object arrays empty, which is exactly why it
      looked correct. The fix classifies by a provable rule and then VERIFIES it by tiling, reporting
      `parameterLayoutVerified` and WITHHOLDING values when it fails — 30 of 38 systems verify, 7 have
      no parameters, and 1 is genuinely ambiguous and says so with instructions.

      The write side stays declined, and reading makes that more defensible rather than less: in a
      cooked-game mod you do not edit the asset, you call `SetNiagaraVariableFloat`/`Vec3`/`Bool` on the
      spawned component from Blueprint — and the exact name string this returns is what those take.

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
- [~] **MetaSound authoring** — declined, but note the premise was nearly wrong: DDS2 contains **185**
      MetaSoundSource assets, so this is live content, not an unused system. Authoring MetaSound
      graphs is still a graph editor's job and out of scope. ASSIGNING and listing them is in scope
      and is folded into the Sound item above, which must therefore handle MetaSoundSource and not
      only SoundCue/SoundWave.
- [~] **Gameplay Tags** — declined, and this one was my top priority until it was checked. DDS2 has
      no DefaultGameplayTags.ini, no GameplayTags settings in DefaultEngine.ini or DefaultGame.ini,
      the plugin is not enabled, and DDS2_GameMode has 0 GameplayTag-typed variables out of 50. What
      it uses instead is FName - the class is full of `name` keys and `name -> X` maps. Building a tag
      surface would have been a whole category nobody would touch. Reaching for FName-keyed lookups is
      already covered by the existing variable and map endpoints.
- [~] **PCG** — procedural world generation. A DDS2 mod does not regenerate the world.
- [~] **Slate** — Slate is C++ UI. Mods use UMG, which is covered.
- [~] **Async Tasks** — a Blueprint-graph concern already reachable through the normal node endpoints;
      there is no separate authoring surface to add.

## Settled by evidence, 2026-08-25

All four parked items are resolved by asking the asset registry what DDS2 actually contains, rather
than assuming. Counts are from `find_assets` against the live editor; "class does not exist" means the
engine has no such class registered in this build, which is as definitive as it gets.

- [~] **GAS Abilities / Attribute Sets** — declined. `GameplayAbilities` is not enabled in
      DrugDealerSimulator2.uproject, and `find_assets` cannot even resolve the `GameplayAbility` or
      `AttributeSet` classes. DDS2 does not use GAS, so the entire category is a non-feature here.
- [~] **PCG** — declined, now with evidence: `PCGGraph` does not resolve either. Confirms the earlier
      reasoning rather than resting on it.
- [~] **StateTree** — declined. Class does not resolve; DDS2 does not use it.
- [~] **Sequencer** — declined for now. DDS2 contains exactly **4** LevelSequence assets against 3771
      SoundWaves. Cutscene authoring is not what this game is made of, and a mod adding one is a rare
      case. Revisit only if a mod actually needs it; the MovieScene plumbing from the UMG animation
      work would make it cheap when that day comes.
- [~] **Control Rig / IK & Retarget / Vertex Animation** — declined, and the count backs it: **2**
      ControlRigBlueprints in the whole game. Nothing to mod.
- [x] **Niagara — mostly already covered, and a category count would have scored it zero.** The
      13-agent audit found the reflection dot-path walker descends
      `EmitterHandles[0].VersionedInstance.Emitter.VersionData[0].RendererProperties[0].Material` on a
      SHIPPED COOKED effect, `add_function_call` reaches the whole BlueprintCallable Niagara surface
      (which is what a shipped mod actually contains), `run_console_captured` scrapes real per-emitter
      particle counts from `fx.Niagara.DumpComponents`, and spawning/binding a NiagaraComponent works
      today. No Niagara-named endpoint and no module dependency, and it still works.
- [~] **Niagara User parameters** — declined, and the investigation was worth more than the endpoint
      would have been.
      READING already works: `get_property` on a system's `ExposedParameters` returns the user
      parameters with names and types (`User.Color`, `User.FoamOpacity`, `User.FoamWidthLeft` on
      BoatFoamTrail). It is an awkward shape — a redirect map keyed by
      `(Name="Color",TypeDefHandle=(...))` — so a dedicated endpoint would be ERGONOMICS, not
      capability. Combined with `add_function_call` reaching the whole runtime Niagara surface, the
      category is functional.
      The audit called the write side "cheap to build". That did not survive contact: probing this
      territory CRASHED THE EDITOR, and the crash was not where anyone would have looked — see below.
      Writing a persisted override is not something to guess at on the strength of an estimate.
- [x] **Cooked Niagara duplication crashes the editor — found and guarded.** `duplicate_asset` on a
      cooked NiagaraSystem access-violates inside Niagara's own code:
      `FVersionedNiagaraEmitterData::PostLoad` -> `UNiagaraEmitter::PostLoad` ->
      `UNiagaraSystem::UpdateSystemAfterLoad`, reading 0x30. Cook strips the editor-only emitter data
      that the copy's PostLoad dereferences. No MifBridge frame at the top of that stack, so it reads
      as a spontaneous editor death.
      READING a cooked Niagara asset is safe; DUPLICATION is what re-runs PostLoad and dies. Now
      refused with that explanation. Checked by class NAME rather than type, deliberately: recognising
      an asset in order to refuse it does not justify a dependency on the whole Niagara plugin module,
      and the string check keeps working where Niagara is not compiled in.
      Third member of a family now: cooked structs assert in FStructureEditorUtils, cooked materials
      have no expression graph, cooked Niagara crashes on duplicate. Cook keeps runtime data and drops
      editor data, and editor-side operations fall into the gap.

## Method note

Every gap above was seeded from a mechanical map of the competitor's categories onto
`endpoints_current.json`. A 13-agent workflow is separately auditing the same question by READING the
handlers, with each claimed gap adversarially verified before it counts. Where that analysis
contradicts this file, the analysis wins — it read the code and this file matched substrings.
