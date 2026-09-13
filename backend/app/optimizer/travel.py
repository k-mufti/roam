"""Travel-time providers behind one interface.

## The interface

`TravelTimeProvider.matrix(points)` returns an NxN matrix of travel times in
seconds. Everything downstream — route ordering, 2-opt, scheduling — consumes
only that, so swapping providers changes no other code.

## Why the public OSRM server's durations are deliberately ignored

Measured against `router.project-osrm.org` during development, the `foot`,
`walking` and `driving` profiles return **byte-identical** results: Puerta del
Sol to the Prado comes back as 2395 m in 363 s. That is 23.8 km/h — car speed.
The public demo server only has the car profile built, and it silently ignores
the profile in the URL rather than erroring.

Trusting those durations would understate walking time by roughly 5x, and the
optimizer would happily pack eleven stops into a day that can physically hold
five.

The obvious fix — take OSRM's *distance* matrix and divide by a walking speed —
turns out to fail too, and worse. Because the underlying graph is the car
network, it routes *around* pedestrianized streets: Puerta del Sol to Plaza
Mayor, a 370 m five-minute stroll, comes back as ~3.7 km, which converts to a
49-minute walk. In the exact part of the city this app is about, the car graph
is not an approximation of the walking graph; it is a different graph.

So **`haversine` is the default provider**, not OSRM. That is an unusual call —
a hand-rolled estimator beating a real routing engine — and it is specific to
the public demo server, not to OSRM. `OSRMProvider` remains fully implemented
and is the right choice against a **self-hosted OSRM with the foot profile
compiled in**, where both distances and durations are correct and
`trust_durations=True` is appropriate. `build_provider` detects the public demo
host and refuses to use it silently. The README documents the self-hosting
command.

## Providers

* `HaversineProvider` — great-circle distance x a detour factor / mode speed.
  No network, deterministic, the default fallback. The detour factor is what
  makes it usable: a 1.0 factor would understate real walking distance by
  ~30% in a city with a street grid.
* `OSRMProvider` — real street-network distances, durations from mode speed.
* `GoogleDirectionsProvider` — used only when a key is configured. Its Routes
  API bills per element, which is why it is not the default.
* `CachingProvider` — memoizes matrices, since the optimizer requests the same
  day's matrix repeatedly during 2-opt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

import httpx

from app.ingestion.normalize import haversine_m

log = logging.getLogger(__name__)


class TravelMode(StrEnum):
    WALK = "walk"
    TRANSIT = "transit"
    DRIVE = "drive"


#: Effective door-to-door speeds in m/s, including the realities of each mode.
#:
#: WALK at 1.25 m/s (4.5 km/h) is a tourist's pace with stops at crossings, not
#: a commuter's 1.4. TRANSIT at 4.2 m/s (15 km/h) is an effective average for
#: Madrid Metro including walking to the station, waiting, and interchanges —
#: which is why it is barely 3x walking despite trains being far faster.
MODE_SPEED_MPS: dict[TravelMode, float] = {
    TravelMode.WALK: 1.25,
    TravelMode.TRANSIT: 4.2,
    TravelMode.DRIVE: 6.0,
}

#: Straight-line distance underestimates real travel. This multiplier converts
#: great-circle metres into approximate street-network metres. 1.35 is the
#: commonly cited figure for dense European city centres; Madrid's irregular
#: old core is if anything worse.
DETOUR_FACTOR = 1.35

#: Fixed overhead per transit leg (walk to station, wait, interchange).
TRANSIT_OVERHEAD_SECONDS = 360.0

#: OSRM's table service is capped server-side. Days have <=8 stops, so this is
#: only a guard against a caller asking for a pool-sized matrix.
OSRM_MAX_POINTS = 90

PUBLIC_OSRM_HOSTS = ("router.project-osrm.org",)


@dataclass(frozen=True, slots=True)
class LatLng:
    lat: float
    lng: float


@runtime_checkable
class TravelTimeProvider(Protocol):
    name: str

    def matrix(self, points: list[LatLng]) -> list[list[float]]:
        """NxN travel times in seconds. Diagonal is 0."""
        ...


# --- haversine --------------------------------------------------------------


class HaversineProvider:
    """Offline estimator. Always available, never wrong in a surprising way."""

    name = "haversine"

    def __init__(self, mode: TravelMode = TravelMode.WALK) -> None:
        self.mode = mode

    def _seconds(self, a: LatLng, b: LatLng) -> float:
        metres = haversine_m(a.lat, a.lng, b.lat, b.lng) * DETOUR_FACTOR
        seconds = metres / MODE_SPEED_MPS[self.mode]
        if self.mode is TravelMode.TRANSIT and metres > 800:
            # Below ~800m you would walk rather than descend into the Metro, so
            # the overhead only applies to legs long enough to justify it.
            seconds += TRANSIT_OVERHEAD_SECONDS
        return seconds

    def matrix(self, points: list[LatLng]) -> list[list[float]]:
        return [
            [0.0 if i == j else self._seconds(a, b) for j, b in enumerate(points)]
            for i, a in enumerate(points)
        ]


# --- OSRM -------------------------------------------------------------------


class OSRMProvider:
    """Real street-network distances from an OSRM `table` service.

    See the module docstring for why `trust_durations` defaults to False.
    """

    name = "osrm"

    def __init__(
        self,
        base_url: str,
        mode: TravelMode = TravelMode.WALK,
        *,
        profile: str = "foot",
        trust_durations: bool = False,
        timeout: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.mode = mode
        self.profile = profile
        self.timeout = timeout
        self.trust_durations = trust_durations
        if trust_durations and any(h in self.base_url for h in PUBLIC_OSRM_HOSTS):
            log.warning(
                "trust_durations=True against the public OSRM demo server, which "
                "serves car durations for every profile. Walking times will be "
                "understated roughly 5x."
            )
        self._fallback = HaversineProvider(mode)

    def matrix(self, points: list[LatLng]) -> list[list[float]]:
        if len(points) < 2:
            return [[0.0] * len(points) for _ in points]
        if len(points) > OSRM_MAX_POINTS:
            log.warning("%d points exceeds OSRM table limit; using haversine", len(points))
            return self._fallback.matrix(points)

        coords = ";".join(f"{p.lng},{p.lat}" for p in points)
        url = f"{self.base_url}/table/v1/{self.profile}/{coords}"
        annotations = "duration,distance" if self.trust_durations else "distance"
        try:
            response = httpx.get(url, params={"annotations": annotations}, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != "Ok":
                raise ValueError(f"OSRM returned code={payload.get('code')}")
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            # Routing is an enhancement, never a dependency: a dead OSRM must
            # not prevent an itinerary from being produced.
            log.warning("OSRM unavailable (%s); falling back to %s", exc, self._fallback.name)
            return self._fallback.matrix(points)

        if self.trust_durations and payload.get("durations"):
            return [[float(v or 0.0) for v in row] for row in payload["durations"]]

        distances = payload.get("distances")
        if not distances:
            log.warning("OSRM returned no distance matrix; falling back")
            return self._fallback.matrix(points)

        speed = MODE_SPEED_MPS[self.mode]
        out: list[list[float]] = []
        for i, row in enumerate(distances):
            converted = []
            for j, metres in enumerate(row):
                if i == j:
                    converted.append(0.0)
                elif metres is None:
                    # OSRM could not route this pair (island, pedestrian zone
                    # gap). Estimate rather than dropping the leg.
                    converted.append(self._fallback._seconds(points[i], points[j]))
                else:
                    seconds = float(metres) / speed
                    if self.mode is TravelMode.TRANSIT and float(metres) > 800:
                        seconds += TRANSIT_OVERHEAD_SECONDS
                    converted.append(seconds)
            out.append(converted)
        return out


# --- Google Directions ------------------------------------------------------


class GoogleDirectionsProvider:
    """Google Routes API distance matrix. Only used when a key is configured;
    it bills per origin-destination element, so it is never the default."""

    name = "google_directions"
    URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
    MODE_MAP = {
        TravelMode.WALK: "WALK",
        TravelMode.TRANSIT: "TRANSIT",
        TravelMode.DRIVE: "DRIVE",
    }

    def __init__(self, api_key: str, mode: TravelMode = TravelMode.WALK, timeout: float = 25.0):
        self.api_key = api_key
        self.mode = mode
        self.timeout = timeout
        self._fallback = HaversineProvider(mode)

    def matrix(self, points: list[LatLng]) -> list[list[float]]:
        if len(points) < 2:
            return [[0.0] * len(points) for _ in points]
        waypoints = [
            {"waypoint": {"location": {"latLng": {"latitude": p.lat, "longitude": p.lng}}}}
            for p in points
        ]
        body = {
            "origins": waypoints,
            "destinations": waypoints,
            "travelMode": self.MODE_MAP[self.mode],
        }
        try:
            response = httpx.post(
                self.URL,
                json=body,
                headers={
                    "X-Goog-Api-Key": self.api_key,
                    "X-Goog-FieldMask": "originIndex,destinationIndex,duration,condition",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            elements = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Google Routes unavailable (%s); falling back", exc)
            return self._fallback.matrix(points)

        n = len(points)
        out = [[0.0] * n for _ in range(n)]
        for element in elements:
            i, j = element.get("originIndex"), element.get("destinationIndex")
            if i is None or j is None or i == j:
                continue
            raw = element.get("duration") or "0s"
            try:
                out[i][j] = float(str(raw).rstrip("s"))
            except ValueError:
                out[i][j] = self._fallback._seconds(points[i], points[j])
        return out


# --- caching ----------------------------------------------------------------


class CachingProvider:
    """Memoize matrices by rounded coordinate tuple.

    2-opt re-evaluates the same day's stops thousands of times. Without this,
    an HTTP-backed provider would be called once per evaluation.
    """

    def __init__(self, inner: TravelTimeProvider) -> None:
        self.inner = inner
        self.name = f"cached:{inner.name}"
        self._cache: dict[tuple, list[list[float]]] = {}
        self.hits = 0
        self.misses = 0

    def matrix(self, points: list[LatLng]) -> list[list[float]]:
        key = tuple((round(p.lat, 6), round(p.lng, 6)) for p in points)
        cached = self._cache.get(key)
        if cached is not None:
            self.hits += 1
            return cached
        self.misses += 1
        result = self.inner.matrix(points)
        self._cache[key] = result
        return result


# --- factory ----------------------------------------------------------------


def is_public_osrm(base_url: str) -> bool:
    return any(host in (base_url or "") for host in PUBLIC_OSRM_HOSTS)


def build_provider(settings, mode: TravelMode = TravelMode.WALK) -> TravelTimeProvider:
    """Choose a provider from configuration, wrapped in a cache.

    Refuses to use the public OSRM demo server unless explicitly forced, since
    its car-only graph mis-routes pedestrian zones badly enough to produce
    plausible-looking but wrong itineraries. See the module docstring.
    """
    choice = (settings.routing_provider or "haversine").strip().lower()

    if choice == "google":
        if settings.google_directions_api_key:
            return CachingProvider(
                GoogleDirectionsProvider(settings.google_directions_api_key, mode)
            )
        log.info("routing_provider=google but no key configured; using haversine")
        return CachingProvider(HaversineProvider(mode))

    if choice == "osrm":
        if is_public_osrm(settings.osrm_base_url) and not getattr(
            settings, "osrm_allow_public_demo", False
        ):
            log.warning(
                "OSRM_BASE_URL points at the public demo server (%s), which serves a "
                "car-only graph: it routes around pedestrian streets and reports "
                "Puerta del Sol -> Plaza Mayor (a 5-minute walk) as ~3.7km. Using the "
                "haversine estimator instead. Point OSRM_BASE_URL at a self-hosted "
                "foot-profile OSRM for real routing, or set "
                "OSRM_ALLOW_PUBLIC_DEMO=true to override.",
                settings.osrm_base_url,
            )
            return CachingProvider(HaversineProvider(mode))
        trust = not is_public_osrm(settings.osrm_base_url)
        return CachingProvider(
            OSRMProvider(settings.osrm_base_url, mode, trust_durations=trust)
        )

    return CachingProvider(HaversineProvider(mode))
