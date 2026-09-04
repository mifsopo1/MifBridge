"""Is this package legal and complete to sell on Fab? Measured against the actual agreement.

WHY A TOOL AND NOT A CHECKLIST. A checklist is read once, on the day it is written, by the person who
wrote it. Every clause below is a condition on the CONTENTS OF THE ZIP, and the zip is rebuilt on
every release - so the only checklist worth having is one that runs. The clause numbers are quoted so
a reader can go and disagree with the reading rather than having to trust it.

THE SOURCE IS THE LIVE AGREEMENT, fetched 2026-09-04 from https://www.fab.com/distribution-agreement
(last updated 23 February 2026), not a remembered summary. Three clauses do the work:

  3(f)(v)   "content distributed through Fab is licensed only under the Fab End User License
            Agreement, which is not superseded by custom licenses included in Content's distributed
            files"
  3(f)(vi)  "content must not use third-party software licensed under GPL, LGPL, EPL, MSPL, or other
            licenses that would directly or indirectly require that all or part of the asset be
            governed under any terms other than the Fab End User License Agreement"
  3(g)(i)   Submission Materials and Content must not "violate, infringe, or misappropriate any
            copyright, trademark, trade secret, trade dress, patent, publicity, privacy, or other
            right of any person or entity"

  18(k)(ii) "When your Content is created using Generative AI Programs, you are required to tag the
            Content as 'CreatedWithAI'" - where "a material portion of the Content is generated"
  3(b)      "If your Content is made available to end users through channels other than the
            Marketplaces, you will provide any Updates to Epic no later than you provide them to any
            other third party."

WHAT IT CANNOT JUDGE, PRINTED EVERY RUN. Whether an asset infringes, whether a portion is "material",
and whether the listing text is accurate are human judgements. This tool measures the things that ARE
mechanical - what is in the zip, what licence text it carries, whose trademarks it names, whether the
declared state matches the built state - and says plainly which questions it left to a person. A
green run here is not permission to publish; it is the removal of the mechanical reasons not to.

NOT LEGAL ADVICE. Reading a contract is not practising law and this file does not pretend otherwise.
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATE = os.path.join(HERE, "fab_listing.json")

# --------------------------------------------------------------------------------------------
# THE PATTERNS, and why each is written the way it is.

# 3(f)(vi) names five licences. The LGPL carve-out in 3(c) - "unless you are merely dynamically
# linking a shared library" - is deliberately NOT encoded: deciding whether a link is dynamic is not
# something a regex can do, and a tool that quietly granted itself an exception would be worse than
# one that reports the hit and makes a person answer.
# TWO PATTERNS, BECAUSE A DISCLAIMER IS NOT A DEPENDENCY. The first version of this check was one
# regex over the licence NAMES, and it blocked the package on LICENSE, NOTICE.md and README.md -
# three files whose GPL sentences all say the same thing: "MifBridge does not include, link, or
# redistribute any GPL-licensed code". That is the compliance statement itself. Blocking a release on
# it is the exact failure that teaches people to pass --force, and once they have, the check is off
# for the real case too.
#
# So the blocking half matches the OPERATIVE GRANT - the sentence a file carries when it really is
# licensed that way - and the reporting half matches the names and never blocks on its own.
COPYLEFT_HEADER = re.compile(
    rb"This program is free software"
    rb"|under the terms of the GNU (?:General Public|Affero|Lesser)"
    rb"|licen[cs]ed under the (?:GNU )?(?:GPL|AGPL|LGPL)"
    rb"|SPDX-License-Identifier:\s*(?:GPL|AGPL|LGPL|EPL|MS-PL|CC-BY-SA)"
    rb"|under the terms of the Eclipse Public License")

COPYLEFT_MENTION = re.compile(
    rb"GNU General Public License"
    rb"|GNU Affero General Public License"
    rb"|GNU Lesser General Public License"
    rb"|\bAGPL\b|\bLGPL\b|\bGPLv[23]\b|GPL-[23]\.0"
    rb"|Eclipse Public License"
    rb"|Microsoft Public License|\bMS-PL\b"
    rb"|Creative Commons Attribution-ShareAlike|\bCC BY-SA\b")

# A LICENCE GRANT IN THE PAYLOAD, which 3(f)(v) says does not supersede the Fab EULA. The danger is
# not that the file wins - it does not - but that the BUYER READS IT AND BELIEVES IT. An MIT header
# inside a paid plugin tells a buyer they may redistribute it publicly, which is the one thing the
# sale depends on them not doing. Matched on the operative granting sentence rather than the word
# "MIT", because "MIT" appears in ordinary prose and the grant does not.
LICENSE_GRANT = re.compile(
    rb"Permission is hereby granted, free of charge"           # MIT / X11
    rb"|Redistribution and use in source and binary forms"     # BSD
    rb"|Licensed under the Apache License"
    rb"|is licensed under the MIT [Ll]icen[cs]e"
    rb"|THE SOFTWARE IS PROVIDED \"AS IS\"")

# 3(g)(i). Third-party game IP this plugin was developed against. Deliberately NOT a general
# trademark scanner - it names the one body of third-party IP that is actually all over this repo,
# because a scanner that flagged every capitalised word would be ignored within a day.
THIRD_PARTY_IP = re.compile(rb"DrugDealerSimulator\w*|DDS2\w*", re.I)

# Credentials. report_discord.json is gitignored and has never been in a zip; this checks anyway,
# because the cost of being wrong once is somebody else's Discord.
SECRETS = re.compile(
    rb"discord\.com/api/webhooks/\d"          # a real webhook has digits; the docstring placeholder is /...
    rb"|ghp_[A-Za-z0-9]{30,}"
    rb"|github_pat_[A-Za-z0-9_]{30,}"
    rb"|xox[baprs]-[A-Za-z0-9-]{10,}"
    rb"|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----")

TEXTUAL = (".cpp", ".h", ".cs", ".py", ".md", ".txt", ".json", ".ini", ".uplugin", ".js", ".ts")

# EXTENSIONLESS FILES, BY NAME, AND THIS IS NOT A DETAIL. The first run of this tool reported
# "3(f)(v) OK - no licence grant is shipped inside the payload" while the payload contained a file
# called exactly `LICENSE` holding the full MIT grant. It has no extension, so the tuple above never
# matched it, so it was never read, so the one check whose entire purpose is to find that file
# reported the absence of what it had not looked for. A false OK on a compliance check is worse than
# no check at all, because it is quoted afterwards as evidence.
TEXTUAL_NAMES = ("LICENSE", "LICENCE", "NOTICE", "COPYING", "AUTHORS", "README", "CHANGELOG")


def load_state():
    """The declared listing state, or an empty one. Missing is a finding, not a crash."""
    try:
        return json.load(io.open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def head_commit():
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True)
        return (out.stdout or "").strip() or None
    except Exception:
        return None


def scan(zpath):
    """Every textual member of the zip, decoded once. Returns {name: bytes}."""
    z = zipfile.ZipFile(zpath)
    members = {}
    for n in z.namelist():
        if n.endswith("/"):
            continue
        base = n.rsplit("/", 1)[-1]
        if not (n.lower().endswith(TEXTUAL)
                or base.upper().split(".")[0] in TEXTUAL_NAMES):
            continue
        try:
            members[n] = z.read(n)
        except Exception:
            continue
    return z.namelist(), members


# --------------------------------------------------------------------------------------------
# THE CHECKS. Each returns (verdict, blocking, lines) where verdict is OK / FINDING / UNKNOWN.
#
# UNKNOWN IS A FIRST-CLASS ANSWER and not a synonym for OK. "There is no evidence of a problem" and
# "I checked and there is no problem" are different sentences, and a readiness report that prints the
# first while meaning the second is how a package ships with an unanswered question in it.

def check_copyleft(names, members):
    headers = sorted(n for n, b in members.items() if COPYLEFT_HEADER.search(b))
    mentions = sorted(n for n, b in members.items()
                      if COPYLEFT_MENTION.search(b) and n not in headers)
    if headers:
        return "FINDING", True, (
            ["3(f)(vi) forbids this outright: 'content must not use third-party software licensed",
             "under GPL, LGPL, EPL, MSPL'. These carry an actual licence GRANT, not a mention:"]
            + ["  %s" % n for n in headers[:20]]
            + ["",
               "%d further file(s) merely NAME a copyleft licence; not blocking:" % len(mentions)]
            + ["  %s" % n for n in mentions[:10]])
    lines = ["no copyleft licence GRANT in %d textual file(s) - nothing here is licensed under one"
             % len(members)]
    if mentions:
        lines += [
            "%d file(s) NAME a copyleft licence without being under one. Read them once, then stop"
            % len(mentions),
            "worrying: in this package they ARE the compliance statement, saying that",
            "MifKismetReconstructor (GPL-3.0) is reached through a delegate and is not bundled:"]
        lines += ["  %s" % n for n in mentions[:10]]
    return "OK", False, lines


def check_license_grant(names, members):
    hits = sorted(n for n, b in members.items() if LICENSE_GRANT.search(b))
    if not hits:
        return "OK", False, ["no licence grant is shipped inside the payload"]
    return "FINDING", True, (
        ["3(f)(v): what ships through Fab is licensed under the Fab EULA, and a licence file inside",
         "the payload does NOT supersede it. The risk is the buyer believing it - an MIT grant tells",
         "them they may redistribute the plugin publicly, which is the one thing the sale depends on",
         "them not doing. Replace with a short notice that points at the Fab EULA."]
        + ["  %s" % n for n in hits[:20]])


def check_third_party_ip(names, members):
    per = {}
    for n, b in members.items():
        c = len(THIRD_PARTY_IP.findall(b))
        if c:
            per[n] = c
    if not per:
        return "OK", False, ["no third-party game IP named in the payload"]
    worst = sorted(per.items(), key=lambda kv: -kv[1])[:10]
    return "FINDING", True, (
        ["3(g)(i): Content must not infringe another party's trademark. This package names the game",
         "it was developed against %d time(s) across %d file(s). Comments are a presentation problem;"
         % (sum(per.values()), len(per)),
         "a string the PLUGIN EMITS AT RUNTIME is a buyer-facing one. Both need clearing before a",
         "commercial listing - run this with --ip-detail to see which is which."]
        + ["  %-58s %d" % (n, c) for n, c in worst])


def check_secrets(names, members):
    hits = sorted(n for n, b in members.items() if SECRETS.search(b))
    if not hits:
        return "OK", False, ["no webhook, token, or private key in the payload"]
    return "FINDING", True, ["A CREDENTIAL IS IN THE ZIP. Rotate it, then fix the packaging:"] + \
        ["  %s" % n for n in hits]


def check_ai_tag(state):
    """18(k)(ii). A decision, and this checks it was MADE, not that it was made correctly."""
    v = state.get("createdWithAI")
    if v is True:
        return "OK", False, [
            "declared CreatedWithAI:true - 18(k)(ii) requires the tag where a material portion is",
            "generated. Tick the box in the Fab portal; this file only records the decision."]
    if v is False:
        why = state.get("createdWithAIReason") or ""
        if not why:
            return "FINDING", True, [
                "declared CreatedWithAI:false with no reason. 18(k)(ii) is not a preference - if a",
                "material portion of this plugin was written by a generative model the tag is",
                "REQUIRED. Record why it does not apply in createdWithAIReason, or set it true."]
        return "OK", False, ["declared CreatedWithAI:false because: %s" % why]
    return "FINDING", True, [
        "18(k)(ii) requires Content created with generative AI to be tagged 'CreatedWithAI', where",
        "'a material portion of the Content is generated with Generative AI Programs'. No decision",
        "is recorded in tools/fab_listing.json. This tool cannot judge 'material' - a person must.",
        "Set createdWithAI true or false, and if false say why in createdWithAIReason."]


def check_update_parity(state):
    """3(b), and the only check here that is about a WORKFLOW rather than a file.

    It cannot see GitHub, so it does not pretend to. What it CAN do is compare the commit the
    listing was last published from against HEAD, which is the number that decides whether the
    obligation is currently outstanding.
    """
    pub = state.get("publishedCommit")
    head = head_commit()
    if not state.get("otherChannels"):
        return "UNKNOWN", False, [
            "3(b) binds only if the plugin is available to end users through another channel. No",
            "channels are declared in fab_listing.json - if the GitHub repo is readable by anyone",
            "who uses the plugin, or the SDK installer bundles it, list them under otherChannels so",
            "this can be measured instead of assumed."]
    if not pub:
        return "FINDING", True, [
            "channels declared (%s) but no publishedCommit recorded, so how far Fab has fallen"
            % ", ".join(state["otherChannels"]),
            "behind cannot be measured. 3(b) requires Epic gets updates NO LATER than any other",
            "third party."]
    if head and pub == head:
        return "OK", False, ["Fab is published from HEAD (%s) - parity holds" % pub[:12]]
    if not head:
        return "UNKNOWN", False, ["git could not be read, so parity could not be measured"]
    try:
        n = subprocess.run(["git", "rev-list", "--count", "%s..HEAD" % pub], cwd=ROOT,
                           capture_output=True, text=True)
        behind = (n.stdout or "").strip()
    except Exception:
        behind = "?"
    return "FINDING", False, [
        "Fab is published from %s; HEAD is %s - %s commit(s) ahead." % (pub[:12], head[:12], behind),
        "3(b) is only breached once those commits reach an end user through another channel. If the",
        "repo is private to you alone, this is a to-do; if anyone else can pull it, it is overdue."]


def check_completeness(names, members, state):
    """3(f)(i): "products must be complete, fully functional as advertised upon submission"."""
    lines, verdict = [], "OK"
    want = ("README.md",)
    missing = [w for w in want if not any(n.endswith("/" + w) or n == w for n in names)]
    if missing:
        verdict = "FINDING"
        lines.append("missing from the payload: %s" % ", ".join(missing))
    else:
        lines.append("README.md is present")
    if not state.get("listingPrice"):
        verdict = "FINDING"
        lines.append("no listingPrice recorded. 1(d) allows $0.00 or >= $0.99, at your complete")
        lines.append("discretion - but a listing needs one chosen and this file is where it lives.")
    else:
        lines.append("listing price declared: %s" % state["listingPrice"])
    return verdict, verdict == "FINDING", lines


def ip_detail(members):
    """Which of the third-party-IP hits are inside a string the plugin EMITS.

    A regex cannot parse C++, so this is deliberately conservative: it reports a line as
    runtime-visible only when the reference sits inside a double-quoted literal on that line and the
    line is not a comment. Anything it is unsure about is listed separately rather than cleared,
    because "probably a comment" is not a thing to put in a compliance report.
    """
    emitted, commented, unsure = [], [], []
    for n, b in sorted(members.items()):
        if not n.lower().endswith((".cpp", ".h", ".cs", ".py", ".js", ".ts")):
            continue
        for i, raw in enumerate(b.decode("utf-8", "replace").splitlines(), 1):
            if not THIRD_PARTY_IP.search(raw.encode("utf-8", "replace")):
                continue
            stripped = raw.strip()
            is_comment = stripped.startswith(("//", "*", "/*", "#"))
            in_quotes = any(THIRD_PARTY_IP.search(m.encode("utf-8", "replace"))
                            for m in re.findall(r'"([^"]*)"', raw))
            row = (n, i, stripped[:120])
            if in_quotes and not is_comment:
                emitted.append(row)
            elif is_comment and not in_quotes:
                commented.append(row)
            else:
                unsure.append(row)
    return emitted, commented, unsure


def selftest():
    """Each check shown firing AND staying quiet. Returns True on failure, like the audits."""
    bad = []

    def expect(label, got, want):
        if got != want:
            bad.append("%s: got %s, expected %s" % (label, got, want))

    # ---- 3(f)(vi) copyleft: a GRANT blocks, a MENTION does not ------------------------------
    grant = {"x/COPYING": b"This program is free software: you can redistribute it"}
    expect("copyleft/grant", check_copyleft(list(grant), grant)[0], "FINDING")
    expect("copyleft/grant-blocks", check_copyleft(list(grant), grant)[1], True)
    # The exact false failure that blocked a release: a file SAYING it is not GPL.
    disclaim = {"x/LICENSE": b"MifBridge does not include, link, or redistribute any "
                             b"GPL-licensed code. The GNU General Public License does not apply."}
    expect("copyleft/disclaimer", check_copyleft(list(disclaim), disclaim)[0], "OK")
    expect("copyleft/disclaimer-quiet", check_copyleft(list(disclaim), disclaim)[1], False)
    expect("copyleft/clean", check_copyleft([], {})[0], "OK")

    # ---- 3(f)(v) a licence grant in the payload ---------------------------------------------
    mit = {"x/LICENSE": b"MIT License\n\nPermission is hereby granted, free of charge, to any person"}
    expect("grant/mit", check_license_grant(list(mit), mit)[0], "FINDING")
    prose = {"x/README.md": b"This plugin used to be MIT. It is not any more."}
    expect("grant/prose", check_license_grant(list(prose), prose)[0], "OK")

    # THE EXTENSIONLESS BUG, PINNED. scan() decides what the checks above ever see, so a check
    # cannot catch this on its own - the file has to reach it. Asserted on the NAME, because that
    # is what was broken: `LICENSE` has no extension and was skipped by the suffix tuple.
    import tempfile
    import zipfile
    tmp = os.path.join(tempfile.mkdtemp(prefix="fabself-"), "t.zip")
    with zipfile.ZipFile(tmp, "w") as z:
        z.writestr("Pkg/LICENSE", "Permission is hereby granted, free of charge, to any person")
        z.writestr("Pkg/Binary.uasset", b"\x00\x01\x02")
    names, members = scan(tmp)
    if "Pkg/LICENSE" not in members:
        bad.append("scan/extensionless: LICENSE was not read, so 3(f)(v) cannot see it")
    if "Pkg/Binary.uasset" in members:
        bad.append("scan/binary: a .uasset was read as text")
    expect("grant/through-scan", check_license_grant(names, members)[0], "FINDING")

    # ---- 3(g)(i) third-party IP --------------------------------------------------------------
    ip = {"x/a.cpp": b'FString S = TEXT("/DDS2Casino/Foo");'}
    expect("ip/present", check_third_party_ip(list(ip), ip)[0], "FINDING")
    expect("ip/absent", check_third_party_ip([], {})[0], "OK")

    # ---- credentials --------------------------------------------------------------------------
    live = {"x/c.json": b'{"webhook": "https://discord.com/api/webhooks/13985/abcdefg"}'}
    expect("secret/live", check_secrets(list(live), live)[0], "FINDING")
    # The placeholder in report_notify.py's docstring must NOT fire, or the check gets ignored.
    ph = {"x/d.py": b'"webhook": "https://discord.com/api/webhooks/..."'}
    expect("secret/placeholder", check_secrets(list(ph), ph)[0], "OK")

    # ---- 18(k)(ii) the AI tag -----------------------------------------------------------------
    expect("ai/undecided", check_ai_tag({})[0], "FINDING")
    expect("ai/true", check_ai_tag({"createdWithAI": True})[0], "OK")
    expect("ai/false-bare", check_ai_tag({"createdWithAI": False})[0], "FINDING")
    expect("ai/false-reasoned",
           check_ai_tag({"createdWithAI": False, "createdWithAIReason": "hand-written"})[0], "OK")

    # ---- 3(b) update parity -------------------------------------------------------------------
    # No declared channel is UNKNOWN, not OK: the obligation may exist and nothing here can see it.
    expect("parity/no-channels", check_update_parity({})[0], "UNKNOWN")
    expect("parity/channel-no-commit",
           check_update_parity({"otherChannels": ["github"]})[0], "FINDING")

    # ---- 3(f)(i) completeness -----------------------------------------------------------------
    expect("complete/no-readme", check_completeness([], {}, {"listingPrice": "$29.99"})[0],
           "FINDING")
    expect("complete/no-price",
           check_completeness(["P/README.md"], {}, {})[0], "FINDING")
    expect("complete/both",
           check_completeness(["P/README.md"], {}, {"listingPrice": "$29.99"})[0], "OK")

    for b in bad:
        print("  FAILED  %s" % b)
    if bad:
        print("")
        print("%d self-check(s) failed. A readiness tool that cannot prove its own checks fire is"
              % len(bad))
        print("not evidence of anything.")
        return True
    print("  every check fires on a planted defect and stays quiet on a clean one")
    print("  including the two that were WRONG when written: the extensionless LICENSE that made")
    print("  3(f)(v) report a false OK, and the GPL disclaimer that made 3(f)(vi) block a release")
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--zip", help="the package to judge; default: the newest *-fab.zip in tools/dist")
    ap.add_argument("--check", action="store_true", help="exit 1 on any blocking finding")
    ap.add_argument("--selftest", action="store_true",
                    help="prove every check can both fire and stay quiet, then exit")
    ap.add_argument("--ip-detail", action="store_true",
                    help="split the third-party-IP hits into runtime-visible and comment-only")
    args = ap.parse_args()

    if args.selftest:
        print("fab_readiness --selftest")
        return 1 if selftest() else 0

    zpath = args.zip
    if not zpath:
        dist = os.path.join(HERE, "dist")
        cands = sorted((f for f in os.listdir(dist) if f.endswith("-fab.zip")),
                       reverse=True) if os.path.isdir(dist) else []
        if not cands:
            print("no --zip given and no *-fab.zip in tools/dist. Build one first:")
            print("    python tools/make_release.py --fab")
            return 2
        zpath = os.path.join(dist, cands[0])
    if not os.path.isfile(zpath):
        print("no such package: %s" % zpath)
        return 2

    names, members = scan(zpath)
    state = load_state()

    print("fab_readiness: %s" % os.path.basename(zpath))
    print("  %d entries, %d textual file(s) read" % (len(names), len(members)))
    print("  clauses from the Fab Distribution Agreement of 23 Feb 2026")
    print("")

    checks = [
        ("3(f)(vi) copyleft",     check_copyleft(names, members)),
        ("3(f)(v)  own licence",  check_license_grant(names, members)),
        ("3(g)(i)  third-party",  check_third_party_ip(names, members)),
        ("--       credentials",  check_secrets(names, members)),
        ("18(k)(ii) AI tag",      check_ai_tag(state)),
        ("3(b)     parity",       check_update_parity(state)),
        ("3(f)(i)  complete",     check_completeness(names, members, state)),
    ]

    blocking = 0
    for label, (verdict, blocks, lines) in checks:
        print("  %-22s %s" % (label, verdict))
        for ln in lines:
            print("      %s" % ln)
        print("")
        if blocks:
            blocking += 1

    if args.ip_detail:
        emitted, commented, unsure = ip_detail(members)
        print("  THIRD-PARTY IP, SPLIT BY WHO SEES IT")
        print("    %d line(s) inside an emitted string - a BUYER can see these:" % len(emitted))
        for n, i, s in emitted[:30]:
            print("      %s:%d  %s" % (n, i, s))
        print("    %d line(s) in comments only - a presentation problem, not a runtime one"
              % len(commented))
        print("    %d line(s) this could not classify - read them by hand, they are not cleared"
              % len(unsure))
        for n, i, s in unsure[:15]:
            print("      %s:%d  %s" % (n, i, s))
        print("")

    # REACH, not green. Every audit in this repo prints how much of its surface it can judge, and
    # this one judges less of its subject than most: the contract has 19 sections and this reads 7
    # clauses of them, all of which happen to be conditions on the bytes in a zip.
    print("  REACH - what this tool did NOT judge, and a person must:")
    print("    - whether any asset actually infringes (3(g)(i) is broader than one name)")
    print("    - whether the AI-generated portion is 'material' under 18(k)(ii)")
    print("    - whether the listing text matches what the product does (3(f)(i))")
    print("    - everything in sections 4-19 that is not about the contents of the package")
    print("    A clean run removes the mechanical reasons not to publish. It is not permission.")

    if args.check and blocking:
        print("")
        print("BLOCKING: %d check(s) must be cleared before this package can be listed." % blocking)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
