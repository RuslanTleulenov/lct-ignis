"""Точка входа сервиса.

    uvicorn app.main:app --reload --port 8000

Документация API — на /docs, она же годится как демонстрация «что умеет
бэкенд» без интерфейса.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import routes
from .api.service import PlanningService

#: Набор по умолчанию — выгрузка заказчика; синтетика — если её нет.
_DATA = Path(__file__).resolve().parents[2] / "data"
DEFAULT_SNAPSHOT = (_DATA / "beeline" / "vostok" / "snapshot.json"
                    if (_DATA / "beeline" / "vostok" / "snapshot.json").exists()
                    else _DATA / "seed" / "snapshot.json")


@asynccontextmanager
async def lifespan(app: FastAPI):
    snapshot = os.environ.get("ROUTE_PLANNER_SNAPSHOT", str(DEFAULT_SNAPSHOT))
    if not Path(snapshot).exists():
        raise RuntimeError(
            f"Датасет не найден: {snapshot}\n"
            f"Соберите его: py -3.11 data/import_beeline.py "
            f"(выгрузка заказчика) или py -3.11 data/generate.py (синтетика)")
    routes.bind(PlanningService(snapshot))
    yield


app = FastAPI(
    title="Планирование маршрутов инженеров",
    description="Сервис ЛЦТ 2026: распределение заявок, маршруты с временными "
                "окнами, перепланирование по событиям дня и объяснение решений.",
    version="0.1.0",
    lifespan=lifespan,
)

# Фронтенд живёт на отдельном порту дев-сервера Vite
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174", "http://127.0.0.1:5174",
                   "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes.router)
app.include_router(routes.ws_router)


@app.get("/")
def root() -> dict:
    return {"service": "route-planner", "docs": "/docs", "api": "/api/health"}
