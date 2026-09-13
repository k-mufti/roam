"""Optimizer tests: travel providers, clustering, routing, scheduling.

Every test here uses the offline haversine provider so the suite needs no
network. The scheduling tests encode constraints that were violated by working
code at some point during development — the nightclub-at-10am case from the
spec, two lunches an hour apart, three-hour waits, and a 2h24m walking leg.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.models.enums import PACE_PROFILES, Pace, PlaceCategory
from app.models.hours import all_week, from_google_periods
from app.optimizer.cluster import GeoPoint, cluster_by_day, medoid, project_to_metres
from app.optimizer.route import nearest_neighbour, optimize_order, tour_cost, two_opt
from app.optimizer.schedule import (
    CATEGORY_DEFAULT_WINDOWS,
    MIN_SAME_CATEGORY_GAP_MINUTES,
    Stop,
    effective_hours,
    schedule_day,
    simulate,
)
from app.optimizer.travel import (
    DETOUR_FACTOR,
    MODE_SPEED_MPS,
    CachingProvider,
    HaversineProvider,
    LatLng,
    TravelMode,
    is_public_osrm,
)

WALK = HaversineProvider(TravelMode.WALK)

ALL_DAYS = range(7)


def hours(spans):
    return from_google_periods(
        [
            {
                "open": {"day": d, "hour": oh, "minute": om},
                "close": {"day": (d + 1) % 7 if ch < oh else d, "hour": ch, "minute": cm},
            }
            for d, oh, om, ch, cm in spans
        ]
    )


MUSEUM_HOURS = hours([(d, 10, 0, 20, 0) for d in ALL_DAYS if d != 1])  # closed Mondays
CLUB_HOURS = hours([(d, 23, 30, 6, 0) for d in (5, 6, 0)])
RESTO_HOURS = hours(
    [(d, 13, 0, 16, 0) for d in ALL_DAYS] + [(d, 20, 0, 23, 30) for d in ALL_DAYS]
)
PARK_HOURS = hours([(d, 6, 0, 23, 0) for d in ALL_DAYS])

FRIDAY = date(2026, 9, 18)
MONDAY = date(2026, 9, 14)


def stop(key, name, lat, lng, category, score=60.0, hrs=None):
    return Stop(key=key, name=name, lat=lat, lng=lng, category=category, score=score, hours=hrs)


CLUB = stop(
    "club", "Teatro Barceló", 40.42558, -3.69823, PlaceCategory.NIGHTLIFE, 40, CLUB_HOURS
)
MUSEUM = stop(
    "prado", "Museo del Prado", 40.41379, -3.69214, PlaceCategory.MUSEUM, 75, MUSEUM_HOURS
)
LUNCH = stop(
    "botin", "Sobrino de Botín", 40.41424, -3.70887, PlaceCategory.RESTAURANT, 60, RESTO_HOURS
)
DINNER = stop(
    "lucio", "Casa Lucio", 40.41132, -3.70992, PlaceCategory.RESTAURANT, 55, RESTO_HOURS
)
PARK = stop(
    "retiro", "Parque de El Retiro", 40.41531, -3.68439, PlaceCategory.PARK, 76, PARK_HOURS
)


# --- travel -----------------------------------------------------------------


class TestTravel:
    def test_matrix_is_square_with_zero_diagonal(self):
        pts = [LatLng(40.41, -3.70), LatLng(40.42, -3.71), LatLng(40.43, -3.69)]
        m = WALK.matrix(pts)
        assert len(m) == 3 and all(len(row) == 3 for row in m)
        assert all(m[i][i] == 0.0 for i in range(3))

    def test_symmetric(self):
        pts = [LatLng(40.41, -3.70), LatLng(40.42, -3.71)]
        m = WALK.matrix(pts)
        assert m[0][1] == pytest.approx(m[1][0])

    def test_detour_factor_is_applied(self):
        """A 1.0 factor would understate real walking distance by ~30%."""
        pts = [LatLng(40.41689, -3.70346), LatLng(40.41552, -3.70742)]
        seconds = WALK.matrix(pts)[0][1]
        from app.ingestion.normalize import haversine_m

        straight = haversine_m(40.41689, -3.70346, 40.41552, -3.70742)
        assert seconds == pytest.approx(
            straight * DETOUR_FACTOR / MODE_SPEED_MPS[TravelMode.WALK], rel=1e-6
        )

    def test_transit_is_faster_than_walking_over_distance(self):
        pts = [LatLng(40.41, -3.70), LatLng(40.46, -3.68)]
        walk = WALK.matrix(pts)[0][1]
        transit = HaversineProvider(TravelMode.TRANSIT).matrix(pts)[0][1]
        assert transit < walk

    def test_transit_overhead_makes_short_hops_not_worth_it(self):
        """Below ~800m you walk rather than descend into the Metro."""
        near = [LatLng(40.41689, -3.70346), LatLng(40.41700, -3.70360)]
        transit = HaversineProvider(TravelMode.TRANSIT)
        assert transit.matrix(near)[0][1] < 120  # no overhead charged

    def test_single_point_matrix(self):
        assert WALK.matrix([LatLng(40.4, -3.7)]) == [[0.0]]

    def test_empty_matrix(self):
        assert WALK.matrix([]) == []

    def test_caching_provider_memoizes(self):
        cached = CachingProvider(HaversineProvider(TravelMode.WALK))
        pts = [LatLng(40.41, -3.70), LatLng(40.42, -3.71)]
        cached.matrix(pts)
        cached.matrix(pts)
        assert cached.misses == 1 and cached.hits == 1

    def test_public_osrm_is_detected(self):
        """The public demo serves a car-only graph; using it silently would
        report Sol -> Plaza Mayor (a 5-minute walk) as ~49 minutes."""
        assert is_public_osrm("https://router.project-osrm.org")
        assert not is_public_osrm("http://localhost:5000")


# --- clustering -------------------------------------------------------------


class TestClustering:
    def test_longitude_is_scaled_by_latitude(self):
        """At 40.4°N a degree of longitude is ~76% of a degree of latitude.
        Clustering raw degrees would overweight east-west distance by a third."""
        coords = project_to_metres(
            [GeoPoint("o", 40.4, -3.7), GeoPoint("n", 41.4, -3.7), GeoPoint("e", 40.4, -2.7)],
            origin_lat=40.4,
            origin_lng=-3.7,
        )
        north = abs(coords[1][1])
        east = abs(coords[2][0])
        assert east / north == pytest.approx(0.76, abs=0.02)

    def test_respects_day_count(self):
        pts = [GeoPoint(f"p{i}", 40.41 + i * 0.002, -3.70 + i * 0.002, 50) for i in range(12)]
        assert len(cluster_by_day(pts, 3, capacity=6)) == 3

    def test_every_point_assigned_exactly_once(self):
        pts = [GeoPoint(f"p{i}", 40.41 + i * 0.003, -3.70 - i * 0.002, 50) for i in range(15)]
        groups = cluster_by_day(pts, 3, capacity=10)
        keys = [p.key for g in groups for p in g]
        assert sorted(keys) == sorted(p.key for p in pts)

    def test_capacity_is_enforced(self):
        """Plain KMeans produces wildly unbalanced days when a city's good
        places cluster downtown, as Madrid's do."""
        pts = [GeoPoint(f"c{i}", 40.415 + i * 0.0002, -3.705 + i * 0.0002, 50) for i in range(14)]
        pts += [GeoPoint(f"f{i}", 40.46 + i * 0.01, -3.62 - i * 0.01, 40) for i in range(4)]
        groups = cluster_by_day(pts, 3, capacity=6)
        assert all(len(g) <= 6 for g in groups)

    def test_deterministic(self):
        pts = [GeoPoint(f"p{i}", 40.41 + i * 0.004, -3.70 + i * 0.003, 50) for i in range(10)]
        a = [[p.key for p in g] for g in cluster_by_day(pts, 3, 5)]
        b = [[p.key for p in g] for g in cluster_by_day(pts, 3, 5)]
        assert a == b

    def test_fewer_points_than_days(self):
        groups = cluster_by_day([GeoPoint("a", 40.4, -3.7, 50)], 3, capacity=5)
        assert len(groups) == 3
        assert sum(len(g) for g in groups) == 1

    def test_no_points(self):
        assert cluster_by_day([], 3, capacity=5) == [[], [], []]

    def test_medoid_is_an_actual_member(self):
        pts = [GeoPoint(f"p{i}", 40.41 + i * 0.002, -3.70, 50) for i in range(5)]
        assert medoid(pts) in pts

    def test_medoid_of_empty_is_none(self):
        assert medoid([]) is None


# --- routing ----------------------------------------------------------------


class TestRouting:
    POINTS = [
        LatLng(40.41689, -3.70346),  # Sol
        LatLng(40.41379, -3.69214),  # Prado
        LatLng(40.41531, -3.68439),  # Retiro
        LatLng(40.41552, -3.70742),  # Plaza Mayor
        LatLng(40.42403, -3.71783),  # Debod
        LatLng(40.45306, -3.68835),  # Bernabeu
    ]

    def test_visits_every_node_once(self):
        m = WALK.matrix(self.POINTS)
        order = optimize_order(m)
        assert sorted(order) == list(range(len(self.POINTS)))

    def test_two_opt_improves_nearest_neighbour(self):
        """2-opt exists to fix NN's long return leg after greedily walking away
        from the start."""
        m = WALK.matrix(self.POINTS)
        nn = nearest_neighbour(m, 0)
        improved = two_opt(nn, m)
        assert tour_cost(improved, m) < tour_cost(nn, m)

    def test_two_opt_never_worsens(self):
        m = WALK.matrix(self.POINTS)
        for start in range(len(self.POINTS)):
            nn = nearest_neighbour(m, start)
            assert tour_cost(two_opt(nn, m), m) <= tour_cost(nn, m) + 1e-9

    def test_feasibility_veto_is_respected(self):
        """Without this hook 2-opt returns a tour 4 minutes shorter that
        schedules a nightclub at 10am."""
        m = WALK.matrix(self.POINTS)
        forbidden = {1, 2, 3, 4}

        def feasible(order):
            return order[1] not in forbidden

        result = two_opt(nearest_neighbour(m, 0), m, is_feasible=feasible)
        assert result[1] not in forbidden or result == nearest_neighbour(m, 0)

    def test_custom_cost_function_is_used(self):
        m = WALK.matrix(self.POINTS)
        calls = []

        def cost(order):
            calls.append(order)
            return tour_cost(order, m)

        two_opt(nearest_neighbour(m, 0), m, cost_fn=cost)
        assert calls

    def test_trivial_inputs(self):
        assert optimize_order([[0.0]]) == [0]
        assert optimize_order([]) == []
        assert two_opt([0, 1, 2], [[0, 1, 2], [1, 0, 1], [2, 1, 0]]) == [0, 1, 2]


# --- scheduling -------------------------------------------------------------


class TestOpeningHoursAreHardConstraints:
    def test_nightclub_is_not_scheduled_in_the_morning(self):
        """The spec's own example."""
        plan = schedule_day(
            0, FRIDAY, [CLUB, MUSEUM, PARK], WALK,
            day_start_hour=9, day_end_hour=26, max_stops=6,
        )
        club = next((s for s in plan.stops if s.stop.key == "club"), None)
        assert club is not None, "the club should be scheduled, just not in the morning"
        assert club.start_minute >= 23 * 60 + 30

    def test_place_closed_that_weekday_is_dropped(self):
        plan = schedule_day(
            0, MONDAY, [MUSEUM, PARK], WALK,
            day_start_hour=9, day_end_hour=22, max_stops=6,
        )
        assert "prado" not in {s.stop.key for s in plan.stops}
        assert any(s.key == "prado" for s, _ in plan.dropped)

    def test_visit_must_fit_inside_one_open_interval(self):
        """earliest_open only guarantees the door is open at arrival; a museum
        closing at 20:00 cannot absorb a 105-minute visit starting at 19:45."""
        plan = schedule_day(
            0, FRIDAY, [MUSEUM], WALK,
            day_start_hour=19, day_end_hour=23, max_stops=3,
        )
        assert plan.stops == []
        assert "window too short" in plan.dropped[0][1]

    def test_overnight_close_is_handled(self):
        plan = schedule_day(
            0, FRIDAY, [CLUB], WALK, day_start_hour=22, day_end_hour=27, max_stops=2
        )
        assert plan.stops and plan.stops[0].crosses_midnight

    def test_unknown_hours_use_a_category_prior_not_free_rein(self):
        """A bar with no hours data was being booked at 10:26."""
        bar = stop("bar", "Some Bar", 40.4168, -3.7034, PlaceCategory.BAR, 60, None)
        plan = schedule_day(
            0, FRIDAY, [bar], WALK, day_start_hour=9, day_end_hour=26, max_stops=2
        )
        assert plan.stops
        assert plan.stops[0].start_minute >= 18 * 60
        assert plan.stops[0].hours_assumed is True

    def test_real_hours_are_not_marked_assumed(self):
        _, assumed = effective_hours(MUSEUM)
        assert assumed is False

    def test_category_without_a_defensible_prior_stays_permissive(self):
        other = stop("o", "Unknown", 40.4168, -3.7034, PlaceCategory.OTHER, 60, None)
        assert PlaceCategory.OTHER not in CATEGORY_DEFAULT_WINDOWS
        hrs, assumed = effective_hours(other)
        assert hrs is None and assumed is False


class TestDayComposition:
    def test_meals_are_spaced_into_lunch_and_dinner(self):
        """A daily cap of two restaurants alone permitted 13:00 lunch followed
        by 14:21 lunch."""
        plan = schedule_day(
            0, FRIDAY, [LUNCH, DINNER, MUSEUM, PARK], WALK,
            day_start_hour=9, day_end_hour=25, max_stops=6,
        )
        meals = sorted(
            s.start_minute for s in plan.stops if s.stop.category is PlaceCategory.RESTAURANT
        )
        if len(meals) >= 2:
            gap = MIN_SAME_CATEGORY_GAP_MINUTES[PlaceCategory.RESTAURANT]
            assert meals[1] - meals[0] >= gap

    def test_daily_category_cap(self):
        museums = [
            stop(f"m{i}", f"Museum {i}", 40.4138 + i * 0.004, -3.6921 + i * 0.003,
                 PlaceCategory.MUSEUM, 70, MUSEUM_HOURS)
            for i in range(6)
        ]
        plan = schedule_day(
            0, FRIDAY, museums, WALK, day_start_hour=9, day_end_hour=24, max_stops=8
        )
        count = sum(1 for s in plan.stops if s.stop.category is PlaceCategory.MUSEUM)
        assert count <= 3

    def test_no_two_stops_at_effectively_the_same_location(self):
        """Scraped sources carry aggregate listings ("Museum Triangle") 9m from
        a place we also have individually. Dedup keeps them apart correctly; the
        scheduler must decline to visit both."""
        twin = stop("twin", "Museum Triangle", 40.41381, -3.69224, PlaceCategory.ATTRACTION,
                    55, MUSEUM_HOURS)
        plan = schedule_day(
            0, FRIDAY, [MUSEUM, twin], WALK, day_start_hour=10, day_end_hour=22, max_stops=4
        )
        assert len(plan.stops) == 1

    def test_absurd_legs_are_rejected(self):
        """KMeans absorbed a park 11km out into the downtown cluster and the
        optimiser produced a 2h24m walking leg."""
        far = stop("far", "Parque de El Capricho", 40.44851, -3.61039,
                   PlaceCategory.PARK, 68, PARK_HOURS)
        plan = schedule_day(
            0, FRIDAY, [PARK, MUSEUM, far], WALK,
            day_start_hour=9, day_end_hour=22, max_stops=6, max_leg_minutes=45,
        )
        for scheduled in plan.stops:
            assert scheduled.travel_seconds / 60.0 <= 45

    def test_stop_cap_is_respected(self):
        plan = schedule_day(
            0, FRIDAY, [MUSEUM, PARK, LUNCH, DINNER, CLUB], WALK,
            day_start_hour=9, day_end_hour=26, max_stops=2,
        )
        assert len(plan.stops) <= 2


class TestScheduleIntegrity:
    def test_times_are_monotonic_and_non_overlapping(self):
        plan = schedule_day(
            0, FRIDAY, [MUSEUM, PARK, LUNCH, DINNER, CLUB], WALK,
            day_start_hour=9, day_end_hour=26, max_stops=6,
        )
        for earlier, later in zip(plan.stops, plan.stops[1:], strict=False):
            assert earlier.end_minute <= later.start_minute

    def test_arrival_never_after_start(self):
        plan = schedule_day(
            0, FRIDAY, [MUSEUM, PARK, LUNCH], WALK,
            day_start_hour=9, day_end_hour=24, max_stops=5,
        )
        for s in plan.stops:
            assert s.arrival_minute <= s.start_minute

    def test_long_gaps_are_free_time_not_waiting(self):
        """Reporting a 5-hour gap as "waited 299m" was simply wrong, and it made
        the optimiser penalise unavoidable afternoon gaps like queueing."""
        plan = schedule_day(
            0, FRIDAY, [PARK, DINNER], WALK,
            day_start_hour=9, day_end_hour=24, max_stops=4,
        )
        for s in plan.stops:
            assert not (s.wait_minutes > 60)
            if s.free_minutes:
                assert s.wait_minutes == 0

    def test_nothing_schedulable_yields_an_empty_day_not_a_crash(self):
        plan = schedule_day(
            0, MONDAY, [MUSEUM], WALK, day_start_hour=9, day_end_hour=10, max_stops=3
        )
        assert plan.stops == [] and plan.dropped

    def test_empty_candidates(self):
        plan = schedule_day(0, FRIDAY, [], WALK, day_start_hour=9, day_end_hour=22, max_stops=5)
        assert plan.stops == [] and plan.dropped == []

    def test_simulate_drops_rather_than_raises(self):
        stops = [MUSEUM, CLUB]
        matrix = WALK.matrix([s.point for s in stops])
        kept, dropped = simulate([0, 1], stops, matrix, MONDAY.weekday(), 9 * 60, 20 * 60)
        assert len(kept) + len(dropped) == 2


class TestPaceProfiles:
    @pytest.mark.parametrize("pace", list(Pace))
    def test_every_pace_is_configured(self, pace):
        profile = PACE_PROFILES[pace]
        assert profile["day_start_hour"] < profile["day_end_hour"]
        assert profile["stops_per_day"] >= 1
        assert profile["meals_per_day"] >= 1

    def test_packed_allows_more_than_relaxed(self):
        assert (
            PACE_PROFILES[Pace.PACKED]["stops_per_day"]
            > PACE_PROFILES[Pace.MODERATE]["stops_per_day"]
            > PACE_PROFILES[Pace.RELAXED]["stops_per_day"]
        )

    def test_stop_counts_leave_room_for_meals(self):
        """3/5/7 looked fine until the itineraries contained no lunch at all."""
        for pace, profile in PACE_PROFILES.items():
            assert profile["stops_per_day"] > profile["meals_per_day"] + 1, pace


def test_all_week_builds_every_day():
    week = all_week([("10:00", "20:00")])
    assert len(week) == 7
    assert all(v == [{"open": "10:00", "close": "20:00"}] for v in week.values())
