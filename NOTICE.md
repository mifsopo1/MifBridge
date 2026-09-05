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

**The full MIT permission notice, reproduced as that licence requires.** Until 2026-09-04 this
section named the licence and gave the copyright line but did not carry the notice itself, which MIT
asks for in "all copies or substantial portions". That was a gap while MifBridge was MIT and is not
one to carry into a paid product:

> MIT License
>
> Copyright (c) 2025 Siddharth Ahuja
>
> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
> associated documentation files (the "Software"), to deal in the Software without restriction,
> including without limitation the rights to use, copy, modify, merge, publish, distribute,
> sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all copies or
> substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
> NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
> NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
> DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT
> OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

**The reasoning changed on 2026-09-04 and the conclusion did not.** This used to read "MIT-to-MIT, so
the adaptation is permitted", which stopped being the reason when MifBridge became proprietary. MIT
permits use in a proprietary work - it grants the right to sublicense and to sell - on the condition
that the notice above travels with it. It does, here and in the headers of the adapting files. What
changed is that the condition now carries the whole weight, so it is written out rather than
summarised.

**Deliberately not adapted**, and none of it is present in this repo: the telemetry modules, the
secret store, any third-party generative-service integration, any bundled API keys, and the
project's terms-and-conditions document.

---

## Unreal Engine

The `MifBridge` C++ module links Unreal Engine at build time. The engine is covered by **Epic
Games' Unreal Engine EULA**, not by this repository's licence, and no engine source is
redistributed here.

The `create_editable_child` endpoint additionally calls an engine-side function that exists only in
a cooked-editor engine fork, so that one endpoint requires the fork in order to build.

---

## MifKismetReconstructor — GPL-3.0, not bundled

The 12 `kr_*` endpoints are registered **at runtime** by the separate `MifKismetReconstructor`
plugin (GPL-3.0), through an engine-provided delegate. That plugin is distributed separately, is
not included in this repository, and is not part of this MIT work. MifBridge neither includes nor
links its source.
