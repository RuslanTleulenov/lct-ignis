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
            f"выполнит за {actual} мин вместо {nominal}")
    else:
        exp.choice.append(f"{spec}: уровень {level}, ровно как требует "
                          f"работа сложности {job.complexity}")

    # --- оборудование -----------------------------------------------------
    if job.required_equipment:
        rare = [ds.equipment[q].name for q in job.required_equipment
                if ds.equipment[q].is_rare]
        where = (f", забрал утром на складе {route.pickup_warehouse}"
                 if route.pickup_warehouse else ", было на руках")
        kit = ", ".join(ds.equipment[q].name for q in job.required_equipment)
        exp.choice.append(f"оборудование{where}: {kit}"
                          + (f" (дефицитное: {', '.join(rare)})" if rare else ""))

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
            f"по пути: крюк {max(0, detour)} мин относительно маршрута без неё "
            f"({stop.travel_min_from_prev} мин от предыдущего визита)")
    else:
        exp.choice.append(f"{stop.travel_min_from_prev} мин от предыдущей точки")

    candidates = plan.candidates.get(job.id) or [
        ds.engineers[v].id for v in eligible_engineers(ds, job, plan.onboard or {})]
    if len(candidates) == 1:
        exp.choice.append("единственный возможный исполнитель в этот день")

    # --- время ------------------------------------------------------------
    hard = " (жёсткое)" if job.tw_hard else ""
    exp.timing.append(f"окно клиента {min_to_hhmm(job.tw_start)}–"
                      f"{min_to_hhmm(job.tw_end)}{hard}, "
                      f"визит {min_to_hhmm(stop.service_start)}–"
                      f"{min_to_hhmm(stop.service_end)}")
    if route.lunch_start is not None and route.lunch_start == stop.arrival:
        exp.timing.append(f"приехал в {min_to_hhmm(stop.arrival)}, "
                          f"перед визитом обед {route.lunch_min} мин")
    if stop.wait_min:
        exp.timing.append(f"ждал {stop.wait_min} мин: "
                          f"{'окно ещё не открылось' if stop.arrival < job.tw_start else 'резерв в расписании'}")
    if stop.sla_late_min:
        exp.timing.append(f"SLA {min_to_hhmm(job.sla_deadline)} нарушен на "
                          f"{stop.sla_late_min} мин — заявку пришлось подвинуть "
                          f"ради более срочных")
    else:
        slack = job.sla_deadline - stop.service_start
        exp.timing.append(f"до дедлайна SLA {min_to_hhmm(job.sla_deadline)} "
                          f"оставалось {slack} мин")

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
                f"тоже мог бы — {ins.detail}; вставка в его маршрут добавит "
                f"{ins.extra_travel} мин пути", ins.extra_travel))
        else:
            exp.alternatives.append(Alternative(
                other.id, other.name, False, ins.detail))

    exp.alternatives.sort(key=lambda a: (not a.possible, a.extra_travel))
    return exp
