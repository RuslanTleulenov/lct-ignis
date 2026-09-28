"""Состояние диспетчерской: датасет, текущий план, прожитая часть дня.

Состояние держится в памяти, а не в базе. Для сервиса, который считает план на
день и живёт один прогон демонстрации, СУБД добавила бы миграции и ничего не
дала бы взамен: источник правды — snapshot.json на диске, а сброс к началу дня
должен быть мгновенным. Когда понадобится история планов между запусками,
сюда встанет SQLAlchemy без изменения API.

Расчёт блокирующий и занимает секунды, поэтому все операции защищены общим
замком: два одновременных «Построить план» не должны драться за состояние.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from ..baseline.control import control_plan
from ..baseline.greedy import Comparison, compare, greedy_plan, tz_baseline
from ..domain.importer import ImportError_, decode, read_table, snapshot_from_json,     snapshot_from_tables
from .datasets import DatasetInfo, Registry
from ..domain.loader import (load_dataset, parse_priority, parse_transport,
                             save_engineers)
from ..domain.models import (
    DayEvent, Engineer, Job, Priority, TransportMode, hhmm_to_min, min_to_hhmm,
    nominal_duration,
)
from ..explain.why_not import WhyNot, why_not
from ..explain.route import RouteExplanation, explain_route
from ..explain.why_this import Explanation, why_this
from ..solver.engine import Plan, Weights, attach_geometry, solve
from ..solver.replan import DayState, PlanDiff, project, replan, state_at
from ..travel.provider import default_provider


#: Зона обслуживания — Москва и область. Точка вне дорожной выгрузки считается
#: по прямой с коэффициентом (см. travel/osm.py), поэтому граница здесь только
#: отсекает опечатки в координатах, а не ограничивает географию заявок.
SERVICE_AREA = (54.2, 35.1, 56.9, 40.2)


class PlanningService:
    def __init__(self, snapshot: str | Path) -> None:
        self.snapshot = Path(snapshot)
        self.provider = default_provider()
        self._lock = threading.RLock()
        self.registry = Registry()
        self.reset()

    # -- наборы данных ----------------------------------------------------

    def assumptions(self) -> list[str]:
        """Допущения, принятые при подготовке набора, — из meta снимка."""
        try:
            raw = json.loads(self.snapshot.read_text(encoding="utf-8"))
            return list(raw.get("meta", {}).get("assumptions", []))
        except Exception:
            return []

    @property
    def dataset_key(self) -> str | None:
        return self.registry.key_for(self.snapshot)

    def switch_dataset(self, key: str) -> DatasetInfo:
        """Переключиться на встроенный или загруженный набор. День сбрасывается."""
        with self._lock:
            info = self.registry.get(key)
            self.snapshot = info.path
            self.reset()
            return info

    def upload_dataset(self, files: dict[str, bytes], name: str = "") -> DatasetInfo:
        """Принять CSV/JSON, собрать снимок, зарегистрировать и переключиться.

        files: имя файла -> содержимое. Один .json — снимок сервиса либо
        {jobs, engineers, events}; либо CSV-таблицы, где файл с заявками
        угадывается по имени (jobs/заявки), с инженерами — по (engineers/инженеры),
        с событиями — по (events/события).
        """
        geocode = self._geocoder()
        jsons = {n: b for n, b in files.items() if n.lower().endswith(".json")}
        if jsons:
            n, b = next(iter(jsons.items()))
            try:
                data = json.loads(decode(b))
            except json.JSONDecodeError as exc:
                raise ImportError_([f"{n}: не JSON — {exc.msg} (строка {exc.lineno})"])
            snapshot = snapshot_from_json(data, geocode)
            title = name or snapshot["meta"].get("title") or n
        else:
            def pick(*needles):
                for n, b in files.items():
                    low = n.lower()
                    if any(x in low for x in needles):
                        return read_table(decode(b))
                return None
            jobs = pick("job", "заявк", "orders", "tasks")
            engineers = pick("engineer", "инженер", "brigad", "бригад", "staff")
            events = pick("event", "событ")
            if jobs is None or engineers is None:
                raise ImportError_(["нужны два CSV: с заявками (jobs/заявки в имени) "
                                    "и с инженерами (engineers/инженеры в имени)"])
            title = name or "Загруженный набор"
            snapshot = snapshot_from_tables(jobs, engineers, events, title=title,
                                            geocode=geocode)
            snapshot["meta"]["title"] = title
        info = self.registry.register_upload(snapshot, title)
        return self.switch_dataset(info.key)

    def _geocoder(self):
        """Геокодер для адресов без координат: кеш заказчика плюс сеть."""
        try:
            import sys
            data_dir = Path(__file__).resolve().parents[3] / "data"
            if str(data_dir) not in sys.path:
                sys.path.insert(0, str(data_dir))
            from geocode import Geocoder            # noqa: WPS433
            gc = Geocoder(online=True)
        except Exception:
            return None

        def lookup(address: str, district: str) -> tuple[float, float, str]:
            geo = gc.lookup(address, district)
            gc.save()
            return geo.lat, geo.lon, geo.precision
        return lookup

    # -- жизненный цикл ---------------------------------------------------

    def reset(self) -> None:
        """Вернуть день к утру. Датасет перечитывается с диска."""
        with self._lock:
            self.ds = load_dataset(self.snapshot)
            self.plan: Plan | None = None
            #: Утренний план и его набор заявок. Сравнение с ручным
            #: планированием считается только по ним: после перепланирования
            #: день уже наполовину прожит, а baseline строил бы его с нуля —
            #: такие числа сравнивать нельзя.
            self.morning_plan: Plan | None = None
            self.morning_jobs: list[Job] = []
            self.baseline: Plan | None = None
            self.diff: PlanDiff | None = None
            self.completed: dict[str, str] = {}
            #: Ручные закрепления диспетчера: заявка -> инженер. Живут поверх
            #: любых пересчётов, включая построение плана заново: это решение
            #: человека, и система не вправе тихо его отменить.
            self.pins: dict[str, str] = {}
            self.preset = "default"
            self.time_limit = 15
            self.day_start = min(e.shift_start for e in self.ds.engineers)
            self.day_end = max(e.shift_end for e in self.ds.engineers)
            self.now = self.day_start
            self.version = 0
            #: Справочник менялся после построения плана — план устарел.
            self.staff_changed = False
            self.log: list[dict] = []

    @property
    def ready(self) -> bool:
        return self.plan is not None

    def _note(self, kind: str, text: str, at: int | None = None, **extra) -> None:
        # Время указывается явно: событие журналируется до того, как часы
        # сервиса сдвинутся, иначе запись получает время предыдущего шага.
        self.log.append({"at": min_to_hhmm(self.now if at is None else at),
                         "kind": kind, "text": text,
                         "version": self.version, **extra})

    # -- планирование -----------------------------------------------------

    def build(self, preset: str = "default", time_limit_s: int = 15,
              all_jobs: bool = False, weights: Weights | None = None) -> Plan:
        """Утренний план. Сбрасывает прожитую часть дня."""
        with self._lock:
            self.completed = {}
            self.now = self.day_start
            self.diff = None
            self.preset, self.time_limit = preset, time_limit_s
            jobs = (self.ds.jobs if all_jobs
                    else [j for j in self.ds.jobs if j.known_at_day_start])
            self.plan = solve(
                self.ds, jobs,
                weights=weights or Weights.preset(preset),
                provider=self.provider, time_limit_s=time_limit_s,
                pins=self.pins)
            attach_geometry(self.plan, self.ds, self.provider)
            self.morning_plan, self.morning_jobs = self.plan, jobs
            self.baseline = None
            self.staff_changed = False
            self.version += 1
            k = self.plan.kpi
            self._note("build",
                       f"План на смену сформирован: назначено "
                       f"{k['jobs_assigned']} из {k['jobs_total']}, "
                       f"время в пути {k['travel_min']} мин")
            return self.plan

    def replan_at(self, now: int, time_limit_s: int = 3,
                  stability: int = 200) -> tuple[Plan, PlanDiff]:
        """Пересчитать остаток дня на момент `now`."""
        with self._lock:
            if self.plan is None:
                raise RuntimeError("План ещё не построен")
            if now < self.now:
                raise ValueError(
                    f"Время идёт только вперёд: сейчас {min_to_hhmm(self.now)}, "
                    f"запрошено {min_to_hhmm(now)}")
            self.now = now
            st = state_at(self.ds.events, now, self.completed)
            self.plan, self.diff = replan(
                self.ds, self.plan, st,
                weights=Weights(stability=stability),
                provider=self.provider, time_limit_s=time_limit_s,
                pins=self.pins)
            attach_geometry(self.plan, self.ds, self.provider)
            self.version += 1
            self._note("replan", self.diff.summary(),
                       affected=self.diff.affected)
            return self.plan, self.diff

    def next_event_time(self) -> int | None:
        upcoming = [e.at for e in self.ds.events if e.at > self.now]
        return min(upcoming) if upcoming else None

    def step(self, time_limit_s: int = 3, stability: int = 200
             ) -> tuple[Plan, PlanDiff, list[dict]] | None:
        """Промотать день до ближайшего события и перепланировать."""
        with self._lock:
            nxt = self.next_event_time()
            if nxt is None:
                return None
            fired = [{"at": min_to_hhmm(e.at), "type": e.type,
                      "payload": e.payload, "comment": e.comment}
                     for e in self.ds.events if e.at == nxt]
            for e in fired:
                self._note("event", e["comment"], at=nxt, event_type=e["type"])
            plan, diff = self.replan_at(nxt, time_limit_s, stability)
            return plan, diff, fired

    def finish_day(self) -> None:
        """Досчитать день до конца смен: всё запланированное считается сделанным."""
        with self._lock:
            if self.plan is None:
                return
            project(self.ds, self.plan,
                    state_at(self.ds.events, self.day_end + 60, self.completed))

    # -- приём новой заявки ------------------------------------------------

    def add_job(self, data: dict, time_limit_s: int = 3,
                stability: int = 200) -> tuple[Job, PlanDiff | None]:
        """Принять заявку в работающий день и сразу пересчитать остаток.

        Пересчёт здесь не отдельная кнопка, а часть приёма: требование кейса —
        «автоматически перепланирует день при поступлении новой заявки».

        Заявка живёт в памяти до «Сброса» и на диск не пишется. Снимок — это
        входной датасет дня, а не журнал операций: если складывать туда всё
        созданное, «Сброс» перестанет возвращать день к утру, и повторить
        демонстрацию с теми же цифрами станет невозможно.
        """
        with self._lock:
            job = self._validate_job(data)
            self.ds.jobs.append(job)
            self._note("job", f"Новая заявка {job.priority.value}: {job.customer}",
                       at=job.created_at_min)

            if self.plan is None:
                self.version += 1
                return job, None

            st = state_at(self.ds.events, self.now, self.completed)
            self.plan, self.diff = replan(
                self.ds, self.plan, st, weights=Weights(stability=stability),
                provider=self.provider, time_limit_s=time_limit_s, pins=self.pins)
            attach_geometry(self.plan, self.ds, self.provider)
            self.version += 1
            self._note("replan", self.diff.summary(), affected=self.diff.affected)
            return job, self.diff

    def _validate_job(self, data: dict) -> Job:
        def fail(msg: str):
            raise ValueError(msg)

        wt_id = str(data.get("work_type_id", ""))
        wt = self.ds.work_types.get(wt_id)
        if wt is None:
            fail("Выберите тип работ")

        customer = str(data.get("customer", "")).strip()
        if len(customer) < 2:
            fail("Укажите заказчика")

        try:
            priority = parse_priority(str(data.get("priority", "normal")))
        except ValueError:
            fail(f"Неизвестный приоритет: {data.get('priority')}")

        complexity = int(data.get("complexity", 3))
        if not 1 <= complexity <= 5:
            fail("Категория сложности — от 1 до 5")
        duration = nominal_duration(wt.base_duration_min, complexity)

        try:
            tw_start = hhmm_to_min(str(data.get("tw_start", "")))
            tw_end = hhmm_to_min(str(data.get("tw_end", "")))
        except (ValueError, IndexError):
            fail("Окно доступа указывается в формате ЧЧ:ММ")
        if tw_end <= tw_start:
            fail("Конец окна доступа раньше начала")
        # Сначала про время, потом про размер: закрывшееся окно — более
        # фундаментальная проблема, чем его ширина, и сообщение полезнее.
        if tw_end <= self.now:
            fail(f"Окно закрылось в {min_to_hhmm(tw_end)}, "
                 f"сейчас {min_to_hhmm(self.now)} — заявку уже не выполнить")
        lat, lon = float(data.get("lat", 0)), float(data.get("lon", 0))
        south, west, north, east = SERVICE_AREA
        if not (south <= lat <= north and west <= lon <= east):
            fail("Объект вне зоны обслуживания — укажите адрес в пределах города")

        # SLA считается от момента поступления, как и в генераторе.
        if priority is Priority.URGENT:
            sla = min(self.now + 4 * 60, self.day_end)
        else:
            sla = tw_end

        transport = None
        if data.get("required_transport"):
            try:
                transport = parse_transport(str(data["required_transport"]))
            except ValueError:
                fail(f"Неизвестный тип транспорта: {data['required_transport']}")

        seq = len(self.ds.jobs) + 1
        used = {j.id for j in self.ds.jobs}
        while f"JOB-{seq:04d}" in used:
            seq += 1

        return Job(
            id=f"JOB-{seq:04d}",
            external_id=f"ЗН-{self.ds.date.replace('-', '')[2:]}-{seq:04d}",
            customer=customer,
            district=str(data.get("district", "")).strip() or "—",
            address=str(data.get("address", "")).strip() or "адрес не указан",
            lat=lat, lon=lon,
            work_type_id=wt.id,
            specialization=wt.specialization,
            min_level=wt.min_level,
            complexity=complexity,
            duration_min=duration,
            required_equipment=wt.equipment,
            tw_start=tw_start, tw_end=tw_end,
            tw_hard=bool(data.get("tw_hard", False)),
            priority=priority,
            sla_deadline=sla,
            created_at_min=self.now,
            known_at_day_start=False,
            contact_phone=str(data.get("contact_phone", "")).strip(),
            required_transport=transport,
        )

    def add_event(self, kind: str, payload: str, time_limit_s: int = 3,
                  stability: int = 200) -> tuple[DayEvent, PlanDiff | None]:
        """Событие по требованию диспетчера: отмена заявки или недоступность
        инженера с текущего момента — и немедленный пересчёт остатка дня.

        Это второй и третий сценарии перепланирования из ТЗ (п. 2.1, 6); первый —
        срочная заявка — принимается через `add_job`.
        """
        with self._lock:
            if kind == "job_cancelled":
                job = next((j for j in self.ds.jobs if j.id == payload), None)
                if job is None:
                    raise ValueError(f"Нет заявки {payload}")
                if payload in self.completed:
                    raise ValueError(f"Заявка {payload} уже выполнена — отменять нечего")
                if any(e.type == kind and e.payload == payload for e in self.ds.events):
                    raise ValueError(f"Заявка {payload} уже отменена")
                comment = f"{job.customer} отменил визит (диспетчер)"
            elif kind == "engineer_unavailable":
                eng = next((e for e in self.ds.engineers if e.id == payload), None)
                if eng is None:
                    raise ValueError(f"Нет инженера {payload}")
                if any(e.type == kind and e.payload == payload for e in self.ds.events):
                    raise ValueError(f"{eng.name} уже выведен из смены")
                # без рода: в выгрузке заказчика исполнители — «Бригада Соколов»
                comment = f"{eng.name}: недоступность с {min_to_hhmm(self.now)} (диспетчер)"
            else:
                raise ValueError("Тип события: job_cancelled или engineer_unavailable")

            event = DayEvent(at=self.now, type=kind, payload=payload, comment=comment)
            self.ds.events.append(event)
            self.ds.events.sort(key=lambda e: e.at)
            self._note("event", comment, event_type=kind)

            if self.plan is None:
                self.version += 1
                return event, None
            st = state_at(self.ds.events, self.now, self.completed)
            self.plan, self.diff = replan(
                self.ds, self.plan, st, weights=Weights(stability=stability),
                provider=self.provider, time_limit_s=time_limit_s, pins=self.pins)
            attach_geometry(self.plan, self.ds, self.provider)
            self.version += 1
            self._note("replan", self.diff.summary(), affected=self.diff.affected)
            return event, self.diff

    # -- справочник инженеров ---------------------------------------------

    def upsert_engineer(self, data: dict, engineer_id: str | None = None) -> Engineer:
        """Завести нового инженера или изменить существующего.

        План при этом не пересчитывается: новый человек попадёт в маршруты со
        следующего «Построить план». Дописывать инженера в уже прожитый день
        нельзя — ему неоткуда взять утренний комплект инструмента и негде
        находиться в момент врезки.
        """
        with self._lock:
            eng = self._validate_engineer(data, engineer_id)
            existing = next((i for i, e in enumerate(self.ds.engineers)
                             if e.id == eng.id), None)
            if engineer_id is None and existing is not None:
                raise ValueError(f"Инженер с кодом {eng.id} уже есть")
            if existing is None:
                self.ds.engineers.append(eng)
            else:
                self.ds.engineers[existing] = eng
            self._persist_engineers()
            self._note("staff", f"{'Изменён' if existing is not None else 'Добавлен'} "
                                f"инженер {eng.name} ({eng.id})")
            return eng

    def delete_engineer(self, engineer_id: str) -> None:
        with self._lock:
            eng = next((e for e in self.ds.engineers if e.id == engineer_id), None)
            if eng is None:
                raise ValueError(f"Инженера {engineer_id} нет в справочнике")
            if len(self.ds.engineers) <= 1:
                raise ValueError("Нельзя удалить последнего инженера службы")
            # Удалять человека, на котором висят визиты, — значит оставить план
            # ссылающимся в пустоту. Требуем сначала пересчитать день.
            busy = next((r for r in (self.plan.routes if self.plan else [])
                         if r.engineer_id == engineer_id and r.job_count), None)
            if busy is not None:
                raise ValueError(
                    f"{eng.name} ведёт {busy.job_count} заявок в текущем плане. "
                    f"Постройте план заново или передайте визиты другим.")
            self.ds.engineers = [e for e in self.ds.engineers if e.id != engineer_id]
            self._persist_engineers()
            self._note("staff", f"Удалён инженер {eng.name} ({engineer_id})")

    def _persist_engineers(self) -> None:
        save_engineers(self.snapshot, self.ds.engineers)
        self.day_start = min(e.shift_start for e in self.ds.engineers)
        self.day_end = max(e.shift_end for e in self.ds.engineers)
        # План построен на прежнем составе — он больше не отражает справочник.
        self.staff_changed = self.plan is not None
        self.version += 1

    def _validate_engineer(self, data: dict, engineer_id: str | None) -> Engineer:
        """Проверить данные до записи и вернуть готовую доменную модель.

        Отдельная проверка на координаты: дом за границами выгруженной
        дорожной сети привяжется к ближайшей вершине графа за десятки
        километров, и маршруты поедут в никуда.
        """
        def fail(msg: str):
            raise ValueError(msg)

        code = (engineer_id or str(data.get("id") or "")).strip().upper()
        if not code:
            used = {e.id for e in self.ds.engineers}
            n = 1
            while f"ENG-{n:02d}" in used:
                n += 1
            code = f"ENG-{n:02d}"

        name = str(data.get("name", "")).strip()
        if len(name) < 3:
            fail("Укажите ФИО инженера")

        skills = {str(k): int(v) for k, v in (data.get("skills") or {}).items()}
        if not skills:
            fail("Нужна хотя бы одна специализация")
        for spec, level in skills.items():
            if spec not in self.ds.specializations:
                fail(f"Неизвестная специализация: {spec}")
            if not 1 <= level <= 4:
                fail(f"Уровень по «{self.ds.specializations[spec]}» должен быть от 1 до 4")

        try:
            vehicle = parse_transport(str(data.get("vehicle_type", "car")))
        except ValueError:
            fail(f"Неизвестный тип транспорта: {data.get('vehicle_type')}")

        try:
            shift_start = hhmm_to_min(str(data.get("shift_start", "")))
            shift_end = hhmm_to_min(str(data.get("shift_end", "")))
            break_from = hhmm_to_min(str(data.get("break_from", "12:00")))
            break_to = hhmm_to_min(str(data.get("break_to", "15:00")))
        except (ValueError, IndexError):
            fail("Время указывается в формате ЧЧ:ММ")

        if shift_end - shift_start < 120:
            fail("Смена короче двух часов — проверьте время")
        break_min = int(data.get("break_min", 45))
        if break_min < 0 or break_min > 180:
            fail("Обед должен укладываться в 0–180 минут")
        overtime = int(data.get("max_overtime_min", 60))
        if overtime < 0 or overtime > 240:
            fail("Допустимая переработка — от 0 до 240 минут")
        if break_min and not (shift_start <= break_from < break_to <= shift_end):
            fail("Окно обеда должно быть внутри смены")
        if break_min and break_to - break_from < break_min:
            fail("Окно обеда короче самого обеда")

        equipment = [str(q) for q in (data.get("onboard_equipment") or [])]
        unknown = [q for q in equipment if q not in self.ds.equipment]
        if unknown:
            fail(f"Нет такого оборудования: {', '.join(unknown)}")
        heavy = [self.ds.equipment[q].name for q in equipment
                 if self.ds.equipment[q].bulky]
        if heavy and not vehicle.can_carry_bulky:
            fail(f"Габаритное оборудование ({', '.join(heavy)}) "
                 f"нельзя выдать инженеру без автомобиля")

        lat, lon = float(data.get("home_lat", 0)), float(data.get("home_lon", 0))
        south, west, north, east = SERVICE_AREA
        if not (south <= lat <= north and west <= lon <= east):
            fail("Точка дома вне зоны обслуживания — укажите адрес в пределах города")

        return Engineer(
            id=code, name=name, skills=skills,
            shift_start=shift_start, shift_end=shift_end,
            break_from=break_from, break_to=break_to, break_min=break_min,
            vehicle_type=vehicle, home_lat=lat, home_lon=lon,
            home_address=str(data.get("home_address", "")).strip() or "адрес не указан",
            onboard_equipment=set(equipment),
            max_overtime_min=overtime,
        )

    # -- ручное вмешательство диспетчера ----------------------------------

    def set_pin(self, job_id: str, engineer_id: str,
                time_limit_s: int | None = None) -> dict:
        """Закрепить заявку за инженером и пересчитать остальное.

        Допустимость проверяется до расчёта: если у инженера нет квалификации,
        оборудования или машины под габарит, отказ приходит с объяснением, а не
        молча испорченным планом.
        """
        with self._lock:
            if self.plan is None:
                raise RuntimeError("План ещё не построен")
            allowed = self.plan.candidates.get(job_id)
            if allowed is None:
                raise ValueError(f"Заявки {job_id} нет в текущем расчёте")
            if engineer_id not in allowed:
                who = self.ds.engineer(engineer_id).name
                reason = "не проходит по квалификации, оборудованию или транспорту"
                wn = self.why_not(job_id)
                if wn:
                    hit = next((b for b in wn.blockers
                                if b.engineer_id == engineer_id), None)
                    if hit:
                        reason = f"{hit.reason} — {hit.detail}"
                raise ValueError(f"{who} не может взять {job_id}: {reason}")

            before = dict(self.plan.kpi)
            self.pins[job_id] = engineer_id
            self._resolve(time_limit_s)
            self._note("pin",
                       f"Заявка {job_id} закреплена диспетчером за "
                       f"исполнителем: {self.ds.engineer(engineer_id).name}")
            return self._cost_of_change(before)

    def clear_pin(self, job_id: str, time_limit_s: int | None = None) -> dict:
        with self._lock:
            if job_id not in self.pins:
                raise ValueError(f"{job_id} не закреплена")
            before = dict(self.plan.kpi) if self.plan else {}
            del self.pins[job_id]
            self._resolve(time_limit_s)
            self._note("pin", f"Закрепление заявки {job_id} снято")
            return self._cost_of_change(before)

    def _resolve(self, time_limit_s: int | None) -> None:
        """Пересчитать в том режиме, в котором сейчас находится день.

        Лимит времени по умолчанию берётся тот же, с каким считался исходный
        план. Иначе «цена закрепления» окажется враньём: часть разницы будет
        не следствием решения диспетчера, а просто более коротким поиском.
        """
        if self.now <= self.day_start and not self.completed:
            self.plan = solve(
                self.ds, self.morning_jobs,
                weights=Weights.preset(self.preset), provider=self.provider,
                time_limit_s=time_limit_s or self.time_limit, pins=self.pins)
            attach_geometry(self.plan, self.ds, self.provider)
            self.morning_plan = self.plan
        else:
            st = state_at(self.ds.events, self.now, self.completed)
            self.plan, self.diff = replan(
                self.ds, self.plan, st, weights=Weights(stability=200),
                provider=self.provider, time_limit_s=time_limit_s or 5,
                pins=self.pins)
            attach_geometry(self.plan, self.ds, self.provider)
        self.version += 1

    def _cost_of_change(self, before: dict) -> dict:
        """Во что обошлось решение диспетчера — считаем и показываем честно."""
        after = self.plan.kpi if self.plan else {}
        return {
            "travel_delta_min": after.get("travel_min", 0) - before.get("travel_min", 0),
            "assigned_delta": after.get("jobs_assigned", 0) - before.get("jobs_assigned", 0),
            "sla_delta": after.get("sla_violations", 0) - before.get("sla_violations", 0),
            "pins": dict(self.pins),
        }

    # -- объяснения -------------------------------------------------------

    def explain(self, job_id: str) -> Explanation | None:
        with self._lock:
            if self.plan is None:
                return None
            return why_this(self.ds, self.plan, self.ds.job(job_id), self.provider)

    def explain_route(self, engineer_id: str) -> RouteExplanation | None:
        with self._lock:
            if self.plan is None:
                return None
            return explain_route(self.ds, self.plan, engineer_id)

    def why_not(self, job_id: str) -> WhyNot | None:
        with self._lock:
            if self.plan is None:
                return None
            return why_not(self.ds, self.plan, self.ds.job(job_id), self.provider)

    def unassigned_jobs(self) -> list[Job]:
        with self._lock:
            return [self.ds.job(j) for j in (self.plan.unassigned if self.plan else [])]

    # -- сравнение с ручным планированием ---------------------------------

    def compare_with_manual(self, baseline: str = "tz"
                            ) -> tuple[Plan, Plan, Comparison, Plan | None]:
        """Утренний план против базового варианта и, если есть, против факта.

        baseline: "tz" — первому подходящему по порядку (ТЗ, п. 2.3);
                  "smart" — по срочности окна ближайшему.
        Факт — распределение диспетчера заказчика из контрольного файла;
        у синтетики его нет.
        """
        with self._lock:
            if self.morning_plan is None:
                raise RuntimeError("План ещё не построен")
            common = dict(provider=self.provider, onboard=self.morning_plan.onboard,
                          pickup=self.morning_plan.pickup)
            if baseline == "smart":
                self.baseline = greedy_plan(self.ds, self.morning_jobs, **common)
            else:
                self.baseline = tz_baseline(self.ds, self.morning_jobs, **common)
            fact = control_plan(self.ds, self.morning_jobs, self.provider)
            return (self.baseline, self.morning_plan,
                    compare(self.baseline, self.morning_plan), fact)

    # -- справочники ------------------------------------------------------

    def day_state(self) -> DayState:
        return state_at(self.ds.events, self.now, self.completed)
