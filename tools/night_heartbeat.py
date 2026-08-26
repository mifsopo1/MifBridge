"""Heartbeat + deadline for the overnight autonomous run.

Two files, both under ~/.claude so they are outside the repo:

  AUTOPILOT_UNTIL             epoch-ms deadline, read by the stop hook and by the resumer.
  MIFBRIDGE_NIGHT_HEARTBEAT   epoch-ms of the last sign of life from the working session.

The heartbeat exists to stop TWO sessions driving one editor. A scheduled resumer that fires while the
main session is mid-build would have two processes killing and relaunching the same editor and writing
the same files. So the resumer checks this first: fresh means someone is working, and it exits.

STALE_SECONDS is deliberately generous. A single build plus an editor relaunch plus a suite run is
comfortably ten minutes with nothing wrong, so anything tighter would declare a healthy session dead
and start a second one - the exact failure it exists to prevent.
"""
import os
import sys
import time

HOME = os.path.expanduser("~")
UNTIL = os.path.join(HOME, ".claude", "AUTOPILOT_UNTIL")
BEAT = os.path.join(HOME, ".claude", "MIFBRIDGE_NIGHT_HEARTBEAT")
STALE_SECONDS = 1500          # 25 minutes


def _read_ms(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except Exception:
        return 0


def touch():
    with open(BEAT, "w") as f:
        f.write(str(int(time.time() * 1000)))


def deadline_passed():
    d = _read_ms(UNTIL)
    return d == 0 or d <= time.time() * 1000


def hours_left():
    d = _read_ms(UNTIL)
    return max(0.0, (d - time.time() * 1000) / 3600000.0)


def heartbeat_age_seconds():
    b = _read_ms(BEAT)
    return 10 ** 9 if b == 0 else (time.time() * 1000 - b) / 1000.0


def status():
    return {
        "deadlinePassed": deadline_passed(),
        "hoursLeft": round(hours_left(), 2),
        "heartbeatAgeSeconds": round(heartbeat_age_seconds(), 1),
        "someoneWorking": heartbeat_age_seconds() < STALE_SECONDS,
    }


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "touch":
        touch()
        print("heartbeat touched")
    else:
        import json
        print(json.dumps(status(), indent=1))
