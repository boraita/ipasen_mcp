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

## Features

- **Queries** (read-only): `overview`, `grades` (teacher gradebook with per-criterion marks),
  `term_grades`, `schedule`, `absences`, `messages` / `read_message`, `upcoming_events`,
  `school_calendar`, `tutor`, `authorizations`, `school_services`, and more (37 tools).
- **Actions** (16 tools): report/justify absences, answer trip authorizations, sign or decline
  documents, reply to or write to teachers, book/cancel meetings, mark messages, manage people
  authorized to pick children up. Every action is a two-step **preview → confirm** call; see
  [ACTIONS.md](ACTIONS.md).
- Children are selected by first name (`child="Ana"`); messages sent once per child are merged.
- Supports two-factor authentication and remembers trusted devices.
- MCP tool annotations: queries are `readOnlyHint`, irreversible actions `destructiveHint`.

## How it works

| | |
|---|---|
| API base | `https://seneca.juntadeandalucia.es/api/public/` |
| Login | `POST security/oauth/token` (form: `username`, `password`, `grant_type=password`, `scope=PASENDROID`, `client_id=ipasen`) |
| Auth | `Authorization: Bearer <access_token>`; the server rotates the token in the `token-renew` response header |
| 2FA | if required, login returns `data.twoFactorAuthToken` + `sendTo`; retry with `2FactorAuthToken`, `2FactorAuthCode`, `trustDevice` |
| Responses | `{success, message, data, errors[]}` |

The older `seneca/jsp/pasendroid/` API (used by 2021-era projects) is no longer what the app uses.

## Quick start

Requires Python ≥ 3.10.

```bash
cd mcp/education/ipasen
python3 -m venv venv && venv/bin/pip install -r requirements.txt

mkdir -p ~/.config/ipasen-mcp && chmod 700 ~/.config/ipasen-mcp
umask 077; printf 'IPASEN_USER=your_user\nIPASEN_PASSWORD=your_password\n' > ~/.config/ipasen-mcp/credentials.env

claude mcp add ipasen-mcp -s user -- $(pwd)/venv/bin/python3 $(pwd)/server.py
```

- Other clients (Claude Desktop, OpenCode, OpenClaw), multiple accounts and safety advice:
  [CLIENTS.md](CLIENTS.md).
- **OpenClaw**: one command, designed so your OpenClaw agent can install it by itself —
  [clients/openclaw/INSTALL.md](clients/openclaw/INSTALL.md).

## Usage

1. `children` → each child with year, class group, tutor, school and enrollment number.
2. `overview` → unread messages, next events, absences, latest grades, pending authorizations.
3. Any other tool with `child="<first name>"` (or empty for all children).

Data from the school (subjects, weekdays, absence reasons) comes in Spanish.

## Known server quirks

- `centros/datos-centro` fails when several children attend the same school
  (`NonUniqueResultException`); `school_info` uses the home-screen data instead.
- `seguimiento/get-notas-evolucion` returns 404; not exposed.
- The agenda only accepts `yyyy-mm-dd` dates after the start of the school year.
- "Administrador PASEN" notices are regenerated on every request and keep no read state.

## Files

| File | Purpose |
|---|---|
| `server.py` | the MCP server (FastMCP, stdio) |
| `ACTIONS.md` | how actions work and how to use each one safely |
| `CLIENTS.md` | connecting any MCP client; multiple accounts; keeping agents safe |
| `clients/openclaw/` | OpenClaw installer, AI-oriented install guide and skill template |

## License

[MIT](LICENSE) — applies to the files in this folder only.
