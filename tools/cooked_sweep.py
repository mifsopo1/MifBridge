"""Hand every path-taking endpoint a REAL COOKED asset and see what survives.

WHY THIS IS NOT fuzz_endpoints.py. That sweep hands every endpoint a GHOST path - something that does
not exist - which tests the "not found" branch and nothing else. It has never asked what happens
against a real COOKED asset, and that is the branch that actually matters here: DDS2 is a cooked game,
so nearly every asset a modder touches is cooked, and docs/02_GOTCHAS.md section 6c records that
cooked assets keep their runtime data and lose their editor data in ways that are FATAL rather than
empty:

  * UUserDefinedStruct - a CastChecked on cooked editor data terminates the editor
  * UMaterial         - UMaterialExpression is UCLASS(Optional), so GetExpressions() derefs stripped
                        editor-only data with no null check
  * UNiagaraSystem    - duplication crashed the editor in FVersionedNiagaraEmitterData::PostLoad

Two of those three are fatal, and the common case is the untested one. That asymmetry is the whole
reason this file exists.

SAFETY. Read-only by intent and by construction: `confirm` is never sent (mifaudit strips it and this
does not use scratch_confirm), the DENY list still applies, nothing is saved, and no scratch asset is
created. An endpoint that mutates without confirm can still dirty a package in memory - that is
recoverable, is never written to disk, and is the price of testing the branch at all.

A crash here is a FINDING, not an accident. The editor is relaunched and the sweep continues, and the
endpoint that killed it is recorded with the exact asset that did it, so the repro is one call.
"""
import json
import os
import sys
import time

import mifaudit as M

# Class -> the parameter names that plausibly want an asset of that class. Deliberately conservative:
# feeding a Material path to something expecting a Blueprint tests argument validation, not the cooked
# hazard, and would bury the findings that matter in noise.
BY_CLASS = [
    ("Material", ("material", "materialPath")),
    ("MaterialInstanceConstant", ("material", "materialPath", "instance")),
    ("Blueprint", ("blueprintId", "blueprint")),
    ("SkeletalMesh", ("mesh", "skeletalMesh")),
    ("StaticMesh", ("mesh", "staticMesh")),
    ("UserDefinedStruct", ("struct", "structPath", "structName")),
    ("UserDefinedEnum", ("enum", "enumPath")),
    ("DataTable", ("dataTable", "table")),
    ("NiagaraSystem", ("system",)),
    ("Texture2D", ("texture",)),
    ("AnimSequence", ("animation", "anim")),
]
# Generic path parameters take a sample of every class in turn, since the endpoint could want any.
GENERIC = ("path", "assetPath")


def sample_assets():
    """One real COOKED asset per class, chosen from the live registry."""
    out = {}
    for cls, _ in BY_CLASS:
        r = M.call("find_assets", {"class": cls, "pathPrefix": "/Game/", "limit": 3})
        for a in (r.get("assets") or []):
            p = a.get("path")
            if p:
                out.setdefault(cls, []).append(p)
    return out


def main():
    ok, why = M.require_sdk_bridge(force=True)
    if not ok:
        print("refusing to run: %s" % why)
        return 2
    print("target: %s" % why, flush=True)

    assets = sample_assets()
    print("cooked samples: %s" % ", ".join("%s=%d" % (k, len(v)) for k, v in sorted(assets.items())), flush=True)
    if not assets:
        print("no cooked assets found - nothing to sweep")
        return 3

    names = sorted(M.endpoint_names())
    deny = set(M.DENY)
    checked = crashes = 0
    last_reported = 0
    findings = []

    for ep in names:
        if ep in deny:
            continue
        acc = set(M.call("describe_endpoint", {"name": ep}).get("acceptedParams") or [])
        if not acc:
            continue

        # Build the payloads worth trying for this endpoint.
        trials = []
        for cls, params in BY_CLASS:
            for p in params:
                if p in acc and assets.get(cls):
                    trials.append((cls, {p: assets[cls][0]}))
        for p in GENERIC:
            if p in acc:
                for cls in ("Material", "Blueprint", "UserDefinedStruct", "NiagaraSystem", "DataTable"):
                    if assets.get(cls):
                        trials.append((cls, {p: assets[cls][0]}))
        if not trials:
            continue

        for cls, payload in trials:
            checked += 1
            try:
                r = M.call(ep, payload, timeout=60)
                alive = True
            except Exception:
                r, alive = None, M.bridge_responsive()
            if not alive or not M.bridge_responsive():
                crashes += 1
                asset = list(payload.values())[0]
                M.record("COOKED_CRASH", ep,
                         "the editor died against a real cooked %s: %s" % (cls, asset),
                         severity="critical", probe="cooked", sample=json.dumps(payload))
                findings.append((ep, cls, asset))
                print("  CRASH  %-32s %s (%s)" % (ep, cls, asset), flush=True)
                M.launch_editor()
                if not M.wait_for_bridge(timeout=900):
                    print("  editor did not come back - stopping", flush=True)
                    return 1
                break     # do not keep hammering an endpoint that just killed the editor
        # FLUSHED, AND IT NAMES THE ENDPOINT. Without flush this whole sweep is invisible until the
        # process ends: ~900 calls produced a 0-byte log for ten minutes, and if the editor had died
        # in the middle there would have been nothing to say WHERE. That is the failure PM-012 is
        # about - the harness knew and did not say - and the same fix run_all_suites already carries.
        # The endpoint name matters as much as the count: the last line printed is the call that hung.
        # DELTA, not modulo. This was `checked % 25 == 0` and it silently skipped most of its own
        # reports: `checked` increments once per CALL (inside the trials loop) while this test only
        # runs once per ENDPOINT, so any multiple of 25 crossed mid-endpoint is stepped straight over.
        # Observed 2026-08-26: the run printed 75, 150, 250 and then 750 - a 500-call gap with five
        # minutes of silence, which is indistinguishable from a hang and sent one investigation down
        # a blind alley. That is precisely the failure the comment above says this exists to prevent:
        # with a gap that size, "the last line printed is the call that hung" is not true.
        if checked - last_reported >= 25:
            print("  ... %d calls, %d crash(es), at %s" % (checked, crashes, ep), flush=True)
            last_reported = checked

    print("")
    print("=" * 72)
    print("cooked sweep: %d calls across %d endpoints, %d crash(es)" % (checked, len(names), crashes))
    for ep, cls, asset in findings:
        print("  %-32s %-24s %s" % (ep, cls, asset))
    if not findings:
        print("nothing died against a real cooked asset.")
    print("=" * 72)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
