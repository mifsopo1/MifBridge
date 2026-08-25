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

- [ ] select_level_actors: name the actorPaths that did not resolve. `selected:0` currently reads the same as "these exist and none matched", so a caller doing select-then-operate-on-selection gets an empty selection and no reason.
- [ ] Re-run the endpoint sweep with the narrowed leak detector and the confirming-retry hang logic, and confirm or drop the unexplained recipe_reset_and_loop hang.
- [ ] recipe_reset_and_loop hardcodes StandardMacros when resolving ForEachLoop — the same brittle pattern already fixed in add_macro_instance. Harmless today, but route it through the registry lookup.
- [ ] Extend audit_roundtrip.py to the node types it does not yet cover (branch, sequence, timeline, switch, spawn_actor, interface calls) — the two gaps it already found were both real.
- [ ] Work the audit_postconditions.py MEDIUM list: ~90 mutating handlers with no visible read-back. Triage for the ones where a silent failure would be invisible to the caller, fix those, and record the rest as understood.
- [ ] MifBridgeGraphPatch.cpp still carries a local FPinRef that duplicates the shared FMifPinRef in MifBridgeHandlers.h. Converge them so there is one implementation, per the module's own rule.
- [ ] The hideKnots fix has never been exercised — no endpoint creates a K2Node_Knot, so build a reroute chain some other way and prove it.
- [ ] pie_status misreports a working PIE session. Reproduce and fix, or establish exactly what it is really reporting.
- [ ] snap_actors_to_ground misses ~112 of 303 actors on flat ground. Find out why before trusting it again.

## Done
