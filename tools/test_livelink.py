"""LiveLink: push_livelink_transform, describe_livelink_subject.

Reopened 2026-08-28 as the concrete follow-up to the honest flag left in the LevelSnapshots entry the
same night ("LiveLink's 'needs external data source' reasoning should be treated as UNVERIFIED, not
confirmed"). Traced ILiveLinkClient end to end and found it needs neither real capture hardware nor a
Blueprint virtual subject - it is a plain IModularFeature with a synchronous ForceTick() explicitly
documented for driving LiveLink outside the normal engine tick. No MIF_WITH_LIVELINK compile guard:
everything used here lives in LiveLinkInterface, an unconditional engine RUNTIME module (like
GeometryFramework/GeometryCore), not the optional LiveLink plugin - the gate is a runtime check
(IsModularFeatureAvailable) instead.

A real C++ bug caught by the build, not by reasoning: the first version declared
`FLiveLinkSubjectName SubjectName(FName(*SubjectNameStr));`, which MSVC parses as a function
DECLARATION (the classic "most vexing parse") rather than object construction - confirmed by the
compiler's own error naming the inferred type a function pointer. Fixed with brace-init.

T1300-T1303: the real round trip. Push a transform, read it back exactly (not just ok:true). Push
again under the SAME subject name and confirm it cleanly replaces rather than accumulating garbage
(LiveLinkClient's own existing behavior, not something this file added).

T1304-T1307: refusals checked for the specific reason - a subject that was never pushed, missing
subjectName, unknown parameter.

T1308-T1310: push/read genuinely works DURING PIE too, at Andre's direct ask for live PIE endpoint
testing - real start_pie/stop_pie, not simulated.

T1311-T1312: THE REAL FINDING, and a correction of an earlier wrong conclusion caught by this suite
itself. Manual curl-by-curl testing (push, start PIE, check - each step several real seconds apart)
first looked like a subject going invalid specifically on the editor<->PIE transition. An earlier
version of this automated suite, running the same sequence back-to-back with far less elapsed time,
did NOT reproduce that - the inconsistency was the tell that something else was going on. Traced to
FLiveLinkSubject::GetState() (LiveLinkSubject.cpp): a subject reads invalid once
FApp::GetCurrentTime() - GetLastPushTime() exceeds ULiveLinkSettings::
GetTimeWithoutFrameToBeConsiderAsInvalid() (default 0.5 seconds) - a plain wall-clock staleness
timeout LiveLink applies to every subject, built for continuously-streaming data, with NO connection
to PIE at all. T1311 proves the real mechanism directly (push, sleep past 0.5s, confirm invalid - no
PIE involved). T1312 proves PIE genuinely doesn't matter to it either way (push during PIE, read back
immediately - valid; the same push read again just past the threshold - invalid), closing out the
original wrong hypothesis on the same evidence that first suggested it.
"""
import json
import sys
import time

import mifaudit as M


PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def wait_for_pie_state(target, timeout=30):
    # ONE definition now lives in mifaudit (2026-08-30). This suite had its own copy whose polls used
    # raw_post's 60s default inside a 30s outer budget, so the budget was never enforced and an
    # expiry raised an uncaught mifaudit.Timeout. That was fixed in test_pie_family.py on 2026-08-29
    # and not here - the copies are the reason, so they are gone.
    return M.wait_for_pie_state(target, timeout=timeout)

def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    st = int(time.time() % 100000)
    subject = "MifTestSubject%d" % st

    # ------------------------------------------------------------------ T1300-T1303 real round trip
    print("\n=== T1300-T1303: push and read back a real transform, then update in place ===")
    pushed = M.call("push_livelink_transform", {
        "subjectName": subject, "locationX": 100, "locationY": 200, "locationZ": 50, "rotationYaw": 45})
    check("T1300 push succeeds", pushed.get("ok") is True, json.dumps(pushed)[:200])
    check("T1300 isValid true after ForceTick", pushed.get("isValid") is True, pushed)

    desc = M.call("describe_livelink_subject", {"subjectName": subject})
    check("T1301 describe succeeds", desc.get("ok") is True, json.dumps(desc)[:200])
    t = desc.get("transform") or {}
    check("T1301 the pushed transform round-trips exactly",
          t.get("locationX") == 100 and t.get("locationY") == 200 and t.get("locationZ") == 50
          and t.get("rotationYaw") == 45, t)

    updated = M.call("push_livelink_transform", {"subjectName": subject, "locationX": 500})
    check("T1302 a second push under the SAME name succeeds", updated.get("ok") is True, updated)
    desc2 = M.call("describe_livelink_subject", {"subjectName": subject})
    t2 = desc2.get("transform") or {}
    check("T1303 it cleanly REPLACED the frame - locationX is now 500, not stacked or averaged",
          t2.get("locationX") == 500, t2)
    check("T1303 unspecified fields reset to identity default, not carried over from the first push",
          t2.get("locationY") == 0 and t2.get("rotationYaw") == 0, t2)

    # ------------------------------------------------------------------ T1304-T1307 refusals
    print("\n=== T1304-T1307: refusals checked for the specific reason ===")
    missing = M.call("describe_livelink_subject", {"subjectName": "MifNeverPushed%d" % st})
    check("T1304 a subject that was never pushed is refused", missing.get("ok") is False, missing)

    no_name = M.call("push_livelink_transform", {})
    check("T1305 missing subjectName is refused on push", no_name.get("ok") is False, no_name)

    no_name2 = M.call("describe_livelink_subject", {})
    check("T1306 missing subjectName is refused on describe", no_name2.get("ok") is False, no_name2)

    bad_param = M.call("push_livelink_transform", {"subjectName": subject, "speed": 5})
    check("T1307 unknown parameter is rejected", bad_param.get("ok") is False, bad_param)
    check("T1307 rejection names the unrecognised key", "speed" in (bad_param.get("error") or ""),
          bad_param.get("error"))

    # ------------------------------------------------------------------ T1308 staleness in the plain editor
    print("\n=== T1308: the real mechanism - a subject goes invalid ~0.5s after its last push, no PIE involved ===")
    stale_subject = "MifStaleSubject%d" % st
    fresh = M.call("push_livelink_transform", {"subjectName": stale_subject, "locationX": 1})
    check("T1308 push succeeds", fresh.get("ok") is True and fresh.get("isValid") is True, fresh)
    time.sleep(0.7)  # past ULiveLinkSettings' default 0.5s TimeWithoutFrameToBeConsiderAsInvalid
    gone_stale = M.call("describe_livelink_subject", {"subjectName": stale_subject})
    check("T1308 the SAME subject reads invalid ~0.7s later - staleness, not a bug",
          gone_stale.get("ok") is False, gone_stale)
    refreshed = M.call("push_livelink_transform", {"subjectName": stale_subject, "locationX": 2})
    check("T1308 pushing again immediately makes it valid again",
          refreshed.get("ok") is True and refreshed.get("isValid") is True, refreshed)

    # ------------------------------------------------------------------ T1309-T1312 real PIE testing
    print("\n=== T1309-T1312: push/read genuinely work during PIE, and staleness behaves identically there ===")
    pie_subject = "MifPieSubject%d" % st
    # start_pie/stop_pie are in mifaudit's own DENY list - a guard against a BLIND sweep starting
    # PIE, not against this: a deliberate, narrowly-scoped, immediately-paired start/stop, exactly
    # the documented exception. M.raw_post is the correct bypass, same as scratch_confirm elsewhere.
    started = M.raw_post("start_pie", {})
    check("T1309 start_pie accepted", started.get("ok") is True, started)
    running_status = wait_for_pie_state("running")
    check("T1309 PIE actually reached state=running", running_status.get("state") == "running",
          running_status)

    if running_status.get("state") == "running":
        pie_push = M.call("push_livelink_transform", {"subjectName": pie_subject, "locationX": 7})
        check("T1310 push during PIE works and reads back valid immediately",
              pie_push.get("ok") is True and pie_push.get("isValid") is True, pie_push)

        time.sleep(0.7)
        pie_stale = M.call("describe_livelink_subject", {"subjectName": pie_subject})
        check("T1311 the SAME staleness rule applies during PIE - invalid ~0.7s later, same as the editor",
              pie_stale.get("ok") is False, pie_stale)

        M.raw_post("stop_pie", {})
        stopped_status = wait_for_pie_state("stopped")
        check("T1312 PIE actually reached state=stopped", stopped_status.get("state") == "stopped",
              stopped_status)

        if stopped_status.get("state") == "stopped":
            back_in_editor = M.call("push_livelink_transform", {"subjectName": pie_subject, "locationX": 9})
            check("T1312 push/read works cleanly back in the editor after PIE stops",
                  back_in_editor.get("ok") is True and back_in_editor.get("isValid") is True, back_in_editor)
    else:
        check("T1309-T1311 (skipped) PIE never reached running - cannot test the transition", True,
              running_status)

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
