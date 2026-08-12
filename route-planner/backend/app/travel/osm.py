"""Провайдер времени в пути поверх дорожного графа OSM.

Заменяет `HaversineProvider` там, где выгрузка OSM доступна. Реализует тот же
протокол `TravelTimeProvider`, поэтому солвер о подмене не знает, и добавляет
`path()` — геометрию маршрута для карты.

Кеширование обязательно, а не для красоты. `why_not` и `why_this` перебирают
позиции вставки и дёргают `minutes()` десятки раз подряд; без кеша каждый вызов
запускал бы Дейкстру по 38 тысячам вершин. Поэтому результат поиска от вершины
(расстояния и предшественники) сохраняется целиком: первый вызов стоит около
10 мс, остальные из этого же источника — бесплатны.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import numpy as np

from ..domain.models import TransportMode
from .graph import RoutingGraph, haversine_km
from .provider import Matrix, Point, traffic_factor

#: Профиль графа для каждого типа транспорта.
PROFILE = {
    TransportMode.CAR: "car",
    TransportMode.VAN: "car",
    TransportMode.WALK_TRANSIT: "transit",
}

#: Фургон тяжелее и медленнее легковой машины на том же маршруте.
VAN_PENALTY = 1.12

#: Сколько результатов поиска держим в памяти. 38 тыс. вершин × (float64 +
#: int32) ≈ 460 КБ на источник, так что 256 источников — это ~120 МБ.
SSSP_CACHE = 256


class OsmRoutingProvider:
    """Маршрутизация по дорогам, пешком и на метро."""

    def __init__(self, graph: RoutingGraph) -> None:
        self.graph = graph
        self._sssp: OrderedDict[tuple[int, str], tuple] = OrderedDict()
        self._matrix_cache: dict[tuple, Matrix] = {}
        self.hits = 0
        self.misses = 0

    # ------------------------------------------------------------------ загрузка

    @classmethod
    def from_cache(cls, osm_dir: Path, cache_path: Path | None = None
                   ) -> "OsmRoutingProvider | None":
        """Собрать провайдер из выгрузки OSM, переиспользуя готовый граф.

        Возвращает None, если выгрузки нет: вызывающий сам решит, падать ему
        или откатиться на геодезическое приближение.
        """
        osm_dir = Path(osm_dir)
        roads = osm_dir / "roads.json"
        if not roads.exists():
            return None
        cache_path = cache_path or (osm_dir / "graph.pkl")
        if Path(cache_path).exists():
            try:
                return cls(RoutingGraph.load(Path(cache_path)))
            except Exception:
                pass                       # кеш от другой версии — пересоберём
        graph = RoutingGraph.build(roads, osm_dir / "metro.json")
        try:
            graph.save(Path(cache_path))
        except Exception:
            pass                           # кеш не критичен
        return cls(graph)

    # ------------------------------------------------------------------ поиск

    def _profile(self, mode: TransportMode) -> str:
        return PROFILE.get(mode, "car")

    def _penalty(self, mode: TransportMode) -> float:
        return VAN_PENALTY if mode is TransportMode.VAN else 1.0

    def _search(self, vertex: int, profile: str):
        key = (int(vertex), profile)
        cached = self._sssp.get(key)
        if cached is not None:
            self.hits += 1
            self._sssp.move_to_end(key)
            return cached
        self.misses += 1
        dist, pred = self.graph.times(np.array([vertex]), profile, want_paths=True)
        result = (np.atleast_2d(dist)[0], np.atleast_2d(pred)[0])
        self._sssp[key] = result
        if len(self._sssp) > SSSP_CACHE:
            self._sssp.popitem(last=False)
        return result

    # ------------------------------------------------------------------ протокол

    def matrix(self, points, mode: TransportMode, departure_min: int) -> Matrix:
        profile = self._profile(mode)
        key = (tuple(points), profile)
        base = self._matrix_cache.get(key)
        if base is None:
            nodes = self.graph.snap(list(points), profile)
            dist = self.graph.times(nodes, profile)
            base = np.atleast_2d(dist)[:, nodes]
            # Изолированная точка не должна ронять расчёт: заменяем
            # недостижимость правдоподобной оценкой по прямой.
            if not np.isfinite(base).all():
                bad = ~np.isfinite(base)
                fallback = np.array([
                    [haversine_km(a[0], a[1], b[0], b[1]) * 1.35 / 25 * 60
                     for b in points] for a in points])
                base = np.where(bad, fallback, base)
            self._matrix_cache[key] = base

        scale = traffic_factor(mode, departure_min) * self._penalty(mode)
        scaled = np.rint(base * scale).astype(int)
        np.fill_diagonal(scaled, 0)
        return np.maximum(scaled, 0).tolist()

    def minutes(self, a: Point, b: Point, mode: TransportMode,
                departure_min: int) -> int:
        if a == b:
            return 0
        profile = self._profile(mode)
        va, vb = self.graph.snap([a, b], profile)
        dist, _ = self._search(int(va), profile)
        value = dist[int(vb)]
        if not np.isfinite(value):
            value = haversine_km(a[0], a[1], b[0], b[1]) * 1.35 / 25 * 60
        return max(1, int(round(
            value * traffic_factor(mode, departure_min) * self._penalty(mode))))

    def distance_km(self, a: Point, b: Point,
                    mode: TransportMode = TransportMode.CAR) -> float:
        """Длина реального пути по дорогам, а не по прямой."""
        line = self.path(a, b, mode)
        if len(line) < 2:
            return haversine_km(a[0], a[1], b[0], b[1]) * 1.35
        total = 0.0
        for (lon1, lat1), (lon2, lat2) in zip(line, line[1:]):
            total += haversine_km(lat1, lon1, lat2, lon2)
        return total

    # ------------------------------------------------------------------ геометрия

    def path(self, a: Point, b: Point,
             mode: TransportMode = TransportMode.CAR) -> list[list[float]]:
        """Ломаная [[lon, lat], …] по дорогам — то, что рисуется на карте."""
        if a == b:
            return []
        profile = self._profile(mode)
        va, vb = self.graph.snap([a, b], profile)
        _, pred = self._search(int(va), profile)
        line = self.graph.route_geometry(pred, int(va), int(vb))
        if not line:
            return [[a[1], a[0]], [b[1], b[0]]]
        # Дом и объект стоят не на перекрёстке: дотягиваем концы до точек.
        return [[a[1], a[0]], *line, [b[1], b[0]]]

    def uses_metro(self, a: Point, b: Point) -> bool:
        """Проходит ли пеший маршрут через метро — для подписи на карте."""
        profile = "transit"
        va, vb = self.graph.snap([a, b], profile)
        _, pred = self._search(int(va), profile)
        node, guard = int(vb), 0
        while node != int(va) and node >= 0 and guard < 100_000:
            if node >= self.graph.n_road:
                return True
            node = int(pred[node])
            guard += 1
        return False
