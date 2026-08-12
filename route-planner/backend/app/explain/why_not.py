"""Почему заявка НЕ попала в план.

Кейс просит «показывать причины выбора маршрута». Причину назначения объяснить
легко, и это сделают все. Гораздо ценнее для диспетчера обратное: заявка не
назначена — что конкретно мешает и что надо изменить, чтобы она влезла.

Ответ считается, а не сочиняется. Для каждого инженера проверяются жёсткие
ограничения по порядку, а если все пройдены — честно перебираются позиции
вставки в его текущий маршрут с проверкой, переживут ли сдвиг последующие
визиты. На выходе — первая нарушенная причина по каждому инженеру и сводка.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..domain.models import Dataset, Engineer, Job, min_to_hhmm, service_minutes
from ..solver.engine import Plan, Route
from ..travel.provider import TravelTimeProvider

REASON_SKILL = "недостаточная квалификация"
REASON_VEHICLE = "требуется автотранспорт"
REASON_EQUIPMENT = "нет необходимого оборудования"
REASON_WINDOW = "окно доступа недостижимо"
REASON_FULL = "смена заполнена"
REASON_SHIFT = "выходит за пределы смены"


@dataclass(slots=True)
class Blocker:
    engineer_id: str
    engineer_name: str
    reason: str
    detail: str


@dataclass(slots=True)
class WhyNot:
    job_id: str
    blockers: list[Blocker] = field(default_factory=list)
    #: Инженеры, которым заявку можно отдать, подвинув остальные визиты.
    feasible_with_shift: list[tuple[str, str]] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(b.reason for b in self.blockers))

    @property
    def qualified(self) -> list[Blocker]:
        """Те, кто проходит по квалификации, но всё равно не может взять заявку.

        Именно они и интересны. «Нет допуска у 14 из 18» — это фон: у половины
        службы другая специальность, и это не новость. Новость — что мешает
        оставшимся четверым.
        """
        return [b for b in self.blockers if b.reason != REASON_SKILL]

    def verdict(self) -> str:
        if self.feasible_with_shift:
            who = "; ".join(f"{name} — {how}"
                            for name, how in self.feasible_with_shift[:2])
            return f"можно назначить, подвинув другие визиты: {who}"
        qualified = self.qualified
        if not qualified:
            return (f"ни один из {len(self.blockers)} инженеров не имеет "
                    f"нужной квалификации")
        parts = [f"{reason} — {n}" for reason, n in
                 Counter(b.reason for b in qualified).most_common()]
        return (f"по квалификации подходят {len(qualified)}, "
                f"но: {', '.join(parts)}")

    def text(self, limit: int = 6) -> str:
        lines = [f"{self.job_id}: {self.verdict()}"]
        for b in self.qualified[:limit]:
            lines.append(f"    {b.engineer_id} {b.engineer_name}: "
                         f"{b.reason} — {b.detail}")
        if len(self.qualified) > limit:
            lines.append(f"    … ещё {len(self.qualified) - limit}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Проверка временной выполнимости через вставку в существующий маршрут
# --------------------------------------------------------------------------

def _latest_start(job: Job, svc: int) -> int:
    """Позже этого момента визит начинать нельзя."""
    return job.tw_end - svc if job.tw_hard else job.tw_end


def _survives_push(route: Route, from_idx: int, push: int, ds: Dataset,
                   eng: Engineer) -> bool:
    """Выдержат ли последующие визиты сдвиг на `push` минут.

    Мягкие окна сдвиг переживают (это штраф, а не запрет), жёсткие и конец
    смены — нет.
    """
    for stop in route.stops[from_idx:]:
        if stop.kind == "end":
            if stop.arrival + push > eng.shift_end + eng.max_overtime_min:
                return False
            continue
        if stop.kind != "job" or not stop.job_id:
            continue
        other = ds.job(stop.job_id)
        if other.tw_hard:
            svc = service_minutes(other, eng)
            if stop.service_start + push > _latest_start(other, svc):
                return False
    return True


@dataclass(slots=True)
class Insertion:
    """Результат попытки вставить заявку в маршрут инженера."""

    feasible: bool
    reason: str = ""
    detail: str = ""
    start: int | None = None
    extra_travel: int = 0     # насколько удлиняется дорога
    push_min: int = 0         # на сколько сдвигаются последующие визиты


def try_insert(ds: Dataset, route: Route, eng: Engineer, job: Job,
               provider: TravelTimeProvider) -> Insertion:
    """Влезает ли заявка в маршрут инженера, и если нет — почему.

    Используется и для «почему не назначено», и для разбора альтернатив в
    «почему именно этот инженер»: вопрос-то один и тот же.
    """
    svc = service_minutes(job, eng)
    latest = _latest_start(job, svc)
    earliest: int | None = None
    best: Insertion | None = None

    for i in range(len(route.stops) - 1):
        a, b = route.stops[i], route.stops[i + 1]
        depart = a.service_end
        to_job = provider.minutes((a.lat, a.lon), (job.lat, job.lon),
                                  eng.vehicle_type, depart)
        start = max(depart + to_job, job.tw_start)
        if earliest is None or start < earliest:
            earliest = start
        if start > latest:
            continue                       # в этой точке маршрута уже поздно

        finish = start + svc
        back = provider.minutes((job.lat, job.lon), (b.lat, b.lon),
                                eng.vehicle_type, finish)
        direct = provider.minutes((a.lat, a.lon), (b.lat, b.lon),
                                  eng.vehicle_type, depart)
        extra = to_job + back - direct
        push = (finish + back) - b.arrival

        if push > 0 and not _survives_push(route, i + 1, push, ds, eng):
            continue

        # Формулировка зависит от того, есть ли кого двигать. У инженера без
        # заявок «остальные визиты сдвигаются на 428 минут» — бессмыслица:
        # сдвигать нечего, он просто поедет специально.
        following = any(s.kind == "job" for s in route.stops[i + 1:])
        if not any(s.kind == "job" for s in route.stops):
            detail = (f"смена свободна, выезд отдельным рейсом: "
                      f"начало в {min_to_hhmm(start)}")
            push = 0
        elif push <= 0:
            detail = f"в графике есть окно, начало в {min_to_hhmm(start)}"
        elif following:
            detail = (f"начало в {min_to_hhmm(start)}, последующие визиты "
                      f"смещаются на {push} мин")
        else:
            detail = (f"начало в {min_to_hhmm(start)}, возвращение "
                      f"на {push} мин позже")
        candidate = Insertion(True, "", detail, start, max(0, extra), max(0, push))
        # из допустимых позиций берём ту, что меньше всего удлиняет маршрут
        if best is None or candidate.extra_travel < best.extra_travel:
            best = candidate

    if best is not None:
        return best

    # Ни одна позиция не подошла. Различаем два принципиально разных случая:
    # инженер физически не успевает к закрытию окна — или успевает, но тогда
    # рассыпается остальной маршрут.
    if earliest is None:
        return Insertion(False, REASON_FULL, "маршрут не допускает включения")
    if earliest > latest:
        return Insertion(False, REASON_WINDOW,
                         f"освобождается не ранее {min_to_hhmm(earliest)}, "
                         f"работы должны начаться до {min_to_hhmm(latest)}")
    return Insertion(False, REASON_FULL,
                     f"может приступить в {min_to_hhmm(earliest)}, но включение "
                     f"нарушает жёсткие окна других визитов либо выводит за смену")


# --------------------------------------------------------------------------
# Основная функция
# --------------------------------------------------------------------------

def why_not(ds: Dataset, plan: Plan, job: Job,
            provider: TravelTimeProvider) -> WhyNot:
    """Разобрать по каждому инженеру, что мешает взять заявку."""
    result = WhyNot(job_id=job.id)
    onboard = plan.onboard or {}
    needs_car = ds.needs_vehicle(job)

    for route in plan.routes:
        eng = ds.engineer(route.engineer_id)

        level = eng.level_in(job.specialization)
        if level < job.min_level:
            spec = ds.specializations.get(job.specialization, job.specialization)
            result.blockers.append(Blocker(
                eng.id, eng.name, REASON_SKILL,
                f"{spec}: уровень {level or 'отсутствует'}, "
                f"требуется {job.min_level}"))
            continue

        if needs_car and not eng.vehicle_type.can_carry_bulky:
            bulky = [ds.equipment[q].name for q in job.required_equipment
                     if ds.equipment[q].bulky]
            result.blockers.append(Blocker(
                eng.id, eng.name, REASON_VEHICLE,
                f"перевозка «{', '.join(bulky)}» требует автотранспорта"))
            continue

        missing = set(job.required_equipment) - set(onboard.get(eng.id, ()))
        if missing:
            names = [ds.equipment[q].name for q in sorted(missing)]
            result.blockers.append(Blocker(
                eng.id, eng.name, REASON_EQUIPMENT,
                f"не закреплено: {', '.join(names)}"))
            continue

        ins = try_insert(ds, route, eng, job, provider)
        if ins.feasible:
            result.feasible_with_shift.append((eng.name, ins.detail))
        else:
            result.blockers.append(Blocker(eng.id, eng.name, ins.reason, ins.detail))

    return result


def why_not_report(ds: Dataset, plan: Plan,
                   provider: TravelTimeProvider) -> str:
    """Свод по всем неназначенным заявкам плана."""
    if not plan.unassigned:
        return "Все заявки назначены."

    lines = [f"Не назначено {len(plan.unassigned)} заявок — разбор причин:\n"]
    binding: Counter[str] = Counter()      # что мешает тем, кто подходит
    by_spec: Counter[str] = Counter()

    for job_id in plan.unassigned:
        job = ds.job(job_id)
        wn = why_not(ds, plan, job, provider)
        wt = ds.work_types[job.work_type_id].name
        lines.append(f"{job.priority.value} {job.id} — {wt}, {job.district}, "
                     f"окно {min_to_hhmm(job.tw_start)}-{min_to_hhmm(job.tw_end)}"
                     f"{' (жёсткое)' if job.tw_hard else ''}")
        lines.append("  " + wn.verdict())
        for b in wn.qualified[:3]:
            lines.append(f"    {b.engineer_name}: {b.detail}")
        lines.append("")

        if wn.qualified:
            binding.update(b.reason for b in wn.qualified)
        else:
            binding[REASON_SKILL] += 1
        by_spec[job.specialization] += 1

    lines.append("Что на самом деле упирается (по инженерам, прошедшим допуск):")
    for reason, count in binding.most_common():
        lines.append(f"  {count:5d}  {reason}")
    lines.append("\nНеназначенные по специализациям:")
    for spec, count in by_spec.most_common():
        lines.append(f"  {count:5d}  {ds.specializations.get(spec, spec)}")
    return "\n".join(lines)
