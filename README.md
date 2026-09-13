# Trip Package

Aggregates places for one city from four independent data sources, deduplicates
them into single records, ranks them with a **source-credibility weighted**
score, and generates an optimized day-by-day itinerary with a map route.

```bash
make setup        # venv + npm install (needs python3.12, node, docker)
make seed         # start Postgres+PostGIS, migrate, ingest, score, tag
make dev          # API on :8000, web on :5173
```

Then open <http://localhost:5173>. API docs at <http://localhost:8000/docs>.

**No API keys are required.** Every source falls back to checked-in fixtures,
and two of the four run live with no credentials at all.

---

## Contents

- [Scope](#scope)
- [What's real vs. mocked](#whats-real-vs-mocked)
- [Architecture](#architecture)
- [The `Place` schema](#the-place-schema)
- [Entity resolution](#entity-resolution)
- [Scoring — the core differentiator](#scoring--the-core-differentiator)
- [Tagging](#tagging)
- [The optimizer](#the-optimizer)
- [Things that surprised us](#things-that-surprised-us)
- [Setup in detail](#setup-in-detail)
- [CLI](#cli)
- [API](#api)
- [Known limitations](#known-limitations)

---

## Scope

**One city: Madrid.** Deliberately. `city` is a real column and a real query
parameter everywhere — there is no hardcoded `"Madrid"` in any logic — so
adding a second city means appending a `CityConfig` to `backend/app/config.py`
and running ingestion. But there is no city selector and no multi-city UI.

Out of scope for v1: user accounts, user-submitted reviews, reviewer trust
graphs, weather rerouting, group preference reconciliation, live crowd data,
calendar export.

## What's real vs. mocked

This is answerable from the running app, not just from this file:
`GET /api/meta/provenance` reports each source's last run straight out of the
`ingest_runs` audit table, and the sidebar shows it.

| Source | Without credentials | With credentials | Notes |
|---|---|---|---|
| **Google Places** | fixture | live | Needs a GCP project with **billing enabled**. Fixture is a recorded v1 `places:searchNearby` response: 37 real Madrid places with real coordinates, plausible ratings. |
| **Yelp Fusion** | fixture | live | Free key, 500 req/day. 23 businesses, deliberately with name variants and 20–80 m coordinate jitter so entity resolution gets a real workout. |
| **Reddit** | fixture | live | **The one key worth getting** — free, no payment card. 7 threads / 43 comments of realistic r/madrid prose. |
| **Wikivoyage scraper** | live ✅ | — | Runs live today, no key. 53 listings across 6 district articles. |

Fixtures are parsed by **the same mapping code as live responses** — the
fixture is a recorded API payload, not a shortcut around the parser. If the
parser breaks, fixture mode breaks too, which is what makes mock mode a real
test rather than a stub.

### Why Reddit needs a key

Reddit's `robots.txt` is `Disallow: /` **for every user agent**, which covers
the `.json` endpoints that most tutorials reach for. This project does not
touch them. Live mode authenticates against the official API
(`oauth.reddit.com`, `client_credentials` grant — the same sanctioned path PRAW
uses). Registering a "script" app is free and needs no payment details:

```bash
# https://www.reddit.com/prefs/apps -> create app -> type "script"
echo 'REDDIT_CLIENT_ID=...'     >> .env
echo 'REDDIT_CLIENT_SECRET=...' >> .env
make reset && make seed
```

## Architecture

```
Google Places ─┐
Yelp Fusion   ─┤
Reddit        ─┼─→ SourceAdapter ─→ EntityResolver ─→ Postgres/PostGIS
Wikivoyage    ─┘   (live│fixture)     (dedup/merge)        │
                                                            ├─→ scoring  ─→ composite_score + breakdown
                                                            ├─→ tagging  ─→ tags[]
                                                            └─→ optimizer ─→ candidates → clusters → schedule
                                                                                  │
                                                              FastAPI ─→ React + Leaflet
```

Adapters never touch the database. They emit a source-agnostic `RawPlace`, and
**all** persistence, deduplication and merge logic lives in one place, so a new
source is ~100 lines of mapping code and inherits dedup for free. The live/
fixture split lives in the `SourceAdapter` base class rather than in each
adapter, so degradation is uniform and impossible to forget.

```
backend/
  app/
    ingestion/   base.py (contract) · google_places · yelp · reddit · scraper
                 resolver.py (dedup) · mentions.py (NLP) · normalize.py · runner.py
    models/      place.py (SQLAlchemy) · hours.py · enums.py
    scoring/     model.py (pure, no ORM) · config.py (tunables) · explain.py
    tagging/     lexicon.py (negation) · structural.py · keywords.py (TF-IDF)
    optimizer/   travel.py (routing iface) · cluster.py · route.py · schedule.py
    api/         FastAPI routes
  alembic/       migrations
  tests/         180 tests
frontend/        React + Leaflet
```

### Stack choices

**Postgres + PostGIS over SQLite + SpatiaLite.** SpatiaLite is genuinely painful
to install on macOS (needs `mod_spatialite`, and Homebrew's Python often ships
without extension loading enabled), and we'd have lost JSONB, array columns, and
trigram indexes — all three of which the design leans on. Cost: `docker compose
up` in setup. On Apple Silicon the official PostGIS image is amd64-only and runs
emulated; `docker-compose.yml` notes the native arm64 alternative.

**No queue.** The spec says a CLI runner is fine. Ingestion is a batch job over
a few thousand rows that takes seconds; Kafka or Celery would be pure ceremony.
The seam is `app.ingestion.runner.run_source`, already a pure function of
`(session, source, city)`.

**VADER over a transformer for sentiment.** The task is scoring short, typo-heavy
forum prose. A transformer adds 500 MB+ and GPU-shaped inference cost to do it
slightly better, and the difference disappears once signals are aggregated over
dozens of comments. `scikit-learn` earns its place twice: KMeans for day
clustering, TF-IDF for keyword extraction.

## The `Place` schema

```sql
places
  id                uuid PK
  name              text
  name_normalized   text        -- matching key; pg_trgm GIN index
  city              text        -- real field, indexed
  lat, lng          float
  geom              geography(Point,4326)   -- GiST index
  category          enum(restaurant|cafe|bar|attraction|museum|park|shopping|hotel|nightlife|other)
  price_tier        smallint    CHECK BETWEEN 1 AND 4
  tags              text[]      -- GIN index
  hours             jsonb
  composite_score   float
  score_breakdown   jsonb       -- full audit trail
  duplicate_of      uuid FK -> places.id
  merge_confidence  float
```

Plus `source_signals`, `text_evidence` (snippets with provenance, feeding
tagging), `merge_reviews` (the ambiguous-merge queue), `ingest_runs` (audit).

Four decisions worth defending:

1. **`geography`, not `geometry`.** With `geography(Point,4326)`,
   `ST_DWithin(geom, pt, 4828)` takes **metres**. With `geometry(4326)` the same
   call compares *degrees* and silently returns wrong answers at Madrid's
   latitude — a classic PostGIS footgun. Geography ops are slower; irrelevant at
   a few thousand rows.

2. **`source_signals` is a child table, not a JSONB array** — despite the spec
   describing it as an array (the API still *exposes* it as one). It needs
   `UNIQUE(source, source_place_id)` to make re-ingestion idempotent, which is
   not expressible inside a JSONB blob.

3. **`hours` genuinely is JSONB** — read whole, never queried by key, and its
   real shape is awkward in columns: Madrid restaurants have *split* days
   (13:00–16:00, then 20:00–23:30) and bars close at 02:00 the *next* day.
   Three distinct states matter: intervals = open, `[]` = **closed** (hard
   constraint), key absent = **unknown** (treated permissively — refusing to
   schedule everything we lack data for would gut the itinerary).

4. **Merge losers are kept, not deleted.** `duplicate_of` points at the
   survivor, so dedup is auditable and reversible.

## Entity resolution

"Café Rivas" (Yelp), "Cafe Rivas Palermo" (Google) and a bare "rivas" mention
(Reddit) are one cafe. Naive inserts give three rows, three mediocre scores, and
three pins stacked on each other.

**Blocking → scoring → a three-way decision.** Candidates come from PostGIS
`ST_DWithin` *or* `pg_trgm` similarity, which keeps this O(candidates) rather
than O(n²). Name similarity blends rapidfuzz's `token_set_ratio` (handles the
*extra tokens* sources differ by) with a strict `ratio` that pulls the blend
back down — `token_set_ratio` alone matches "Casa Lucio" to "Casa Botín".

The decision is a **distance-tiered rule table**, not one blended threshold:

| Tier | Distance | Requires | Rationale |
|---|---|---|---|
| 1 | ≤ 40 m | name ≥ 0.62 **and a shared distinctive token** | Same building |
| 2 | ≤ 120 m | name ≥ 0.80 | Same block |
| 3 | ≤ 250 m | name ≥ 0.92 | Same street |

The first version used a single blended confidence and **failed on real data**,
flagging four pairs that were unmistakably the same place 14–15 m apart, because
cross-language names only reach ~0.67 fuzzy similarity and a linear blend cannot
express "at 15 m the names barely need to agree".

The **distinctive-token anchor** is what lets tier 1 be lenient without
over-merging. A token counts only if it is ≥4 characters and not generic —
`casa`, `taberna`, `mercado`, `plaza`, `madrid` are all excluded, because dozens
of Madrid places share them:

| Pair | Distance | Anchor | Decision |
|---|---|---|---|
| Café Rivas ↔ Cafe Rivas Palermo | 30 m | `rivas` | ✅ merge |
| Museo del Prado ↔ Museo Nacional del Prado | 15 m | `prado` | ✅ merge |
| Retiro Park ↔ Parque de El Retiro | 14 m | `retiro` | ✅ merge (cross-language) |
| Botín ↔ Sobrino de Botín | 14 m | `botin` | ✅ merge |
| **Casa Lucio ↔ Casa Botín** | 40 m | *none* — `casa` is generic | ⚑ flagged, not merged |
| Casa Dani ↔ Mercado de la Paz | 15 m | none | ✳ separate (a stall is not the market) |
| Toma Café ↔ Toma Café | 800 m | `toma` | ✳ separate (different branch) |

On the full fixture set: **16/16 true duplicates merged, zero false merges**,
one genuinely ambiguous pair flagged (`trip review` shows it). Ambiguous pairs
are never guessed — they are written to `merge_reviews` for a human.

Coordinate-less mentions (Reddit) take a stricter path: they can only *attach*
to a place another source geocoded, never create one, and need either 0.90
similarity or an **unambiguous** distinctive-token containment. That containment
rule is what recovers "the Prado", "Retiro", "Botin", "Thyssen" — abbreviations
worth thousands of upvotes that a similarity-only rule discarded. Mentions that
match nothing are dropped and counted: neighbourhoods (*Malasaña*, *Lavapiés*),
an artwork (*Guernica*), a cocktail (*Chipotle Chillón*).

## Scoring — the core differentiator

`backend/app/scoring/model.py` is **pure functions over dataclasses, no ORM**,
so it can be demoed in isolation:

```bash
backend/.venv/bin/trip explain "Museo del Prado"
```

```
COMPOSITE SCORE: 74.7 / 100

  google_places   quality 0.826   weight 0.999  (43% of total)
    weight =   credibility 1.00 × volume 1.00 × recency 1.00   [0 days old]
    · rating 4.8 shrunk to 4.80 toward the google_places mean of 4.48 (96421 reviews vs 50 prior)
    · z-score +1.95 against this source's own spread (sd 0.16, measured)

  yelp            quality 0.765   weight 0.744  (32% of total)
    · rating 5.0 shrunk to 4.96 toward the yelp mean of 4.26 (842 reviews vs 50 prior)
    · z-score +1.47 against this source's own spread (sd 0.47, measured)

  reddit          quality 0.513   weight 0.554  (24% of total)
    · no star rating on Reddit; quality derived from upvote-weighted sentiment +0.037
    · 6 mention(s), 3033 upvote(s) -> evidence 14.02, sentiment trusted at 70%

  How it combines:
    weighted mean quality            0.7305   (3 source(s))
    total evidence (Σ weights)       2.2968   -> confidence 0.87
    shrunk toward neutral 0.50       0.7001
    cross-source agreement           0.5818
    corroboration bonus             +0.0465
    composite = min(1, base + bonus) × 100 = 74.7
```

The same breakdown is in the UI behind "Why this score?" on any pin.

### Four mechanisms, each fixing a different defect of averaging

**1. Per-source z-scoring.** Measured on this project's actual data:

| Source | Madrid mean | Std dev |
|---|---|---|
| Google Places | **4.484** | 0.162 |
| Yelp Fusion | **4.261** | 0.474 |

Yelp's spread is **2.9× wider**. So a 4.3 is −1.1σ on Google (below average) and
+0.08σ on Yelp (above it). Averaging the raw numbers treats those as the same
claim about quality. Distributions are measured **per city**, because rating
inflation is regional.

**2. Bayesian shrinkage by review volume**, plus a separate volume term in the
weight. Shrinkage moves the *estimate* toward the source mean; volume moves how
much the estimate *counts*. This is what makes 4.5-from-900 outrank
4.5-from-3 rather than tie.

**3. Corroboration bonus scaled by measured agreement**, not by source count.
Additive and capped at 0.12, so it reorders comparable places without letting a
badly-rated place outrank an excellent one. Four sources that *disagree* earn
almost nothing.

**4. Evidence-confidence shrinkage.** This one was added after real data exposed
a flaw: a weighted mean is *scale-invariant*, so with a single signal the weight
cancels out entirely and one low-credibility blog listing scored **identically**
to one 87,000-review Google rating. That ranked all 36 blog-only places at
66/100, above three-source places. The base is now shrunk toward a neutral 0.5
in proportion to total evidence weight; blog-only places land at 55.0.

### Sources without ratings

Reddit and the scraper have no stars, and **`rating` stays NULL** — inventing a
star value would launder a guess into the schema. Instead:

- **Reddit**: quality from upvote-weighted, sentence-scoped sentiment, shrunk
  toward neutral by `log1p(upvotes) + mentions`, so one +2 comment cannot read
  as a strong opinion. Upvotes and thread score drive its *weight* — per the
  spec's requirement to weight by thread upvotes and sentiment, not by raw
  mention count.
- **Blog**: inclusion in a curated list is a flat, mildly positive quality at low
  weight. The source expresses no degree, so neither do we.

## Tagging

Three producers, combined: a **negation-aware lexicon** over review text,
**structural** tags from columns we already trust, and **TF-IDF** keywords.

### Negation is the whole problem

Naive substring matching gets all of these backwards. Each is verbatim fixture
text:

| Text | Naive | Correct |
|---|---|---|
| "Tiny, **no laptops**, in and out." | laptop-friendly | **not** laptop-friendly |
| "Loud, fun, **not a place for a quiet** dinner." | quiet | **not** quiet |
| "**Not a romantic** dinner, more of an event." | date-night | **not** date-night |
| "A real neighbourhood market, **not a tourist** one." | touristy | **not** touristy |

Implemented as a NegEx-style 6-token lookback with a **clause-boundary guard**,
so "It's not cheap, **but** the quiet terrace is lovely" still tags `quiet`.
Matching is token-based, so `line` does not match `online`. Veto phrases bypass
the negation filter, since the phrase "no laptops" *is* the negation.

Rules fire on **weighted evidence share**, not snippet count: one heavily
upvoted comment can carry a tag, a stray mention among twenty cannot.

### Sentiment

VADER, with two corrections that measurably mattered:

- **Sentence-scoped, not document-scoped.** A comment praising one place and
  panning another would otherwise assign both the same average. The window
  *widens* when the naming sentence carries no opinion — "Casa Dani in Mercado
  de la Paz. Their tortilla is genuinely the best in the city" names the place
  in one sentence and evaluates it in the next.
- **A travel-domain lexicon extension.** VADER scored "mediocre **at best**" as
  **+0.48**, reading "best" as praise. `tourist trap`, `overrated`, `hidden gem`,
  `worth it`, `at best` are all corrected.

### Consensus tags

`hidden-gem` and `tourist-trap` exist only because there are multiple sources —
neither is computable from any single one. Both were wrong on first pass:

- `tourist-trap` used `sentiment <= 0.0`, which read *absent* opinion as
  negative and tagged **Parque de El Retiro** (152k reviews, one neutral
  comment) a tourist trap. Now requires ≤ −0.15 plus real Reddit evidence.
- `hidden-gem` used an 8,000-review ceiling, which tagged **Salmon Guru** — a
  World's 50 Best Bars fixture — a hidden gem. Ceiling is now 2,500.

### TF-IDF keywords

Term frequency alone is useless here: "tapas" appears in half of Madrid's
reviews with near-zero discriminating power. IDF is exactly the right measure.
Output is namespaced `topic:` so an unbounded data-derived vocabulary can never
collide with a curated tag. The exclusion set is **derived from `TAG_RULES`
programmatically** so the two cannot drift, plus a per-place veto on the place's
own name tokens — those have maximal TF-IDF and zero information. Output went
from `topic:quiet` / `topic:park` to `topic:tortilla` / `topic:cocktail` /
`topic:football`.

## The optimizer

```bash
backend/.venv/bin/trip itinerary --start 2026-09-18 --days 3 --pace moderate
```

Pipeline: **rank a candidate pool → cluster geographically per day → schedule
each day**.

- **Clustering** is KMeans for shape plus a **capacity-constrained repair pass**
  for balance. Plain KMeans produces wildly unbalanced days when a city's good
  places concentrate downtown, as Madrid's do. Coordinates are projected to
  local metres first — at 40.4°N a degree of longitude is only ~76% of a degree
  of latitude, so clustering raw degrees overweights east–west distance by a
  third.
- **Routing** is nearest-neighbour + 2-opt, trying every start. 2-opt improves
  the NN tour by ~17% on the Madrid pool.
- **Scheduling is time-aware from the start**, not "order then assign times".
  Feasibility depends on order: a bar opening at 19:00 and a museum closing at
  20:00 can both be visited, but only in one sequence. A shortest tour can be an
  impossible schedule.

### Constraints that turned out to be necessary

Each of these was violated by working code at some point:

| Constraint | What went wrong without it |
|---|---|
| Opening hours as hard constraints | The spec's own nightclub-at-10am |
| Category **default windows** for unknown hours | A flamenco dinner venue booked at 10:26, because scraped sources supply no hours and "unknown" was fully permissive |
| Minimum spacing between same-category stops | 13:00 lunch followed by 14:21 lunch — two restaurants, technically within the daily cap |
| 2-opt objective includes **waiting** | The pass traded ~3 hours of idle time for 5 minutes of walking and called it an improvement |
| Waiting vs. **free time** distinction | A 5-hour afternoon gap reported as "waited 299m" — and penalised like queueing |
| Compactness-aware shortlist + max leg | A **2h24m walking leg** after KMeans absorbed a park 11 km out into the downtown cluster |
| Minimum separation between stops | Scraped aggregate listings ("Museum Triangle") sit 9 m from a place we also have individually |
| Meal reservation in the shortlist | Generated itineraries contained **no lunch or dinner at all** — sights outrank a tapas bar on score, so the scheduler was never offered a meal |
| Meal bonus gated on *arrival* time | The bonus was strong enough that Day 1 opened with "09:00: wait four hours for lunch" |

Total travel across a 3-day Madrid plan went from **279 min → 126 min** as these
landed.

### Routing providers

Behind one interface (`matrix(points) -> seconds`), so providers swap without
touching anything downstream: `HaversineProvider`, `OSRMProvider`,
`GoogleDirectionsProvider`, and a caching wrapper.

**The default is `haversine`, not OSRM.** That is an unusual call, so here is
the measurement. Against `router.project-osrm.org`, the `foot`, `walking` and
`driving` profiles return **byte-identical** results — Puerta del Sol to the
Prado comes back as 2395 m in 363 s, i.e. **23.8 km/h**, car speed. The public
demo only has the car profile built and silently ignores the profile in the URL.
Trusting those durations understates walking time ~5× and the optimizer packs
eleven stops into a day that holds five.

Taking OSRM's *distance* matrix and dividing by a walking speed fails worse:
because the graph is the car network, it routes **around** pedestrianized
streets. Sol → Plaza Mayor — a 370 m, five-minute stroll — comes back as ~3.7 km
and converts to a **49-minute walk**. In the exact part of the city this app is
about, the car graph is not an approximation of the walking graph.

`build_provider` detects the public demo host and refuses to use it silently.
`OSRMProvider` is the right choice against a self-hosted foot-profile OSRM,
where durations are trusted automatically:

```bash
docker run -t -v "$PWD/osrm:/data" ghcr.io/project-osrm/osrm-backend \
  osrm-extract -p /opt/foot.lua /data/spain-latest.osm.pbf
# ...partition, customize, then:
echo 'OSRM_BASE_URL=http://localhost:5000' >> .env
echo 'ROUTING_PROVIDER=osrm' >> .env
```

## Things that surprised us

Short list, because each changed the design:

1. **Reddit's `robots.txt` is `Disallow: /`.** The `.json` endpoints everyone
   uses are off-limits; the official OAuth API is the sanctioned path.
2. **The public OSRM server is car-only** and lies about it, including about
   distances, in exactly the pedestrian zones that matter.
3. **Google's and Yelp's rating scales are not comparable** — 4.484 ± 0.162 vs
   4.261 ± 0.474 in Madrid. This is the empirical justification for the whole
   scoring module.
4. **A weighted mean is scale-invariant**, so one weak source scored the same as
   one overwhelming source. Non-obvious, and it silently broke the ranking.
5. **VADER reads "mediocre at best" as positive** (+0.48).
6. **Distance alone cannot decide a merge** — food-hall stalls are metres apart —
   and **names alone cannot either**, across languages. The anchor token is what
   makes the pair of signals work.

## Setup in detail

Requires **Python 3.12** (not 3.13/3.14 — the NLP/scientific wheels are not
reliable there yet), **Node 20+**, and **Docker**.

```bash
make setup          # venv, backend deps, npm install, .env from .env.example
make seed           # db up, migrate, ingest all sources, score, tag
make dev            # API :8000 + web :5173
```

`make seed` runs live where possible (the scraper) and fixture elsewhere.
`make seed-offline` forces fixtures everywhere — deterministic, no network.
`make reset` wipes and re-seeds. `make test` runs 180 tests; `make lint` runs
ruff and `tsc`.

Postgres is published on host port **55432**, not 5432, so it cannot collide
with a local Homebrew Postgres.

## CLI

```bash
trip ingest [--source google_places|yelp|reddit|blog] [--fixtures] [--refresh]
trip score                    # recompute composite scores
trip tag                      # regenerate tags
trip explain "<place name>"   # score breakdown, the scoring demo
trip review                   # ambiguous merges awaiting a human
trip status                   # what's in the database
trip itinerary --start YYYY-MM-DD --days 3 --pace moderate --tag quiet
```

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/facets` | Filter options derived from the data present |
| `GET /api/places` | Filter by category, tag, price, score, **corroboration count**, and radius (PostGIS `ST_DWithin`) |
| `GET /api/places/{id}/score` | The full scoring breakdown |
| `POST /api/itinerary` | Generate a plan; returns stops in visiting order with coordinates for the map |
| `GET /api/meta/provenance` | Which sources ran live vs. fixture |

## Known limitations

Stated rather than hidden:

- **Day assignment happens before scheduling.** A place closed on the Monday it
  was clustered into is offered to the next day, but full cross-day
  reassignment would mean solving all days jointly.
- **Google's `searchNearby` caps at 20 results per call** and the v1 API does
  not paginate. Breadth comes from one call per type group; more would mean
  tiling the city into sub-circles, which multiplies cost.
- **Yelp hours and review text need a per-business call**, so they are fetched
  only for the top N by review count, to stay inside 500 req/day.
- **Wikivoyage carries aggregate listings** ("Museum Triangle") that name an
  *area* containing places we also have individually. Entity resolution
  correctly keeps them separate; the scheduler declines to visit both.
- **The planner is single-user.** No accounts, no saved trips.
- **Dwell times are category defaults**, not per-place estimates.
- On Apple Silicon the PostGIS container runs emulated (see
  `docker-compose.yml`).
