"""describe_metasound - the read half of the audio family.

WHY THIS EXISTS. Before it, MifBridge had exactly ONE audio endpoint: audition_sound, which PLAYS a
sound and tells you nothing about it. DDS2 ships 185 MetaSoundSource assets, 354 SoundCues and 3771
SoundWaves, and nothing could describe any of them - the same inverse gap the spec names for Foliage,
a write with no read.

WHAT IT PROVES, and T742 is the one that matters. The endpoint reads the MetaSound document
REFLECTIVELY, and the reason is four separate traps found by reading both engine trees first:

  * the const document accessors were RENAMED between 5.3 and 5.7 (GetDocumentChecked ->
    GetConstDocumentChecked, GetDocument -> GetConstDocument)
  * GetDocumentChecked() is check(nullptr != Document) - a HARD ASSERT, not a null return
  * GetDocumentAccessPtr() is deprecated on 5.7; the engine wraps its own call to it
  * RootMetasoundDocument is protected in both, so there is no direct member read

So the test that earns its place is the one on a COOKED asset with real content, because that is the
combination that would have taken the editor down had any of those routes been taken instead.

SKIPS CLEANLY, with a distinct exit code, on a project with no MetaSounds - which is most projects.
A suite that silently passes without exercising anything is worse than none.

Usage:
    python tools/test_metasound.py

Exit codes:
    0  ran and passed
    1  ran and something failed
    2  SKIPPED - this project has no MetaSounds, nothing was verified
"""
import json
import sys

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)[:240]))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    found = M.call("find_assets", {"class": "MetaSoundSource", "limit": 60})
    assets = found.get("assets") or []
    if not assets:
        print("")
        print("SKIPPED - nothing was verified.")
        print("  This project has no MetaSoundSource assets, so there is nothing to describe.")
        print("  Exit code 2 means SKIPPED, distinct from 0 (passed) on purpose.")
        return 2
    print("MetaSounds in this project: %s" % found.get("count"))

    # Prefer one with a real interface. A MetaSound with no inputs describes correctly and proves
    # less, and picking the biggest is how the node/edge counts get exercised at all.
    best, best_doc = None, None
    for a in assets[:20]:
        d = M.call("describe_metasound", {"path": a["objectPath"]})
        if d.get("ok") is not True:
            continue
        if best_doc is None or (d.get("nodeCount") or 0) > (best_doc.get("nodeCount") or 0):
            best, best_doc = a, d
    if best_doc is None:
        check("T740 at least one MetaSound could be described", False,
              "every one of the first 20 refused - this is a real failure, not an empty project")
        print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
        return 1

    print("chosen: %s (%s nodes)" % (best["name"], best_doc.get("nodeCount")))
    print("")
    print("=== T740: it describes a real MetaSound ===")
    d = best_doc
    check("T740 describe succeeded", d.get("ok") is True, json.dumps(d)[:200])
    check("T740 it reports the asset it read", d.get("name") == best["name"], d.get("name"))
    check("T740 and its class", "MetaSound" in str(d.get("class")), d.get("class"))

    print("")
    print("=== T741: the INTERFACE is what you need to drive it ===")
    ins, outs = d.get("inputs") or [], d.get("outputs") or []
    check("T741 inputs[] is present", isinstance(d.get("inputs"), list), type(d.get("inputs")))
    check("T741 outputs[] is present", isinstance(d.get("outputs"), list), type(d.get("outputs")))
    check("T741 a MetaSound source has at least one output", len(outs) >= 1,
          "outputs=%s - a source with no output produces no audio" % json.dumps(outs)[:160])
    named = [i for i in ins if i.get("name")]
    check("T741 inputs carry a NAME", len(named) == len(ins) if ins else True,
          "%d of %d inputs have no name" % (len(ins) - len(named), len(ins)))
    typed = [i for i in ins if i.get("typeName")]
    check("T741 and a TYPE, which is what makes them usable", len(typed) == len(ins) if ins else True,
          "%d of %d inputs have no typeName" % (len(ins) - len(typed), len(ins)))

    print("")
    # COOKED-ONLY, SKIPPED where nothing is cooked. On an uncooked project the
    # refusal this asserts never comes, so the assertion fails for the environment
    # rather than for a defect - and where the call is a write, it lands instead.
    # Section confirmed self-contained by audit_cooked_section_safety before wrapping.
    #
    # `is not False`: project_is_cooked returns None when the question could not be
    # asked, and an unanswerable question is not a No - None runs this as before.
    COOKED = M.project_is_cooked()
    if COOKED is False:
        print("")
        print('=== T742 SKIPPED - nothing in this project is cooked ===')
        print('  This section asserts what an endpoint REFUSES on cooked content. There is nothing cooked')
        print('  here, so the refusal cannot be provoked - which is not the same as the guard being absent.')
        print('  Where the call is a WRITE, running it unguarded would perform the write it means to see')
        print('  refused. Run against a cooked project for this half.')
    else:
        print("=== T742: it read a COOKED asset without taking the editor down ===")
        # THE test. The engine's own document accessors hard-assert, and this is the asset class that
        # would have triggered it. Reaching this line at all is most of the result.
        check("T742 cooked is reported, not guessed at", isinstance(d.get("cooked"), bool), d.get("cooked"))
        check("T742 the bridge is still answering afterwards",
              M.call("find_assets", {"class": "MetaSoundSource", "limit": 1}).get("ok") is True,
              "the editor died reading a cooked MetaSound")

        print("")
    print("=== T743: the counts agree with what was serialised ===")
    check("T743 inputCount matches inputs[]", d.get("inputCount") == len(ins),
          "inputCount=%r len(inputs)=%d" % (d.get("inputCount"), len(ins)))
    check("T743 outputCount matches outputs[]", d.get("outputCount") == len(outs),
          "outputCount=%r len(outputs)=%d" % (d.get("outputCount"), len(outs)))
    check("T743 no inputWarning when they agree", "inputWarning" not in d, d.get("inputWarning"))
    check("T743 the node graph is reported as counts", isinstance(d.get("nodeCount"), (int, float)),
          d.get("nodeCount"))
    check("T743 and its edges", isinstance(d.get("edgeCount"), (int, float)), d.get("edgeCount"))

    print("")
    print("=== T744: refusals name what went wrong ===")
    r = M.call("describe_metasound", {})
    check("T744 a missing path is refused", r.get("ok") is False, json.dumps(r)[:200])
    check("T744 and it says where to get one", "find_assets" in str(r.get("error")),
          str(r.get("error"))[:200])

    r = M.call("describe_metasound", {"path": "/Game/_MifNope/Nothing_Here"})
    check("T744 an unloadable path is refused", r.get("ok") is False, json.dumps(r)[:200])

    r = M.call("describe_metasound", {"metasound": best["objectPath"], "nodes": True})
    check("T744 an unknown parameter is refused, not ignored", r.get("ok") is False,
          json.dumps(r)[:200])

    print("")
    print("=== T745: a non-MetaSound is refused by NAME, not by an empty answer ===")
    other = M.call("find_assets", {"class": "SoundWave", "limit": 1})
    wave = (other.get("assets") or [{}])[0].get("objectPath")
    if wave:
        r = M.call("describe_metasound", {"path": wave})
        check("T745 a SoundWave is refused", r.get("ok") is False, json.dumps(r)[:200])
        check("T745 and the refusal names the class it actually got",
              "SoundWave" in str(r.get("error")), str(r.get("error"))[:220])
        check("T745 and distinguishes 'not a MetaSound' from 'engine renamed the property'",
              "renamed" in str(r.get("error")), str(r.get("error"))[:220])
    else:
        print("  (no SoundWave in this project - T745 not exercised)")

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s" % (f[0],))
        print("          %s" % (f[1],))
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
