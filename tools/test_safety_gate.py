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
    check("T631 the default mode is not 'full'", mode == "scratch" or mode == "read",
          "writeMode is %r. Either MIF_BRIDGE_WRITE_MODE is set in this environment, the default is "
          "wrong, or this is an OLD BUILD with no gate at all. NOT attempting the destructive calls "
          "below in any of those cases." % mode)
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
        return 1

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
