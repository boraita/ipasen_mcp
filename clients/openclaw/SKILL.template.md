---
name: {{SKILL_NAME}}
description: School life of {{CHILD_NAMES}} through iPasen/Séneca (Andalusia) with the MCP tools {{PREFIX}}__*. Grades, absences, timetable, school messages, tutor, trips/authorizations and school calendar{{ACTIONS_SUMMARY}}. Use it whenever {{HOLDER}} asks about school, "el cole", iPasen, Séneca, grades ("notas"), exams, absences ("faltas"), messages from school, the tutor, trips or school holidays.
---

# iPasen ({{HOLDER}})

All tools start with `{{PREFIX}}__`. They return real data from the school (Junta de Andalucía).
School data is in Spanish; always answer {{HOLDER}} in the language they write in.

## Family

{{FAMILY}}
- The `child` parameter takes a first name ({{CHILD_EXAMPLES}}). Leave it empty for all children.

## Queries (free to use, no permission needed)

| {{HOLDER}} asks… | Tool |
|---|---|
| "what's new at school?", daily summary | `{{PREFIX}}__overview` |
| grades, exams, how is X doing in Y | `{{PREFIX}}__grades` (filter `subject`) |
| term report grades | `{{PREFIX}}__term_grades` |
| timetable, what lessons tomorrow | `{{PREFIX}}__schedule` (`day="tuesday"`) |
| absences | `{{PREFIX}}__absences`, `{{PREFIX}}__absence_notices` |
| messages from school | `{{PREFIX}}__messages` (`unread_only=true`), then `{{PREFIX}}__read_message(id)` |
| upcoming events, holidays, trips | `{{PREFIX}}__upcoming_events`, `{{PREFIX}}__school_calendar` |
| tutor and office hours | `{{PREFIX}}__tutor` |
| pending authorizations | `{{PREFIX}}__authorizations` (`only_unsigned=true`) |
| lunch, after-school activities | `{{PREFIX}}__school_services` |

Chat replies (Telegram): plain text, short sentences, no `###` headings or tables. Dates like "Mon 9/11".
Before saying "no new messages", check `unread_messages` in `overview`: messages are listed with the
children they belong to. Generic "Administrador PASEN" notices only matter if asked.

{{ACTIONS_SECTION}}

## If something fails

- "Missing IPASEN_USER…" or "Login rejected": credentials in `{{CREDENTIALS_FILE}}`. Tell {{HOLDER}}.
- "two_factor_required": ask {{HOLDER}} for the code they received and call `{{PREFIX}}__login(code="…")`.
- Séneca server errors (500, timeout): say so; retry queries only, never actions.
- Full action guide: `{{INSTALL_DIR}}/ACTIONS.md`.
