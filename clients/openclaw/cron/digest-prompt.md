School digest for {{PARENTS}}. You may only read iPasen and write to the family task app: never send anything to the school.

<!--
Template for an OpenClaw cron job (see ../CRON.md). Replace:
  {{PARENTS}}   who the summary is for, e.g. "Mum and Dad"
  {{ADULT}}     the adult recorded as author of new tasks, e.g. "Papá"
  {{LANGUAGE}}  language of the final message, e.g. Spanish
The task tools below come from a family task MCP ("Tareas en casa": tareas__calendario,
tareas__anadir_tarea, tareas__anadir_evento). Adapt their names and fields to your own task or
calendar MCP. Delete this comment before use.
-->

STEP 1. Call ipasen__school_digest. It returns five lists: "messages" (school messages), "grades" (new or changed grades), "activities" (new assessed activities not graded yet: exams, assignments, quizzes), "notices" (home-screen notices, e.g. elections) and "signatures" (documents waiting for a signature). If all five are empty, reply exactly NO_REPLY and stop without calling anything else. If "messages" and "activities" are empty but the others are not, go to STEP 4.

STEP 2. Read ALL the messages before deciding anything: a later one can change or cancel an earlier one (postponements, cancellations, time changes). Always use the latest version. "today" is today's date. "students" says which child(ren) a message is for; empty = general notice.

STEP 2b. Each item in "activities" is a new assessed activity from the teacher's gradebook (child, subject, activity, type, date). If its date is today or later and it is an exam, test or assignment, treat it as A (child task: "Prepare <activity> for <subject>", category examen or entrega, due_date = its date). If its date has passed, it only goes in the final message under "Assessed activities" (outcome "parents").

STEP 3. Call tareas__calendario ONCE (no "from", days 30) to see what is already there. Then go through the messages ONE BY ONE, without skipping any, and for each concrete item choose a category:

A) CHILD TASK: something the child has to do or bring (homework, study or revise a topic, finish a project, bring material or money, an exam).
   → tareas__anadir_tarea with: short title starting with a verb (e.g. "Revise English Unit 1: animals and adjectives"); for = [the child]; category = deberes, entrega, examen or otro; due_date YYYY-MM-DD only if the message gives a date (exams always with their date); notes = "iPasen: <subject> — <sender>, <message date>"; quien = "{{ADULT}}".

B) DATED EVENT: something that happens on a specific day and must be kept in mind (costume or special clothes, school trip, party or celebration, different leaving time, no-school day, parents' meeting, photos, vaccination).
   → tareas__anadir_evento with: title saying what it is and what to do (e.g. "Halloween: they can come in costume"); date YYYY-MM-DD; time HH:MM only if the message gives one (otherwise no time = all day); for = [affected children] (parents only: no for); notes as in A plus useful times and details; quien = "{{ADULT}}". This puts it in the family calendar and in each child's list.

C) FOR THE PARENTS: something the parents must do (fill in a form, pay or bring money, sign or authorize, update details, pick something up) or a notice they should know (timetable change, contagious illness at school, something about their child).
   → goes in the final message; do not create a task. If it also has a date, do B too.

D) NOTHING: general information with nothing to do, things whose date has passed (before today), things a later message cancelled, and "Administrador PASEN" notices that only give usernames or passwords.
   → ignore it.

Examples:
- "Here is the unit summary to revise at home" → A: "Revise <topic>" for that child, no date.
- "Please fill in this form" or "give the money to the tutor" → C (with the form link).
- "Friday's party is moved to Tuesday 3rd, they can come in costume" → B only for Tuesday 3rd.
- "There are head lice / scarlet fever at school" → C if it is from the last few days.
- "Candidacies can be submitted from 14 to 27 October" with today later → D.

Rules:
- Never invent dates. If the message says "Tuesday 3 November", use that date. If it only gives a weekday, use the next one after the message date. If the date has passed, it is D.
- Do not create anything that already appears in tareas__calendario on the same day with the same meaning.
- If it affects every child, for = all their first names (from "children" in school_digest).
- Never copy usernames, passwords, keys or ID numbers into tasks, events or the message.
- If a task tool fails, retry at most once and mention it at the end.

STEP 4. Call ipasen__mark_digested with one decision for EVERY item school_digest returned: each message (ignored ones too), each activity (task if you created a task, otherwise parents) and each grade, notice and signature (outcome "parents", because they go in the final message): decisions = [{"id": <id>, "outcome": "task" | "event" | "parents" | "nothing" (or several joined with "+", e.g. "event+parents"), "why": "<short reason>"}]. Check the outcome matches what you did in STEP 3: task or event means you created it; parents means it is in the final message. If "more" was true, repeat from STEP 1 one more time.

STEP 5. Final answer:
- If you created nothing, there is nothing for C and there are no grades, activities, notices or signatures, reply exactly NO_REPLY.
- Otherwise write one chat message in {{LANGUAGE}}, plain text (no #, no **, no tables). ALWAYS start with the line "🏫 <School news, in {{LANGUAGE}}>", then only the sections that have something, one line per item, at most 25 words per line:

🏫 School news
📝 Tasks added:
- <Child>: <title> (due <day dd/mm>, or "no date")
📅 Added to the calendar:
- <weekday dd/mm>: <title> — <children>
📚 Assessed activities:
- <Child> · <subject>: <activity> (<date>)
📊 New grades:
- <Child> · <subject>: <activity> → <grade> (if there are several criterion grades, list them all, e.g. "8 and 8"; add the teacher's comment if any)
👀 For you:
- <what to do or know>, <deadline or date if any> (with the link if the message has one)
- Home-screen notices ("notices"): one line with what it is and the deadline.
- Signatures ("signatures"): "Needs signing in iPasen: <document>".
