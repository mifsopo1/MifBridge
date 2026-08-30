"""material_statistics - and the two ways reading a number can be worse than not reading it.

WHY THIS EXISTS. Instruction counts, sampler count and texture samples are what a material
optimisation is actually judged by, and they were the one thing the material editor's Stats panel
showed that the bridge could not. It is the missing VERIFY step for three endpoints that already
ship: set_material_property, set_material_instance_parameter and recompile_material can all change
a material's cost, and none of them could tell you whether they had.

T7401 IS THE ONE THAT MATTERS, and it is a guard against an unbounded editor freeze rather than
against a wrong value. UMaterialEditingLibrary::GetStatistics does this
(MaterialEditingLibrary.cpp:1358-1362):

    if (!Resource->IsGameThreadShaderMapComplete())
        Resource->SubmitCompileJobs_GameThread(EShaderCompileJobPriority::High);
    Resource->FinishCompilation();

FinishCompilation is a synchronous stall on the game thread with no progress and no cancel. Called
straight from an HTTP handler on a material whose shaders are not cached, that is a hang - on a
complex material, minutes of frozen editor for what reads like a harmless query. So the endpoint
asks the engine's own public predicate FIRST (MaterialShared.h:2183) and refuses, naming the wait,
unless the caller opts in with compile:true.

This is not hypothetical and the suite does not have to invent it: /Paper2D's sprite material
instances ship with no completed shader map in this editor, so T7401 exercises the real refusal and
T7402 exercises the real opt-in.

T7403 GUARDS THE QUIETER HAZARD. Every field of FMaterialStatistics is `= 0` initialised
(MaterialEditingLibrary.h:22-52), and GetStatistics returns the struct untouched when
GetMaterialResource hands back null (:1356). So a material with no resource for this feature level
reports zero pixel instructions - which is indistinguishable from a genuinely trivial material and
is exactly the wrong answer to give an optimisation pass. The endpoint resolves the resource itself
and refuses when it is absent, instead of passing zeros off as a measurement.

WHAT IS NOT EXERCISED, said plainly rather than implied: cooked:true. Every material this editor's
asset registry returns is uncooked engine or plugin content - the project's own cooked materials
live in paks the registry does not enumerate here - so the claim that this works on cooked content
(a cooked material keeps its shader maps even though its expression graph is stripped, which is
precisely where list_material_expressions correctly returns nothing) is NOT proven by this suite.
The cooked flag is reported on every response so the gap is visible rather than silent.
"""
import json
import sys

import mifaudit as M

PASS = []
FAIL = []

NUMERIC = ("vertexShaderInstructions", "pixelShaderInstructions", "samplers",
           "vertexTextureSamples", "pixelTextureSamples", "virtualTextureSamples",
           "uvScalars", "interpolatorScalars")


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

    # ------------------------------------------------------------------ T7400 the read
    print("=== T7400: a material with built shaders reports real figures ===")
    r = M.raw_post("material_statistics",
                   {"path": "/Engine/EngineMaterials/WorldGridMaterial.WorldGridMaterial"})
    check("T7400 it answers for a stock material", r.get("ok") is True, json.dumps(r)[:250])
    # ASSERT THE VALUES, not the keys. A response full of nulls has every field present.
    typed = [k for k in NUMERIC if isinstance(r.get(k), (int, float))]
    check("T7400 all eight statistics come back as real numbers",
          len(typed) == len(NUMERIC),
          "%d of %d numeric: %s" % (len(typed), len(NUMERIC), json.dumps(r)[:220]))
    # A material that draws something cannot be zero instructions. This is the check that would
    # catch the null-resource-returns-zeros failure if the guard in T7403 ever regressed.
    check("T7400 and the instruction counts are non-zero, which zeros-from-a-null-resource "
          "would not be",
          (r.get("pixelShaderInstructions") or 0) > 0
          and (r.get("vertexShaderInstructions") or 0) > 0,
          "vs=%s ps=%s" % (r.get("vertexShaderInstructions"), r.get("pixelShaderInstructions")))
    check("T7400 it says whether the shader map was already complete",
          r.get("shaderMapComplete") is True, r.get("shaderMapComplete"))
    check("T7400 and it did not have to wait", r.get("waitedForCompile") is False,
          r.get("waitedForCompile"))
    # The numbers invite a comparison they do not support, so the response says so.
    check("T7400 the note warns they are representative-shader figures, not a permutation total",
          "representative-shader" in (r.get("note") or ""), (r.get("note") or "")[:180])
    check("T7400 and that comparing two DIFFERENT materials says less than it looks",
          "two different materials" in (r.get("note") or ""), (r.get("note") or "")[:220])

    inst = M.raw_post("material_statistics", {
        "path": "/Engine/EngineSky/VolumetricClouds/m_SimpleVolumetricCloud_Inst."
                "m_SimpleVolumetricCloud_Inst"})
    if inst.get("ok"):
        check("T7400 a MaterialInstance answers too, not just a Material",
              inst.get("class") == "MaterialInstanceConstant", inst.get("class"))

    # ------------------------------------------------------------------ T7401 the freeze guard
    print("\n=== T7401: an unbuilt shader map is refused, not silently waited on ===")
    # Found by looking rather than assumed: these ship with no completed shader map here.
    candidates = [x["path"] for x in
                  (M.call("find_assets", {"class": "MaterialInstanceConstant",
                                          "limit": 40}).get("assets") or [])]
    unbuilt = None
    for path in candidates:
        probe = M.raw_post("material_statistics", {"path": path})
        if probe.get("ok") is False and probe.get("wouldBlock") is True:
            unbuilt = (path, probe)
            break
    if not unbuilt:
        print("  NOTE  every material here has a built shader map, so T7401 is unexercised.")
    else:
        path, probe = unbuilt
        check("T7401 a material with no built shader map is refused", probe.get("ok") is False,
              json.dumps(probe)[:200])
        # THE assertion: the refusal must be machine-readable, not just prose, or a caller cannot
        # branch on it without parsing English.
        check("T7401 and flags wouldBlock, so a caller can branch without parsing prose",
              probe.get("wouldBlock") is True, json.dumps(probe)[:200])
        check("T7401 the reason names the STALL specifically, not a vague failure",
              "STALL" in (probe.get("error") or "")
              and "FinishCompilation" in (probe.get("error") or ""),
              (probe.get("error") or "")[:260])
        check("T7401 and names the way forward",
              "compile:true" in (probe.get("error") or ""), (probe.get("error") or "")[:260])
        # No numbers may be reported when nothing was measured - half an answer here reads as a
        # cheap material.
        leaked = [k for k in NUMERIC if k in probe]
        check("T7401 and reports NO statistics at all, since nothing was measured",
              not leaked, "leaked: %s" % leaked)

        # -------------------------------------------------------------- T7402 the opt-in
        print("\n=== T7402: compile:true accepts the wait and then answers ===")
        got = M.raw_post("material_statistics", {"path": path, "compile": True})
        check("T7402 the same material answers once the wait is accepted",
              got.get("ok") is True, json.dumps(got)[:250])
        typed2 = [k for k in NUMERIC if isinstance(got.get(k), (int, float))]
        check("T7402 with all eight figures present as numbers",
              len(typed2) == len(NUMERIC), json.dumps(got)[:250])
        # The distinction the caller needs afterwards: these numbers cost a stall.
        check("T7402 and it records that it DID wait, which the first call did not",
              got.get("waitedForCompile") is True and r.get("waitedForCompile") is False,
              "this=%s stockMaterial=%s"
              % (got.get("waitedForCompile"), r.get("waitedForCompile")))

    # ------------------------------------------------------------------ T7403 wrong asset types
    print("\n=== T7403: things that have no statistics are refused, not answered with zeros ===")
    fn = [x["path"] for x in
          (M.call("find_assets", {"class": "MaterialFunction", "limit": 1}).get("assets") or [])]
    if fn:
        f = M.raw_post("material_statistics", {"path": fn[0]})
        check("T7403 a MaterialFunction is refused", f.get("ok") is False, json.dumps(f)[:200])
        # A function genuinely HAS no shader map of its own; saying so beats a row of zeros.
        check("T7403 and told why - it has no shader map of its own",
              "no shader map of its own" in (f.get("error") or ""), (f.get("error") or "")[:220])
        check("T7403 with no statistics in the response",
              not [k for k in NUMERIC if k in f], json.dumps(f)[:200])

    nota = M.raw_post("material_statistics",
                      {"path": "/Engine/EditorResources/S_Actor.S_Actor"})
    check("T7403 a texture is refused by class", nota.get("ok") is False,
          (nota.get("error") or "")[:200])
    missing = M.raw_post("material_statistics", {"path": "/Game/_MifNope/DoesNotExist"})
    check("T7403 a missing asset is refused", missing.get("ok") is False,
          (missing.get("error") or "")[:180])
    nopath = M.raw_post("material_statistics", {})
    check("T7403 a missing path is refused", nopath.get("ok") is False,
          (nopath.get("error") or "")[:180])
    bogus = M.raw_post("material_statistics", {
        "path": "/Engine/EngineMaterials/WorldGridMaterial.WorldGridMaterial",
        "featureLevel": "SM5"})
    check("T7403 an unknown parameter is refused by name rather than ignored",
          bogus.get("ok") is False and "featureLevel" in (bogus.get("error") or ""),
          (bogus.get("error") or "")[:220])

    print("\n  NOT EXERCISED: cooked:true. Every material this asset registry returns is uncooked")
    print("  engine or plugin content, so the claim that statistics survive a cook - which is much")
    print("  of the point, since that is where list_material_expressions correctly finds nothing -")
    print("  is NOT proven here. The cooked flag is on every response so the gap stays visible.")

    check("T7403 - the editor is still alive", M.call("self_audit", {}).get("ok") is True,
          "GetStatistics can submit shader compile jobs on the game thread")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
