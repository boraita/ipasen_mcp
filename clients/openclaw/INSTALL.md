# Installing iPasen in OpenClaw (written for the agent)

Goal: a family member chats with their OpenClaw assistant (e.g. on Telegram) and asks about school —
grades, absences, messages, trips — and, if they want, lets it report absences or reply to
teachers with a confirmation code that only they receive.

## For the human: what to tell your OpenClaw agent

Copy and send this to your agent:

> Install the iPasen MCP for me following
> https://github.com/boraita/AI/blob/main/mcp/education/ipasen/clients/openclaw/INSTALL.md
> — read it entirely before running anything, and ask me whatever you need.

The agent will ask you for your iPasen username and password, whether you want actions
(absences, replies, authorizations) or only queries, and your Telegram chat id if you want actions.

---

## For the agent: installation procedure

Follow these steps in order. Do not improvise other install methods. Run commands with your
shell/exec tool on the OpenClaw host, as the user that runs the gateway.

### 0. Rules while installing

- Never echo, log or store the password anywhere except through the installer (it writes a
  mode-600 file). Do not save it to memory, notes, USER.md or any skill.
- Do not edit `openclaw.json` by hand; the installer backs it up and uses the OpenClaw CLI.
- Do not test actions afterwards with invented requests. Only test queries (step 5).

### 1. Ask the human

1. **Which OpenClaw agent** will use it (usually the one you are, e.g. `main`). If other agents
   belong to other people, list them: they will be denied access.
2. **iPasen username and password.** Suggest they delete the chat message after you confirm the
   install worked.
3. **Queries only, or also actions?** Explain: actions send real things to the school; each one
   needs a 6-digit code that the bot sends to their Telegram chat and that you never see.
4. If actions: **their Telegram chat id** (their numeric Telegram user id; in a private chat with
   the bot, the chat id equals it). If you know it from the conversation metadata, confirm it
   with them.
5. If this is a **second account** (another parent) on the same OpenClaw: a different `--name`
   (e.g. `ipasen_partner`) and which agent is theirs.

### 2. Check prerequisites

```bash
command -v openclaw && openclaw --version
python3 --version            # needs 3.10+ (stdlib only for the installer)
command -v uv || echo "no uv" # only needed if python3 -m venv fails (missing ensurepip)
```
If Python is older than 3.10 and `uv` is missing, install uv without sudo:
`curl -LsSf https://astral.sh/uv/install.sh | sh` (installs to `~/.local/bin`).

### 3. Download the installer and read it

```bash
mkdir -p ~/mcp && cd ~/mcp
curl -fsSLO https://raw.githubusercontent.com/boraita/AI/main/mcp/education/ipasen/clients/openclaw/install.py
python3 install.py --help
```
Skim `install.py` before running it (it is short, standard library only).

### 4. Run it

Pass credentials through environment variables (not as arguments). Examples:

```bash
# queries only, for agent main
IPASEN_USER='…' IPASEN_PASSWORD='…' python3 ~/mcp/install.py --agent main

# queries + actions with Telegram confirmation
IPASEN_USER='…' IPASEN_PASSWORD='…' python3 ~/mcp/install.py --agent main --telegram-chat-id 123456789

# second account for another person's agent, hidden from main
IPASEN_USER='…' IPASEN_PASSWORD='…' python3 ~/mcp/install.py --name ipasen_partner \
  --agent partner --deny-agent main --telegram-chat-id 987654321
```

Useful flags: `--holder "Name"` (how the skill refers to the person), `--read-only` (turn
actions off again), `--ref <tag>` (pin a version), `--uninstall [--purge]`.

Expected output ends with `Done.` and a probe line like `ipasen: 37 tools` (read-only) or
`ipasen: 53 tools` (with actions). With actions, the human receives a Telegram message
"iPasen MCP (ipasen) installed…" — ask them to confirm it arrived.

If it fails:
| Message | Fix |
|---|---|
| `login needs a 2FA code` | after install, call `ipasen__login()`, ask the human for the code, then `ipasen__login(code="…")` |
| `Login rejected (401)` | wrong username/password; ask again |
| `need Python >= 3.10 … or uv` | install uv (step 2) |
| `no Telegram bot token` | OpenClaw has several bots: add `--telegram-account <id>` |
| `agent '…' not found` | use an id from `agents.entries` (`openclaw config get agents.entries`) |

### 5. Verify (queries only)

Start a new session so the skill and tools are loaded, then ask yourself, e.g.
"What's new at school?" — you should call `ipasen__overview`. Report to the human what you found.
Tell them how to use it: "ask me about grades, absences, messages, trips…", and, if actions are
on, that you will always show a preview and wait for the code from Telegram.

### 6. Updating later

Re-run the same command; it is idempotent (downloads the latest server, keeps credentials,
rewrites the skill). To change only the mode: add or drop `--telegram-chat-id`, or use `--read-only`.

---

## What the installer changes

| Where | What |
|---|---|
| `~/mcp/<name>/` | server code, venv, docs |
| `~/.config/ipasen-mcp/<name>/` | `credentials.env` (600): login, confirm mode, bot token, chat id · `session.json` (600) |
| `openclaw.json` | `mcp.servers.<name>` (command + paths, no secrets), tool filter, per-agent allow/deny; backup `openclaw.json.bak-pre-<name>-YYYYMMDD` |
| `<agent workspace>/skills/<name>/SKILL.md` | skill with the family's data and the action rules |

Uninstall: `python3 ~/mcp/install.py --name <name> --uninstall --purge`.
