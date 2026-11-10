import asyncio
import json
import re
from datetime import date, timedelta

import pytest
from mcp.server.fastmcp.exceptions import ToolError

import server

HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
REAL_TELEGRAM = server._telegram  # captured before the `telegram` fixture patches it
TODAY = date.today()
TOMORROW = (TODAY + timedelta(days=1)).strftime("%d/%m/%Y")


def call(name: str, args: dict | None = None):
    """Call a tool through the MCP layer and return its decoded JSON result."""
    content = asyncio.run(server.mcp.call_tool(name, args or {}))
    blocks = content[0] if isinstance(content, tuple) else content
    texts = [b.text for b in blocks]
    values = [json.loads(t) for t in texts]
    return values[0] if len(values) == 1 else values


def tools():
    return asyncio.run(server.mcp.list_tools())


# ── Metadata ──────────────────────────────────────────────────────────────────

def test_every_tool_declares_all_four_annotations():
    for t in tools():
        assert t.annotations is not None, t.name
        for hint in HINTS:
            assert isinstance(getattr(t.annotations, hint), bool), f"{t.name}.{hint}"


def test_annotations_match_behaviour():
    by_name = {t.name: t.annotations for t in tools()}
    actions = {n for n, t in ((t.name, t) for t in tools()) if t.description.startswith("ACTION")}
    assert len(actions) == 16
    for name in actions:
        assert by_name[name].readOnlyHint is False, name
    for name in ("delete_absence_report", "sign_document", "decline_document", "answer_authorization", "update_message"):
        assert by_name[name].destructiveHint is True, name
    for name in ("report_absence", "send_message", "reply_message", "request_tutorship"):
        assert by_name[name].destructiveHint is False, name
    for name in ("logout", "mark_digested"):
        assert by_name[name].openWorldHint is False, name
    queries = set(by_name) - actions - {"login", "logout", "mark_digested"}
    for name in queries:
        assert by_name[name] == server.READ_ONLY, name


# ── Every declared tool ───────────────────────────────────────────────────────

def _signature_png(tmp_path):
    f = tmp_path / "signature.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    return str(f)


def tool_args(tmp_path) -> dict:
    return {
        "login": {}, "logout": {}, "children": {}, "my_data": {}, "upcoming_events": {}, "overview": {},
        "student_profile": {}, "schedule": {"day": "monday"}, "tutor": {}, "absences": {}, "absence_notices": {},
        "subjects": {}, "term_grades": {}, "grades": {}, "observations": {}, "behaviour": {}, "authorizations": {},
        "authorization_detail": {"authorization_id": "AUT1"}, "pickup_people": {}, "school_services": {},
        "school_history": {}, "textbooks": {}, "tutorships": {}, "access_keys": {}, "scholarships": {},
        "documents_to_sign": {}, "school_info": {}, "school_calendar": {}, "notice_board": {}, "messages": {},
        "read_message": {"message_id": 100}, "sent_messages": {}, "notifications": {}, "news": {},
        "raw_get": {"path": "faltas/motivos"}, "school_digest": {},
        "mark_digested": {"decisions": [{"id": 100, "outcome": "event", "why": "test"}]},
        "absence_slots": {"child": "Ana", "day": TOMORROW},
        "report_absence": {"child": "Ana", "start": TOMORROW},
        "delete_absence_report": {"child": "Ana", "report_id": 555},
        "justify_absence": {"child": "Ana", "absence_code": "A1", "reason": "Enfermedad"},
        "delete_justification": {"child": "Ana", "absence_code": "A2"},
        "answer_authorization": {"child": "Ana", "authorization_id": "AUT1", "authorize": True},
        "sign_document": {"child": "Ana", "document_id": 42, "signature_png": _signature_png(tmp_path)},
        "decline_document": {"document_id": 42, "reason": "No"},
        "update_message": {"message_id": 100, "change": "read"},
        "message_tags": {},
        "tag_message": {"message_id": 100, "tags": ["Importante"]},
        "message_recipients": {"child": "Ana"},
        "send_message": {"child": "Ana", "to": ["García López"], "subject": "Hola", "text": "Texto"},
        "reply_message": {"message_id": 101, "text": "Gracias"},
        "tutorship_slots": {"child": "Ana", "teacher": "García"},
        "request_tutorship": {"child": "Ana", "teacher": "García", "day": TOMORROW, "time": "18:00"},
        "cancel_tutorship": {"child": "Ana", "tutorship_id": 77},
        "answer_tutorship": {"child": "Ana", "tutorship_id": 77, "accept": True},
        "mark_observation": {"child": "Ana", "observation_id": "O1"},
        "manage_pickup_person": {"child": "Ana", "operation": "update", "document_id": "12345678Z", "phone": "611111111"},
    }


def test_args_cover_every_declared_tool(tmp_path):
    assert set(tool_args(tmp_path)) == {t.name for t in tools()}


@pytest.mark.parametrize("name", sorted(t.name for t in asyncio.run(server.mcp.list_tools())))
def test_tool_runs(api, tmp_path, name):
    result = call(name, tool_args(tmp_path)[name])
    assert result is not None
    action = next(t for t in tools() if t.name == name).description.startswith("ACTION")
    if action:
        # Without confirm_code an action only previews: nothing may be POSTed.
        assert result["status"] == "PREVIEW — nothing has been sent", result
        assert result["confirm_code"]
        assert [c for c in api.calls if c[0] == "POST" and c[1] != "security/oauth/token"] == []


# ── Session ───────────────────────────────────────────────────────────────────

def test_login_and_token_rotation(api):
    assert call("login") == {"ok": True}
    call("children")
    assert json.loads(server.STATE_FILE.read_text())["access_token"] == "token-1"


def test_login_rejected(api):
    api.login_status = 401
    with pytest.raises(ToolError, match="Login rejected"):
        call("login")


# ── Queries ───────────────────────────────────────────────────────────────────

def test_children_and_child_resolution(api):
    assert [c["child"] for c in call("children")] == ["Ana", "Pablo"]
    assert list(call("absences", {"child": "ana"})) == ["Ana"]
    with pytest.raises(ToolError, match="not found"):
        call("absences", {"child": "Nobody"})


def test_messages_merge_copies_per_child(api):
    msgs = call("messages")
    msgs = msgs if isinstance(msgs, list) else [msgs]
    excursion = next(m for m in msgs if m["id"] == 100)
    assert excursion["students"] == ["Ana", "Pablo"] and excursion["unread"] is True


def test_read_message_picks_requested_message(api):
    m = call("read_message", {"message_id": 100})
    assert m["subject"] == "Excursión" and m["students"] == ["Ana", "Pablo"]
    assert m["attachments"] == [{"name": "a.pdf", "url": "https://example.org/a.pdf"}]


def test_grades_include_criteria(api):
    grades = call("grades", {"child": "Ana"})["Ana"]
    assert grades == [g for g in grades if g["activity"] == "Speaking"]  # only graded by default
    assert grades[0]["criteria_grades"][0]["grade"] == 10


# ── Digest ────────────────────────────────────────────────────────────────────

def test_digest_returns_each_message_once(api):
    first = call("school_digest")
    assert {m["id"] for m in first["messages"]} == {100, 101}
    call("mark_digested", {"decisions": [{"id": 100, "outcome": "event+parents", "why": "trip"},
                                         {"id": 101, "outcome": "task", "why": "revise"}]})
    assert call("school_digest")["messages"] == []
    log = json.loads(server.STATE_FILE.read_text())["digest_log"]
    assert [d["outcome"] for d in log] == ["event+parents", "task"]


def test_mark_digested_requires_a_decision_per_message(api):
    with pytest.raises(ToolError, match="outcome"):
        call("mark_digested", {"decisions": [{"id": 100}]})


# ── Action gate: inline mode ──────────────────────────────────────────────────

def test_inline_preview_then_confirm_sends(api):
    args = {"child": "Ana", "start": TOMORROW, "times": ["11:00"], "observations": "Dentista"}
    preview = call("report_absence", args)
    assert preview["preview"]["slots"] == ["11:00 - 11:30 Inglés"]  # two teachers, one slot code
    sent = call("report_absence", {**args, "confirm_code": preview["confirm_code"]})
    assert sent["status"] == "SENT"
    assert api.posts("faltas/nueva-falta") == [{"enrollment": "1001", "initdate": TOMORROW, "enddate": TOMORROW,
                                                "observations": "Dentista", "sectionscode": [12]}]


def test_inline_code_is_bound_to_the_request(api):
    preview = call("report_absence", {"child": "Ana", "start": TOMORROW})
    rejected = call("report_absence", {"child": "Ana", "start": TOMORROW, "observations": "changed",
                                       "confirm_code": preview["confirm_code"]})
    assert rejected["status"] == "REJECTED — nothing has been sent"
    assert api.posts("faltas/nueva-falta") == []


@pytest.mark.parametrize("args, reason", [
    ({"child": "", "start": TOMORROW}, "exactly one student"),
    ({"child": "Ana", "start": "2026-11-03"}, "Invalid date"),
    ({"child": "Ana", "start": TOMORROW, "times": ["15:00"]}, "does not exist"),
    ({"child": "Ana", "start": TOMORROW, "end": (TODAY + timedelta(days=3)).strftime("%d/%m/%Y"), "times": ["9:00"]}, "single day"),
])
def test_report_absence_validation(api, args, reason):
    result = call("report_absence", args)
    assert result["status"] == "REJECTED — nothing has been sent" and reason in result["reason"]


def test_update_message_targets_each_child_copy(api):
    preview = call("update_message", {"message_id": 100, "change": "read", "child": "Ana"})
    assert preview["preview"]["copies"] == ["Ana"]
    call("update_message", {"message_id": 100, "change": "read", "child": "Ana", "confirm_code": preview["confirm_code"]})
    assert api.posts("mensajeria/gestion-mensajeria") == [{"id": 100, "id_message": 1100, "notread": False}]


def test_reply_requires_child_when_message_has_several_copies(api):
    result = call("reply_message", {"message_id": 100, "text": "ok"})
    assert result["status"].startswith("REJECTED") and "several children" in result["reason"]


def test_send_message_rejects_ambiguous_recipient(api):
    result = call("send_message", {"child": "Ana", "to": ["Molina"], "subject": "s", "text": "t"})
    assert result["status"].startswith("REJECTED") and "ambiguous" in result["reason"]


def test_request_tutorship_checks_mode(api):
    result = call("request_tutorship", {"child": "Ana", "teacher": "García", "day": TOMORROW, "time": "18:15", "online": True})
    assert result["status"].startswith("REJECTED") and "online" in result["reason"]


# ── Action gate: telegram mode ────────────────────────────────────────────────

def _code(outbox: list[str]) -> str:
    return re.search(r"Code: (\d{6})", outbox[-1]).group(1)


def test_telegram_code_never_reaches_the_agent(api, telegram):
    args = {"child": "Ana", "start": TOMORROW}
    preview = call("report_absence", args)
    code = _code(telegram)
    assert code not in json.dumps(preview) and "TELEGRAM" in preview["confirm_code"]
    assert call("report_absence", {**args, "confirm_code": code})["status"] == "SENT"
    # single use
    assert call("report_absence", {**args, "confirm_code": code})["status"].startswith("REJECTED")
    assert len(api.posts("faltas/nueva-falta")) == 1


def test_telegram_code_voided_after_three_wrong_attempts(api, telegram):
    args = {"child": "Ana", "start": TOMORROW}
    call("report_absence", args)
    code = _code(telegram)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(3):
        assert call("report_absence", {**args, "confirm_code": wrong})["status"].startswith("REJECTED")
    assert call("report_absence", {**args, "confirm_code": code})["status"].startswith("REJECTED")
    assert api.posts("faltas/nueva-falta") == []


def test_telegram_mode_without_bot_refuses_to_preview(api, telegram, monkeypatch):
    monkeypatch.delenv("IPASEN_TELEGRAM_BOT_TOKEN")
    monkeypatch.setattr(server, "_telegram", REAL_TELEGRAM)  # real sender: must refuse before any network call
    result = call("report_absence", {"child": "Ana", "start": TOMORROW})
    assert result["status"].startswith("REJECTED") and "Telegram" in result["reason"]
