"""A stand-in for dietopro.com used by the end-to-end tests.

It answers the same endpoints the integration uses, with data shaped like a real
account (snake_case keys, a plan for the CURRENT week, a plato with a superPlato
and a lone food as alimento_cantidad) and records every request so the tests can
check what the integration actually sent.
"""
from __future__ import annotations

import asyncio
import threading
from datetime import date, datetime, timedelta, timezone

from aiohttp import web

TOKEN = "fake-jwt"
REFRESH = "fake-refresh"


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _plato(pid: int, name: str, kcal: float, ing: list[tuple[str, float]], rating: float = 0):
    return {
        "id": pid,
        "energia": kcal,
        "proteinas_totales": 20,
        "grasas_totales": 10,
        "glucidos_totales": 40,
        "alergenos": [],
        "alimento_cantidades": [
            {
                "alimento": {"nombre": n, "grupo_alimento": {"nombre": "Grupo"}, "super_grupo_alimento": {"nombre": "Categoría"}},
                "cantidad": q,
                "medida_casera": "1 ración",
            }
            for n, q in ing
        ],
        "super_plato": {"id": pid * 10, "nombre": name, "receta": "Cocinar y servir.", "rating": rating, "image_path": None},
    }


def _ingesta(hora: str, subs: list[dict]) -> dict:
    return {"hora": hora, "subingestas": subs}


def _loose_food(sub_id: str) -> dict:
    return {
        "id": sub_id,
        "changed_by_paciente": False,
        "alimento_cantidad": {
            "cantidad": 130,
            "medida_casera": "1 filete",
            "alimento": {
                "id": 4321,
                "nombre": "Pollo, pechuga, solomillo",
                "energia": 120,
                "proteinas_totales": 23,
                "grupo_alimento": {"nombre": "Carnes", "super_grupo_alimento": {"nombre": "Proteicos"}},
            },
        },
    }


def _dieta(i: int) -> dict:
    return {
        "id": 9000 + i,
        "nombre_horario": "Turno partido",
        "desayuno": _ingesta("08:00", [{"id": f"{i}1", "plato": _plato(100 + i, f"Tostada integral {i}", 300, [("Pan integral", 60)], rating=4)}]),
        "tentempie1": _ingesta("11:15", [_loose_food(f"{i}2")]),
        "comida": _ingesta("14:30", [{"id": f"{i}3", "plato": _plato(200 + i, f"Lasaña de carne {i}", 650, [("Pasta", 80), ("Carne picada", 100)])}]),
        "merienda1": _ingesta("17:30", [{"id": f"{i}4", "plato": _plato(300 + i, "Yogur con nueces", 190, [("Yogur", 125), ("Nueces", 15)])}]),
        "cena": _ingesta("21:00", [{"id": f"{i}5", "plato": _plato(400 + i, "Merluza al horno", 340, [("Merluza", 200)])}]),
    }


class FakeDietoPro:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.open_measurement = True
        self.fail_with: int | None = None  # make every /api/paciente call return this status
        self.valid_password = "secret"
        self.reject_auth = False  # every login/token is refused (expired password)

    # ------------------------------------------------------------------ data
    def plans(self) -> list[dict]:
        today = date.today()
        this = _monday(today)
        out = []
        for pid, monday, sent in (
            (101, this - timedelta(days=7), this - timedelta(days=9)),
            (102, this, this - timedelta(days=2)),
            (103, this + timedelta(days=7), this - timedelta(days=0)),
        ):
            out.append(
                {
                    "id": pid,
                    "created": f"{(monday - timedelta(days=6)).isoformat()}T10:00:00+00:00",
                    "title": f"Plan dietético semanal de Test Paciente {monday.strftime('%d/%m/%Y')}",
                    "sent_by_email_at": f"{sent.isoformat()}T09:00:00+00:00",
                }
            )
        return out

    def plan_detail(self, pid: int) -> dict:
        plan = next((p for p in self.plans() if p["id"] == pid), self.plans()[1])
        return {**plan, "paciente": {"id": 1}, "updated": plan["created"], "updated_by_paciente": False, "dietas": [_dieta(i) for i in range(7)]}

    def cita(self) -> dict:
        when = datetime.now(timezone.utc).replace(hour=18, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return {
            "cita": {"id": 555, "duracion": 15, "fecha": when.isoformat(), "observaciones": "", "confirmed_by_paciente": False,
                     "asked_by_paciente": False, "confirmed_by_dietista": True},
            "citas_confirmed_by_paciente": False,
            "cita_cancelation_deadline": "disabled",
            "cita_booking_enabled": False,
            "cita_booking_max_advance": 14,
            "cita_booking_duration": 15,
            "pending_dietista_confirmation": False,
        }

    def seguimientos(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        return [
            {"id": 1, "created_timestamp": str(int((now - timedelta(days=21)).timestamp())), "peso": 93.0, "imc": 32.0, "peso_grasa": 29.0, "porcentaje_grasa": 31.2, "porcentaje_agua": 50.0, "perimetro_cintura": 106},
            {"id": 2, "created_timestamp": str(int((now - timedelta(days=14)).timestamp())), "peso": 92.1, "imc": 31.7, "peso_grasa": 28.6, "porcentaje_grasa": 31.0, "porcentaje_agua": 50.2, "perimetro_cintura": 105},
            {"id": 3, "created_timestamp": str(int((now - timedelta(days=3)).timestamp())), "peso": 91.2, "imc": 31.4, "peso_grasa": 28.1, "porcentaje_grasa": 30.8, "porcentaje_agua": 50.4, "perimetro_cintura": 104},
        ]

    # ------------------------------------------------------------------ app
    def app(self) -> web.Application:
        app = web.Application(middlewares=[self._record])
        r = app.router
        r.add_post("/api/login_check", self.login)
        r.add_post("/api/paciente/token/refresh", self.refresh)
        r.add_get("/api/paciente/plans", self.json(self.plans))
        r.add_get("/api/paciente/plan", self.plan)
        r.add_get("/api/paciente/cita", self.json(self.cita))
        r.add_get("/api/paciente/seguimientos", self.json(self.seguimientos))
        r.add_get("/api/paciente/dietista", self.json(lambda: {"first_name": "Lorena", "family_name": "G", "email": "d@example.com", "mobile_phone": "+34600000000", "nombre_ingestas": [{"ingesta_type": "comida", "ingesta_name": "Almuerzo"}]}))
        r.add_get("/api/paciente/current-user", self.json(lambda: {"fullname": "Test Paciente", "email": "t@example.com", "phone": "600"}))
        r.add_get("/api/paciente/eni", self.json(lambda: {"id": 1, "last_viewed_step": "eniFinish", "valor_energetico": 1850, "peso_sugerido": 80.0, "valor_energetico_por_dia": [1850] * 7,
                                                           "porcentaje_macronutrientes_por_dia": [{"glucidos": 45, "proteinas": 25, "grasas": 30}] * 7}))
        r.add_get("/api/paciente/rating", self.json(lambda: {"ratings": []}))
        r.add_get("/api/paciente/charla", self.json(lambda: [{"id": 1, "charla": {"id": 2, "titulo": "Hidratación", "link1": "https://example.com", "nombre_link1": "Ver", "observaciones": ""}, "tema": {"id": 3, "titulo": "Hábitos"}, "fecha_imparticion": "2026-10-01T10:00:00+00:00", "visto_en_app": False}]))
        r.add_get("/api/paciente/chat/unread", self.json(lambda: {"count": 2}))
        r.add_get("/api/paciente/chat/messages", self.json(lambda: {"messages": [{"id": 1, "text": "Hola, ¿qué tal la semana?", "created": datetime.now(timezone.utc).isoformat()}], "has_more": False}))
        r.add_post("/api/paciente/chat/messages", self.ok)
        r.add_put("/api/paciente/chat/reset-unread", self.ok)
        r.add_post("/api/paciente/chat/images", self.ok)
        r.add_post("/api/paciente/chat/documents", self.ok)
        r.add_get("/api/paciente/citas/available-slots", self.json(lambda: {"slots": {(date.today() + timedelta(days=2)).isoformat(): ["10:00", "10:30"]}}))
        r.add_post("/api/paciente/citas/booking-request", self.ok)
        r.add_post("/api/paciente/citas/{id}/cancel", self.ok)
        r.add_post("/api/paciente/citas/{id}/confirm", self.ok)
        r.add_get("/api/paciente/platos", self.json(lambda: [{"id": 7001, "super_plato": {"nombre": "Alternativa A"}}, {"id": 7002, "super_plato": {"nombre": "Alternativa B"}}]))
        r.add_patch("/api/paciente/plans/{id}", self.ok)
        r.add_get("/api/paciente/alimentos", self.json(lambda: [{"id": 5001, "nombre": "Pavo, pechuga", "grupo_alimento": {"nombre": "Carnes", "super_grupo_alimento": {"nombre": "Proteicos"}}, "medidas_caseras": [{"cantidad_maxima": 140, "cantidad_minima": 120, "medida": "1 filete"}]}]))
        r.add_patch("/api/paciente/changes/{id}/alimento", self.ok)
        r.add_get("/api/paciente/seguimiento", self.open_seguimiento)
        r.add_put("/api/paciente/seguimientos/{id}/step", self.ok)
        r.add_patch("/api/paciente/finish/seguimiento", self.ok)
        r.add_put("/api/paciente/superplato/{id}/rating", self.ok)
        return app

    @web.middleware
    async def _record(self, request: web.Request, handler):
        body = None
        if request.method in ("POST", "PUT", "PATCH") and request.content_type == "application/json":
            body = await request.json()
        elif request.method in ("POST", "PUT", "PATCH") and request.content_type.startswith("multipart/"):
            reader = await request.multipart()
            body = {}
            while (part := await reader.next()) is not None:
                body[part.name] = part.filename or (await part.text())
        self.requests.append({"method": request.method, "path": request.path, "query": dict(request.query), "body": body})
        if self.reject_auth and (request.path.startswith("/api/paciente") or request.path == "/api/login_check"):
            return web.Response(status=401)
        if self.fail_with and request.path.startswith("/api/paciente"):
            return web.Response(status=self.fail_with, text="down")
        if request.path.startswith("/api/paciente") and request.path != "/api/paciente/token/refresh":
            if request.headers.get("Authorization") != f"Bearer {TOKEN}":
                return web.Response(status=401)
        return await handler(request)

    def json(self, factory):
        async def handler(_request: web.Request) -> web.Response:
            return web.json_response(factory())
        return handler

    async def ok(self, _request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def login(self, request: web.Request) -> web.Response:
        form = await request.post()
        if form.get("_password") != self.valid_password:
            return web.Response(status=401)
        return web.json_response({"token": TOKEN, "refresh_token": REFRESH})

    async def refresh(self, _request: web.Request) -> web.Response:
        return web.json_response({"token": TOKEN, "refresh_token": REFRESH})

    async def plan(self, request: web.Request) -> web.Response:
        pid = int(request.query.get("id") or self.plans()[1]["id"])
        return web.json_response(self.plan_detail(pid))

    async def open_seguimiento(self, _request: web.Request) -> web.Response:
        if not self.open_measurement:
            return web.Response(status=404)
        return web.json_response({"id": 77})

    # ------------------------------------------------------------------ running
    def calls(self, method: str, path_prefix: str) -> list[dict]:
        return [r for r in self.requests if r["method"] == method and r["path"].startswith(path_prefix)]


class RunningFake:
    """Runs the fake server in its own thread/event loop so sync tests can use it."""

    def __init__(self, port: int) -> None:
        self.state = FakeDietoPro()
        self.port = port
        self._loop = asyncio.new_event_loop()
        self._runner: web.AppRunner | None = None
        self._started = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)

        async def start() -> None:
            self._runner = web.AppRunner(self.state.app())
            await self._runner.setup()
            await web.TCPSite(self._runner, "127.0.0.1", self.port).start()
            self._started.set()

        self._loop.run_until_complete(start())
        self._loop.run_forever()

    def start(self) -> "RunningFake":
        self._thread.start()
        self._started.wait(10)
        return self

    def stop(self) -> None:
        if self._runner:
            asyncio.run_coroutine_threadsafe(self._runner.cleanup(), self._loop).result(5)
        self._loop.call_soon_threadsafe(self._loop.stop)
