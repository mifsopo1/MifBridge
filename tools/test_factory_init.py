"""create_asset and the classes whose engine factory does more than NewObject.

WHY THIS EXISTS. create_asset accepts any concrete UObject class and constructs it with a bare
NewObject. That is right for the many classes whose default state is usable, and wrong - sometimes
fatally - for the ones the engine only ever creates through a UFactory that does further work. It
had already bitten twice before anyone went looking:

  ULevelSequence   2026-08-28  malformed - no UMovieScene sub-object, so every Sequencer endpoint
                               failed on it. Fixed with Initialize().
  UUserDefinedEnum 2026-08-30  FATAL - CppForm stayed Regular and the first operation naming an
                               enumerator hit check(CppForm == ECppForm::Namespaced) and
                               terminated the editor. Fixed with SetEnums(empty, Namespaced).

tools/audit_factory_init.py now reads the engine's own factory sources and reports every
FactoryCreateNew that calls something on the object after constructing it - 22 of them, 21 classes
create_asset does not handle. So the third case gets found by running a script rather than by an
editor dying.

IT WARNS RATHER THAN REFUSING, and T6201 is that distinction. Reading those 22 factories shows the
calls are NOT all equal: USkeleton's factory REQUIRES a target skeletal mesh and opens a dialog
without one, so a bare skeleton is genuinely malformed - while USoundClass's InitSoundClasses is a
global audio-device refresh that says nothing about the asset. Refusing all of them would block
legitimate creations to catch a few; creating them all silently is what produced the two bugs above.
So the classes this plugin does not replicate are NAMED, with what the factory does, and the caller
decides.

T6202 IS THE ONE THAT KEEPS THE LIST HONEST: a class that gets proper handling must come OFF the
list, or the warning outlives the problem and starts training people to ignore it. UserDefinedEnum
and LevelSequence are handled, so neither may warn.
"""
import json
import sys
import time

import mifaudit as M
import scratch_confirm as SC

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:400]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:400]))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    if M.write_mode() == "read":
        print("SKIPPED - read mode")
        return 0

    st = int(time.time()) % 100000
    made = []
    try:
        # ------------------------------------------------------------------ T6200 the warning
        print("=== T6200: a class whose factory does more is flagged, not silently created ===")
        a = M.raw_post("create_asset", {"path": "/Game/_MifFact/SC_Test%d" % st,
                                        "class": "SoundClass"})
        check("T6200 the asset is still created - this warns, it does not refuse",
              a.get("ok") is True, json.dumps(a)[:220])
        if a.get("assetPath"):
            made.append(a["assetPath"])
        check("T6200 and it is flagged as possibly incomplete",
              a.get("factoryInitIncomplete") is True, a.get("factoryInitIncomplete"))
        # The note has to name the ACTUAL problem, not just say "may be incomplete" - otherwise a
        # caller has no way to judge whether it matters for their class.
        check("T6200 the note says the engine's factory does more than NewObject",
              "does MORE than NewObject" in (a.get("factoryNote") or ""),
              (a.get("factoryNote") or "")[:200])
        check("T6200 and points at the audit that says exactly what that factory does",
              "audit_factory_init" in (a.get("factoryNote") or ""),
              (a.get("factoryNote") or "")[:250])
        check("T6200 with a concrete example of why it can matter",
              "skeletal mesh" in (a.get("factoryNote") or ""),
              (a.get("factoryNote") or "")[:250])

        # ------------------------------------------------------------------ T6201 not everything
        print("\n=== T6201: a class NOT on the list carries no warning ===")
        b = M.raw_post("create_asset", {"path": "/Game/_MifFact/DT_Test%d" % st,
                                        "class": "DataTable"})
        if b.get("ok"):
            made.append(b.get("assetPath"))
            # THE assertion that stops this becoming noise. A warning on everything is a warning
            # on nothing.
            check("T6201 an ordinary class is created with no factory warning at all",
                  b.get("factoryInitIncomplete") is None, json.dumps(b)[:220])
        else:
            print("  NOTE  DataTable needs a row struct here, so this arm is unexercised.")

        # ------------------------------------------------------------------ T6202 handled classes
        print("\n=== T6202: a class that IS handled must not warn ===")
        c = M.raw_post("create_asset", {"path": "/Game/_MifFact/E_Test%d" % st,
                                        "class": "UserDefinedEnum"})
        check("T6202 a UserDefinedEnum is created", c.get("ok") is True, json.dumps(c)[:200])
        if c.get("assetPath"):
            made.append(c["assetPath"])
        # It is handled - create_asset does the SetEnums itself - so warning about it would be
        # false, and a false warning is how a real one gets ignored.
        check("T6202 and does NOT warn, because create_asset performs that initialisation itself",
              c.get("factoryInitIncomplete") is None, c.get("factoryInitIncomplete"))
        # And the proof it really was initialised: writing to it would otherwise kill the editor.
        add = M.raw_post("add_enum_value", {"enum": c.get("assetPath"), "value": "Alpha"})
        check("T6202 writing to it works, which is the proof the initialisation really happened",
              add.get("ok") is True, json.dumps(add)[:200])
        check("T6202 - the editor is still alive", M.call("self_audit", {}).get("ok") is True,
              "an uninitialised UserDefinedEnum terminates the editor on the first enumerator named")

        # -------------------------------------------------- T6203 what the review found
        print("\n=== T6203: the three defects an adversarial review found in this table ===")
        # (a) THE FALSE ALARM. UMaterialFactoryNew's only post-construct call is PostEditChange()
        # inside `if (InitialTexture != nullptr)` (EditorFactories.cpp:498-525) - a UFactory member
        # create_asset never sets, so the default path is byte-identical to this endpoint's own.
        # Material is among the most commonly created classes, so warning on it fired constantly
        # and trained callers to ignore the warning entirely.
        mat = M.raw_post("create_asset", {"path": "/Game/_MifFact/M_T%d" % st, "class": "Material"})
        if mat.get("assetPath"):
            made.append(mat["assetPath"])
        check("T6203 Material does NOT warn - its factory's only extra work is behind a condition "
              "create_asset can never satisfy",
              mat.get("ok") is True and mat.get("factoryInitIncomplete") is None,
              json.dumps(mat)[:230])

        # (b) THE ONE THAT MUST SURVIVE THE PRUNING. UMaterialInstanceConstant's InitResources()
        # sits inside a plain `if (MIC)` null check and DOES always run - the counter-example that
        # proved brace depth alone was the wrong test for (a).
        mic = M.raw_post("create_asset", {"path": "/Game/_MifFact/MIC_T%d" % st,
                                          "class": "MaterialInstanceConstant"})
        if mic.get("assetPath"):
            made.append(mic["assetPath"])
        check("T6203 MaterialInstanceConstant STILL warns - InitResources runs unconditionally, so "
              "pruning the false alarms must not have taken this with it",
              mic.get("factoryInitIncomplete") is True, json.dumps(mic)[:230])

        # (c) SUBCLASSES. An exact name compare meant no subclass could ever warn, though it
        # inherits the very gap its parent is listed for.
        sub = M.raw_post("create_asset", {"path": "/Game/_MifFact/TLP_T%d" % st,
                                          "class": "TextureLightProfile"})
        if sub.get("assetPath"):
            made.append(sub["assetPath"])
        check("T6203 a SUBCLASS of a listed class warns too",
              sub.get("factoryInitIncomplete") is True, json.dumps(sub)[:230])
        check("T6203 and says which ancestor it matched, so the note is not confusing",
              sub.get("factoryInitVia") == "Texture2D", sub.get("factoryInitVia"))

        # (d) THE SUBCLASS HOLE IN THE STRUCT BRANCH. `Class ==` exact equality let a concrete
        # engine subclass of UUserDefinedStruct fall to the bare NewObject and reproduce the exact
        # EditorData-less asset this handler was changed to prevent.
        sc = M.raw_post("create_asset", {"path": "/Game/_MifFact/AIS_T%d" % st,
                                         "class": "AISenseBlueprintListener"})
        check("T6203 a UUserDefinedStruct SUBCLASS is created", sc.get("ok") is True,
              json.dumps(sc)[:220])
        if sc.get("assetPath"):
            made.append(sc["assetPath"])
            # The proof, same as T6210: readable members means EditorData exists, because
            # GetVarDesc CastChecks it.
            check("T6203 and it went through the engine's creator - its members are readable, "
                  "which a bare NewObject would make impossible",
                  M.raw_post("list_struct_members",
                             {"struct": sc["assetPath"]}).get("ok") is True,
                  json.dumps(sc)[:230])

        seq = M.raw_post("create_asset", {"path": "/Game/_MifFact/LS_Test%d" % st,
                                          "class": "LevelSequence"})
        if seq.get("ok"):
            made.append(seq.get("assetPath"))
            check("T6202 LevelSequence is handled too, so it does not warn either",
                  seq.get("factoryInitIncomplete") is None, seq.get("factoryInitIncomplete"))
    finally:
        for path in [m for m in made if m]:
            SC.confirm_call("delete_asset", {"path": path})

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
