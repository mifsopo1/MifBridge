"""Engine calls that open a MODAL DIALOG - the failure that looks exactly like a crash but is worse.

Every handler runs INLINE on the game thread inside the HTTP ticker (MifBridgeServer.cpp). An engine
call that opens a modal therefore does not "show a dialog" - it stops the ticker. The socket keeps
accepting, the editor window keeps pumping messages and reports Responding=True to Windows, and the
bridge answers nothing ever again until a human clicks the box. From the caller's side that is
indistinguishable from a crash, and it is worse than one, because a crash at least gets restarted.

WHAT THIS SUITE EXISTS FOR. set_variable_type hung the editor on an ordinary three-call sequence:
add a float variable, add a Get node, retype it to int. The engine opens a "Change Variable Type"
warning whenever the variable has ANY referencing node - in this blueprint or in a loaded CHILD
blueprint (BlueprintEditorUtils.cpp:5035, and :5605 for locals). Retyping a variable that has nodes
is the ONLY interesting case, so the modal was on the main path, not an edge.

The audit tool that covers this class (audit_modals.py) did not catch it, for a reason worth keeping
written down: it models the guard as TGuardValue<bool>(GIsRunningUnattendedScript, true), which is
what stops FMessageDialog::Open. FSuppressableWarningDialog does not go through FMessageDialog at
all - it calls GEditor->EditorAddModalWindow directly. Two different dialog classes, two different
guards, and only one of them was modelled.

The assertion in every test below is simply THAT THE CALL CAME BACK. A hang produces no error
response to inspect, so there is nothing subtler to assert: mifaudit raises Timeout, and the test
reports the hang rather than hanging the suite with it.
"""
import io
import json
import os
import sys
import time

import mifaudit as M
import scratch_confirm as SC

PASS, FAIL = [], []

# The caller's own config. The fix flips a suppression flag for the duration of one engine call and
# must put it back; Andre drives this same editor by hand and did not ask for his warnings turned off.
# Resolved from the editor rather than hardcoded - see ini_path(). The literal it used to be
# meant this suite silently checked nothing on any other machine: io.open would raise, the
# leak check would report "could not read" and the suite would carry on green.
INI = None


def ini_path():
    """<savedDir>/Config/WindowsEditor/EditorPerProjectUserSettings.ini, from the editor."""
    global INI
    if INI is None:
        saved = M.call("project_paths", {}).get("savedDir") or ""
        INI = os.path.join(saved, "Config", "WindowsEditor",
                           "EditorPerProjectUserSettings.ini") if saved else ""
    return INI
SUPPRESS_KEY = "ChangeVariableType_Warning"


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def call_must_return(label, endpoint, payload, timeout=45):
    """Call, and treat a timeout as the specific failure this suite is about.

    Returns (response_or_None, seconds). A hang is reported as a hang - not as a generic error - so
    the suite output names the actual failure mode instead of leaving it to be guessed.
    """
    t0 = time.time()
    try:
        r = M.call(endpoint, payload, timeout=timeout)
        return r, time.time() - t0
    except M.Timeout:
        check("%s came back at all" % label, False,
              "%s did not respond in %ds. That is the modal-dialog hang: the game thread is blocked "
              "in a dialog and the bridge is now dead until someone clicks it." % (endpoint, timeout))
        return None, time.time() - t0


def pin_types(graph, guid):
    n = M.call("get_node", {"graphId": graph, "nodeGuid": guid})
    node = n.get("node") or n
    out = []
    for p in (node.get("pins") or []):
        t = p.get("type") or {}
        if isinstance(t, dict) and t.get("category") in ("real", "int", "byte", "bool", "string"):
            out.append("%s:%s" % (p.get("name"), t.get("category")))
    return out


def suppress_key_present():
    """Is the suppression flag sitting in the user's config file?"""
    try:
        s = io.open(ini_path(), encoding="utf-8", errors="ignore").read()
    except Exception:
        return None            # cannot read it; reported rather than silently passed
    return SUPPRESS_KEY in s


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    # ---------------------------------------------------------------- T360 the hang itself
    print("\n=== T360: retyping a variable THAT HAS NODES must not hang the bridge ===")
    baseline = suppress_key_present()
    check("T360 the suppression flag is readable before the call", baseline is not None,
          "could not read %s - the leak check below cannot run" % ini_path())

    bid = M.call("create_blueprint", {"path": "/Game/_MifModal/BP_%d" % st,
                                      "parentClass": "Actor"}).get("blueprintId")
    check("T360 a blueprint exists to work on", bool(bid), "create_blueprint gave no blueprintId")
    if not bid:
        return 1
    M.call("add_variable", {"blueprintId": bid, "name": "Health", "type": "float"})
    graphs = M.call("list_graphs", {"blueprintId": bid}).get("graphs") or []
    g = next((x.get("graphId") for x in graphs if "EventGraph" in (x.get("name") or "")), None)
    gn = M.call("add_variable_get", {"graphId": g, "var": "Health", "x": 0, "y": 0})
    M.call("add_variable_set", {"graphId": g, "var": "Health", "x": 0, "y": 200})
    guid = gn.get("nodeGuid") or (gn.get("node") or {}).get("guid")

    # The precondition that arms the modal. Without a referencing node the engine never prompts, so a
    # test that skips this passes against the broken build - which is exactly what happened.
    check("T360 the variable really has a referencing node (this is what arms the dialog)",
          bool(guid) and bool(pin_types(g, guid)), "no Get node, so the modal path is not exercised")

    r, dt = call_must_return("T360", "set_variable_type",
                             {"blueprintId": bid, "name": "Health", "type": "int"})
    if r is not None:
        check("T360 set_variable_type came back at all", True)
        print("        (returned in %.2fs)" % dt)
        check("T360 and it reports success", r.get("ok") is True, json.dumps(r)[:200])
        check("T360 and the retype actually took", r.get("changed") is True
              and (r.get("typeAfter") or {}).get("category") == "int",
              json.dumps(r.get("typeAfter")))
        # The engine keeps the nodes and reconstructs them; if the pin did not follow, the graph is
        # now lying about the variable's type.
        check("T360 the Get node's pin followed the retype", "Health:int" in pin_types(g, guid),
              str(pin_types(g, guid)))
        c = M.call("compile", {"blueprintId": bid})
        check("T360 and the blueprint still compiles",
              c.get("ok") is True and c.get("numErrors", 1) == 0, "errors=%s" % c.get("numErrors"))
    check("T360 the bridge is still answering afterwards", M.bridge_responsive() is True,
          "the bridge stopped answering - a modal is still up")

    # ---------------------------------------------------------------- T361 the local-variable path
    print("\n=== T361: the same hazard on the LOCAL variable path (:5605) ===")
    fname = "Calc_%d" % st
    fn = M.call("create_function", {"blueprintId": bid, "name": fname})
    check("T361 a function exists to hold a local", fn.get("ok") is True, json.dumps(fn)[:170])
    M.call("compile", {"blueprintId": bid})       # ChangeLocalVariableType needs the generated function
    av = M.call("add_variable", {"blueprintId": bid, "name": "Temp", "type": "float",
                                 "scope": "local", "function": fname})
    check("T361 a local variable is added", av.get("ok") is True, json.dumps(av)[:170])
    fg = next((x.get("graphId") for x in (M.call("list_graphs", {"blueprintId": bid}).get("graphs") or [])
               if (x.get("name") or "") == fname), None)
    if fg:
        M.call("add_variable_get", {"graphId": fg, "var": "Temp", "x": 0, "y": 0})
    r, dt = call_must_return("T361", "set_variable_type",
                             {"blueprintId": bid, "name": "Temp", "type": "int",
                              "scope": "local", "function": fname})
    if r is not None:
        check("T361 the local retype came back at all", True)
        print("        (returned in %.2fs)" % dt)
        # ok:false is acceptable here - some local setups legitimately refuse. A HANG is not.
        check("T361 and it answered rather than hanging", isinstance(r.get("ok"), bool),
              json.dumps(r)[:200])
    check("T361 the bridge is still answering", M.bridge_responsive() is True,
          "the bridge stopped answering on the local path")

    # ---------------------------------------------------------------- T362 no config side effect
    print("\n=== T362: the fix must not leave the user's warning turned off ===")
    after = suppress_key_present()
    if baseline is None or after is None:
        check("T362 the config could be read to check for a leak", False, "could not read %s" % ini_path())
    else:
        # The flag is set for the duration of one engine call and restored. Ending a call with the
        # user's "warn me before retyping a variable" preference silently flipped off is a side
        # effect nobody asked for, and it would persist in their editor forever.
        check("T362 the suppression flag is not left behind in the config", after == baseline,
              "config changed across the call: present before=%s after=%s" % (baseline, after))

    # ---------------------------------------------------------------- T363 the other guard still holds
    print("\n=== T363: rename_variable's RepNotify refusal is a DIFFERENT guard - still intact? ===")
    M.call("add_variable", {"blueprintId": bid, "name": "Ammo", "type": "int"})
    M.call("set_variable_flags", {"blueprintId": bid, "name": "Ammo",
                                  "replicated": True, "repNotify": True})
    # Through the scratch-only confirm path, or the confirm gate refuses first and this test measures
    # that instead of the RepNotify guard it is actually about - which is what it did on its first run.
    t0 = time.time()
    try:
        r = SC.confirm_call("rename_variable",
                            {"blueprintId": bid, "oldName": "Ammo", "newName": "Rounds"}, timeout=45)
        dt = time.time() - t0
    except M.Timeout:
        check("T363 rename_variable came back at all", False,
              "rename_variable hung - the RepNotify modal was reached instead of refused")
        r, dt = None, time.time() - t0
    if r is not None:
        # This one is guarded by REFUSING, not by suppressing, because the engine reverts the name
        # when the dialog is declined. The refusal must stay - it is the thing keeping that path safe.
        check("T363 renaming a RepNotify variable is refused rather than attempted",
              r.get("ok") is False, json.dumps(r)[:200])
        check("T363 and the refusal explains how to proceed",
              "repNotify" in (r.get("error") or "") or "RepNotify" in (r.get("error") or ""),
              (r.get("error") or "")[:200])
    check("T363 the bridge is still answering", M.bridge_responsive() is True, "bridge died")

    # ---------------------------------------------------------------- T364 no-op still a no-op
    print("\n=== T364: retyping to the SAME type still does nothing (not a false success) ===")
    r, dt = call_must_return("T364", "set_variable_type",
                             {"blueprintId": bid, "name": "Health", "type": "int"})
    if r is not None:
        check("T364 a same-type retype reports changed:false", r.get("changed") is False,
              json.dumps(r)[:200])
        check("T364 and says so plainly", len(r.get("note") or "") > 10, r.get("note"))

    # ---------------------------------------------------------------- T365 the backstop is live
    print("")
    print("=== T365: the global backstop is actually in force, not just in the source ===")
    sa = M.call("self_audit", {})
    # self_audit runs through RunEndpoint like every other handler, so the flag it reports is the one a
    # handler actually sees - observed from inside, not asserted from reading the code. If this is ever
    # false, any engine call that opens a dialog can hang the whole bridge again (PM-011).
    check("T365 self_audit reports the unattended guard", "unattendedGuard" in sa,
          "field missing - either the DLL predates the backstop or it was removed")
    check("T365 and the guard is ON inside a running handler", sa.get("unattendedGuard") is True,
          "unattendedGuard=%s - the modal backstop is NOT in force" % sa.get("unattendedGuard"))

    # ---------------------------------------------------------------- T366 batch inherits it
    print("")
    print("=== T366: batch ops are covered too, which is not obvious from the code ===")
    # batch does NOT recurse through RunEndpoint - it dispatches each op straight out of Handlers()
    # (MifBridgeNodes.cpp), so the natural worry is that ops run without the guard and a modal inside a
    # batch could still hang the bridge. They do not: batch is ITSELF invoked through RunEndpoint, so
    # the TGuardValue is on the stack for every op it dispatches.
    #
    # That is an argument, and this is the measurement. self_audit reports the flag as a handler
    # actually sees it, so running it as a batch op answers the question directly.
    b = M.call("batch", {"ops": [{"op": "self_audit"}]})
    ops = b.get("ops") or b.get("results") or []
    check("T366 a batch of one self_audit runs", bool(ops), json.dumps(b)[:200])
    if ops:
        inner = (ops[0] or {}).get("unattendedGuard")
        check("T366 and the guard is ON inside a batch op", inner is True,
              "unattendedGuard=%s inside batch - ops dispatched by batch would run unguarded, so a "
              "modal in any of them hangs the bridge" % inner)

    SC.confirm_call("delete_asset", {"path": "/Game/_MifModal/BP_%d" % st})
    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
