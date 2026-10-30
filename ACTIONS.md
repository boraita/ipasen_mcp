# Actions — how not to get it wrong

For anyone (human or AI) using this MCP. **Queries** (`overview`, `grades`, `messages`…) only read
and are always safe. **Actions** send something real to the school: the teacher sees it, and some
of them cannot be undone.

---

## 1. Confirmation modes

Every action is called twice: once to get a **preview**, once with a `confirm_code` to send it.
Where that code comes from depends on `IPASEN_CONFIRM_MODE` (environment or credentials file):

| Mode | Where the code goes | Use it when |
|---|---|---|
| `inline` (default) | back to the caller, inside the preview | a human approves every tool call in the client (e.g. Claude Code with permission prompts) |
| `telegram` | to the human's Telegram chat (`IPASEN_TELEGRAM_BOT_TOKEN`, `IPASEN_TELEGRAM_CHAT_ID`); the agent never sees it | autonomous or chat agents (OpenClaw, bots, crons nearby) |

> ⚠️ **Why `telegram` exists.** In `inline` mode an agent can confirm itself: it reads the code
> in the preview and calls again straight away. That happened on 2026-10-30 in a test with an
> OpenClaw agent (Gemini flash-lite): it previewed `send_message` and sent it 1.4 s later, and a
> teacher received a real message. In `telegram` mode the code is random, single-use, valid for
> 10 minutes, bound to the exact request, and voided after 3 wrong attempts. The agent can only
> continue if the human types the code back.
>
> Limits: the code is stored hashed in the session file, but an agent with shell access as the
> same OS user could still read secrets or call the API directly. This protects against a
> confused model, not against a hostile one.

---

## 2. The five golden rules

1. **Act only when the user clearly asks.** Never on the agent's own initiative, never "to test",
   never as a side effect of a query.
2. **Always two steps.** First call **without** `confirm_code`: nothing is sent; the response has
   `"status": "PREVIEW — nothing has been sent"` and a `preview`.
3. **Show the preview to the user as is** (student, dates, slots, recipients, text) and wait for an
   explicit yes (`inline`) or for the code they received (`telegram`). Then repeat the call with
   **exactly the same arguments** plus `confirm_code`.
4. **If anything changes, go back to step 2.** The code is bound to the exact request; if a comma
   changes, the response is `REJECTED … does not match` / `No pending preview`.
5. **Never invent ids or dates.** Every id comes from a previous query (table in §4). Dates are
   always `dd/mm/yyyy`.

### Reading responses

| `status` | Meaning | What to do |
|---|---|---|
| `PREVIEW — nothing has been sent` | validation passed, confirmation missing | show `preview`, ask for confirmation |
| `REJECTED — nothing has been sent` | some data is wrong (`reason` says what) | fix it with the user, back to step 2 |
| `SENT` | the server accepted it | verify with the matching query |
| tool error | server or network failure | do not blindly retry: query first to see if it applied |

> After `SENT`, **verify with a query** (e.g. `absence_notices` after `report_absence`). Séneca
> sometimes answers OK without applying a change.

---

## 3. Choosing the student

- `child` takes the first name (`"Ana"`, `"pablo"`, case- and accent-insensitive), part of the
  full name, or the enrollment number.
- In actions `child` is **mandatory and must match exactly one student**. Leaving it empty is
  rejected, so nothing is ever done "for everyone" by accident.
- `children()` lists each child with year, class group, tutor and enrollment.

---

## 4. Where each value comes from

| Value | Query | Field |
|---|---|---|
| time slots of a day | `absence_slots(child, "dd/mm/yyyy")` | `time` |
| reported absence | `absence_notices(child)` | `id` |
| recorded absence | `absences(child)` | `codeabsence` (inside `items`) |
| justification reason | `raw_get("faltas/motivos")` | `description` |
| authorization | `authorizations(child)` | `id` |
| document to sign | `documents_to_sign("pending")` | `id` |
| message | `messages()` | `id` (and `students` tells which children it belongs to) |
| recipients | `message_recipients(child)` | person or group name |
| tags | `message_tags()` | `tagName` |
| meeting teachers | `tutorships(child)` | `available_teachers[].name` |
| meeting slots | `tutorship_slots(child, teacher)` | `date` + `time` |
| existing meeting | `tutorships(child)` | `meetings[].id` |
| teacher note | `observations(child)` | `id` |
| pickup person | `pickup_people(child)` | `id` (DNI/NIE/passport) |

---

## 5. Action catalogue

Undo legend: ✅ yes, with another action · ⚠️ only until the school processes it · ❌ no.

### Absences

#### `report_absence` — tell the school about an absence (doctor, trip…) · ⚠️ `delete_absence_report`
- **Whole day(s):** `start` (+ `end` for several days), `times` empty.
- **Some hours of ONE day:** `start` = `end` and `times` with each slot's start time (`"11:00"`)
  or the full slot (`"11:00 - 11:30"`). Check `absence_slots` first.
- If two lessons share a time (support class, split group), all of them are included, like the app.
- Limits: `end` ≥ `start`; at most 31 days; slots only for a single day.
- `observations` is read by the tutor: state the reason clearly.

```
report_absence(child="Ana", start="03/11/2026", times=["11:00", "12:00"], observations="Dentista")
  → PREVIEW: slots ["11:00 - 11:30 Inglés", "12:00 - 12:30 Conocimiento del Medio"]
report_absence(child="Ana", start="03/11/2026", times=["11:00", "12:00"], observations="Dentista", confirm_code="…")
```

#### `delete_absence_report` — delete a reported absence · ❌
`report_id` = `id` from `absence_notices`.

#### `justify_absence` — justify an absence recorded by the school · ✅ `delete_justification`
- `absence_code` = `codeabsence` from `absences`. Rejected if the school does not allow it.
- `reason`: the school's own list, in Spanish — `Enfermedad` (illness), `Visita al médico`
  (doctor), `Problema familiar` (family matter), `Otros` (other).
- Optional `attachment_path` (PDF or image, max 10 MB, local path).

#### `delete_justification` — withdraw a justification · ❌
The absence becomes unjustified again.

### Authorizations and documents

#### `answer_authorization` — authorize or not a trip/activity · ❌
- Check `authorization_detail(id)` first: price, dates, conditions.
- `authorize=True` authorizes; `False` refuses. The preview shows `already_answered: true` if it
  was answered before.

#### `sign_document` — sign a document from the signature inbox · ❌
- `signature_png`: local path to a **PNG of your handwritten signature** (like the one you would
  draw in the app). The file is checked to be a PNG.
- Pending documents only.

#### `decline_document` — decline a document · ❌
`reason` is required.

### Messages

> **Important:** the school sends **one copy per child**, each with its own state (read,
> favourite…). `messages()` merges them into one entry with `students: [...]`.
> **"Administrador PASEN"** notices (`students` empty) are regenerated by the server on every
> request: they cannot be marked as read or favourited.

#### `update_message` — read, unread, favourite or delete · ✅ (except `delete`, ❌)
- `change`: `read`, `unread`, `favorite`, `unfavorite`, `delete`.
- Without `child` it applies to every copy; with `child`, only to that child's copy.

#### `tag_message` — set tags · ✅
Replaces existing tags; `[]` removes all. Only existing tags (`message_tags`).

#### `reply_message` — reply to a message · ❌
- If the message arrived for several children, **`child` is required** (the reply goes on that
  child's copy).
- Line breaks are kept. Attachments via `attachment_paths`.

#### `send_message` — write a new message to teachers · ❌
- `to`: names as listed by `message_recipients` (a unique part is enough, e.g. `"García López"`),
  or a whole group name (`"Tutores/as de unidad"`).
- If a name matches several people, the call is rejected and lists them.
- Check the `to` list in the preview: it is exactly who will receive it.

### Parent-teacher meetings

#### `request_tutorship` — book a meeting · ✅ `cancel_tutorship`
1. `tutorships(child)` → `available_teachers`.
2. `tutorship_slots(child, teacher)` → free slots with `date`, `time`, `online`, `in_person`.
3. `request_tutorship(child, teacher, day, time, online, message)`.
Rejected if the slot does not exist, is taken, or does not allow the requested mode.

#### `cancel_tutorship` — cancel a meeting · ❌
#### `answer_tutorship` — accept or decline a meeting **proposed by a teacher** · ❌

### Follow-up

#### `mark_observation` — mark a teacher's note as read/unread · ✅

#### `manage_pickup_person` — people authorized to pick the child up · ✅ (with another operation)
- `operation`: `add`, `update`, `delete`. The person is identified by `document_id`.
- `add` needs `name`, `lastname` and `phone`, and is rejected if the person already exists.
- On `update`, fields you leave empty keep their value.
- Document type is inferred: DNI/NIE → `C`, anything else → passport `P`.

---

## 6. Common recipes

**"Tomorrow Ana has the doctor from 11 to 12"**
1. `absence_slots("Ana", "<tomorrow>")` → which slots fall between 11 and 12.
2. `report_absence(child="Ana", start=…, times=["11:00", …], observations="Cita médica")` → preview.
3. User confirms (yes / Telegram code) → same call + `confirm_code`.
4. `absence_notices("Ana")` → check it is there.

**"Justify Pablo's absence on Monday, he was ill"**
1. `absences("Pablo")` → find that date's `codeabsence`.
2. `justify_absence(child="Pablo", absence_code=…, reason="Enfermedad", observations="…")` → preview → confirm.

**"Authorize the trip"**
1. `authorizations(child, only_unsigned=True)` → `id`.
2. `authorization_detail(id)` → show cost and dates.
3. `answer_authorization(child, id, authorize=True)` → preview → confirm.

**"Book an online meeting with the tutor next week"**
1. `tutor(child)` → tutor's name.
2. `tutorship_slots(child, "<name>", start, end)` → offer slots to the user.
3. `request_tutorship(...)` with the chosen slot → preview → confirm.

---

## 7. Verification status

| Action | Status |
|---|---|
| `update_message` (read/unread) | ✅ tested live both ways (2026-10-30) |
| `send_message` | ✅ sent live once — by accident, see §1 |
| `report_absence` preview in `telegram` mode from OpenClaw | ✅ stopped at the preview, nothing sent |
| Everything else | validation and previews tested against real data; request format taken from the official app v12.10.1. Not yet sent live: the first time you use one, verify the result with its query. |

Deliberately not included (they change the account, not school follow-up): changing the user's
password, email or phone, the student's photo, digital textbook licences, creating new tags.
