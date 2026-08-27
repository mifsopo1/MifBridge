# Port allocation

Every localhost port this project's tooling binds or dials, in one place. It exists because a port
collision here does not look like a collision - it looks like a tool that connects, answers, and
quietly talks to the wrong process.

## The map

| Port | Owner | Bound by | How it is set |
|---|---|---|---|
| **8791** | **MifBridge, the primary UE editor** | `UnrealEditor.exe` (this plugin) | `MIF_BRIDGE_PORT`, default 8791 |
| **8792** | **MifBlender**, the Blender addon | `blender.exe` (our addon) | addon preference **and** `MIF_BLENDER_PORT` - two places that must agree |
| 8793 | **MifBlender, DISPLACED** | `blender.exe` (our addon) | fell back here because Curfew held 8792 - see below |
| 9876 | third-party `blender-mcp` | `blender.exe` | that project's default |
| **8801+** | **second, third… UE editor** | `UnrealEditor.exe` | `MIF_BRIDGE_PORT` - **use this range** |
| 8080 | steamwebhelper | unrelated | - |
| 8081 | AMPService | unrelated | - |

`MIF_BRIDGE_PORT` was **unset** at both User and Machine scope when this was written, so any editor
started without it explicitly in its environment binds 8791.

**8793 is not a fourth owner - it is MifBlender in the wrong place.** Curfew took 8792, so our addon
could not bind its configured port and ended up on 8793. That is worth stating precisely, because
"something is listening on 8793" reads like an unrelated program to anyone finding it later, and the
instinct would be to route around it rather than to fix the cause. Verified against the process
(`blender.exe`) and against the record in the feature spec, not inferred from the port number.

## Why a collision is worse than it sounds

The MCP server's `_blender()` dials 8792 and speaks a **length-prefixed binary protocol**. The UE
bridge on 8791 speaks **HTTP**. They are not interchangeable, but they are both *sockets that accept a
connection*, so pointing one at the other does not produce "connection refused" - it produces a
confusing failure much further along, after something has already been half-done.

The reverse is worse. If a UE editor is put on 8792 before Blender starts, MifBlender's addon cannot
bind, and every Blender tool call reaches **that editor** instead. The editor answers. It looks
connected. `docs/06_OPEN_ISSUES_FROM_USE.md` issue 15 is that case, found in Curfew.

## Which side moves

**Move the EDITOR, not the addon.** The addon's port lives in two places that have to agree - its
Blender preference and `MIF_BLENDER_PORT` - so moving it is two changes that can disagree. The
editor's port is one environment variable.

So: **a second editor goes to 8801 or above, and 879x is left alone.**

## The guard

`MifBridge.cpp:69-85` refuses nothing but warns loudly and specifically when configured for 8792 or
9876, naming the real owner and pointing at this range. It warns rather than refuses on purpose: the
port might be free on a machine with no Blender, and a plugin that will not start is worse than one
that says what it suspects.

## Curfew

Curfew (UE 5.7, `D:/RoguelikeDealerGame`) was found on **8792** - MifBlender's port - which is what
prompted issue 15 and this document.

Nothing in Curfew's `Config/*.ini` sets it and `MIF_BRIDGE_PORT` is unset system-wide, so it was
coming from that session's own environment. The recommended value is **8801**. The guard now warns at
startup if it happens again, so this is contained rather than silent even if the environment is not
fixed.

Not changed from here: Curfew is a separate project and its plugin copy is vendored. The
recommendation is recorded and has been passed to the session that works on it.
