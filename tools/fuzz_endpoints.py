"""Adversarial sweep over every endpoint. Finds guards that are not there, and inputs that kill.

Five probes per endpoint, cheapest and safest first:

  1. GUARD      {"__mif_fuzz_key__": 1} and nothing else. A guarded endpoint answers by NAMING the
                unknown key (RejectUnknownParams). Anything else means the key was silently dropped -
                the bug class this codebase treats as its most damaging, because the caller believes
                a parameter took effect when nothing read it.
  2. EMPTY      {}. Whatever happens, the error must say what was required. "failed" is not an answer
                an agent can act on.
  3. WRONGTYPE  every declared key filled with a value of the wrong shape (object where a string
                belongs, array where a number belongs). Looking for unhandled casts, not for refusal -
                refusal is the correct outcome.
  4. ABSURD     empty strings, 64KB strings, huge and negative numbers, deep nesting.
  5. GHOST      well-formed but nonexistent references - a syntactically valid guid that names no
                node, a /Game path that does not exist. The endpoint must say so, not assume.

Classification:
  CRASH        the bridge stopped answering during this call. Highest severity; not retried.
  HANG         no answer inside the timeout.
  BAD_RESPONSE not JSON, from a JSON API.
  UNGUARDED    an unknown key was accepted without comment.
  BAD_ERROR    ok:false with an empty, generic, or self-referential message.
  LEAK         the response carries an assertion, stack frame or file path from the C++ side.

Safety: /Game/_MifAudit* only, confirm never sent, nothing saved, DENY list enforced by mifaudit.
"""
import json
import sys
import time

import mifaudit as M

FUZZ_KEY = "__mif_fuzz_key__"

GHOST_GUID = "DEADBEEF00004444DEADBEEF00004444"

# UNIQUE PER RUN *AND* PER ENDPOINT.
#
# Per-run came first, because a fixed ghost path stopped being a ghost between runs: run 1 created
# /Game/_MifAudit_DoesNotExist/Nope and by run 2 that path resolved to a real Blueprint, so an endpoint
# was reported for correctly answering about an asset that existed.
#
# That fixed the wrong half. The probe hands its ghost to EVERY endpoint including create_blueprint,
# which is supposed to accept a path that does not exist yet - there is a `creates` exclusion below so
# it is not flagged for that, but not being flagged does not stop it creating the asset. And
# endpoint_names() is sorted(), so create_blueprint runs at 'c' and everything after it is handed a
# path that by then really exists.
#
# Run 4 is the proof: six of its seven GHOST_OK findings sit alphabetically after 'c' and were asked
# about a path that existed; only audit_unused at 'a' ran before it. It is also how duplicate_asset was
# handed an EXISTING destination, which is what raised the modal dialog that froze the editor.
GHOST_RUN = int(time.time())


def ghost_path(endpoint):
    """A path no earlier endpoint in this run can have created, because only this one is given it."""
    return "/Game/_MifAuditGhost_%d/%s_Nope" % (GHOST_RUN, endpoint)

# Parameters that name a SET to search rather than a THING to resolve. A prefix or filter that matches
# nothing legitimately returns ok:true with zero results; an identity that resolves to nothing should
# fail. The payload alone cannot tell these apart - both are "ok:true and empty" - but the key that was
# ghosted can, and it matches the hand triage of run 4 exactly: audit_unused and find_assets ghosted
# pathPrefix (correct empties), while describe_package, get_dependencies, get_referencers,
# diff_properties_vs_default and invoke_editor_tab ghosted an identity (real findings).
SEARCH_KEYS = ("prefix", "folder", "contains", "filter", "query", "search", "pattern")


def _is_search_key(k):
    return any(w in k.lower() for w in SEARCH_KEYS)


def looked_and_found_nothing(ghosted_keys, r):
    """True when ok:true means "I searched and found nothing", which is a correct answer.

    Requires BOTH: every ghosted key names a set to search rather than a thing to resolve, AND the
    response carries no actual content. Flagging correct empties made the GHOST_OK bucket mostly noise
    and buried the one finding that mattered - but suppressing on emptiness alone hides the real case,
    an endpoint that resolves a nonexistent identity and answers about it anyway.

    Strings are ignored on purpose: responses routinely echo the path they were asked about, and that
    echo says nothing about whether anything was found.
    """
    if not ghosted_keys or not all(_is_search_key(k) for k in ghosted_keys):
        return False
    payload = {k: v for k, v in r.items()
               if k not in ("ok", "endpoint", "note", "warning", "elapsedMs")}
    for k, v in payload.items():
        if isinstance(v, bool):
            if v:
                return False
        elif isinstance(v, (int, float)):
            if v != 0:
                return False
        elif isinstance(v, (list, dict)):
            if len(v) > 0:
                return False
    return True


WRONG_SHAPES = [
    {"__wrong": "object-where-scalar-expected"},
    ["array-where-scalar-expected"],
    True,
]
ABSURD = ["", "x" * 65536, -2147483648, 2147483647, 1e308, "../../../../etc/passwd",
          "\x00\x01\x02", "%s%s%s%n", "'; DROP TABLE --"]

# A leak is an accident, not a citation. This module deliberately explains engine behaviour by
# pointing at the header that causes it ("TabManager.h:1113-1117"), so matching ".cpp:" flags the
# documentation and misses nothing real. Keep only markers that cannot appear on purpose.
LEAKY = ("Assertion failed", "Unhandled Exception", "EXCEPTION_ACCESS_VIOLATION",
         "Fatal error", "0x00000", "Stack:")

GENERIC_ERRORS = ("", "failed", "error", "failure", "invalid", "bad request", "unknown error")


def looks_leaky(blob):
    return [m for m in LEAKY if m in blob]


def declared_keys(endpoint):
    """Accepted parameter names, from the endpoint's own describe."""
    r = M.call("describe_endpoint", {"endpoint": endpoint}, timeout=60)
    if not isinstance(r, dict):
        return []
    keys = r.get("acceptedParams") or []
    if keys:
        return [k for k in keys if isinstance(k, str)]
    params = r.get("params") or r.get("parameters") or []
    out = []
    for p in params:
        if isinstance(p, dict) and p.get("name"):
            out.append(p["name"])
        elif isinstance(p, str):
            out.append(p)
    return out


def probe(endpoint, label, payload, timeout=45):
    """One call. Returns (status, response_or_none). Never raises.

    A hang is upgraded to "dead" when the bridge is gone afterwards. The killing call does not
    return a connection error - it stops responding while the editor tears itself down, so the
    naive reading blames whichever probe ran NEXT. The first run of this fuzzer reported the crash
    against `2147483647` when the actual killer was the 64KB string two probes earlier.
    """
    try:
        r = M.call(endpoint, payload, timeout=timeout)
        return "ok", r
    except M.Dead:
        return "dead", None
    except M.Timeout:
        alive, _ = M.require_sdk_bridge()
        if not alive:
            return "dead", None          # this probe is the killer, not a later one
        # SLOW IS NOT HUNG. The editor garbage-collects and rescans the asset registry on its own
        # schedule, so a single timeout can be an unlucky moment rather than a property of the call.
        # Confirm once with a longer budget before calling it a hang - an unreproducible hang in a
        # report is worse than no report, because someone then goes looking for a bug that is not there.
        try:
            r = M.call(endpoint, payload, timeout=timeout * 3)
            return "slow", r
        except M.Dead:
            return "dead", None
        except M.Timeout:
            # STILL not necessarily this endpoint's fault. Handlers run inline on the game thread, so
            # a call queues behind whatever the EDITOR is doing - a long GC, an asset-registry rescan,
            # or work an earlier endpoint deferred to a later tick rather than finishing inline. The
            # kr_* endpoints reconstruct blueprints for real even on garbage input and can hold the
            # thread for minutes.
            #
            # This mattered: run 4 reported recipe_reset_and_loop as a hang, and probing it in
            # isolation against an idle editor answered in 0.33s with the exact same payload. The
            # endpoint was fine; the editor was busy. Measure that instead of guessing, by timing a
            # trivial endpoint right afterwards, and carry the number into the finding so nobody goes
            # hunting for a bug that is not there.
            idle_ms = None
            try:
                t0 = time.time()
                M.call("self_audit", {}, timeout=30)
                idle_ms = int((time.time() - t0) * 1000)
            except Exception:
                pass
            return "hang", {"__editor_probe_ms": idle_ms}
    except Exception as e:                                  # harness bug, not an endpoint finding
        return "harness-error", {"error": str(e)}


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None

    ok, why = M.require_sdk_bridge()
    if not ok:
        print("REFUSING TO RUN:", why)
        print("The fuzzer only drives the SDK editor - see mifaudit.require_sdk_bridge.")
        return 2
    print("target:", why)

    names = [n for n in M.endpoint_names() if n not in M.DENY]
    if only:
        names = [n for n in names if only in n]
    print("fuzzing %d endpoints\n" % len(names))

    counts = {}
    crashers = []

    for i, ep in enumerate(names, 1):
        if i % 25 == 0:
            print("  ... %d/%d  (%s)" % (i, len(names), ", ".join(
                "%s=%d" % kv for kv in sorted(counts.items())) or "clean"))

        if not M.ensure_editor():
            print("EDITOR WILL NOT COME BACK - stopping at %s (%d/%d)" % (ep, i, len(names)))
            M.record("run-aborted", ep, "editor did not return after relaunch", severity="critical")
            break

        keys = declared_keys(ep)

        # ---------------------------------------------------------------- 1. GUARD
        status, r = probe(ep, "guard", {FUZZ_KEY: 1})
        if status == "dead":
            M.record("CRASH", ep, "bridge died on an unknown-key probe", severity="critical",
                     probe="guard")
            crashers.append(ep)
            counts["CRASH"] = counts.get("CRASH", 0) + 1
            continue
        if status == "hang":
            M.record("HANG", ep, "no response within timeout on an unknown-key probe",
                     severity="high", probe="guard")
            counts["HANG"] = counts.get("HANG", 0) + 1
        elif status == "ok" and isinstance(r, dict) and not r.get("_denied"):
            blob = json.dumps(r)
            if r.get("_badJson"):
                M.record("BAD_RESPONSE", ep, "non-JSON body from a JSON API", severity="high",
                         probe="guard", sample=r.get("_raw", "")[:200])
                counts["BAD_RESPONSE"] = counts.get("BAD_RESPONSE", 0) + 1
            elif FUZZ_KEY not in blob:
                # The endpoint never mentioned the key it was handed - nothing rejected it.
                M.record("UNGUARDED", ep,
                         "accepted an unknown parameter without naming it; a typo'd or unsupported "
                         "key would be silently ignored here",
                         severity="high", probe="guard",
                         responseOk=bool(r.get("ok")), sample=blob[:220])
                counts["UNGUARDED"] = counts.get("UNGUARDED", 0) + 1
            leaks = looks_leaky(blob)
            if leaks:
                M.record("LEAK", ep, "response carries internal detail: %s" % ", ".join(leaks),
                         severity="medium", probe="guard", sample=blob[:220])
                counts["LEAK"] = counts.get("LEAK", 0) + 1

        # ---------------------------------------------------------------- 2. EMPTY
        status, r = probe(ep, "empty", {})
        if status == "dead":
            M.record("CRASH", ep, "bridge died on an empty payload", severity="critical",
                     probe="empty")
            crashers.append(ep)
            counts["CRASH"] = counts.get("CRASH", 0) + 1
            continue
        if status == "ok" and isinstance(r, dict) and r.get("ok") is False:
            err = (r.get("error") or "").strip()
            if err.lower() in GENERIC_ERRORS or len(err) < 12:
                M.record("BAD_ERROR", ep,
                         "empty payload rejected with an error that does not say what was required: %r"
                         % err, severity="medium", probe="empty")
                counts["BAD_ERROR"] = counts.get("BAD_ERROR", 0) + 1

        # ---------------------------------------------------------------- 3. WRONGTYPE
        if keys:
            for shape in WRONG_SHAPES:
                payload = {k: shape for k in keys if k.lower() not in M.FORBIDDEN_KEYS}
                if not payload:
                    continue
                status, r = probe(ep, "wrongtype", payload)
                if status == "dead":
                    M.record("CRASH", ep, "bridge died with every parameter set to %r" % (shape,),
                             severity="critical", probe="wrongtype")
                    crashers.append(ep)
                    counts["CRASH"] = counts.get("CRASH", 0) + 1
                    break
                if status == "hang":
                    M.record("HANG", ep, "no response with every parameter set to %r" % (shape,),
                             severity="high", probe="wrongtype")
                    counts["HANG"] = counts.get("HANG", 0) + 1
                elif status == "ok" and isinstance(r, dict):
                    blob = json.dumps(r)
                    leaks = looks_leaky(blob)
                    if leaks:
                        M.record("LEAK", ep, "internal detail leaked on a wrong-type payload: %s"
                                 % ", ".join(leaks), severity="medium", probe="wrongtype",
                                 sample=blob[:220])
                        counts["LEAK"] = counts.get("LEAK", 0) + 1
            if ep in crashers:
                continue

        # ---------------------------------------------------------------- 4. ABSURD
        if keys:
            for val in ABSURD:
                payload = {k: val for k in keys if k.lower() not in M.FORBIDDEN_KEYS}
                if not payload:
                    continue
                status, r = probe(ep, "absurd", payload)
                if status == "dead":
                    M.record("CRASH", ep, "bridge died with every parameter set to %r"
                             % (val if not isinstance(val, str) or len(val) < 40
                                else val[:40] + "...(%d chars)" % len(val),),
                             severity="critical", probe="absurd")
                    crashers.append(ep)
                    counts["CRASH"] = counts.get("CRASH", 0) + 1
                    break
                if status == "hang":
                    busy = (r or {}).get("__editor_probe_ms")
                    note = ""
                    if busy is not None:
                        note = ("; a trivial endpoint answered in %dms right after, so the EDITOR was "
                                "responsive and this call specifically was not" % busy) if busy < 2000                             else ("; a trivial endpoint also took %dms right after, so the editor was "
                                  "busy generally - do not blame this endpoint without an isolated "
                                  "re-probe" % busy)
                    M.record("HANG", ep, "no response with parameters set to %r%s"
                             % (val if not isinstance(val, str) or len(val) < 40 else "<64KB string>",
                                note),
                             severity="high", probe="absurd")
                    counts["HANG"] = counts.get("HANG", 0) + 1
            if ep in crashers:
                continue

        # ---------------------------------------------------------------- 5. GHOST
        ghosts = {}
        for k in keys:
            kl = k.lower()
            if kl in M.FORBIDDEN_KEYS:
                continue
            if "guid" in kl or kl in ("node", "nodeid", "src", "dst", "srcnode", "dstnode"):
                ghosts[k] = GHOST_GUID
            elif "path" in kl or kl in ("asset", "blueprintid", "graphid", "material", "level"):
                ghosts[k] = ghost_path(ep)
        if ghosts:
            status, r = probe(ep, "ghost", ghosts)
            if status == "dead":
                M.record("CRASH", ep, "bridge died on well-formed but nonexistent references",
                         severity="critical", probe="ghost")
                crashers.append(ep)
                counts["CRASH"] = counts.get("CRASH", 0) + 1
                continue
            if status == "ok" and isinstance(r, dict):
                blob = json.dumps(r)
                # A CREATION endpoint is supposed to accept a path that does not exist yet - that is
                # its whole job - so "succeeded against a nonexistent path" is the correct answer
                # there, not a finding. Flagging create_blueprint was this probe's own false positive.
                creates = ep.startswith(("create_", "add_", "new_", "spawn_", "import_", "duplicate_"))
                if r.get("ok") is True and not creates and not looked_and_found_nothing(ghosts, r):
                    M.record("GHOST_OK", ep,
                             "reported success for references that do not exist (%s)"
                             % ", ".join(sorted(ghosts)),
                             severity="high", probe="ghost", sample=blob[:220])
                    counts["GHOST_OK"] = counts.get("GHOST_OK", 0) + 1
                leaks = looks_leaky(blob)
                if leaks:
                    M.record("LEAK", ep, "internal detail leaked on nonexistent references: %s"
                             % ", ".join(leaks), severity="medium", probe="ghost", sample=blob[:220])
                    counts["LEAK"] = counts.get("LEAK", 0) + 1

    print("\n" + "=" * 72)
    print("fuzz complete: %d endpoints" % len(names))
    for k in sorted(counts):
        print("  %-14s %d" % (k, counts[k]))
    if crashers:
        print("\nCRASHERS (not retried):")
        for c in crashers:
            print("   ", c)
    print("\nfindings appended to", M.FINDINGS)
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
