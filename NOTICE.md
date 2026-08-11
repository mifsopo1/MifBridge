# Third-party notices

MifBridge itself is MIT licensed — see [`LICENSE`](LICENSE), © 2026 Mifsopo ("Mif").
This file records the third-party work it builds on and the terms that come with it.

---

## blender-mcp — MIT

**Project:** [blender-mcp](https://github.com/MCPBlender/blender-mcp)
**Copyright:** © 2025 Siddharth Ahuja
**Licence:** MIT

`tools/blender-addon/MifBlender/` adapts two patterns from this project:

1. **The socket wire protocol** — a 4-byte big-endian length prefix followed by a UTF-8 JSON body,
   with a size cap and a `recv`-until-complete loop (`framing.py`).
2. **Main-thread job marshalling** — the approach for getting work off a socket thread and onto
   Blender's main thread before touching `bpy`, including the background-mode (`blender -b`) case
   where there is no event loop for timers to fire on (`server.py`).

MIT-to-MIT, so the adaptation is permitted; the copyright notice is carried in the headers of the
adapting files as well as here.

**Deliberately not adapted**, and none of it is present in this repo: the telemetry modules, the
secret store, any third-party generative-service integration, any bundled API keys, and the
project's terms-and-conditions document.

---

## Unreal Engine

The `MifBridge` C++ module links Unreal Engine at build time. The engine is covered by **Epic
Games' Unreal Engine EULA**, not by this repository's MIT licence, and no engine source is
redistributed here.

The `create_editable_child` endpoint additionally calls an engine-side function that exists only in
a cooked-editor engine fork, so that one endpoint requires the fork in order to build.

---

## MifKismetReconstructor — GPL-3.0, not bundled

The 12 `kr_*` endpoints are registered **at runtime** by the separate `MifKismetReconstructor`
plugin (GPL-3.0), through an engine-provided delegate. That plugin is distributed separately, is
not included in this repository, and is not part of this MIT work. MifBridge neither includes nor
links its source.
