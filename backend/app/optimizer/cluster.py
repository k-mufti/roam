"""Geographic clustering: split a candidate pool into one group per day.

The goal is that a day's stops sit near each other, so the traveller isn't
crossing Madrid four times. Two requirements pull against each other:

* **Compactness** — stops in a day should be close together. KMeans optimises
  exactly this.
* **Balance** — a 3-day trip needs three usable days, not one day with eleven
  stops and two with one. KMeans has no notion of cluster size and routinely
  produces exactly that when a city's good places are concentrated downtown,
  which in Madrid they emphatically are.

So: KMeans for the shape, then a capacity-constrained repair pass for the
balance. The repair moves the point that is *most marginal* to its own cluster
(largest distance to its centroid relative to the nearest alternative) rather
than an arbitrary one, which keeps the compactness KMeans found.

Coordinates are projected to local metres before clustering. Clustering raw
degrees would distort distances, because at Madrid's latitude (40.4°N) a degree
of longitude is only ~76% as long as a degree of latitude — so east-west
distances would be systematically overweighted by about a third.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
from sklearn.cluster import KMeans

log = logging.getLogger(__name__)

EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True, slots=True)
class GeoPoint:
    key: str
    lat: float
    lng: float
    #: Higher is better; used to break ties and to choose day ordering.
    score: float = 0.0


def project_to_metres(
    points: list[GeoPoint], origin_lat: float | None = None, origin_lng: float | None = None
) -> np.ndarray:
    """Equirectangular projection to local metres about an origin.

    Exact enough over a city (<20km) and far cheaper than a proper projection.
    """
    if not points:
        return np.empty((0, 2))
    lat0 = origin_lat if origin_lat is not None else sum(p.lat for p in points) / len(points)
    lng0 = origin_lng if origin_lng is not None else sum(p.lng for p in points) / len(points)
    cos_lat = math.cos(math.radians(lat0))
    return np.array(
        [
            [
                math.radians(p.lng - lng0) * EARTH_RADIUS_M * cos_lat,
                math.radians(p.lat - lat0) * EARTH_RADIUS_M,
            ]
            for p in points
        ]
    )


def cluster_by_day(
    points: list[GeoPoint], days: int, capacity: int, *, random_state: int = 42
) -> list[list[GeoPoint]]:
    """Partition `points` into `days` compact groups of at most `capacity` each.

    `random_state` is fixed so the same request yields the same itinerary — a
    planner that reshuffles on every refresh is unusable.
    """
    if days <= 0:
        return []
    if not points:
        return [[] for _ in range(days)]
    if days == 1:
        return [sorted(points, key=lambda p: -p.score)[:capacity]]

    # More clusters than points: hand out one each and leave the rest empty.
    if len(points) <= days:
        groups: list[list[GeoPoint]] = [[p] for p in points]
        groups.extend([] for _ in range(days - len(points)))
        return groups

    coords = project_to_metres(points)
    kmeans = KMeans(n_clusters=days, n_init=10, random_state=random_state)
    labels = kmeans.fit_predict(coords)

    groups = [[] for _ in range(days)]
    for point, label in zip(points, labels, strict=True):
        groups[int(label)].append(point)

    groups = _rebalance(groups, coords, points, kmeans.cluster_centers_, capacity)

    # Within a day, keep the strongest places first; the scheduler decides the
    # visiting order, but if a day overflows we want the best stops kept.
    return [sorted(group, key=lambda p: -p.score) for group in groups]


def _rebalance(
    groups: list[list[GeoPoint]],
    coords: np.ndarray,
    points: list[GeoPoint],
    centres: np.ndarray,
    capacity: int,
) -> list[list[GeoPoint]]:
    """Move points out of over-capacity clusters into the nearest cluster with
    room, preferring to move the points that fit their cluster worst."""
    index_of = {point.key: i for i, point in enumerate(points)}

    def distance(point: GeoPoint, cluster: int) -> float:
        dx, dy = coords[index_of[point.key]] - centres[cluster]
        return float(math.hypot(dx, dy))

    for _ in range(len(points) * 2):  # bounded: each iteration moves one point
        over = [i for i, g in enumerate(groups) if len(g) > capacity]
        if not over:
            break
        source = max(over, key=lambda i: len(groups[i]))
        targets = [i for i, g in enumerate(groups) if len(g) < capacity and i != source]
        if not targets:
            break  # genuinely more points than the trip can hold; truncation handles it

        # The point whose own cluster fits it worst relative to its best
        # alternative is the cheapest one to move. Loop variables are bound as
        # defaults so the closure does not capture the mutating names.
        def regret(point: GeoPoint, _src: int = source, _dst: tuple = tuple(targets)) -> float:
            best_alt = min(distance(point, t) for t in _dst)
            return distance(point, _src) - best_alt

        mover = max(groups[source], key=regret)
        destination = min(targets, key=lambda t: distance(mover, t))
        groups[source].remove(mover)
        groups[destination].append(mover)

    sizes = [len(g) for g in groups]
    if any(size > capacity for size in sizes):
        log.info("clusters still over capacity %d after rebalance: %s", capacity, sizes)
    return groups


def medoid(points: list[GeoPoint]) -> GeoPoint | None:
    """The member minimising total distance to the others.

    A medoid rather than a centroid because it is an actual place: the centroid
    of a cluster spanning the old town and a park 9km away sits in a residential
    street that nobody would visit, and measuring compactness from there is
    misleading.
    """
    if not points:
        return None
    if len(points) <= 2:
        return max(points, key=lambda p: p.score)
    coords = project_to_metres(points)
    best_index, best_total = 0, float("inf")
    for i in range(len(points)):
        total = float(np.hypot(*(coords - coords[i]).T).sum())
        if total < best_total:
            best_index, best_total = i, total
    return points[best_index]


def order_days(groups: list[list[GeoPoint]]) -> list[list[GeoPoint]]:
    """Order the days themselves so the strongest day comes first.

    Travellers front-load: if the trip gets cut short or they run out of energy,
    the best cluster should already have happened.
    """
    return sorted(groups, key=lambda g: -sum(p.score for p in g))
