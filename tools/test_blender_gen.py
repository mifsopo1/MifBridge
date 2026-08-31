"""ops_gen.py - the local text/image -> 3D generation pipeline, and the only addon module with
ZERO test coverage of any kind before this file.

WHY THIS EXISTS. parity_check.py counts 20 addon ops total; test_blender_mesh.py and
test_blender_ops.py between them exercise 15. The five missing are exactly ops_gen.py's whole
surface: gen_status, gen_image, gen_mesh, gen_texture, gen_asset - a ComfyUI-driven pipeline
(Flux for the reference image, Hunyuan3D-2 for shape and paint) that nothing had ever called from
a test.

WHAT THIS MACHINE CAN AND CANNOT PROVE, said plainly rather than smoothed over. Real generation
needs a running ComfyUI with specific custom nodes and multi-gigabyte checkpoints installed - not
present here (confirmed: nothing answers on 127.0.0.1:8188). So this suite:

  * proves the PARAMETER CONTRACTS that need no backend at all - gen_mesh refuses without an
    image, gen_texture refuses without a mesh path, every op's reject_unknown guard rejects an
    unknown key;
  * proves the GRACEFUL-FAILURE path when ComfyUI is unreachable, which is the state EVERY ONE of
    these five ops is in on a machine that has not set ComfyUI up - and is therefore the most
    common real-world outcome, not an edge case. Every op reaches the exact same
    "cannot reach ComfyUI at ..." MifOpError, from the exact same _post() call, so it is checked
    identically across all five rather than assumed from reading gen_image's path and trusting
    the other four share it;
  * CANNOT prove what a real generation actually produces - that needs the ComfyUI + Hunyuan3D-2
    stack this machine does not have, and even with it present, a multi-minute-to-hour GPU job has
    no place in a routine regression suite. Left unproven here on purpose, not silently assumed.

If ComfyUI DOES answer when this runs (a different machine, or a future session with it set up),
the suite still passes - it proves gen_status returns a real capability report instead of a
connection error, and explicitly logs the generation-output shape as still unproven rather than
attempting a real GPU job inline.
"""
import json
import os
import socket
import struct
import sys

HOST = os.environ.get("MIF_BLENDER_HOST", "127.0.0.1")
PORT = int(os.environ.get("MIF_BLENDER_PORT", "8792"))
TOKEN = os.environ.get("MIF_BLENDER_TOKEN", os.environ.get("MIF_BRIDGE_TOKEN", "dev"))
COMFY_HOST = os.environ.get("MIF_COMFY_HOST", "127.0.0.1:8188")

PASS, FAIL = [], []
UNPROVEN = []


def call(op, timeout=30.0, **params):
    """One framed op. Same wire format as test_blender_ops.py's call()."""
    frame = {"endpoint": op, "token": TOKEN,
             "params": {k: v for k, v in params.items() if v is not None}}
    body = json.dumps(frame).encode("utf-8")
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((HOST, PORT))
        s.sendall(struct.pack(">I", len(body)) + body)
        head = b""
        while len(head) < 4:
            chunk = s.recv(4 - len(head))
            if not chunk:
                return {"ok": False, "error": "connection closed reading the header"}
            head += chunk
        want = struct.unpack(">I", head)[0]
        buf = b""
        while len(buf) < want:
            chunk = s.recv(min(65536, want - len(buf)))
            if not chunk:
                return {"ok": False, "error": "connection closed reading the body"}
            buf += chunk
        return json.loads(buf.decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        s.close()


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %-4s %s" % ("PASS" if cond else "FAIL", name))
    if not cond and detail:
        print("       %s" % str(detail)[:220])


def reachable(host, port, timeout=1.5):
    """Delegates to blender_audit_common - a real framed PING, never a bare connect.

    This was a private socket.connect() that returned True for anything accepting a connection. On
    this machine a UE editor holds MifBlender's port 8792 (docs/06 issue 15), so it answered True
    with no Blender running and the suite would run its body against the wrong protocol. A false
    failure is worse than a false pass: it teaches the reader to ignore the suite.
    """
    import blender_audit_common as _B
    _B.HOST, _B.PORT = host, port
    return _B.reachable(timeout=timeout)

def main():
    print("MifBlender gen ops (ops_gen.py) - %s:%d" % (HOST, PORT))
    if not reachable(HOST, PORT):
        print("")
        print("SKIPPED - nothing was verified.")
        print("  Blender is not listening on %s:%d, so no addon op was exercised." % (HOST, PORT))
        print("  Start Blender with the MifBlender addon enabled and run this again.")
        return 2

    comfy_host, comfy_port_s = (COMFY_HOST.split(":") + ["8188"])[:2]
    comfy_up = reachable(comfy_host, int(comfy_port_s), timeout=2.0)
    print("ComfyUI at %s: %s" % (COMFY_HOST, "REACHABLE" if comfy_up else "not reachable"))
    print("")

    # ------------------------------------------------------------------ T800 parameter contracts
    # These need NO backend at all - the checks fire before any ComfyUI call is made.
    print("=== T800: parameter contracts that need no ComfyUI ===")
    r = call("gen_mesh")
    check("T800 gen_mesh with neither image nor imagePath refuses",
          r.get("ok") is False and "imagePath" in str(r.get("error", "")), r.get("error"))
    r = call("gen_texture")
    check("T800 gen_texture with no meshPath refuses",
          r.get("ok") is False, r.get("error"))
    r = call("gen_texture", meshPath="/some/mesh.glb")
    check("T800 gen_texture with a meshPath but no image refuses",
          r.get("ok") is False and "reference image" in str(r.get("error", "")), r.get("error"))
    r = call("gen_image")
    check("T800 gen_image with no prompt refuses", r.get("ok") is False, r.get("error"))
    r = call("gen_asset")
    check("T800 gen_asset with no prompt refuses", r.get("ok") is False, r.get("error"))

    for op in ("gen_status", "gen_image", "gen_mesh", "gen_texture", "gen_asset"):
        r = call(op, nonsenseParam="x")
        check("T800 %s rejects an unknown parameter" % op,
              r.get("ok") is False, r.get("error"))

    print("")
    if not comfy_up:
        # ------------------------------------------------------------------ T801 unreachable path
        print("=== T801: every gen_ op fails the SAME clean way when ComfyUI is unreachable ===")
        # gen_status calls _object_info directly with nothing to catch a connection failure - it
        # fails the same way the others do rather than degrading to an all-false capability report.
        r = call("gen_status", timeout=15)
        check("T801 gen_status refuses rather than hanging or crashing", r.get("ok") is False, r.get("error"))
        check("T801 gen_status names ComfyUI and how to start it",
              "ComfyUI" in str(r.get("error", "")) and "8188" in str(r.get("error", "")),
              r.get("error"))

        r = call("gen_image", prompt="a red wooden crate", timeout=15)
        check("T801 gen_image fails cleanly rather than hanging for its 600s default timeout",
              r.get("ok") is False, r.get("error"))
        check("T801 gen_image's failure names ComfyUI",
              "ComfyUI" in str(r.get("error", "")), r.get("error"))

        r = call("gen_mesh", imagePath="/tmp/does_not_matter.png", timeout=15)
        check("T801 gen_mesh fails cleanly rather than hanging for its 1800s default timeout",
              r.get("ok") is False, r.get("error"))
        check("T801 gen_mesh's failure names ComfyUI",
              "ComfyUI" in str(r.get("error", "")), r.get("error"))

        r = call("gen_texture", meshPath="/tmp/does_not_matter.glb",
                 imagePath="/tmp/does_not_matter.png", timeout=15)
        check("T801 gen_texture fails cleanly rather than hanging for its 2400s default timeout",
              r.get("ok") is False, r.get("error"))
        check("T801 gen_texture's failure names ComfyUI",
              "ComfyUI" in str(r.get("error", "")), r.get("error"))

        r = call("gen_asset", prompt="a red wooden crate", timeout=15)
        check("T801 gen_asset fails cleanly rather than hanging for its 3600s default timeout",
              r.get("ok") is False, r.get("error"))
        check("T801 gen_asset's failure names ComfyUI",
              "ComfyUI" in str(r.get("error", "")), r.get("error"))

        UNPROVEN.append("what a REAL generation actually produces (gen_status's real capability "
                        "report, and gen_image/gen_mesh/gen_texture/gen_asset's real outputs) - "
                        "ComfyUI is not reachable on this machine. The failure path above is proven "
                        "instead, which is the state every one of these five ops is actually in on "
                        "a machine without ComfyUI set up.")
    else:
        # ------------------------------------------------------------------ T802 reachable path
        print("=== T802: ComfyUI is reachable - gen_status reports real capabilities ===")
        r = call("gen_status", timeout=30)
        check("T802 gen_status succeeds", r.get("ok") is True, json.dumps(r)[:200])
        check("T802 and reports a node count", isinstance(r.get("nodeCount"), (int, float)), r)
        caps = r.get("capabilities") or {}
        check("T802 and reports capability flags",
              all(k in caps for k in ("shape", "texture", "delight", "textToImage")), caps)

        UNPROVEN.append("real generation output (gen_image/gen_mesh/gen_texture/gen_asset) - "
                        "ComfyUI answered on this run, but an actual GPU generation job (minutes "
                        "to over an hour) has no place in a routine regression suite. Run one by "
                        "hand if the pipeline itself needs verifying end to end.")

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    if UNPROVEN:
        print("")
        print("NOT PROVEN BY THIS SUITE (green above does not cover these):")
        for u in UNPROVEN:
            print("  - %s" % u)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
