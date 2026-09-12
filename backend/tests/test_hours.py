import pytest

from app.models.hours import (
    earliest_open,
    from_google_periods,
    from_yelp_open,
    intervals_for,
    is_open_during,
)

MON, FRI, SUN = 0, 4, 6

GOOGLE = from_google_periods(
    [
        {"open": {"day": 1, "hour": 10, "minute": 0}, "close": {"day": 1, "hour": 20, "minute": 0}},
        {"open": {"day": 5, "hour": 23, "minute": 0}, "close": {"day": 6, "hour": 3, "minute": 0}},
    ]
)


class TestGoogleConversion:
    def test_maps_sunday_zero_to_sunday(self):
        # Google: 0=Sun. Python: 0=Mon. Getting this wrong shifts every day.
        assert GOOGLE["mon"] == [{"open": "10:00", "close": "20:00"}]

    def test_absent_day_is_explicitly_closed(self):
        assert GOOGLE["sun"] == []

    def test_missing_close_means_24h(self):
        h = from_google_periods([{"open": {"day": 1, "hour": 0, "minute": 0}}])
        assert h["mon"] == [{"open": "00:00", "close": "23:59"}]


class TestOvernight:
    def test_close_before_open_rolls_past_midnight(self):
        assert intervals_for(GOOGLE, FRI) == [(23 * 60, 27 * 60)]

    def test_visit_after_midnight_fits(self):
        # 23:30-01:00 on Friday.
        assert is_open_during(GOOGLE, FRI, 23 * 60 + 30, 25 * 60)


class TestThreeStates:
    def test_open_interval(self):
        assert is_open_during(GOOGLE, MON, 11 * 60, 12 * 60 + 30)

    def test_outside_interval_is_closed(self):
        assert not is_open_during(GOOGLE, MON, 9 * 60, 10 * 60 + 30)

    def test_empty_list_is_hard_closed(self):
        assert not is_open_during(GOOGLE, SUN, 11 * 60, 12 * 60)

    def test_unknown_is_permissive(self):
        # None must NOT mean closed, or the optimizer refuses to schedule
        # anything we lack hours for.
        assert is_open_during(None, MON, 1 * 60, 2 * 60)
        assert is_open_during({}, MON, 1 * 60, 2 * 60)


class TestEarliestOpen:
    def test_waits_until_opening(self):
        assert earliest_open(GOOGLE, MON, 8 * 60) == 10 * 60

    def test_returns_request_when_already_open(self):
        assert earliest_open(GOOGLE, MON, 11 * 60) == 11 * 60

    def test_none_when_day_is_over(self):
        assert earliest_open(GOOGLE, MON, 23 * 60) is None

    def test_unknown_hours_never_wait(self):
        assert earliest_open(None, MON, 8 * 60) == 8 * 60


def test_yelp_conversion_day_zero_is_monday():
    h = from_yelp_open([{"day": 0, "start": "0900", "end": "1700"}])
    assert h["mon"] == [{"open": "09:00", "close": "17:00"}]
    assert h["sun"] == []


@pytest.mark.parametrize("bad", [[{"day": 9, "start": "0900", "end": "1700"}], [{}]])
def test_yelp_ignores_malformed_entries(bad):
    h = from_yelp_open(bad)
    assert all(v == [] for v in h.values())
