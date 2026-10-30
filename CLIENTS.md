# Connecting the iPasen MCP to a client or agent

The server is a Python **stdio** MCP server: any client that can spawn a process and speak MCP
over stdin/stdout can use it (Claude Code, Claude Desktop, OpenCode, OpenClaw…). This guide
covers what each client needs and how to keep an agent from making mistakes with actions.

**OpenClaw users:** skip to §4 — there is an installer your agent can run for you.

---

## 1. Host requirements

| Requirement | Detail |
|---|---|
| Python ≥ 3.10 | venv via `python -m venv` or, where `ensurepip` is missing (minimal Debian/Ubuntu), `uv venv` |
| Dependencies | `requirements.txt`: `mcp<2`, `httpx` (`mcp` 2.x changed the FastMCP API) |
| Network | outbound HTTPS to `seneca.juntadeandalucia.es` (and `api.telegram.org` in Telegram mode) |
| Credentials | the family's iPasen username and password |

### Install

```bash
mkdir -p ~/mcp/ipasen && cp server.py requirements.txt ACTIONS.md ~/mcp/ipasen/
cd ~/mcp/ipasen
python3 -m venv venv && venv/bin/pip install -r requirements.txt
# or, without ensurepip:
uv venv --python python3.12 venv && VIRTUAL_ENV=$PWD/venv uv pip install -r requirements.txt
```

### Settings and secrets

Every setting is read from the process environment first, then from a `KEY=value` file
(`~/.config/ipasen-mcp/credentials.env`, or the path in `IPASEN_CREDENTIALS_FILE`):

| Key | Purpose |
|---|---|
| `IPASEN_USER`, `IPASEN_PASSWORD` | iPasen login |
| `IPASEN_CONFIRM_MODE` | `inline` (default) or `telegram` — see [ACTIONS.md §1](ACTIONS.md) |
| `IPASEN_TELEGRAM_BOT_TOKEN`, `IPASEN_TELEGRAM_CHAT_ID` | where confirmation codes go in `telegram` mode |
| `IPASEN_STATE_FILE` (env only) | session file, default `~/.config/ipasen-mcp/session.json` |

**Keep secrets in the file, not in the client config.** Client configs (`~/.claude.json`,
`openclaw.json`…) get copied into backups and logs, and the password would end up in all of them.

```bash
mkdir -p ~/.config/ipasen-mcp && chmod 700 ~/.config/ipasen-mcp
umask 077; cat > ~/.config/ipasen-mcp/credentials.env <<'EOF'
IPASEN_USER=your_user
IPASEN_PASSWORD=your_password
EOF
```

### Session

- The OAuth token, 2FA device trust and pending confirmations live in the state file (mode 600).
- Each host keeps its own session. Using one account from several hosts at once works: on a 401
  the server logs in again by itself.
- With 2FA, the first time the agent must call `login()`, ask the user for the code and call
  `login(code="…")`. With `trust_device=True` (default) it is not asked again.

### Multiple accounts (e.g. both parents)

Run one server per account, each with its own name, credentials file and state file:

```bash
IPASEN_CREDENTIALS_FILE=~/.config/ipasen-mcp/partner/credentials.env \
IPASEN_STATE_FILE=~/.config/ipasen-mcp/partner/session.json \
  ~/mcp/ipasen/venv/bin/python ~/mcp/ipasen/server.py
```

Register it in the client under a different name (e.g. `ipasen_partner`) and give each person's
agent access only to their own server.

### Smoke test (no client)

```bash
cd ~/mcp/ipasen && venv/bin/python -c "import server; print(server.login()); print(server.children())"
```

---

## 2. What a client gets from the server

| Item | Purpose |
|---|---|
| Server `instructions` | the action rules (preview → confirm). Some clients pass them to the model, some do not — repeat them in a skill or system prompt (§5) |
| Tool annotations | queries: `readOnlyHint=true`. Actions: `readOnlyHint=false`, plus `destructiveHint=true` when they cannot be undone. Clients use them to decide when to ask for approval |
| Descriptions | actions start with `ACTION ·` and say which query each id comes from |
| `ACTIONS.md` | the full action guide; give the agent access to it or copy it into its skill |

Inventory: 39 non-action tools (37 queries + `login`/`logout`) and 16 actions (7 reversible,
9 destructive). Tool names seen by the model depend on the client:

| Client | Tool name |
|---|---|
| Claude Code | `mcp__ipasen-mcp__grades` |
| OpenClaw | `ipasen__grades` |
| Claude Desktop / OpenCode | `grades` (grouped under the server) |

---

## 3. Client configuration

### Claude Code

```bash
claude mcp add ipasen-mcp -s user -- ~/mcp/ipasen/venv/bin/python ~/mcp/ipasen/server.py
```
(or run `./install.sh` at the repository root). Keep the action tools behind permission prompts:
do not auto-allow `mcp__ipasen-mcp__*` and do not use bypass-permissions mode if you want each
action approved — otherwise switch to `telegram` mode.

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "ipasen": {
      "command": "/Users/YOU/mcp/ipasen/venv/bin/python",
      "args": ["/Users/YOU/mcp/ipasen/server.py"]
    }
  }
}
```

### OpenCode (`~/.config/opencode/config.json`)

```json
"mcp": {
  "ipasen": {
    "type": "local",
    "command": ["/home/YOU/mcp/ipasen/venv/bin/python", "/home/YOU/mcp/ipasen/server.py"],
    "enabled": true
  }
}
```

---

## 4. OpenClaw

Use the installer: [clients/openclaw/INSTALL.md](clients/openclaw/INSTALL.md). It is written so the
OpenClaw agent itself can follow it, and it:

- downloads the server, creates the venv (falls back to `uv` when `ensurepip` is missing);
- stores credentials and Telegram settings in `~/.config/ipasen-mcp/<name>/credentials.env` (600),
  never in `openclaw.json`;
- checks the login and reads the children's data;
- registers the server with `openclaw mcp add` (backing up `openclaw.json` first);
- installs **read-only** by default; enables actions **only** with `--telegram-chat-id`, in
  `telegram` confirmation mode (inline mode is never used in OpenClaw);
- grants or denies the server per agent (`--agent`, `--deny-agent`);
- writes a skill filled in with the family's data;
- reloads and probes (`37 tools` read-only, `53 tools` with actions).

What it does by hand, for reference:

```bash
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.bak-pre-ipasen-$(date +%Y%m%d)
openclaw mcp add ipasen --command ~/mcp/ipasen/venv/bin/python --arg ~/mcp/ipasen/server.py \
  --cwd ~/mcp/ipasen --env IPASEN_CREDENTIALS_FILE=$HOME/.config/ipasen-mcp/ipasen/credentials.env \
  --env IPASEN_STATE_FILE=$HOME/.config/ipasen-mcp/ipasen/session.json --timeout 90 --connect-timeout 30
openclaw mcp tools ipasen --exclude "login,logout"     # + every action when read-only
openclaw mcp reload && openclaw mcp probe
```

OpenClaw notes:
- MCP tools appear as `<server>__<tool>`. An agent with `tools.profile: "full"` sees every server;
  restricted agents need `<server>__*` in `tools.alsoAllow` (or `tools.allow`). A `config patch`
  replaces lists, so repeat existing values.
- To keep a full-profile agent away from another person's account, add `<server>__*` to its
  `tools.deny` (installer: `--deny-agent`).
- `--timeout 90`: `overview` makes about 10 requests to Séneca.
- Never let cron jobs use actions: there is nobody to confirm.

---

## 5. Keeping agents from making mistakes

1. **Pick the confirmation mode for the client.** Human approves each call → `inline` is fine.
   Autonomous/chat agent → `telegram`, or install read-only.
2. **Put the rules in the agent's prompt or skill**, not only in the MCP server instructions
   (the OpenClaw skill template has a ready-made section).
3. **Confirmation must come from the human** in a later message. An agent must never confirm itself
   or chain preview and send in the same turn — `telegram` mode enforces this.
4. **Small models:** if a model gets lost among 55 tools, expose only what it needs (`--include` /
   `--exclude` in OpenClaw, `tools.allow` elsewhere). For a "just ask" bot, hide all actions.
5. **Plain-text channels** (Telegram, SMS): ask for plain-text answers; previews are shown line by line.
6. **Verify:** after `SENT`, the agent checks the matching query ([ACTIONS.md §4](ACTIONS.md))
   and tells the user whether it applied.
7. **Never test actions against a real account by prompting an agent** with a made-up request —
   that is how the 2026-10-30 incident happened. Test with actions hidden, or in `telegram` mode
   without ever typing the code.
