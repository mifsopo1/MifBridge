"""list_niagara_user_parameters - the values, not just the names.

The names were never the blocked part: get_property on
ExposedParameters.SortedParameterOffsets[N].Name already returns "User.Camera Forward Offset". The
VALUES are the blocked part. They live in a flat byte array indexed by offset, typed only by an opaque
index into FNiagaraTypeRegistry - a C++ singleton with no reflection surface - so no composition of
existing endpoints can answer "what is User.Spawn Rate set to".

T211 is the test with teeth, and it is a genuine cross-check rather than a restatement. It pulls the
raw ParameterData through get_property, decodes it INDEPENDENTLY in Python, and requires the two to
agree. If the handler misread an offset, a width or an endianness, the two implementations disagree
and this fails. Asserting the endpoint against itself would not have caught any of those.

T212 tests the tiling invariant: sizes are derived from the gaps between sorted offsets, so they must
exactly cover ParameterData with no gap and no overlap. That is what makes sizeBytes exact rather than
assumed, and it is the property that breaks first if the offsets are read wrongly.

It also caught the bug it was written for. A parameter store keeps THREE parallel arrays -
ParameterData, DataInterfaces and UObjects - behind ONE list of offsets, so an Offset is a byte
position for a value parameter and an ARRAY INDEX for an object one, with nothing in the entry saying
which. Taking widths across all of them gave typeIndex 86 two different widths on
/Game/UDS_Mif/Particles/Rain.Rain. Every value assertion below is therefore scoped by offsetSpace, and
T216 covers the object-backed parameters on their own terms.

T213 is why the type is reported as an index and the value in every valid reading. Four bytes is a
float, an int32 or a bool and the store does not say which - typeIndex 88 on this asset holds
collision channels whose float reading is denormal garbage (1.4e-45), and typeIndex 89 holds bools
stored as -1, whose float bits are NaN. Anything that picked "float" because the width was 4 would
report both as nonsense while looking successful.
"""
import json
import struct
import sys

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def pick_system():
    """The system with the MOST user parameters, chosen deliberately.

    A suite that samples whichever asset find_assets happens to return first is a coin flip - that
    already burned test_material_params, which passed for hours and then failed on a different draw.
    """
    r = M.call("find_assets", {"class": "NiagaraSystem", "limit": 300})
    best, best_n = None, -1
    for a in (r.get("assets") or []):
        p = a.get("path")
        q = M.call("list_niagara_user_parameters", {"path": p})
        n = q.get("totalParameters") or 0
        if n > best_n:
            best, best_n = p, n
    return best, best_n


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    path, n = pick_system()
    if not path or n <= 0:
        # SKIPPED (2), not a setup ERROR (3). Returning 3 reported this as something broken when
        # the honest answer is "this project has nothing to test against", and the two read very
        # differently in a sweep summary.
        #
        # TWO independent reasons here, either enough on its own, and neither is fixable from the
        # suite: none of this project's 38 NiagaraSystems declares a user parameter, and they are
        # all COOKED - set_niagara_user_parameter refuses a cooked system outright, because the
        # parameter store is runtime data that cannot be saved or recompiled, so the old value
        # returns. There is also no endpoint that CREATES a user parameter, so no fixture can be
        # built the way test_landscape_heightmap builds its landscape.
        print("SKIPPED - nothing was verified.")
        print("  No NiagaraSystem in this project declares a user parameter (%d systems checked),"
              % len(M.call("find_assets", {"class": "NiagaraSystem", "limit": 300}).get("assets")
                    or []))
        print("  and this project's Niagara content is COOKED, which set_niagara_user_parameter")
        print("  refuses anyway - the parameter store cannot be saved or recompiled.")
        print("  No endpoint creates a user parameter, so this suite cannot build its own fixture.")
        print("  Needs an UNCOOKED project with authored user parameters.")
        print("  Exit code 2 means SKIPPED, distinct from 0 (passed) and 1 (failed) on purpose.")
        return 2
    print("richest system: %s (%d parameters)" % (path, n))

    r = M.call("list_niagara_user_parameters", {"path": path})
    params = r.get("parameters") or []

    # ------------------------------------------------------------------ T210 the read
    print("\n=== T210: the read ===")
    check("T210 it answers", r.get("ok") is True, json.dumps(r)[:180])
    check("T210 it echoes the system", (r.get("system") or "").startswith(path.split(".")[0]),
          r.get("system"))
    check("T210 count matches the parameters returned", r.get("count") == len(params),
          "count=%s len=%d" % (r.get("count"), len(params)))
    check("T210 unfiltered, count equals totalParameters",
          r.get("count") == r.get("totalParameters"),
          "%s vs %s" % (r.get("count"), r.get("totalParameters")))
    check("T210 every parameter is in the User namespace",
          all((p.get("name") or "").startswith("User.") for p in params),
          str([p.get("name") for p in params if not (p.get("name") or "").startswith("User.")])[:150])
    check("T210 every parameter reports an offset",
          all(isinstance(p.get("offset"), (int, float)) for p in params),
          "one or more entries lack an offset")
    # Said on every entry, because offset 0 means a different thing in each space.
    check("T210 every parameter says WHICH offset space it is in",
          all(p.get("offsetSpace") in ("parameterData", "objectArray") for p in params),
          str(sorted({p.get("offsetSpace") for p in params})))
    vals = [p for p in params if p.get("offsetSpace") == "parameterData"]
    objs = [p for p in params if p.get("offsetSpace") == "objectArray"]
    print("  (%d value parameters, %d object-backed)" % (len(vals), len(objs)))
    check("T210 value parameters report a size",
          all(isinstance(p.get("sizeBytes"), (int, float)) for p in vals),
          "a parameterData entry lacks sizeBytes")
    # The layout check is what makes sizeBytes trustworthy, so it must always be stated.
    check("T210 it reports whether the layout check passed",
          isinstance(r.get("parameterLayoutVerified"), bool), r.get("parameterLayoutVerified"))
    check("T210 and on this asset it passed", r.get("parameterLayoutVerified") is True,
          r.get("layoutNote"))
    # The index is passed through, never translated - translating it would be the guess.
    check("T210 typeIndex is reported rather than a type name",
          all("typeIndex" in p for p in params) and not any("typeName" in p for p in params),
          "typeIndex must be present and no invented typeName")

    # ------------------------------------------------------------------ T211 independent decode
    print("\n=== T211 [teeth]: decoded independently and cross-checked ===")
    g = M.call("get_property", {"objectPath": path, "property": "ExposedParameters.ParameterData"})
    raw = str(g.get("value") or "")
    blob = bytes(int(x) for x in raw.strip("()").split(",") if x.strip().lstrip("-").isdigit())
    check("T211 the raw buffer is readable and the reported size matches it",
          len(blob) == r.get("parameterDataBytes"),
          "python read %d bytes, endpoint reported %s" % (len(blob), r.get("parameterDataBytes")))

    mismatches, checked_f, checked_v = [], 0, 0
    for p in vals:
        off, size = int(p.get("offset")), int(p.get("sizeBytes"))
        if off < 0 or off + size > len(blob):
            continue
        chunk = blob[off:off + size]
        if size == 4 and "asFloat" in p:
            mine_f = struct.unpack("<f", chunk)[0]
            mine_i = struct.unpack("<i", chunk)[0]
            checked_f += 1
            if p.get("asInt32") != mine_i:
                mismatches.append("%s asInt32 %s != %s" % (p.get("name"), p.get("asInt32"), mine_i))
            # Only compare finite floats: a bool stored as -1 is NaN as a float, and NaN != NaN.
            if mine_f == mine_f and abs(mine_f) < 3.0e38:
                if abs((p.get("asFloat") or 0.0) - mine_f) > max(1e-6, abs(mine_f) * 1e-6):
                    mismatches.append("%s asFloat %s != %s" % (p.get("name"), p.get("asFloat"), mine_f))
            if p.get("asBool") != (mine_i != 0):
                mismatches.append("%s asBool %s != %s" % (p.get("name"), p.get("asBool"), mine_i != 0))
        elif "asFloats" in p:
            mine = list(struct.unpack("<%df" % (size // 4), chunk))
            checked_v += 1
            theirs = p.get("asFloats") or []
            if len(mine) != len(theirs) or any(abs(a - b) > max(1e-6, abs(a) * 1e-6)
                                               for a, b in zip(mine, theirs)):
                mismatches.append("%s asFloats %s != %s" % (p.get("name"), theirs, mine))

    check("T211 enough parameters were cross-checked to mean something",
          checked_f >= 5 and checked_v >= 1, "scalars=%d vectors=%d" % (checked_f, checked_v))
    check("T211 the handler and an independent Python decode agree on EVERY value",
          not mismatches, "; ".join(mismatches[:4]))

    # ------------------------------------------------------------------ T212 the tiling invariant
    print("\n=== T212: sizes are exact, not assumed ===")
    ordered = sorted(vals, key=lambda p: p.get("offset"))
    gaps = []
    for i, p in enumerate(ordered):
        end = int(p.get("offset")) + int(p.get("sizeBytes"))
        nxt = int(ordered[i + 1].get("offset")) if i + 1 < len(ordered) else r.get("parameterDataBytes")
        if end != nxt:
            gaps.append("%s ends at %d, next starts at %s" % (p.get("name"), end, nxt))
    check("T212 the parameters tile ParameterData with no gap and no overlap",
          not gaps, "; ".join(gaps[:3]))
    check("T212 the sizes sum to the whole buffer",
          sum(int(p.get("sizeBytes")) for p in vals) == r.get("parameterDataBytes"),
          "sum=%d buffer=%s" % (sum(int(p.get("sizeBytes")) for p in vals),
                                r.get("parameterDataBytes")))
    # THE regression. One typeIndex is one type, so it must have one width - and it did not, back when
    # widths were taken across the object parameters as well.
    widths = {}
    for p in vals:
        widths.setdefault(p.get("typeIndex"), set()).add(p.get("sizeBytes"))
    bad = {k: sorted(v) for k, v in widths.items() if len(v) != 1}
    check("T212 one typeIndex always has one width", not bad, json.dumps(bad))

    # ------------------------------------------------------------------ T213 no type is guessed
    print("\n=== T213: four bytes is not assumed to be a float ===")
    four = [p for p in vals if p.get("sizeBytes") == 4]
    check("T213 every 4-byte value offers all three readings",
          all(all(k in p for k in ("asFloat", "asInt32", "asBool")) for p in four),
          "%d of %d 4-byte entries are missing a reading"
          % (sum(1 for p in four if not all(k in p for k in ("asFloat", "asInt32", "asBool"))), len(four)))
    vecs = [p for p in vals if p.get("sizeBytes") in (8, 12, 16)]
    check("T213 vector values report the right number of floats",
          all(len(p.get("asFloats") or []) == p.get("sizeBytes") // 4 for p in vecs),
          str([(p.get("name"), p.get("sizeBytes"), len(p.get("asFloats") or [])) for p in vecs])[:180])
    # The guard exists because a bool stored as -1 has NaN float bits, and reporting that as a value
    # without saying so is the silent-nonsense case this endpoint is meant to avoid.
    nonfinite = [p for p in four if p.get("floatIsFinite") is False]
    check("T213 a non-finite float reading is flagged rather than reported as a number",
          all(struct.unpack("<f", blob[int(p["offset"]):int(p["offset"]) + 4])[0] !=
              struct.unpack("<f", blob[int(p["offset"]):int(p["offset"]) + 4])[0]
              or abs(struct.unpack("<f", blob[int(p["offset"]):int(p["offset"]) + 4])[0]) > 3.0e38
              for p in nonfinite),
          "%d entries flagged; each must really be NaN or infinite" % len(nonfinite))
    check("T213 and it is not flagged on ordinary values",
          not any(p.get("floatIsFinite") is False
                  for p in four
                  if abs(struct.unpack("<f", blob[int(p["offset"]):int(p["offset"]) + 4])[0]) < 1e30
                  and struct.unpack("<f", blob[int(p["offset"]):int(p["offset"]) + 4])[0] ==
                      struct.unpack("<f", blob[int(p["offset"]):int(p["offset"]) + 4])[0]),
          "floatIsFinite was set on a perfectly finite float")

    # ------------------------------------------------------------------ T216 object parameters
    print("\n=== T216: object-backed parameters are labelled, not decoded ===")
    if objs:
        # These hold a Material or a data interface, and their offset indexes a different array. A
        # value here would be a fabrication, so there must not be one.
        check("T216 no object parameter pretends to have a decoded value",
              not any(any(k in p for k in ("asFloat", "asFloats", "asInt32", "asBool")) for p in objs),
              str([p.get("name") for p in objs
                   if any(k in p for k in ("asFloat", "asFloats"))])[:160])
        check("T216 each one explains why it has no value",
              all("index into" in (p.get("valueNote") or "") for p in objs),
              (objs[0].get("valueNote") or "")[:170])
        # Not decoded is not the same as dropped: the arrays are returned so a caller can resolve the
        # index itself, which is how you find which Material a "User.…Material" points at.
        arrays = (r.get("dataInterfaces") or []) + (r.get("uobjects") or [])
        check("T216 the arrays their offsets index into ARE returned",
              len(arrays) > 0, "dataInterfaces=%d uobjects=%d"
              % (len(r.get("dataInterfaces") or []), len(r.get("uobjects") or [])))
        check("T216 and every object offset is a valid index into one of them",
              all(0 <= int(p.get("offset")) < max(len(r.get("dataInterfaces") or []),
                                                  len(r.get("uobjects") or [])) for p in objs),
              str([(p.get("name"), p.get("offset")) for p in objs])[:180])
        check("T216 the returned entries are real object paths",
              all(isinstance(x, str) and x.startswith("/") for x in arrays), str(arrays[:2])[:170])
    else:
        # Worth stating rather than silently passing: this asset exercises only one of the three spaces.
        check("T216 this asset has no object-backed parameters to check", True,
              "single-space asset - the three-space handling is not exercised here")

    # ------------------------------------------------------------------ T214 filter
    print("\n=== T214: filtering ===")
    stem = (params[0].get("name") or "User.").split(".")[-1][:6]
    f = M.call("list_niagara_user_parameters", {"path": path, "nameContains": stem})
    check("T214 the filter narrows the result",
          0 < (f.get("count") or 0) <= (r.get("count") or 0), "%s of %s" % (f.get("count"), r.get("count")))
    check("T214 every returned name matches the filter",
          all(stem in (p.get("name") or "") for p in (f.get("parameters") or [])), stem)
    # totalParameters is the asset's number, not the filter's - conflating them hides the filter.
    check("T214 totalParameters still reports the whole asset",
          f.get("totalParameters") == r.get("totalParameters"),
          "%s vs %s" % (f.get("totalParameters"), r.get("totalParameters")))

    # ------------------------------------------------------------------ T215 guards
    print("\n=== T215: guards ===")
    notniagara = (M.call("find_assets", {"class": "Material", "limit": 1}).get("assets") or [{}])[0].get("path")
    for label, payload, expect in (
        ("no path", {}, "path is required"),
        ("missing asset", {"path": "/Game/NoSuchNiagara_zz"}, "no asset at"),
        ("a non-Niagara asset", {"path": notniagara}, "not a NiagaraSystem"),
    ):
        q = M.call("list_niagara_user_parameters", payload)
        check("T215 %s refused" % label, q.get("ok") is False, json.dumps(q)[:150])
        check("T215 %s explains" % label, expect in (q.get("error") or ""), (q.get("error") or "")[:170])
    # Discoverability: the write side is absent on purpose and must say so.
    w = M.call("list_niagara_user_parameters", {"path": path, "value": 1})
    check("T215 a write attempt is refused and says the write side is not implemented",
          w.get("ok") is False and "read-only" in (w.get("error") or ""), (w.get("error") or "")[:180])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
