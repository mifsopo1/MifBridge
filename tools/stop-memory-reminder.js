#!/usr/bin/env node
// Stop hook: make Claude save session context (yays/nays/leanings/decisions) to
// its per-project memory before ending a turn.
//
// Behaviour:
//   - stop_hook_active === true  -> allow the stop (loop safety; we already reminded once).
//   - a memory file under ~/.claude/projects/<sanitized-cwd>/memory/ was modified AFTER the
//     last human message in the transcript -> allow the stop (memory already saved this turn).
//   - otherwise -> block once with a reminder. Any parse/IO failure falls back to "block once",
//     which is the safe direction for this rule.
//
// Andre's rule (2026-08-16): "always create context and memory after everything we talk
// about — yays, nays, good/bad ideas, where we're starting to lean — as a stop hook."
//
// 2026-08-21: that casual phrasing had been taken literally and turned into YAY/NAY/LEAN/OPEN
// labels in the log. Andre: "you started doing them 2 days ago and i hate them". The SUBSTANCE
// he asked for is decisions and leanings; the tag vocabulary was never the point. Plain prose now.

const fs = require('fs');
const os = require('os');
const path = require('path');

const REASON =
  "Stop hook (Andre's standing rule): before ending this turn, save context to memory. " +
  "If anything was decided, rejected, leaned toward, or judged a good or bad idea, record it " +
  "in the project's decision-log memory as a dated plain-English line saying what was decided " +
  "and why (create it and index it in MEMORY.md if it doesn't exist). Do NOT label entries " +
  "YAY/NAY/LEAN/OPEN or use any other shouty tag vocabulary - Andre dislikes it; write the way " +
  "you would tell a colleague. Update the project memory if the state of work changed, and keep " +
  "MEMORY.md's index current. Then stop. " +
  "If there is genuinely nothing new since the last save, say so in one line and stop.";

function block() {
  process.stdout.write(JSON.stringify({ decision: 'block', reason: REASON }));
  process.exit(0);
}
function allow() {
  process.exit(0);
}

let raw = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (c) => (raw += c));
process.stdin.on('end', () => {
  let input = {};
  try { input = JSON.parse(raw || '{}'); } catch (_) { input = {}; }

  if (input.stop_hook_active === true) return allow();

  try {
    const cwd = String(input.cwd || process.cwd());
    const sanitized = cwd.replace(/[^a-zA-Z0-9]/g, '-');
    const memDir = path.join(os.homedir(), '.claude', 'projects', sanitized, 'memory');

    // Latest memory write time.
    let latestMemMs = 0;
    for (const f of fs.readdirSync(memDir)) {
      try {
        const st = fs.statSync(path.join(memDir, f));
        if (st.isFile() && st.mtimeMs > latestMemMs) latestMemMs = st.mtimeMs;
      } catch (_) { /* ignore */ }
    }
    if (!latestMemMs) return block(); // no memory at all -> definitely remind

    // Timestamp of the last human message in the transcript.
    const tp = input.transcript_path;
    if (!tp || !fs.existsSync(tp)) return block();
    const lines = fs.readFileSync(tp, 'utf8').split('\n');
    let lastUserMs = 0;
    for (let i = lines.length - 1; i >= 0; i--) {
      const line = lines[i];
      if (!line || line.indexOf('"type":"user"') === -1) continue;
      let j;
      try { j = JSON.parse(line); } catch (_) { continue; }
      if (j.type !== 'user' || j.isSidechain) continue;
      const content = j.message && j.message.content;
      const isHuman =
        typeof content === 'string' ||
        (Array.isArray(content) &&
          content.some((b) => b && b.type === 'text') &&
          !content.some((b) => b && b.type === 'tool_result'));
      if (!isHuman) continue;
      const t = Date.parse(j.timestamp || '');
      if (!isNaN(t)) { lastUserMs = t; break; }
    }
    if (!lastUserMs) return block();

    // Memory written after the last human message -> already saved this turn.
    if (latestMemMs > lastUserMs) return allow();
    return block();
  } catch (_) {
    return block();
  }
});
