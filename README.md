# Trip Package

Aggregates places for one city from four independent data sources, deduplicates
them into single records, ranks them with a source-credibility weighted score,
and generates an optimized day-by-day itinerary with a map route.

**Status: in progress.** See "Build progress" below.

## Scope (v1)

One city: **Madrid, Spain.** This is deliberate. `city` is a real column and a
real query parameter everywhere — nothing is hardcoded in logic — so adding a
second city means appending to `CITIES` in `backend/app/config.py` and running
ingestion. But there is no city selector and no multi-city UI in v1.

Also explicitly out of scope: user accounts, user-submitted reviews, reviewer
trust graphs, weather rerouting, group preference reconciliation, live
crowd/closure data, calendar export.

## Build progress

- [x] Unified `Place` schema + migrations
- [x] Google Places ingestion (live + fixture fallback)
- [x] Entity resolution / dedup
- [ ] Yelp, Reddit, blog scraper ingestion
- [ ] Credibility-weighted scoring
- [ ] NLP tagging pipeline
- [ ] Itinerary / route optimizer
- [ ] React + Leaflet frontend
