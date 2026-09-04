"""MifBlender ops: local text/image -> 3D generation, driven through ComfyUI.

Nothing here calls a paid API and nothing leaves the machine. The chain is:

    Flux.1              prompt  -> a clean reference image
    Hunyuan3D-2 shape   image   -> an untextured mesh
    Hunyuan3D-2 paint   mesh    -> PBR textures baked from multiview renders
    this module         result  -> imported into the open Blender scene

WHY THIS LIVES IN THE ADDON. Generation used to sit in a mod's tools/ directory, which
meant the mesh was produced somewhere else and imported by hand. Blender is where the
result actually gets inspected and fixed, so the generator belongs on the same side of the
socket as the mesh ops that clean it up.

WHY IT ASKS THE SERVER FOR THE SCHEMA. Node inputs are validated against ComfyUI's own
/object_info rather than hardcoded from reading the custom node source. Custom nodes change
between commits, and a workflow built from stale assumptions fails deep inside the run with
a KeyError on a tensor - long after the interesting part started. Failing at submit time
with "node X has no input Y" is worth the extra request.

TEXTURE IS NOT OPTIONAL FOR A USABLE ASSET. The shape DiT alone returns bare geometry, and
a bare mesh needs a human to author materials before it is worth anything. The paint path
below (delight -> uv wrap -> multiview render -> sample -> bake -> PBR) is what turns a
generation into something that can be dropped straight into a level.

DELIGHT MATTERS MORE THAN IT SOUNDS. Flux renders its reference image with lighting -
speculars, shadows, a key light. Painting straight from that bakes those highlights into
the albedo, and the asset then looks wrong under every other light in the game.
Hy3DDelightImage strips them first. Skipping it is the single most common reason a
generated texture looks "off" in a way nobody can name.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import bpy

from .ops_common import (MifOpError, reject_unknown, take, take_bool, take_float, take_int)

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

DEFAULT_HOST = "127.0.0.1:8188"

# Flux comes in two variants that are NOT interchangeable at the sampler. schnell is
# guidance-distilled: it runs at cfg 1.0, ignores the negative prompt, and is tuned for four
# steps. dev takes a real FluxGuidance node and needs ~20 steps - sampling it at schnell's
# four produces mush, which reads as a bad model rather than a bad configuration.
FLUX = {
    "schnell": {"ckpt": "flux1-schnell-fp8.safetensors", "steps": 4, "guidance": None},
    "dev": {"ckpt": "flux1-dev-fp8.safetensors", "steps": 20, "guidance": 3.5},
}

# The reference image wants to read like a product shot, not a render. A three-quarter
# perspective view produces a subtly sheared mesh that looks like a model failure but is
# really a framing failure, so the framing is part of the prompt rather than advice.
PROMPT_SUFFIX = (
    ", full object centred in frame, orthographic side profile, plain flat background, "
    "even diffuse studio lighting, no dramatic shadows, no motion blur, sharp focus, "
    "single object, entire object visible"
)


def _host(params):
    host = take(params, "host", "server", default=None)
    return str(host or os.environ.get("MIF_COMFY_HOST") or DEFAULT_HOST)


def _post(host, path, payload=None, timeout=60):
    url = "http://%s/%s" % (host.rstrip("/"), path.lstrip("/"))
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:400].decode("utf-8", "replace")
        raise MifOpError(
            "ComfyUI rejected %s: HTTP %s %s. The workflow was refused before anything ran - "
            "usually a node input name that changed in a wrapper update." % (path, exc.code, detail))
    except urllib.error.URLError as exc:
        raise MifOpError(
            "cannot reach ComfyUI at %s (%s). Start it with:\n"
            "    D:\\AI\\ComfyUI\\venv\\Scripts\\python.exe D:\\AI\\ComfyUI\\main.py --port 8188"
            % (host, exc.reason))
    return json.loads(body) if body else {}


def _get(host, path, timeout=60):
    return _post(host, path, None, timeout)


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

_schema_cache = {}


def _object_info(host):
    """ComfyUI's own node schema. Cached per host for the life of the addon session."""
    if host not in _schema_cache:
        _schema_cache[host] = _get(host, "object_info", timeout=120)
    return _schema_cache[host]


def _require_nodes(host, names):
    """Fail at submit time, naming the missing node, rather than deep inside the run."""
    info = _object_info(host)
    missing = [n for n in names if n not in info]
    if missing:
        raise MifOpError(
            "ComfyUI is running but these nodes are not installed: %s. "
            "The Hunyuan3D wrapper provides them - check custom_nodes/ComfyUI-Hunyuan3DWrapper "
            "is present and that ComfyUI logged no import error for it."
            % ", ".join(sorted(missing)))
    return info


def _check_inputs(info, class_type, inputs):
    """Reject an input the installed node does not declare, AND a required one we omit.

    Both halves matter and only the first existed at first. Drift moves in two
    directions: the wrapper can DROP an input we still send (Hy3DGenerateMesh lost
    attention_mode to Hy3DModelLoader) or ADD a required one we do not send
    (DownloadAndLoadHy3DPaintModel and ...DelightModel both gained a required
    'model'). Checking only for unknown inputs caught the first and sailed straight
    past the second, which then failed at ComfyUI queue time -- minutes into the run,
    after the shape stage had already been paid for. Cheap check, expensive omission.
    """
    spec = info.get(class_type, {}).get("input", {})
    required = set((spec.get("required") or {}).keys())
    accepted = set(required)
    accepted.update((spec.get("optional") or {}).keys())
    if not accepted:
        return
    unknown = [k for k in inputs if k not in accepted]
    if unknown:
        raise MifOpError(
            "node '%s' has no input(s) %s. It accepts: %s. The wrapper changed under us - "
            "this is exactly the drift /object_info exists to catch."
            % (class_type, ", ".join(sorted(unknown)), ", ".join(sorted(accepted))))
    absent = sorted(required - set(inputs))
    if absent:
        raise MifOpError(
            "node '%s' requires input(s) %s which this workflow does not set. The wrapper "
            "changed under us - this is exactly the drift /object_info exists to catch."
            % (class_type, ", ".join(absent)))


# ---------------------------------------------------------------------------
# Queue + wait
# ---------------------------------------------------------------------------

def _submit(host, workflow):
    res = _post(host, "prompt", {"prompt": workflow}, timeout=120)
    pid = res.get("prompt_id")
    if not pid:
        raise MifOpError("ComfyUI accepted the request but returned no prompt_id: %s"
                         % json.dumps(res)[:300])
    return pid


def _wait(host, prompt_id, timeout_s):
    """Poll /history. ComfyUI has no completion callback, so polling is the contract."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        hist = _get(host, "history/%s" % prompt_id, timeout=60)
        entry = hist.get(prompt_id)
        if entry:
            status = (entry.get("status") or {})
            if status.get("status_str") == "error" or status.get("completed") is False:
                msgs = json.dumps(status.get("messages") or [])[:600]
                raise MifOpError("ComfyUI run failed: %s" % msgs)
            if entry.get("outputs"):
                return entry
        time.sleep(2.0)
    raise MifOpError(
        "ComfyUI did not finish within %ds. A first run pays for model load (the shape DiT is "
        "2.4GB, Flux fp8 is 16.6GB) - raise 'timeout' and try again before assuming a hang."
        % timeout_s)


def _outputs_of(entry, key):
    """Collect one kind of output across every node that produced it."""
    found = []
    for node_out in (entry.get("outputs") or {}).values():
        for item in (node_out.get(key) or []):
            found.append(item)
    return found


# ---------------------------------------------------------------------------
# Workflow fragments
# ---------------------------------------------------------------------------

def _wf_text_to_image(info, prompt, seed, variant, width, height, steps=None):
    cfg = FLUX.get(variant)
    if cfg is None:
        raise MifOpError("unknown flux variant '%s' - use 'schnell' or 'dev'." % variant)
    steps = steps or cfg["steps"]

    wf = {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": cfg["ckpt"]}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"text": prompt + PROMPT_SUFFIX, "clip": ["1", 1]}},
        # cfg stays 1.0 for BOTH variants: Flux is guidance-distilled, so real
        # classifier-free guidance is not what steers it. dev is steered by FluxGuidance.
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "5": {"class_type": "KSampler",
              "inputs": {"seed": seed, "steps": steps, "cfg": 1.0,
                         "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                         "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
                         "latent_image": ["4", 0]}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
    }
    if cfg["guidance"] is not None:
        wf["7"] = {"class_type": "FluxGuidance",
                   "inputs": {"conditioning": ["2", 0], "guidance": cfg["guidance"]}}
        wf["5"]["inputs"]["positive"] = ["7", 0]
    for nid, node in wf.items():
        _check_inputs(info, node["class_type"], node["inputs"])
    return wf


def _wf_shape(info, image_ref, prefix, seed, steps, octree, guidance):
    """Image -> mesh.

    octree_resolution is the detail dial. 384 is the wrapper's comfortable default; 512
    resolves noticeably more surface without changing the silhouette, and there is no point
    generating detail only for a later remesh to throw it away - so the default here is 512
    and the postprocess below is deliberately conservative.
    """
    wf = {
        "10": {"class_type": "Hy3DModelLoader",
               # attention_mode moved here from Hy3DGenerateMesh in a wrapper update. It is
               # selected on the LOADER now, so setting it on the sampler is a hard reject.
               "inputs": {"model": "hunyuan3d-dit-v2-0-fp16.safetensors",
                          "attention_mode": "sdpa"}},
        "11": {"class_type": "Hy3DGenerateMesh",
               # guidance_scale 5.0 is Tencent's value in both the upstream pipeline and
               # their gradio app; the wrapper defaults to kijai's 5.5.
               "inputs": {"pipeline": ["10", 0], "image": image_ref,
                          "guidance_scale": guidance, "steps": steps, "seed": seed}},
        "12": {"class_type": "Hy3DVAEDecode",
               "inputs": {"vae": ["10", 1], "latents": ["11", 0],
                          "box_v": 1.01, "octree_resolution": octree,
                          "num_chunks": 8000, "mc_level": 0.0,
                          "mc_algo": "mc", "enable_flash_vdm": True}},
        "13": {"class_type": "Hy3DPostprocessMesh",
               "inputs": {"trimesh": ["12", 0], "remove_floaters": True,
                          "remove_degenerate_faces": True, "reduce_faces": False,
                          "max_facenum": 200000, "smooth_normals": False}},
        "14": {"class_type": "Hy3DExportMesh",
               "inputs": {"trimesh": ["13", 0], "filename_prefix": prefix,
                          "file_format": "glb", "save_file": True}},
    }
    for node in wf.values():
        _check_inputs(info, node["class_type"], node["inputs"])
    return wf


def _wf_texture(info, mesh_ref, image_ref, prefix, seed, steps, view_size):
    """Mesh + reference image -> PBR-textured mesh.

    Order is not negotiable: delight the reference first, UV the mesh second, then render
    the mesh from several cameras, sample new views conditioned on the delit reference, bake
    those back down, and finally lift PBR channels out of the bake.
    """
    wf = {
        # Both download nodes gained a REQUIRED 'model' in a wrapper update; an empty
        # inputs dict is now a queue-time rejection, not a "use the default".
        "20": {"class_type": "DownloadAndLoadHy3DDelightModel",
               "inputs": {"model": "hunyuan3d-delight-v2-0"}},
        "21": {"class_type": "Hy3DDelightImage",
               "inputs": {"delight_pipe": ["20", 0], "image": image_ref,
                          "steps": 50, "width": 512, "height": 512,
                          "cfg_image": 1.5, "seed": seed}},
        # 'hunyuan3d-paint-v2-0-turbo' is the faster alternative if bake time ever hurts.
        "22": {"class_type": "DownloadAndLoadHy3DPaintModel",
               "inputs": {"model": "hunyuan3d-paint-v2-0"}},
        "23": {"class_type": "Hy3DMeshUVWrap", "inputs": {"trimesh": mesh_ref}},
        "24": {"class_type": "Hy3DCameraConfig",
               # Six orbit views plus top/bottom-ish elevations. Fewer views leaves seams on
               # whatever face no camera saw; more costs time without adding coverage.
               "inputs": {"camera_azimuths": "0, 90, 180, 270, 0, 180",
                          "camera_elevations": "0, 0, 0, 0, 90, -90",
                          "view_weights": "1, 0.5, 0.5, 0.5, 0.05, 0.05",
                          "camera_distance": 1.45, "ortho_scale": 1.2}},
        "25": {"class_type": "Hy3DRenderMultiView",
               "inputs": {"trimesh": ["23", 0], "camera_config": ["24", 0],
                          "render_size": view_size, "texture_size": 2048}},
        "26": {"class_type": "Hy3DSampleMultiView",
               "inputs": {"pipeline": ["22", 0], "ref_image": ["21", 0],
                          "normal_maps": ["25", 0], "position_maps": ["25", 1],
                          "camera_config": ["24", 0], "view_size": view_size,
                          "steps": steps, "seed": seed}},
        "27": {"class_type": "Hy3DBakeFromMultiview",
               "inputs": {"images": ["26", 0], "renderer": ["25", 2],
                          "camera_config": ["24", 0]}},
        "28": {"class_type": "Hy3DMeshVerticeInpaintTexture",
               "inputs": {"texture": ["27", 0], "mask": ["27", 1], "renderer": ["27", 2]}},
        "29": {"class_type": "CV2InpaintTexture",
               "inputs": {"texture": ["28", 0], "mask": ["28", 1],
                          "inpaint_radius": 3, "inpaint_method": "ns"}},
        "30": {"class_type": "Hy3DApplyTexture",
               "inputs": {"texture": ["29", 0], "renderer": ["27", 2]}},
        "31": {"class_type": "Hy3DExportMesh",
               "inputs": {"trimesh": ["30", 0], "filename_prefix": prefix + "_textured",
                          "file_format": "glb", "save_file": True}},
    }
    for node in wf.values():
        _check_inputs(info, node["class_type"], node["inputs"])
    return wf


# ---------------------------------------------------------------------------
# Import + quality gate
# ---------------------------------------------------------------------------

def _import_glb(path, name_hint):
    if not os.path.isfile(path):
        raise MifOpError(
            "ComfyUI reported success but '%s' is not on disk. If ComfyUI runs on another "
            "machine the file is on ITS filesystem - copy it over, or pass import_result=false."
            % path)
    before = set(bpy.data.objects.keys())
    bpy.ops.import_scene.gltf(filepath=path)
    new = [bpy.data.objects[n] for n in bpy.data.objects.keys() if n not in before]
    meshes = [o for o in new if o.type == "MESH"]
    if meshes and name_hint:
        meshes[0].name = name_hint
    return meshes


def _quality_report(objs):
    """What a generated mesh has to survive before it is worth anyone's time.

    Reported, never enforced: a mesh that fails one of these is often still the best
    starting point available, and silently rejecting it would waste the run. Loud is enough.
    """
    report = []
    for obj in objs:
        mesh = obj.data
        tris = sum(max(0, len(p.vertices) - 2) for p in mesh.polygons)
        dims = tuple(round(d, 4) for d in obj.dimensions)
        report.append({
            "object": obj.name,
            "triangles": tris,
            "vertices": len(mesh.vertices),
            "materials": len(mesh.materials),
            "uvLayers": len(mesh.uv_layers),
            "hasUVs": len(mesh.uv_layers) > 0,
            "dimensions": dims,
            "openEdges": _boundary_edges(mesh),
            "warnings": _warnings(tris, mesh, dims),
        })
    return report


def _boundary_edges(mesh):
    """Count edges used by fewer than two faces - a watertight mesh has none.

    Counted from polygon.edge_keys, which is one pass over the faces. The obvious version -
    for each edge, scan every polygon - is O(edges x faces) and turns a 200k-triangle
    generation into a hang. MeshVertex/MeshEdge also have no link_faces (that is bmesh), so
    there is no cheap adjacency to borrow.
    """
    used = {}
    for poly in mesh.polygons:
        for key in poly.edge_keys:
            used[key] = used.get(key, 0) + 1
    return sum(1 for count in used.values() if count < 2)


def _warnings(tris, mesh, dims):
    out = []
    if not mesh.uv_layers:
        out.append("no UVs - texturing and lightmaps will both fail until it is unwrapped")
    if not mesh.materials:
        out.append("no material - shape-only generation, run gen_texture to paint it")
    if tris > 250000:
        out.append("%d triangles is far above a game budget - decimate before use" % tris)
    if tris < 200:
        out.append("only %d triangles - the shape model probably failed to find an object" % tris)
    if max(dims) <= 0.0:
        out.append("zero-sized bounds - the import produced nothing usable")
    return out


# ---------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------

def op_gen_status(params):
    """Is the generator usable, and what is installed."""
    reject_unknown(params, ("host", "server"), "gen_status")
    host = _host(params)
    info = _object_info(host)

    have = {n: (n in info) for n in (
        "Hy3DModelLoader", "Hy3DGenerateMesh", "Hy3DGenerateMeshMultiView",
        "Hy3DVAEDecode", "Hy3DExportMesh", "DownloadAndLoadHy3DPaintModel",
        "DownloadAndLoadHy3DDelightModel", "Hy3DMeshUVWrap", "Hy3DRenderMultiView",
        "Hy3DSampleMultiView", "Hy3DBakeFromMultiview", "Hy3DApplyTexture",
        "CheckpointLoaderSimple")}

    ckpts = []
    try:
        ckpts = ((info.get("CheckpointLoaderSimple", {}).get("input", {})
                  .get("required", {}).get("ckpt_name") or [[]])[0])
    except Exception:  # noqa: BLE001
        pass
    shapes = []
    try:
        shapes = ((info.get("Hy3DModelLoader", {}).get("input", {})
                   .get("required", {}).get("model") or [[]])[0])
    except Exception:  # noqa: BLE001
        pass

    return {
        "ok": True,
        "host": host,
        "nodeCount": len(info),
        "capabilities": {
            "shape": have["Hy3DGenerateMesh"],
            "shapeMultiView": have["Hy3DGenerateMeshMultiView"],
            "texture": have["DownloadAndLoadHy3DPaintModel"] and have["Hy3DBakeFromMultiview"],
            "delight": have["DownloadAndLoadHy3DDelightModel"],
            "textToImage": have["CheckpointLoaderSimple"] and bool(ckpts),
        },
        "nodesPresent": have,
        "checkpoints": list(ckpts)[:20],
        "shapeModels": list(shapes)[:20],
    }


def op_gen_image(params):
    """Prompt -> reference image, left in ComfyUI's output folder."""
    reject_unknown(params, ("prompt", "seed", "variant", "width", "height", "steps",
                            "host", "server", "timeout"), "gen_image")
    host = _host(params)
    prompt = take(params, "prompt", required=True)
    seed = take_int(params, "seed", default=0)
    variant = str(take(params, "variant", default="schnell"))
    width = take_int(params, "width", default=1024)
    height = take_int(params, "height", default=1024)
    steps = take_int(params, "steps", default=None)
    timeout = take_int(params, "timeout", default=600)

    info = _require_nodes(host, ("CheckpointLoaderSimple", "KSampler", "VAEDecode"))
    wf = _wf_text_to_image(info, prompt, seed, variant, width, height, steps)
    wf["99"] = {"class_type": "SaveImage",
                "inputs": {"images": ["6", 0], "filename_prefix": "MifGen/ref"}}
    entry = _wait(host, _submit(host, wf), timeout)
    images = _outputs_of(entry, "images")
    return {"ok": True, "images": images, "variant": variant, "seed": seed,
            "steps": steps or FLUX[variant]["steps"]}


def op_gen_mesh(params):
    """Reference image -> untextured mesh. Accepts a ComfyUI image ref or a local file."""
    reject_unknown(params, ("image", "imagePath", "prefix", "seed", "steps", "octree",
                            "guidance", "host", "server", "timeout", "importResult",
                            "import_result", "name"), "gen_mesh")
    host = _host(params)
    prefix = str(take(params, "prefix", default="MifGen/mesh"))
    seed = take_int(params, "seed", default=0)
    steps = take_int(params, "steps", default=30)
    octree = take_int(params, "octree", default=512)
    guidance = take_float(params, "guidance", default=5.0)
    timeout = take_int(params, "timeout", default=1800)
    do_import = take_bool(params, "importResult", "import_result", default=True)
    name = take(params, "name", default=None)

    image = take(params, "image", default=None)
    image_path = take(params, "imagePath", default=None)
    if not image and not image_path:
        raise MifOpError("gen_mesh needs 'image' (a ComfyUI image name) or 'imagePath' "
                         "(a file on this machine). Use gen_asset to go from a prompt.")

    info = _require_nodes(host, ("Hy3DModelLoader", "Hy3DGenerateMesh",
                                 "Hy3DVAEDecode", "Hy3DExportMesh"))
    if image_path:
        image = _upload_image(host, str(image_path))
    wf = _wf_shape(info, [str(image), 0] if not isinstance(image, list) else image,
                   prefix, seed, steps, octree, guidance)
    # LoadImage feeds the shape node when the caller supplied a filename.
    if not isinstance(image, list):
        wf["9"] = {"class_type": "LoadImage", "inputs": {"image": str(image)}}
        wf["11"]["inputs"]["image"] = ["9", 0]

    entry = _wait(host, _submit(host, wf), timeout)
    meshes = _outputs_of(entry, "mesh") or _outputs_of(entry, "result")
    path = _first_path(meshes)
    out = {"ok": True, "meshOutputs": meshes, "path": path,
           "octree": octree, "steps": steps, "seed": seed}
    if do_import and path:
        objs = _import_glb(path, name)
        out["imported"] = [o.name for o in objs]
        out["quality"] = _quality_report(objs)
    return out


def op_gen_texture(params):
    """Existing mesh + reference image -> PBR textures baked on."""
    reject_unknown(params, ("meshPath", "mesh_path", "image", "imagePath", "prefix",
                            "seed", "steps", "viewSize", "host", "server", "timeout",
                            "importResult", "import_result", "name"), "gen_texture")
    host = _host(params)
    mesh_path = take(params, "meshPath", "mesh_path", required=True)
    prefix = str(take(params, "prefix", default="MifGen/mesh"))
    seed = take_int(params, "seed", default=0)
    steps = take_int(params, "steps", default=15)
    view_size = take_int(params, "viewSize", default=512)
    timeout = take_int(params, "timeout", default=2400)
    do_import = take_bool(params, "importResult", "import_result", default=True)
    name = take(params, "name", default=None)

    image = take(params, "image", default=None)
    image_path = take(params, "imagePath", default=None)
    if not image and not image_path:
        raise MifOpError("gen_texture needs the reference image the mesh was generated "
                         "from ('image' or 'imagePath') - the painter conditions on it.")

    info = _require_nodes(host, ("DownloadAndLoadHy3DPaintModel", "Hy3DMeshUVWrap",
                                 "Hy3DRenderMultiView", "Hy3DSampleMultiView",
                                 "Hy3DBakeFromMultiview", "Hy3DApplyTexture",
                                 "Hy3DExportMesh", "Hy3DUploadMesh"))
    if image_path:
        image = _upload_image(host, str(image_path))

    # Hy3DUploadMesh takes a name from ComfyUI's INPUT dir, not a path - so a mesh that
    # exists on disk has to be uploaded first. A value that is NOT an existing file is
    # passed straight through, so a caller who already knows an input-dir name still works.
    mesh_ref = str(mesh_path)
    if os.path.isfile(mesh_ref):
        mesh_ref = _upload_mesh(host, mesh_ref)

    wf = {"5": {"class_type": "Hy3DUploadMesh", "inputs": {"mesh": mesh_ref}},
          "9": {"class_type": "LoadImage", "inputs": {"image": str(image)}}}
    wf.update(_wf_texture(info, ["5", 0], ["9", 0], prefix, seed, steps, view_size))
    entry = _wait(host, _submit(host, wf), timeout)
    meshes = _outputs_of(entry, "mesh") or _outputs_of(entry, "result")
    path = _first_path(meshes)
    out = {"ok": True, "meshOutputs": meshes, "path": path, "steps": steps, "seed": seed}
    if do_import and path:
        objs = _import_glb(path, name)
        out["imported"] = [o.name for o in objs]
        out["quality"] = _quality_report(objs)
    return out


def op_gen_asset(params):
    """Prompt -> reference image -> mesh -> PBR texture -> imported into the scene.

    The one call that produces something usable. Set texture=false to stop at geometry.
    """
    reject_unknown(params, ("prompt", "name", "seed", "variant", "steps", "shapeSteps",
                            "textureSteps", "octree", "guidance", "texture", "width",
                            "height", "host", "server", "timeout", "importResult",
                            "import_result"), "gen_asset")
    host = _host(params)
    prompt = take(params, "prompt", required=True)
    name = take(params, "name", default=None)
    seed = take_int(params, "seed", default=0)
    variant = str(take(params, "variant", default="schnell"))
    want_texture = take_bool(params, "texture", default=True)
    do_import = take_bool(params, "importResult", "import_result", default=True)
    timeout = take_int(params, "timeout", default=3600)

    prefix = "MifGen/%s" % (str(name) if name else "asset")

    img = op_gen_image({"prompt": prompt, "seed": seed, "variant": variant,
                        "width": take_int(params, "width", default=1024),
                        "height": take_int(params, "height", default=1024),
                        "host": host, "timeout": timeout})
    image_name = _first_name(img.get("images"))
    if not image_name:
        raise MifOpError("the text-to-image stage produced no image; nothing to build from.")

    mesh = op_gen_mesh({"image": image_name, "prefix": prefix, "seed": seed,
                        "steps": take_int(params, "shapeSteps", default=30),
                        "octree": take_int(params, "octree", default=512),
                        "guidance": take_float(params, "guidance", default=5.0),
                        "host": host, "timeout": timeout,
                        "importResult": do_import and not want_texture, "name": name})

    result = {"ok": True, "prompt": prompt, "seed": seed, "image": image_name,
              "shape": {"path": mesh.get("path")}}
    if not want_texture:
        result["imported"] = mesh.get("imported")
        result["quality"] = mesh.get("quality")
        return result

    if not mesh.get("path"):
        raise MifOpError("the shape stage produced no mesh file, so there is nothing to texture.")
    tex = op_gen_texture({"meshPath": mesh["path"], "image": image_name, "prefix": prefix,
                          "seed": seed,
                          "steps": take_int(params, "textureSteps", default=15),
                          "host": host, "timeout": timeout,
                          "importResult": do_import, "name": name})
    result["textured"] = {"path": tex.get("path")}
    result["imported"] = tex.get("imported")
    result["quality"] = tex.get("quality")
    return result


# ---------------------------------------------------------------------------
# helpers that need the HTTP layer
# ---------------------------------------------------------------------------

def _upload_image(host, path):
    """A local image file -> ComfyUI's input dir. Returns the name to use in LoadImage."""
    return _upload_file(host, path, "image")


def _upload_mesh(host, path):
    """A local mesh file -> ComfyUI's input dir. Returns the name to use in Hy3DUploadMesh.

    Hy3DUploadMesh's 'mesh' input is an ENUM over ComfyUI's INPUT directory (/object_info
    reports {"mesh": [[]]} on a clean install), so handing it an absolute path - e.g. the
    one Hy3DExportMesh just wrote into the OUTPUT dir - is an unconditional HTTP 400 at
    queue time. That made gen_texture unusable for every realistic input, and gen_asset
    with it, since gen_asset routes through op_gen_texture.

    /upload/image is not image-only despite the name: verified 2026-08-15 by uploading a
    9.3 MB .glb, which returned {"name": "...glb", "type": "input"} and landed in the
    input dir. It is ComfyUI's generic ingest endpoint.
    """
    return _upload_file(host, path, "mesh")


def _upload_file(host, path, kind):
    """ComfyUI's /upload/image is multipart, not JSON - hand-rolled to avoid a dependency.

    ONE implementation for both images and meshes: the two differed only in the error
    string, and a second hand-rolled multipart body is a second place for the boundary
    handling to be wrong. The form FIELD stays "image" for both - that is the field name
    the endpoint requires, regardless of what the bytes are.
    """
    if not os.path.isfile(path):
        raise MifOpError("no such %s: %s" % (kind, path))
    boundary = "----MifBlender%d" % int(time.time())
    name = os.path.basename(path)
    with open(path, "rb") as handle:
        blob = handle.read()
    body = b"".join([
        ("--%s\r\n" % boundary).encode(),
        ('Content-Disposition: form-data; name="image"; filename="%s"\r\n' % name).encode(),
        b"Content-Type: application/octet-stream\r\n\r\n", blob, b"\r\n",
        ("--%s\r\n" % boundary).encode(),
        b'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n',
        ("--%s--\r\n" % boundary).encode(),
    ])
    req = urllib.request.Request(
        "http://%s/upload/image" % host.rstrip("/"), data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            res = json.loads(resp.read() or b"{}")
    except urllib.error.URLError as exc:
        raise MifOpError("uploading '%s' to ComfyUI failed: %s" % (path, exc))
    return res.get("name") or name


def _first_name(items):
    for item in (items or []):
        if isinstance(item, dict) and item.get("filename"):
            return item["filename"]
        if isinstance(item, str):
            return item
    return None


def _first_path(items):
    for item in (items or []):
        if isinstance(item, dict):
            for key in ("path", "fullpath", "filename"):
                if item.get(key):
                    return item[key]
        elif isinstance(item, str):
            return item
    return None


OPS = {
    "gen_status": op_gen_status,
    "gen_image": op_gen_image,
    "gen_mesh": op_gen_mesh,
    "gen_texture": op_gen_texture,
    "gen_asset": op_gen_asset,
}
