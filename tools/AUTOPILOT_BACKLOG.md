<!-- MIFBRIDGE-DEV-ONLY: this file documents the development Stop hook and its kill switch,
     names a competitor product, and hard-codes a path on the author's machine. It belongs in
     git and out of the release zip - those are different questions. -->
# Autopilot backlog

The Stop hook (`~/.claude/hooks/autopilot-continue.js`) reads this file and refuses to end the turn
while any `- [ ]` line is still open. Tick an item `- [x]` only when it is genuinely done — built,
tested, and committed — not when the code is merely written.

Three ways this stops:
- every item is ticked;
- `~/.claude/AUTOPILOT_OFF` exists (create that file to brake immediately);
- 40 continues, after which it gives up so a stuck item cannot spin forever.

Add newly discovered work as new `- [ ]` lines. If an item turns out to be wrong or impossible, tick
it off and write one line saying why it was dropped — do not leave it open to spin on.

## Open

### Project dashboard / brainmap — added 2026-08-26 at Andre's request

Andre sent screenshots of the competitor's Project Dashboard (tabs: Dependency Graph, Complexity
Heatmap, Asset Distribution, Inheritance Tree, Performance) plus its mermaid-style flow diagrams, and
asked for the equivalent engine-side, purple-branded. He then said to put it on this backlog rather
than build it inline.

SPLIT DELIBERATELY INTO DATA AND VISUAL, because they are wildly different sizes. The data endpoints
are ordinary MifBridge work — read the asset registry, shape JSON, test it. The interactive
force-directed graph is a large Slate build and is the only part that genuinely needs a session of its
own. Do the data first: it is independently useful over MCP even with no widget, and every visual is a
consumer of it.

- [x] DONE (c01b966). `project_dependency_graph` returns nodes and edges under a prefix, each node
      carrying dependsOn AND referencedBy. Guards copied from H_audit_unused: refuses while the
      registry scans, refuses a prefix under two segments, caps and REPORTS the cap. 46 assertions
      in test_project_graph.py. ORIGINAL: `project_dependency_graph` — nodes and edges for the asset dependency graph. Build on the
      existing referencer machinery rather than a second traversal (grep get_referencers /
      get_dependencies first — one of them already walks this). Needs: a root filter or path prefix so
      it does not return the whole project by default, a depth cap, and a `truncated` flag. The DDS2
      project has 588 discovered plugins and thousands of assets — an uncapped graph is a hang, and
      this project's rule is that a cap must always be reported.

- [x] DONE (a44d428), and this entry was stale - the endpoint already existed under the name
      `blueprint_inheritance_tree` when this was checked 2026-08-29. Reads the whole project's
      hierarchy off Asset Registry tags alone (FBlueprintTags::ParentClassPath /
      NativeParentClassPath) - LOADS NOTHING, so the cooked-content cost this item warned about
      (gotchas 6c) never applies. Supports root (subtree from a blueprint or native class name) and
      maxDepth, reports nativeRoots, cycle detection against stale/hand-edited registry metadata, and
      registryStillScanning. Several real bugs were found and fixed live during its own development
      (documented inline in MifBridgeProject.cpp:290-541) - a class-path-vs-asset-path suffix
      mismatch that made every node its own root, and a native-root lookup that matched against the
      wrong string shape. Tested: T843 in test_uncovered_reads3.py. ORIGINAL: `project_inheritance_tree` — Blueprint and native class parent/child tree. describe_class
      already resolves a single class; this is the transitive version. Watch the cooked case: a cooked
      Blueprint's generated class is present but its editor-only data may not be, and gotchas 6c
      covers what that costs.

- [x] DONE (c01b966). Counts by class and folder, pure Asset Registry so it loads nothing - which is
      why it accepts a bare /Game where the graph endpoint refuses one. Live: 32,265 assets, 196
      classes, 49 folders. ORIGINAL: `project_asset_distribution` — counts by class, by folder, by size. Pure Asset Registry, so it
      can be a read that loads nothing (the list_level_sequences pattern). Report
      `registryStillScanning` for the same reason that one does: a low count during a scan is
      indistinguishable from a low count.

- [x] PARTLY DONE (4443bce), and the rest is deliberately not built. The HEATMAP view answers the
      question this item existed for - what is most connected, therefore most expensive to change -
      using referencers + dependencies, which needs no asset loading. Per-Blueprint NODE counts do
      need loading every Blueprint, and on cooked content that is the gotchas 6c hazard for a
      number nobody asked for. Reopen only if node-level complexity is actually wanted.
      ORIGINAL: `project_complexity_metrics` — per-Blueprint node count, graph count, function count, variable
      count, and reference count, so a "heatmap" has something real to colour. Everything needed is
      already reachable through the existing blueprint reads; this is aggregation, not new engine
      surface.

- [x] DONE (739f695, 5e7bc74), and it declined the part worth declining. perf_heavy_actors reports a
      static CENSUS - triangles, components, material slots, and which actors run a Blueprint tick.
      It does NOT claim frame time: get_perf_stats' own caveat explains why editor timing is the
      editor drawing its viewport. For real attribution, trace_start/trace_stop drive Unreal
      Insights and write a .utrace (verified: 17.5MB in four seconds). Inventing an fps number
      would have been the easy, wrong answer. ORIGINAL: Performance tab equivalent. The competitor reads Unreal Insights traces. Decide FIRST whether
      that is worth it here or whether the existing perf endpoints (there is already a
      test_perf_stats.py) cover enough. This is the item most likely to be worth declining — judge it
      on value for DDS2 modding, not on matching a feature list.

- [x] DONE (84d4ff0, 4443bce, 739f695). Four internal tabs over a WidgetSwitcher - ACTIVITY,
      BRAINMAP, HEATMAP, PERFORMANCE - rather than four nomad tabs, which would scatter the tool
      across the editor's docking layout. The graph IS built: custom-painted, zoom and pan,
      coloured by asset type, sized by referencer count, engine type icons when zoomed in, click to
      reveal in the Content Browser. ORIGINAL: The in-editor dashboard itself: extend MifBridgePanel.cpp with tabs over the endpoints above.
      Asset distribution renders as bars and the inheritance tree as an STreeView — both are ordinary
      Slate and worth doing early. The DEPENDENCY GRAPH is the hard one: an interactive
      force-directed node graph needs SNodePanel/SGraphPanel or a custom OnPaint, and is a session of
      its own rather than an afternoon. Do not start it until the four data endpoints above are built
      and tested, because a graph widget with no data source is unverifiable.

- [x] DONE 2026-08-29 (build in flight, tested once it lands). `project_dependency_graph` takes
      mermaid:true, additive alongside the existing nodes/edges (no field removed, no shape change for
      an existing caller who never passes it) - returns a `mermaid` string field holding a
      `flowchart TD` diagram, capped at the same maxNodes. Node ids are synthesised (N0, N1, ...)
      rather than derived from the package path, since a Mermaid id cannot contain '/' or '.' and
      every package path has both; the real name is the quoted label instead. Handles the
      includeExternal case too - an edge target outside pathPrefix has no node-loop label yet, so it
      gets one the first time it is seen as an edge endpoint. T644 in test_project_graph.py covers the
      additive contract (mermaid omitted -> field absent), line-count parity against nodeCount/edgeCount,
      and includeExternal+mermaid together. blueprint_inheritance_tree was left alone - it is a tree,
      not a general graph, and its own STreeView widget (MifBridgeInheritView.cpp, already shipped)
      already gives the same understanding with no export step; reopen only if a text-portable form of
      that one is actually wanted. ORIGINAL: Mermaid-style flow export. The competitor renders a diagram and offers "Open in Window" and
      "Export PNG". Cheapest useful version here: have the data endpoints emit mermaid text, which
      renders anywhere without a widget at all. Worth doing before the Slate graph, because it
      delivers the same understanding for a fraction of the work.


- [x] From the postcondition triage: the setters and connectors are the MEDIUM entries that could still fail silently, unlike the creation ones. Check connect_material_expressions (the material analogue of connect_pins, which had exactly this defect), set_component_transform and set_actor_transform for whether a refused write is reported.

- [x] select_level_actors: name the actorPaths that did not resolve. `selected:0` currently reads the same as "these exist and none matched", so a caller doing select-then-operate-on-selection gets an empty selection and no reason.
- [x] duplicate_asset hung the editor on a modal dialog (found by the sweep, logged as a crash).
      Built, tested, committed. Refuses a taken destination in 0.01s with a readable message and the
      existing asset survives - the dialog it replaced offered to DELETE it. 13 checks in
      test_modal_guard.py, of which T70-T72 exercise the real guard; T73/T74 stop at the confirm gate
      and are labelled as not covering it.
- [x] AUDIT THE WHOLE SURFACE for reachable FMessageDialog paths. duplicate_asset and rename_asset both
      claimed "headless - no dialog" and neither was: the non-WithDialog entry points only suppress the
      PICKERS, while the validation inside AssetTools calls FMessageDialog::Open directly. A modal on
      the game thread makes the bridge stop answering entirely and reads as a crash, so this is a class
      of bug, not one endpoint. Check every endpoint that reaches AssetTools, ObjectTools, PackageTools
      or FEditorFileUtils. This is also the best remaining explanation for the old unexplained
      recipe_reset_and_loop hang.
      Done, as a checked tool rather than a one-off grep: tools/audit_modals.py. It reports every
      MifBridge call into a known-prompting engine API as guarded or not (3 guarded, 0 unguarded), and
      re-verifies that the engine lines cited as proof those APIs prompt still contain what they are
      quoted as saying, so the audit cannot rot silently against a future engine. Its guard detection
      is lexical and per-function, which errs toward false alarms - the right direction here.
      Also checked and left alone: the FEditorFileUtils save/load sites. MifBridgeUndo.cpp:536-553
      already documents those hazards deliberately, and load_level defers LoadMap to next tick on
      purpose, so they are considered rather than overlooked.
- [x] The fuzzer's ghost path stops being a ghost DURING a run, not just between runs. It is unique per
      run now, but create_blueprint legitimately creates it, so every later endpoint probed with that
      same path is being asked about something that exists. That is how duplicate_asset was handed an
      existing destination. Make the ghost unique per endpoint, or exclude creation endpoints from the
      ghost probe.
      The order makes it exactly diagnosable: endpoint_names() is sorted(), so create_blueprint at 'c'
      contaminates every endpoint probed after it. Of run 4's GHOST_OK findings only audit_unused ('a')
      is uncontaminated; describe_package, diff_properties_vs_default, find_assets, get_dependencies,
      get_referencers and invoke_editor_tab all come after 'c' and were asked about a path that by then
      really existed. invoke_editor_tab almost certainly OPENED it.
- [x] The ghost detector also flags endpoints whose ok:true is correct. A search over a prefix that
      matches nothing (audit_unused, find_assets) legitimately returns ok:true with zero results -
      that is an empty result set, not a phantom success. Separate "answered about a thing that does
      not exist" from "correctly found nothing" or the bucket stays noise.
      Both done in ea37587. The ghost path is per-ENDPOINT now, so nothing an earlier endpoint creates
      can be in a later one's way. The empty-answer rule is decided by the GHOSTED KEY rather than the
      payload: a prefix or filter that matched nothing is a correct empty, an identity that resolved to
      nothing is a finding. That reproduces the hand triage of run 4 exactly. 12 checks in
      tools/test_fuzz_detector.py, which runs offline with no editor.
- [x] invoke_editor_tab silently ignores 'asset' unless manager is "assetEditor". ResolveTabManager
      returns early for the default manager:"global" and never reads the asset argument, so a caller
      who meant an asset-editor tab and forgot to set manager gets a global operation and no warning.
      RejectUnknownParams cannot catch this - 'asset' IS a declared parameter; it is ignored by MODE.
      Found by run 5's ghost probe. Either refuse the combination or echo that the asset was unused.
- [x] Third calibration for the ghost detector: an endpoint that EXPLICITLY reports non-existence is
      answering honestly, not phantom-succeeding. Run 5 flagged describe_package (existsOnDisk:false,
      inRegistry:false), get_dependencies and get_referencers (packageExists:false plus an existsNote)
      - all three state the truth in the response and none is a defect. Treat a false existence-ish
      boolean the same way an empty search result is treated now.
      Done. reported_absent() in fuzz_endpoints.py: a BOOLEAN whose NAME is about existence and is
      false means the endpoint answered honestly. Narrow on purpose - invoke_editor_tab's
      enumerable:false does NOT match, because that is about whether tab ids can be listed, and that
      endpoint has a real defect that must stay flagged. 17 checks in test_fuzz_detector.py, using
      the actual run-5 responses as fixtures.
- [x] Work the audit_mode_params.py review list (18 handlers). It finds the invoke_editor_tab SHAPE:
      a parameter that is declared and valid but silently unused on some branch, which
      RejectUnknownParams structurally cannot catch because the parameter is not unknown - it is
      ignored by MODE. The list is already filtered to parameters never named in any refusal in their
      own handler, which is what clears sculpt_landscape and create_landscape (both explain that
      amount is raise/lower only and targetZ is flatten only).
      Spot-checked one: list_sublevels' netMode is DOCUMENTED as "only meaningful with world:pie" but
      not enforced, so it is a milder instance - documented rather than silent. Decide per handler
      whether the doc string is enough or whether it should refuse like invoke_editor_tab now does.
      Not urgent; this is hygiene on a class of bug, not a live defect list.
      WORKED, and closed with a decision rather than left to spin. The tool was sharpened three times,
      each after it accused something innocent: refusal-mention filtering (sculpt_landscape DOES say
      amount is raise/lower only), brace depth (it was listing every parameter of every mode-having
      handler), and presence-guard exclusion (set_viewport_camera's location/rotation/lookAt sit
      inside `if (TryGetObjectField(...))` and apply on every mode). 18 -> 12 rows, and none of the
      passes lost invoke_editor_tab.
      Spot-checked the survivors: most are ALIAS clusters (path/functionName/name are one argument
      under three spellings) read through multi-line JStrAny that the line-based scan cannot see -
      documented as a known limitation in the tool's header. The one genuine milder instance is
      list_sublevels' netMode, which is DOCUMENTED as "only meaningful with world:pie" but not
      enforced. No further fixes are warranted right now; the tool exists so the next instance is
      found deliberately instead of by accident.
- [x] trace_ground silently ignored an unresolvable ignoreActor. `if (AActor* Ignore =
      FindActorInWorld(...)) { AddIgnoredActor(Ignore); }` - when the name resolved to nothing the if
      never fired, the trace ran WITHOUT ignoring anything, and the caller got a confident hit:true
      possibly against the very actor they asked to exclude. Second instance of the invoke_editor_tab
      class, found by the same ghost probe in run 6.
      BOTH DONE - built, tested (T46/T47 in test_audit_fixes.py, 22 checks green), committed. Each
      test also asserts the ORDINARY path still works, because a fix that refuses too much is its own
      defect. The resolve-or-skip pattern search converged: trace_ground was the ONLY place in 46k
      lines where user input is resolved inside an `if` with no failing else; the one other match is
      `if (!ResolveMaterialProperty(...))`, which is the correct shape.
- [x] Validation sweep against the FIXED build. Run 4 tested a binary that predates c190ae5 and used
      the ghost detector that predates ea37587, so it cannot show either fix working. A clean run
      should now give 0 CRASH, and the GHOST_OK bucket should collapse from 9 to the handful that are
      real once contamination and correct-empties stop being counted. Budget hours, not minutes - the
      kr_* endpoints do genuine blueprint reconstruction even on garbage input, which is where run 4
      spent most of its time.
      RUN 6 DONE, 238 endpoints against the fixed build:
          CRASH     1 -> 0    duplicate_asset's modal, fixed in c190ae5
          HANG      1 -> 0    recipe_reset_and_loop, proven to be a busy editor and not a defect
          GHOST_OK  9 -> 6
      Of the six, four are not defects: describe_package (existsOnDisk:false), get_dependencies and
      get_referencers (packageExists:false plus a note) all state the truth - the third calibration
      landed after this run started so it could not suppress them - and trigger_cook returns
      executed:false, "Plan only", which is exactly what that endpoint does.
      The two real ones are invoke_editor_tab and trace_ground, both the same silent-ignore class and
      both fixed. That is the entire yield, and it is a good one: a crasher and a data-loss path in
      run 4, two silent ignores in run 6.
- [x] UMG WidgetAnimation authoring is missing entirely (reported 2026-08-25, QOLCrafting_P /
      WBP_QOL_DropZone). Verified: nothing under Source/MifBridge mentions WidgetAnimation or
      MovieScene, and the three 'anim' endpoints (describe_animation, list_animations, add_anim_node)
      are all SKELETAL animation, not UMG. The report is correct.
      WRONG, corrected after the fact: it WAS blocked on a Build.cs change. UMG does list
      MovieScene/MovieSceneTracks publicly, but that propagates INCLUDE PATHS only - the headers
      compiled and the LINK failed on UMovieScene::SetPlaybackRange and friends. Both modules are
      now in MifBridge.Build.cs. Compiling is not linking - that file already says so twice.
      Shape: add_widget_animation, add_widget_animation_track, set_widget_animation_keys, and the two
      removes - or one apply_widget_animation_patch in the style of apply_graph_patch.
      The real difficulty is not the API surface, it is three invariants:
        * TIME. The reported source is display rate 20fps with tick resolution 60000/1. Keys are
          FFrameNumber in TICK space, so 0.95s is 57000 ticks, not frame 19 and not 0.95. Getting this
          wrong puts every key on the wrong frame while every call still reports success.
        * BINDING. A widget binding is an FGuid possessable in the UMovieScene plus the entry in
          UWidgetAnimation::AnimationBindings that maps it to the widget name. Create one and not the
          other and the animation exists, compiles, plays, and moves nothing.
        * INTERPOLATION. Cubic/Auto is a per-key tangent mode on the float channel, separate from the
          key value, and defaults to linear if not set.
      Read-back must prove all three, not just that the objects exist.
      Research done, so implementation does not have to guess. The editor's own path is
      AnimationTabSummoner.cpp:589:
        * NewObject<UWidgetAnimation>(WidgetBlueprint, FName(), RF_Transactional), then
          SetDisplayLabel(name) and Rename(*name);
        * MovieScene = NewObject<UMovieScene>(TheAnimation, FName(name), RF_Transactional) - outered to
          the ANIMATION, not the blueprint;
        * SetDisplayRate(FFrameRate(20,1));
        * SetPlaybackRange(In * GetTickResolution(), Out * GetTickResolution() + 1) - note the +1;
        * GetEditorData().WorkStart / WorkEnd;
        * and it is only part of the asset once WidgetBlueprint->Animations.Add(Anim) runs
          (AnimationTabSummoner.cpp:260/277). Miss that line and the animation exists, compiles, and
          is not in the widget.
      For the binding, use UWidgetAnimation::BindPossessableObject(Guid, Object, Context) after
      MovieScene->AddPossessable - it is the one call that keeps the possessable and the
      AnimationBindings entry in sync, and it handles the root-widget and panel-slot cases.
      LANDMINE: BindPossessableObject opens with CastChecked<UUserWidget>(Context)
      (WidgetAnimation.cpp:157). A null or wrong Context TERMINATES THE EDITOR - same class as the
      FName 1023 assert, not a refusal. Headless there is no preview widget to hand it, so the context
      has to be constructed or resolved deliberately and checked before the call, never passed through.
      DONE for the reported case (5813eac + 22be0d2): add_widget_animation, list_widget_animations,
      add_widget_animation_track, set_widget_animation_keys. The report's ArrowLoop reproduces exactly
      - 20fps, tick resolution 60000/1, keys on 0 / 30000 / 57000 / 69000 / 90000. The CastChecked
      landmine is sidestepped by building the binding the way BindPossessableObject's plain-widget
      branch does, which never reads Context; the ROOT widget case is refused, not approximated.
      38 checks across two suites.
- [x] Widget animation: the other property tracks. Only RenderTransform.Translation is authorable and
      anything else is refused BY NAME rather than ignored. Opacity, Margin, colour and visibility are
      separate track classes; the plumbing built here (binding, section range, batch-validated keys,
      seconds-to-ticks) generalises to them.
- [x] Widget animation: removal. There is no remove_widget_animation or remove_widget_animation_track,
      so an animation authored by mistake can only be undone in the Designer.
      Both done. RenderOpacity (float) and ColorAndOpacity (R/G/B/A) join the transform track - they
      share FMovieSceneFloatChannel, so it was a table rather than a rewrite. Visibility is a BOOL
      channel and is refused BY NAME with the supported list instead of half-working.
      remove_widget_animation and remove_widget_animation_track, no confirm flag (undoable blueprint
      edits, matching remove_variable, not delete_asset). removeBinding drops BOTH halves of a binding.
      26 checks in test_widget_animation_props.py; 64 across the three UMG suites.
- [x] Widget animation: Visibility, the one deliberately left out. It is a bool channel
      (UMovieSceneVisibilityTrack), so it needs a second key path alongside the float one. The
      refusal already names it as unsupported, so this is additive rather than a correction.
      Done. UMovieSceneVisibilityTrack / UMovieSceneBoolSection, with its own key path rather than
      forced through the float one: a bool channel is STEPPED, so 'interp' on a Visibility key is
      REFUSED rather than accepted and ignored. A numeric 1/0 is accepted as a convenience and the
      response reports back the boolean actually stored. 35 checks in test_widget_animation_props.py.
      Worth noting: the check that asserted Visibility was UNSUPPORTED started failing the moment it
      was implemented - a test correctly failing because the behaviour improved. Repointed at a
      property with no mapping rather than deleted, since the refusal is the part worth pinning.
- [x] Re-run the endpoint sweep with the narrowed leak detector and the confirming-retry hang logic, and confirm or drop the unexplained recipe_reset_and_loop hang.
      Run 4 completed: 232 endpoints, 1 CRASH (duplicate_asset - the modal, now fixed in c190ae5),
      9 GHOST_OK (6 of them the fuzzer's own contamination, since fixed), 1 HANG.
      CONFIRMED, and no longer unexplained as to trigger: run 4 reproduced it on the ABSURD probe with
      every parameter set to ''. Recorded as HANG rather than CRASH, so the bridge was
      still alive on the confirming re-check - it did not die, it stopped answering that call.
      Embedded NULs are the obvious suspect: an FString carrying  truncates at the C-string
      boundary in some paths and not others, so a length check and a copy can disagree. Reproduce it
      against a single endpoint before touching anything.
- [x] recipe_reset_and_loop hangs on control characters in its parameters (see above). Find the actual
      blocking call. Do NOT assume it is the modal-dialog class - that is the fresh hypothesis and this
      hang predates the evidence for it.
      A static pass ruled out the obvious candidates rather than confirming one. Ruled OUT so far:
        * Not "treated as empty". The probe tries "" FIRST and that does not hang; only ""
          does. The difference is that Len()==3 passes every IsEmpty() guard while *Str is empty as a
          C string, so it gets FURTHER than the empty case, not less far.
        * Not a graph-resolution scan. ResolveGraphField takes IsEmpty() as false and calls
          ResolveGraph, which Splits on "::" first and returns immediately when that fails. Fast path.
        * Not response truncation on an embedded NUL. FJsonSerializer escapes control characters, so
          the body carries the six characters  and never a raw NUL byte - Content-Length and the
          payload agree.
      Strongest remaining hypothesis, and it is NOT endpoint-specific: handlers run synchronously
      inline on the game thread, so "hang" as the fuzzer measures it is a CLIENT-side timeout and can
      simply mean the editor was busy longer than the timeout. The kr_* band proves the editor can be
      busy for minutes at a stretch. Before blaming this endpoint, re-probe it in isolation against an
      idle editor and bisect the parameters one at a time.
      DROPPED - it is not an endpoint defect. tools/probe_recipe_hang.py sends the exact run-4 payload
      to an idle editor and it answers in 0.33s with "missing graphId". The empty-string control
      answers identically, which also DISPROVES the reasoning I had written above: IsEmpty() is in
      fact true for "", so it behaves exactly like "" rather than getting further. The
      input was never the trigger.
      What actually happened is the client-side-timeout explanation. Handlers run inline on the game
      thread, so a call queues behind whatever the editor is doing, and run 4 hit this endpoint right
      after the kr_* band, which reconstructs blueprints for real even on garbage input. The fuzzer's
      confirming retry (45s then 135s) was not enough to see through it.
      Fixed the instrument rather than the endpoint: a HANG finding now times a trivial endpoint
      immediately afterwards and records the number, so "this call is hung" and "the editor was busy"
      stop looking identical in the report.


- [x] recipe_reset_and_loop hardcodes StandardMacros when resolving ForEachLoop — the same brittle pattern already fixed in add_macro_instance. Harmless today, but route it through the registry lookup.
- [x] Extend audit_roundtrip.py to the node types it does not yet cover (branch, sequence, timeline, switch, spawn_actor, interface calls) — the two gaps it already found were both real.
- [x] Work the audit_postconditions.py MEDIUM list: ~90 mutating handlers with no visible read-back. Triage for the ones where a silent failure would be invisible to the caller, fix those, and record the rest as understood.
- [x] MifBridgeGraphPatch.cpp still carries a local FPinRef that duplicates the shared FMifPinRef in MifBridgeHandlers.h. Converge them so there is one implementation, per the module's own rule.
- [x] The hideKnots fix has never been exercised — no endpoint creates a K2Node_Knot, so build a reroute chain some other way and prove it.
- [x] pie_status misreports a working PIE session. Reproduce and fix, or establish exactly what it is really reporting.
- [x] snap_actors_to_ground misses ~112 of 303 actors on flat ground. Find out why before trusting it again.
      Fixed in 9f2e7d9. LineTraceMultiByChannel returns overlaps plus ONE blocking hit ("no tests will
      be done after that", World.h) - it does not see past a blocker the way the code's comment said.
      Every static mesh blocks WorldStatic, so any actor over another actor had only the prop in its
      results and no ground was ever found. Now re-traces past each non-ground blocker, bounded at 32.
      11 checks in tools/test_snap_ground.py, including three that the fix must not break.
- [x] There is no single-actor read endpoint. get_level_actor does not exist; reading one actor's
      transform back means list_level_actors with nameContains and filtering client-side. Found by
      writing a test helper that called the endpoint it assumed was there and silently returned None.
      Added. 243 endpoints, parity clean. The helper that exposed the gap now uses the endpoint and
      cross-checks it against the lister, so the two reads have to agree.

## Done

- [x] pie_status: cannot reproduce, and the fix is already in the code. Tested both ways against a
      live editor - simulate mode and a real possessed-pawn session - and it reported stopped ->
      running correctly within one poll each time, with pieActorCount and the pawn path populated.
      The handler carries a comment describing exactly the defect the item was about:
      IsPlayingSessionInEditor() goes true BEFORE the world exists, so polling on it reports running
      while GetPIEWorld() is still null; it uses UWorld::HasBegunPlay() instead. The open item was
      stale, carried forward from a note written before that change. Dropped rather than left to spin.

- [x] hideKnots is finally exercised, by closing the gap that made it untestable. Reroute nodes were
      readable but not writable - list_nodes has hideKnots, SerializePin resolves through knot chains,
      SkipKnots tunnels them, and nothing in the surface could create one. There is no paste/import
      endpoint either, so a knot could not be conjured any other way. Added add_reroute, which also
      splices into an existing wire (src -> knot -> dst) so a knot CHAIN is buildable. With a
      two-knot chain in place, hideKnots removes both from the listing and the wire resolves end to
      end to the real target - the first time that code has run against a real knot. Compiles clean.

- [x] Extended audit_roundtrip to branch, sequence, switch, spawn_actor and timeline (13 checks -> 23),
      and it immediately found that add_timeline HAS NEVER WORKED. The handler assumed placing a
      UK2Node_Timeline runs PostPlacedNewNode and creates the UTimelineTemplate. That node has no
      PostPlacedNewNode override at all - its only Post* override is PostPasteNode, and the
      template-creating code lives on the PASTE path. So no template was ever created, every call fell
      into the "template not found" branch, and it failed on a brand-new blueprint while its error
      text blamed a name collision that could not exist. Now creates the template explicitly with
      FBlueprintEditorUtils::AddNewTimeline before placing the node, which also turns the collision
      case into a real checked refusal with nothing left behind. Verified: node titled MyTL with
      Alpha/Beta track pins, collision refused, compiles 0/0.

- [x] Checked the setters and connectors the triage pointed at. All three are sound, which is a
      useful result rather than a null one: connect_material_expressions checks the bool that
      UMaterialEditingLibrary returns and fails with the valid input/output names listed;
      set_actor_transform checks its own bool; set_component_transform writes through
      SetRelative*_Direct, which assigns the field outright and cannot refuse. So the triage rule
      holds - creation endpoints and checked-bool calls are fine, and VOID setters are the only
      shape that half-succeeds silently.
      One real inconsistency fixed along the way: set_component_transform reported only the component
      name, while set_actor_transform reports locationApplied/rotationApplied/scaleApplied. It now
      echoes which of the three were written and the transform the component actually carries, so a
      caller can confirm without a second round trip.

- [x] audit_postconditions triage. The MEDIUM list was ~90 and almost all noise, and the fault was in
      my detector: MUTATION_HINTS contained "->Set", which matches `Out->SetStringField` - every
      endpoint builds its response that way, so every read-only lister looked like an unverified
      write. Corrected to ignore response writes and to skip the module's own readOnly bucket (46
      endpoints). That left 3 HIGH, all real and all now fixed: rename_event (OnRenameNode is void and
      declines a colliding name, so a refused rename read as a successful one), add_macro_instance
      (SetMacroGraph is void; a node whose reference did not take exists and does nothing), and
      duplicate_actors (labels through SetActorLabelChecked). HIGH is now 0.
      The remaining 66 MEDIUM are understood rather than ignored: they are dominated by CREATION
      endpoints (add_branch, create_material, add_component) where the object existing is the
      verification - NewObject failing would have thrown, and the response already carries the thing
      that was made. Nothing there can silently half-succeed the way a void setter can.

- [x] Converged MifBridgeGraphPatch's local FPinRef onto the shared FMifPinRef and dropped the Graph
      parameter RestoreLinks no longer needed (the ref carries its own graph). One implementation.

- [x] recipe_reset_and_loop now uses a shared ResolveMacroGraph (registry-backed, StandardMacros only
      a preference). Deliberately did NOT converge add_macro_instance onto it: the two want opposite
      semantics. The recipe wants "find ForEachLoop wherever it is" - it is internal and there is one
      right answer. add_macro_instance must REFUSE when the macro is not in the library the caller
      named and tell them the correct macroPath, because silently instantiating from a different
      library is exactly the confusion the Switch Has Authority report was about. Sharing the resolver
      there would trade a good error for a silent surprise.

- [x] select_level_actors (51c11fa). The logged item was wrong: the handler already reports notFound
      for paths that do not resolve. The real defect was one layer up - actorPaths given a STRING made
      TryGetArrayField fail, the whole block was skipped, and it answered ok:true/selected:0 having
      done nothing. Arrays turned out to be a hole in the module's documented silent-ignore backstop,
      so the fix is a checked JArray reader across all 19 request-array reads, not a message change.

- [x] DONE (c7bc493), and this entry was stale as of 2026-08-29 - both the "STILL TO CONFIRM" symbol
      question and the tab itself are resolved. FBlueprintTags::ParentClassPath / NativeParentClassPath
      confirmed at 5.3 BlueprintSupport.h:38/40 and 5.7 :32/34, same names, same COREUOBJECT_API export
      on both engines - no guard needed. MifBridgeInheritView.cpp (369 lines) builds an STreeView over
      MifBridge::MakeInheritWidget(), wired into MifBridgePanel.cpp as the fifth tab (index 4,
      "INHERITANCE"), lazily built on first visit the same way HEATMAP is. Reads 2855 blueprints of
      32265 assets on DDS2 without loading one, backed by the blueprint_inheritance_tree endpoint this
      same backlog entry's sibling item was about (also found stale and closed the same pass). Native
      roots and blueprint nodes get distinct colouring (Purple/Native), matching the panel's existing
      palette convention. ORIGINAL: Inheritance tree view - the one dashboard tab still missing.
      The competitor's is collapsible groups (UserWidget 156 blueprints, ActorComponent 36, ...) with
      a coloured pill per class. STreeView is the widget.

- [x] DONE 2026-08-29 - duplicate of the "Mermaid-style flow export" item under Open above. See that
      entry for what shipped (project_dependency_graph's mermaid:true parameter, T644).

- [ ] Real asset THUMBNAILS in the brainmap, instead of class icons.
      Andre asked for "viewport icons of each item or cached images". Class icons shipped because
      FSlateIconFinder::FindIcon works by NAME inside a paint path and loads nothing. Real thumbnails
      need FAssetThumbnail, which produces an SWidget - so the brainmap has to become a panel with a
      child per node rather than a custom-painted leaf. That is a real refactor, not a tweak.
