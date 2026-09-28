"""Приём заявки в работающий день и автоматический пересчёт.

Требование кейса: «автоматически перепланирует день при поступлении новой
заявки». Пересчёт здесь — часть приёма, а не отдельная кнопка, и тесты
проверяют именно это.
"""

from __future__ import annotations

import shutil

import pytest

from app.api.service import PlanningService
from conftest import SNAPSHOT


@pytest.fixture()
def service(tmp_path, provider):
    if not SNAPSHOT.exists():
        pytest.skip("датасет не сгенерирован: py -3.11 data/generate.py")
    copy = tmp_path / "snapshot.json"
    shutil.copy2(SNAPSHOT, copy)
    svc = PlanningService(copy)
    svc.provider = provider
    return svc


def payload(**over) -> dict:
    base = {
        "customer": "БЦ «Аврора Плаза»",
        "work_type_id": "WT-03",
        "address": "Пресненская наб., 12",
        "district": "Пресненский",
        "lat": 55.7494, "lon": 37.5395,
        "complexity": 3, "priority": "P2",
        "tw_start": "10:00", "tw_end": "16:00", "tw_hard": False,
    }
    return {**base, **over}


def test_fields_are_derived_from_directory(service):
    from app.domain.models import nominal_duration

    job, _ = service.add_job(payload())
    wt = service.ds.work_types["WT-03"]
    assert job.specialization == wt.specialization
    assert job.min_level == wt.min_level
    assert job.required_equipment == wt.equipment, "оборудование берётся из матрицы"
    assert job.duration_min == nominal_duration(wt.base_duration_min, 3)
    assert job.duration_min > wt.base_duration_min, "сложность 3 растягивает норматив"
    assert job.created_at_min == service.now
    assert job.known_at_day_start is False


def test_sla_counts_from_arrival(service):
    p1, _ = service.add_job(payload(priority="P1", tw_end="18:00"))
    assert p1.sla_deadline == min(service.now + 4 * 60, service.day_end)
    p3, _ = service.add_job(payload(priority="P3"))
    assert p3.sla_deadline == p3.tw_end, "для плановых срок равен концу окна"


def test_intake_replans_automatically(service):
    service.build(time_limit_s=6)
    before = service.plan.kpi["travel_min"]
    job, diff = service.add_job(payload(priority="P1", tw_end="18:00"),
                                time_limit_s=4)

    assert diff is not None, "приём заявки обязан пересчитать день"
    assigned = {s.job_id for r in service.plan.routes for s in r.stops
                if s.kind == "job"}
    assert job.id in assigned or job.id in service.plan.unassigned
    assert service.plan.kpi["travel_min"] != before or diff.kept


def test_intake_without_plan_just_registers(service):
    job, diff = service.add_job(payload())
    assert diff is None, "пересчитывать нечего — плана ещё нет"
    assert any(j.id == job.id for j in service.ds.jobs)


def test_replan_keeps_most_of_the_day(service):
    """Одна заявка не должна перекраивать весь день.

    Проверяются структурные свойства, а не конкретные числа: тесты идут на
    геодезическом приближении, которое завышает дорогу и делает задачу теснее,
    поэтому доля затронутых плавает. На графе OSM при лимите 3 с наблюдаем
    2–4 инженера из 18.
    """
    service.build(time_limit_s=6)
    _, diff = service.add_job(payload(priority="P1", tw_end="18:00"), time_limit_s=4)
    assert diff.kept > len(diff.moved), "большая часть визитов осталась на месте"
    assert diff.untouched, "хотя бы часть службы день не заметила"


def test_new_job_is_not_persisted(service):
    """Снимок — входной датасет дня, а не журнал операций.

    Иначе «Сброс» перестанет возвращать день к утру и повторить прогон
    с теми же цифрами станет невозможно.
    """
    import json

    before = len(json.loads(service.snapshot.read_text(encoding="utf-8"))["jobs"])
    service.add_job(payload())
    after = len(json.loads(service.snapshot.read_text(encoding="utf-8"))["jobs"])
    assert after == before

    service.reset()
    assert len(service.ds.jobs) == before, "после сброса день вернулся к исходному"


@pytest.mark.parametrize("patch, message", [
    ({"customer": ""}, "заказчика"),
    ({"work_type_id": "WT-99"}, "тип работ"),
    ({"complexity": 9}, "от 1 до 5"),
    ({"tw_start": "14:00", "tw_end": "10:00"}, "раньше начала"),
    ({"tw_start": "06:00", "tw_end": "07:00"}, "закрылось"),
    ({"lat": 59.93, "lon": 30.33}, "вне зоны обслуживания"),
    ({"priority": "P9"}, "приоритет"),
])
def test_validation_rejects(service, patch, message):
    with pytest.raises(ValueError, match=message):
        service.add_job(payload(**patch))


def test_short_window_is_allowed(service):
    """Окно короче норматива — не ошибка: по ТЗ оно ограничивает начало работ.

    Слот заказчика «10:00–12:00» под двухчасовой ремонт значит «мастер придёт
    с десяти до двенадцати», а не «уйдёт к двенадцати». Раньше такая заявка
    отвергалась на вводе, и диспетчер не мог завести половину реальных слотов.
    """
    wt = service.ds.work_types["WT-05"]        # самый длинный норматив
    job, _ = service.add_job(payload(work_type_id=wt.id, complexity=5,
                                     tw_start="10:00", tw_end="12:00"))
    assert job.duration_min > 120 and job.tw_end - job.tw_start == 120
