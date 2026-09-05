"""Compare set_property on EmitterHandles[N].bIsEnabled against set_niagara_emitter.

THE CLAIM, stated as fact in set_niagara_emitter's own text: set_property is enough to DISABLE an
emitter but not to ENABLE one, because it skips the RefreshFromExternalChanges and
InvalidateCompileResults that the endpoint does (MifBridgeNiagara2.cpp:655). No suite has ever
compared them - audit_cross_endpoint_claims found the claim and that tool exits 0 either way.

Curfew is uncooked and has 25 UNiagaraEmitters with editor data, so the comparison is reachable
here for the first time.

WHAT THIS CAN AND CANNOT SHOW, said up front. Everything below is read back through the bridge, so
it measures what the SYSTEM REPORTS. If set_property leaves the flag true while the compiled state
is stale, list_niagara_emitters may still say enabled - that is exactly the failure the claim
describes, and it means "both report enabled" is NOT proof the two are equivalent. The compile
state is the tell, and set_niagara_emitter reports whether it invalidated.

Scratch only, under /Game/_MifNiagaraEquiv. Saves nothing.
"""
import json
import sys

sys.path.insert(0, r"D:\DDS2SDK\Game\Plugins\MifBridge\tools")
import mifaudit as M
import scratch_confirm as SC

ok, why = M.require_sdk_bridge()
print("target: %s" % why)
if not ok:
    raise SystemExit(2)

srcs = [a["path"] for a in (M.call("find_assets", {"class": "NiagaraEmitter", "limit": 25})
                            .get("assets") or []) if not M.is_scratch_fixture(a)]
if not srcs:
    print("no source emitter - cannot run")
    raise SystemExit(2)

import time
st = int(time.time() % 100000)
SYS = "/Game/_MifNiagaraEquiv/NS_Equiv%d" % st
r = M.call("create_asset", {"path": SYS, "class": "NiagaraSystem"})
print("created %s -> ok=%s" % (SYS, r.get("ok")))
if r.get("ok") is False:
    print(json.dumps(r)[:300]); raise SystemExit(1)

add = M.call("add_niagara_emitter", {"system": SYS, "emitter": srcs[0], "name": "EquivEmitter"})
print("add_niagara_emitter ok=%s index=%s" % (add.get("ok"), add.get("emitterIndex")))
if add.get("ok") is False:
    print(json.dumps(add)[:300])


def state(tag):
    e = M.call("list_niagara_emitters", {"system": SYS})
    rows = e.get("emitters") or []
    row = rows[0] if rows else {}
    print("   %-34s enabled=%-6s  %s" % (tag, row.get("enabled"),
                                         json.dumps({k: v for k, v in row.items()
                                                     if k not in ("name",)})[:120]))
    return row.get("enabled")


print("")
print("baseline:")
base = state("after add_niagara_emitter")

print("")
print("A. DISABLE through each path, then re-enable through set_niagara_emitter to reset:")
p = M.call("set_property", {"objectPath": SYS, "propertyPath": "EmitterHandles[0].bIsEnabled",
                            "value": False})
print("   set_property(False) ok=%s applied=%s verified=%s" % (p.get("ok"), p.get("applied"),
                                                               p.get("verified")))
after_sp_off = state("after set_property False")
M.call("set_niagara_emitter", {"system": SYS, "emitter": "EquivEmitter", "enabled": True})
state("reset via set_niagara_emitter True")

print("")
print("B. ENABLE through set_property, from a disabled start:")
sne = M.call("set_niagara_emitter", {"system": SYS, "emitter": "EquivEmitter", "enabled": False})
print("   set_niagara_emitter(False) ok=%s note=%s"
      % (sne.get("ok"), str(sne.get("compileNote") or "")[:80]))
state("disabled via set_niagara_emitter")
p2 = M.call("set_property", {"objectPath": SYS, "propertyPath": "EmitterHandles[0].bIsEnabled",
                             "value": True})
print("   set_property(True) ok=%s applied=%s verified=%s" % (p2.get("ok"), p2.get("applied"),
                                                              p2.get("verified")))
after_sp_on = state("after set_property True")

print("")
print("C. what set_niagara_emitter reports that set_property does not:")
sne2 = M.call("set_niagara_emitter", {"system": SYS, "emitter": "EquivEmitter", "enabled": True})
extra = {k: v for k, v in sne2.items()
         if k not in ("ok", "endpoint", "elapsedMs", "system", "index")}
print("   %s" % json.dumps(extra)[:400])
print("   set_property's response fields: %s"
      % json.dumps({k: v for k, v in p2.items()
                    if k in ("applied", "verified", "valueBefore", "valueAfter",
                             "packageDirtyRestored")})[:300])

print("")
print("VERDICT INPUTS - not a conclusion, the numbers to reason from:")
print("   set_property(False) left enabled = %r" % after_sp_off)
print("   set_property(True)  left enabled = %r" % after_sp_on)

for path in (SYS,):
    d = SC.confirm_call("delete_asset", {"path": path})
    print("cleanup %s -> %s" % (path, d.get("ok")))
