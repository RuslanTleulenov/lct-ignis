"""Модели предметной области.

Всё время внутри системы — минуты от полуночи (int). Строки вида "08:30"
живут только на границах: при чтении датасета и при отдаче в API. Это избавляет
от возни с часовыми поясами и делает арифметику в солвере тривиальной.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# --------------------------------------------------------------------------
# Время
# --------------------------------------------------------------------------

def hhmm_to_min(value: str) -> int:
    """"08:30" -> 510."""
    h, m = value.strip().split(":")
    return int(h) * 60 + int(m)


def min_to_hhmm(value: int) -> str:
    """510 -> "08:30". Переработка за полночь не предполагается, но и не ломается."""
    return f"{value // 60:02d}:{value % 60:02d}"


# --------------------------------------------------------------------------
# Перечисления
# --------------------------------------------------------------------------

class TransportMode(str, Enum):
    """Справочник ТЗ «Билайн Бизнес», п. 2.4.1: четыре типа, один на инженера."""

    CAR = "car"             # Автомобиль
    FOOT = "foot"           # Пешеход
    BIKE = "bike"           # Велосипед
    TRANSIT = "transit"     # Общественный транспорт

    @property
    def label(self) -> str:
        return TRANSPORT_LABEL[self]

    @property
    def can_carry_bulky(self) -> bool:
        """Габаритное оборудование увезёт только автомобиль."""
        return self is TransportMode.CAR


TRANSPORT_LABEL = {
    TransportMode.CAR: "Автомобиль",
    TransportMode.FOOT: "Пешеход",
    TransportMode.BIKE: "Велосипед",
    TransportMode.TRANSIT: "Общественный транспорт",
}


class Priority(str, Enum):
    """Справочник ТЗ: два уровня. Срочная имеет приоритет при перепланировании."""

    NORMAL = "normal"   # Обычная
    URGENT = "urgent"   # Срочная

    @property
    def label(self) -> str:
        return "Срочная" if self is Priority.URGENT else "Обычная"

    @property
    def drop_penalty(self) -> int:
        """Штраф за неназначенную заявку, в «минутах» целевой функции.

        Порядок величин важнее абсолютных значений: срочную солвер не бросит
        никогда, кроме случая, когда её физически невозможно выполнить.
        """
        return 200_000 if self is Priority.URGENT else 20_000

    @property
    def sla_penalty_per_min(self) -> int:
        """Во сколько обходится каждая минута просрочки SLA."""
        return 300 if self is Priority.URGENT else 25


# --------------------------------------------------------------------------
# Справочники
# --------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Equipment:
    id: str
    name: str
    bulky: bool
    units: int
    stock: str               # "all" — есть на любом складе, иначе id склада

    def available_at(self, warehouse_id: str) -> bool:
        return self.stock == "all" or self.stock == warehouse_id

    @property
    def is_rare(self) -> bool:
        """Редкий прибор хранится централизованно и является узким местом."""
        return self.stock != "all"


@dataclass(frozen=True, slots=True)
class WorkType:
    id: str
    name: str
    specialization: str
    min_level: int
    base_duration_min: int
    equipment: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Warehouse:
    id: str
    name: str
    lat: float
    lon: float
    address: str
    open_from: int
    open_to: int


# --------------------------------------------------------------------------
# Инженеры и заявки
# --------------------------------------------------------------------------

@dataclass(slots=True)
class Engineer:
    id: str
    name: str
    skills: dict[str, int]           # специализация -> уровень 1..4
    shift_start: int
    shift_end: int
    break_from: int                  # окно, внутри которого должен уместиться обед
    break_to: int
    break_min: int
    vehicle_type: TransportMode
    home_lat: float
    home_lon: float
    home_address: str
    onboard_equipment: set[str]
    max_overtime_min: int = 0

    # Заполняется утренним распределением дефицитного инструмента (см. solver).
    pickup_warehouse: str | None = None

    # Состояние на момент перепланирования: где инженер сейчас и когда
    # освободится. У утреннего планирования пусто — маршрут стартует из дома.
    current_lat: float | None = None
    current_lon: float | None = None
    available_from: int | None = None

    @property
    def home(self) -> tuple[float, float]:
        return self.home_lat, self.home_lon

    @property
    def current_position(self) -> tuple[float, float] | None:
        if self.current_lat is None or self.current_lon is None:
            return None
        return self.current_lat, self.current_lon

    @property
    def starts_at(self) -> int:
        return self.available_from if self.available_from is not None else self.shift_start

    def level_in(self, specialization: str) -> int:
        return self.skills.get(specialization, 0)


@dataclass(slots=True)
class Job:
    id: str
    external_id: str
    customer: str
    district: str
    address: str
    lat: float
    lon: float
    work_type_id: str
    specialization: str
    min_level: int
    complexity: int
    duration_min: int
    required_equipment: tuple[str, ...]
    tw_start: int
    tw_end: int
    tw_hard: bool
    priority: Priority
    sla_deadline: int
    created_at_min: int              # минуты от полуночи дня плана; может быть < 0
    known_at_day_start: bool
    status: str = "new"
    contact_phone: str = ""
    #: Требуемый тип транспорта из ТЗ: указывается только при наличии
    #: ограничения. Габаритное оборудование ставит его в «автомобиль» само.
    required_transport: TransportMode | None = None
    #: Точность координат: house | house~ | street | district. Всё, кроме
    #: house, интерфейс показывает как приблизительное.
    geo_precision: str = "house"
    #: Как было на самом деле — из контрольного распределения заказчика:
    #: кому диспетчер отдал заявку и чем она закончилась. Нужно только для
    #: сравнения «план против факта»; у синтетики пусто.
    control_engineer: str | None = None
    control_status: str = ""

    @property
    def location(self) -> tuple[float, float]:
        return self.lat, self.lon


@dataclass(frozen=True, slots=True)
class DayEvent:
    at: int
    type: str
    payload: str
    comment: str


# --------------------------------------------------------------------------
# Датасет целиком
# --------------------------------------------------------------------------

@dataclass(slots=True)
class Dataset:
    date: str
    specializations: dict[str, str]
    work_types: dict[str, WorkType]
    equipment: dict[str, Equipment]
    warehouses: dict[str, Warehouse]
    engineers: list[Engineer]
    jobs: list[Job]
    events: list[DayEvent] = field(default_factory=list)
    #: Как называть набор в интерфейсе: «Билайн Бизнес — Восток, 17.08.2026».
    title: str = ""
    #: Обязательный MVP по ТЗ: маршрут начинается в стартовой точке, а
    #: возвращаться после последней заявки не требуется. Синтетический набор
    #: может включить возврат домой — тогда пробег считается с ним.
    return_to_start: bool = False

    def job(self, job_id: str) -> Job:
        return next(j for j in self.jobs if j.id == job_id)

    def engineer(self, engineer_id: str) -> Engineer:
        return next(e for e in self.engineers if e.id == engineer_id)

    def required_transport(self, job: Job) -> TransportMode | None:
        """Какой транспорт обязателен: указанный в заявке либо автомобиль,
        если хоть один требуемый инструмент габаритный."""
        if job.required_transport is not None:
            return job.required_transport
        if any(self.equipment[q].bulky for q in job.required_equipment):
            return TransportMode.CAR
        return None

    def needs_vehicle(self, job: Job) -> bool:
        return self.required_transport(job) is TransportMode.CAR

    @property
    def uses_levels(self) -> bool:
        """Есть ли в наборе уровни квалификации выше первого — иначе интерфейс
        их не показывает: справочник ТЗ уровней не знает."""
        return any(w.min_level > 1 for w in self.work_types.values())

    @property
    def uses_equipment(self) -> bool:
        return bool(self.equipment)


# --------------------------------------------------------------------------
# Длительность визита
# --------------------------------------------------------------------------

#: Насколько быстрее работает инженер, чей уровень выше требуемого.
#: Это и есть «учёт сложности проводимых работ» из названия кейса: сложная
#: заявка у слабого исполнителя съедает больше дня, поэтому солверу иногда
#: выгодно отправить старшего дальше по городу.
SKILL_SPEEDUP = {0: 1.00, 1: 0.90, 2: 0.85, 3: 0.80}


def nominal_duration(base_duration_min: int, complexity: int) -> int:
    """Норматив визита по типу работ и категории сложности, кратно 5 минутам.

    Та же формула, что в data/generate.py: генератор намеренно не зависит от
    пакета `app`, поэтому копия там оставлена сознательно. Менять — в обоих
    местах, иначе заведённая вручную заявка будет считаться иначе, чем
    сгенерированная.
    """
    return int(round(base_duration_min * (0.85 + 0.15 * complexity) / 5.0)) * 5


def service_minutes(job: Job, engineer: Engineer) -> int:
    """Сколько времени займёт визит именно у этого инженера."""
    surplus = max(0, engineer.level_in(job.specialization) - job.min_level)
    factor = SKILL_SPEEDUP.get(surplus, 0.80)
    return max(5, round(job.duration_min * factor))
