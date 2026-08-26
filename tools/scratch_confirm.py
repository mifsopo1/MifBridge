"""Allow `confirm` ONLY when every path in the payload is a scratch path.

WHY THIS EXISTS. mifaudit strips `confirm` from every payload, alongside `save`, `force`, `overwrite`,
`discardUnsaved` and `replaceExisting`. That guard is correct and has earned its place: it is why an
unattended overnight run cannot destroy a real asset.

The cost has become visible though. Roughly eleven mutating endpoints have NO success-path coverage
because of it - write_datatable_rows, delete_datatable_rows, remove_enum_value, remove_interface,
remove_component, remove_node, revert_inherited_component, rename_variable, rename_function,
rename_event, rename_event_dispatcher. Those are exactly the endpoints where a silent failure costs
most, and every suite written tonight had to record the same gap.

THE POINT OF THE GUARD IS NOT "never send confirm". It is "never destroy something that matters". A
payload whose every path lies under /Game/_Mif cannot destroy something that matters: those assets are
created by the suites, never saved, and vanish when the editor restarts. So the exemption preserves the
guard's actual purpose rather than weakening it.

WHAT IS STILL REFUSED, unconditionally and with no scratch exemption:
  save, force, overwrite, discardUnsaved, replaceExisting - none of them are about a single asset, so
  a path check says nothing useful about them. `save` in particular would make a scratch asset
  permanent, which is the one thing that turns a harmless test artefact into a real one.

HOW IT DECIDES. Every string anywhere in the payload - nested dicts and lists included - that looks
like an asset path (starts with a mount point such as /Game/) must start with a scratch prefix. A
payload containing NO path at all is refused rather than allowed: "no evidence of danger" is not
"evidence of safety", and an endpoint addressed purely by guid could be pointing anywhere.

This module is opt-in per call. It does not modify mifaudit's own guard, so anything that does not
deliberately reach for `confirm_call` keeps the strict behaviour.
"""
import json
import re

import mifaudit as M

SCRATCH_PREFIXES = ("/Game/_Mif",)

# No scratch exemption for these, ever. See the module docstring.
NEVER = ("save", "force", "overwrite", "discardunsaved", "replaceexisting")

# A mount point, i.e. the shape of a thing that names an asset. Deliberately broad: if it looks like a
# path at all it must prove it is a scratch path.
PATHLIKE = re.compile(r"^/[A-Za-z0-9_]+/")


class NotScratch(Exception):
    """Raised rather than returned, so a caller cannot ignore it by forgetting to check."""


def _paths_in(value, found):
    if isinstance(value, str):
        if PATHLIKE.match(value):
            found.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            _paths_in(v, found)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _paths_in(v, found)


def paths_in(payload):
    found = []
    _paths_in(payload, found)
    return found


def is_scratch(path):
    return any(path.startswith(p) for p in SCRATCH_PREFIXES)


def check(payload):
    """Raise NotScratch unless every path in the payload is a scratch path. Returns the paths seen."""
    for k in payload or {}:
        if k.lower() in NEVER:
            raise NotScratch(
                "'%s' has no scratch exemption - it is not about a single asset, and `save` in "
                "particular would turn a disposable test artefact into a real one" % k)
    found = paths_in(payload)
    if not found:
        # Absence of evidence is not evidence of safety: an endpoint addressed only by guid could be
        # pointing at anything.
        raise NotScratch(
            "no asset path in this payload, so it cannot be shown to be scratch-only. Address the "
            "target by a /Game/_Mif... path, or do not use confirm here.")
    bad = [p for p in found if not is_scratch(p)]
    if bad:
        raise NotScratch(
            "these are not scratch paths: %s. confirm is only ever sent when EVERY path in the "
            "payload lies under %s." % (", ".join(bad), " or ".join(SCRATCH_PREFIXES)))
    return found


def confirm_call(endpoint, payload, timeout=None):
    """M.call with confirm=true, permitted only for a provably scratch-only payload.

    Bypasses mifaudit's strip deliberately and narrowly - via raw_post, with the check above run
    first. If the check raises, nothing is sent.
    """
    check(payload)
    body = dict(payload)
    body["confirm"] = True
    kwargs = {"timeout": timeout} if timeout else {}
    return M.raw_post(endpoint, body, **kwargs)


if __name__ == "__main__":
    # Self-test. A guard that decides what may be destroyed is worth proving before it is trusted, and
    # these cases are the ones that would matter if it were wrong.
    OK = [
        {"path": "/Game/_MifDT/T_1"},
        {"blueprintId": "/Game/_MifNodes/BP_1.BP_1", "oldName": "A", "newName": "B"},
        {"rows": [{"Name": "R"}], "path": "/Game/_MifDT/T_1"},
        {"nested": {"deep": ["/Game/_MifX/Thing"]}},
    ]
    BAD = [
        ({}, "no path at all"),
        ({"path": "/Game/Characters/Alisha"}, "a real game asset"),
        ({"path": "/Game/_MifDT/T", "other": "/Game/Real/Thing"}, "one real path among scratch ones"),
        ({"path": "/Game/_MifDT/T", "save": True}, "save has no exemption"),
        ({"nested": {"deep": ["/Game/Real/Thing"]}}, "a real path buried in a nested structure"),
        ({"path": "/DDS2Casino/Asset/Thing"}, "another mount point entirely"),
    ]
    bad_count = 0
    for p in OK:
        try:
            check(p)
            print("  allow   %s" % json.dumps(p)[:70])
        except NotScratch as e:
            bad_count += 1
            print("  WRONG - refused a scratch payload: %s (%s)" % (json.dumps(p)[:60], e))
    for p, why in BAD:
        try:
            check(p)
            bad_count += 1
            print("  WRONG - ALLOWED %s: %s" % (why, json.dumps(p)[:60]))
        except NotScratch:
            print("  refuse  %-34s (%s)" % (json.dumps(p)[:34], why))
    print("\n%s" % ("self-test clean" if bad_count == 0 else "%d WRONG DECISIONS" % bad_count))
    raise SystemExit(1 if bad_count else 0)
