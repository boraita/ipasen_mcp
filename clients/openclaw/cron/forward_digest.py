#!/usr/bin/env python3
"""Forward the school digest delivered by OpenClaw cron jobs to more chats. No LLM involved.

A cron job can deliver to a single chat. This script reads the latest runs of the digest jobs and
sends every run that was really delivered (not NO_REPLY) and not forwarded yet, as-is, to each
target. Run it from a command cron a few minutes after each digest job. Idempotent: forwarded
run ids are kept in a state file.

    forward_digest.py --job <cron id> [--job <cron id>] --target <chat id> [--target ...] [--dry-run]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--job", action="append", required=True, help="digest cron job id (repeatable)")
    p.add_argument("--target", action="append", required=True, help="chat id to forward to (repeatable)")
    p.add_argument("--channel", default="telegram", help="OpenClaw channel (default telegram)")
    p.add_argument("--state", default=str(Path.home() / ".config/ipasen-mcp/forwarded.json"), help="forwarded run ids")
    p.add_argument("--max-age-hours", type=float, default=14, help="ignore runs older than this (default 14)")
    p.add_argument("--openclaw", default="", help="openclaw CLI path (default: PATH or ~/.nvm/current/bin)")
    p.add_argument("--dry-run", action="store_true", help="print what would be sent, send nothing")
    args = p.parse_args()

    nvm_bin = Path.home() / ".nvm/current/bin"
    openclaw = args.openclaw or shutil.which("openclaw") or str(nvm_bin / "openclaw")
    # Command crons run with a minimal PATH; the openclaw CLI needs node next to it.
    env = {**os.environ, "PATH": f"{Path(openclaw).parent}:{nvm_bin}:" + os.environ.get("PATH", "/usr/bin:/bin")}

    def oc(*cmd: str) -> str:
        proc = subprocess.run([openclaw, *cmd], capture_output=True, text=True, env=env)
        if proc.returncode:
            sys.exit(f"openclaw {' '.join(cmd[:2])} failed: {(proc.stderr or proc.stdout)[-400:]}")
        return proc.stdout

    state = Path(args.state).expanduser()
    done = set(json.loads(state.read_text())) if state.exists() else set()
    pending = []
    for job in args.job:
        for e in json.loads(oc("cron", "runs", job, "--limit", "5")).get("entries", []):
            fresh = time.time() * 1000 - e.get("ts", 0) < args.max_age_hours * 3600 * 1000
            if fresh and e.get("deliveryStatus") == "delivered" and e.get("summary") and e.get("runId") not in done:
                pending.append(e)

    for e in sorted(pending, key=lambda e: e["ts"]):
        for target in args.target:
            print(("DRY " if args.dry_run else "SEND ") + f"{e['runId']} -> {target}: {e['summary'][:60]!r}")
            if not args.dry_run:
                oc("message", "send", "--channel", args.channel, "--target", target, "--message", e["summary"])
        done.add(e["runId"])

    if not args.dry_run:
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps(sorted(done)[-200:]))
    print(f"forwarded {len(pending)} run(s)")


if __name__ == "__main__":
    main()
