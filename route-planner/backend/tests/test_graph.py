"""Граф маршрутизации по OSM.

Пропускается целиком, если выгрузки нет: она весит 14 МБ и не хранится в
репозитории. Восстанавливается командой `py -3.11 data/fetch_osm.py`.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.domain.models import TransportMode
from conftest import OSM_DIR

pytestmark = pytest.mark.skipif(
    not (OSM_DIR / "roads.json").exists(),
    reason="нет выгрузки OSM: py -3.11 data/fetch_osm.py")


@pytest.fixture(scope="module")
def graph():
    from app.travel.graph import RoutingGraph
    cache = OSM_DIR / "graph.pkl"
    if cache.exists():
        return RoutingGraph.load(cache)
    return RoutingGraph.build(OSM_DIR / "roads.json", OSM_DIR / "metro.json")


@pytest.fixture(scope="module")
def osm_provider(graph):
    from app.travel.osm import OsmRoutingProvider
    return OsmRoutingProvider(graph)


def test_all_profiles_present(graph):
    assert set(graph.graphs) == {"car", "foot", "transit"}
    assert graph.n_road > 10_000


def test_no_absurd_travel_times(graph, ds):
    """Веса-заглушки в графе привели бы к времени в миллиарды минут."""
    points = [(j.lat, j.lon) for j in ds.jobs[:30]]
    for mode in ("car", "foot", "transit"):
        nodes = graph.snap(points, mode)
        matrix = np.atleast_2d(graph.times(nodes, mode))[:, nodes]
        finite = matrix[np.isfinite(matrix)]
        assert finite.max() < 24 * 60, (
            f"{mode}: максимальное время {finite.max():.0f} мин — "
            f"похоже, Дейкстра прошла по запрещающему ребру")


def test_every_job_reachable_in_every_profile(graph, ds):
    """Привязка идёт к сильносвязной компоненте своего профиля."""
    points = [(j.lat, j.lon) for j in ds.jobs]
    for mode in ("car", "foot", "transit"):
        nodes = graph.snap(points, mode)
        matrix = np.atleast_2d(graph.times(nodes, mode))[:, nodes]
        assert np.isfinite(matrix).all(), (
            f"{mode}: {int((~np.isfinite(matrix)).sum())} недостижимых пар")


def test_metro_beats_walking(graph, ds):
    """Слой метро должен реально работать, а не совпадать с пешим."""
    points = [(j.lat, j.lon) for j in ds.jobs[:40]]
    foot = np.atleast_2d(graph.times(graph.snap(points, "foot"), "foot"))[
        :, graph.snap(points, "foot")]
    transit = np.atleast_2d(graph.times(graph.snap(points, "transit"), "transit"))[
        :, graph.snap(points, "transit")]
    far = foot > 60
    assert far.any(), "в выборке нет дальних пар — проверка бессмысленна"
    assert np.median(transit[far]) < np.median(foot[far]) * 0.75, (
        "метро не даёт выигрыша: возможно, линии не привязались к станциям")


def test_road_route_is_longer_than_straight_line(osm_provider, ds):
    """Путь по улицам не может быть короче прямой."""
    from app.travel.graph import haversine_km

    for a, b in zip(ds.jobs[:8], ds.jobs[8:16]):
        km = osm_provider.distance_km((a.lat, a.lon), (b.lat, b.lon))
        straight = haversine_km(a.lat, a.lon, b.lat, b.lon)
        assert km >= straight * 0.95, (
            f"путь {km:.1f} км короче прямой {straight:.1f} км")


def test_geometry_follows_streets(osm_provider, ds):
    """Ломаная маршрута описывает улицы, а не соединяет точки напрямую."""
    a, b = ds.jobs[0], ds.jobs[7]
    line = osm_provider.path((a.lat, a.lon), (b.lat, b.lon), TransportMode.CAR)
    assert len(line) > 10, f"в маршруте всего {len(line)} точек — это прямая"
    assert line[0] == [a.lon, a.lat] and line[-1] == [b.lon, b.lat]


def test_van_is_slower_than_car(osm_provider, ds):
    a, b = ds.jobs[0], ds.jobs[9]
    car = osm_provider.minutes((a.lat, a.lon), (b.lat, b.lon),
                               TransportMode.CAR, 10 * 60)
    van = osm_provider.minutes((a.lat, a.lon), (b.lat, b.lon),
                               TransportMode.VAN, 10 * 60)
    assert van >= car


def test_rush_hour_is_slower(osm_provider, ds):
    a, b = ds.jobs[0], ds.jobs[9]
    midday = osm_provider.minutes((a.lat, a.lon), (b.lat, b.lon),
                                  TransportMode.CAR, 13 * 60)
    rush = osm_provider.minutes((a.lat, a.lon), (b.lat, b.lon),
                                TransportMode.CAR, 18 * 60)
    assert rush > midday, "профиль пробок не влияет на время в пути"
