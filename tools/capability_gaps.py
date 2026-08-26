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
        stem = cls.lower().replace("blueprint", "").replace("definition", "").replace("2d", "")
        write = [e for e in names
                 if any(e.startswith(v) for v in ("add_", "set_", "create_", "remove_", "delete_",
                                                  "rename_", "apply_", "connect_", "import_"))
                 and stem and stem[:6] in e.lower()]
        rows.append({"class": cls, "assets": n, "writeEndpoints": sorted(write)})
    rows.sort(key=lambda r: -r["assets"])

    print("%-34s %7s  %s" % ("class", "assets", "endpoints that look like they author it"))
    print("-" * 100)
    for r in rows:
        if r["assets"] == 0:
            continue
        print("%-34s %7d  %s" % (r["class"], r["assets"],
                                 ", ".join(r["writeEndpoints"])[:56] or "(none by name)"))
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
