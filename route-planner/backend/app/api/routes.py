"""HTTP- и WebSocket-эндпоинты диспетчерской.

Расчёт блокирующий и длится секунды, поэтому каждый вызов солвера уходит в
пул потоков (`run_in_threadpool`). Иначе один «Построить план» вешает весь
сервер, включая WebSocket, и интерфейс выглядит зависшим.

После каждого изменения плана всем подключённым клиентам уходит уведомление —
диспетчеру не нужно жать «обновить», чтобы увидеть последствия события.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..domain.models import hhmm_to_min, min_to_hhmm
from .serialize import engineer_out, job_out, plan_out
from .service import PlanningService

router = APIRouter(prefix="/api")
ws_router = APIRouter()

#: Сервис создаётся в main.py и подставляется сюда при старте приложения.
service: PlanningService = None  # type: ignore[assignment]


def bind(svc: PlanningService) -> None:
    global service
    service = svc


# --------------------------------------------------------------------------
# WebSocket
# --------------------------------------------------------------------------

class Hub:
    def __init__(self) -> None:
        self.active: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)

    async def broadcast(self, message: dict) -> None:
        for ws in list(self.active):
            try:
                await ws.send_json(message)
            except Exception:
                self.active.discard(ws)


hub = Hub()


@ws_router.websocket("/ws")
async def websocket(ws: WebSocket) -> None:
    await hub.connect(ws)
    try:
        await ws.send_json({"type": "hello", "version": service.version,
                            "now": min_to_hhmm(service.now)})
        while True:
            await ws.receive_text()          # клиент ничего не шлёт, держим канал
    except WebSocketDisconnect:
        hub.disconnect(ws)
    except Exception:
        hub.disconnect(ws)


# --------------------------------------------------------------------------
# Запросы
# --------------------------------------------------------------------------

class BuildRequest(BaseModel):
    preset: str = Field("default", pattern="^(default|sla|travel|balance)$")
    time_limit_s: int = Field(15, ge=1, le=120)
    all_jobs: bool = False


class ReplanRequest(BaseModel):
    at: str | None = Field(None, description='Момент времени "ЧЧ:ММ"')
    time_limit_s: int = Field(3, ge=1, le=60)
    stability: int = Field(200, ge=0, le=5000)


class PinRequest(BaseModel):
    job_id: str
    engineer_id: str | None = Field(
        None, description="Кому закрепить. Пусто — снять закрепление")
    #: По умолчанию берётся лимит исходного расчёта, иначе «цена закрепления»
    #: смешается с эффектом более короткого поиска.
    time_limit_s: int | None = Field(None, ge=1, le=60)


# --------------------------------------------------------------------------
# Справочники
# --------------------------------------------------------------------------

@router.get("/health")
def health() -> dict:
    return {"ok": True, "ready": service.ready, "version": service.version,
            "now": min_to_hhmm(service.now), "date": service.ds.date,
            "staff_changed": service.staff_changed,
            "engineers": len(service.ds.engineers)}


@router.get("/dataset")
def dataset() -> dict:
    ds = service.ds
    return {
        "date": ds.date,
        "day": [min_to_hhmm(service.day_start), min_to_hhmm(service.day_end)],
        "specializations": ds.specializations,
        "work_types": [
            {"id": w.id, "name": w.name, "specialization": w.specialization,
             "min_level": w.min_level, "base_duration_min": w.base_duration_min,
             "equipment": [{"id": q, "name": ds.equipment[q].name,
                            "bulky": ds.equipment[q].bulky}
                           for q in w.equipment]}
            for w in ds.work_types.values()
        ],
        "equipment": [
            {"id": e.id, "name": e.name, "bulky": e.bulky, "units": e.units,
             "stock": e.stock, "rare": e.is_rare}
            for e in ds.equipment.values()
        ],
        "warehouses": [
            {"id": w.id, "name": w.name, "lat": w.lat, "lon": w.lon,
             "address": w.address,
             "open": [min_to_hhmm(w.open_from), min_to_hhmm(w.open_to)]}
            for w in ds.warehouses.values()
        ],
        "counts": {"jobs": len(ds.jobs), "engineers": len(ds.engineers),
                   "events": len(ds.events)},
    }


class EngineerIn(BaseModel):
    """Карточка инженера. Проверяется сервисом, а не только схемой:
    ошибки должны приходить с человеческой формулировкой."""

    id: str | None = None
    name: str
    skills: dict[str, int]
    shift_start: str
    shift_end: str
    break_from: str = "12:00"
    break_to: str = "15:00"
    break_min: int = 45
    vehicle_type: str = "car"
    home_lat: float
    home_lon: float
    home_address: str = ""
    onboard_equipment: list[str] = Field(default_factory=list)
    max_overtime_min: int = 60


@router.get("/engineers")
def engineers() -> list[dict]:
    onboard = service.plan.onboard if service.plan else None
    return [engineer_out(service.ds, e, onboard) for e in service.ds.engineers]


@router.post("/engineers", status_code=201)
async def create_engineer(req: EngineerIn) -> dict:
    try:
        eng = await run_in_threadpool(service.upsert_engineer, req.model_dump(), None)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await hub.broadcast({"type": "staff", "version": service.version})
    return engineer_out(service.ds, eng, service.plan.onboard if service.plan else None)


@router.patch("/engineers/{engineer_id}")
async def update_engineer(engineer_id: str, req: EngineerIn) -> dict:
    if not any(e.id == engineer_id for e in service.ds.engineers):
        raise HTTPException(404, f"Инженера {engineer_id} нет в справочнике")
    try:
        eng = await run_in_threadpool(service.upsert_engineer, req.model_dump(),
                                      engineer_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await hub.broadcast({"type": "staff", "version": service.version})
    return engineer_out(service.ds, eng, service.plan.onboard if service.plan else None)


@router.delete("/engineers/{engineer_id}")
async def remove_engineer(engineer_id: str) -> dict:
    try:
        await run_in_threadpool(service.delete_engineer, engineer_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await hub.broadcast({"type": "staff", "version": service.version})
    return {"ok": True, "engineers": len(service.ds.engineers)}


@router.get("/jobs")
def jobs() -> list[dict]:
    plan = service.plan
    assigned: dict[str, str] = {}
    if plan:
        for r in plan.routes:
            for s in r.stops:
                if s.kind == "job" and s.job_id:
                    assigned[s.job_id] = r.engineer_id
    out = []
    for job in service.ds.jobs:
        if job.id in service.completed:
            status = "done"
        elif job.id in assigned:
            status = "planned"
        elif plan and job.id in plan.unassigned:
            status = "unassigned"
        else:
            status = "new"
        item = job_out(service.ds, job, status)
        item["engineer_id"] = assigned.get(job.id) or service.completed.get(job.id)
        out.append(item)
    return out


class JobIn(BaseModel):
    """Заявка, поступившая в течение дня."""

    customer: str
    work_type_id: str
    address: str = ""
    district: str = ""
    lat: float
    lon: float
    # Диапазон проверяет сервис, а не схема: pydantic отдаёт наружу свой
    # массив ошибок, а диспетчеру нужна фраза на русском.
    complexity: int = 3
    priority: str = "P3"
    tw_start: str
    tw_end: str
    tw_hard: bool = False
    contact_phone: str = ""
    time_limit_s: int = Field(3, ge=1, le=30)
    stability: int = Field(200, ge=0, le=5000)


@router.post("/jobs", status_code=201)
async def create_job(req: JobIn) -> dict:
    """Принять заявку и сразу пересчитать остаток дня.

    Пересчёт входит в приём, а не выносится отдельной кнопкой: кейс требует
    «автоматически перепланирует день при поступлении новой заявки».
    """
    body = req.model_dump()
    try:
        job, diff = await run_in_threadpool(
            service.add_job, body, req.time_limit_s, req.stability)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    payload = {
        "job": job_out(service.ds, job, "planned" if diff else "new"),
        "plan": _plan_payload(service.diff) if service.ready else None,
    }
    await hub.broadcast({"type": "plan", "reason": "job", "version": service.version,
                         "plan": payload["plan"]})
    return payload


@router.get("/events")
def events() -> list[dict]:
    return [{"at": min_to_hhmm(e.at), "type": e.type, "payload": e.payload,
             "comment": e.comment, "fired": e.at <= service.now}
            for e in service.ds.events]


@router.get("/log")
def log() -> list[dict]:
    return service.log


# --------------------------------------------------------------------------
# План
# --------------------------------------------------------------------------

def _plan_payload(diff=None) -> dict:
    return plan_out(service.ds, service.plan, plan_id=f"plan-{service.version}",
                    now=service.now, completed=service.completed, diff=diff) | {
        "staff_changed": service.staff_changed}


@router.get("/plan")
def get_plan() -> dict:
    if not service.ready:
        raise HTTPException(404, "План ещё не построен — вызовите /api/plan/build")
    return _plan_payload(service.diff)


@router.post("/plan/build")
async def build(req: BuildRequest) -> dict:
    await run_in_threadpool(service.build, req.preset, req.time_limit_s,
                            req.all_jobs)
    payload = _plan_payload()
    await hub.broadcast({"type": "plan", "reason": "build",
                         "version": service.version, "plan": payload})
    return payload


@router.post("/plan/replan")
async def do_replan(req: ReplanRequest) -> dict:
    if not service.ready:
        raise HTTPException(409, "Сначала постройте план")
    if req.at is None:
        raise HTTPException(422, 'Укажите момент времени, например {"at": "12:30"}')
    try:
        _, diff = await run_in_threadpool(
            service.replan_at, hhmm_to_min(req.at), req.time_limit_s,
            req.stability)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    payload = _plan_payload(diff)
    await hub.broadcast({"type": "plan", "reason": "replan",
                         "version": service.version, "plan": payload})
    return payload


@router.post("/plan/step")
async def step(req: ReplanRequest) -> dict:
    """Промотать день до ближайшего события и пересчитать остаток."""
    if not service.ready:
        raise HTTPException(409, "Сначала постройте план")
    result = await run_in_threadpool(service.step, req.time_limit_s, req.stability)
    if result is None:
        raise HTTPException(409, "События дня закончились")
    _, diff, fired = result
    payload = _plan_payload(diff) | {"events": fired}
    await hub.broadcast({"type": "plan", "reason": "event",
                         "version": service.version, "events": fired,
                         "plan": payload})
    return payload


@router.post("/plan/pin")
async def pin(req: PinRequest) -> dict:
    """Закрепить заявку за инженером или снять закрепление.

    Недопустимое закрепление отклоняется с объяснением — это и есть «проверка
    допустимости при ручном вмешательстве» из требований кейса.
    """
    if not service.ready:
        raise HTTPException(409, "Сначала постройте план")
    try:
        if req.engineer_id:
            cost = await run_in_threadpool(
                service.set_pin, req.job_id, req.engineer_id, req.time_limit_s)
        else:
            cost = await run_in_threadpool(
                service.clear_pin, req.job_id, req.time_limit_s)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    payload = _plan_payload(service.diff) | {"cost": cost}
    await hub.broadcast({"type": "plan", "reason": "pin",
                         "version": service.version, "plan": payload})
    return payload


@router.post("/plan/finish")
async def finish() -> dict:
    await run_in_threadpool(service.finish_day)
    return {"completed": len(service.completed)}


@router.post("/plan/reset")
async def reset() -> dict:
    await run_in_threadpool(service.reset)
    await hub.broadcast({"type": "reset", "version": service.version})
    return {"ok": True, "now": min_to_hhmm(service.now)}


# --------------------------------------------------------------------------
# Объяснимость
# --------------------------------------------------------------------------

@router.get("/plan/explain/{job_id}")
def explain(job_id: str) -> dict:
    if not service.ready:
        raise HTTPException(409, "Сначала постройте план")
    exp = service.explain(job_id)
    if exp is None:
        raise HTTPException(404, f"{job_id} в текущем плане нет")
    return {
        "job_id": exp.job_id,
        "engineer_id": exp.engineer_id,
        "engineer_name": exp.engineer_name,
        "choice": exp.choice,
        "timing": exp.timing,
        "alternatives": [
            {"engineer_id": a.engineer_id, "engineer_name": a.engineer_name,
             "possible": a.possible, "detail": a.detail,
             "extra_travel_min": a.extra_travel}
            for a in exp.alternatives
        ],
        "text": exp.text(),
    }


def _why_not_payload(job_id: str) -> dict:
    wn = service.why_not(job_id)
    if wn is None:
        raise HTTPException(409, "Сначала постройте план")
    return {
        "job_id": wn.job_id,
        "verdict": wn.verdict(),
        "counts": wn.counts,
        "qualified": [
            {"engineer_id": b.engineer_id, "engineer_name": b.engineer_name,
             "reason": b.reason, "detail": b.detail}
            for b in wn.qualified
        ],
        "blocked_total": len(wn.blockers),
        "feasible_with_shift": [
            {"engineer_name": n, "detail": d} for n, d in wn.feasible_with_shift
        ],
    }


@router.get("/plan/why-not")
def why_not_all() -> list[dict]:
    if not service.ready:
        raise HTTPException(409, "Сначала постройте план")
    return [_why_not_payload(j.id) | {"job": job_out(service.ds, j, "unassigned")}
            for j in service.unassigned_jobs()]


@router.get("/plan/why-not/{job_id}")
def why_not_one(job_id: str) -> dict:
    return _why_not_payload(job_id)


# --------------------------------------------------------------------------
# Сравнение с ручным планированием
# --------------------------------------------------------------------------

@router.get("/plan/compare")
async def compare_manual(order: str = "edf", pick: str = "nearest") -> dict:
    if not service.ready:
        raise HTTPException(409, "Сначала постройте план")
    base, opt, cmp = await run_in_threadpool(
        service.compare_with_manual, order, pick)
    return {
        "scope": "morning",
        "note": "Сравнение считается по утреннему плану: после перепланирования "
                "день уже частично прожит, и ручной план с нуля с ним несопоставим.",
        "baseline_kpi": base.kpi,
        "optimized_kpi": opt.kpi,
        "rows": [{"label": lb, "manual": m, "optimized": o, "effect": d}
                 for lb, m, o, d in cmp.rows],
        "baseline_routes": [
            {"engineer_id": r.engineer_id, "job_count": r.job_count,
             "travel_min": r.travel_min, "travel_km": r.travel_km}
            for r in base.routes
        ],
    }
