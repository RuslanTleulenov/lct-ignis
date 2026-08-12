"""Общие фикстуры.

План считается один раз на всю сессию: каждый прогон солвера — это секунды,
а тесты проверяют свойства решения, а не воспроизводимость поиска.

Маршрутизация в тестах — геодезическое приближение, а не граф OSM. Тесты
проверяют ограничения солвера, и падать из-за отсутствия 14-мегабайтной
выгрузки они не должны. Сам граф проверяется отдельно в test_graph.py и
пропускается, если выгрузки нет.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain.loader import load_dataset          # noqa: E402
from app.solver.engine import solve                 # noqa: E402
from app.travel.provider import CachedProvider, HaversineProvider  # noqa: E402

SNAPSHOT = ROOT.parent / "data" / "seed" / "snapshot.json"
OSM_DIR = ROOT.parent / "data" / "osm"


@pytest.fixture(scope="session")
def ds():
    if not SNAPSHOT.exists():
        pytest.skip("датасет не сгенерирован: py -3.11 data/generate.py")
    return load_dataset(SNAPSHOT)


@pytest.fixture(scope="session")
def provider():
    return CachedProvider(HaversineProvider())


@pytest.fixture(scope="session")
def morning_jobs(ds):
    return [j for j in ds.jobs if j.known_at_day_start]


@pytest.fixture(scope="session")
def plan(ds, morning_jobs, provider):
    return solve(ds, morning_jobs, provider=provider, time_limit_s=6)


def visits(plan):
    """Все назначенные визиты как (маршрут, остановка)."""
    return [(r, s) for r in plan.routes for s in r.stops if s.kind == "job"]
