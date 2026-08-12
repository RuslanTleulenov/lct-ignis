"""Почему заявка досталась именно этому инженеру и именно в это время.

Второй половина объяснимости. В отличие от `why_not`, здесь всё считается по
уже готовому плану: квалификация, оборудование, крюк относительно соседних
визитов — и главное, что было бы, отдай мы заявку каждому из остальных
подходящих. Последнее и есть настоящий ответ на вопрос «почему он»: не
«потому что подошёл», а «потому что все альтернативы хуже, вот на сколько».
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.models import Dataset, Job, min_to_hhmm, service_minutes
from ..solver.engine import Plan, Route, Stop, eligible_engineers
from ..travel.provider import TravelTimeProvider
from .why_not import try_insert


@dataclass(slots=True)
class Alternative:
    engineer_id: str
    engineer_name: str
    possible: bool
    detail: str
    extra_travel: int = 0


@dataclass(slots=True)
class Explanation:
    job_id: str
    engineer_id: str
    engineer_name: str
    choice: list[str] = field(default_factory=list)       # почему этот инженер
    timing: list[str] = field(default_factory=list)       # почему это время
    alternatives: list[Alternative] = field(default_factory=list)

    def text(self) -> str:
        lines = [f"{self.job_id} → {self.engineer_id} {self.engineer_name}"]
        lines.append("  Почему он:")
        lines += [f"    • {r}" for r in self.choice]
        lines.append("  Почему в это время:")
        lines += [f"    • {r}" for r in self.timing]
        if self.alternatives:
            lines.append("  Альтернативы:")
            for a in self.alternatives:
                mark = "○" if a.possible else "✗"
                lines.append(f"    {mark} {a.engineer_name}: {a.detail}")
        else:
            lines.append("  Альтернатив нет: других допустимых исполнителей в плане нет.")
        return "\n".join(lines)


def _find(plan: Plan, job_id: str) -> tuple[Route, int] | None:
    for route in plan.routes:
        for i, stop in enumerate(route.stops):
            if stop.kind == "job" and stop.job_id == job_id:
                return route, i
    return None


def why_this(ds: Dataset, plan: Plan, job: Job,
             provider: TravelTimeProvider) -> Explanation | None:
    """Разобрать назначение. None, если заявки в плане нет."""
    found = _find(plan, job.id)
    if found is None:
        return None
    route, idx = found
    eng = ds.engineer(route.engineer_id)
    stop: Stop = route.stops[idx]
    exp = Explanation(job.id, eng.id, eng.name)

    # --- квалификация -----------------------------------------------------
    level = eng.level_in(job.specialization)
    spec = ds.specializations.get(job.specialization, job.specialization)
    if level > job.min_level:
        nominal = job.duration_min
        actual = service_minutes(job, eng)
        exp.choice.append(
            f"{spec}: уровень {level} при требуемом {job.min_level} — "
            f"норматив сокращается с {nominal} до {actual} мин")
    else:
        exp.choice.append(f"{spec}: уровень {level} — соответствует "
                          f"категории сложности {job.complexity}")

    # --- оборудование -----------------------------------------------------
    if job.required_equipment:
        rare = [ds.equipment[q].name for q in job.required_equipment
                if ds.equipment[q].is_rare]
        where = (f"получено на складе {route.pickup_warehouse}"
                 if route.pickup_warehouse else "закреплено за инженером")
        kit = ", ".join(ds.equipment[q].name for q in job.required_equipment)
        exp.choice.append(f"оборудование {where}: {kit}"
                          + (f" (ограниченный парк: {', '.join(rare)})" if rare else ""))

    # --- география --------------------------------------------------------
    prev_stop = route.stops[idx - 1]
    next_stop = route.stops[idx + 1] if idx + 1 < len(route.stops) else None
    if next_stop is not None:
        direct = provider.minutes((prev_stop.lat, prev_stop.lon),
                                  (next_stop.lat, next_stop.lon),
                                  eng.vehicle_type, prev_stop.service_end)
        detour = (stop.travel_min_from_prev + next_stop.travel_min_from_prev
                  - direct)
        exp.choice.append(
            f"расположение по маршруту: отклонение {max(0, detour)} мин "
            f"относительно маршрута без этой заявки, "
            f"{stop.travel_min_from_prev} мин от предыдущего объекта")
    else:
        exp.choice.append(f"{stop.travel_min_from_prev} мин от предыдущего объекта")

    candidates = plan.candidates.get(job.id) or [
        ds.engineers[v].id for v in eligible_engineers(ds, job, plan.onboard or {})]
    if len(candidates) == 1:
        exp.choice.append("единственный исполнитель, допущенный к этим работам")

    # --- время ------------------------------------------------------------
    hard = " (жёсткое)" if job.tw_hard else ""
    exp.timing.append(f"окно доступа {min_to_hhmm(job.tw_start)}–"
                      f"{min_to_hhmm(job.tw_end)}{hard}, работы запланированы "
                      f"на {min_to_hhmm(stop.service_start)}–"
                      f"{min_to_hhmm(stop.service_end)}")
    if route.lunch_start is not None and route.lunch_start == stop.arrival:
        exp.timing.append(f"прибытие в {min_to_hhmm(stop.arrival)}, "
                          f"далее обеденный перерыв {route.lunch_min} мин")
    if stop.wait_min:
        exp.timing.append(
            f"ожидание {stop.wait_min} мин: "
            + ('окно доступа ещё не открыто' if stop.arrival < job.tw_start
               else 'резерв в графике'))
    if stop.sla_late_min:
        exp.timing.append(f"срок по SLA {min_to_hhmm(job.sla_deadline)} превышен "
                          f"на {stop.sla_late_min} мин: заявка смещена в пользу "
                          f"более срочных")
    else:
        slack = job.sla_deadline - stop.service_start
        exp.timing.append(f"запас до срока по SLA {min_to_hhmm(job.sla_deadline)} "
                          f"— {slack} мин")

    # --- альтернативы -----------------------------------------------------
    for route_other in plan.routes:
        if route_other.engineer_id == eng.id:
            continue
        if route_other.engineer_id not in candidates:
            continue
        other = ds.engineer(route_other.engineer_id)
        ins = try_insert(ds, route_other, other, job, provider)
        if ins.feasible:
            # Осторожно с формулировкой: это стоимость ВСТАВКИ в его нынешний
            # маршрут, а не итоговая цена решения. После полного пересчёта
            # сдвинутся и другие визиты, и разница обычно оказывается больше.
            # Считать настоящую цену пришлось бы полным прогоном солвера на
            # каждую альтернативу — для панели, которая открывается по клику,
            # это неприемлемо долго.
            exp.alternatives.append(Alternative(
                other.id, other.name, True,
                f"допущен и может принять — {ins.detail}; включение в его "
                f"маршрут добавит {ins.extra_travel} мин пути", ins.extra_travel))
        else:
            exp.alternatives.append(Alternative(
                other.id, other.name, False, ins.detail))

    exp.alternatives.sort(key=lambda a: (not a.possible, a.extra_travel))
    return exp
