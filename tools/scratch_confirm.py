"""Allow `confirm` ONLY when every path in the payload is a scratch path.

WHY THIS EXISTS. mifaudit strips `confirm` from every payload, alongside `save`, `force`, `overwrite`,
`discardUnsaved` and `replaceExisting`. That guard is correct and has earned its place: it is why an
unattended overnight run cannot destroy a real asset.

The cost has become visible though. Eleven mutating endpoints had NO success-path coverage because of
it, and those are exactly the endpoints where a silent failure costs most.

NINE of them name their target by a required asset path: write_datatable_rows, delete_datatable_rows,
remove_enum_value, remove_interface, remove_component, revert_inherited_component, rename_variable,
rename_function, rename_event_dispatcher.

TWO MORE, remove_node and rename_event, are addressed primarily by nodeGuid - but both also accept an
OPTIONAL graphId ("disambiguates a reused guid", per describe_endpoint), and a graphId returned by
this bridge is itself a full object path (confirmed live: "/Game/_MifX/BP_1.BP_1::EventGraph"), which
IS pathlike and DOES satisfy check() when the owning blueprint is scratch. An earlier version of this
docstring claimed these two "have no path parameter to pass even if you wanted to" - that was wrong,
caught only by actually calling remove_node with graphId included and watching it succeed (see
test_confirm_gated.py's T343, and test_node_spawns.py's T333). Passing graphId is the ordinary case
anyway: locating a node to remove or rename means you already have the graph it lives in.

What check() genuinely cannot do is bless a bare {nodeGuid} with no graphId at all - there the guid is
all there is, and a guid proves nothing about which asset it belongs to. That narrower case is refused
and always will be; it just is not the common one.

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
        # Absence of evidence is not evidence of safety: a bare guid with no graphId could be
        # pointing at anything. remove_node/rename_event pass this check fine WITH a graphId - see
        # the module docstring - this refusal is for the narrower case of neither being present.
        raise NotScratch(
            "no asset path in this payload, so it cannot be shown to be scratch-only. Address the "
            "target by a /Game/_Mif... path, or do not use confirm here. For remove_node/rename_event "
            "specifically: pass graphId too, not just nodeGuid - the graphId this bridge returns is "
            "itself a full object path, and check() accepts it when the owning blueprint is scratch.")
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
        # remove_node/rename_event's real shape WITH graphId - a real object path, not a bare guid -
        # so this is the common case, and it is allowed.
        {"nodeGuid": "6A1F00006A1F00006A1F00006A1F0000",
         "graphId": "/Game/_MifNodes/BP_1.BP_1::EventGraph"},
    ]
    BAD = [
        ({}, "no path at all"),
        ({"path": "/Game/Characters/Alisha"}, "a real game asset"),
        ({"path": "/Game/_MifDT/T", "other": "/Game/Real/Thing"}, "one real path among scratch ones"),
        ({"path": "/Game/_MifDT/T", "save": True}, "save has no exemption"),
        ({"nested": {"deep": ["/Game/Real/Thing"]}}, "a real path buried in a nested structure"),
        ({"path": "/DDS2Casino/Asset/Thing"}, "another mount point entirely"),
        ({"nodeGuid": "6A1F-DEAD", "graphId": "9C2E-BEEF"},
         "remove_node/rename_event WITHOUT graphId as a real path - a bare guid proves nothing"),
        ({"nodeGuid": "6A1F-DEAD", "graphId": "/Game/Real/BP_1.BP_1::EventGraph"},
         "remove_node/rename_event pointed at a REAL blueprint's graph, not a scratch one"),
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
