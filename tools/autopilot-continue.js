#!/usr/bin/env node
/**
 * Stop hook: keep working while the FEATURE PARITY SPEC still has open items.
 *
 * Was backlog-driven; Andre asked for it to be spec-driven instead. The difference is what counts as
 * "done": a backlog empties when its items are ticked, a spec is met when the surface matches the
 * target. The target here is MifBridge's coverage measured against Ultimate Engine CoPilot's
 * advertised categories.
 *
 * A hook that ALWAYS blocks is an unbreakable loop, so this has four independent ways to stop:
 *
 *   1. THE SPEC IS MET.       Blocks only while SPEC contains unchecked "- [ ]" items.
 *   2. DELIBERATE DECLINE.    "- [~]" means decided-against, with the reason on the following line.
 *                             The hook ignores those. This is the important one: several competitor
 *                             categories are structurally irrelevant to modding a COOKED game (you
 *                             cannot add C++ modules to a pak mod), so without a way to say "no, and
 *                             here is why", those items could never be ticked and the loop would
 *                             never end. Declining something with a reason is a finished decision.
 *   3. THE KILL SWITCH.       Create OFF_SWITCH and it stops immediately, whatever the spec says.
 *   4. THE ITERATION CAP.     After MAX_CONTINUES it gives up, so an item that can never be finished
 *                             cannot loop forever. Resets when the spec is met or the run stops.
 *
 * NIGHT SHIFT. NIGHT_UNTIL holds an epoch-ms deadline. While it exists and has not passed, the hook
 * blocks even with an EMPTY spec - otherwise a run asked to cover eight hours would end the moment
 * the spec hit zero at 2am. When the deadline passes the hook DELETES the file itself and reverts to
 * normal behaviour; a deadline needing manual cleanup is one that quietly runs for a week. The kill
 * switch is still checked first, so AUTOPILOT_OFF stops a night shift instantly.
 *
 * Exit 0 with {"decision":"block","reason":...} keeps the turn alive and feeds reason back as the
 * next instruction.
 */
const fs = require("fs");
const os = require("os");
const path = require("path");

const HOME = os.homedir();
// This hook lives in USER settings, so it runs in EVERY session on this machine - including ones
// working on entirely different projects. Blocking those from stopping, and handing them a spec that
// is none of their business, is exactly what happened the first time this was written. Scope it by
// working directory: anywhere else, this hook is inert.
const PROJECT_ROOT = "d:/dds2sdk/game/plugins/mifbridge";
const SPEC = "D:/DDS2SDK/Game/Plugins/MifBridge/tools/FEATURE_PARITY_SPEC.md";
const OFF_SWITCH = path.join(HOME, ".claude", "AUTOPILOT_OFF");
const COUNTER = path.join(HOME, ".claude", ".autopilot-count");
const NIGHT_UNTIL = path.join(HOME, ".claude", "AUTOPILOT_UNTIL");
const MAX_CONTINUES = 60;
// A night shift is bounded by the CLOCK, so the iteration cap is only a backstop against a loop
// that cannot make progress. Still finite.
const MAX_CONTINUES_NIGHT = 500;

function allowStop(systemMessage) {
  try { fs.unlinkSync(COUNTER); } catch (_) {}
  if (systemMessage) process.stdout.write(JSON.stringify({ systemMessage }));
  process.exit(0);
}

function readCount() {
  try { return parseInt(fs.readFileSync(COUNTER, "utf8").trim(), 10) || 0; } catch (_) { return 0; }
}

// --- 0. right project? ----------------------------------------------------
// Silent no-op elsewhere: a session in another repo should neither be blocked nor told about it.
// split/join rather than a regex: a backslash class here is one shell-escaping mistake away from a
// syntax error, and a Stop hook that throws is worse than one that does nothing.
const cwd = process.cwd().split(path.sep).join("/").toLowerCase();
if (cwd.indexOf(PROJECT_ROOT) === -1) {
  process.exit(0);
}

// --- 1. kill switch -------------------------------------------------------
if (fs.existsSync(OFF_SWITCH)) {
  allowStop("Parity autopilot: OFF switch present (" + OFF_SWITCH + ") - stopping.");
}

// --- 1b. night shift ------------------------------------------------------
// Deliberately AFTER the kill switch: AUTOPILOT_OFF must win over a deadline, or an overnight run
// could not be stopped without deleting two files.
let nightMsLeft = 0;
if (fs.existsSync(NIGHT_UNTIL)) {
  let deadline = 0;
  try { deadline = parseInt(fs.readFileSync(NIGHT_UNTIL, "utf8").trim(), 10) || 0; } catch (_) {}
  if (deadline > Date.now()) {
    nightMsLeft = deadline - Date.now();
  } else {
    // Self-clearing. Cleaned up here rather than left for someone to notice.
    try { fs.unlinkSync(NIGHT_UNTIL); } catch (_) {}
    allowStop("Parity autopilot: the night shift deadline has passed - stopping, and the deadline "
      + "file has been removed.");
  }
}

// --- 2. the spec ----------------------------------------------------------
let text = "";
try {
  text = fs.readFileSync(SPEC, "utf8");
} catch (_) {
  allowStop(null);          // no spec file = nothing claimed to be pending
}

const lines = text.split(/\r?\n/);

// Only "- [ ]" is open work. "- [x]" is met and "- [~]" is a recorded decision not to pursue.
const open = lines
  .filter((l) => /^\s*-\s*\[ \]\s*\S/.test(l))
  .map((l) => l.replace(/^\s*-\s*\[ \]\s*/, "").replace(/\*\*/g, "").trim());

const met = lines.filter((l) => /^\s*-\s*\[x\]\s*\S/i.test(l)).length;
const declined = lines.filter((l) => /^\s*-\s*\[~\]\s*\S/.test(l)).length;

if (open.length === 0 && nightMsLeft === 0) {
  allowStop(
    "Parity autopilot: spec is met - " + met + " covered, " + declined +
    " deliberately declined, 0 open. Stopping."
  );
}

// --- 3. iteration cap -----------------------------------------------------
const cap = nightMsLeft > 0 ? MAX_CONTINUES_NIGHT : MAX_CONTINUES;
const count = readCount() + 1;
if (count > cap) {
  allowStop(
    "Parity autopilot: hit the " + cap + "-continue cap with " + open.length +
    " item(s) still open. Stopping so this cannot loop forever - reset by deleting " + COUNTER + "."
  );
}
try { fs.writeFileSync(COUNTER, String(count)); } catch (_) {}

const next = open.slice(0, 5).map((s, i) => "  " + (i + 1) + ". " + s.slice(0, 150)).join("\n");
const hoursLeft = (nightMsLeft / 3600000).toFixed(1);
const nightBanner = nightMsLeft > 0
  ? ("NIGHT SHIFT: " + hoursLeft + "h left before the deadline. Andre is asleep and asked for a full "
     + "night of autonomous work, so do NOT stop even if the spec empties.\n"
     + "  * Prefer work with a clear finish condition over open-ended searching: run a suite, fix "
     + "what it finds, commit, move on.\n"
     + "  * If the spec empties, the standing night work is regression and hunting, not new breadth:\n"
     + "      run every tools/test_*.py against the live editor and fix what broke;\n"
     + "      sweep all endpoints for crashes and hangs (the last full sweep covered 238 of them);\n"
     + "      hunt for endpoints that report success while doing something else, which has been the\n"
     + "      most productive lens all session.\n"
     + "  * File anything found in docs/06_OPEN_ISSUES_FROM_USE.md, and add real work back to the\n"
     + "    spec as new '- [ ]' lines so the morning has a record.\n"
     + "  * Commit and push as you go. A night of work in one unpushed lump is a night at risk.\n"
     + "  * Do NOT save assets, start PIE, or touch anything outside the SDK editor.\n\n")
  : "";
const reason =
  nightBanner +
  "Feature-parity autopilot is on. The spec has " + open.length + " open item(s) (" + met +
  " covered, " + declined + " declined) - continue " + count + "/" + cap +
  (open.length ? ". Do NOT stop - pick up the next one:\n" : ".\n") +
  next +
  (open.length > 5 ? "\n  ... and " + (open.length - 5) + " more" : "") +
  "\n\nThe spec is " + SPEC + ". Rules:\n" +
  "  * Mark '- [x]' only when endpoints exist, are BUILT, TESTED and COMMITTED - not when written.\n" +
  "  * Mark '- [~]' when you decide NOT to pursue something, and put the reason on the next line.\n" +
  "    That is a finished decision, not a dodge - several competitor categories are irrelevant to\n" +
  "    modding a cooked game and should be declined explicitly rather than left to spin.\n" +
  "  * Judge value for DDS2 COOKED-GAME MODDING, not for general UE development and not for how the\n" +
  "    feature list reads. Breadth for its own sake is not the goal; the competitor has a funded team\n" +
  "    and will win a tool-count race.\n" +
  "  * Verify coverage by READING handlers, never by endpoint name. The authoritative endpoint list\n" +
  "    is tools/endpoints_current.json, regenerated from the live editor's self_audit.\n" +
  "  * Add newly discovered work as new '- [ ]' lines so nothing is lost.";

process.stdout.write(JSON.stringify({ decision: "block", reason }));
process.exit(0);
