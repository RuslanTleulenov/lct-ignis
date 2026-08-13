"""Справочник инженеров: заведение, правка, удаление, валидация.

Тесты работают на копии снимка во временном каталоге: справочник пишется на
диск, и трогать рабочий датасет они не должны.
"""

from __future__ import annotations

import json
import shutil

import pytest

from app.api.service import PlanningService
from conftest import SNAPSHOT


@pytest.fixture()
def service(tmp_path):
    if not SNAPSHOT.exists():
        pytest.skip("датасет не сгенерирован: py -3.11 data/generate.py")
    copy = tmp_path / "snapshot.json"
    shutil.copy2(SNAPSHOT, copy)
    return PlanningService(copy)


VALID = {
    "name": "Проверкин Пётр",
    "skills": {"security": 3},
    "shift_start": "09:00", "shift_end": "18:00",
    "break_from": "12:00", "break_to": "15:00", "break_min": 45,
    "vehicle_type": "van",
    "home_lat": 55.76, "home_lon": 37.62,
    "home_address": "Тверская ул., 1",
    "onboard_equipment": ["EQ-LAPTOP"],
}


def test_create_assigns_free_code_and_persists(service):
    before = len(service.ds.engineers)
    eng = service.upsert_engineer(dict(VALID))

    assert eng.id not in {e.id for e in service.ds.engineers[:before]}
    assert len(service.ds.engineers) == before + 1

    saved = json.loads(service.snapshot.read_text(encoding="utf-8"))
    row = next(e for e in saved["engineers"] if e["id"] == eng.id)
    assert row["name"] == VALID["name"]
    assert row["shift_start"] == "09:00"
    assert row["skills"] == {"security": 3}
    # остальные разделы снимка не пострадали
    assert len(saved["jobs"]) == len(service.ds.jobs)


def test_update_replaces_in_place(service):
    eng = service.upsert_engineer(dict(VALID))
    count = len(service.ds.engineers)
    service.upsert_engineer({**VALID, "name": "Другое Имя",
                             "skills": {"security": 4, "it": 2}}, eng.id)
    assert len(service.ds.engineers) == count
    updated = service.ds.engineer(eng.id)
    assert updated.name == "Другое Имя"
    assert updated.skills == {"security": 4, "it": 2}


def test_duplicate_code_rejected(service):
    existing = service.ds.engineers[0].id
    with pytest.raises(ValueError, match="уже есть"):
        service.upsert_engineer({**VALID, "id": existing})


@pytest.mark.parametrize("patch, message", [
    ({"name": "П"}, "ФИО"),
    ({"skills": {}}, "специализация"),
    ({"skills": {"нетакой": 2}}, "Неизвестная специализация"),
    ({"skills": {"security": 9}}, "от 1 до 4"),
    ({"shift_start": "09:00", "shift_end": "10:00"}, "короче двух часов"),
    ({"break_from": "07:00", "break_to": "08:00"}, "внутри смены"),
    ({"onboard_equipment": ["EQ-НЕТ"]}, "Нет такого оборудования"),
    ({"vehicle_type": "walk_transit", "onboard_equipment": ["EQ-LADDER"]},
     "без автомобиля"),
    ({"home_lat": 59.93, "home_lon": 30.33}, "вне зоны обслуживания"),
])
def test_validation_rejects(service, patch, message):
    with pytest.raises(ValueError, match=message):
        service.upsert_engineer({**VALID, **patch})


def test_delete_free_engineer(service):
    eng = service.upsert_engineer(dict(VALID))
    count = len(service.ds.engineers)
    service.delete_engineer(eng.id)
    assert len(service.ds.engineers) == count - 1
    saved = json.loads(service.snapshot.read_text(encoding="utf-8"))
    assert all(e["id"] != eng.id for e in saved["engineers"])


def test_delete_busy_engineer_refused(service, provider):
    """Инженера с визитами в плане удалять нельзя: план осталcя бы битым."""
    service.provider = provider
    service.build(time_limit_s=4)
    busy = next(r for r in service.plan.routes if r.job_count)
    with pytest.raises(ValueError, match="ведёт"):
        service.delete_engineer(busy.engineer_id)
    assert any(e.id == busy.engineer_id for e in service.ds.engineers)


def test_staff_change_marks_plan_stale(service, provider):
    service.provider = provider
    service.build(time_limit_s=4)
    assert service.staff_changed is False
    service.upsert_engineer(dict(VALID))
    assert service.staff_changed is True, "план построен на прежнем составе"
    service.build(time_limit_s=4)
    assert service.staff_changed is False


def test_new_engineer_participates_after_rebuild(service, provider):
    service.provider = provider
    service.build(time_limit_s=4)
    eng = service.upsert_engineer(dict(VALID))
    assert all(r.engineer_id != eng.id for r in service.plan.routes), (
        "новый инженер не должен появляться в уже построенном плане")
    service.build(time_limit_s=4)
    assert any(r.engineer_id == eng.id for r in service.plan.routes)
