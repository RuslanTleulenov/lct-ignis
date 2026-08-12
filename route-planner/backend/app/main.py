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

DEFAULT_SNAPSHOT = (Path(__file__).resolve().parents[2]
                    / "data" / "seed" / "snapshot.json")


@asynccontextmanager
async def lifespan(app: FastAPI):
    snapshot = os.environ.get("ROUTE_PLANNER_SNAPSHOT", str(DEFAULT_SNAPSHOT))
    if not Path(snapshot).exists():
        raise RuntimeError(
            f"Датасет не найден: {snapshot}\n"
            f"Сгенерируйте его командой: py -3.11 data/generate.py")
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
