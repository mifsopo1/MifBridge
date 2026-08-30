"""The plugin-side safety gate - are the standing rules actually enforced now?

WHAT CHANGED. Until 2026-08-26 the rules "do not save assets, do not start PIE, keep scratch under
/Game/_Mif*" were enforced by the AGENT's discipline plus tools/scratch_confirm.py on the Python side.
Nothing in the C++ would refuse a save_package call. Andre named this the highest-value non-endpoint
gap for exactly that reason: it is the one place the design depended on good behaviour rather than
enforcing it. A different agent session, or somebody else running the bridge, was subject to no guard.

THE TRAP THIS SUITE EXISTS TO PIN. MifBridge already had three endpoint classifications, and the
obvious implementation is `if (!IsReadOnlyEndpoint(E)) refuse`. That is BACKWARDS. Those buckets are
about TRANSACTION policy, and the read-only set contains save_package and save_blueprint
(MifBridgeCommon.cpp:489), trigger_cook (:492), start_pie/stop_pie (:559), run_console (:567) and
build_navmesh (:598) - because they manage their own transactions, not because they are harmless. A
gate written against it would PERMIT every save and every PIE while refusing harmless edits: a safety
feature that protects nothing and blocks everything.

T632 is the assertion that would catch that inversion. It checks BOTH directions - the unsafe ops are
refused AND ordinary work still runs - because either half alone can pass while the gate is inverted.

WHAT THIS SUITE DOES NOT COVER, said plainly rather than implied: the scratch-PATH rule is not enforced
yet. This is the unsafe-operation half only. A write to a non-scratch path still succeeds, and pretending
otherwise here would be claiming coverage the plugin does not have.

SAFETY: this suite deliberately CALLS save_package and start_pie. That is safe precisely because the
gate refuses them - which is the thing under test. If the gate were broken, these calls would do real
damage, so T631 asserts the mode is not 'full' BEFORE any of them are attempted and bails out if it is.
"""
import json
import sys

import mifaudit as M

PASS, FAIL = [], []

# Operations that must be refused, split by WHICH LAYER can observe the refusal.
#
# mifaudit carries its own DENY list (mifaudit.py:42) and short-circuits those endpoints BEFORE the
# HTTP call, returning {"ok": false, "error": "denied by harness"}. That is defence in depth and worth
# having - but it means a suite calling save_package through M.call() can never see the C++ gate at
# all. It would assert refusedBy == "safety-gate" and fail, having proved nothing about the plugin.
#
# So the gate is exercised through the unsafe endpoints the harness does NOT intercept. The
# harness-blocked ones are checked separately, for the weaker property that SOMETHING refuses them.
GATE_REACHABLE = ("trigger_cook", "build_navmesh", "import_asset", "exec_console")
HARNESS_BLOCKED = ("save_package", "save_blueprint", "start_pie", "stop_pie", "run_console")


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ------------------------------------------------------------------ T630 self_audit reports it
    print("=== T630: the gate's state is discoverable before anything is attempted ===")
    a = M.call("self_audit", {}, timeout=180)
    check("T630 self_audit answers", a.get("ok") is True, json.dumps(a)[:160])
    check("T630 it reports writeMode", isinstance(a.get("writeMode"), str), json.dumps(a)[:200])
    check("T630 writeMode is one of the three real modes",
          a.get("writeMode") in ("read", "scratch", "full"), "got %r" % a.get("writeMode"))
    check("T630 it reports safetyGateActive as a bool",
          isinstance(a.get("safetyGateActive"), bool), json.dumps(a)[:200])
    # A caller planning a write needs to know BEFORE it tries, not from a refusal afterwards.
    check("T630 safetyGateActive agrees with writeMode",
          a.get("safetyGateActive") == (a.get("writeMode") != "full"),
          "writeMode=%r safetyGateActive=%r" % (a.get("writeMode"), a.get("safetyGateActive")))

    mode = a.get("writeMode")

    # ------------------------------------------------------------------ T631 the default
    print("")
    print("=== T631: the default is a GATED mode, not 'full' ===")
    # A gate that has to be switched on before it matters is off when it matters - the same weakness
    # that made MIF_DBG useless for the crash it was meant to catch.
    # THREE STATES, and until 2026-08-27 this collapsed them into one failure:
    #
    #   gated ('scratch'/'read')  - the default, and what the probes below need
    #   'full'                    - the gate EXISTS and is deliberately off. Since launch_editor can
    #                               now choose the mode, this is a legitimate, intended state and
    #                               reporting it as a failure is crying wolf.
    #   writeMode ABSENT          - an old DLL with no gate at all. Still a genuine failure, and the
    #                               dangerous one, because the probes below would run FOR REAL.
    #
    # The distinction is checkable: a build with a gate reports writeMode whatever the mode is. Only
    # a build without one omits the field.
    if mode == "full":
        print("  SKIP  the gate is present and deliberately OFF (writeMode 'full').")
        print("        Nothing to enforce, and the destructive probes below are NOT attempted.")
        print("        Relaunch with launch_editor(write_mode='scratch') to exercise the gate.")
    else:
        check("T631 the default mode is a GATED one", mode == "scratch" or mode == "read",
              "writeMode is %r. A build WITH a gate reports this field whatever the mode is, so an "
              "absent value means an OLD BUILD with no gate at all. NOT attempting the destructive "
              "calls below." % mode)
    # FAIL-SAFE, and it is the important line in this file. The probes below deliberately call
    # save_package and start_pie, which is safe ONLY because the gate refuses them. Against an older
    # DLL with no gate, writeMode is absent and `mode` is None - and a naive `mode != "full"` test
    # would sail straight past and issue those calls FOR REAL. Anything other than a known gated mode
    # bails out.
    if mode not in ("scratch", "read"):
        print("")
        print("  (bailing out before the destructive probes - without a confirmed gated mode they")
        print("   would be real calls to save_package, start_pie and trigger_cook.)")
        print("=" * 72)
        print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
        # EXIT 2 = SKIPPED, not 1 = FAILED. This bail-out is the suite working CORRECTLY: in full
        # mode there is no gate to test and the probes would be real destructive calls. Returning 1
        # made every full-mode sweep record a permanent red row with FAIL 0 beside it - a correct
        # refusal that reads as a broken suite, which is how people learn to ignore red rows.
        # Found by the first full sweep to include this suite, 2026-08-30. The runner already
        # understands 2 as SKIPPED and reports it separately.
        print("SKIPPED - the gate cannot be tested from '%s' mode; nothing was verified beyond "
              "the reads above." % mode)
        return 2

    # ------------------------------------------------------------------ T632 BOTH directions
    print("")
    print("=== T632 [the inversion guard]: unsafe refused AND ordinary work still runs ===")
    exercised = 0
    for ep in GATE_REACHABLE:
        if ep not in M.endpoint_names():
            continue
        r = M.call(ep, {}, timeout=120)
        if r.get("_denied"):
            # The harness list grew to cover this one; it no longer proves anything about the C++.
            continue
        exercised += 1
        check("T632 %s is refused" % ep, r.get("ok") is False, "IT RAN. %s" % json.dumps(r)[:150])
        # THE assertion. refusedBy proves the refusal came from the PLUGIN, not from the harness or
        # from the handler happening to fail for its own reasons.
        check("T632 %s was refused by the plugin gate" % ep, r.get("refusedBy") == "safety-gate",
              json.dumps(r)[:190])
        # A refusal a caller cannot act on is half an answer.
        check("T632 %s says how to unlock" % ep, "MIF_BRIDGE_WRITE_MODE" in str(r.get("unlock") or ""),
              json.dumps(r)[:190])
    # If the harness DENY list ever grows to cover all four, this suite would silently stop testing the
    # gate while still reporting green. Assert that at least one actually reached the plugin.
    check("T632 at least one unsafe endpoint actually reached the C++ gate", exercised > 0,
          "every candidate was short-circuited by the harness - this suite proved nothing about the "
          "plugin. Widen GATE_REACHABLE with an unsafe endpoint mifaudit does not intercept.")

    print("")
    print("  --- and the harness-blocked ones are refused too, at whichever layer gets there first ---")
    for ep in HARNESS_BLOCKED:
        if ep not in M.endpoint_names():
            continue
        r = M.call(ep, {}, timeout=120)
        check("T632 %s is refused by something" % ep, r.get("ok") is False,
              "IT RAN - neither the harness nor the gate stopped it. %s" % json.dumps(r)[:150])

    print("")
    # THE other half. If only the refusals were asserted, an inverted gate that refuses EVERYTHING
    # would pass this suite - and the 66 existing suites would all be broken.
    print("  --- and ordinary work must still run (this is the half that catches an inverted gate) ---")
    # NOTE on the payloads: these must be VALID calls. An invalid parameter produces ok:false for a
    # reason that has nothing to do with the gate, and this assertion would then read that as an
    # inverted gate. list_blueprints takes no `limit` - it caps at 5000 and filters by substring -
    # and passing one here produced exactly that false alarm on the first run of this suite.
    for ep, payload in (("list_blueprints", {}),
                        ("self_audit", {}),
                        ("list_level_actors", {"limit": 1}),
                        ("describe_endpoint", {"name": "self_audit"})):
        if ep not in M.endpoint_names():
            continue
        r = M.call(ep, payload, timeout=180)
        # refusedBy distinguishes "the gate blocked it" from "the call was malformed", which is the
        # difference between a real inversion and a bad probe.
        check("T632 %s still runs" % ep, r.get("ok") is True,
              ("the GATE refused a harmless read - it is INVERTED. %s" if r.get("refusedBy") == "safety-gate"
               else "call failed for a non-gate reason (check the payload, not the gate): %s")
              % json.dumps(r)[:170])

    # ------------------------------------------------------------------ T633 not self-unlockable
    print("")
    print("=== T633 [the point of the design]: the gate cannot unlock itself ===")
    # set_cvar is a registered endpoint. If the mode lived in a console variable, the agent being
    # gated could switch it off - the gate would be decorative. It is an environment variable read
    # once at startup for exactly this reason.
    check("T633 there is no set_write_mode endpoint",
          "set_write_mode" not in M.endpoint_names(),
          "a settable mode is unlockable by the agent it is meant to gate")
    if "set_cvar" in M.endpoint_names():
        before = M.call("self_audit", {}, timeout=180).get("writeMode")
        M.call("set_cvar", {"name": "mif.BridgeWriteMode", "value": "full"}, timeout=90)
        after = M.call("self_audit", {}, timeout=180).get("writeMode")
        check("T633 set_cvar cannot change the write mode", before == after,
              "mode moved %r -> %r via set_cvar - the gate is self-unlockable" % (before, after))

    # ------------------------------------------------------------------ T636 derive, do not trust
    # Three endpoints reach UEngine::Exec through MifBridge::RunEngineExec, and for a while only two
    # were gated: run_console_captured executed anything while run_console was refused.
    #
    # The list was maintained BY HAND and the family grew a member. So this test does not hardcode
    # the three names - it reads the SOURCE, finds every handler that calls RunEngineExec, and
    # asserts each one is refused. A fourth Exec endpoint added next year fails this test the day it
    # is written, without anyone remembering to update a list.
    print("")
    print("=== T636: EVERY endpoint that reaches UEngine::Exec is gated ===")
    import io, os, re
    PRIV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "Source", "MifBridge", "Private")
    exec_eps = set()
    for fn in os.listdir(PRIV):
        if not fn.endswith(".cpp"):
            continue
        raw = io.open(os.path.join(PRIV, fn), encoding="utf-8", errors="replace").read()
        lines = raw.replace(chr(13) + chr(10), chr(10)).split(chr(10))
        cur = None
        for line in lines:
            m = re.match(r"\s*void (H_[a-z_0-9]+)\(", line)
            if m:
                cur = m.group(1)
            stripped = line.strip()
            if ("RunEngineExec(" in line and cur and not stripped.startswith("//")
                    and "bool RunEngineExec" not in line):
                exec_eps.add(cur[2:])          # strip the H_ prefix
    check("T636 the source scan found the Exec endpoints", len(exec_eps) >= 2, sorted(exec_eps))
    print("    endpoints reaching UEngine::Exec: %s" % sorted(exec_eps))

    mode = (M.call("self_audit", {}, timeout=180) or {}).get("writeMode")
    if mode == "full":
        print("  SKIP  gate is in 'full' mode - nothing to enforce")
    else:
        for ep in sorted(exec_eps):
            r = M.call(ep, {"command": "stat none"})
            # mifaudit has its OWN deny list and it shadows the gate: run_console never reaches the
            # bridge from this harness, so the response says "denied by harness" and refusedBy is
            # absent. That is the test harness protecting the editor, not the gate failing - but it
            # means the gate is UNOBSERVABLE for those endpoints by a direct call.
            #
            # So ask through batch instead. batch is not on the harness deny list, it reaches the
            # bridge, and it hits the same RefuseIfGated - which is exactly the second dispatcher T634
            # exists to cover. Asking the question a second way is what makes the answer worth having.
            if "denied by harness" in (r.get("error") or ""):
                w = M.call("batch", {"ops": [{"op": ep, "command": "stat none"}]})
                inner = (w.get("results") or [{}])[0]
                check("T636 %s is gated (observed through batch)" % ep,
                      inner.get("refusedBy") == "safety-gate",
                      "the harness blocks it directly; through batch it gave refusedBy=%r - %s"
                      % (inner.get("refusedBy"), json.dumps(inner)[:120]))
            else:
                check("T636 %s is gated" % ep, r.get("refusedBy") == "safety-gate",
                      "ok=%s refusedBy=%r - it reaches UEngine::Exec and the gate did not stop it"
                      % (r.get("ok"), r.get("refusedBy")))
    # ------------------------------------------------------------------ T637 export path confinement
    # The gate confines file OUTPUT to the project directory in a gated mode. Three branches, and the
    # first version of this guard only ever exercised two of them.
    #
    # THE RELATIVE ONE IS THE POINT. export_asset resolves a relative file against its own export
    # root (inside the project), but the guard originally ran on the RAW request and resolved it
    # against the process CWD - the editor's binaries directory, outside the project. So "tile.fbx"
    # was refused for being outside the project it was about to be written into. The absolute-path
    # test passed the whole time.
    print("")
    print("=== T637: export output is confined to the project, on ALL three path shapes ===")
    mode = (M.call("self_audit", {}, timeout=180) or {}).get("writeMode")
    if mode == "full":
        print("  SKIP  gate is in 'full' mode - output is deliberately unconfined")
    else:
        SPHERE = "/Engine/EngineMeshes/Sphere.Sphere"
        r = M.call("export_asset", {"asset": SPHERE, "file": "C:/Temp/mif_should_refuse.fbx"},
                   timeout=300)
        check("T637 an ABSOLUTE path outside the project is refused",
              r.get("ok") is False and r.get("refusedRule") == "file-outside-project",
              json.dumps(r)[:190])

        r = M.call("export_asset", {"asset": SPHERE, "file": "mif_relative_probe.fbx"}, timeout=300)
        check("T637 a RELATIVE path is ALLOWED - it resolves inside the export root",
              r.get("ok") is True, json.dumps(r)[:220])
        f = str(r.get("file") or "")
        check("T637 and it landed under the project, not the process CWD",
              "MifBridge" in f and "Saved" in f, "file=%s" % f)

        r = M.call("export_asset", {"asset": SPHERE}, timeout=300)
        check("T637 the DEFAULT path still works (the Blender round trip uses it)",
              r.get("ok") is True, json.dumps(r)[:190])

    # ------------------------------------------------------------------ T635 the UI side doors
    # The gate refuses save_package. Until 2026-08-26 it permitted send_editor_key, which delivers a
    # real key event to whatever has focus - and Ctrl+S in a level editor is Save. It also permitted
    # invoke_editor_command, which executes any registered FUICommandInfo including the Save command.
    #
    # Neither endpoint writes anything itself. That is exactly why they were missed: the unsafe list
    # was built by asking "does this mutate?" when the question is "can this REACH something that
    # mutates?".
    #
    # This test sends the harmless key deliberately. If it ever regresses it must fail by being
    # ALLOWED to send a key, not by actually saving something.
    print("")
    print("=== T635: the gate covers the endpoints that can REACH a save ===")
    mode = (M.call("self_audit", {}, timeout=180) or {}).get("writeMode")
    if mode == "full":
        print("  SKIP  gate is in 'full' mode - nothing to enforce")
    else:
        for ep, payload in (
                ("send_editor_key", {"key": "F13"}),
                ("invoke_editor_command", {"context": "LevelEditor", "command": "Save"}),
        ):
            r = M.call(ep, payload)
            check("T635 %s is refused in scratch mode" % ep, r.get("ok") is False,
                  "IT RAN. %s" % json.dumps(r)[:170])
            check("T635 %s is refused BY THE GATE" % ep,
                  r.get("refusedBy") == "safety-gate",
                  "refusedBy=%r - refused, but not by the gate" % r.get("refusedBy"))

        # And the same call wrapped in batch, since batch is the other dispatcher.
        w = M.call("batch", {"ops": [{"op": "send_editor_key", "key": "F13"}]})
        inner = (w.get("results") or [{}])[0]
        check("T635 and the side door stays shut through batch too",
              inner.get("ok") is False, json.dumps(w)[:200])

        # The OPEN-only UI endpoints must still work - diagnosis is the point of scratch mode.
        t = M.call("invoke_editor_tab", {"tabId": "ContentBrowserTab1"})
        check("T635 invoke_editor_tab still works (it opens UI, it cannot execute)",
              t.get("refusedBy") != "safety-gate",
              "the gate blocked a harmless tab open: %s" % json.dumps(t)[:150])
    # ------------------------------------------------------------------ T634 batch is a SECOND door
    # The gate is enforced in RunEndpoint. batch does NOT recurse through RunEndpoint - it dispatches
    # straight out of Handlers() - so until 2026-08-26 an unsafe endpoint was reachable simply by
    # wrapping it: save_package refused, {"op": "save_package"} inside a batch ran.
    #
    # batch takes an endpoint NAME as data, so this was not an obscure bypass. Any endpoint on the
    # unsafe list was one JSON object away: save_all, run_console, start_pie, load_level, quit_editor.
    #
    # DELIBERATELY uses save_package as the probe rather than something that ends the session. If this
    # test ever regresses, it must fail by SAVING something in scratch - which is bad and recoverable -
    # rather than by quitting the editor mid-suite.
    print("")
    print("=== T634: the gate holds through batch, not just through RunEndpoint ===")
    mode = (M.call("self_audit", {}, timeout=180) or {}).get("writeMode")
    if mode == "full":
        print("  SKIP  gate is in 'full' mode - nothing to enforce")
    else:
        direct = M.call("save_package", {"path": "/Game/_MifGate/NoSuchAsset_zz"})
        check("T634 save_package is refused directly", direct.get("ok") is False,
              json.dumps(direct)[:170])

        wrapped = M.call("batch", {"ops": [{"op": "save_package",
                                           "path": "/Game/_MifGate/NoSuchAsset_zz"}]})
        results = wrapped.get("results") or []
        inner = results[0] if results else {}
        # The OP must be refused. Whether the batch as a whole reports ok:false matters less than
        # that the op did not run, so assert the op and then the aggregate separately.
        check("T634 the same call wrapped in batch is ALSO refused",
              inner.get("ok") is False,
              "IT RAN INSIDE BATCH. %s" % json.dumps(wrapped)[:220])
        check("T634 and the refusal comes from the safety gate, not from something else",
              inner.get("refusedBy") == "safety-gate",
              "refusedBy=%r - refused, but not by the gate" % inner.get("refusedBy"))
        check("T634 the batch as a whole reports failure", wrapped.get("ok") is False,
              json.dumps(wrapped)[:170])

        # A refused op must not silently swallow the ops around it. This is the regression that
        # would turn a security fix into a correctness bug.
        mixed = M.call("batch", {"ops": [{"op": "self_audit"},
                                         {"op": "save_package", "path": "/Game/_MifGate/NoSuchAsset_zz"},
                                         {"op": "self_audit"}]})
        mres = mixed.get("results") or []
        check("T634 a refused op does not drop the ops around it", len(mres) == 3,
              "expected 3 results, got %d: %s" % (len(mres), json.dumps(mixed)[:200]))
        if len(mres) == 3:
            check("T634 the permitted ops still ran",
                  mres[0].get("ok") is True and mres[2].get("ok") is True,
                  json.dumps([mres[0].get("ok"), mres[2].get("ok")]))
            check("T634 and only the gated one was refused", mres[1].get("ok") is False,
                  json.dumps(mres[1])[:170])
    check("T633 the bridge is still answering", M.bridge_responsive() is True, "bridge died")

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
