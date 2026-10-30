#!/usr/bin/env python3
"""Install (or update) the iPasen MCP server into OpenClaw. Idempotent; standard library only.

Run it on the OpenClaw host, as the user that runs the gateway:

    IPASEN_USER=... IPASEN_PASSWORD=... python3 install.py --agent main
    python3 install.py --agent main --telegram-chat-id 123456789      # enable actions
    python3 install.py --name ipasen_partner --agent partner --deny-agent main   # second account

Credentials are read from IPASEN_USER / IPASEN_PASSWORD (never from argv, so they do not show up
in `ps`) or from an existing credentials file. They are stored in
~/.config/ipasen-mcp/<name>/credentials.env (mode 600), never in openclaw.json.

Actions (absences, messages, signatures...) are only enabled with --telegram-chat-id: the
confirmation code then goes to that Telegram chat and the agent never sees it. Without it the
server is installed read-only. See CLIENTS.md for the reasoning.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import date
from pathlib import Path

REPO_RAW = "https://raw.githubusercontent.com/boraita/AI/{ref}/mcp/education/ipasen/"
FILES = ["server.py", "requirements.txt", "ACTIONS.md", "README.md", "CLIENTS.md", "clients/openclaw/SKILL.template.md"]
ACTIONS = ["report_absence", "delete_absence_report", "justify_absence", "delete_justification", "answer_authorization",
           "sign_document", "decline_document", "update_message", "tag_message", "send_message", "reply_message",
           "request_tutorship", "cancel_tutorship", "answer_tutorship", "mark_observation", "manage_pickup_person"]
HOME = Path.home()


def step(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd: list[str], check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, **(env or {})})
    if check and proc.returncode:
        fail(f"{' '.join(cmd[:4])}… failed:\n{proc.stdout[-1500:]}{proc.stderr[-1500:]}")
    return proc


def openclaw(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return run([OPENCLAW, *args], check=check)


def openclaw_config() -> dict:
    path = Path(os.getenv("OPENCLAW_CONFIG_PATH", HOME / ".openclaw" / "openclaw.json"))
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as e:
        fail(f"cannot read {path}: {e}")


def fetch_sources(args, install_dir: Path) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        target = install_dir / Path(name).name
        if args.source:
            shutil.copyfile(Path(args.source) / name, target)
        else:
            with urllib.request.urlopen(REPO_RAW.format(ref=args.ref) + name, timeout=30) as r:
                target.write_bytes(r.read())


def python_ok(exe: str) -> bool:
    return bool(exe) and run([exe, "-c", "import sys; sys.exit(sys.version_info < (3, 10))"], check=False).returncode == 0


def make_venv(install_dir: Path) -> Path:
    venv = install_dir / "venv"
    py = venv / "bin" / "python"
    candidates = [shutil.which(p) for p in ("python3.13", "python3.12", "python3.11", "python3.10", "python3")]
    base = next((c for c in candidates if python_ok(c)), None)
    uv = shutil.which("uv") or (str(HOME / ".local/bin/uv") if (HOME / ".local/bin/uv").exists() else None)
    if not py.exists():
        if base and run([base, "-m", "venv", str(venv)], check=False).returncode == 0:
            pass
        elif uv:
            shutil.rmtree(venv, ignore_errors=True)
            run([uv, "venv", "-q", *(["--python", base] if base else ["--python", "3.12"]), str(venv)])
        else:
            fail("need Python >= 3.10 with venv/ensurepip, or `uv` (curl -LsSf https://astral.sh/uv/install.sh | sh)")
    req = str(install_dir / "requirements.txt")
    if run([str(py), "-m", "pip", "--version"], check=False).returncode == 0:
        run([str(py), "-m", "pip", "install", "-q", "-r", req])
    elif uv:
        run([uv, "pip", "install", "-q", "-r", req], env={"VIRTUAL_ENV": str(venv)})
    else:
        fail("the venv has no pip and `uv` is not installed")
    return py


def write_settings(path: Path, updates: dict) -> None:
    """Merge KEY=value pairs into the credentials file, keeping it private."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    lines = path.read_text().splitlines() if path.exists() else []
    keys_done = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in updates:
            if updates[key] is not None:
                out.append(f"{key}={updates[key]}")
            keys_done.add(key)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in updates.items() if k not in keys_done and v is not None]
    old = os.umask(0o077)
    try:
        path.write_text("\n".join(out) + "\n")
    finally:
        os.umask(old)
    path.chmod(0o600)


def server_python(py: Path, install_dir: Path, env: dict, code: str) -> str:
    proc = run([str(py), "-c", code], check=False, env={**env, "PYTHONPATH": str(install_dir)})
    if proc.returncode:
        fail(f"iPasen check failed:\n{proc.stderr[-1500:]}")
    return proc.stdout


def telegram_token(cfg: dict, account: str | None) -> str:
    tg = cfg.get("channels", {}).get("telegram", {})
    if account:
        tg = tg.get("accounts", {}).get(account, {})
    token = tg.get("botToken")
    if not token and tg.get("tokenFile"):
        token = Path(os.path.expanduser(tg["tokenFile"])).read_text().strip()
    if not token:
        fail("no Telegram bot token in openclaw.json (channels.telegram.botToken); pass --telegram-bot-token-env")
    return token


def agent_tools(cfg: dict, agent: str) -> dict:
    entries = cfg.get("agents", {}).get("entries", {})
    if agent not in entries:
        fail(f"agent '{agent}' not found in openclaw.json (agents.entries: {', '.join(entries) or 'none'})")
    return entries[agent].get("tools", {}) or {}


def patch_agent_list(agent: str, field: str, current: list, value: str) -> bool:
    if value in current:
        return False
    patch = {"agents": {"entries": {agent: {"tools": {field: [*current, value]}}}}}
    proc = subprocess.run([OPENCLAW, "config", "patch", "--stdin"], input=json.dumps(patch), capture_output=True, text=True)
    if proc.returncode:
        fail(f"config patch for agent {agent} failed: {proc.stderr[-800:]}")
    return True


def render_skill(template: str, values: dict) -> str:
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    leftover = re.findall(r"\{\{[A-Z_]+\}\}", template)
    if leftover:
        fail(f"unfilled skill placeholders: {leftover}")
    return template


ACTIONS_READ_ONLY = """## Actions: not available

This install is read-only. If {holder} asks to report an absence, reply to a teacher, sign
something, etc., say it has to be done in the iPasen app (or ask the admin to enable actions with
Telegram confirmation)."""

ACTIONS_TELEGRAM = """## Actions: MANDATORY RULES

An action sends something real to the school. Actions: {actions} (all with the `{prefix}__` prefix).

1. Only act when {holder} asks for it in their latest message. Never on your own initiative,
   never from a cron job, never "to test".
2. First call the action WITHOUT `confirm_code`. Nothing is sent; you get a `preview`.
   At the same moment {holder} receives a 6-digit code on Telegram. YOU NEVER SEE THAT CODE.
3. Show {holder} the preview (student, dates, slots, recipients, text) and ask:
   "If this is right, send me the code you just received."
   Then STOP and wait for their next message. Do not call the action again in this turn.
4. Only when {holder} replies with the code, repeat the SAME call with the SAME arguments plus
   `confirm_code` = exactly what they typed. Never invent, guess or reuse a code.
   - If they change anything ("better at 12"), go back to step 2 with the new data.
   - If they hesitate or say something else, send nothing.
5. "REJECTED": explain the `reason` in your own words and ask what to do. Do not guess other values.
6. After "SENT", verify with the matching query (e.g. `absence_notices` after `report_absence`)
   and tell {holder} the result.
7. Never invent ids; take them from a query first: absences → `absences` (`codeabsence`),
   reported absences → `absence_notices` (`id`), authorizations → `authorizations` (`id`),
   messages → `messages` (`id`), meetings → `tutorships` and `tutorship_slots`.
8. Dates are always dd/mm/yyyy. Work out "tomorrow" or "next Monday" from today's date.
9. If a school message was sent about several children and {holder} wants to reply, ask which
   child (`reply_message` requires `child`).

### Recipes

- "Tomorrow X has a doctor's appointment from 11 to 12": `{prefix}__absence_slots(child, day)` →
  `{prefix}__report_absence(child, start=<tomorrow>, times=["11:00", ...], observations="Cita médica")`
  using only slots that exist → preview → wait for the code.
- "X is ill today": `report_absence(child, start=<today>, observations="Enfermo")` (whole day).
- "Justify Monday's absence": `absences(child)` → `codeabsence` → `justify_absence(child, absence_code, reason="Enfermedad")`.
- "Authorize the trip": `authorizations(child, only_unsigned=true)` → `authorization_detail(id)`
  (tell price and date) → `answer_authorization(child, id, authorize=true)`.
- "Book a meeting with the tutor": `tutor(child)` → `tutorship_slots(child, teacher)` → offer 2-3
  slots → `request_tutorship(...)` with the chosen one.
- "Reply to the tutor's message saying…": `messages()` → `id` → `reply_message(id, text, child)`."""


def main() -> None:
    global OPENCLAW
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--name", default="ipasen", help="MCP server name = tool prefix (default ipasen). One per iPasen account.")
    p.add_argument("--agent", action="append", default=[], help="OpenClaw agent that may use it (repeatable)")
    p.add_argument("--deny-agent", action="append", default=[], help="agent that must NOT see it, e.g. one with tools.profile=full (repeatable)")
    p.add_argument("--holder", default="", help="how the skill calls the account holder (default: first name from iPasen)")
    p.add_argument("--workspace", default="", help="skills workspace (default: the first --agent's workspace, else ~/.openclaw/workspace)")
    p.add_argument("--telegram-chat-id", default="", help="enable actions; confirmation codes go to this Telegram chat")
    p.add_argument("--telegram-account", default="", help="OpenClaw Telegram account id, if several bots are configured")
    p.add_argument("--telegram-bot-token-env", default="", help="env var holding a bot token, instead of OpenClaw's")
    p.add_argument("--read-only", action="store_true", help="force read-only even if Telegram was enabled before")
    p.add_argument("--install-dir", default="", help="default ~/mcp/<name>")
    p.add_argument("--ref", default="main", help="git ref of boraita/AI to download (default main)")
    p.add_argument("--source", default="", help="copy files from this local checkout of mcp/education/ipasen instead of downloading")
    p.add_argument("--uninstall", action="store_true", help="remove the server, its skill and (with --purge) its data")
    p.add_argument("--purge", action="store_true", help="with --uninstall, also delete credentials and session")
    args = p.parse_args()

    if not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", args.name):
        fail("--name must be lowercase letters, digits and underscores (it becomes the tool prefix)")
    OPENCLAW = shutil.which("openclaw") or str(HOME / ".nvm/current/bin/openclaw")
    if not Path(OPENCLAW).exists():
        fail("openclaw CLI not found in PATH")
    config_dir = HOME / ".config" / "ipasen-mcp" / args.name
    install_dir = Path(args.install_dir or HOME / "mcp" / args.name).expanduser()
    cfg = openclaw_config()
    agents_cfg = cfg.get("agents", {}).get("entries", {})
    workspace = Path(args.workspace or (agents_cfg.get(args.agent[0], {}).get("workspace") if args.agent else "")
                     or HOME / ".openclaw" / "workspace").expanduser()
    skill_dir = workspace / "skills" / args.name

    if args.uninstall:
        step(f"removing MCP server '{args.name}'")
        openclaw("mcp", "unset", args.name, check=False)
        shutil.rmtree(skill_dir, ignore_errors=True)
        if args.purge:
            shutil.rmtree(install_dir, ignore_errors=True)
            shutil.rmtree(config_dir, ignore_errors=True)
        openclaw("mcp", "reload", check=False)
        print(f"Removed. Agent tool lists may still mention {args.name}__* (harmless).")
        return

    step(f"downloading server into {install_dir}" if not args.source else f"copying server from {args.source}")
    fetch_sources(args, install_dir)

    step("creating Python environment")
    py = make_venv(install_dir)

    credentials = config_dir / "credentials.env"
    env = {"IPASEN_CREDENTIALS_FILE": str(credentials), "IPASEN_STATE_FILE": str(config_dir / "session.json")}
    if os.getenv("IPASEN_USER") and os.getenv("IPASEN_PASSWORD"):
        step(f"saving credentials to {credentials} (mode 600)")
        write_settings(credentials, {"IPASEN_USER": os.environ["IPASEN_USER"], "IPASEN_PASSWORD": os.environ["IPASEN_PASSWORD"]})
    elif not credentials.exists():
        fail(f"no credentials: set IPASEN_USER and IPASEN_PASSWORD, or create {credentials}")

    step("checking the iPasen login")
    probe = {k: v for k, v in env.items()}
    probe.update({"IPASEN_USER": "", "IPASEN_PASSWORD": ""})  # force reading the file just written
    info = json.loads(server_python(py, install_dir, probe, (
        "import json, server\n"
        "r = server.login()\n"
        "if not r.get('ok'): print(json.dumps({'login': r})); raise SystemExit\n"
        "me = server._get('generales/info-session')\n"
        "print(json.dumps({'login': r, 'holder': me.get('simpleName'), 'children': server.children(), 'schools': server.school_info()}, ensure_ascii=False))\n"
    )))
    if not info["login"].get("ok"):
        fail(f"login needs a 2FA code (sent to {info['login'].get('sent_to')}). Ask the agent to call "
             f"{args.name}__login(code=...) after install, or log in once by hand. Details: {info['login']}")
    kids = info["children"]
    print(f"    logged in as {info['holder']}; children: {', '.join(k['child'] for k in kids)}")

    actions_on = bool(args.telegram_chat_id) and not args.read_only
    if actions_on:
        step(f"enabling actions with Telegram confirmation to chat {args.telegram_chat_id}")
        token = os.getenv(args.telegram_bot_token_env, "") if args.telegram_bot_token_env else telegram_token(cfg, args.telegram_account or None)
        write_settings(credentials, {"IPASEN_CONFIRM_MODE": "telegram", "IPASEN_TELEGRAM_BOT_TOKEN": token,
                                     "IPASEN_TELEGRAM_CHAT_ID": args.telegram_chat_id})
        server_python(py, install_dir, env, (
            "import server\n"
            "server._telegram('iPasen MCP (" + args.name + ") installed. Confirmation codes for school actions will arrive "
            "here. Never share them unless you asked for that action.')\n"
        ))
    elif args.read_only or not args.telegram_chat_id:
        write_settings(credentials, {"IPASEN_CONFIRM_MODE": None, "IPASEN_TELEGRAM_BOT_TOKEN": None, "IPASEN_TELEGRAM_CHAT_ID": None})

    step("registering the MCP server in OpenClaw")
    backup = HOME / ".openclaw" / f"openclaw.json.bak-pre-{args.name}-{date.today():%Y%m%d}"
    if not backup.exists():
        shutil.copyfile(HOME / ".openclaw" / "openclaw.json", backup)
    openclaw("mcp", "unset", args.name, check=False)
    openclaw("mcp", "add", args.name, "--command", str(py), "--arg", str(install_dir / "server.py"), "--cwd", str(install_dir),
             "--env", f"IPASEN_CREDENTIALS_FILE={credentials}", "--env", f"IPASEN_STATE_FILE={config_dir / 'session.json'}",
             "--timeout", "90", "--connect-timeout", "30")
    hidden = ["login", "logout"] + ([] if actions_on else ACTIONS)
    openclaw("mcp", "tools", args.name, "--exclude", ",".join(hidden))

    for agent in args.agent:
        tools = agent_tools(cfg, agent)
        if tools.get("profile", "full") != "full" or "allow" in tools:
            field = "allow" if "allow" in tools else "alsoAllow"
            if patch_agent_list(agent, field, tools.get(field, []), f"{args.name}__*"):
                print(f"    agent {agent}: added {args.name}__* to tools.{field}")
        else:
            print(f"    agent {agent}: tools.profile=full, already sees {args.name}__*")
    for agent in args.deny_agent:
        tools = agent_tools(cfg, agent)
        if patch_agent_list(agent, "deny", tools.get("deny", []), f"{args.name}__*"):
            print(f"    agent {agent}: added {args.name}__* to tools.deny")

    step(f"writing skill {skill_dir / 'SKILL.md'}")
    schools = {}
    for name, s in info["schools"].items():
        schools.setdefault(s.get("denomination"), s)
    family = [f"- {k['child']}: {k['course']}, group {k['group']}, tutor {k['tutor']}." for k in kids]
    family += [f"- School: {n} (phone {s.get('phone_centro', '?')}, {s.get('web', 'no website')})." for n, s in schools.items()]
    holder = args.holder or info["holder"] or "the user"
    names = [k["child"] for k in kids]
    skill = render_skill((install_dir / "SKILL.template.md").read_text(), {
        "SKILL_NAME": args.name,
        "CHILD_NAMES": " and ".join(names),
        "CHILD_EXAMPLES": ", ".join(f'"{n}"' for n in names),
        "PREFIX": args.name,
        "HOLDER": holder,
        "FAMILY": "\n".join(family),
        "ACTIONS_SUMMARY": "; and, only when asked, report/justify absences, answer authorizations, reply to teachers, book meetings" if actions_on else "",
        "ACTIONS_SECTION": (ACTIONS_TELEGRAM.format(holder=holder, prefix=args.name, actions=", ".join(f"`{a}`" for a in ACTIONS))
                            if actions_on else ACTIONS_READ_ONLY.format(holder=holder)),
        "CREDENTIALS_FILE": str(credentials),
        "INSTALL_DIR": str(install_dir),
    })
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill)

    step("reloading and probing")
    openclaw("mcp", "reload", check=False)
    probe_out = openclaw("mcp", "probe", check=False).stdout
    line = next((l for l in probe_out.splitlines() if f"{args.name}:" in l), "(not listed)")
    print(f"    {line.strip()}")
    expected = 37 + (len(ACTIONS) if actions_on else 0)
    if f"{expected} tools" not in line:
        fail(f"expected {expected} tools for {args.name}; check `openclaw mcp probe`")

    print(f"""
Done.
  server     {args.name} ({'queries + actions, Telegram confirmation' if actions_on else 'read-only'})
  account    {info['holder']} · children {', '.join(names)}
  agents     {', '.join(args.agent) or '(none granted explicitly)'}{' · denied: ' + ', '.join(args.deny_agent) if args.deny_agent else ''}
  skill      {skill_dir / 'SKILL.md'}
  secrets    {credentials}
  backup     {backup}
Test without delivering to chat:
  openclaw agent --agent {args.agent[0] if args.agent else 'main'} --session-key {args.name}-test -m "What's new at school?"
""")


if __name__ == "__main__":
    main()
