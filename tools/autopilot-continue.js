// TRACKED COPY. The live hook runs from ~/.claude/hooks/autopilot-continue.js - this copy exists so
// the hook is versioned next to the spec it reads. If you change one, change both.
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
const MAX_CONTINUES = 60;

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

if (open.length === 0) {
  allowStop(
    "Parity autopilot: spec is met - " + met + " covered, " + declined +
    " deliberately declined, 0 open. Stopping."
  );
}

// --- 3. iteration cap -----------------------------------------------------
const count = readCount() + 1;
if (count > MAX_CONTINUES) {
  allowStop(
    "Parity autopilot: hit the " + MAX_CONTINUES + "-continue cap with " + open.length +
    " item(s) still open. Stopping so this cannot loop forever - reset by deleting " + COUNTER + "."
  );
}
try { fs.writeFileSync(COUNTER, String(count)); } catch (_) {}

const next = open.slice(0, 5).map((s, i) => "  " + (i + 1) + ". " + s.slice(0, 150)).join("\n");
const reason =
  "Feature-parity autopilot is on. The spec has " + open.length + " open item(s) (" + met +
  " covered, " + declined + " declined) - continue " + count + "/" + MAX_CONTINUES +
  ". Do NOT stop - pick up the next one:\n" +
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
