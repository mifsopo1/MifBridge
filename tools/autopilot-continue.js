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
 *                             The hook ignores those, so an item that will never be built cannot
 *                             keep the loop alive forever. Declining with a reason is finished.
 *
 *                             THE REASON MUST NOT BE "irrelevant to cooked modding". This file
 *                             used to give exactly that as its example, and it was wrong in a way
 *                             that quietly shrinks the product: MifBridge is a GENERAL UE5 TOOL
 *                             that happens to be BUILT on a cooked editor. Something useless for
 *                             pak-mod work can be essential for ordinary 5.7 development, and an
 *                             example inviting that justification teaches every future session to
 *                             decline it.
 *
 *                             A valid decline says the thing is impossible, already covered, or
 *                             worthless to EVERY UE5 user - not that one test project has no use
 *                             for it.
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
 * THE ITERATION CAP DURING A NIGHT SHIFT DOES NOT STOP THE SHIFT (2026-08-28). It used to: hitting
 * MAX_CONTINUES_NIGHT called allowStop(), which really ends the turn - no more blocking - and the run
 * only continued again once something else re-invoked the session (Andre noticing and re-prompting,
 * or the hourly mifbridge-autonomous-resume task). That is a real gap, not a graceful pause: Andre
 * asked mid-week why a run had stopped, and the answer was this cap, silently ending a shift that was
 * supposed to run unattended until its OWN deadline. The file's own comment already said the design
 * intent - "a night shift is bounded by the CLOCK, so the iteration cap is only a backstop" - the code
 * just did not honour it. Now: while a night shift is active, hitting the cap resets the counter and
 * keeps blocking instead of calling allowStop(). The clock (NIGHT_UNTIL) and the kill switch
 * (AUTOPILOT_OFF) remain the only two ways a night shift actually ends; the cap still applies, and
 * still stops the loop for real, on an ordinary run with no deadline.
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
const openRaw = lines
  .filter((l) => /^\s*-\s*\[ \]\s*\S/.test(l))
  .map((l) => l.replace(/^\s*-\s*\[ \]\s*/, "").replace(/\*\*/g, "").trim());

// PRIORITY, because document order is not what Andre wants next.
//
// The spec is roughly chronological, so MifBlender sat at the top of every "next up" list purely by
// having been written down early - while its own text says it comes AFTER the UE side is comfortable.
// The four in-editor features he actually asked for kept being reported below it, and one of them
// (the mesh splitter) fell out of a status summary entirely.
//
// Andre, 2026-08-27: "do NOT forget about our UI additions befor emoving to blender, add that to the
// stophook, the ui additions". A preference that lives only in a conversation expires with the
// session; this is why it lives here.
//
// Earlier pattern wins. Unmatched items keep document order between the matched ones and MifBlender.
const PRIORITY = [
  /inheritance tree/i,
  /behavior tree.*(view|diagram)|diagram.*behavior tree/i,
  /mesh splitter|skeletal.*split/i,
  /dropdown|write-mode/i,
  /panel|in-editor|brainmap|heatmap/i,
];
const rank = (item) => {
  for (let i = 0; i < PRIORITY.length; i++) {
    if (PRIORITY[i].test(item)) return i;
  }
  // MifBlender LAST by an explicit rule rather than by accident.
  if (/mifblender|blender/i.test(item)) return PRIORITY.length + 1;
  return PRIORITY.length;
};
const open = openRaw
  .map((item, i) => ({ item, i, r: rank(item) }))
  .sort((a, b) => (a.r - b.r) || (a.i - b.i))
  .map((x) => x.item);

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
let count = readCount() + 1;
if (count > cap) {
  if (nightMsLeft > 0) {
    // A night/week shift is bounded by NIGHT_UNTIL, not by this counter - see the comment above. Reset
    // and keep going rather than really stopping; the clock and the kill switch are what end this.
    count = 1;
  } else {
    allowStop(
      "Parity autopilot: hit the " + cap + "-continue cap with " + open.length +
      " item(s) still open. Stopping so this cannot loop forever - reset by deleting " + COUNTER + "."
    );
  }
}
try { fs.writeFileSync(COUNTER, String(count)); } catch (_) {}

// TOKEN BUDGET. This reason block is re-sent on EVERY turn, and the rules half of it was
// byte-identical each time - roughly 500 tokens per turn, tens of thousands over a long run, buying
// nothing after the first read. Andre: "whatever can reduce token usage in our work please do so".
//
// So the standing rules are emitted RARELY and the changing part always. The rules live in the spec
// file and in project memory; restating them every turn is the definition of waste.
const FULL_EVERY = 20;
const wantFull = (count % FULL_EVERY) === 1;
const shown = wantFull ? 5 : 2;

const next = open.slice(0, shown)
  .map((s, i) => "  " + (i + 1) + ". " + s.slice(0, wantFull ? 150 : 90)).join("\n");

const RULES =
  "\n\nSpec: " + SPEC + "\n" +
  "  * '- [x]' only when BUILT, TESTED and COMMITTED. '- [~]' to decline, reason on the next line.\n" +
  "  * MifBridge is a GENERAL UE5 TOOL. Judge value for ALL of UE5 - 5.3 through 5.7,\n" +
  "    COOKED AND UNCOOKED. DDS2 (cooked 5.3.2) and Curfew (uncooked 5.7) are the two it\n" +
  "    is TESTED on, not the limit of who it is for.\n" +
  "  * 'irrelevant to cooked modding' is NOT a valid reason to decline an item.\n" +
  "  * Verify coverage by READING handlers, never by endpoint name. self_audit is the live list.\n" +
  "  * Add new work as '- [ ]' lines so nothing is lost.\n" +
  "  * Do NOT save assets, start PIE, or touch anything outside the SDK editor.\n" +
  "  * Commit and push as you go. Touch tools/night_heartbeat.py every 10-15 min while working.";

const reason =
  "Autopilot: " + open.length + " open (" + met + " covered, " + declined + " declined), " +
  count + "/" + cap + ". Continue - next:\n" + next +
  (open.length > shown ? "\n  ... +" + (open.length - shown) + " more" : "") +
  (wantFull ? RULES : "");

process.stdout.write(JSON.stringify({ decision: "block", reason }));
process.exit(0);
