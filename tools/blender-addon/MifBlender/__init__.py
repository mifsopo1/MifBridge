"""MifBlender -- the Blender backend of MifBridge.

MifBridge is one MCP server with two backends: an Unreal Engine 5.3 C++ plugin
over HTTP on 127.0.0.1:8791, and this Blender addon over a framed-JSON TCP
socket on 127.0.0.1:8792. Same tool namespace, same {ok:true,...} /
{ok:false,error:"..."} response shape on both sides.

  https://github.com/  (MifBridge, MIT, (c) 2026 Mifsopo "Mif")

The socket framing and the main-thread-marshalling pattern are adapted from
blender-mcp (github.com/MCPBlender/blender-mcp), (c) 2025 Siddharth Ahuja,
MIT licence. MIT to MIT, with thanks.

Install: Edit > Preferences > Add-ons > Install..., pick the zip (or this
folder's parent), enable "MifBlender". The server auto-starts. Status and
controls live in the 3D viewport N-panel under the "MifBridge" tab.
"""

bl_info = {
    "name": "MifBlender (MifBridge backend)",
    "author": "Mifsopo",
    "version": (0, 1, 0),
    "blender": (4, 4, 0),
    "location": "View3D > Sidebar (N) > MifBridge",
    "description": "Framed-JSON socket backend so the MifBridge MCP server can drive "
                   "Blender alongside Unreal.",
    "warning": "Exposes a local socket that can import/export files and optionally run "
               "Python. Loopback only, token gated.",
    "category": "Import-Export",
}

# The 4.4 floor is now CONSERVATIVE rather than unverified. Measured 2026-08-27 with
# tools/blender_probe.py and tools/test_blender_ops.py, on four real installs:
#
#   version     imports  registers  33 ops  FBX kwargs  op suite
#   3.6.23        yes      yes        yes    all present   12/12
#   4.2.17 LTS    yes      yes        yes    all present   12/12
#   4.4.0         yes      yes        yes    all present   12/12
#   5.0.1         yes      yes        yes    all present   12/12
#
# The worry this comment used to carry was real but did not bite: the exporter's
# properties DO move between releases -- use_ascii is gone in 4.4 -- but that is one
# this addon never passes. All 17 kwargs in FBX_EXPORT_ARGS, all 3 in FBX_IMPORT_ARGS,
# the 4 enum values (FBX_SCALE_NONE / FACE / SRGB / AUTO) and all 6 bmesh.ops are still
# real on 5.0.1.
#
# Blender 5.0 still ships 25 legacy bl_info addons of its own and still has
# addon_utils.enable, so the extensions manifest is NOT required. Determined by running
# it, not by reading release notes.
#
# The floor stays at 4.4 anyway, and that is a decision rather than an oversight: the op
# SUITE covers set_material_slots and the read ops around it, not the FBX mesh round
# trip. The kwargs are proven present on 3.6; the round trip through them is not proven
# on 3.6. Lower the floor when something exercises it.

import importlib
import os
import sys

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty
from bpy.types import AddonPreferences, Operator, Panel

# Reload submodules cleanly when the addon is re-enabled or hot-reloaded,
# otherwise Blender keeps the stale bytecode and edits appear not to apply.
_SUBMODULES = ("framing", "ops_common", "ops_scene", "ops_mesh", "ops_gen", "ops_rig",
               "ops_lightcam", "ops_anim", "ops_render", "ops_world",
               "ops_physics", "ops_particles", "ops_nodes", "ops_viewport", "server")
for _name in _SUBMODULES:
    _full = "%s.%s" % (__name__, _name)
    if _full in sys.modules:
        importlib.reload(sys.modules[_full])

from . import server as mif_server  # noqa: E402

# Module-level server handle -- do NOT hang this on bpy.types (breaks theme presets).
_server = None


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

class MifBlenderPreferences(AddonPreferences):
    bl_idname = __name__

    port: IntProperty(
        name="Port",
        description="TCP port on 127.0.0.1. The Unreal side of MifBridge uses 8791; "
                    "the unrelated third-party blender-mcp addon uses 9876",
        default=mif_server.DEFAULT_PORT, min=1024, max=65535,
    )
    token: StringProperty(
        name="Token",
        description="Shared secret. Must match MIF_BLENDER_TOKEN on the MCP server "
                    "(which itself defaults to MIF_BRIDGE_TOKEN). Leaving it EMPTY "
                    "disables authentication entirely",
        default="dev", subtype="PASSWORD",
    )
    auto_start: BoolProperty(
        name="Start server automatically",
        description="Start listening as soon as the addon is enabled and on Blender "
                    "launch. Ignored in background mode, where starting would block "
                    "startup forever",
        default=True,
    )
    allow_run_python: BoolProperty(
        name="Allow run_python (arbitrary code execution)",
        description="Lets the run_python op exec arbitrary Python inside Blender with "
                    "your user's privileges. The socket is loopback-only and token "
                    "gated, but turn this off if you do not need the escape hatch",
        default=True,
    )
    job_timeout: IntProperty(
        name="Main-thread job timeout (s)",
        description="How long a request waits for Blender's main thread before it is "
                    "answered with an error. Long bakes and modal operators block the "
                    "main thread. KEEP THIS BELOW the MCP server's work timeout "
                    "(MIF_BLENDER_TIMEOUT, default 180s): whichever end gives up first "
                    "owns the failure, and it should be this one, so the socket carries "
                    "a real error instead of the MCP abandoning a job Blender then goes "
                    "on running against a scene the caller believes untouched",
        default=int(mif_server.DEFAULT_JOB_TIMEOUT), min=5, max=86400,
    )
    verbose: BoolProperty(
        name="Log to console",
        description="Print connection and error lines to Blender's system console",
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.prop(self, "port")
        col.prop(self, "token")
        col.prop(self, "job_timeout")
        layout.prop(self, "auto_start")
        layout.prop(self, "verbose")

        box = layout.box()
        box.prop(self, "allow_run_python")
        if self.allow_run_python:
            box.label(text="run_python can execute any code the MCP client sends.",
                      icon="ERROR")
        if not self.token:
            layout.label(text="Token is empty: any local process can drive Blender.",
                         icon="ERROR")

        row = layout.row(align=True)
        row.operator(MIFBLENDER_OT_start.bl_idname, icon="PLAY")
        row.operator(MIFBLENDER_OT_stop.bl_idname, icon="PAUSE")
        layout.label(text=_status_line())


def prefs(context=None):
    addon = (context or bpy.context).preferences.addons.get(__name__)
    return addon.preferences if addon else None


# ---------------------------------------------------------------------------
# Server control
# ---------------------------------------------------------------------------

def _status_line():
    if _server is None or not _server.running:
        return "MifBlender: stopped"
    # The pid is on the status line because it is the ONLY thing that
    # distinguishes two Blender windows from the outside. bl_status reports the
    # pid of whichever process actually owns the port; if it does not match the
    # one shown here, this Blender is not the one your edits are landing in.
    return "MifBlender: listening on %s:%d, pid %d (%d client%s)" % (
        _server.host, _server.port, os.getpid(), _server._clients,
        "" if _server._clients == 1 else "s")


def start_server(port=None, token=None, job_timeout=None, verbose=None):
    """Start (or restart) the socket server. Safe to call twice."""
    global _server
    settings = prefs()
    if _server is not None and _server.running:
        _server.stop()
    _server = mif_server.MifBlenderServer(
        port=port if port is not None else getattr(settings, "port", mif_server.DEFAULT_PORT),
        token=token if token is not None else getattr(settings, "token", "dev"),
        job_timeout=(job_timeout if job_timeout is not None
                     else getattr(settings, "job_timeout",
                                  mif_server.DEFAULT_JOB_TIMEOUT)),
        verbose=verbose if verbose is not None else getattr(settings, "verbose", True),
    )
    mif_server.ensure_timer()
    return _server.start()


def stop_server():
    global _server
    if _server is not None:
        _server.stop()
    _server = None


def serve_forever(port=None, token=None, job_timeout=None):
    """Headless entry point:  blender -b --python-expr "import MifBlender; MifBlender.serve_forever()"

    BLOCKS. In background mode there is no event loop, so the drain timer never
    fires and the server has to own the main thread. This is also exactly why
    the addon does not auto-start in background: register() would never return.
    """
    if not bpy.app.background:
        raise RuntimeError("serve_forever() is for `blender -b`. In the GUI the server "
                           "already runs on its own thread -- use start_server().")
    start_server(port=port, token=token, job_timeout=job_timeout)


class MIFBLENDER_OT_start(Operator):
    bl_idname = "mifblender.start_server"
    bl_label = "Start MifBlender Server"
    bl_description = "Begin listening for MifBridge MCP connections"

    def execute(self, context):
        if start_server():
            self.report({"INFO"}, _status_line())
            return {"FINISHED"}
        self.report({"ERROR"},
                    "could not bind the port -- see the system console for why")
        return {"CANCELLED"}


class MIFBLENDER_OT_stop(Operator):
    bl_idname = "mifblender.stop_server"
    bl_label = "Stop MifBlender Server"
    bl_description = "Close the listening socket"

    def execute(self, context):
        stop_server()
        self.report({"INFO"}, "MifBlender: stopped")
        return {"FINISHED"}


class VIEW3D_PT_mifblender(Panel):
    bl_label = "MifBlender"
    bl_idname = "VIEW3D_PT_mifblender"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MifBridge"

    def draw(self, context):
        layout = self.layout
        settings = prefs(context)
        running = _server is not None and _server.running

        row = layout.row()
        row.label(text=_status_line(),
                  icon="RADIOBUT_ON" if running else "RADIOBUT_OFF")

        col = layout.column(align=True)
        if running:
            col.operator(MIFBLENDER_OT_stop.bl_idname, text="Stop", icon="PAUSE")
        else:
            col.operator(MIFBLENDER_OT_start.bl_idname, text="Start", icon="PLAY")

        if settings is not None:
            box = layout.box()
            box.label(text="Port: %d (127.0.0.1 only)" % settings.port)
            box.label(text="This Blender's pid: %d" % os.getpid())
            box.label(text="Auth: %s" % ("token" if settings.token else "NONE"),
                      icon="NONE" if settings.token else "ERROR")
            box.label(text="run_python: %s"
                           % ("allowed" if settings.allow_run_python else "blocked"))
        layout.label(text="Ops: %d" % len(mif_server.op_names()))


_CLASSES = (
    MifBlenderPreferences,
    MIFBLENDER_OT_start,
    MIFBLENDER_OT_stop,
    VIEW3D_PT_mifblender,
)


def _deferred_autostart():
    """One-shot, main thread. Deferred so preferences are fully constructed
    before we read them -- reading them inside register() is a coin flip on a
    fresh enable."""
    settings = prefs()
    if settings is None or settings.auto_start:
        start_server()
    return None  # one-shot: do not re-arm


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)

    if bpy.app.background:
        # No event loop: the drain timer would never fire, and auto-starting
        # would block register() forever inside accept(). Headless callers must
        # call MifBlender.serve_forever() themselves.
        print("[MifBlender] background mode: not auto-starting. "
              "Call MifBlender.serve_forever() to serve on this thread.")
        return

    mif_server.ensure_timer()
    bpy.app.timers.register(_deferred_autostart, first_interval=0.1)


def unregister():
    stop_server()
    try:
        if bpy.app.timers.is_registered(_deferred_autostart):
            bpy.app.timers.unregister(_deferred_autostart)
    except Exception:
        pass
    mif_server.remove_timer()
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
