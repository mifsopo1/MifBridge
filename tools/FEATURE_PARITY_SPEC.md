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

- [x] **`list_bones`** — the prerequisite that fell out of investigating the IK Rig question, and a
      gap in its own right. Nothing in the bridge could name a bone.
      `USkeleton::ReferenceSkeleton` is a plain C++ member rather than a UPROPERTY, so reflection
      cannot reach it; `get_property` on a Skeleton returns `BoneTree`, which holds per-bone
      retargeting modes and no names. `describe_animation` has curves and notifies but no tracks, and
      `list_sockets` reports things that attach TO bones without enumerating them. No new module
      dependency. Reports the hierarchy, and says WHICH reference skeleton it read, because a mesh and
      its skeleton can hold different bones. 36 checks.
- [x] **IK Rig / IK Retargeter authoring** — 8 endpoints, 67 checks. **Andre overruled the earlier
      decline and was right to.** That decline judged the value from DDS2's skeletons alone; MifBridge
      also runs against his other Unreal projects, which the reasoning never accounted for. The
      superseded analysis is kept below because its measurements were sound even though its conclusion
      was too narrow.

      `set_ik_rig_mesh`, `set_ik_rig_retarget_root`, `add_ik_retarget_chain`,
      `remove_ik_retarget_chain`, `list_ik_rig`, `set_retarget_rigs`, `auto_map_retarget_chains`,
      `set_retarget_chain_mapping`, `list_retarget_chain_mapping`. Proven end to end on a genuinely
      cross-species pair — a 161-bone UE5 Mannequin retargeted onto a 53-bone Akita — built,
      auto-mapped and hand-corrected entirely through the bridge.

      **UE4 SAFE.** IK Rig is a UE5 plugin. Build.cs detects it and defines `MIF_WITH_IKRIG`; the
      `.uplugin` reference is `"Optional": true` so a missing plugin is a logged skip rather than a
      refusal to load MifBridge at all (`PluginManager.cpp:2164`). The endpoints stay REGISTERED
      everywhere and refuse with that reason, so "this engine has no IK Rig" is distinguishable from
      "no such endpoint", and the three-way parity holds on every engine.

      The endpoints are not about reach — `set_property` can write every field they touch. They are
      about correctness: it will happily write a skeleton with an empty reference pose, chains naming
      absent bones, and a mapping to the DEPRECATED `ChainMapping` property that nothing reads, all
      returning ok:true. Three controller calls also lie outright (silent clear, silent rename, and
      `SetIKRig` auto-mapping as a side effect); those are now documented in 02_GOTCHAS §13 and
      guarded here.

      Two findings worth keeping. The Akita's `Spine_01` is a SIBLING branch off `Spine_base`, so an
      obvious-looking `Spine_01 -> Spine_05` chain spans nothing — caught by the descendant check,
      which the engine does not perform. And a parameter originally called `force` was silently
      stripped by this project's own audit harness, which treats `force` as a destructive-operation
      flag alongside `confirm` and `save`; it is now `remapExisting`.

      **Superseded reasoning, kept because the measurements stand:** the mechanism was already known
      available, and the decline rested on DDS2's own skeletons being the same rig.

      **The mechanism is available, and that was worth establishing.** `UIKRigController` and
      `UIKRetargeterController` are both class-level `IKRIGEDITOR_API`, so unlike the Foliage case
      every member is linkable with no per-member trap. Between them they cover the whole loop:
      `SetSkeletalMesh`, `SetRetargetRoot`, `AddRetargetChain`, `SetIKRig(source|target)`,
      `SetPreviewMesh`, and `AutoMapChains` with Exact or Fuzzy (levenshtein) matching. The IKRig
      plugin is `EnabledByDefault: True` and its editor module loads at `PostEngineInit`, the same
      phase as MifBridge. So this item's open question — whether a retargeter can be authored without
      the chain-mapping UI — is answered YES.

      **Why it is declined anyway.** The case for it was IMPORT: bringing an animation authored
      against another skeleton onto the DDS2 character. Measured with the new `list_bones`, that case
      does not exist. `DDS2_CharacterSkeleton`, `SK_Mannequin` and `SK_Mannequin_Arms_Skeleton` are all
      161-bone UE5-Mannequin-structure rigs with **identical bone sets and identical parentage — zero
      bones differ**. They are the same rig. Retargeting between them is not a translation problem, and
      an IK Rig would be an elaborate way to map every bone onto itself.

      What IS missing for that workflow is far smaller and already reachable: `CompatibleSkeletons` is
      empty on all three, and it is a `CPF_Edit`, saved `TArray<TSoftObjectPtr<USkeleton>>` that
      `set_property` can write. (Verified as editable and saved; NOT written, because that would mutate
      a shipped game asset and the audit rules here keep writes to scratch.)

      The skeletons that genuinely differ are the animals and vehicles — cat 50 bones, cow 40, dogs
      50–54, bicycle 9, scooter 10. Cross-species retargeting is what an IK Rig would actually buy
      here, and human-to-dog animation transfer is not a DDS2 modding need anyone has raised.

      **Reopen if** the goal is animal or vehicle retargeting, or bringing in content from a genuinely
      foreign rig (Mixamo, another game). The mechanism is proven and the prerequisite is now built, so
      it would be a build rather than an investigation.
- [~] **The CityAnimations pack ships a mangled skeleton.** Found by `list_bones` rather than looked
      for. `/Game/Animations/AssetPacks/CityAnimations/.../UE4_Mannequin_Skeleton` is not a 68-bone UE4
      mannequin at all: it is a 161-bone skeleton in which **61 bones are named `<realname><index>`**
      — `spine_012`, `index_02_l10`, `ik_hand_r67` — alongside the correctly-named originals, which
      are also present. That is a CONTENT defect, not a bridge one, so nothing here is blocked by it,
      but any animation retargeted through that skeleton will behave oddly. Andre should decide whether
      to repair it or stop using it before anything is built that reads it.
      Marked as not-pursued rather than left open because it is not a bridge capability at all:
      repairing or replacing a shipped content asset is Andre's call, and nothing in MifBridge
      is blocked meanwhile. The finding is recorded here so it is not lost.
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

- [x] **IK Rig GOALS and SOLVERS authoring** — 8 endpoints, 53 checks. The IK half of an IK Rig; the
      retargeting endpoints author a root and chains, which is everything retargeting needs and none of
      what IK needs. `list_ik_solver_types`, `add_ik_solver`, `remove_ik_solver`, `set_ik_solver`,
      `add_ik_goal`, `remove_ik_goal`, `set_ik_goal_bone`, `set_ik_goal_solver_connection`, plus
      `list_ik_rig` extended to report solvers, goals and which solvers each goal reaches.

      The three editor-killing asserts recorded on this item were AVOIDED rather than guarded.
      `SetGoalCurrentTransform` is not exposed at all (it sets a preview pose, so nothing is lost);
      `GetSolverUniqueName` is never called, so solvers are reported by class name; and `AddNewGoal`'s
      two indistinguishable failures are pre-checked separately so the refusal says which.

      Building it caught a real bug in the validator shipped hours earlier: it demanded retarget chains
      and a retarget root from EVERY rig, which called a perfectly good IK-only rig invalid — and
      because a failed structural check gates the engine probe, the one answer that would have settled
      it never ran. `list_ik_rig` now reports `purpose` (retargeting / IK / both / nothing yet) and
      judges the rig against it.

      * `UIKRigController::SetGoalCurrentTransform` does `check(Goal)`
        (`IKRigController.cpp:1243-1244`). Passing an unknown goal name TERMINATES the editor. Any
        "set goal transform" endpoint must resolve the goal itself first and refuse.
      * `UIKRigSolver::GetNiceName()`'s base implementation is `checkNoEntry()`
        (`IKRigSolver.h:63`). A "list solver types" endpoint that calls it on a solver class which
        does not override it terminates the editor.
      * `UIKRigController::AddNewGoal` neither sanitises nor uniquifies, contrary to how
        `AddRetargetChain` behaves. It returns `NAME_None` for BOTH "name already exists"
        (`IKRigController.cpp:900-903`) and "unknown bone" (`:906-912`), so the two must be told apart
        by the caller. Sanitise first with the exported static
        `UIKRigController::SanitizeGoalName(FString&)` (`IKRigController.h:215`).

      Build this only if goals or solvers are actually wanted — pure retargeting, which is what the
      shipped endpoints do, needs neither.

- [x] **Night regression: every `tools/test_*.py` green.** 31 suites, 0 failures, 0 editor deaths,
      against the 285-endpoint build. Worth running rather than assuming: a lot of shared code had moved
      that day, and `test_ik_rig` had already broken from a change made hours after it was written.
      `tools/run_all_suites.py` runs them sequentially - they all drive one editor, and two suites making
      scratch assets at once would interleave in ways that make a failure impossible to attribute. It
      relaunches the editor if a suite kills it and RECORDS that it had to, because a suite that takes
      the editor down is the headline of the report rather than a footnote.

- [x] **Full crash/hang sweep across all endpoints** — clean. 274 endpoints swept (the other 11 sit
      on the harness DENY list: PIE, the saves, `run_console`), five adversarial probes each. **Zero
      crashes, zero hangs, zero bad responses, zero unguarded keys, zero leaks.**

      Two `GHOST_OK`, and only one of them new: `trigger_cook`, which is a false positive — it executes
      nothing, returns `executed:false` and a command plan for a human, and its `asset` parameter only
      substitutes into a `--filter` argument in the returned string. An `executed_nothing()` exclusion
      was added to the sweep for it, because one false positive in a report of one teaches you the
      report is noise.

      A separate lesson came out of triaging it. The findings file is cumulative, append-only and
      untracked, and rows carried no timestamp, so stale results could not be told from current ones —
      several steps went into establishing that seven rows predated the very exclusion written to
      suppress them. `record()` now stamps `ts` and `runId`.

- [x] **Silent-failure hunt — three families down, three real bugs found.** Aimed by
      `tools/coverage_gaps.py` (188 of 285 endpoints named in no suite) rather than by intuition.

      * **DataTables** — `tools/test_datatables.py`, 23 checks. No bug, but `create_datatable` went
        from UNVERIFIED to verified after five days, and `read_datatable` is now cross-checked against
        `get_datatable_row` on a real 268-row table.
      * **Inherited components** — `tools/test_inherited_components.py`, 37 checks. No new bug: PM-007
        holds across all four failure shapes, including the partial ones. It had had no regression test
        at all, and its symptom is invisible from the caller's side.
      * **Enums** — `tools/test_enums.py`, 32 checks, and TWO real bugs. `add_enum_value` appended a
        junk entry under the wrong name and reported success; `list_enum_values` discarded the only
        meaningful name a user-defined enum has. Both fixed, both filed as §10.

      35 suites now, all green, no editor deaths.

- [x] **Hunt round two: interfaces, dispatchers, components — all three clean.** 50 checks across
      `tools/test_interfaces.py` (21) and `tools/test_components_dispatchers.py` (29). Recorded as a
      result rather than passed over: five families were hunted tonight off `coverage_gaps.py`, three
      were clean and two were not, and knowing which is which is worth as much as the fixes when
      someone is deciding where to spend an evening.

      Two assertions from these are worth keeping. `add_interface` CONFORMS the blueprint, creating a
      function graph per non-event interface function — so the meaningful question is not "was it
      added" but "can the blueprint answer its functions afterwards", which a conform that silently did
      nothing would fail. And `set_component_transform`'s per-field `locationApplied` /
      `rotationApplied` / `scaleApplied` flags are asserted in BOTH directions, because a flag that is
      always true carries no information.

      Superseded framing, kept because the aiming method was the useful part: Still named in
      no suite: `add_interface` / `implement_interface_function` / `list_interfaces` /
      `remove_interface`; `add_call_dispatcher` / `list_dispatchers` / `rename_event_dispatcher`;
      `remove_component` / `set_component_transform` / `add_component_bound_event`. Same method — read
      the handler, then attack it by capability and ask whether it reports success while doing
      something else. Two of the three families hunted so far yielded a real bug, so the hit rate
      justifies continuing. The most productive lens
      all session has been "does this report success while doing something else" - it found the
      transparent PNG, the stale viewport frame, the three-space Niagara offsets, the null-solver crash
      and the WidgetAnimation name leak. Pick families with no dedicated suite and test them that way
      rather than by reading. Finish condition: each family either gets a finding filed or is recorded
      as checked, so the morning knows what was covered.

- [x] **DataTable family** — `tools/test_datatables.py`, 23 checks, and `create_datatable` is now
      verified after five days marked UNVERIFIED. It creates a table with a real row struct, and the
      table is confirmed through a DIFFERENT endpoint than the one that made it — a creator confirming
      its own work is the weakest possible evidence. `read_datatable` was cross-checked against
      `get_datatable_row` row by row on a real 268-row DDS2 table, and a nonexistent row name is
      refused rather than fabricated.

      **A deliberate coverage gap remains, and it is recorded in the suite rather than hidden.** The
      success paths of `write_datatable_rows` and `delete_datatable_rows` are NOT exercised: both
      require `confirm=true`, and the audit harness strips `confirm` from every payload alongside
      `save` and `force`. Bypassing that guard to test a write would defeat the point of having it on
      an unattended run. Closing it properly needs someone running with the guard relaxed against a
      scratch table.

      Superseded framing, kept because the measurement was the useful part:
      Measured, not guessed: `tools/coverage_gaps.py` shows 188 of 285 endpoints are never named in a
      test suite, and all six DataTable endpoints are among them - `create_datatable`, `read_datatable`,
      `write_datatable_rows`, `delete_datatable_rows`, `get_datatable_row`, `list_datatables`. This is
      the highest-value block in that list because DataTables are the core of DDS2 modding: items,
      recipes and prices all live in them. `docs/06_OPEN_ISSUES_FROM_USE.md` also records
      `create_datatable` as "IMPLEMENTED 2026-08-21, UNVERIFIED - built but not yet exercised against
      a running editor", which has been true for five days.

      Hunt it the way that has worked all session: by capability, adversarially, asking whether each
      one reports success while doing something else. A row write that reports ok and changes nothing,
      or changes the wrong row, is the failure that matters here - it is silent, and it corrupts the
      thing a mod is actually made of. Finish condition: a suite exists, `create_datatable` is either
      verified or filed as broken, and the open-issues entry stops saying UNVERIFIED.

- [x] **Node-creation endpoints — the largest uncovered block, now driven from the live registry.**
      `tools/test_node_spawns.py`, 42 checks. `add_*` was 33 endpoints named in no suite, and node
      creation is most of what this bridge is for. The failure worth hunting there is not a crash but
      an endpoint answering ok:true with a node guid while the graph gains nothing usable — invisible
      until a compile much later blames something else.

      The suite asks `describe_endpoint` for each `add_*` endpoint's accepted parameters and drives
      every one that needs nothing beyond a graph and coordinates. So a node endpoint added next month
      is covered the day it lands. That matters specifically here: the 33 got uncovered by being added
      one at a time, and a hand-written list would repeat exactly that.

      Every node is checked three ways, because `ok:true` is the thing under suspicion — a guid comes
      back, `get_node` can still resolve that guid in the graph, and the blueprint compiles with all of
      them present. It also asserts the two reads AGREE: a node can resolve individually and be absent
      from `list_nodes`, and disagreement between them is worth catching.

      Clean. One engine behaviour worth not mistaking for a bug: `add_break_struct` works on `FVector`
      and `add_make_struct` refuses it, because breaking needs only read access while making needs
      every member writable from Blueprint.

- [x] **The confirm-gated success paths — eleven endpoints that had no coverage at all.**
      `tools/scratch_confirm.py` + `tools/test_confirm_gated.py`, 33 checks.

      Every suite written tonight ended with the same note: the SUCCESS path of some destructive verb
      is unexercised, because it needs `confirm=true` and the audit harness strips `confirm` alongside
      `save` and `force`. That guard is right — it is why an unattended run cannot destroy a real asset
      — but the cost had reached roughly eleven endpoints, and they are exactly the ones where a silent
      failure costs most.

      Resolved without weakening it. `confirm` is sent only when EVERY path in the payload lies under
      `/Game/_Mif`, checked mechanically, including paths buried in nested dicts and lists. `save` keeps
      NO exemption, because it is the one flag that turns a disposable test artefact into a real asset.
      A payload with no path at all is refused rather than allowed — absence of evidence is not evidence
      of safety, and an endpoint addressed only by guid could be pointing anywhere. The guard is
      self-tested against ten cases including a real path nested inside a scratch payload, and T340
      re-checks it in the suite before anything is trusted to it.

      What the tests ask is not "did it return ok" but whether the dangerous part actually happened:
      a renamed variable takes its Get and Set nodes with it; a removed component PROMOTES its child
      rather than taking it along; removing the middle enum entry leaves the survivors' own names
      intact rather than shifting them; a written DataTable row reads back. All clean.

- [x] **The regression runner now checks REPEATABILITY, not just correctness.** Five suites were
      broken in one night by a single underlying thing: state surviving between runs inside one editor
      session. Unsaved scratch assets live until the process ends, so a suite that hardcodes a scratch
      path creates it on run one and dies in setup on run two, and one that pages its own results falls
      off the end once enough have piled up. Every one had been green for weeks — the set had simply
      never been run twice without a restart in between, which is exactly what an unattended overnight
      run does.

      Fixing the five instances would have left the class open. `run_all_suites.py` now runs every
      suite TWICE by default and names any suite that passed the first time and failed the second,
      because that is the specific signature and it should not have to be spotted in a list. The two
      passes INTERLEAVE — every suite once, then every suite again — since it is often another suite's
      leftovers that break a suite rather than its own, and back-to-back runs would miss that.

      Current state: **78 runs across 39 suites, 0 failed, 0 editor deaths.**

- [x] **Material GRAPH authoring, including the cooked-asset hazard the sweep could not reach.**
      `tools/test_material_graph.py`, 30 checks. `test_material_write` covered instances and
      parameters; the graph half — eight endpoints — was named in no suite, and it is the half with a
      documented way to kill the editor.

      `UMaterialExpression` is `UCLASS(Optional)`, so a cooked package has NO expression graph, and
      `UMaterial::GetExpressions()` dereferences `GetEditorOnlyData()` with no null check. On a cooked
      material that is a crash, not an empty list. **The adversarial sweep could not test this**: it
      hands every endpoint a GHOST path, so it never asked what happens against a real COOKED asset —
      which is the actual hazard, since DDS2 is a cooked game and nearly every material a modder
      touches is cooked.

      Tested against a real one. The read degrades honestly (`ok:true`, `cooked:true`) rather than
      refusing, because "this material has no graph" and "no such material" are different answers a
      modder needs to tell apart; both writes refuse and name the reason; the editor survives all
      three. The guards hold.

      The authoring loop is then asserted end to end by READING the graph back rather than trusting
      the calls — an expression that reports an index but never appears, or a connection that reports
      success while `connectionCount` stays zero, is the failure worth catching.

- [x] **Cooked-asset sweep — a branch the adversarial sweep structurally could not reach.**
      `tools/cooked_sweep.py`. **883 calls across 285 endpoints against REAL cooked assets, zero
      crashes.**

      `fuzz_endpoints.py` hands every endpoint a GHOST path, so it tests the "not found" branch and
      nothing else. It has never asked what happens against a real COOKED asset — and that is the
      branch that matters, because DDS2 is a cooked game: nearly every asset a modder touches is
      cooked, so the untested case was also the common one.

      §6c of the gotchas records why that branch is dangerous rather than merely empty: a cooked
      `UUserDefinedStruct` hits a `CastChecked` that terminates the editor, a cooked `UMaterial` has no
      expression graph behind a null-check-free deref, and a cooked `UNiagaraSystem` crashed on
      duplication. Two of the three are fatal.

      The sweep picks a real cooked asset per class from the live registry and feeds it to every
      endpoint whose parameters plausibly want that class — deliberately conservative, since handing a
      Material to something expecting a Blueprint tests argument validation rather than the cooked
      hazard and would bury real findings in noise. Read-only by construction: `confirm` is never sent,
      the DENY list still applies, nothing is saved. A crash is treated as a finding, recorded with the
      exact asset that caused it so the repro is one call, and the editor is relaunched to continue.

## Deliberately not pursuing

- [~] **`remove_tree_widget` has no confirm gate — left for Andre to decide, not declined on merit.**
      Every other remover requires `confirm:true` (`remove_component`, `remove_variable`,
      `remove_function`, `remove_event_dispatcher`), and this one deletes a widget's whole SUBTREE in a
      single call — four widgets went in one call while testing. Adding the gate would make the family
      consistent and would BREAK any existing caller, which is a judgement about your scripts rather
      than about the code, so it is not something to change unattended at 5am. Marked `[~]` so the stop
      hook does not block on it; `tools/test_widget_tree.py` prints it on every run so it cannot get
      quietly lost. The endpoint now at least reports `removedCount` and `removedWidgets`, so the
      subtree is disclosed either way.

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

## Hunt round three, 2026-08-26 — questions rather than families

The spec has nothing left open, and the families hunted in round two came back clean. So this round
aimed the same lens — *does this report success while doing something else* — at whole QUESTIONS that
applied to every endpoint at once, rather than at one family at a time. The handlers read this round
(`undo_transactions`, `set_texture_settings`, the thumbnail family) were genuinely careful; the value
was in questions nobody had asked of any of them.

- [x] **Does a modal ever reach a caller who cannot click it?** It did, and it was the worst failure
      the bridge has. `set_variable_type` opened a "Change Variable Type" warning whenever the
      variable had ANY referencing node — the endpoint's entire purpose — and a modal on the game
      thread stops the HTTP ticker, so the bridge answered nothing again. PM-011. Closed twice over:
      `FMifScopedDialogSuppression` for that endpoint, and `RunEndpoint` now runs EVERY handler under
      `GIsRunningUnattendedScript`, which is what `UEditorAssetSubsystem` does for the same shape of
      API. `audit_modals.py` models both dialog classes now; it had modelled only `FMessageDialog`,
      which is why it missed this one. `audit_blocking.py` covers the other way the bridge stops
      answering — an unbounded wait — which had no tool at all. 0 undeclared.

- [x] **Does UNDO put back what an endpoint changed?** All nine checked restore exactly, including a
      four-operation `apply_graph_patch` that reverts wholly in one undo. `tools/test_undo_integrity.py`.
      Clean, and recorded as a result.

- [x] **Does something that LOOKS like a read leave a mark?** Measured by dirty packages, which is
      what the editor consults to decide what a save would write. 50 of 64 exercised, none dirtied
      anything. `tools/audit_read_purity.py` NAMES the 14 it could not exercise and says they are not
      evidence — "0 findings" across mostly-failed calls is an untested result, not a clean one.

- [x] **What does the SECOND identical call do?** This found a real bug. `add_component` twice with
      the same name returned ok twice and left `Turret` AND `Turret1`; its three siblings all refuse a
      taken name. Now consistent, checked against the engine's own `GenerateNewComponentName` so the
      rule cannot drift. `tools/test_idempotence.py`.

- [x] **And the mirror question** — what does removing something twice do? The `remove_*` family
      answered consistently, and the question exposed a GAP rather than a bug: there was no
      `remove_event_dispatcher`, though everything else in that family has a remover. Built it: both
      halves, refuses a partial removal, reports how many call/bind nodes it orphans. **286 endpoints.**

- [x] **Thumbnails and textures — the two rendering families with no suite.** Thumbnails came back
      clean: the orbit parameters each change the rendered bytes (the `capture_viewport` stale-frame
      failure), and the alpha channel is measured rather than assumed. Textures turned up a small real
      one: `set_texture_settings` recommended `compressionSettings:UserInterface2D` in four help
      strings and its own parser refused it, because that is the DETAILS-PANEL name for
      `TC_EditorIcon` and the parser matched authored names only. Fixed in the shared enum parser, so
      `lodGroup`, `mipGenSettings` and `filter` gain it too.

- [x] **Pins, function flags, node search, transactions — the last four families with no suite.**
      One fix and three clean. `set_function_flags` did not say its change waits for a COMPILE: the
      flags live on the function's entry node (which is what it writes and reads back), but what
      executes is the generated class, so `describe_class` answers the pre-compile value and a caller
      checking their own write concludes it failed. Measured false / false / true across the compile.
      It now reports `needsCompileToApply`, as the widget-tree endpoints already did.

      The other three hold, including the one with history: `set_pin_type`'s silent revert is properly
      closed — a node that derives pin types from its connections puts the wildcard back, and the
      endpoint now FAILS naming both what was asked and what the pin actually is. Undo and redo both
      work end to end. Worth knowing from `find_nodes`: `byClass` matches the C++ CLASS and `byTitle`
      matches what you SEE, and they differ for the commonest node there is — a Branch node's class is
      `K2Node_IfThenElse`, so searching `byClass:"Branch"` correctly finds nothing, which reads like an
      empty graph rather than a wrong query.

- [x] **Whole-surface re-verification after the night's C++ changes.** Seven source files changed, so
      the broad sweeps were re-run against the final build rather than trusting the suites:
      **904 calls across 286 endpoints against real COOKED assets, 0 crashes**; parity clean (286 MCP
      tools ↔ 286 endpoints, no drift); `audit_modals` 7 guarded / 0 unguarded / 0 citation drift;
      `audit_blocking` 0 undeclared; `audit_read_purity` 0 reads dirtied a package.
      **104 runs across 52 suites, 0 failed, 0 took the editor down.**

**What this round taught about method.** Aiming at a question rather than a family found more, because
the families are in good shape and the questions were not being asked of any of them. Two of the
findings came from the same shape — *an engine "add" that takes a name and hands back a different
one, reporting success* — in unrelated subsystems (`GenerateNewComponentName`,
`GetUniqueRetargetChainName`). Worth carrying forward as a standing suspicion rather than a fact
about components.

Also worth carrying forward, from nearly reverting a good change: **never compare timings across two
editor sessions without checking focus.** UE throttles Slate's tick when the editor is not the
foreground window, and per-call latency IS one tick. The suite set looked 3x slower after a commit;
with focus restored that build was FASTER than the baseline it was being blamed against.

## Method note

Every gap above was seeded from a mechanical map of the competitor's categories onto
`endpoints_current.json`. A 13-agent workflow is separately auditing the same question by READING the
handlers, with each claimed gap adversarially verified before it counts. Where that analysis
contradicts this file, the analysis wins — it read the code and this file matched substrings.
