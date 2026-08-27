#!/usr/bin/env node
// Stop hook: notice when MifBridge (or the editor behind it) actually broke during this turn,
// write it down so it survives, and tell Claude to raise it with Andre.
//
// Andre's rule (2026-08-21): "if somethings not working, flag the mifbridge error for fixing and
// announce to me, with prompt."
//
// WHY IT PERSISTS BEFORE IT BLOCKS
//   Claude Code sets stop_hook_active on the retry after ANY hook blocks. With two blocking Stop
//   hooks, the second one allows on that retry and its finding is silently dropped - which would
//   happen exactly on the turns where memory ALSO needed saving. So findings are appended to
//   ~/.claude/mifbridge-issues.md unconditionally; blocking is only how Claude gets told to
//   mention it. Nothing depends on winning the race.
//
// WHAT COUNTS AS BROKEN
//   Deliberately narrow. The bridge manual tells Claude to PROBE endpoints by sending a bogus key,
//   so "unrecognised parameter" is usually intentional and is NOT flagged. Neither is a plain
//   ok:false, which is a normal, expected answer for a great many calls. Only signatures that mean
//   the bridge or the editor genuinely fell over are matched.

const fs = require('fs');
const os = require('os');
const path = require('path');

const ISSUES = path.join(os.homedir(), '.claude', 'mifbridge-issues.md');

// Anything naming the watcher itself, its output file, or its test fixtures. A line containing
// one of these is talking ABOUT the watchdog, not reporting a bridge failure.
const SELF_REFS = [
  'mifbridge-error-watch',
  'mifbridge-issues',
  'fake_bad.jsonl',
  'fake_ok.jsonl',
  'SIGNATURES',
  'SELF_REFS',
];

// [label, regex]. Keep this tight: a noisy watchdog gets ignored, which is worse than none.
const SIGNATURES = [
  ['editor not answering (connection refused)', /WinError 10061|ConnectionRefusedError|connection refused/i],
  ['connection reset mid-call (editor likely died)', /ConnectionResetError|WinError 10054|connection reset/i],
  ['editor crashed', /has crashed|Assertion failed|EXCEPTION_ACCESS_VIOLATION|EXCEPTION_STACK_OVERFLOW/i],
  ['bridge returned HTTP 5xx', /HTTP Error 5\d\d|"?HTTP 5\d\d"?/],
  ['bridge endpoint missing (HTTP 404 on /api/)', /HTTP Error 404|"?HTTP 404"?/],
  ['request timed out against 8791', /timed out.*8791|8791.*timed out/i],
];

function readJsonLines(file) {
  try {
    return fs.readFileSync(file, 'utf8').split('\n');
  } catch (_) {
    return [];
  }
}

// Timestamp of the last real human message, so only THIS turn is inspected.
function lastHumanMs(lines) {
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i];
    if (!line || line.indexOf('"type":"user"') === -1) continue;
    let j;
    try { j = JSON.parse(line); } catch (_) { continue; }
    if (j.type !== 'user' || j.isSidechain) continue;
    const c = j.message && j.message.content;
    const isHuman =
      typeof c === 'string' ||
      (Array.isArray(c) && c.some((b) => b && b.type === 'text') &&
        !c.some((b) => b && b.type === 'tool_result'));
    if (!isHuman) continue;
    const t = Date.parse(j.timestamp || '');
    if (!isNaN(t)) return { ms: t, index: i };
  }
  return { ms: 0, index: 0 };
}

function main(input) {
  const tp = input.transcript_path;
  if (!tp || !fs.existsSync(tp)) return { found: [] };

  const lines = readJsonLines(tp);
  const { index } = lastHumanMs(lines);
  const seen = new Map();

  for (let i = index; i < lines.length; i++) {
    const line = lines[i];
    if (!line) continue;

    // SELF-REFERENCE GUARD. This hook's own source lists every signature it hunts for, and any
    // turn that edits or tests it puts those literals straight into the transcript. Without this
    // the watcher flags itself - it did exactly that on the turn it was written, reporting all
    // five signatures at once when no bridge call had been made at all.
    if (SELF_REFS.some((s) => line.indexOf(s) !== -1)) continue;

    // Only inspect tool RESULTS. A tool_use input is something Claude wrote - a file being
    // authored, a script being run - not something the bridge said back.
    if (line.indexOf('tool_result') === -1) continue;

    // Only bother with entries that mention the bridge at all.
    if (line.indexOf('8791') === -1 && line.indexOf('MifBridge') === -1 &&
        line.indexOf('/api/') === -1) continue;
    for (const [label, re] of SIGNATURES) {
      if (!re.test(line)) continue;
      if (!seen.has(label)) {
        const m = line.match(re);
        seen.set(label, (m && m[0] ? String(m[0]) : '').slice(0, 160));
      }
    }
  }
  return { found: [...seen.entries()] };
}

let raw = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (c) => (raw += c));
process.stdin.on('end', () => {
  let input = {};
  try { input = JSON.parse(raw || '{}'); } catch (_) { input = {}; }

  let found = [];
  try { found = main(input).found; } catch (_) { found = []; }

  if (!found.length) process.exit(0);

  // PERSIST FIRST - this must not depend on whether the block is delivered.
  const stamp = new Date().toISOString().slice(0, 16).replace('T', ' ');
  const cwd = String(input.cwd || '');
  let entry = `\n## ${stamp} — ${cwd}\n`;
  for (const [label, sample] of found) {
    entry += `- **${label}**${sample ? '  \n  `' + sample.replace(/`/g, "'") + '`' : ''}\n`;
  }
  try {
    if (!fs.existsSync(ISSUES)) {
      fs.writeFileSync(ISSUES,
        '# MifBridge issues seen during sessions\n\n' +
        'Written automatically by `hooks/mifbridge-error-watch.js` when a bridge call fails in a\n' +
        'way that means the bridge or the editor actually broke - not for ordinary `ok:false`\n' +
        'answers, and not for deliberate parameter probes.\n\n' +
        'Delete entries once they are fixed or understood.\n');
    }
    fs.appendFileSync(ISSUES, entry);
  } catch (_) { /* persisting is best-effort; never break the turn over it */ }

  // Already blocked once this turn by some hook - the file above is the durable record.
  if (input.stop_hook_active === true) process.exit(0);

  const list = found.map(([l]) => l).join('; ');
  const reason =
    'MifBridge trouble detected this turn: ' + list + '. ' +
    'It has been appended to ~/.claude/mifbridge-issues.md. Before ending the turn: tell Andre ' +
    'plainly what broke and whether it affected the work you just reported (say so if you cannot ' +
    'tell). MifBridge is his own plugin, so if this looks like a bridge defect rather than a ' +
    'misuse, give him a ready-to-paste prompt he can run in the MifBridge repo to fix it - name ' +
    'the endpoint, the exact error, and what you expected. Do not re-run the failed call blindly.';

  process.stdout.write(JSON.stringify({ decision: 'block', reason }));
  process.exit(0);
});
