"""Tell a reporter on Discord that their report was worked, and @-mention them.

WHY THIS EXISTS. The loop closes a GitHub issue and posts the outcome there, which is correct and
also invisible: a modder who filed a report from inside the editor is not sitting on the repository's
notification feed. Andre's framing was exactly right - the ping is only useful "if he knows to pull
it", so the message says what changed and that a pull is needed, rather than just "fixed".

OUTWARD-FACING, so it reuses the loop's existing security boundary rather than inventing a second
one. report_trust.json decides who may have a report auto-processed and auto-replied to; the same
file decides who may be pinged. There is deliberately no state where the loop is messaging people
about reports it was not allowed to work on.

FAILS CLOSED AND QUIET. No config file, no webhook, an unmapped login, a network error - every one of
those is "do not notify", never "raise". A report that was fixed and replied to on GitHub must not be
reported as a failure because a courtesy ping did not go out. Everything is logged; nothing is fatal.

CONFIG lives in tools/report_discord.json, which is GITIGNORED and never committed:

    {
      "webhook": "https://discord.com/api/webhooks/...",
      "contacts": { "infectedcoolpat-jpg": "273659879561101343" }
    }

The webhook URL is a credential - anyone holding it can post into that channel as this integration,
so it does not go in git. The contacts map is in the same file rather than a tracked one on purpose:
it links a GitHub login to a Discord account, and publishing that pairing for every contributor to a
public repository is a small thing to hand out for no benefit.

Usage:
    python tools/report_notify.py --issue 2 --author infectedcoolpat-jpg --outcome fixed \\
        --summary "one line of what changed" [--commit <sha>] [--dry-run]
"""
import argparse
import io
import datetime
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "report_discord.json")
TRUST = os.path.join(HERE, "report_trust.json")
REPO = "mifsopo1/MifBridge"


def log(msg):
    print("report_notify: %s" % msg, flush=True)


def load_config():
    """Webhook and contacts, or (None, {}) if this is not set up. Absence is not an error."""
    try:
        cfg = json.load(io.open(CONFIG, encoding="utf-8"))
    except FileNotFoundError:
        log("no report_discord.json - Discord notification is not configured, skipping")
        return None, {}
    except Exception as exc:
        log("report_discord.json is unreadable (%s) - skipping" % exc)
        return None, {}
    hook = cfg.get("webhook") or None
    contacts = {str(k).lower(): str(v) for k, v in (cfg.get("contacts") or {}).items()}
    return hook, contacts


def trusted(login):
    """The SAME gate the intake and the reply use. A missing file means nobody, never everybody."""
    try:
        raw = json.load(io.open(TRUST, encoding="utf-8")).get("trusted") or []
        return str(login).lower() in set(str(x).lower() for x in raw)
    except Exception as exc:
        log("trust file unreadable (%s) - treating nobody as trusted" % exc)
        return False


def compose(issue, author, mention, outcome, summary, commit):
    """The message. Written to be useful to somebody who was not watching the repository."""
    who = "<@%s>" % mention if mention else ("@" + author)
    url = "https://github.com/%s/issues/%s" % (REPO, issue)
    head = {
        "fixed": "%s your report #%s is fixed." % (who, issue),
        "explained": "%s your report #%s turned out not to be a defect - here is why." % (who, issue),
        "needs-you": "%s your report #%s needs something only you can provide." % (who, issue),
    }.get(outcome, "%s update on your report #%s." % (who, issue))

    lines = [head]
    if summary:
        lines.append(summary.strip())
    if outcome == "fixed":
        # THE POINT OF THE PING. A fix sitting in a repository the reporter has not pulled has not
        # reached them, and "fixed" on its own reads as "already working for you".
        lines.append("Pull `master` and rebuild the plugin to pick it up%s."
                     % (" (commit `%s`)" % commit[:9] if commit else ""))
    lines.append("<%s>" % url)
    return "\n".join(lines)


# One colour per outcome, so the card reads before the text does. GitHub's own palette, because the
# card sits next to a GitHub link and matching it is less jarring than inventing a scheme.
_COLOURS = {
    "fixed": 0x2EA043,        # green
    "explained": 0x8957E5,    # purple
    "needs-you": 0xD29922,    # amber
    "update": 0x1F6FEB,       # blue
}

_TITLES = {
    "fixed": "Fixed",
    "explained": "Not a defect",
    "needs-you": "Needs a human",
    "update": "Update",
}


def issue_title(number):
    """The issue's real title, or None. Best effort and never fatal - it is decoration."""
    try:
        out = subprocess.run(
            ["gh", "issue", "view", str(number), "--json", "title", "--jq", ".title"],
            capture_output=True, text=True, timeout=15)
        title = (out.stdout or "").strip()
        return title or None
    except Exception:                                               # noqa: BLE001
        return None


def build_embed(issue, author, outcome, summary, commit=None, title=None):
    """The styled card. Everything except the @-mention, which cannot live here and still ping."""
    url = "https://github.com/%s/issues/%s" % (REPO, issue)
    heading = "#%s - %s" % (issue, _TITLES.get(outcome, "Update"))
    if title:
        heading = "#%s %s" % (issue, title)
    embed = {
        "title": heading[:250],
        "url": url,
        "color": _COLOURS.get(outcome, 0x1F6FEB),
        # Discord caps a description at 4096; reports run long, so this is trimmed rather than
        # rejected as a 400 with nothing delivered.
        "description": (summary or "").strip()[:4000] or "_no summary given_",
        "author": {
            "name": author,
            "url": "https://github.com/%s" % author,
            # A public, stable URL that needs no hosting of our own.
            "icon_url": "https://github.com/%s.png?size=64" % author,
        },
        "footer": {"text": "MifBridge - autonomous report loop"},
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "fields": [],
    }
    if outcome == "fixed":
        embed["fields"].append({
            "name": "What to do",
            "value": "Pull `master` and rebuild the plugin to pick it up%s."
                     % (" (commit `%s`)" % commit[:9] if commit else ""),
            "inline": False,
        })
    elif commit:
        embed["fields"].append({"name": "Commit", "value": "`%s`" % commit[:9], "inline": True})
    embed["fields"].append({"name": "Status", "value": _TITLES.get(outcome, "Update"),
                            "inline": True})
    return embed


def notify(issue, author, outcome, summary, commit=None, dry_run=False, supplied=None):
    if not trusted(author):
        log("%s is not a trusted reporter - not messaging anyone" % author)
        return False
    hook, contacts = load_config()
    if not hook:
        return False
    # THE REPORTER'S OWN ID WINS, because it is the only source that scales. The contacts map has to
    # be filled in by hand for every new person, which means the first report from anyone new can
    # never ping them - and nobody remembers to go back and add them afterwards.
    #
    # It is UNTRUSTED INPUT and is treated as such: a Discord snowflake is a bare decimal id, so
    # anything else is discarded rather than interpolated. That is what stops "@everyone" or
    # "1234> hey @here" arriving in the mention slot. allowed_mentions pins the ping to this exact
    # id as well, so even a valid-looking id can only ever ping the one account it names.
    mention = None
    if supplied is not None:
        s = str(supplied).strip().lstrip("<@!").rstrip(">")
        if s.isdigit() and 5 <= len(s) <= 25:
            mention = s
            log("using the reporter's own Discord id from the report")
        else:
            log("ignoring a malformed `discord` value in the report - not a bare numeric id")
    if not mention:
        mention = contacts.get(str(author).lower())
    if not mention:
        # Send anyway, without a ping. The channel still learns the report was handled, which is
        # more useful than silence - it just cannot tap the one person on the shoulder.
        log("no Discord id mapped for %s - posting without an @-mention" % author)
    # compose() still builds the ONE-STRING version. It is the fallback payload and the thing the
    # dry run prints beneath the card, so the two can never drift into describing different sends.
    body = compose(issue, author, mention, outcome, summary, commit)
    # THE PING LIVES HERE AND ONLY HERE. A <@id> inside an embed renders as a mention and does not
    # notify - Discord fires a notification only for mentions in the message body. Putting the whole
    # message in the card would have looked better and silently stopped pinging the one person the
    # message exists for.
    head = body.splitlines()[0] if body else ""
    embed = build_embed(issue, author, outcome, summary, commit, title=issue_title(issue))

    if dry_run:
        log("DRY RUN, would post:")
        log("  content | " + head)
        log("  embed   | %s" % embed["title"])
        log("  embed   | colour #%06X, author %s" % (embed["color"], embed["author"]["name"]))
        for l in embed["description"].splitlines():
            log("  embed   | " + l)
        for f in embed["fields"]:
            log("  embed   | [%s] %s" % (f["name"], f["value"]))
        return True

    # allowed_mentions restricts pings to the ONE user this is about. Without it a summary that
    # happens to contain @everyone - and the summary is partly derived from a report someone else
    # wrote - would ping the whole server.
    mentions = {"parse": [], "users": [mention] if mention else []}
    payload = {"content": head, "embeds": [embed], "allowed_mentions": mentions}

    def post(body_obj):
        req = urllib.request.Request(
            hook, data=json.dumps(body_obj).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "MifBridge-report-loop"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status

    try:
        log("posted to Discord (HTTP %s)" % post(payload))
        return True
    except urllib.error.HTTPError as exc:
        # THE MESSAGE MATTERS MORE THAN THE CARD. An embed is one more thing that can be rejected -
        # a field too long, a malformed timestamp - and a pretty payload that 400s delivers nothing
        # at all. So the plain string that worked before this change is tried once more.
        log("Discord refused the embed (HTTP %s) - retrying as plain text" % exc.code)
        try:
            log("posted to Discord as plain text (HTTP %s)"
                % post({"content": body, "allowed_mentions": mentions}))
            return True
        except Exception as exc2:                                   # noqa: BLE001
            log("plain-text retry failed too (%s) - the GitHub reply already went out, so this is "
                "cosmetic" % str(exc2)[:90])
    except Exception as exc:
        log("could not reach Discord (%s) - the GitHub reply already went out, so this is cosmetic"
            % str(exc)[:120])
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue", required=True)
    ap.add_argument("--author", required=True)
    ap.add_argument("--outcome", default="fixed",
                    choices=["fixed", "explained", "needs-you", "update"])
    ap.add_argument("--summary", default="")
    ap.add_argument("--commit", default=None)
    ap.add_argument("--discord", default=None,
                    help="the reporter's own Discord id, as carried through from the report")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    ok = notify(a.issue, a.author, a.outcome, a.summary, a.commit, a.dry_run, a.discord)
    # ALWAYS 0. A courtesy ping that did not go out must never fail the pipeline step that calls it.
    log("notified" if ok else "not notified (see above) - not an error")
    return 0


if __name__ == "__main__":
    sys.exit(main())
