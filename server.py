"""Unofficial MCP server for iPasen (Séneca, Junta de Andalucía, Spain).

Talks to the same REST API as the official Android app (juntadeandalucia.ced.pasen 12.10.1):
  base  https://seneca.juntadeandalucia.es/api/public/
  login POST security/oauth/token  (grant_type=password, scope=PASENDROID, client_id=ipasen)
  auth  Authorization: Bearer <token>; the server rotates the token in the `token-renew` header.

Read-only queries plus actions (actions need a preview + confirm_code, see ACTIONS.md).
Credentials: IPASEN_USER / IPASEN_PASSWORD in the environment or in ~/.config/ipasen-mcp/credentials.env.
"""

import base64
import hashlib
import html
import json
import os
import re
import secrets
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

BASE_URL = "https://seneca.juntadeandalucia.es/api/public/"
APP_VERSION = "12.10.1"
USER_AGENT = f"iPasen/{APP_VERSION}(Google Pixel 7;ANDROID 14;Scale/2.625)"
STATE_FILE = Path(os.getenv("IPASEN_STATE_FILE", Path.home() / ".config" / "ipasen-mcp" / "session.json"))
CREDENTIALS_FILE = Path(os.getenv("IPASEN_CREDENTIALS_FILE", Path.home() / ".config" / "ipasen-mcp" / "credentials.env"))

mcp = FastMCP("ipasen", instructions="""
iPasen (Séneca): queries and actions about the user's children at their school (Andalusia, Spain).
Data from the school comes in Spanish; answer the user in their language.

Rules for ACTIONS (every tool whose description starts with "ACTION"):
1. Never send anything the user has not explicitly asked for.
2. First call the action WITHOUT confirm_code: it only returns a "preview".
3. Show the preview to the user as is (student, dates, time slots, recipients, text) and wait for
   their "yes". Only then repeat the call with the SAME arguments + confirm_code.
   If the response says the code was sent by Telegram, you do not know it: ask the user for it and
   use exactly what they type. Never invent or derive it.
4. If the user changes anything, go back to step 2: the previous code is no longer valid.
5. Always use ids taken from a previous query (absences, messages, authorizations...);
   never invent ids or dates. Dates are written dd/mm/yyyy.
Full guide: ACTIONS.md next to server.py.
""")

_client = httpx.Client(
    base_url=BASE_URL,
    headers={"Accept-Charset": "utf-8", "User-agent": USER_AGENT},
    timeout=30,
)


# ── SESSION ───────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(**changes) -> None:
    state = _load_state()
    state.update(changes)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))
    STATE_FILE.chmod(0o600)


def _setting(key: str, default: str = "") -> str:
    """Config value: process environment or, if missing, CREDENTIALS_FILE (KEY=value lines).

    The file keeps secrets (password, bot token) out of the MCP client config and its backups.
    """
    if os.getenv(key):
        return os.environ[key]
    if CREDENTIALS_FILE.is_file():
        for line in CREDENTIALS_FILE.read_text().splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and name.strip() == key and not name.startswith("#"):
                return value.strip().strip("'\"")
    return default


def _credentials() -> tuple[str, str]:
    user, password = _setting("IPASEN_USER"), _setting("IPASEN_PASSWORD")
    if not user or not password:
        raise RuntimeError(f"Missing IPASEN_USER / IPASEN_PASSWORD (MCP environment or {CREDENTIALS_FILE})")
    return user, password


def _token_login(code_2fa: str = "", trust_device: bool = True) -> dict:
    user, password = _credentials()

    form = {
        "username": user,
        "password": password,
        "grant_type": "password",
        "scope": "PASENDROID",
        "client_id": "ipasen",
    }
    state = _load_state()
    if code_2fa:
        if not state.get("two_factor_token"):
            raise RuntimeError("No pending 2FA challenge: call login() first")
        form["2FactorAuthToken"] = state["two_factor_token"]
        form["2FactorAuthCode"] = code_2fa
        form["trustDevice"] = str(trust_device).lower()
    elif state.get("trusted_two_factor_token"):
        # Device already trusted: the server accepts the previous 2FA token.
        form["2FactorAuthToken"] = state["trusted_two_factor_token"]

    r = _client.post("security/oauth/token", data=form)
    body = r.json()
    if r.is_success and body.get("access_token"):
        _save_state(
            access_token=body["access_token"],
            two_factor_token=None,
            trusted_two_factor_token=body.get("twoFactorAuthToken") or state.get("trusted_two_factor_token"),
        )
        return {"ok": True}

    error = (body.get("errors") or [{}])[0]
    challenge = body.get("data") or {}
    if challenge.get("twoFactorAuthToken"):
        _save_state(two_factor_token=challenge["twoFactorAuthToken"])
        return {
            "ok": False,
            "two_factor_required": True,
            "sent_to": challenge.get("sendTo"),
            "message": error.get("detail") or body.get("message"),
        }
    raise RuntimeError(f"Login rejected ({error.get('status', r.status_code)}): {error.get('detail') or body.get('message')}")


def _request(method: str, path: str, params: dict | None = None, retry: bool = True, **body):
    token = _load_state().get("access_token")
    if not token:
        result = _token_login()
        if not result["ok"]:
            raise RuntimeError(f"2FA code required (sent to {result.get('sent_to')}): use login(code=...)")
        token = _load_state()["access_token"]

    params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    r = _client.request(method, path, params=params, headers={"Authorization": f"Bearer {token}"}, **body)
    if renewed := r.headers.get("token-renew"):
        _save_state(access_token=renewed)
    if r.status_code == 401 and retry:
        _save_state(access_token=None)
        return _request(method, path, params, retry=False, **body)

    try:
        body = r.json()
    except ValueError:
        r.raise_for_status()
        return r.text
    if not r.is_success or body.get("success") is False:
        error = (body.get("errors") or [{}])[0]
        raise RuntimeError(f"{path}: {error.get('detail') or body.get('message') or r.status_code}")
    return _clean(body.get("data", body))


def _clean(obj):
    """Drop Java metadata (@class) and empty fields from responses."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items() if k != "@class" and v not in (None, "", [])}
    if isinstance(obj, list):
        return [_clean(x) for x in obj]
    return obj


def _text(markup: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>", "\n", markup or "", flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t\xa0]+", " ", text)).strip()


def _fold(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()


def _get(path: str, **params):
    return _request("GET", path, params)


# ── CHILDREN ──────────────────────────────────────────────────────────────────

_children_cache: list[dict] = []


def _children() -> list[dict]:
    if not _children_cache:
        _children_cache.extend(_get("generales/info-session").get("sons", []))
    return _children_cache


def _resolve(child: str = "") -> list[dict]:
    """Children matching `child` (first name, part of the full name or enrollment number); empty = all."""
    kids = _children()
    if not child:
        return kids
    needle = _fold(child.strip())
    found = [k for k in kids if needle in (k["enrollment"], _fold(k.get("fullname", "")), _fold(k.get("simpleName", "")))
             or needle in _fold(k.get("fullname", ""))]
    if not found:
        names = ", ".join(k.get("simpleName", k["enrollment"]) for k in kids)
        raise ValueError(f"Student '{child}' not found. Children on this account: {names}")
    return found


def _per_child(child: str, fetch) -> dict:
    """Run `fetch(kid)` for each selected child and return the results keyed by first name."""
    return {k.get("simpleName", k["enrollment"]): fetch(k) for k in _resolve(child)}


def _iso(day: str) -> str:
    d, m, y = day.split("/")
    return f"{y}-{m}-{d}"


# ── ACCOUNT ───────────────────────────────────────────────────────────────────

@mcp.tool()
def login(code: str = "", trust_device: bool = True) -> dict:
    """Log in to iPasen with IPASEN_USER/IPASEN_PASSWORD (normally automatic).

    If the account has two-factor auth, the first call returns two_factor_required and where
    the code was sent. Call again with code='123456' to finish.

    Args:
        code: 2FA code received (second call only)
        trust_device: trust this machine so 2FA is not asked again
    """
    return _token_login(code, trust_device)


@mcp.tool()
def logout() -> dict:
    """Forget the stored token (keeps the 2FA device trust)."""
    _save_state(access_token=None, two_factor_token=None)
    _children_cache.clear()
    return {"ok": True}


@mcp.tool()
def children() -> list:
    """Children linked to the account: name, year, class group, tutor, school and enrollment number.

    Every other tool accepts `child` as a first name (e.g. 'Ana') or enrollment number;
    leave it empty to get all children.
    """
    return [
        {
            "name": k.get("fullname"),
            "child": k.get("simpleName"),
            "age": k.get("age"),
            "course": k.get("course"),
            "group": k.get("unity"),
            "tutor": k.get("tutor"),
            "school": k.get("description"),
            "center_code": k.get("centerCode"),
            "enrollment": k["enrollment"],
        }
        for k in _children()
    ]


@mcp.tool()
def my_data() -> dict:
    """Personal and contact details of the legal guardian (account holder)."""
    return _get("generales/get-datos-usuario")


# ── SUMMARY / AGENDA ──────────────────────────────────────────────────────────

@mcp.tool()
def upcoming_events(days: int = 30, start: str = "") -> list:
    """Agenda: holidays, trips, exams and school events in the coming days.

    Args:
        days: how many days ahead (default 30)
        start: first day yyyy-mm-dd (default today; cannot be before the school year starts)
    """
    first = date.fromisoformat(start) if start else date.today()
    events = _get("generales/get-agenda", initdate=first.isoformat(), enddate=(first + timedelta(days=days)).isoformat())
    return [
        {k: e.get(k) for k in ("initdate", "endDate", "initHour", "endHour", "title", "description", "category") if e.get(k)}
        for e in sorted(events, key=lambda e: _iso(e["initdate"]))
    ]


@mcp.tool()
def overview(child: str = "") -> dict:
    """Quick summary: unread messages, upcoming events (14 days), absences, latest grades and
    authorizations waiting for a signature. Good first query of the day.

    Args:
        child: first name or enrollment (empty = all)
    """
    unread = [m for m in messages(unread_only=True) if not child or any(_fold(child) in _fold(s) for s in m.get("students", []))]

    def per_kid(k):
        e = k["enrollment"]
        graded = [a for a in activity_grades(k["enrollment"], only_graded=True)]
        return {
            "course": f'{k.get("course")} {k.get("unity")}',
            "absences": len(_get("faltas/get-faltas", enrollment=e)),
            "latest_grades": sorted(graded, key=lambda a: _iso(a.get("date", "01/01/1970")), reverse=True)[:8],
            "authorizations_unsigned": [a.get("motive") for a in _get("seguimiento/get-aut-act", enrollment=e) if not a.get("signature")],
            "observations": len(_get("seguimiento/observaciones", enrollment=e)),
            "behaviour_incidents": len(_get("seguimiento/get-conductas-contrarias", enrollment=e)),
        }

    return {
        "unread_messages": [{"id": m["id"], "date": m["date"], "from": m["from"], "subject": m["subject"], "students": m["students"]}
                            for m in unread],
        "next_events": upcoming_events(days=14),
        "children": _per_child(child, per_kid),
    }


# ── STUDENT ───────────────────────────────────────────────────────────────────

_WEEKDAYS = {"monday": "lunes", "tuesday": "martes", "wednesday": "miercoles", "thursday": "jueves", "friday": "viernes"}


@mcp.tool()
def student_profile(child: str = "") -> dict:
    """Student record (personal data, enrollment, address, contacts)."""
    return _per_child(child, lambda k: _get("seguimiento/datos-alumno", enrollment=k["enrollment"]))


@mcp.tool()
def schedule(child: str = "", day: str = "") -> dict:
    """Weekly timetable: time slot, subject and teacher, grouped by day.

    Args:
        child: first name or enrollment (empty = all)
        day: optional, one day ('monday'/'lunes', 'tuesday'/'martes'...)
    """
    wanted = _WEEKDAYS.get(_fold(day), _fold(day))

    def fetch(k):
        week: dict[str, list[str]] = {}
        for s in sorted(_get("horarios/get-horario", enrollment=k["enrollment"]), key=lambda s: s.get("section", 0)):
            if day and wanted != _fold(s["day"]):
                continue
            week.setdefault(s["day"], []).append(f'{s.get("hoursection")}  {s.get("subject")} ({s.get("prof", "")})')
        return week
    return _per_child(child, fetch)


@mcp.tool()
def tutor(child: str = "") -> dict:
    """Class tutor, their office hours for families and the teaching team per subject."""
    def fetch(k):
        t = _get("horarios/tutoria", enrollment=k["enrollment"])
        return {
            "tutor": t.get("tutorName"),
            "group": t.get("unity"),
            "family_hours": [f'{a["day"]} {a["time_started"]}-{a["time_end"]}: {a["activity"]}' for a in t.get("activitys", [])],
            "teachers": {x["description"]: x.get("teachers") for x in t.get("teachingTeam", [])},
        }
    return _per_child(child, fetch)


@mcp.tool()
def absences(child: str = "") -> dict:
    """Absences and late arrivals recorded by the school, with their justification status."""
    return _per_child(child, lambda k: _get("faltas/get-faltas", enrollment=k["enrollment"]))


@mcp.tool()
def absence_notices(child: str = "") -> dict:
    """Absences reported by the family (doctor appointments, etc.) and the time slots affected."""
    def fetch(k):
        return [
            {
                "id": n.get("codenotification"),
                "sent": n.get("send_date"),
                "from": n.get("start_date"),
                "to": n.get("end_date"),
                "reason": (n.get("observation") or "").strip(),
                "sections": [f'{s.get("description")} {s.get("prof", "")}' for s in n.get("sections", [])],
            }
            for n in _get("faltas/get-notificaciones", enrollment=k["enrollment"])
        ]
    return _per_child(child, fetch)


@mcp.tool()
def subjects(child: str = "") -> dict:
    """Enrolled subjects with their status (passed, pending...) and subject code."""
    def fetch(k):
        return [
            {"subject": s.get("subject"), "code": s.get("codesubject"), "group": s.get("subjectgroup"),
             "unit": s.get("unit"), "state": s.get("state"), "approved": s.get("approved")}
            for s in _get("seguimiento/get-materias-matricula", enrollment=k["enrollment"])
        ]
    return _per_child(child, fetch)


@mcp.tool()
def term_grades(child: str = "") -> dict:
    """Grading periods of the year (1st, 2nd, 3rd, final) with dates and, once the school
    publishes them, the grades of each period."""
    return _per_child(child, lambda k: _get("seguimiento/get-notas-convocatorias", enrollment=k["enrollment"]))


def activity_grades(enrollment: str, subject: str = "", only_graded: bool = False, period: str = "") -> list:
    rows = []
    for p in _get("seguimiento/get-actividades-evaluables-criterios", enrollment=enrollment):
        if period and _fold(period) not in _fold(p.get("description", "")):
            continue
        for s in p.get("subjects", []):
            if subject and _fold(subject) not in _fold(s.get("name", "")):
                continue
            for a in s.get("activities", []):
                scores = [n for d in a.get("diary_score", []) for n in d.get("note_array", [])]
                overall = [d["note"] for d in a.get("diary_score", []) if d.get("note")]
                comments = [d["comment"] for d in a.get("diary_score", []) if d.get("comment")]
                if only_graded and not scores and not overall:
                    continue
                rows.append({
                    "period": p.get("description"),
                    "subject": s.get("name", "").strip(),
                    "teacher": a.get("professor"),
                    "activity": a.get("description_activity"),
                    "type": a.get("descriptionCatacteva") or a.get("type_activity"),
                    "date": a.get("start_date"),
                    "delivered": next((d["delivery_date"] for d in a.get("diary_score", []) if d.get("delivery_date")), None),
                    "grade": overall[0] if overall else None,
                    "criteria_grades": [{"grade": n.get("note"), "criterion": n.get("description")} for n in scores],
                    "comment": comments[0] if comments else None,
                })
    return [{k: v for k, v in r.items() if v not in (None, [], "")} for r in rows]


@mcp.tool()
def grades(child: str = "", subject: str = "", only_graded: bool = True, period: str = "") -> dict:
    """Grades of exams, assignments and assessed activities (teacher's gradebook),
    including the grade of each assessment criterion.

    Args:
        child: first name or enrollment (empty = all)
        subject: filter by subject (e.g. 'inglés', 'matemáticas' — school data is in Spanish)
        only_graded: True (default) only graded activities; False also includes pending ones
        period: filter by grading period ('1ª', '2ª'...)
    """
    return _per_child(child, lambda k: activity_grades(k["enrollment"], subject, only_graded, period))


@mcp.tool()
def observations(child: str = "") -> dict:
    """Notes written by teachers about the student."""
    return _per_child(child, lambda k: _get("seguimiento/observaciones", enrollment=k["enrollment"]))


@mcp.tool()
def behaviour(child: str = "") -> dict:
    """Behaviour incidents recorded by the school."""
    return _per_child(child, lambda k: _get("seguimiento/get-conductas-contrarias", enrollment=k["enrollment"]))


@mcp.tool()
def authorizations(child: str = "", only_unsigned: bool = False) -> dict:
    """Activity authorizations (trips, photos, outings): signed or pending.

    Args:
        child: first name or enrollment (empty = all)
        only_unsigned: True to list only those still waiting for an answer
    """
    def fetch(k):
        return [
            {"activity": a.get("motive"), "signed": a.get("signature", False), "signed_on": a.get("signatureDate"),
             "period": a.get("signaturePeriod"), "has_cost": a.get("hasAmount"), "id": a.get("aluatprotut")}
            for a in _get("seguimiento/get-aut-act", enrollment=k["enrollment"])
            if not (only_unsigned and a.get("signature"))
        ]
    return _per_child(child, fetch)


@mcp.tool()
def authorization_detail(authorization_id: str) -> dict:
    """Details of one authorization (id = `id` field from authorizations)."""
    return _get("seguimiento/get-det-aut-act", aluatprotut=authorization_id)


@mcp.tool()
def pickup_people(child: str = "") -> dict:
    """People authorized to pick the student up from school."""
    return _per_child(child, lambda k: _get("seguimiento/get-per-aut-rec-alu", enrollment=k["enrollment"]))


@mcp.tool()
def school_services(child: str = "") -> dict:
    """Contracted services: lunch, early-morning care, after-school activities, with schedule and monthly price."""
    return _per_child(child, lambda k: _get("centros/servicios-centro", enrollment=k["enrollment"]))


@mcp.tool()
def school_history(child: str = "") -> dict:
    """School history: previous years, schools and results."""
    return _per_child(child, lambda k: _get("seguimiento/trayectoria-escolar", enrollment=k["enrollment"]))


@mcp.tool()
def textbooks(child: str = "") -> dict:
    """Textbooks from the free-textbook programme (vouchers / digital licences)."""
    return _per_child(child, lambda k: _get("horarios/libros-texto", enrollment=k["enrollment"]))


@mcp.tool()
def tutorships(child: str = "", only_active: bool | None = None) -> dict:
    """Parent-teacher meetings requested or scheduled, and teachers available for them."""
    return _per_child(child, lambda k: {
        "meetings": _get("horarios/ipasen/tutorias", enrollment=k["enrollment"], isActive=only_active),
        "available_teachers": _get("horarios/ipasen/profesores-tutorias", enrollment=k["enrollment"]),
    })


@mcp.tool()
def access_keys(child: str = "") -> dict:
    """The student's education accounts (iANDE, Moodle...) and their links."""
    return _per_child(child, lambda k: _get("usuarios/mis-claves", enrollment=k["enrollment"]))


@mcp.tool()
def scholarships(child: str = "") -> dict:
    """Scholarships and grants applied for or awarded."""
    return _per_child(child, lambda k: _get("centros/get-becas", enrollment=k["enrollment"]))


@mcp.tool()
def documents_to_sign(state: str = "pending") -> list:
    """School documents in the signature inbox.

    Args:
        state: 'pending' (default), 'signed' or 'declined'
    """
    code = {"pending": "P", "signed": "F", "declined": "R"}[state]
    return _get("firma/ipasen/bandeja-firma", typeState=code, enrollment="", page=1)


# ── SCHOOL ────────────────────────────────────────────────────────────────────

@mcp.tool()
def school_info(child: str = "") -> dict:
    """Each child's school: name, phone, website, year and class group.

    (The `centros/datos-centro` endpoint fails server-side when several children attend the same
    school, so the data comes from the app's home screen instead.)
    """
    sons = _get("generales/home")[0]["session"][0]["sons"]
    wanted = {k["enrollment"] for k in _resolve(child)}
    return {
        s["simpleName"]: {k: e.get(k) for k in ("denomination", "center", "phone_centro", "web", "course", "unit", "tutor")}
        for s in sons for e in s.get("enrollment", []) if e["enrollment"] in wanted
    }


@mcp.tool()
def school_calendar(child: str = "") -> list:
    """Official school calendar: holidays, start/end of terms and breaks."""
    code = _resolve(child)[0]["centerCode"]
    return [
        {"month": m.get("section"), "days": [f'{d["dateholiday"]}: {d["description"]}' for d in m.get("items", [])]}
        for m in _get("centros/calendario-centro", centercode=code)
    ]


@mcp.tool()
def notice_board(child: str = "") -> dict:
    """School notice board."""
    return _per_child(child, lambda k: _get("centros/get-tablon-anuncios", enrollment=k["enrollment"], centercode=k["centerCode"]))


# ── MESSAGES ──────────────────────────────────────────────────────────────────

@mcp.tool()
def messages(page: int = 1, unread_only: bool = False, child: str = "", search: str = "") -> list:
    """Messages received from the school and teachers (newest first), with a short preview.

    Messages sent about several children are merged into one entry with the list of students.
    Use read_message(id) for the full text.

    Args:
        page: page number (about 10 messages per page)
        unread_only: only unread messages
        child: only messages about one child (first name or enrollment)
        search: filter by text in subject/sender/body
    """
    names = {k["enrollment"]: k.get("simpleName") for k in _children()}
    enrollments = ",".join(k["enrollment"] for k in _resolve(child)) if child else ""
    merged: dict = {}
    for m in _get("mensajeria/get-comunicaciones", page=page, enrollments=enrollments):
        if unread_only and m.get("isread"):
            continue
        body = _text(m.get("body_message", ""))
        if search and _fold(search) not in _fold(f'{m.get("subject")} {m.get("sender")} {body}'):
            continue
        item = merged.setdefault(m["id"], {
            "id": m["id"], "date": m.get("date"), "from": m.get("sender"), "subject": m.get("subject"),
            "unread": not m.get("isread"), "attachments": m.get("has_adj", False),
            "preview": body[:280] + ("…" if len(body) > 280 else ""), "students": [],
        })
        item["unread"] = item["unread"] or not m.get("isread")
        student = names.get(str(m.get("enrollment")))
        if student and student not in item["students"]:
            item["students"].append(student)
    return list(merged.values())


@mcp.tool()
def read_message(message_id: int) -> dict:
    """Full text of a message and its replies (does not mark it as read).

    Args:
        message_id: `id` returned by messages
    """
    # The endpoint returns the whole conversation with that sender (every message, oldest first,
    # 10 per page, one copy per child), so keep only the requested message. Its `pages` field just
    # echoes the page asked for: read until an empty page.
    names = {k["enrollment"]: k.get("simpleName") for k in _children()}
    found = []
    for page in range(1, 60):
        batch = _get("mensajeria/get-conversaciones", id=message_id, page=page).get("messages", [])
        found += [m for m in batch if m.get("id") == message_id]
        if not batch or (found and all(m.get("id") != message_id for m in batch)):
            break
    if not found:
        raise ValueError(f"Message {message_id} not found in its conversation (use messages() for valid ids)")
    m = found[0]
    return {
        "id": message_id, "date": m.get("date"), "from": m.get("sender"), "subject": m.get("subject"),
        "students": [n for n in dict.fromkeys(names.get(str(x.get("enrollment"))) for x in found) if n],
        "text": _text(m.get("body_message", "")),
        "attachments": [{"name": a.get("attachmentName") or a.get("name"), "url": a.get("url")} for a in m.get("attachments", [])],
    }


@mcp.tool()
def sent_messages(page: int = 1) -> list:
    """Messages sent by the account holder."""
    return _get("mensajeria/get-mensajes-salida", page=page)


@mcp.tool()
def notifications(page: int = 1) -> list:
    """Automatic notifications inbox (absences, published grades...)."""
    return _get("mensajeria/get-bandeja-notificaciones", page=page)


@mcp.tool()
def news() -> list:
    """General announcements from the regional education authority or the school."""
    return _get("mensajeria/avisos")


@mcp.tool()
def raw_get(path: str, params: dict | None = None):
    """Generic GET to the iPasen API for endpoints not covered above (read-only).

    Args:
        path: path relative to /api/public/, e.g. 'faltas/motivos'
        params: optional query string (students go as 'enrollment')
    """
    return _request("GET", path.lstrip("/"), params)


# ── ACTIONS ───────────────────────────────────────────────────────────────────
# All actions follow the same two-step protocol (see ACTIONS.md):
#   1) without confirm_code → validate, return {"preview", ...} and send NOTHING
#   2) with the confirm_code and the same arguments → send
# The code is bound to the exact request, so any change forces step 1 again.

class _Abort(ValueError):
    pass


def _check(condition, message: str):
    if not condition:
        raise _Abort(message)


def _date(value: str) -> date:
    try:
        d, m, y = value.strip().split("/")
        return date(int(y), int(m), int(d))
    except ValueError:
        raise _Abort(f"Invalid date '{value}': use dd/mm/yyyy (e.g. 03/11/2026)") from None


def _one(child: str) -> dict:
    kids = _resolve(child)
    _check(child and len(kids) == 1, f"Name exactly one student in `child` (options: {', '.join(k['simpleName'] for k in _children())})")
    return kids[0]


def _file(path: str) -> tuple[str, bytes]:
    f = Path(path).expanduser()
    _check(f.is_file(), f"File not found: {f}")
    _check(f.stat().st_size <= 10 * 1024 * 1024, f"{f.name} is larger than 10 MB")
    return f.name, f.read_bytes()


CONFIRM_TTL = 600
CONFIRM_MAX_ATTEMPTS = 3


def _send(action: str, path: str, preview: dict, confirm_code: str, *, json_body=None, parts=None, files_digest=""):
    """Gate for every action. Returns the preview (step 1) or None when the request may be sent (step 2).

    IPASEN_CONFIRM_MODE:
      inline   (default) the code comes back in the response. Fine when a human approves every
               tool call (e.g. Claude Code with permission prompts); it does NOT stop an autonomous
               agent from confirming itself.
      telegram a random single-use code is sent to the human over Telegram
               (IPASEN_TELEGRAM_BOT_TOKEN / IPASEN_TELEGRAM_CHAT_ID); the agent never sees it.
    """
    payload = json.dumps({"path": path, "json": json_body, "parts": parts, "files": files_digest}, sort_keys=True, ensure_ascii=False, default=str)
    request_hash = hashlib.sha256(payload.encode()).hexdigest()
    mode = _setting("IPASEN_CONFIRM_MODE", "inline").lower()
    _check(mode in ("inline", "telegram"), f"Invalid IPASEN_CONFIRM_MODE '{mode}' (inline or telegram)")

    if mode == "inline":
        code = request_hash[:6].upper()
        if not confirm_code:
            return {
                "status": "PREVIEW — nothing has been sent",
                "action": action,
                "preview": preview,
                "confirm_code": code,
                "next_step": "Show the preview to the user. If they say yes, repeat the call with the same arguments and confirm_code.",
            }
        _check(confirm_code.strip().upper() == code,
               "confirm_code does not match this request (some data changed). Ask for a new preview without confirm_code.")
        return None

    now = time.time()
    pending = {h: p for h, p in (_load_state().get("pending_actions") or {}).items() if p["expires"] > now}
    if not confirm_code:
        code = f"{secrets.randbelow(10**6):06d}"
        salt = secrets.token_hex(8)
        _telegram(f"🔐 iPasen · {action}\n\n{_plain(preview)}\n\nCode: {code}\n"
                  f"Valid for {CONFIRM_TTL // 60} min and only for this exact request. "
                  "Give it to the assistant only if you want this sent; otherwise ignore it.")
        pending[request_hash] = {"salt": salt, "code": hashlib.sha256((salt + code).encode()).hexdigest(),
                                 "expires": now + CONFIRM_TTL, "attempts": 0}
        _save_state(pending_actions=pending)
        return {
            "status": "PREVIEW — nothing has been sent",
            "action": action,
            "preview": preview,
            "confirm_code": "SENT TO THE USER BY TELEGRAM. You do not know it: never invent or guess it.",
            "next_step": "Show the preview and ask the user for the 6-digit code they just received. "
                         "Only after they type it, repeat the call with the same arguments and that confirm_code.",
        }
    entry = pending.get(request_hash)
    if not entry:
        _save_state(pending_actions=pending)
        raise _Abort("No pending preview for this exact request (expired, already used, or some data changed). "
                     "Ask for a new preview without confirm_code.")
    if hashlib.sha256((entry["salt"] + confirm_code.strip()).encode()).hexdigest() != entry["code"]:
        entry["attempts"] += 1
        if entry["attempts"] >= CONFIRM_MAX_ATTEMPTS:
            del pending[request_hash]
        _save_state(pending_actions=pending)
        raise _Abort("Wrong code." + (" Too many attempts: ask for a new preview." if request_hash not in pending
                                      else " Ask the user to check it."))
    del pending[request_hash]
    _save_state(pending_actions=pending)
    return None


def _plain(preview: dict) -> str:
    lines = []
    for key, value in preview.items():
        if isinstance(value, list):
            lines.append(f"{key}:" + "".join(f"\n  · {v}" for v in value))
        elif isinstance(value, dict):
            lines.append(f"{key}: " + ", ".join(f"{k}={v}" for k, v in value.items()))
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


def _telegram(text: str) -> None:
    token, chat = _setting("IPASEN_TELEGRAM_BOT_TOKEN"), _setting("IPASEN_TELEGRAM_CHAT_ID")
    _check(token and chat, "Telegram mode without IPASEN_TELEGRAM_BOT_TOKEN / IPASEN_TELEGRAM_CHAT_ID: cannot ask for confirmation")
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": text}, timeout=20)
        ok = r.json().get("ok")
    except (httpx.HTTPError, ValueError) as e:
        raise _Abort(f"Could not send the code by Telegram ({type(e).__name__}); nothing has been sent") from None
    _check(ok, f"Telegram refused to deliver the code ({r.status_code}); nothing has been sent")


def _post(path: str, *, json_body=None, files=None):
    result = _request("POST", path, json=json_body, files=files) if files else _request("POST", path, json=json_body)
    return {"status": "SENT", "endpoint": path, "server_response": result}


def _message_copies(message_id: int, child: str = "", pages: int = 40) -> list[dict]:
    """Copies of a message in the inbox: the school sends one per child, each with its own id_message."""
    wanted = {k["enrollment"] for k in _resolve(child)} if child else None
    copies = []
    for page in range(1, pages + 1):
        batch = _get("mensajeria/get-comunicaciones", page=page)
        copies += [m for m in batch if m.get("id") == message_id and (wanted is None or str(m.get("enrollment")) in wanted)]
        if not batch or (copies and all(m.get("id") != message_id for m in batch)):
            break
    _check(copies, f"Message {message_id}{' for ' + child if child else ''} not found in the last {pages} pages (use messages())")
    return copies


def _copy_label(m: dict) -> str:
    names = {k["enrollment"]: k.get("simpleName") for k in _children()}
    return names.get(str(m.get("enrollment")), "general")


def _guard(fn):
    """Turn validation errors into a clear response instead of a generic tool failure."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except _Abort as e:
            return {"status": "REJECTED — nothing has been sent", "reason": str(e)}
    wrapper.__name__, wrapper.__doc__ = fn.__name__, fn.__doc__
    wrapper.__signature__ = __import__("inspect").signature(fn)
    return wrapper


# ── Absences ──────────────────────────────────────────────────────────────────

@mcp.tool()
def absence_slots(child: str, day: str) -> list:
    """Time slots of one specific day (to report a partial-day absence). Read-only.

    Args:
        child: student's first name
        day: dd/mm/yyyy
    """
    k = _one(child)
    return [{"time": s.get("hoursection"), "subject": s.get("subject"), "teacher": s.get("prof"), "code": s.get("codesection")}
            for s in _get("horarios/get-horario", enrollment=k["enrollment"], date=_date(day).strftime("%d/%m/%Y"))]


@mcp.tool()
@_guard
def report_absence(child: str, start: str, end: str = "", times: list[str] | None = None,
                   observations: str = "", confirm_code: str = "") -> dict:
    """ACTION · Tell the school the student will miss / missed school (doctor, trip...).

    - Whole day(s): give start (and end for several days) and leave `times` empty.
    - Only some hours of ONE day: start=end and `times` with the start time of each slot
      ("11:00", "11:30") or the full slot ("11:00 - 11:30"). See absence_slots().
    Can be undone with delete_absence_report (while the school has not processed it).

    Args:
        child: student's first name (exactly one)
        start: first day, dd/mm/yyyy
        end: last day, dd/mm/yyyy (empty = same day)
        times: specific slots (single day only); empty = whole day
        observations: reason the tutor will read (e.g. "Cita en el dentista")
        confirm_code: empty for the preview; the code to send
    """
    k = _one(child)
    first, last = _date(start), _date(end or start)
    _check(last >= first, "End date is before start date")
    _check((last - first).days <= 31, "More than 31 days: split it or talk to the school")
    sections, slot_desc = None, ["Whole day"]
    if times:
        _check(first == last, "Time slots can only be given for a single day (start = end)")
        slots = _get("horarios/get-horario", enrollment=k["enrollment"], date=first.strftime("%d/%m/%Y"))
        _check(slots, f"{k['simpleName']} has no lessons on {first:%d/%m/%Y}")
        sections, slot_desc = [], []
        for t in times:
            wanted = re.sub(r"\s", "", t)
            match = [s for s in slots if re.sub(r"\s", "", s["hoursection"]) == wanted or re.sub(r"\s", "", s["hoursection"]).split("-")[0] == wanted]
            _check(match, f"Slot '{t}' does not exist that day. Slots: {', '.join(dict.fromkeys(s['hoursection'] for s in slots))}")
            # Several lessons can share a time (e.g. support classes): include them all, like the app does.
            for slot in match:
                if slot["codesection"] not in sections:
                    sections.append(slot["codesection"])
                    slot_desc.append(f'{slot["hoursection"]} {slot["subject"]}')
    body = {"enrollment": k["enrollment"], "initdate": first.strftime("%d/%m/%Y"), "enddate": last.strftime("%d/%m/%Y"),
            "observations": observations, "sectionscode": sections}
    preview = {"student": k["fullname"], "from": body["initdate"], "to": body["enddate"], "slots": slot_desc, "observations": observations or "(empty)"}
    return _send("Report absence", "faltas/nueva-falta", preview, confirm_code, json_body=body) or _post("faltas/nueva-falta", json_body=body)


@mcp.tool()
@_guard
def delete_absence_report(child: str, report_id: int, confirm_code: str = "") -> dict:
    """ACTION · Delete an absence reported by the family (the one from report_absence).

    Args:
        child: student's first name
        report_id: `id` returned by absence_notices()
        confirm_code: empty for the preview; the code to send
    """
    k = _one(child)
    match = [n for n in _get("faltas/get-notificaciones", enrollment=k["enrollment"]) if n.get("codenotification") == report_id]
    _check(match, f"No reported absence with id {report_id} for {k['simpleName']}")
    n = match[0]
    body = {"codenotification": report_id}
    preview = {"student": k["fullname"], "delete_absence": f'{n.get("start_date")} → {n.get("end_date")}', "reason": n.get("observation")}
    return _send("Delete reported absence", "faltas/borrar-notificacion", preview, confirm_code, json_body=body) or _post("faltas/borrar-notificacion", json_body=body)


def _absence_items(k: dict) -> list[dict]:
    return [i for sec in _get("faltas/get-faltas", enrollment=k["enrollment"]) for i in sec.get("items", [])]


@mcp.tool()
@_guard
def justify_absence(child: str, absence_code: str, reason: str, observations: str = "",
                    attachment_path: str = "", confirm_code: str = "") -> dict:
    """ACTION · Justify an absence recorded by the school.

    Args:
        child: student's first name
        absence_code: `codeabsence` of the absence (from absences())
        reason: one of the school's reasons, in Spanish: 'Enfermedad' (illness), 'Visita al médico'
                (doctor), 'Problema familiar' (family matter) or 'Otros' (other) — or its code
        observations: explanation for the tutor
        attachment_path: optional local path to a supporting document (PDF/image, max 10 MB)
        confirm_code: empty for the preview; the code to send
    """
    k = _one(child)
    match = [i for i in _absence_items(k) if str(i.get("codeabsence")) == str(absence_code)]
    _check(match, f"{k['simpleName']} has no absence with code {absence_code} (see absences())")
    item = match[0]
    _check(item.get("canjustified", True), "The school does not allow justifying this absence from iPasen")
    motives = _get("faltas/motivos")
    motive = [m for m in motives if str(m["code"]) == reason or _fold(m["description"]) == _fold(reason)]
    _check(motive, f"Invalid reason '{reason}'. Options: {', '.join(m['description'] for m in motives)}")
    name, content = _file(attachment_path) if attachment_path else ("", b"")
    parts = {"codeabsence": str(absence_code), "codemotive": str(motive[0]["code"]), "obaservations": observations}
    preview = {"student": k["fullname"], "absence": f'{item.get("dateabsence")} {item.get("section", "")} {item.get("subject", "")}'.strip(),
               "reason": motive[0]["description"], "observations": observations or "(empty)", "attachment": name or "(none)"}
    digest = hashlib.sha256(content).hexdigest()
    if (r := _send("Justify absence", "faltas/justificar-falta", preview, confirm_code, parts=parts, files_digest=digest)):
        return r
    files = [(key, (None, value, "text/plain")) for key, value in parts.items()]
    files.append(("attachment", (name, content, "application/octet-stream" if name else None)))
    return _post("faltas/justificar-falta", files=files)


@mcp.tool()
@_guard
def delete_justification(child: str, absence_code: str, confirm_code: str = "") -> dict:
    """ACTION · Withdraw the justification sent for an absence (it becomes unjustified again).

    Args:
        child: student's first name
        absence_code: `codeabsence` of the justified absence (absences())
        confirm_code: empty for the preview; the code to send
    """
    k = _one(child)
    match = [i for i in _absence_items(k) if str(i.get("codeabsence")) == str(absence_code) and i.get("codejustification")]
    _check(match, f"Absence {absence_code} of {k['simpleName']} has no justification sent")
    body = {"codejustification": match[0]["codejustification"]}
    preview = {"student": k["fullname"], "absence": match[0].get("dateabsence"), "justification_to_withdraw": match[0].get("motive")}
    return _send("Withdraw justification", "faltas/borrar-justificacion", preview, confirm_code, json_body=body) or _post("faltas/borrar-justificacion", json_body=body)


# ── Authorizations and documents ──────────────────────────────────────────────

@mcp.tool()
@_guard
def answer_authorization(child: str, authorization_id: str, authorize: bool, comments: str = "", confirm_code: str = "") -> dict:
    """ACTION · Authorize (authorize=True) or NOT authorize (False) an activity or trip.

    Check authorization_detail() first (price, dates). Once answered it cannot be undone from the app.

    Args:
        child: student's first name
        authorization_id: `id` returned by authorizations()
        authorize: True = I authorize, False = I do not
        comments: optional comment for the school
        confirm_code: empty for the preview; the code to send
    """
    k = _one(child)
    match = [a for a in _get("seguimiento/get-aut-act", enrollment=k["enrollment"]) if a.get("aluatprotut") == authorization_id]
    _check(match, f"No authorization with id {authorization_id} for {k['simpleName']} (see authorizations())")
    a = match[0]
    body = {"aluatpro": a.get("aluatpro"), "aluatprotut": authorization_id, "comments": comments, "signature": authorize}
    preview = {"student": k["fullname"], "activity": a.get("motive"), "period": a.get("signaturePeriod"),
               "has_cost": a.get("hasAmount", False), "answer": "AUTHORIZE" if authorize else "DO NOT AUTHORIZE",
               "already_answered": a.get("signature", False), "comment": comments or "(empty)"}
    return _send("Answer authorization", "seguimiento/set-aut-act-tut", preview, confirm_code, json_body=body) or _post("seguimiento/set-aut-act-tut", json_body=body)


def _pending_document(document_id: int) -> dict:
    for state in ("P", "F", "R"):
        for section in _get("firma/ipasen/bandeja-firma", typeState=state, enrollment="", page=1):
            for d in section.get("documents", []):
                if d.get("id") == document_id:
                    _check(state == "P", f"Document {document_id} is no longer pending ({section.get('section')})")
                    return d
    raise _Abort(f"Document {document_id} not found in the signature inbox (documents_to_sign())")


@mcp.tool()
@_guard
def sign_document(child: str, document_id: int, signature_png: str, confirm_code: str = "") -> dict:
    """ACTION · Sign a document in the signature inbox with a handwritten signature (PNG image).

    Cannot be undone. The image is your signature (transparent or white background), like the one
    you would draw in the app.

    Args:
        child: student the document is about
        document_id: `id` of the pending document (documents_to_sign())
        signature_png: local path to the signature PNG
        confirm_code: empty for the preview; the code to send
    """
    k = _one(child)
    d = _pending_document(document_id)
    _check(d.get("isSignable", True), "This document cannot be signed from iPasen")
    name, content = _file(signature_png)
    _check(content[:8] == b"\x89PNG\r\n\x1a\n", f"{name} is not a PNG")
    body = {"enrollment": k["enrollment"], "idUser": d.get("idUser"), "imagen": base64.b64encode(content).decode()}
    preview = {"student": k["fullname"], "document": d.get("description"), "sent_by": d.get("sendBy"), "date": d.get("date"), "signature": name}
    return _send("Sign document", "firma/firma-manuscrita-documento", preview, confirm_code, json_body=body) or _post("firma/firma-manuscrita-documento", json_body=body)


@mcp.tool()
@_guard
def decline_document(document_id: int, reason: str, confirm_code: str = "") -> dict:
    """ACTION · Decline (refuse to sign) a document in the signature inbox. Cannot be undone.

    Args:
        document_id: `id` of the pending document (documents_to_sign())
        reason: reason for declining (required)
        confirm_code: empty for the preview; the code to send
    """
    d = _pending_document(document_id)
    _check(d.get("isRejectable", True), "This document cannot be declined")
    _check(reason.strip(), "Give a reason for declining")
    body = {"idDocument": str(document_id), "idUser": str(d.get("idUser")), "rejectionReason": reason}
    preview = {"document": d.get("description"), "sent_by": d.get("sendBy"), "decline_reason": reason}
    return _send("Decline document", "firma/rechazar-documento-bandeja-firma", preview, confirm_code, json_body=body) or _post("firma/rechazar-documento-bandeja-firma", json_body=body)


# ── Messages ──────────────────────────────────────────────────────────────────

@mcp.tool()
@_guard
def update_message(message_id: int, change: str, child: str = "", confirm_code: str = "") -> dict:
    """ACTION · Change the state of a received message.

    School messages arrive once per child (one copy each); by default the change applies to every
    copy, or only to the copy of the child given in `child`.
    "Administrador PASEN" notices are regenerated by the server on every query and keep no state.

    Args:
        message_id: `id` from messages()
        change: 'read', 'unread', 'favorite', 'unfavorite' or 'delete'
                ('delete' removes it from the inbox and cannot be undone)
        child: optional, apply only to that child's copy
        confirm_code: empty for the preview; the code to send
    """
    changes = {"read": {"notread": False}, "unread": {"notread": True}, "favorite": {"favorite": True},
               "unfavorite": {"favorite": False}, "delete": {"delete": True}}
    _check(change in changes, f"change must be one of: {', '.join(changes)}")
    copies = _message_copies(message_id, child)
    bodies = [{"id": m["id"], "id_message": m["id_message"], **changes[change]} for m in copies]
    preview = {"message": copies[0].get("subject"), "from": copies[0].get("sender"), "change": change,
               "copies": [_copy_label(m) for m in copies]}
    if (r := _send("Update message", "mensajeria/gestion-mensajeria", preview, confirm_code, json_body=bodies)):
        return r
    return {"status": "SENT", "copies": [_post("mensajeria/gestion-mensajeria", json_body=b)["server_response"] for b in bodies]}


@mcp.tool()
def message_tags() -> list:
    """Personal tags available to classify messages. Read-only."""
    return _get("mensajeria/etiquetas")


@mcp.tool()
@_guard
def tag_message(message_id: int, tags: list[str], confirm_code: str = "") -> dict:
    """ACTION · Set the tags of a message (replaces existing ones; empty list = remove all).

    Args:
        message_id: `id` from messages()
        tags: names of existing tags (message_tags())
        confirm_code: empty for the preview; the code to send
    """
    available = _get("mensajeria/etiquetas")
    ids = []
    for t in tags:
        match = [x for x in available if _fold(x.get("tagName", "")) == _fold(t) or str(x.get("tagId")) == t]
        _check(match, f"Tag '{t}' does not exist. Available: {', '.join(x.get('tagName', '') for x in available) or 'none'}")
        ids.append(str(match[0]["tagId"]))
    copies = _message_copies(message_id)
    bodies = [{"id": m["id"], "id_message": m["id_message"], "tags": ids} for m in copies]
    preview = {"message": copies[0].get("subject"), "tags": tags or "(remove all)", "copies": [_copy_label(m) for m in copies]}
    if (r := _send("Tag message", "mensajeria/gestion-mensajeria", preview, confirm_code, json_body=bodies)):
        return r
    return {"status": "SENT", "copies": [_post("mensajeria/gestion-mensajeria", json_body=b)["server_response"] for b in bodies]}


@mcp.tool()
def message_recipients(child: str) -> list:
    """Teachers/groups you can write to about a student. Read-only."""
    k = _one(child)
    return [{"group": g.get("name"), "people": [u.get("name") for u in g.get("users", [])]}
            for g in _get("mensajeria/get-colectivos", enrollment=k["enrollment"])]


@mcp.tool()
@_guard
def send_message(child: str, to: list[str], subject: str, text: str, attachment_paths: list[str] | None = None,
                 allow_response: bool = True, confirm_code: str = "") -> dict:
    """ACTION · Write a NEW message to the student's teachers. Cannot be undone once sent.

    Args:
        child: student the message is about
        to: recipients by name (or a unique part of the name) as listed by message_recipients(),
            e.g. ["García López"]; or the name of a whole group ("Tutores/as de unidad")
        subject: subject line
        text: message body (line breaks are kept)
        attachment_paths: optional local file paths
        allow_response: let recipients reply
        confirm_code: empty for the preview; the code to send
    """
    k = _one(child)
    _check(subject.strip() and text.strip(), "Subject and text are required")
    groups = _get("mensajeria/get-colectivos", enrollment=k["enrollment"])
    users = {u["id_user"]: u for g in groups for u in g.get("users", [])}
    chosen = {}
    for name in to:
        group = [g for g in groups if _fold(g.get("name", "")) == _fold(name)]
        match = group[0]["users"] if group else [u for u in users.values() if _fold(name) in _fold(u.get("name", ""))]
        _check(match, f"Recipient '{name}' not found (use message_recipients())")
        _check(group or len({u["id_user"] for u in match}) == 1,
               f"'{name}' is ambiguous: {', '.join(sorted({u['name'] for u in match}))}")
        chosen.update({u["id_user"]: u for u in match})
    _check(chosen, "Give at least one recipient")
    recipients = [{"data_code": u["data_code"], "id_user": str(u["id_user"])} for u in chosen.values()]
    files = [_file(p) for p in attachment_paths or []]
    parts = {"subject": subject, "message": text.replace("\n", "<br/>"), "recipient": json.dumps(recipients), "allowResponse": str(allow_response).lower()}
    preview = {"student": k["fullname"], "to": [u["name"] for u in chosen.values()], "subject": subject, "text": text,
               "attachments": [n for n, _ in files] or "(none)", "allow_response": allow_response}
    digest = hashlib.sha256(b"".join(c for _, c in files)).hexdigest()
    if (r := _send("Send new message", "mensajeria/enviar-nuevo-mensaje", preview, confirm_code, parts=parts, files_digest=digest)):
        return r
    multipart = [("subject", (None, parts["subject"], "text/plain")), ("message", (None, parts["message"], "text/plain")),
                 ("recipient", (None, parts["recipient"], "application/json")), ("allowResponse", (None, parts["allowResponse"]))]
    multipart += [("attachments", (n, c, "application/octet-stream")) for n, c in files]
    return _post("mensajeria/enviar-nuevo-mensaje", files=multipart)


@mcp.tool()
@_guard
def reply_message(message_id: int, text: str, child: str = "", attachment_paths: list[str] | None = None, confirm_code: str = "") -> dict:
    """ACTION · Reply to a received message (goes to the sender). Cannot be undone once sent.

    Args:
        message_id: `id` from messages()
        text: reply (line breaks are kept)
        child: which child it is about (required if the message arrived for several children)
        attachment_paths: optional local file paths
        confirm_code: empty for the preview; the code to send
    """
    _check(text.strip(), "The reply text is empty")
    copies = _message_copies(message_id, child)
    _check(len(copies) == 1, f"The message arrived for several children ({', '.join(_copy_label(m) for m in copies)}): set `child`")
    m = copies[0]
    files = [_file(p) for p in attachment_paths or []]
    parts = {"id": str(m["id"]), "id_message": str(m["id_message"]), "body_message": text.strip().replace("\n", "<br/>"), "allowResponse": "true"}
    preview = {"replying_to": m.get("subject"), "to": m.get("sender"), "about": _copy_label(m), "text": text.strip(), "attachments": [n for n, _ in files] or "(none)"}
    digest = hashlib.sha256(b"".join(c for _, c in files)).hexdigest()
    if (r := _send("Reply to message", "mensajeria/responder-mensaje", preview, confirm_code, parts=parts, files_digest=digest)):
        return r
    multipart = [(key, (None, value, "text/plain")) for key, value in parts.items()]
    multipart += [("attachments", (n, c, "application/octet-stream")) for n, c in files]
    return _post("mensajeria/responder-mensaje", files=multipart)


# ── Parent-teacher meetings ───────────────────────────────────────────────────

@mcp.tool()
def tutorship_slots(child: str, teacher: str, start: str = "", end: str = "") -> list:
    """Free slots to request a meeting with a teacher. Read-only.

    Args:
        child: student's first name
        teacher: teacher's name (or part of it), from tutorships()['available_teachers']
        start: from dd/mm/yyyy (default today)
        end: until dd/mm/yyyy (default +30 days)
    """
    k, t = _tutorship_teacher(child, teacher)
    first = _date(start) if start else date.today()
    last = _date(end) if end else first + timedelta(days=30)
    return [{"date": s["fdate"], "time": s["time"], "online": s.get("l_peronline"), "in_person": s.get("l_perpresencia")}
            for s in _slots(t, first, last) if s.get("isAvailable")]


def _tutorship_teacher(child: str, teacher: str) -> tuple[dict, dict]:
    k = _one(child)
    teachers = _get("horarios/ipasen/profesores-tutorias", enrollment=k["enrollment"])
    match = [t for t in teachers if _fold(teacher) in _fold(t.get("name", ""))]
    _check(len(match) == 1, f"Teacher '{teacher}' not found or ambiguous. Available: {', '.join(t['name'] for t in teachers)}")
    return k, match[0]


def _slots(t: dict, first: date, last: date) -> list:
    since = _date(t["f_tomapos"]).isoformat() if t.get("f_tomapos") else ""
    return _get("horarios/ipasen/horas-tutoria", teacherId=t["id"], startDate=first.isoformat(), endDate=last.isoformat(), datetomapos=since)


@mcp.tool()
@_guard
def request_tutorship(child: str, teacher: str, day: str, time: str, online: bool = False, message: str = "",
                      reminder: bool = True, confirm_code: str = "") -> dict:
    """ACTION · Request a parent-teacher meeting in a free slot.

    Can be undone with cancel_tutorship.

    Args:
        child: student's first name
        teacher: teacher's name (or part of it)
        day: dd/mm/yyyy (must be a slot from tutorship_slots())
        time: start time ("18:15") or slot ("18:15-18:30")
        online: True = video call, False = in person
        message: what the meeting is about, for the teacher
        reminder: receive a reminder
        confirm_code: empty for the preview; the code to send
    """
    k, t = _tutorship_teacher(child, teacher)
    d = _date(day)
    wanted = re.sub(r"\s", "", time)
    match = [s for s in _slots(t, d, d) if s["fdate"] == d.strftime("%d/%m/%Y")
             and (re.sub(r"\s", "", s["time"]) == wanted or s["time"].split("-")[0].strip() == wanted)]
    _check(match, f"No slot {time} on {d:%d/%m/%Y} with {t['name']} (see tutorship_slots())")
    slot = match[0]
    _check(slot.get("isAvailable"), "That slot is already taken")
    _check(slot.get("l_peronline") if online else slot.get("l_perpresencia"), f"That slot does not allow {'online' if online else 'in-person'} meetings")
    body = {"enrollment": k["enrollment"], "fdate": slot["fdate"], "idTutoria": slot["id"], "isOnline": online, "message": message, "recordatorio": reminder}
    preview = {"student": k["fullname"], "with": t["name"], "subject": t.get("subject"), "day": slot["fdate"], "time": slot["time"],
               "mode": "online" if online else "in person", "message": message or "(empty)"}
    return _send("Request meeting", "horarios/ipasen/nueva-tutoria", preview, confirm_code, json_body=body) or _post("horarios/ipasen/nueva-tutoria", json_body=body)


def _tutorship(child: str, tutorship_id: int) -> tuple[dict, dict]:
    k = _one(child)
    match = [t for t in _get("horarios/ipasen/tutorias", enrollment=k["enrollment"]) if t.get("id") == tutorship_id]
    _check(match, f"No meeting with id {tutorship_id} for {k['simpleName']} (see tutorships())")
    return k, match[0]


@mcp.tool()
@_guard
def cancel_tutorship(child: str, tutorship_id: int, comment: str = "", confirm_code: str = "") -> dict:
    """ACTION · Cancel a requested or scheduled meeting.

    Args:
        child: student's first name
        tutorship_id: `id` from tutorships()['meetings']
        comment: reason for cancelling
        confirm_code: empty for the preview; the code to send
    """
    k, t = _tutorship(child, tutorship_id)
    body = {"id": tutorship_id, "comment": comment}
    preview = {"student": k["fullname"], "meeting": t, "comment": comment or "(empty)"}
    return _send("Cancel meeting", "horarios/ipasen/cancelar-tutorias", preview, confirm_code, json_body=body) or _post("horarios/ipasen/cancelar-tutorias", json_body=body)


@mcp.tool()
@_guard
def answer_tutorship(child: str, tutorship_id: int, accept: bool, comment: str = "", confirm_code: str = "") -> dict:
    """ACTION · Accept or decline a meeting PROPOSED by a teacher.

    Args:
        child: student's first name
        tutorship_id: `id` from tutorships()['meetings']
        accept: True accepts, False declines
        comment: comment (recommended when declining)
        confirm_code: empty for the preview; the code to send
    """
    k, t = _tutorship(child, tutorship_id)
    body = {"id": tutorship_id, "isAccepted": accept, "comment": "" if accept else comment}
    preview = {"student": k["fullname"], "meeting": t, "answer": "ACCEPT" if accept else "DECLINE", "comment": body["comment"] or "(empty)"}
    return _send("Answer meeting", "horarios/ipasen/gestion-tutorias", preview, confirm_code, json_body=body) or _post("horarios/ipasen/gestion-tutorias", json_body=body)


# ── Follow-up ─────────────────────────────────────────────────────────────────

@mcp.tool()
@_guard
def mark_observation(child: str, observation_id: str, read: bool = True, confirm_code: str = "") -> dict:
    """ACTION · Mark a teacher's note as read (or unread). Can be undone.

    Args:
        child: student's first name
        observation_id: `id` from observations()
        read: True = read
        confirm_code: empty for the preview; the code to send
    """
    k = _one(child)
    match = [o for o in _get("seguimiento/observaciones", enrollment=k["enrollment"]) if str(o.get("id")) == str(observation_id)]
    _check(match, f"No note with id {observation_id} for {k['simpleName']}")
    body = {"id": str(observation_id), "isRead": read}
    preview = {"student": k["fullname"], "note": f'{match[0].get("date")} {match[0].get("teacher")}: {match[0].get("subject")}', "mark_as": "read" if read else "unread"}
    return _send("Mark note", "seguimiento/gestion-observaciones", preview, confirm_code, json_body=body) or _post("seguimiento/gestion-observaciones", json_body=body)


@mcp.tool()
@_guard
def manage_pickup_person(child: str, operation: str, document_id: str, name: str = "", lastname: str = "",
                         second_lastname: str = "", phone: str = "", confirm_code: str = "") -> dict:
    """ACTION · Add, update or remove a person authorized to pick the student up.

    Args:
        child: student's first name
        operation: 'add', 'update' or 'delete'
        document_id: the person's DNI/NIE/passport number (identifies who is updated or removed)
        name, lastname, second_lastname, phone: details (required for 'add'; empty ones are kept on 'update')
        confirm_code: empty for the preview; the code to send
    """
    _check(operation in ("add", "update", "delete"), "operation must be 'add', 'update' or 'delete'")
    k = _one(child)
    people = _get("seguimiento/get-per-aut-rec-alu", enrollment=k["enrollment"])
    current = next((p for p in people if _fold(p.get("id", "")) == _fold(document_id.strip())), None)
    if operation == "add":
        _check(current is None, f"{document_id} is already authorized; use operation='update'")
        _check(name and lastname and phone, "Adding requires name, lastname and phone")
        current = {}
    else:
        _check(current, f"{document_id} is not in {k['simpleName']}'s list of authorized people")
    ident = document_id.strip().upper()
    is_dni_nie = bool(re.fullmatch(r"[0-9XYZ]\d{7}[A-Z]", ident))
    person = {"token": current.get("token"), "name": name or current.get("name"), "lastname": lastname or current.get("lastname"),
              "secondlastname": second_lastname or current.get("secondlastname"), "typeid": "C" if is_dni_nie else "P",
              "id": ident, "tlf": phone or current.get("tlf"), "enrollment": int(k["enrollment"]), "delete": operation == "delete"}
    preview = {"student": k["fullname"], "operation": {"add": "ADD", "update": "UPDATE", "delete": "REMOVE"}[operation],
               "person": f'{person["name"]} {person["lastname"]} {person["secondlastname"] or ""}'.strip(),
               "document": f'{ident} ({"DNI/NIE" if is_dni_nie else "passport"})', "phone": person["tlf"]}
    return _send("Authorized pickup person", "seguimiento/gestion-per-aut-rec-alu", preview, confirm_code, json_body=person) or _post("seguimiento/gestion-per-aut-rec-alu", json_body=person)


# Actions that another action can undo; every other action is flagged destructive.
_REVERSIBLE = {"report_absence", "justify_absence", "update_message", "tag_message", "request_tutorship",
               "mark_observation", "manage_pickup_person"}
for _tool in mcp._tool_manager.list_tools():
    if _tool.description.startswith("ACTION"):
        _tool.annotations = ToolAnnotations(readOnlyHint=False, destructiveHint=_tool.name not in _REVERSIBLE,
                                            idempotentHint=False, openWorldHint=True)
    elif _tool.name in ("login", "logout"):
        _tool.annotations = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
    else:
        _tool.annotations = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


if __name__ == "__main__":
    mcp.run()
