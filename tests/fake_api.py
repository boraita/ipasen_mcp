"""In-memory fake of the iPasen REST API used by the tests. No network, invented data only.

Every request is recorded in `FakeApi.calls` so tests can assert what would have been sent.
"""

import json
from datetime import date, datetime, timedelta

import httpx

ANA, PABLO = "1001", "1002"


def _fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")


class FakeApi:
    def __init__(self):
        self.calls: list[tuple[str, str, dict, bytes]] = []
        self.login_status = 200
        self.today = date.today()

    # ── helpers ──────────────────────────────────────────────────────────────
    def posts(self, path: str) -> list[dict]:
        out = []
        for method, p, _, body in self.calls:
            if method == "POST" and p == path:
                try:
                    out.append(json.loads(body))
                except ValueError:
                    out.append({"_raw": body})
        return out

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # ── routing ──────────────────────────────────────────────────────────────
    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/api/public/", 1)[-1]
        params = dict(request.url.params)
        body = request.content
        self.calls.append((request.method, path, params, body))

        if path == "security/oauth/token":
            if self.login_status != 200:
                return httpx.Response(self.login_status, json={"success": False, "data": None, "errors": [
                    {"status": self.login_status, "detail": "Las credenciales proporcionadas no son correctas."}]})
            return httpx.Response(200, json={"access_token": "token-1", "token_type": "bearer"})

        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "data": {}}, headers={"token-renew": "token-2"})

        handler = getattr(self, "get_" + path.replace("/", "_").replace("-", "_"), None)
        if handler is None:
            return httpx.Response(404, json={"success": False, "errors": [{"status": 404, "detail": f"unknown {path}"}]})
        return httpx.Response(200, json={"success": True, "data": handler(params)})

    # ── GET endpoints ────────────────────────────────────────────────────────
    def get_generales_info_session(self, p):
        return {"@class": "x", "simpleName": "Tutor", "sons": [
            {"fullname": "Ana Prueba", "simpleName": "Ana", "enrollment": ANA, "course": "3º Primaria", "unity": "3ºA",
             "tutor": "García López, Laura", "description": "CEIP Test", "centerCode": "C1", "age": 8},
            {"fullname": "Pablo Prueba", "simpleName": "Pablo", "enrollment": PABLO, "course": "5 años", "unity": "5B",
             "tutor": "Ruiz, Marta", "description": "CEIP Test", "centerCode": "C1", "age": 5},
        ]}

    def get_generales_get_datos_usuario(self, p):
        return {"name": "Tutor Prueba"}

    def get_generales_get_agenda(self, p):
        return [{"initdate": _fmt(self.today + timedelta(days=3)), "endDate": _fmt(self.today + timedelta(days=3)),
                 "title": "Festivo", "category": "Festivo"}]

    def get_generales_home(self, p):
        return [{"session": [{"sons": [
            {"simpleName": "Ana", "enrollment": [{"enrollment": ANA, "denomination": "CEIP Test", "center": "C1",
                                                  "phone_centro": "900000000", "web": "https://example.org", "course": "3º", "unit": "3ºA", "tutor": "García"}]},
            {"simpleName": "Pablo", "enrollment": [{"enrollment": PABLO, "denomination": "CEIP Test", "center": "C1"}]},
        ]}]}]

    def get_faltas_get_faltas(self, p):
        if p.get("enrollment") != ANA:
            return []
        return [{"section": "Noviembre", "items": [
            {"codeabsence": "A1", "dateabsence": _fmt(self.today - timedelta(days=1)), "canjustified": True,
             "section": "9:00 - 9:30", "subject": "Lengua"},
            {"codeabsence": "A2", "dateabsence": _fmt(self.today - timedelta(days=2)), "codejustification": "J2", "motive": "Enfermedad"},
        ]}]

    def get_faltas_get_notificaciones(self, p):
        return [{"codenotification": 555, "send_date": _fmt(self.today), "start_date": _fmt(self.today),
                 "end_date": _fmt(self.today), "observation": "Dentista", "sections": [{"description": "11:00 - 11:30", "prof": "X"}]}]

    def get_faltas_motivos(self, p):
        return [{"code": 1, "description": "Enfermedad"}, {"code": 2, "description": "Visita al médico"}]

    def get_horarios_get_horario(self, p):
        return [
            {"day": "Lunes", "section": 1, "hoursection": "9:00 - 9:30", "subject": "Lengua", "prof": "García", "codesection": 11},
            {"day": "Lunes", "section": 2, "hoursection": "11:00 - 11:30", "subject": "Inglés", "prof": "Molina", "codesection": 12},
            {"day": "Lunes", "section": 2, "hoursection": "11:00 - 11:30", "subject": "Inglés", "prof": "Linares", "codesection": 12},
            {"day": "Martes", "section": 1, "hoursection": "9:00 - 9:30", "subject": "Mates", "prof": "García", "codesection": 21},
        ]

    def get_horarios_tutoria(self, p):
        return {"tutorName": "García López, Laura", "unity": "3ºA",
                "activitys": [{"day": "lunes", "time_started": "18:00", "time_end": "19:00", "activity": "Tutoría"}],
                "teachingTeam": [{"description": "Inglés", "teachers": "Molina"}]}

    def get_horarios_ipasen_tutorias(self, p):
        return [{"id": 77, "date": _fmt(self.today + timedelta(days=5)), "teacher": "García López, Laura"}]

    def get_horarios_ipasen_profesores_tutorias(self, p):
        return [{"id": 5, "name": "Laura García López", "subject": "Tutoría", "f_tomapos": "01/09/2026"}]

    def get_horarios_ipasen_horas_tutoria(self, p):
        start = datetime.strptime(p["startDate"], "%Y-%m-%d").date()
        return [{"id": 900, "fdate": _fmt(start), "time": "18:00-18:15", "l_peronline": True, "l_perpresencia": True, "isAvailable": True},
                {"id": 901, "fdate": _fmt(start), "time": "18:15-18:30", "l_peronline": False, "l_perpresencia": True, "isAvailable": True}]

    def get_horarios_libros_texto(self, p):
        return []

    def get_seguimiento_datos_alumno(self, p):
        return [{"section": "Datos", "items": [["Nombre", "Prueba"]]}]

    def get_seguimiento_get_materias_matricula(self, p):
        return [{"subject": "Inglés", "codesubject": 7, "subjectgroup": "Obligatorias", "unit": "3º", "state": "Pendiente", "approved": False}]

    def get_seguimiento_get_notas_convocatorias(self, p):
        return [{"course": "1ª Evaluación", "dateStart": "01/10/2026", "dateFinish": "22/12/2026"}]

    def get_seguimiento_get_actividades_evaluables_criterios(self, p):
        return [{"description": "1ª Evaluación", "subjects": [{"name": "Inglés", "activities": [
            {"description_activity": "Speaking", "start_date": "01/10/2026", "professor": "Molina",
             "descriptionCatacteva": "SPEAKING", "diary_score": [{"delivery_date": "15/10/2026",
                                                                   "note_array": [{"note": 10, "description": "ING.1"}]}]},
            {"description_activity": "Exam", "start_date": "23/10/2026", "professor": "Molina", "diary_score": [{}]},
        ]}]}]

    def get_seguimiento_observaciones(self, p):
        return [{"id": "O1", "date": _fmt(self.today), "teacher": "García", "subject": "Lengua", "isRead": False}]

    def get_seguimiento_get_conductas_contrarias(self, p):
        return []

    def get_seguimiento_get_aut_act(self, p):
        return [{"aluatprotut": "AUT1", "aluatpro": "PRO1", "motive": "Excursión", "signature": False,
                 "signaturePeriod": "01/11/2026 - 30/11/2026", "hasAmount": True}]

    def get_seguimiento_get_det_aut_act(self, p):
        return {"motive": "Excursión", "amount": 5}

    def get_seguimiento_get_per_aut_rec_alu(self, p):
        return [{"token": "T1", "name": "Abuela", "lastname": "Pérez", "secondlastname": "López", "typeid": "D",
                 "id": "12345678Z", "tlf": "600000000"}]

    def get_seguimiento_trayectoria_escolar(self, p):
        return []

    def get_centros_servicios_centro(self, p):
        return [{"serviceType": "Comedor", "monthPrice": 50}]

    def get_centros_get_becas(self, p):
        return {}

    def get_centros_calendario_centro(self, p):
        return [{"section": "noviembre 2026", "items": [{"dateholiday": "02/11/2026", "description": "Festivo"}]}]

    def get_centros_get_tablon_anuncios(self, p):
        return []

    def get_usuarios_mis_claves(self, p):
        return {"services": []}

    def get_firma_ipasen_bandeja_firma(self, p):
        if p.get("typeState") == "P":
            return [{"section": "Pendientes", "documents": [
                {"id": 42, "idUser": 7, "description": "Autorización fotos", "sendBy": "Dirección", "date": _fmt(self.today),
                 "isSignable": True, "isRejectable": True}]}]
        return [{"section": "Otros", "documents": []}]

    def get_mensajeria_get_comunicaciones(self, p):
        if int(p.get("page", 1)) > 1:
            return []
        now = self.today.strftime("%d/%m/%Y") + " 10:00"
        return [
            {"id": 100, "id_message": 1100, "enrollment": int(ANA), "isread": False, "sender": "García López, Laura",
             "subject": "Excursión", "body_message": "<p>El martes vamos de excursión.</p>", "date": now, "has_adj": False},
            {"id": 100, "id_message": 1200, "enrollment": int(PABLO), "isread": True, "sender": "García López, Laura",
             "subject": "Excursión", "body_message": "<p>El martes vamos de excursión.</p>", "date": now, "has_adj": False},
            {"id": 101, "id_message": 1101, "enrollment": int(ANA), "isread": True, "sender": "Molina",
             "subject": "Inglés", "body_message": "Repasad la unidad 1.", "date": now, "has_adj": True},
        ]

    def get_mensajeria_get_conversaciones(self, p):
        # Like the real API: the whole conversation with the sender, oldest first.
        if int(p.get("page", 1)) > 1:
            return {"pages": p.get("page"), "messages": []}
        return {"pages": 1, "messages": [
            {"id": 99, "subject": "Mensaje antiguo", "body_message": "viejo", "enrollment": int(ANA)},
            {"id": 100, "subject": "Excursión", "body_message": "<p>El martes vamos de excursión.</p>", "enrollment": int(ANA),
             "sender": "García López, Laura", "attachments": [{"attachmentName": "a.pdf", "url": "https://example.org/a.pdf"}]},
            {"id": 100, "subject": "Excursión", "body_message": "<p>El martes vamos de excursión.</p>", "enrollment": int(PABLO)},
        ]}

    def get_mensajeria_get_mensajes_salida(self, p):
        return []

    def get_mensajeria_get_bandeja_notificaciones(self, p):
        return [{"notRead": 0}]

    def get_mensajeria_avisos(self, p):
        return []

    def get_mensajeria_etiquetas(self, p):
        return [{"tagId": 3, "tagName": "Importante"}]

    def get_mensajeria_get_colectivos(self, p):
        return [{"name": "Tutores/as de unidad", "users": [{"id_user": 5, "name": "García López, Laura (3ºA)", "data_code": "DC5"}]},
                {"name": "Equipo docente", "users": [{"id_user": 6, "name": "Molina, Lorena", "data_code": "DC6"},
                                                      {"id_user": 8, "name": "Molina, Pedro", "data_code": "DC8"}]}]
