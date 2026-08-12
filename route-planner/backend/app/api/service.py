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

import threading
from pathlib import Path

from ..baseline.greedy import Comparison, compare, greedy_plan
from ..domain.loader import load_dataset
from ..domain.models import Job, min_to_hhmm
from ..explain.why_not import WhyNot, why_not
from ..explain.why_this import Explanation, why_this
from ..solver.engine import Plan, Weights, attach_geometry, solve
from ..solver.replan import DayState, PlanDiff, project, replan, state_at
from ..travel.provider import default_provider


class PlanningService:
    def __init__(self, snapshot: str | Path) -> None:
        self.snapshot = Path(snapshot)
        self.provider = default_provider()
        self._lock = threading.RLock()
        self.reset()

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
            self.version += 1
            k = self.plan.kpi
            self._note("build",
                       f"Построен план на день: назначено "
                       f"{k['jobs_assigned']}/{k['jobs_total']}, "
                       f"в пути {k['travel_min']} мин")
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
                       f"Диспетчер закрепил {job_id} за "
                       f"{self.ds.engineer(engineer_id).name}")
            return self._cost_of_change(before)

    def clear_pin(self, job_id: str, time_limit_s: int | None = None) -> dict:
        with self._lock:
            if job_id not in self.pins:
                raise ValueError(f"{job_id} не закреплена")
            before = dict(self.plan.kpi) if self.plan else {}
            del self.pins[job_id]
            self._resolve(time_limit_s)
            self._note("pin", f"Снято закрепление {job_id}")
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

    def why_not(self, job_id: str) -> WhyNot | None:
        with self._lock:
            if self.plan is None:
                return None
            return why_not(self.ds, self.plan, self.ds.job(job_id), self.provider)

    def unassigned_jobs(self) -> list[Job]:
        with self._lock:
            return [self.ds.job(j) for j in (self.plan.unassigned if self.plan else [])]

    # -- сравнение с ручным планированием ---------------------------------

    def compare_with_manual(self, order: str = "edf", pick: str = "nearest"
                            ) -> tuple[Plan, Plan, Comparison]:
        with self._lock:
            if self.morning_plan is None:
                raise RuntimeError("План ещё не построен")
            self.baseline = greedy_plan(
                self.ds, self.morning_jobs, provider=self.provider,
                onboard=self.morning_plan.onboard,
                pickup=self.morning_plan.pickup, order=order, pick=pick)
            return (self.baseline, self.morning_plan,
                    compare(self.baseline, self.morning_plan))

    # -- справочники ------------------------------------------------------

    def day_state(self) -> DayState:
        return state_at(self.ds.events, self.now, self.completed)
