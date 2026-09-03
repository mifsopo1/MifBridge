"""Which suites ADOPT an object of a class ANOTHER SUITE CREATES, without filtering out scratch?

WHY THIS EXISTS. On 2026-09-01 test_landscape_heightmap reported 1590uu of collision error against
a perfectly good endpoint. It was measuring the wrong terrain: it takes the first landscape with no
edit layers, which was safe for as long as nothing else made one, and test_landscape_layer_register
had started making one that same day. The suite was right; its fixture was somebody else's scratch.

Eleven more sites of the same class were then found BY HAND - a survey of all 176 suites, an
adversarial pass, two commits (8a626bc, a879158). That survey is the problem this file addresses.
It was a one-off. Suite 179 gets no survey, and the bug is invisible in the individual run its
author will do: test_landscape_heightmap passed alone and failed only on the SECOND pass of a sweep,
which is a 30-minute exclusive run nobody does while writing a suite.

THE RULE, and the fourth clause is the one that makes this usable rather than noise:

    a discovery call (find_assets / list_level_actors / landscape_info / list_partition_actors)
    not scoped to the caller's own scratch and not guarded,
    whose rows are reduced to an IDENTIFIER (path / actorPath / objectPath / name / label)
    rather than merely counted,
    FOR A CLASS SOME OTHER SUITE IN THIS DIRECTORY CREATES.

That last clause is the adversarial pass, mechanised. The hand survey's rejection criterion was
"name a suite that could create a matching object", and that is computable from the tree: every
create_/import_/duplicate_ call in every suite is read for the class it produces. Adopting a
SoundWave is safe because nothing here makes one; adopting a Skeleton is not, because two suites do.
Without this clause the rule flags 85 sites across 52 unsurveyed suites and is worth nobody's time.

Extracting an identifier is what separates adopting from counting. A suite that only wants a number
never reaches for a row's path; a suite that adopts always does.

IT REPORTS A READING LIST. Plenty of unscoped discovery is still correct - confirming an asset the
suite just created, reading project content it never measures against. What this cannot know is
whether the object affects the verdict. What it CAN say is which sites have never been asked.

WRITES is marked separately, because the two harms differ in size. Reading somebody's scratch gives
you a wrong measurement (heightmap). WRITING to it mutates another suite's fixture mid-run and the
failure lands in that suite, later, looking like a defect in unrelated code.

MEASURED AGAINST GROUND TRUTH, not asserted. The eight UE suites fixed on 2026-09-02 are known, and
so is the tree before them. --ground-truth re-runs the rule against 8a626bc^ and scores recall.
Do not change the rule without re-running it.

  python tools/audit_fixture_adoption.py                 the reading list
  python tools/audit_fixture_adoption.py --plant         prove it sees a known adoption
  python tools/audit_fixture_adoption.py --ground-truth  score it against the 8 known fixes
  python tools/audit_fixture_adoption.py --classes       the collision map it derived, to audit it
  python tools/audit_fixture_adoption.py --all           include the sites it cleared, and why
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Endpoints that hand back a list of objects THE PROJECT ALREADY CONTAINS - the only ones from which
# a suite can adopt. Deliberately not every list_* endpoint: list_bones and list_graphs enumerate the
# insides of an asset the caller already named, so there is no choice of fixture left to get wrong.
DISCOVERY = ("find_assets", "list_level_actors", "landscape_info", "list_partition_actors")

# The helpers whose presence means the question HAS been asked.
GUARDS = ("is_scratch_fixture", "pick_adoptable")

# READ AND CLEARED, written next to the site as `# ADOPTION-OK: <why>`.
#
# WHY A SUPPRESSION EXISTS AT ALL, given how easily one becomes a way to hide things. Some sites are
# self-correcting BY CONSTRUCTION and no filter would improve them: test_niagara_user_params only
# accepts a system whose user-parameter count is above zero, and a freshly minted scratch system has
# none, so it cannot be adopted however hard the registry tries. Reporting those every run is how a
# reading list stops being read, and "already fine" is a legitimate answer to give in writing.
#
# THE REASON IS MANDATORY - a bare marker does not suppress - and the count of suppressed sites is
# printed on every run whether or not anything is flagged, so they cannot quietly accumulate.
CLEARED = re.compile(r'#\s*ADOPTION-OK:\s*(\S.*)')

# HAND-ROLLED SCRATCH FILTERS COUNT AS GUARDS. test_anim_curve writes
# `if not a["path"].startswith("/Game/_Mif")` inline, which is the same rule spelled differently and
# was in the tree before the helper existed. Reporting it as unguarded would be false, and reporting
# it as fine loses a real observation - so it is cleared here and listed under --all, where the
# parallel implementation is visible to anyone consolidating them.
#
# THREE SPELLINGS, FOUND BY READING THE FALSE POSITIVES. Besides the literal prefix there is
# `p.startswith(SC.SCRATCH_PREFIXES)` - scratch_confirm's own tuple, used by test_set_struct_member
# and test_node_spawns. Missing it reported two suites that already do exactly the right thing.
HANDROLLED = re.compile(r'_Mif[A-Za-z]*"\s*\)|startswith\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\.)?SCRATCH'
                        r'|"_Mif"\s+(?:not\s+)?in')

# Reaching for one of these is what makes a row a FIXTURE rather than a number.
IDENT = re.compile(r'\.get\(\s*"(path|actorPath|objectPath|name|label)"|'
                   r'\[\s*"(path|actorPath|objectPath|name|label)"\s*\]')

# Scratch scoping, in the ARGUMENT text only - a suite hunting its own leftovers is doing the right
# thing. Bare identifiers are resolved against the file's own assignments before this is applied.
#
# BOTH SPELLINGS, because mifaudit uses two: assets live under SCRATCH_ASSET_PREFIX "/Game/_Mif" and
# level actors carry SCRATCH_LABEL_PREFIX "Mif" with no underscore. Matching only the underscored
# form flagged test_confirm_gated's `{"nameContains": "MifGuardProbe"}` - a call scoped to its own
# probe by name, which is precisely the behaviour this file asks for.
SCRATCH_SCOPED = re.compile(r'_Mif|"Mif[A-Z]|SCRATCH|scratch')

# EXACT-IDENTITY LOOKUP, NOT ADOPTION. test_bulk_rename's exists() enumerates a folder and compares
# each row to one path it was given; nothing is being chosen, so there is no wrong choice to make.
#
# THE COMPARISON VARIABLE IS NOT ALWAYS CALLED `path`. A fixed vocabulary of names missed
# test_confirm_gated's `a.get("actorPath") == probe`, so what is matched is the SHAPE: a row's own
# identifier on one side of an ==. Nothing is being chosen when the answer has to equal one thing.
_ROW_ID = (r'(?:\.get\(\s*"(?:path|actorPath|objectPath|name|label)"\s*\)'
           r'|\[\s*"(?:path|actorPath|objectPath|name|label)"\s*\])')
#
# startswith AGAINST A VARIABLE IS THE SAME QUESTION. test_duplicate_cooked_guard writes
# `any((a.get("path") or "").startswith(dst) for a in found)` - it is asking whether one specific
# destination came back, not choosing among what did. Against a LITERAL it would be a scoping filter
# rather than an identity test, which is why the argument must be an identifier.
IDENTITY = re.compile(_ROW_ID + r'[^\n!<>=]*==|==[^\n]*' + _ROW_ID + r'|'
                      + _ROW_ID + r'[^\n]{0,30}\.startswith\(\s*[A-Za-z_]')

# AN IDENTITY LOOKUP WITH A FIRST-ROW FALLBACK IS STILL ADOPTION, and this is not hypothetical:
# test_spline_landscape asks for its own landscape by actorPath and then, if that finds nothing,
# takes `(info.get("landscapes") or [{}])[0]` - whatever is first. It was one of the eight suites
# fixed by hand and this site was left behind, so treating the comparison as proof cleared a live
# instance of the exact bug. The comparison says what the suite WANTS; the fallback says what it
# accepts.
FALLBACK = re.compile(r'or\s*\[\s*(?:\{\s*\}\s*)?\]\s*\)\s*\[')

# EVERY WAY A SUITE REACHES THE BRIDGE, not just M.call. test_virtual_bone_authoring duplicates its
# scratch Skeleton with M.raw_post, and reading only M.call left Skeleton out of the collision map
# entirely - which cleared test_ported_anim, one of the eight sites this rule exists to catch.
POST = r'(?:M\.(?:call|raw_post)|confirm_call)\(\s*"'

WRITE_CALL = re.compile(POST + r'(set_|add_|delete_|create_|rename_|import_|reimport_|'
                        r'save_|apply_|remove_|assign_|connect_|compile|duplicate_)')

CALL_SITE = re.compile(POST + r'(' + "|".join(DISCOVERY) + r')"\s*,')
CLASS_ARG = re.compile(r'"(?:class|classFilter|classNames|type|assetClass)"\s*:\s*"([A-Za-z0-9_]+)"')

# Creators whose product class is not in their arguments. Read off the handlers, not guessed.
IMPLIED_CLASS = {
    "create_blueprint": "Blueprint",
    "create_material": "Material",
    "create_material_instance": "MaterialInstanceConstant",
    "create_procedural_mesh": "StaticMesh",
    "create_mesh_boolean": "StaticMesh",
    "create_landscape": "Landscape",
    "create_water_zone": "WaterZone",
    "create_datatable": "DataTable",
    "create_widget_blueprint": "WidgetBlueprint",
    "import_texture": "Texture2D",
    "add_niagara_emitter": "NiagaraSystem",
}
CREATOR = re.compile(POST + r'((?:create|import|duplicate)_[a-z_]+)"\s*,')

WINDOW_BEFORE = 3   # comprehensions put the identifier expression ABOVE the call
WINDOW_AFTER = 10


def args_text(text, start):
    """The call's argument dict, by brace matching from the first { after the endpoint name.

    Regex cannot do this: a one-line dict and a dict built over four lines have to read the same, and
    a lazy `\\{[^}]*\\}` stops at the first nested close.
    """
    i = text.find("{", start)
    if i < 0 or i > start + 200:
        return ""
    depth, j = 0, i
    while j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
        j += 1
    return text[i:i + 400]


def resolve_scoping(args, src):
    """Is this call scoped to scratch, including through a variable?

    `{"pathPrefix": ROOT}` where ROOT = "/Game/_MifDT" is scratch-scoped and reading only the literal
    argument text says it is not. Resolves a bare identifier against assignments in the same file -
    which covers the constant-at-the-top form every suite here uses.
    """
    # ORIGIN CONTAINER IS A SCRATCH EXCLUSION, and it is one by definition rather than by
    # convention. MifBridgeAssetOps.cpp:1690: "a package name the registry knows but which has no
    # loose file is container content." An asset a suite creates in-session is a loose package or
    # not on disk at all, so it can never come back from this filter. test_consolidate asks for
    # container-origin Materials on purpose and was reported as an unguarded adopter for it.
    if re.search(r'"origin"\s*:\s*"container"', args):
        return True
    if SCRATCH_SCOPED.search(args):
        return True
    # TWO LEVELS, because one was not enough. test_duplicate_cooked_guard confirms its own duplicate
    # with `folder = dst.rsplit("/", 1)[0]` and then searches that folder - so the scratch literal is
    # in `dst`, one hop further than the argument. Stopping at the first hop reported a suite
    # checking its OWN destination as an unguarded adoption of any class in the project.
    seen, pending = set(), list(
        re.findall(r'"(?:pathPrefix|folder|path)"\s*:\s*([A-Za-z_][A-Za-z0-9_]*)', args))
    for _ in range(2):
        nxt = []
        for name in pending:
            if name in seen:
                continue
            seen.add(name)
            for val in re.findall(r'^\s*%s\s*=\s*[^\n]*' % re.escape(name), src, re.M):
                if SCRATCH_SCOPED.search(val):
                    return True
                nxt += re.findall(r'\b([A-Za-z_][A-Za-z0-9_]*)\b', val.split("=", 1)[1])
        pending = nxt
    return False


def window_around(text, start):
    """WINDOW_BEFORE lines above through WINDOW_AFTER lines below - a guard can sit on either side."""
    head = start
    for _ in range(WINDOW_BEFORE):
        prev = text.rfind("\n", 0, head - 1)
        if prev < 0:
            head = 0
            break
        head = prev
    end = start
    for _ in range(WINDOW_AFTER + 1):
        nxt = text.find("\n", end)
        if nxt < 0:
            end = len(text)
            break
        end = nxt + 1
    # A SITE OWNS ONLY ITS OWN REGION. test_load_partition_actors takes an unfiltered listing purely
    # to compare `matched` counts, and eight lines later a DIFFERENT list_partition_actors call reads
    # labels off its rows. A fixed line window handed the second call's identifier to the first and
    # reported a count-only site as an adoption. Stop at the next discovery call.
    nxt_site = CALL_SITE.search(text, start + 1)
    if nxt_site and nxt_site.start() < end:
        end = nxt_site.start()
    return text[head:end]


ASSIGNED = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*[\(\[]?\s*$')


def names_a_row(src, start, win):
    """Does THIS call's result get reduced to an identifier, or just a nearby call's?

    PROXIMITY IS NOT DATAFLOW, and the difference is not academic. test_partition_actors takes a
    loadedOnly listing purely to compare `matched` against a number, and four lines later reads a
    label off rows belonging to an EARLIER call to build a nameContains fragment. A line window
    hands that label to the wrong site and reports a count-only call as an adoption.

    Three shapes, because that is what the suites actually write:

      chained inline   (M.call(...).get("assets") or [{}])[0].get("path")   - the identifier is
                       downstream of this call and nothing else, so proximity is right.
      comprehension    rows = [a.get("path") for a in (M.call(...)...)]     - the identifier sits
                       ABOVE the call on the same logical line. Also unambiguous.
      assigned         lo = M.call(...)                                     - the result has a NAME,
                       and only reads that mention that name belong to it.
    """
    line_start = src.rfind("\n", 0, start) + 1
    head = src[line_start:start]
    if IDENT.search(head):                    # comprehension over this call's rows
        return True
    am = ASSIGNED.search(head)
    if not am:                                # chained inline - nothing else can own it
        return bool(IDENT.search(win))
    var = am.group(1)
    for hit in IDENT.finditer(win):
        near = win[max(0, hit.start() - 90):hit.end() + 40]
        if re.search(r'\b%s\b' % re.escape(var), near):
            return True
    return False


def created_classes(directory):
    """{class name: {suites that create one}} - the collision map, read off the suite sources.

    This IS the adversarial pass: "name a suite that could create a matching object". A class nothing
    here creates cannot be collided with, so adopting one is safe no matter how unguarded the site.
    """
    out = {}
    for name in sorted(os.listdir(directory)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        src = io.open(os.path.join(directory, name), encoding="utf-8", errors="replace").read()
        for m in CREATOR.finditer(src):
            endpoint = m.group(1)
            found = set(CLASS_ARG.findall(args_text(src, m.end())))
            if endpoint in IMPLIED_CLASS:
                found.add(IMPLIED_CLASS[endpoint])
            # A DUPLICATE'S CLASS IS THE SOURCE'S, AND THE SOURCE IS NOT IN THE ARGUMENTS - it takes
            # path and newPath. But a suite that duplicates has to FIND its source first, so the
            # class it names anywhere in its own file is the class it copies. Bounded in practice:
            # five suites in the whole directory duplicate anything.
            if endpoint.startswith("duplicate_"):
                found |= set(CLASS_ARG.findall(src))
            for cls in found:
                out.setdefault(cls, set()).add(name)
    # Landscapes are spawned, not "created" as assets, and the level-actor discovery endpoints see
    # them - so the endpoint that makes one has to be in the map under the name a caller filters on.
    for name in sorted(os.listdir(directory)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        src = io.open(os.path.join(directory, name), encoding="utf-8", errors="replace").read()
        if 'create_landscape"' in src:
            out.setdefault("Landscape", set()).add(name)
        if 'spawn_actor_in_level"' in src:
            out.setdefault("*actor*", set()).add(name)
    return out


def scan_file(path, collisions, include_cleared=False):
    src = io.open(path, encoding="utf-8", errors="replace").read()
    me = os.path.basename(path)
    hits = []
    for m in CALL_SITE.finditer(src):
        endpoint = m.group(1)
        args = args_text(src, m.end())
        win = window_around(src, m.start())
        line = src.count("\n", 0, m.start()) + 1

        def cleared(why):
            if include_cleared:
                hits.append((line, endpoint, "OK  " + why, False, ""))

        if any(g in win for g in GUARDS):
            cleared("guarded by mifaudit")
            continue
        ok = CLEARED.search(win)
        if ok:
            cleared("ADOPTION-OK: %s" % ok.group(1).strip()[:70])
            continue
        if HANDROLLED.search(win):
            cleared("hand-rolled scratch filter (should use mifaudit.is_scratch_fixture)")
            continue
        if resolve_scoping(args, src):
            cleared("scoped to scratch")
            continue
        if not names_a_row(src, m.start(), win):
            cleared("counts only, never names a row")
            continue
        if IDENTITY.search(win) and not FALLBACK.search(win):
            cleared("exact-identity lookup, not a choice of fixture")
            continue

        # WHICH CLASSES COULD COLLIDE HERE. A discovery call naming no class sees everything, so any
        # creator at all is a collision; one naming a class collides only with creators of that class.
        wanted = set(CLASS_ARG.findall(args))
        if endpoint in ("list_level_actors", "landscape_info", "list_partition_actors"):
            wanted = wanted or {"Landscape" if endpoint == "landscape_info" else "*actor*"}
        if wanted:
            makers = set()
            for cls in wanted:
                makers |= collisions.get(cls, set())
        else:
            makers = set().union(*collisions.values()) if collisions else set()
        makers.discard(me)
        if not makers:
            cleared("no other suite creates %s" % (", ".join(sorted(wanted)) or "anything"))
            continue

        who = sorted(makers)
        hits.append((line, endpoint, "ADOPTS %s" % (", ".join(sorted(wanted)) or "any class"),
                     bool(WRITE_CALL.search(win)),
                     "%d suite(s) create one: %s%s" % (len(who), ", ".join(w[5:-3] for w in who[:3]),
                                                       ", ..." if len(who) > 3 else "")))
    return hits


def report(directory, include_cleared=False, quiet=False):
    collisions = created_classes(directory)
    found = {}
    names = sorted(p for p in os.listdir(directory)
                   if p.startswith("test_") and p.endswith(".py"))
    for name in names:
        hits = [h for h in scan_file(os.path.join(directory, name), collisions, include_cleared)
                if include_cleared or h[2].startswith("ADOPTS")]
        if hits:
            found[name] = hits
    if quiet:
        return {k: v for k, v in found.items() if any(h[2].startswith("ADOPTS") for h in v)}
    flagged = sum(1 for v in found.values() for h in v if h[2].startswith("ADOPTS"))
    writes = sum(1 for v in found.values() for h in v if h[2].startswith("ADOPTS") and h[3])
    # THE SUPPRESSION COUNT IS PRINTED EVEN WHEN IT IS ZERO, and even on a clean run. A suppression
    # nobody can see is how a reading list becomes a list of what somebody once felt like reading.
    suppressed = []
    for name in names:
        src = io.open(os.path.join(directory, name), encoding="utf-8", errors="replace").read()
        suppressed += [(name, m.group(1).strip()) for m in CLEARED.finditer(src)]
    print("%d suite(s) scanned; %d site(s) adopt a class another suite creates, %d beside a WRITE; "
          "%d marked ADOPTION-OK\n" % (len(names), flagged, writes, len(suppressed)))
    if suppressed and ("--all" in sys.argv or not flagged):
        print("READ AND CLEARED - suppressed by an explicit marker, with the reason given:")
        for name, why in suppressed:
            print("  %-34s %s" % (name, why[:78]))
        print("")
    if not flagged:
        print("OK  every discovery call is scratch-scoped, guarded, count-only, or names a class")
        print("    nothing else here creates.")
    else:
        print("A READING LIST. Unscoped discovery can still be correct - the adopted object may not")
        print("affect the verdict. What this says is that the question has not been asked here.\n")
    for name in sorted(found):
        print("  %s" % name)
        for line, endpoint, why, writes_here, note in found[name]:
            print("      :%-5d %-22s %s%s" % (line, endpoint, why,
                                              "   <- AND WRITES" if writes_here else ""))
            if note:
                print("             %s" % note)
    return {k: v for k, v in found.items() if any(h[2].startswith("ADOPTS") for h in v)}


# The eight UE suites fixed by 8a626bc and a879158 on 2026-09-02. The three Blender ones are not
# here: they discriminate by object name inside a live Blender scene, which this file does not read.
KNOWN_FIXED = ("test_landscape_heightmap.py", "test_material_graph.py", "test_material_params.py",
               "test_niagara_emitter.py", "test_ported_anim.py", "test_spline_landscape.py",
               "test_staticmesh_write_guard.py", "test_uncovered_reads8.py")


def ground_truth():
    """Score the rule against the tree BEFORE the fixes, where the answer is already known.

    A detector nobody has run against a known instance proves nothing - the house rule that has paid
    for itself more than once here. The pre-fix sources come out of git into a temp directory; the
    working tree is never touched.
    """
    tmp = tempfile.mkdtemp(prefix="mif_adopt_gt_")
    try:
        base = subprocess.check_output(["git", "rev-parse", "8a626bc^"], cwd=ROOT).decode().strip()
        listing = subprocess.check_output(
            ["git", "ls-tree", "--name-only", base + ":tools"], cwd=ROOT).decode()
        names = [n for n in listing.split("\n") if n.startswith("test_") and n.endswith(".py")]
        for n in names:
            blob = subprocess.check_output(["git", "show", "%s:tools/%s" % (base, n)], cwd=ROOT)
            with open(os.path.join(tmp, n), "wb") as fh:
                fh.write(blob)
        print("GROUND TRUTH against %s (the tree before 8a626bc), %d suites\n"
              % (base[:9], len(names)))
        before = report(tmp, quiet=True)
        caught = [n for n in KNOWN_FIXED if n in before]
        missed = [n for n in KNOWN_FIXED if n not in before]
        extra = sorted(n for n in before if n not in KNOWN_FIXED)
        print("  RECALL   %d of %d known-fixed suites flagged" % (len(caught), len(KNOWN_FIXED)))
        for n in missed:
            print("      MISSED  %s" % n)
        print("  OTHERS   %d suite(s) flagged that the hand survey did not fix" % len(extra))
        for n in extra:
            print("      %s" % n)
        after = report(HERE, quiet=True)
        still = [n for n in KNOWN_FIXED if n in after]
        print("\n  ON THE CURRENT TREE the fixes must read as fixed: %d of %d still flagged%s"
              % (len(still), len(KNOWN_FIXED), ("  <- " + ", ".join(still)) if still else ""))
        return 0 if len(caught) == len(KNOWN_FIXED) and not still else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def plant():
    """A planted adoption, in a temp directory - never in tools/, which is the suite directory."""
    tmp = tempfile.mkdtemp(prefix="mif_adopt_plant_")
    try:
        # TWO FILES, because one is not enough to test the rule that matters. The victim adopts a
        # StaticMesh; the maker is what makes that unsafe. A plant with no maker SHOULD be cleared,
        # and if it is not, the collision clause is not doing anything.
        with io.open(os.path.join(tmp, "test_planted_maker.py"), "w",
                     encoding="utf-8", newline="\r\n") as fh:
            fh.write(u'import mifaudit as M\n\n\ndef main():\n'
                     u'    M.call("create_procedural_mesh", {"path": "/Game/_MifP/SM_x"})\n')
        victim = os.path.join(tmp, "test_planted_adoption.py")
        with io.open(victim, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write(u'import mifaudit as M\n\n\ndef main():\n'
                     u'    rows = M.call("find_assets", {"class": "StaticMesh",\n'
                     u'                                  "pathPrefix": "/Game/"}).get("assets")\n'
                     u'    target = rows[0].get("path")\n'
                     u'    M.call("set_property", {"path": target, "name": "x", "value": 1})\n')
        hits = [h for h in scan_file(victim, created_classes(tmp)) if h[2].startswith("ADOPTS")]
        seen = bool(hits) and hits[0][3]
        # And the same victim with nothing creating a StaticMesh must come back clean.
        os.remove(os.path.join(tmp, "test_planted_maker.py"))
        alone = [h for h in scan_file(victim, created_classes(tmp)) if h[2].startswith("ADOPTS")]
        print("PLANT  adoption seen=%s  marked as a WRITE=%s  cleared when nothing creates one=%s"
              % (bool(hits), bool(hits) and hits[0][3], not alone))
        ok = seen and not alone
        print("\n%s" % ("PLANT SEEN FOR THE RIGHT REASON - a clean run is worth something" if ok
                        else "PLANT NOT SEEN AS MINE - a clean run would mean NOTHING"))
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    if "--plant" in sys.argv:
        return plant()
    if "--ground-truth" in sys.argv:
        return ground_truth()
    if "--classes" in sys.argv:
        cm = created_classes(HERE)
        print("the collision map, derived from every create_/import_/duplicate_ call in tools/\n")
        for cls in sorted(cm):
            who = sorted(cm[cls])
            print("  %-28s %2d  %s%s" % (cls, len(who), ", ".join(w[5:-3] for w in who[:4]),
                                         ", ..." if len(who) > 4 else ""))
        return 0
    report(HERE, include_cleared="--all" in sys.argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
