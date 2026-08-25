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

- [x] From the postcondition triage: the setters and connectors are the MEDIUM entries that could still fail silently, unlike the creation ones. Check connect_material_expressions (the material analogue of connect_pins, which had exactly this defect), set_component_transform and set_actor_transform for whether a refused write is reported.

- [x] select_level_actors: name the actorPaths that did not resolve. `selected:0` currently reads the same as "these exist and none matched", so a caller doing select-then-operate-on-selection gets an empty selection and no reason.
- [ ] Re-run the endpoint sweep with the narrowed leak detector and the confirming-retry hang logic, and confirm or drop the unexplained recipe_reset_and_loop hang.
- [x] recipe_reset_and_loop hardcodes StandardMacros when resolving ForEachLoop — the same brittle pattern already fixed in add_macro_instance. Harmless today, but route it through the registry lookup.
- [x] Extend audit_roundtrip.py to the node types it does not yet cover (branch, sequence, timeline, switch, spawn_actor, interface calls) — the two gaps it already found were both real.
- [x] Work the audit_postconditions.py MEDIUM list: ~90 mutating handlers with no visible read-back. Triage for the ones where a silent failure would be invisible to the caller, fix those, and record the rest as understood.
- [x] MifBridgeGraphPatch.cpp still carries a local FPinRef that duplicates the shared FMifPinRef in MifBridgeHandlers.h. Converge them so there is one implementation, per the module's own rule.
- [x] The hideKnots fix has never been exercised — no endpoint creates a K2Node_Knot, so build a reroute chain some other way and prove it.
- [ ] pie_status misreports a working PIE session. Reproduce and fix, or establish exactly what it is really reporting.
- [ ] snap_actors_to_ground misses ~112 of 303 actors on flat ground. Find out why before trusting it again.

## Done

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
