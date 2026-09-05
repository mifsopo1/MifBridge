"""Which cooked-only suites WRITE when the cooked guard does not fire, and which only read.

The question the write-hazard item asks first. Answered by READING the assertions, not by running
them against somebody's uncooked project - which is the item's own instruction and the reason this
script exists rather than a sweep.

METHOD. For every suite that mentions a cooked assertion, find the endpoints it calls, and classify
each against the LIVE readOnly list from self_audit rather than against a guess from the name. A
suite that calls only read-only endpoints in its cooked sections is a readability problem; one that
calls a transacted or self-managed endpoint is a safety problem, because on an uncooked project the
refusal it asserts never arrives and the call lands.
"""
import glob
import io
import json
import os
import re
import sys

sys.path.insert(0, r"D:\DDS2SDK\Game\Plugins\MifBridge\tools")
import mifaudit as M

TOOLS = r"D:\DDS2SDK\Game\Plugins\MifBridge\tools"
CALL = re.compile(r'(?:M\.call|M\.raw_post|SC\.confirm_call)\s*\(\s*["\']([a-z0-9_]+)["\']')
COOKED_LINE = re.compile(r"cooked", re.I)

ok, why = M.require_sdk_bridge()
if not ok:
    print("no editor: %s" % why)
    raise SystemExit(2)

audit = M.call("self_audit", {"includeEndpointDetails": True}, timeout=180)
rows = audit.get("endpointDetails") or []
bucket = {r["name"]: r.get("bucket") for r in rows if isinstance(r, dict) and r.get("name")}
print("buckets read from self_audit: %d endpoint(s)" % len(bucket))
if len(bucket) < 100:
    # THE DENOMINATOR IS THE CHECK. The first run of this asked for includeList/includeDetails -
    # neither is a parameter self_audit accepts - got no endpointDetails, and every endpoint fell
    # through to the READ bucket by default. It printed "28 readability, 0 safety", which is the
    # answer this investigation was hoping for and was entirely an artefact.
    print("REFUSING to classify: self_audit returned almost no buckets, so every endpoint would")
    print("fall through to the read bucket and the split below would be manufactured.")
    raise SystemExit(2)

write_suites, read_suites, unknown = [], [], []
for path in sorted(glob.glob(os.path.join(TOOLS, "test_*.py"))):
    src = io.open(path, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
    lines = src.split("\n")
    # a cooked assertion: a check(...) line mentioning cooked
    cooked_at = [i for i, l in enumerate(lines)
                 if COOKED_LINE.search(l) and ("check(" in l or "check (" in l)]
    if not cooked_at:
        continue
    # endpoints called within 25 lines ABOVE each cooked assertion - the call it is asserting on
    eps = set()
    for i in cooked_at:
        for l in lines[max(0, i - 25):i + 3]:
            for m in CALL.finditer(l):
                eps.add(m.group(1))
    writes = sorted(e for e in eps if bucket.get(e) in ("transacted", "selfManaged"))
    reads = sorted(e for e in eps if bucket.get(e) == "readOnly")
    unk = sorted(e for e in eps if e not in bucket)
    name = os.path.basename(path)
    if writes:
        write_suites.append((name, len(cooked_at), writes))
    elif reads or unk:
        read_suites.append((name, len(cooked_at), reads + unk))
    if unk:
        unknown.append((name, unk))

print("")
print("SUITES WHOSE COOKED ASSERTIONS SIT ON A NON-READ-ONLY ENDPOINT: %d" % len(write_suites))
print("  (bucket transacted or selfManaged - it GETS the blanket transaction, so a call CAN land.")
print("   That is a superset of 'mutates': list_redirectors and lighting_build_status are in here")
print("   and plainly do not write. Confirmed-landing so far: test_anim_curve, measured.)")
for n, c, w in write_suites:
    print("  %-38s %2d cooked assertion(s)  not-readOnly: %s" % (n, c, ", ".join(w[:5])))
print("")
print("SUITES WHOSE COOKED ASSERTIONS ARE READ-ONLY (cannot land, readability only): %d"
      % len(read_suites))
for n, c, r in read_suites:
    print("  %-38s %2d cooked assertion(s)  reads: %s" % (n, c, ", ".join(r[:5])))
if unknown:
    print("")
    print("endpoints not in self_audit's list (addon ops or helpers), not classified:")
    for n, u in unknown[:6]:
        print("  %-38s %s" % (n, ", ".join(u[:6])))
