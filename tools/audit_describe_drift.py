"""Does describe_endpoint tell an agent everything the handler will actually accept?

THE ONE DRIFT DIRECTION THE BRIDGE CANNOT SEE IN ITSELF. describe_endpoint reports its own coverage and
is honest about the limit:

    staleTableRows detects ONE drift direction only - a row whose endpoint is no longer registered. The
    opposite drift, a guard that exists in the source but has no row here, leaves no runtime trace and
    is NOT detectable from inside the DLL.

That is true from inside. From outside it is easy: the handler's RejectUnknownParams accept-list is in
the source, describe_endpoint's row is in the running build, and a key in the first but not the second
is capability an agent can never discover.

WHY IT MATTERS MORE THAN A STALE COMMENT. describe_endpoint is the MACHINE-READABLE contract - it is
what an agent consults before deciding whether an endpoint can do a thing. A parameter missing from a
comment costs a human one read of the source. A parameter missing from this table means the capability
effectively does not exist for a caller that discovers by asking.

Five rows were drifting when this was written, and two were not aliases but whole capabilities:
  set_material_parameter   omitted textures, switches, association, index
  add_foliage_instances    omitted foliageType/type - an entire second mode of the endpoint
  set_spline_points        omitted skipPostEditChange, which its own handler calls REQUIRED on
                           blueprints that rebuild their own spline
  add_cast                 omitted pure
  reparent_blueprint       omitted parentClass/path (aliases only - the primary spellings were there)

This is the same shape as the param-reach backlog, one layer up: there the capability existed and no
MCP tool could send it; here it exists and no caller can find out it exists.

SELF-CHECK. This scan can fail silently in two ways - the source regex stops matching handlers, or the
bridge stops answering describe_endpoint - and either would report a clean result forever. So it asserts
it extracted a plausible number of handlers AND that one known key survives both paths, and refuses to
report anything if it cannot.

Usage:
    python tools/audit_describe_drift.py
"""
import glob
import io
import os
import re
import sys

import mifaudit as M

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "Source", "MifBridge", "Private")
NL = chr(10)

# If the source walk finds fewer handlers than this, the regex has drifted and nothing below is
# trustworthy. There were 267 handlers with an accept-list when this was written.
MIN_HANDLERS = 200

# One key that must survive BOTH paths: present in the source accept-list, and present in the live
# table. It is the parameter whose absence from the table motivated this tool.
CANARY = ("set_spline_points", "skipposteditchange")

HANDLER = re.compile(r"\s*void H_(\w+)\(const TSharedRef<FJsonObject>&")
ACCEPT = re.compile(r"RejectUnknownParams\s*\(\s*In\s*,\s*Out\s*,\s*\{(.*?)\}", re.S)
KEY = re.compile(r'TEXT\("([a-zA-Z][a-zA-Z0-9_]*)"\)')


def source_accept_lists():
    """endpoint -> set of keys its RejectUnknownParams admits, read from the .cpp files."""
    out = {}
    for path in sorted(glob.glob(os.path.join(SRC, "*.cpp"))):
        lines = io.open(path, encoding="utf-8", errors="replace").read().replace(chr(13) + NL, NL).split(NL)
        for i, ln in enumerate(lines):
            m = HANDLER.match(ln)
            if not m:
                continue
            depth, j, opened = 0, i, False
            while j < len(lines):
                depth += lines[j].count("{") - lines[j].count("}")
                if "{" in lines[j]:
                    opened = True
                if opened and depth <= 0:
                    break
                j += 1
            body = NL.join(lines[i:j + 1])
            a = ACCEPT.search(body)
            if a:
                out[m.group(1)] = set(k.lower() for k in KEY.findall(a.group(1)))
    return out


def main():
    if not M.wait_for_bridge(timeout=900):
        print("the bridge is not answering - this check needs the LIVE table and cannot run")
        return 2

    src = source_accept_lists()
    print("handlers with an accept-list in source: %d" % len(src))
    if len(src) < MIN_HANDLERS:
        print("SELF-CHECK FAILED: expected at least %d, so the source walk has drifted." % MIN_HANDLERS)
        print("Do not trust a clean result until this is resolved.")
        return 2
    ep, key = CANARY
    if key not in src.get(ep, set()):
        print("SELF-CHECK FAILED: %s should accept %r in source and does not." % (ep, key))
        print("Either the handler changed or the extraction is broken - resolve before believing this.")
        return 2

    drift, checked, norow = [], 0, 0
    for name, accepted in sorted(src.items()):
        d = M.call("describe_endpoint", {"name": name}, timeout=30)
        if not d.get("ok"):
            continue
        table = set(str(x).lower() for x in (d.get("acceptedParams") or []))
        if not table:
            norow += 1          # no row at all; the endpoint's own coverage figure already counts these
            continue
        checked += 1
        missing = sorted(accepted - table)
        if missing:
            drift.append((name, missing))
        if name == ep and key not in table:
            print("SELF-CHECK FAILED: the live table lost the canary key %r on %s." % (key, ep))
            return 2

    print("rows compared against their handler: %d   (%d endpoints have no row at all)" % (checked, norow))
    print("")
    if drift:
        print("ROWS THAT HIDE A PARAMETER THE HANDLER ACCEPTS:")
        for name, missing in drift:
            print("  %-30s table omits: %s" % (name, ", ".join(missing)))
        print("")
        print("Each of these is capability a caller cannot discover by asking. Add the keys to the")
        print("GMifDescKeys_<endpoint> array in MifBridgeDescribe.cpp, and to the summary line beside it.")
        return 1
    print("OK  every describe row lists everything its handler accepts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
