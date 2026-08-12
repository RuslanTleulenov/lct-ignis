"""Время в пути между точками.

Единственное место, где система знает про географию. Наружу торчит один метод
`matrix()`, поэтому подменить приближение на реальные времена по УДС можно, не
трогая солвер.

Реализации:
    HaversineProvider — работает всегда, без сети и без Docker. Единственный
        вариант, гарантированно доступный на защите.
    OSRMProvider      — реальные времена по дорогам, если появится доступ
        к OSRM (`/table`). Заглушка с понятной ошибкой, пока адрес не задан.
    CachedProvider    — обёртка с кешем: один и тот же набор точек при
        перепланировании не пересчитывается заново.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Protocol, Sequence

from ..domain.models import TransportMode

Point = tuple[float, float]
Matrix = list[list[int]]

EARTH_R_KM = 6371.0

#: Средняя скорость по городу, км/ч. Для «пешком + общественный транспорт»
#: это эффективная скорость двери-в-дверь с учётом ожидания и пересадок.
SPEED_KMH: dict[TransportMode, float] = {
    TransportMode.CAR: 27.0,
    TransportMode.VAN: 23.0,
    TransportMode.WALK_TRANSIT: 13.0,
}

#: Коэффициент извилистости: реальный путь по улицам длиннее прямой линии.
#: 1.35 — типичное значение для радиально-кольцевой сети Москвы.
DETOUR_FACTOR = 1.35

#: Профиль пробок по часам. Общественный транспорт от них почти не зависит —
#: см. traffic_factor().
TRAFFIC_BY_HOUR: dict[int, float] = {
    0: 0.75, 1: 0.75, 2: 0.75, 3: 0.75, 4: 0.80, 5: 0.85,
    6: 0.95, 7: 1.25, 8: 1.55, 9: 1.45, 10: 1.15, 11: 1.10,
    12: 1.10, 13: 1.10, 14: 1.15, 15: 1.25, 16: 1.40, 17: 1.60,
    18: 1.65, 19: 1.40, 20: 1.15, 21: 1.00, 22: 0.90, 23: 0.80,
}


def traffic_factor(mode: TransportMode, minute_of_day: int) -> float:
    hour = max(0, min(23, minute_of_day // 60))
    factor = TRAFFIC_BY_HOUR[hour]
    if mode is TransportMode.WALK_TRANSIT:
        # метро в пробке не стоит: сглаживаем профиль до четверти эффекта
        return 1.0 + (factor - 1.0) * 0.25
    return factor


def haversine_km(a: Point, b: Point) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_R_KM * math.asin(math.sqrt(h))


class TravelTimeProvider(Protocol):
    def matrix(self, points: Sequence[Point], mode: TransportMode,
               departure_min: int) -> Matrix:
        """Матрица времени в пути в минутах (целых)."""
        ...

    def minutes(self, a: Point, b: Point, mode: TransportMode,
                departure_min: int) -> int:
        """Время в пути между двумя точками — для точечных проверок."""
        ...

    def distance_km(self, a: Point, b: Point,
                    mode: TransportMode = TransportMode.CAR) -> float:
        ...


class HaversineProvider:
    """Геодезическое расстояние × коэффициент извилистости ÷ скорость × пробки.

    Компромисс осознанный: абсолютные времена приблизительны, но относительные
    (что ближе, что дальше) устойчивы, а именно на них и работает оптимизация.
    Зато провайдер не может отвалиться в момент демонстрации.
    """

    def __init__(self, detour: float = DETOUR_FACTOR,
                 speeds: dict[TransportMode, float] | None = None) -> None:
        self.detour = detour
        self.speeds = speeds or dict(SPEED_KMH)

    def distance_km(self, a: Point, b: Point,
                    mode: TransportMode = TransportMode.CAR) -> float:
        return haversine_km(a, b) * self.detour

    def minutes(self, a: Point, b: Point, mode: TransportMode,
                departure_min: int) -> int:
        if a == b:
            return 0
        km = self.distance_km(a, b)
        speed = self.speeds[mode] / traffic_factor(mode, departure_min)
        return max(1, round(km / speed * 60))

    def matrix(self, points: Sequence[Point], mode: TransportMode,
               departure_min: int) -> Matrix:
        n = len(points)
        factor = traffic_factor(mode, departure_min)
        speed_kmh = self.speeds[mode] / factor
        out: Matrix = [[0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                km = haversine_km(points[i], points[j]) * self.detour
                minutes = max(1, round(km / speed_kmh * 60))
                out[i][j] = out[j][i] = minutes
        return out


class OSRMProvider:
    """Реальные времена по дорожной сети через OSRM `/table`.

    Не используется по умолчанию: публичный сервер OSRM ограничивает размер
    матрицы и может быть недоступен, а локальный требует Docker, которого на
    машине нет. Подключается флагом конфигурации, когда доступ появится.
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def distance_km(self, a: Point, b: Point,
                    mode: TransportMode = TransportMode.CAR) -> float:
        return haversine_km(a, b) * DETOUR_FACTOR

    def minutes(self, a: Point, b: Point, mode: TransportMode,
                departure_min: int) -> int:
        raise NotImplementedError("OSRMProvider ещё не подключён")

    def matrix(self, points: Sequence[Point], mode: TransportMode,
               departure_min: int) -> Matrix:
        raise NotImplementedError(
            "OSRMProvider ещё не подключён: нужен доступный OSRM-сервер. "
            "Пока используйте HaversineProvider."
        )


class CachedProvider:
    """Кеш поверх любого провайдера.

    При перепланировании набор точек меняется мало, а матрица 100×100 считается
    заметное время. Ключ — сами точки, режим и час выезда.
    """

    def __init__(self, inner: TravelTimeProvider) -> None:
        self.inner = inner
        self._cache: dict[tuple, Matrix] = {}
        self.hits = 0
        self.misses = 0

    def distance_km(self, a: Point, b: Point,
                    mode: TransportMode = TransportMode.CAR) -> float:
        return self.inner.distance_km(a, b, mode)

    def minutes(self, a: Point, b: Point, mode: TransportMode,
                departure_min: int) -> int:
        return self.inner.minutes(a, b, mode, departure_min)

    def matrix(self, points: Sequence[Point], mode: TransportMode,
               departure_min: int) -> Matrix:
        key = (tuple(points), mode, departure_min // 60)
        cached = self._cache.get(key)
        if cached is not None:
            self.hits += 1
            return cached
        self.misses += 1
        result = self.inner.matrix(points, mode, departure_min)
        self._cache[key] = result
        return result


#: Куда fetch_osm.py кладёт выгрузку.
OSM_DIR = Path(__file__).resolve().parents[3] / "data" / "osm"


def default_provider(osm_dir: Path | None = None) -> TravelTimeProvider:
    """Маршрутизация по дорогам, если выгрузка OSM есть; иначе — приближение.

    Откат нужен, чтобы сервис поднимался на машине без выгрузки: расчёт будет
    грубее, но ничего не сломается. В логе это видно явно — молча подменять
    модель времени нельзя, разница в цифрах достигает полутора раз.
    """
    # Импорт внутри функции: osm.py берёт из этого модуля traffic_factor,
    # и на уровне модуля получилась бы циклическая зависимость.
    from .osm import OsmRoutingProvider

    provider = OsmRoutingProvider.from_cache(osm_dir or OSM_DIR)
    if provider is not None:
        return provider
    print("[travel] выгрузки OSM нет — время в пути считается по прямой "
          "с коэффициентом извилистости. Запустите: py -3.11 data/fetch_osm.py")
    return CachedProvider(HaversineProvider())
