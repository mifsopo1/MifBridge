"""CHECK: can a caller DISCOVER the values these parameters demand?

The generalisation of a real bug found 2026-08-31. `apply_spline_to_landscape` refused with "Pass
editLayer naming one that exists" - correct, and useless, because NO endpoint reported a landscape's
sculpt edit layer names. The caller was told to name something they could not enumerate. It blocked
an entire test suite until landscape_info grew an `editLayers[]` field.

That sits one level below audit_advice_gaps.py, which finds advice naming an OPERATION that does not
exist. This finds a parameter naming a VALUE that cannot be found:

    audit_advice_gaps       "call list_endpoints"              -> no such endpoint
    audit_value_discovery   editLayer "name one that exists"   -> nothing lists them

WHY THIS IS A CURATED MAP AND NOT A NAME MATCHER, which is the whole design and was learned by
writing the name matcher first and catching it being useless.

The first version matched each concept against endpoint NAMES: "landscape edit layer" would look for
a reader whose name contained those words. It reported everything clean. Then, testing it against the
bug it was built from - the rule that a checker proving nothing until run against a known instance -
`landscape_info` matched on the word "landscape" alone. It would have passed the original defect. And
tightening it to require every word fails the OTHER way, because the answer today lives in a FIELD
called editLayers[] inside landscape_info, not in an endpoint called list_edit_layers.

A reader is a (endpoint, field) pair. No amount of name matching sees a field, so the map below names
both, deliberately, and the check has two halves that fail for different reasons:

  UNMAPPED    a parameter naming an existing engine object with no entry here at all. Cannot pass
              vacuously: a new one is flagged the day it appears, and the only way to clear it is to
              say where its values come from.
  BROKEN      an entry whose reader does not actually return that field, checked LIVE against the
              running editor. That is what stops the map rotting into documentation of a past truth.

Live checking is best-effort: with no bridge the map is still checked for completeness, and the run
says which half it skipped rather than implying it verified more than it did.
"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import harvest_param_table as H            # one accepted-key parser, not two

# parameter (lowercased) -> (what it names, reader endpoint, field in that reader's response)
# `None` for the reader means "deliberately needs no enumerator", with the reason - an asset PATH is
# found with find_assets like any other asset, and a free-text name is the caller's to invent.
DISCOVERY = {
    "editlayer":     ("landscape edit layer", "landscape_info", "editLayers"),

    # Every spelling of "a bone on this skeleton". They all resolve the same way, and listing them
    # individually is the point - a new endpoint inventing boneC is flagged until someone says so.
    "bonename":      ("bone", "list_bones", "bones"),
    "bone1":         ("bone", "list_bones", "bones"),
    "bone2":         ("bone", "list_bones", "bones"),
    "bonea":         ("bone", "list_bones", "bones"),
    "boneb":         ("bone", "list_bones", "bones"),
    "sourcebone":    ("bone", "list_bones", "bones"),
    "targetbone":    ("bone", "list_bones", "bones"),
    "rootbone":      ("bone", "list_bones", "bones"),
    "bone":          ("bone", "list_bones", "bones"),
    "startbone":     ("bone", "list_bones", "bones"),
    "endbone":       ("bone", "list_bones", "bones"),
    "parentbone":    ("bone", "list_bones", "bones"),
    "socket":        ("socket", "list_sockets", "sockets"),
    "socketname":    ("socket", "list_sockets", "sockets"),
    "morphtarget":   ("morph target", "list_morph_targets", "morphTargets"),
    "virtualbone":   ("virtual bone", "list_virtual_bones", "virtualBones"),
    "collection":    ("collection", "list_collections", "collections"),
    "datalayer":     ("data layer", "list_data_layers", "dataLayers"),
    "sublevel":      ("sublevel", "list_sublevels", "sublevels"),

    # Deliberately unenumerated, with the reason. These are NOT gaps.
    "slot":          ("anim slot", None, "free text - the slot is CREATED by the call if absent"),
    "slotname":      ("anim slot", None, "free text - the slot is CREATED by the call if absent"),
    "marker":        ("sync marker", None, "free text on write; read back by describe_animation"),
    "markername":    ("sync marker", None, "free text on write; read back by describe_animation"),
    "notify":        ("anim notify", None, "free text - authored, not chosen from a list"),
    "notifytrack":   ("anim notify track", None, "free text - created by add_anim_notify_track"),
    "curve":         ("anim curve", None, "free text - authored by add_anim_curve"),
    "curvename":     ("anim curve", None, "free text - authored by add_anim_curve"),
    "track":         ("sequencer track", None, "created by add_sequence_track, not chosen"),
    "trackname":     ("sequencer track", None, "created by add_sequence_track, not chosen"),
    "chain":         ("retarget chain", None, "created by add_ik_retarget_chain, not chosen"),
    "chainname":     ("retarget chain", None, "created by add_ik_retarget_chain, not chosen"),
    "goal":          ("ik goal", None, "created by add_ik_goal, not chosen"),
    "goalname":      ("ik goal", None, "created by add_ik_goal, not chosen"),
    "emitter":       ("niagara emitter", None, "created by add_niagara_emitter, not chosen"),
    "emittername":   ("niagara emitter", None, "created by add_niagara_emitter, not chosen"),
    "blackboardkey": ("blackboard key", None, "created by add_blackboard_key, not chosen"),
    "tag":           ("gameplay tag", None, "list_gameplay_tags exists; tags are also authored"),
    "tagname":       ("gameplay tag", None, "list_gameplay_tags exists; tags are also authored"),
    "parametername": ("material/niagara parameter", None,
                      "list_material_parameters / describe_niagara_system, by asset kind"),

    # NOT LOOKUPS AT ALL. Recorded so the stem matcher's false positives are answered once here
    # instead of being re-raised every time someone reads its output.
    "notifyclass":      ("a CLASS path, not a notify name", None, "find_assets / describe_class"),
    "notifystateclass": ("a CLASS path, not a notify name", None, "find_assets / describe_class"),
    "fieldnotify":      ("a BOOLEAN flag, not a notify", None, "not a name at all"),
    "repnotify":        ("a BOOLEAN flag, not a notify", None, "not a name at all"),
    "repnotifyfunction": ("a function the call CREATES", None, "authored, not chosen"),
    "minbonesize":      ("a NUMBER, not a bone", None, "not a name at all"),
    "datalayertype":    ("an ENUM, not a data layer", None, "the accepted values are in the refusal"),
}

# Parameter names that clearly denote "the name of an existing engine object". Anything matching
# these and absent from DISCOVERY is reported UNMAPPED.
def looks_like_a_named_thing(key):
    k = key.lower()
    if k in DISCOVERY:
        return True
    for stem in ("bone", "socket", "slot", "curve", "marker", "notify", "editlayer",
                 "morphtarget", "virtualbone", "datalayer", "blackboardkey", "emitter"):
        if stem in k:
            return True
    return False


# Readers that need an argument before they can answer. Probing them with {} makes them REFUSE,
# and a refusal is not evidence a field is missing - the first run of this reported twelve bone
# mappings BROKEN because list_bones quite correctly wants a mesh. Same unexercised-versus-failed
# distinction the suites were corrected for: a check that cannot run must say so, not fail.
def probe_payload(endpoint):
    try:
        import mifaudit as M
    except Exception:
        return {}
    if endpoint in ("list_bones", "list_sockets", "list_morph_targets", "list_virtual_bones"):
        mesh, _ = M.discover_skeletal_mesh(())
        return {"path": mesh} if mesh else {}
    return {}


def live_fields(endpoint, payload=None):
    """The top-level keys a reader really returns.

    None  - the bridge is unreachable.
    False - the reader REFUSED, so this says nothing about the field either way.
    """
    try:
        import mifaudit as M
        r = M.raw_post(endpoint, payload if payload is not None else probe_payload(endpoint),
                       timeout=60)
    except Exception:
        return None
    if not isinstance(r, dict):
        return None
    if r.get("ok") is False:
        return False
    keys = set(r.keys())
    # Readers that answer per-object nest their rows; look one level in so `editLayers` inside
    # landscape_info's landscapes[] counts as reported.
    for v in r.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            keys |= set(v[0].keys())
    return keys


def main():
    # --check separates what BLOCKS from what merely reports, the same split harvest_param_table
    # uses. UNMAPPED blocks: it is static, deterministic, and means someone added a parameter
    # naming an engine object without saying where its values come from. BROKEN blocks only when a
    # live check actually RAN - a packaging box with no editor must not fail for want of one, and
    # "could not check" is not "is wrong".
    blocking = "--check" in sys.argv

    rows, _missing, _problems, _decls = H.harvest()

    unmapped, checked, broken, skipped = [], 0, [], []
    seen = set()
    for ep, guard, _via in rows:
        # harvest stores the accepted keys as the raw TEXT("a"), TEXT("b") source text, not a list -
        # iterating it directly walks CHARACTERS and silently matches nothing, which is how the first
        # run of this reported "0 parameter roles" against a codebase full of them.
        for key in re.findall(r'TEXT\("([^"]+)"\)', guard.get("keys") or ""):
            if not looks_like_a_named_thing(key):
                continue
            k = key.lower()
            if k not in DISCOVERY:
                unmapped.append((ep, key))
            seen.add(k)

    print("checked %d endpoint(s); %d parameter role(s) named an existing engine object"
          % (len(rows), len(seen)))

    # ---- the half that cannot pass vacuously
    if unmapped:
        print()
        print("UNMAPPED - these name an existing engine object and nothing here says where a")
        print("caller finds the valid values. Add an entry to DISCOVERY: either the (endpoint,")
        print("field) that enumerates them, or None with the reason it needs no enumerator.")
        for ep, key in sorted(set(unmapped)):
            print("  %-38s %s" % (ep, key))

    # ---- the half that stops the map rotting
    live_cache = {}
    for k, (what, reader, field) in sorted(DISCOVERY.items()):
        if reader is None or k not in seen:
            continue
        if reader not in live_cache:
            live_cache[reader] = live_fields(reader)
        fields = live_cache[reader]
        if fields is None:
            skipped.append((k, reader, "no bridge"))
            continue
        if fields is False:
            skipped.append((k, reader, "the reader refused - needs a fixture this audit could "
                                       "not supply, so the mapping is UNVERIFIED, not wrong"))
            continue
        checked += 1
        if field not in fields:
            broken.append((k, what, reader, field, sorted(fields)[:12]))

    print()
    if broken:
        print("BROKEN - the mapped reader does NOT return that field. Either the field was renamed")
        print("or it never existed, and a caller following this map finds nothing:")
        for k, what, reader, field, got in broken:
            print("  %-16s %s -> %s.%s   (returns: %s)" % (k, what, reader, field, ", ".join(got)))
    elif checked:
        print("every mapped reader really returns its field - verified live against the running")
        print("editor, %d of them. No parameter here demands a value the caller cannot discover."
              % checked)
    if skipped:
        print()
        print("UNVERIFIED - mapped but not checked. Not a finding either way:")
        for reason in sorted({why for _, _, why in skipped}):
            readers = sorted({r for _, r, w in skipped if w == reason})
            print("  %s" % reason)
            print("    %s" % ", ".join(readers))
    if not unmapped and not broken and not checked:
        print("no live check ran and nothing is unmapped - completeness only.")

    if blocking and unmapped:
        print()
        print("BLOCKING: %d parameter(s) name an engine object with no discovery entry."
              % len(set(unmapped)))
        return 1
    if blocking and broken:
        print()
        print("BLOCKING: %d mapping(s) point at a field their reader does not return." % len(broken))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
