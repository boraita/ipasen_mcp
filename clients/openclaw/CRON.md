# Daily school digest with OpenClaw crons

Turn new iPasen messages into tasks and calendar events automatically, and send the parents a
short chat message only when there is something to know or do.

```
07:13 / 19:52  digest job (agent, read-only iPasen + task app)  ──► chat of parent 1
07:17 / 19:56  forward job (command, no LLM)                    ──► chat of parent 2…
```

What the digest job does with each new item from `school_digest`:

| Found in iPasen | Result |
|---|---|
| homework, revise a topic, bring something, exam, new assessed activity with a future date | a task for that child in the task app |
| something on a given day (costume, trip, party, no-school day, parents' meeting) | an event in the family calendar (and in the child's list) |
| something the parents must do or know (form, money, signature, notice, new grade) | a line in the chat message |
| nothing to do, past dates, cancelled things, password notices | ignored |
| nothing new at all | `NO_REPLY` → no message is sent |

Every item gets an explicit decision recorded with `mark_digested` (`task`, `event`, `parents`,
`nothing` + why), so the model cannot skip messages silently and you can audit what it decided
(`digest_log` in the state file).

## Requirements

- The iPasen MCP installed in OpenClaw ([INSTALL.md](INSTALL.md)), read-only is enough.
- A task/calendar MCP for the family. The template uses "Tareas en casa"
  (`tareas__calendario`, `tareas__anadir_tarea`, `tareas__anadir_evento`); adapt the tool names in
  the prompt to yours.

## 1. The digest job

Copy [cron/digest-prompt.md](cron/digest-prompt.md) to the OpenClaw workspace, fill in
`{{PARENTS}}`, `{{ADULT}}` and `{{LANGUAGE}}`, and remove the comment block:

```bash
mkdir -p ~/.openclaw/workspace/crons
curl -fsSL https://raw.githubusercontent.com/boraita/ipasen_mcp/main/clients/openclaw/cron/digest-prompt.md \
  -o ~/.openclaw/workspace/crons/ipasen-digest.md
# edit the placeholders, then:
P="$(cat ~/.openclaw/workspace/crons/ipasen-digest.md)"
T="ipasen__school_digest ipasen__mark_digested tareas__calendario tareas__anadir_tarea tareas__anadir_evento"
for spec in "Morning|13 7 * * *" "Evening|52 19 * * *"; do
  openclaw cron add --name "iPasen digest (${spec%%|*})" --cron "${spec#*|}" --tz Europe/Madrid --exact \
    --agent main --session isolated --message "$P" --tools "$T" \
    --model google/gemini-2.5-flash --fallbacks google/gemini-3.1-flash-lite --thinking low \
    --light-context --timeout-seconds 300 --announce --channel telegram --to <PARENT_1_CHAT_ID>
done
```

Key choices:
- `--tools` limits the job to the digest and the task app: **no iPasen action tool is reachable**
  from a cron (nobody is there to confirm).
- `NO_REPLY` as the final answer suppresses delivery, so quiet days send nothing.
- Pick minutes that run shortly **before** any existing morning/evening agenda job, so new tasks
  show up in it, and that do not collide with other jobs' minutes.
- A mid-size model follows the multi-step prompt better than the smallest ones.

To change the prompt later, edit the file and push it to both jobs:
`openclaw cron edit <id> --message "$(cat ~/.openclaw/workspace/crons/ipasen-digest.md)"`.

## 2. Forwarding to more people

A cron job delivers to one chat. To give the same message to another parent without running the
digest twice (which would create duplicate tasks), forward it with
[cron/forward_digest.py](cron/forward_digest.py) from a command job a few minutes later:

```bash
curl -fsSL https://raw.githubusercontent.com/boraita/ipasen_mcp/main/clients/openclaw/cron/forward_digest.py \
  -o ~/.openclaw/workspace/crons/forward_digest.py
F="python3 $HOME/.openclaw/workspace/crons/forward_digest.py --job <MORNING_JOB_ID> --job <EVENING_JOB_ID> --target <PARENT_2_CHAT_ID>"
openclaw cron add --name "iPasen digest: forward (Morning)" --cron "17 7 * * *" --tz Europe/Madrid --exact --command "$F" --no-deliver --timeout-seconds 120
openclaw cron add --name "iPasen digest: forward (Evening)" --cron "56 19 * * *" --tz Europe/Madrid --exact --command "$F" --no-deliver --timeout-seconds 120
```

It only forwards runs that were actually delivered (never `NO_REPLY` ones), remembers what it
sent (`~/.config/ipasen-mcp/forwarded.json`), and picks up a late run on the next turn.

## 3. Testing

```bash
openclaw cron run <EVENING_JOB_ID>                 # real run: creates tasks and sends the message
openclaw cron runs <EVENING_JOB_ID> --limit 1      # result, model, delivery status
python3 ~/.openclaw/workspace/crons/forward_digest.py --job … --target … --dry-run
```

To re-process a message during testing, remove its id from `digested_ids` in the iPasen state
file (`~/.config/ipasen-mcp/<name>/session.json`). `school_digest` itself only reads, so you can
call it by hand to see what the next run will get.

## Lessons from the first runs

- Without a per-item decision the model skipped 2 of 7 messages silently (a revision task and a
  form for the parents). `mark_digested` now requires one decision per item.
- Read all messages before acting: a "postponed to Tuesday" message must replace the original
  date, not add a second event.
- Never list usernames or passwords: some iPasen notices contain the children's credentials.
