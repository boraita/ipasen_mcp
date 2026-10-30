# ipasen-mcp

**Unofficial** [MCP](https://modelcontextprotocol.io) server for **iPasen**, the school-tracking
platform for families in Andalusia, Spain (Séneca, Junta de Andalucía). It lets an AI assistant
(Claude Code, Claude Desktop, OpenCode, OpenClaw…) check grades, absences, timetable, school
messages, trips and the school calendar, and — with explicit human confirmation — report or
justify absences, answer authorizations, reply to teachers, book parent-teacher meetings, etc.

> ### Disclaimer
> This project is **not affiliated with, endorsed by or supported by the Junta de Andalucía**.
> It is an independent client written for interoperability: it uses the same public HTTPS API
> as the official iPasen mobile app and contains **no code, keys or assets from that app**.
> Each user logs in with **their own iPasen account** to access **their own family's data**.
> Use it at your own risk and responsibility, and in line with the platform's terms of use.
> The API may change or be blocked at any time.

**Contents:** [Features](#features) · [Installation](#installation) · [Usage](#usage) ·
[Actions and confirmation](#actions-and-confirmation) · [Tool reference](#tool-reference) ·
[Troubleshooting](#troubleshooting) · [How it works](#how-it-works)

## Features

- **37 read-only tools**: daily `overview`, grades from the teacher's gradebook (with the mark of
  each assessment criterion), term grades, timetable, absences, school messages, upcoming events,
  school calendar, tutor and office hours, trip authorizations, lunch/after-school services…
- **16 actions**: report/justify absences, answer trip authorizations, sign or decline documents,
  reply to or write to teachers, book/cancel meetings, mark messages, manage the people allowed
  to pick children up. Every action is a two-step **preview → confirm** call.
- **Safe for autonomous agents**: optional Telegram confirmation — the code goes to the human's
  phone and the agent never sees it.
- Children are selected by first name (`child="Ana"`); the messages the school sends once per
  child are merged into one.
- Two-factor authentication with trusted devices; several accounts (e.g. both parents) side by side.
- MCP tool annotations: queries are `readOnlyHint`, irreversible actions `destructiveHint`.
- **OpenClaw installer** that your OpenClaw agent can run by itself.

---

## Installation

### Requirements

- Python **3.10+**
- An **iPasen family account** (the username and password you use in the iPasen app)
- Outbound HTTPS to `seneca.juntadeandalucia.es` (and `api.telegram.org` if you use Telegram confirmation)

### OpenClaw (easiest: let your agent do it)

Send this to your OpenClaw assistant:

> Install the iPasen MCP for me following
> https://github.com/boraita/ipasen_mcp/blob/main/clients/openclaw/INSTALL.md
> — read it entirely before running anything, and ask me whatever you need.

It will ask for your iPasen login, whether you want actions or only queries, and (for actions)
your Telegram chat id. Or run the installer yourself on the OpenClaw host:

```bash
mkdir -p ~/mcp && cd ~/mcp
curl -fsSLO https://raw.githubusercontent.com/boraita/ipasen_mcp/main/clients/openclaw/install.py

# queries only
IPASEN_USER='your_user' IPASEN_PASSWORD='your_password' python3 install.py --agent main

# queries + actions confirmed by a code sent to your Telegram
IPASEN_USER='your_user' IPASEN_PASSWORD='your_password' python3 install.py --agent main --telegram-chat-id 123456789
```

The installer downloads the server, creates the virtualenv, stores secrets in
`~/.config/ipasen-mcp/<name>/credentials.env` (mode 600, never in `openclaw.json`), registers the
server, grants it to the agent, writes a skill with your family's data, and probes it
(`37 tools` read-only, `53 tools` with actions). Re-run it to update. Details and options:
[clients/openclaw/INSTALL.md](clients/openclaw/INSTALL.md).

### Claude Code, Claude Desktop, OpenCode

**1. Get the code and create a virtualenv**

```bash
git clone https://github.com/boraita/ipasen_mcp.git ~/ipasen_mcp && cd ~/ipasen_mcp
python3 -m venv venv && venv/bin/pip install -r requirements.txt
# if `python3 -m venv` fails (no ensurepip, e.g. minimal Debian/Ubuntu), use uv:
# uv venv --python 3.12 venv && VIRTUAL_ENV=$PWD/venv uv pip install -r requirements.txt
```

**2. Store your credentials in a private file** (keeps the password out of client configs,
which get copied into backups and logs):

```bash
mkdir -p ~/.config/ipasen-mcp && chmod 700 ~/.config/ipasen-mcp
umask 077; cat > ~/.config/ipasen-mcp/credentials.env <<'EOF'
IPASEN_USER=your_user
IPASEN_PASSWORD=your_password
EOF
```

**3. Check the login**

```bash
cd ~/ipasen_mcp && venv/bin/python -c "import server; print(server.login()); print(server.children())"
```
You should see `{'ok': True}` and your children. If it says `two_factor_required`, see
[Two-factor authentication](#two-factor-authentication).

**4. Register the server in your client**

*Claude Code*
```bash
claude mcp add ipasen-mcp -s user -- ~/ipasen_mcp/venv/bin/python ~/ipasen_mcp/server.py
```

*Claude Desktop* — `claude_desktop_config.json` (Settings → Developer → Edit config):
```json
{
  "mcpServers": {
    "ipasen": {
      "command": "/Users/YOU/ipasen_mcp/venv/bin/python",
      "args": ["/Users/YOU/ipasen_mcp/server.py"]
    }
  }
}
```

*OpenCode* — `~/.config/opencode/config.json`:
```json
"mcp": {
  "ipasen": {
    "type": "local",
    "command": ["/home/YOU/ipasen_mcp/venv/bin/python", "/home/YOU/ipasen_mcp/server.py"],
    "enabled": true
  }
}
```

*Any other MCP client*: stdio transport, command `<repo>/venv/bin/python <repo>/server.py`.

Restart the client and ask *"What's new at school?"*.

### Configuration

Settings are read from the server's environment first, then from the credentials file:

| Key | Default | Purpose |
|---|---|---|
| `IPASEN_USER`, `IPASEN_PASSWORD` | — | iPasen login |
| `IPASEN_CONFIRM_MODE` | `inline` | how actions are confirmed: `inline` or `telegram` ([see below](#actions-and-confirmation)) |
| `IPASEN_TELEGRAM_BOT_TOKEN` | — | bot that delivers confirmation codes (`telegram` mode) |
| `IPASEN_TELEGRAM_CHAT_ID` | — | your Telegram chat/user id (`telegram` mode) |
| `IPASEN_CREDENTIALS_FILE` (env only) | `~/.config/ipasen-mcp/credentials.env` | where the settings above are read from |
| `IPASEN_STATE_FILE` (env only) | `~/.config/ipasen-mcp/session.json` | token, 2FA trust and pending confirmations (mode 600) |

### Two-factor authentication

If your account uses 2FA, ask the assistant to log in: it calls `login()`, iPasen sends you a code,
you give it to the assistant, and it calls `login(code="123456")`. With the default
`trust_device=True` the code is not asked again on that machine.

### Several accounts (e.g. both parents)

Run one server per account with its own files, and register each under a different name:

```bash
claude mcp add ipasen-partner -s user \
  -e IPASEN_CREDENTIALS_FILE=$HOME/.config/ipasen-mcp/partner/credentials.env \
  -e IPASEN_STATE_FILE=$HOME/.config/ipasen-mcp/partner/session.json \
  -- ~/ipasen_mcp/venv/bin/python ~/ipasen_mcp/server.py
```
In OpenClaw: `install.py --name ipasen_partner --agent partner --deny-agent main …`.

### Updating and uninstalling

- Update: `cd ~/ipasen_mcp && git pull && venv/bin/pip install -r requirements.txt`, then restart
  the client. OpenClaw: re-run `install.py` with the same options.
- Uninstall: remove it from the client (`claude mcp remove ipasen-mcp -s user`), then delete
  `~/ipasen_mcp` and `~/.config/ipasen-mcp`. OpenClaw: `python3 install.py --uninstall --purge`.

---

## Usage

Just talk to your assistant. Examples:

| You say | The assistant uses |
|---|---|
| "What's new at school?" | `overview` |
| "How is Ana doing in English?" | `grades(child="Ana", subject="inglés")` |
| "What does Pablo have on Tuesday?" | `schedule(child="Pablo", day="tuesday")` |
| "Any unread messages from school?" | `messages(unread_only=True)` → `read_message(id)` |
| "When are the Christmas holidays?" | `school_calendar` |
| "Is there any trip I need to authorize?" | `authorizations(only_unsigned=True)` |
| "When can I see the tutor?" | `tutor` |
| "Tomorrow Ana has the dentist from 11 to 12, let the school know" | `absence_slots` → `report_absence` (preview, then confirm) |
| "Reply to the tutor's message saying thanks" | `messages` → `reply_message` (preview, then confirm) |

Tips:
- Start with `children` or `overview`; every other tool takes `child="<first name>"`
  (case/accent-insensitive) or nothing for all children.
- Data from the school (subjects, weekdays, absence reasons) comes in **Spanish**; the assistant
  answers in your language.
- Dates for actions are `dd/mm/yyyy`.

---

## Actions and confirmation

Actions send something real to the school. Each one is called **twice**:

1. **Without `confirm_code`** → nothing is sent. The response is
   `"status": "PREVIEW — nothing has been sent"` with exactly what would be sent (student, dates,
   time slots, recipients, text).
2. **With the same arguments plus `confirm_code`** → it is sent (`"status": "SENT"`).

The code is bound to the exact request: if anything changes, it is rejected and a new preview is
needed. Invalid data (unknown id, wrong date, ambiguous recipient, two children when one is
required…) returns `"REJECTED — nothing has been sent"` with the reason.

Where the code comes from depends on `IPASEN_CONFIRM_MODE`:

| Mode | The code… | Use it with |
|---|---|---|
| `inline` (default) | comes back in the preview | clients where **you approve every tool call** (e.g. Claude Code with permission prompts on) |
| `telegram` | is sent to **your Telegram**; the agent never sees it and must ask you for it. Random, single-use, valid 10 min, voided after 3 wrong tries | autonomous or chat agents (OpenClaw, bots), or any client where tool calls run without asking you |

> ⚠️ In `inline` mode an agent can confirm itself. This happened in testing: an agent previewed a
> message to a teacher and sent it 1.4 s later without asking. If your client does not ask you
> before each tool call, use `telegram` mode or hide the action tools.

To enable `telegram` mode outside OpenClaw, add to your credentials file:
```
IPASEN_CONFIRM_MODE=telegram
IPASEN_TELEGRAM_BOT_TOKEN=123456:ABC...   # a bot you created with @BotFather
IPASEN_TELEGRAM_CHAT_ID=123456789         # your user id; send the bot a message first
```

Full rules, where every id comes from, and per-action details: [ACTIONS.md](ACTIONS.md).
Advice for keeping agents safe: [CLIENTS.md](CLIENTS.md).

---

## Tool reference

### Queries (read-only)

| Tool | Returns |
|---|---|
| `children` | children with year, class group, tutor, school, enrollment |
| `overview` | unread messages, events in the next 14 days, absences, latest grades, unsigned authorizations |
| `grades` | gradebook activities with grade per criterion (`subject`, `period`, `only_graded` filters) |
| `term_grades` | grading periods and their grades once published |
| `subjects` | enrolled subjects and status |
| `schedule` | weekly timetable by day (`day` filter, English or Spanish) |
| `absences` / `absence_notices` | absences recorded by the school / absences reported by the family |
| `messages` / `read_message` | inbox (merged per child; `unread_only`, `child`, `search`) / full text and attachments |
| `sent_messages`, `notifications`, `news` | sent box, automatic notifications, general announcements |
| `upcoming_events` / `school_calendar` | agenda (holidays, trips, exams) / official school calendar |
| `tutor` | class tutor, family office hours, teaching team |
| `tutorships` | parent-teacher meetings and teachers available |
| `authorizations` / `authorization_detail` | trip/activity authorizations / details (price, dates) |
| `observations`, `behaviour` | teacher notes, behaviour incidents |
| `school_info`, `notice_board`, `school_services` | school contact data, notice board, lunch/after-school services |
| `pickup_people`, `student_profile`, `school_history`, `textbooks`, `access_keys`, `scholarships`, `documents_to_sign`, `my_data` | what the name says |
| `absence_slots`, `message_recipients`, `message_tags`, `tutorship_slots` | helpers to prepare actions |
| `raw_get` | any other GET endpoint of the API |
| `login`, `logout` | session handling (normally automatic) |

### Actions (preview → confirm)

| Tool | Does | Undo |
|---|---|---|
| `report_absence` | tell the school about an absence (whole days or some hours of one day) | `delete_absence_report` |
| `delete_absence_report` | delete a reported absence | — |
| `justify_absence` | justify a recorded absence, optional attachment | `delete_justification` |
| `delete_justification` | withdraw a justification | — |
| `answer_authorization` | authorize or refuse a trip/activity | — |
| `sign_document` / `decline_document` | sign (PNG signature) or decline a document | — |
| `send_message` / `reply_message` | write to teachers / reply to a message | — |
| `update_message` | read, unread, favourite, unfavourite, delete | yes, except delete |
| `tag_message` | set tags on a message | yes |
| `request_tutorship` | book a meeting in a free slot | `cancel_tutorship` |
| `cancel_tutorship` / `answer_tutorship` | cancel a meeting / accept or decline one proposed by a teacher | — |
| `mark_observation` | mark a teacher note read/unread | yes |
| `manage_pickup_person` | add, update or remove a person authorized to pick up | yes |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Missing IPASEN_USER / IPASEN_PASSWORD` | create the credentials file (Installation step 2) or set `IPASEN_CREDENTIALS_FILE` |
| `Login rejected (401)` | wrong username/password; check them in the iPasen app |
| `two_factor_required` | see [Two-factor authentication](#two-factor-authentication) |
| `ModuleNotFoundError: mcp.server.fastmcp` | you have `mcp` 2.x: `venv/bin/pip install -r requirements.txt` (pins `mcp<2`) |
| `python3 -m venv` fails with `ensurepip` | use `uv` (Installation step 1) |
| `Telegram mode without IPASEN_TELEGRAM_BOT_TOKEN…` | add the token and chat id, or set `IPASEN_CONFIRM_MODE=inline` |
| Action says `No pending preview for this exact request` | the code expired (10 min), was used, or an argument changed: ask for a new preview |
| `Student 'X' not found` | use a first name from `children` |
| Slow `overview` | it makes ~10 requests to Séneca; allow a 60–90 s tool timeout |

---

## How it works

| | |
|---|---|
| API base | `https://seneca.juntadeandalucia.es/api/public/` |
| Login | `POST security/oauth/token` (form: `username`, `password`, `grant_type=password`, `scope=PASENDROID`, `client_id=ipasen`) |
| Auth | `Authorization: Bearer <access_token>`; the server rotates the token in the `token-renew` response header |
| 2FA | if required, login returns `data.twoFactorAuthToken` + `sendTo`; retry with `2FactorAuthToken`, `2FactorAuthCode`, `trustDevice` |
| Responses | `{success, message, data, errors[]}` |

The older `seneca/jsp/pasendroid/` API (used by 2021-era projects) is no longer what the app uses.

Known server quirks:
- `centros/datos-centro` fails when several children attend the same school
  (`NonUniqueResultException`); `school_info` uses the home-screen data instead.
- `seguimiento/get-notas-evolucion` returns 404; not exposed.
- The agenda only accepts `yyyy-mm-dd` dates after the start of the school year.
- `mensajeria/get-conversaciones` returns the whole conversation with a sender (10 per page,
  oldest first); `read_message` picks the requested message from it.
- "Administrador PASEN" notices are regenerated on every request and keep no read state.

## Files

| File | Purpose |
|---|---|
| `server.py` | the MCP server (FastMCP, stdio) |
| `requirements.txt` | Python dependencies (`mcp<2`, `httpx`) |
| `ACTIONS.md` | action rules, where each id comes from, per-action details |
| `CLIENTS.md` | client integration in depth, multiple accounts, keeping agents safe |
| `clients/openclaw/` | OpenClaw installer (`install.py`), agent-oriented guide (`INSTALL.md`), skill template |

## License

[MIT](LICENSE).
