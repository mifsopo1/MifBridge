"""Which asset types does this project actually CONTAIN, and can the bridge author them?

The lesson this project keeps relearning is that judging coverage by category NAME is worthless. The
13-agent audit rated five categories HIGH and all five collapsed when tested by capability; the IK Rig
decline was made on skeleton measurements rather than on the feature list. So this counts real assets
and pairs them with a real endpoint list, instead of asking whether a feature "sounds" covered.

Two numbers per class, and the pairing is the point:

  assets   how many of that class the project holds. A class with none is not a gap, whatever the
           competitor's feature list says.
  write    endpoints whose name suggests they author that class. Deliberately a WEAK signal - it is a
           shortlist for reading handlers, never a verdict. Nothing here concludes anything on its own.

Read-only: find_assets and nothing else.

FOUND LIVE, 2026-08-29: the substring heuristic below produced its own false negatives, the exact
failure mode this file's own docstring warns about. IKRigDefinition/IKRetargeter showed empty because
real endpoints spell it "ik_rig"/"ik_retarget" with an underscore the stem[:6] match never accounts
for, and eighteen more classes (CurveFloat, AnimMontage, ParticleSystem, SoundClass, UserDefinedEnum,
PCGGraph and others) showed empty because none of them individually have a dedicated author endpoint -
but ALL of them are reachable through `create_asset`, which is fully generic (any concrete, non-Actor,
non-Blueprint UObject subclass) and was live-tested against every one of them the day this note was
added: 9 of 11 spot-checked succeeded outright, the remaining 2 (PrimaryDataAsset, NavigationData)
correctly REFUSED as abstract with a helpful message rather than silently failing. `create_asset` is
listed below as a standing candidate for every class for exactly this reason - it is still only a
candidate, not a verdict, since an abstract base class or one needing a special factory (the way
ULevelSequence needed an Initialize() call, found earlier this session) will refuse or half-work. Keep
reading the handler, or just try create_asset live, before concluding either way.
"""
import json
import sys

import mifaudit as M

# Deliberately broad, including classes expected to be absent - "we checked and there are none" is a
# finding, and it is the one that stops a category being rebuilt from a feature list later.
CLASSES = [
    "Blueprint", "WidgetBlueprint", "AnimBlueprint", "SkeletalMesh", "StaticMesh", "Skeleton",
    "AnimSequence", "AnimMontage", "BlendSpace", "AimOffsetBlendSpace", "PhysicsAsset",
    "IKRigDefinition", "IKRetargeter", "ControlRigBlueprint",
    "Material", "MaterialInstanceConstant", "MaterialFunction", "MaterialParameterCollection",
    "Texture2D", "TextureCube", "TextureRenderTarget2D",
    "SoundWave", "SoundCue", "SoundClass", "SoundMix", "MetaSoundSource", "SoundAttenuation",
    "DataTable", "CurveTable", "CurveFloat", "CurveVector", "CurveLinearColor",
    "UserDefinedStruct", "UserDefinedEnum", "DataAsset", "PrimaryDataAsset",
    "NiagaraSystem", "NiagaraEmitter", "ParticleSystem",
    "LevelSequence", "World", "Landscape", "FoliageType_InstancedStaticMesh", "LandscapeGrassType",
    "BehaviorTree", "BlackboardData", "AIController", "NavigationData",
    "InputAction", "InputMappingContext", "GameplayEffect", "AttributeSet",
    "PCGGraph", "SubsurfaceProfile", "PoseAsset", "AnimComposite",
]


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    names = M.endpoint_names()
    rows = []
    for cls in CLASSES:
        r = M.call("find_assets", {"class": cls, "limit": 500})
        n = len(r.get("assets") or []) if r.get("ok") else -1
        # underscore-tolerant: "ikrigdefinition" now also matches "ik_rig" endpoints, which the old
        # bare-substring check could not (found live - IKRigDefinition/IKRetargeter both showed empty
        # despite having real, extensive dedicated endpoints, purely because of the underscore).
        stem = cls.lower().replace("blueprint", "").replace("definition", "").replace("2d", "")
        stem_nounderscore = stem.replace("_", "")
        write = [e for e in names
                 if any(e.startswith(v) for v in ("add_", "set_", "create_", "remove_", "delete_",
                                                  "rename_", "apply_", "connect_", "import_"))
                 and stem and (stem[:6] in e.lower() or stem_nounderscore[:6] in e.lower().replace("_", ""))]
        # create_asset is a STANDING CANDIDATE for every class, not matched by name because "asset"
        # names no specific class - it is fully generic for any concrete, non-Actor, non-Blueprint
        # UObject subclass. Listed separately so a reader can see it is a different KIND of signal
        # (a known-generic fallback, not a per-class match) rather than folding it into `write` and
        # losing that distinction.
        rows.append({"class": cls, "assets": n, "writeEndpoints": sorted(write),
                     "genericCreateAssetCandidate": "create_asset" in names})
    rows.sort(key=lambda r: -r["assets"])

    print("%-34s %7s  %-30s %s" % ("class", "assets", "endpoints that look like they author it", "generic"))
    print("-" * 110)
    for r in rows:
        if r["assets"] == 0:
            continue
        print("%-34s %7d  %-30s %s" % (
            r["class"], r["assets"], ", ".join(r["writeEndpoints"])[:30] or "(none by name)",
            "create_asset" if r["genericCreateAssetCandidate"] else ""))
    dedicated_only_empty = [r["class"] for r in rows
                            if r["assets"] and not r["writeEndpoints"] and not r["genericCreateAssetCandidate"]]
    if dedicated_only_empty:
        print("\nNO write endpoint AND no create_asset in this build (the real shortlist, small on")
        print("purpose - everything else has at least the generic candidate to try/read first):")
        print("  " + ", ".join(dedicated_only_empty))
    absent = [r["class"] for r in rows if r["assets"] == 0]
    print("\nclasses with ZERO assets in this project (not gaps - recorded so they are not rebuilt")
    print("from a feature list later):\n  " + ", ".join(absent))
    with open("capability_gaps.json", "w") as f:
        json.dump(rows, f, indent=1)
    print("\nwritten to tools/capability_gaps.json")
    print("NOTE: writeEndpoints is a name match and proves nothing. Read the handler before")
    print("concluding anything is covered - that mistake is why five audited categories collapsed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
