"""list_niagara_user_parameters reports a TYPE now, instead of offering three readings of one value.

WHAT IT USED TO RETURN, measured on a real system rather than described:

    {"name":"User.BoatSize","typeIndex":86,"sizeBytes":4,
     "asFloat":1,"asInt32":1065353216,"asBool":true,"rawBytes":[0,0,128,63]}

Three interpretations of the same four bytes, because the reflection path knew the SIZE and not the
TYPE - and typeIndex 86 is an internal integer that means nothing outside the engine. A caller could
not tell whether BoatSize was 1.0 or 1065353216. That is a hex dump with hints, not a report.

The parameter store carried the answer the whole time: ReadParameterVariables() returns
FNiagaraVariableWithOffset, each with a real FNiagaraTypeDefinition. The type was inferred only
because this file avoided linking Niagara - and its own comment said so. That rationale was out of
date: MIF_WITH_NIAGARA is already used by four other things including create_asset, so the
dependency was present whether this file used it or not.

T8200 IS THE ASSERTION THAT MATTERS: exactly ONE value per parameter. A response carrying asFloat
AND asInt32 AND asBool has not answered the question, it has forwarded it.

THE REFLECTION PATH IS NOT DELETED, and T8202 is why that is deliberate rather than laziness. It is
what answers on a build without the Niagara plugin. Removing a working degraded path to make the
good one look tidier trades real coverage for appearance - so `typed` is reported, and a caller can
tell which path answered instead of guessing from the shape of the reply.
"""
import json
import sys

import mifaudit as M

PASS = []
FAIL = []

# The keys the old guessing path emitted. None of them should survive on a typed answer.
GUESS_KEYS = ("asFloat", "asInt32", "asBool", "asFloats", "typeIndex")


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

    systems = [a["path"] for a in
               (M.call("find_assets", {"class": "NiagaraSystem", "limit": 12}).get("assets") or [])]
    withparams = []
    for path in systems:
        r = M.raw_post("list_niagara_user_parameters", {"path": path})
        if r.get("ok") and (r.get("count") or 0) > 0:
            withparams.append((path, r))
        if len(withparams) >= 2:
            break
    if not withparams:
        print("SKIPPED - no NiagaraSystem here exposes a User. parameter, so nothing was verified.")
        return 2

    path, r = withparams[0]
    print("using %s (%d parameters)" % (path, r.get("count")))

    # ------------------------------------------------------------------ T8200 one value
    print("\n=== T8200: a type, and exactly one decoded value ===")
    check("T8200 the read answers", r.get("ok") is True, json.dumps(r)[:200])
    check("T8200 and says it took the typed path, so the caller knows which answered",
          r.get("typed") is True, r.get("typed"))

    params = [p for p in (r.get("parameters") or []) if isinstance(p, dict)]
    check("T8200 it returned parameter rows", len(params) > 0, len(params))

    # ASSERT THE VALUES, not key presence. Counting fully-typed rows and comparing against the
    # total cannot pass vacuously on an empty list the way all() can.
    typed_rows = sum(1 for p in params
                     if isinstance(p.get("type"), str) and p["type"]
                     and isinstance(p.get("valueKind"), str) and p["valueKind"])
    check("T8200 every row carries a real type NAME and a valueKind",
          len(params) > 0 and typed_rows == len(params),
          "%d of %d fully typed: %s" % (typed_rows, len(params), json.dumps(params)[:220]))

    # THE assertion this endpoint exists for. Three readings of one value is not an answer.
    ambiguous = [p["name"] for p in params if any(k in p for k in GUESS_KEYS)]
    check("T8200 and NO row offers asFloat/asInt32/asBool for the same bytes - the guessing is gone",
          not ambiguous, "still guessing on: %s" % ambiguous)

    # A value-typed row must actually carry a value, or the type is decoration.
    valued = [p for p in params if p.get("valueKind") in
              ("float", "int", "bool", "vec2", "vec3", "vec4", "quat", "position", "linearColor")]
    missing = [p.get("name") for p in valued if "value" not in p]
    check("T8200 every row with a decodable kind actually carries a value",
          not missing, "no value on: %s" % missing)

    # ------------------------------------------------------------------ T8201 the decoding
    print("\n=== T8201: the decoding matches the type, not the byte count ===")
    by_kind = {}
    for p in params:
        by_kind.setdefault(p.get("valueKind"), []).append(p)
    for kind, want_len in (("vec2", 2), ("vec3", 3), ("position", 3),
                           ("vec4", 4), ("quat", 4), ("linearColor", 4)):
        for p in by_kind.get(kind, []):
            v = p.get("value")
            check("T8201 %s '%s' decodes to %d components" % (kind, p.get("name"), want_len),
                  isinstance(v, list) and len(v) == want_len, json.dumps(p)[:200])
    for p in by_kind.get("float", []):
        # A float that came back as its own bit pattern is the exact failure the old path had.
        check("T8201 float '%s' is a number, not an int32 bit pattern" % p.get("name"),
              isinstance(p.get("value"), (int, float)) and abs(p.get("value")) < 1e9,
              json.dumps(p)[:200])
    for p in by_kind.get("bool", []):
        check("T8201 bool '%s' is a real boolean" % p.get("name"),
              isinstance(p.get("value"), bool), json.dumps(p)[:200])

    # ------------------------------------------------------------------ T8202 honesty
    print("\n=== T8202: what it does NOT decode, said plainly ===")
    for p in params:
        if p.get("valueKind") == "raw":
            check("T8202 undecoded '%s' still NAMES its type - an unknown struct and a float no "
                  "longer look alike" % p.get("name"),
                  bool(p.get("type")) and "does not decode" in (p.get("note") or ""),
                  json.dumps(p)[:220])
        if p.get("valueKind") in ("dataInterface", "object"):
            check("T8202 object-valued '%s' says there is no number rather than dumping pointer "
                  "bytes" % p.get("name"),
                  "no number to report" in (p.get("note") or ""), json.dumps(p)[:220])

    check("T8202 the response explains which path answered and why it is better",
          "exactly one decoded value" in (r.get("source") or ""), (r.get("source") or "")[:200])

    # ------------------------------------------------------------------ T8203 guards
    print("\n=== T8203: the guards still hold ===")
    notsys = M.raw_post("list_niagara_user_parameters",
                        {"path": "/Engine/EngineMaterials/WorldGridMaterial.WorldGridMaterial"})
    check("T8203 a non-NiagaraSystem is refused by class",
          notsys.get("ok") is False and "not a NiagaraSystem" in (notsys.get("error") or ""),
          (notsys.get("error") or "")[:200])
    nopath = M.raw_post("list_niagara_user_parameters", {})
    check("T8203 a missing path is refused", nopath.get("ok") is False,
          (nopath.get("error") or "")[:180])
    filt = M.raw_post("list_niagara_user_parameters",
                      {"path": path, "nameContains": "zzz_no_such_parameter"})
    check("T8203 a filter that matches nothing returns an empty list, not everything",
          filt.get("ok") is True and filt.get("count") == 0, json.dumps(filt)[:200])

    check("T8203 - the editor is still alive", M.call("self_audit", {}).get("ok") is True,
          "this project's gotchas record a cooked NiagaraSystem killing the editor in PostLoad")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
