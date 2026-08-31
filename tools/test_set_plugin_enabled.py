"""set_plugin_enabled - the write half of an `enabled` field three endpoints already report.

T9003 IS THE ONE THAT JUSTIFIES THE SHAPE OF THE ENDPOINT, and it is the reason the postcondition is
not "is the plugin listed in the .uproject". ProjectManager.cpp, after updating a reference, checks
whether the resulting state matches the default-enabled set and REMOVES the entry entirely if it
does - still marking the project dirty. So for a plugin left at its default, ABSENCE is the correct
end state. This suite asserts that directly: enabling a default-disabled plugin produces an explicit
entry, and disabling it again produces NO entry with effectiveAfter false. A naive implementation
reads that correct restore as a failure.

T9000 IS THE GUARD THAT WOULD OTHERWISE CORRUPT THE PROJECT FILE. SetPluginEnabled appends a plugin
reference for ANY name it is handed, consults the plugin registry only for metadata, and its single
failure case is "no project loaded" - so a typo does not fail, it writes a reference to nothing into
the .uproject and returns true. The endpoint checks the name first; this asserts the refusal, and
that the refusal explains why rather than just saying no.

THE .uproject IS RESTORED FROM A BYTE COPY, VERIFIED. This is the only suite here that writes to a
file belonging to the project rather than to scratch assets, so it is arranged around getting it
back: bytes are copied before anything, the restore runs in a finally, and it is checked by
comparing bytes rather than assumed. A mutate-restore whose restore quietly failed is exactly how
engine_probe_result.json was corrupted earlier in this project's history - and the restore is NOT
delegated to the endpoint, because saving reserialises the whole descriptor and is legitimately not
byte-identical.
"""
import io
import json
import os
import sys

import mifaudit as M

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:400]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:400]))


def pick_subject():
    """A DISABLED plugin to toggle, or None. Named in the output either way.

    Disabled on purpose: enabling it writes an explicit entry and disabling it again strips that
    entry, which exercises BOTH sides of the default-stripping rule in one round trip.
    """
    r = M.raw_post("list_game_feature_plugins", {})
    plugins = r.get("plugins") or []
    disabled = [p.get("name") for p in plugins if p.get("enabled") is False and p.get("name")]
    enabled = [p.get("name") for p in plugins if p.get("enabled") is True and p.get("name")]
    return (disabled[0] if disabled else None), (enabled[0] if enabled else None), plugins


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    if M.write_mode() == "read":
        print("SKIPPED - read mode")
        return 0

    subject, an_enabled, plugins = pick_subject()
    print("discovered plugins: %d" % len(plugins))
    print("subject (currently DISABLED): %s" % subject)
    print("a currently ENABLED one     : %s" % an_enabled)
    if not subject:
        # PRECONDITION, NOT A PASS. Every assertion below is about changing a state, and with
        # nothing disabled there is nothing to change - so this reports SKIPPED rather than
        # quietly passing a suite that verified none of it.
        print("\nSKIPPED - no disabled plugin was discovered, so the write path has no safe")
        print("  subject and NOTHING was verified. Exit 2 means skipped, distinct from 0.")
        return 2

    # ------------------------------------------------------------------ T9000 the guards
    print("\n=== T9000: the refusals, and the one that protects the .uproject ===")
    bad = M.raw_post("set_plugin_enabled", {"name": "MifNoSuchPlugin", "enabled": True,
                                            "dryRun": True})
    check("T9000 an undiscovered plugin name is REFUSED", bad.get("ok") is False,
          json.dumps(bad)[:200])
    # THE assertion. Without this check the engine writes the typo into the project file.
    check("T9000 and the refusal explains that the engine would have written the name into the "
          ".uproject as a reference to nothing",
          "reference to nothing" in (bad.get("error") or ""), (bad.get("error") or "")[:260])

    noflag = M.raw_post("set_plugin_enabled", {"name": subject, "dryRun": True})
    check("T9000 a missing `enabled` is refused rather than defaulted",
          noflag.get("ok") is False and "no default" in (noflag.get("error") or ""),
          (noflag.get("error") or "")[:220])
    check("T9000 and says why defaulting it would be dangerous",
          "silently" in (noflag.get("error") or ""), (noflag.get("error") or "")[:220])

    unknown = M.raw_post("set_plugin_enabled", {"name": subject, "enabled": True,
                                                "restart": True})
    check("T9000 an unknown parameter is refused and the hint says the bridge cannot restart",
          unknown.get("ok") is False and "cannot restart" in (unknown.get("error") or ""),
          (unknown.get("error") or "")[:220])

    # ------------------------------------------------------------------ T9001 dryRun
    print("\n=== T9001: dryRun answers the question and writes nothing ===")
    dry = M.raw_post("set_plugin_enabled", {"name": subject, "enabled": True, "dryRun": True})
    check("T9001 dryRun reports it would change", dry.get("ok") is True
          and dry.get("wouldChange") is True and dry.get("changed") is False,
          json.dumps(dry)[:250])
    check("T9001 and reports the current effective state as disabled",
          dry.get("effectiveBefore") is False, json.dumps(dry)[:200])
    uproject = dry.get("projectFile")
    check("T9001 the response names the project file it would write",
          bool(uproject) and os.path.isfile(uproject), uproject)
    if not (uproject and os.path.isfile(uproject)):
        return 1

    before_bytes = io.open(uproject, "rb").read()
    M.raw_post("set_plugin_enabled", {"name": subject, "enabled": True, "dryRun": True})
    # Measured, not trusted: "writes nothing" is a claim about a file, so read the file.
    check("T9001 and the .uproject is byte-identical afterwards - dryRun really wrote nothing",
          io.open(uproject, "rb").read() == before_bytes, "the project file changed on a dry run")

    # ------------------------------------------------------------------ T9002 already in state
    print("\n=== T9002: already in the asked-for state is an answer, not a write ===")
    same = M.raw_post("set_plugin_enabled", {"name": subject, "enabled": False})
    check("T9002 disabling an already-disabled plugin succeeds with changed:false",
          same.get("ok") is True and same.get("changed") is False, json.dumps(same)[:250])
    check("T9002 and says so rather than reporting a write it did not do",
          "ALREADY" in (same.get("note") or ""), (same.get("note") or "")[:200])
    check("T9002 and the file is still untouched",
          io.open(uproject, "rb").read() == before_bytes, "the project file changed")

    # ------------------------------------------------------------------ T9003 the write
    print("\n=== T9003: the real write, and the entry that correctly disappears ===")
    backup = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "_mif_uproject_restore.bytes")
    io.open(backup, "wb").write(before_bytes)
    if io.open(backup, "rb").read() != before_bytes:
        print("  ABORTING - the byte backup did not verify, so the write is not attempted.")
        return 1

    try:
        on = M.raw_post("set_plugin_enabled", {"name": subject, "enabled": True})
        check("T9003 enabling succeeds and the effective state moved",
              on.get("ok") is True and on.get("effectiveBefore") is False
              and on.get("effectiveAfter") is True and on.get("changed") is True,
              json.dumps(on)[:280])
        check("T9003 an explicit entry now exists for a plugin moved OFF its default",
              on.get("hasExplicitEntry") is True, json.dumps(on)[:200])
        check("T9003 the project went dirty and the save cleared it - the engine's own signal, "
              "since SetPluginEnabled's return value says nothing about whether anything moved",
              on.get("projectDirtyAfterEdit") is True and on.get("saved") is True
              and on.get("projectDirtyAfterSave") is False, json.dumps(on)[:250])
        check("T9003 and the file on disk really changed",
              on.get("projectFileChangedOnDisk") is True
              and io.open(uproject, "rb").read() != before_bytes, json.dumps(on)[:200])
        check("T9003 a byte-copy backup was written and exists",
              bool(on.get("backup")) and os.path.isfile(on.get("backup") or ""), on.get("backup"))
        check("T9003 it says a RESTART is required and that this session did not change",
              on.get("restartRequired") is True and on.get("enabledInThisSession") is False,
              json.dumps(on)[:220])
        check("T9003 and warns that the whole file was reserialised, so the diff will be large",
              "whole" in (on.get("formattingNote") or "").lower(),
              (on.get("formattingNote") or "")[:200])

        # THE assertion this endpoint's postcondition was designed around.
        off = M.raw_post("set_plugin_enabled", {"name": subject, "enabled": False})
        check("T9003 disabling it again reports effectiveAfter false", off.get("ok") is True
              and off.get("effectiveAfter") is False, json.dumps(off)[:250])
        check("T9003 AND the explicit entry is GONE - the engine strips a reference that matches "
              "the default, so absence is the correct end state and a 'find it in the file' "
              "postcondition would call this correct restore a failure",
              off.get("hasExplicitEntry") is False, json.dumps(off)[:250])
    finally:
        # Always, verified, and NOT delegated to the endpoint: saving reserialises the descriptor,
        # so the round trip is legitimately not byte-identical to what was there before.
        current = io.open(uproject, "rb").read()
        if current != before_bytes:
            io.open(uproject, "wb").write(before_bytes)
        restored = io.open(uproject, "rb").read() == before_bytes
        check("T9003 (cleanup) the .uproject is byte-identical to how this suite found it",
              restored, "THE PROJECT FILE WAS NOT RESTORED - bytes are in %s" % backup)
        if restored:
            os.remove(backup)
            stray = uproject + ".mifbak"
            if os.path.isfile(stray):
                os.remove(stray)
            check("T9003 (cleanup) and no .mifbak was left behind by the suite",
                  not os.path.isfile(stray), stray)

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
